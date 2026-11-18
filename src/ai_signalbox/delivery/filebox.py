"""The built-in delivery: a box folder per workspace, a signal file every live session of it watches, a start file
per session, and a background vigil while the session sits idle.

How a message reaches a session, in every state:
- at session start, the start hook claims what waits and puts it in the session's context;
- while idle or busy, and before the first turn, a touch of a watched file (the workspace's `.signal`, the session's
  `.start-<sid>`) runs the wake hook (FileChanged, asyncRewake), which exits 2 with the text and wakes the session;
- the Stop vigil (asyncRewake) looks at the box every few seconds while the session is idle, as a fallback;
- the prompt hook piggybacks what waits on the person's next prompt.

Each message is claimed by one atomic rename into `delivered/`, so two hooks of a session never deliver it twice.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .. import threads as threads_mod
from ..util import hhmm, now, touch


class Filebox:
    def __init__(self, app):
        self.app = app
        self.store = app.store
        d = app.cfg['delivery']
        self.vigil_step = float(os.environ.get('SIGNALBOX_VIGIL_STEP') or d.get('vigil_step') or 2.0)
        self.vigil_max = float(d.get('vigil_max') or 84600.0)
        self.start_touches = tuple(d.get('start_touches') or (4, 12, 30))

    # ------------------------------------------------------------ marks
    def watch_paths(self, workspace, sid) -> list[Path]:
        return [self.store.signal_path(workspace), self.store.start_path(workspace, sid)]

    def arm(self, workspace, sid) -> list[Path]:
        """What wakes the session from now on, before its first turn too: the workspace's signal and its own start
        file. Returns the paths for the start hook's watchPaths."""
        for f in self.watch_paths(workspace, sid):
            if not f.exists():
                touch(f, 'created')
        self.store.woke_path(workspace, sid).unlink(missing_ok=True)
        return self.watch_paths(workspace, sid)

    # ------------------------------------------------------------ send and claim
    def send(self, msg) -> None:
        self.store.box_put(msg['workspace'], msg)
        touch(self.store.signal_path(msg['workspace']), msg['id'])

    def listens(self, workspace, sid, reg=None) -> bool:
        """A live session gets a message by itself, with no one typing in it: it watches the signal (its start file
        exists), its vigil is up, or it is mid-turn (the vigil comes up when the turn ends)."""
        if self.store.start_path(workspace, sid).exists() or self.store.vigil_path(workspace, sid).exists():
            return True
        reg = self.app.host.registry() if reg is None else reg
        return any(r.get('sid') == sid and r.get('status') == 'busy' for r in reg)

    def keeps_with(self, workspace, to, m, alive=None) -> bool:
        """The message stays with the session it went to while that one is alive and gets it by itself, or when it
        answers that session's ask. Otherwise another session of the workspace takes it."""
        if not (to in alive if alive is not None else self.app.host.session_alive(to)):
            return False
        return bool(m.get('ask')) or self.listens(workspace, to)

    def claim(self, workspace, sid, alive=None, thread=None) -> list[dict]:
        """Take this session's messages out of the box, and the workspace's messages whose session is gone or alive
        and deaf (unless they answer its ask). With `thread`, every message of that thread, whoever it went to: the
        session answering it takes them, so the one they went to does not answer again later."""
        out = []
        for key, m in self.store.box_list(workspace):
            to = m.get('sid')
            if thread is not None:
                if m.get('thread') != thread:
                    continue
            elif to not in (None, sid) and self.keeps_with(workspace, to, m, alive):
                continue
            got = self.store.box_claim(workspace, key, {'delivered_at': now().isoformat(), 'to_sid': sid})
            if got:
                out.append(got)
        return out

    def release(self, workspace, sid) -> int:
        """Session ending: its vigil leaves, and what it did not read goes back to the workspace for the next one."""
        for p in (self.store.vigil_path(workspace, sid), self.store.start_path(workspace, sid),
                  self.store.woke_path(workspace, sid)):
            p.unlink(missing_ok=True)
        back = 0
        for key, m in self.store.box_list(workspace):
            if m.get('sid') == sid:
                m.update(sid=None, from_sid=sid)
                self.store.box_update(workspace, key, m)
                back += 1
        if back:
            touch(self.store.signal_path(workspace), 'returned')  # another live session of the workspace takes them
        return back

    # ------------------------------------------------------------ the text a session reads
    def deliver(self, who, msgs, via) -> str:
        app, t = self.app, self.app.text
        person = app.cfg['person'].get('source') or 'person'
        parts = []
        for m in msgs:
            if m.get('notice'):  # a notice (priority): delivered as it is, no thread to answer
                app.emit('delivered', who, f"<- {m.get('from') or 'signalbox'}: {m.get('text')}", 'hook',
                         id=m.get('id'), via=via, notice=m['notice'])
                parts.append(m.get('text'))
                continue
            tid = m.get('thread')
            app.emit('delivered', who, f"<- {person}: {m.get('text')}", 'hook', id=m.get('id'), ask=m.get('ask'),
                     via=via, thread=tid)
            if tid:
                threads_mod.follow(app, tid, who)
            source = m.get('from') or person
            if tid and (m.get('turn') or 1) > 1:
                th = app.store.get_thread(tid) or {}
                parts.append(t('delivery.turn', source=source, workspace=m.get('workspace'), time=hhmm(m.get('at')),
                               thread=tid, turn=m['turn'],
                               closed=t('delivery.closed') if th.get('state') == 'closed' else '', text=m.get('text')))
                continue
            about = t('delivery.about_none')
            if m.get('ask'):
                about = t('delivery.about_ask', n=m['ask'], ask_text=f" ({m['ask_text']})" if m.get('ask_text') else '')
            parts.append(t('delivery.first', source=source, workspace=m.get('workspace'), time=hhmm(m.get('at')),
                           about=about, id=m.get('id'), opens=t('delivery.opens', thread=tid) if tid else '',
                           text=m.get('text')))
        extra = [t('delivery.footer_thread') if any(m.get('thread') for m in msgs) else '',
                 t('delivery.footer_first') if any(m.get('thread') and not m.get('ask') and (m.get('turn') or 1) == 1
                                                   for m in msgs) else '',
                 t('delivery.footer_person') if any(not m.get('notice') for m in msgs) else t('delivery.footer_notice')]
        parts += [x for x in extra if x]
        return '\n\n'.join(parts)

    def wake_text(self, workspace, sid, file_path, who, alive=None):
        """What a touched watched file has for this session, or None. The workspace's signal: its messages. Its own
        start file: the messages and the threads owed to it, once per start."""
        name = Path(str(file_path or '')).name
        if name == self.store.signal_path(workspace).name:
            msgs = self.claim(workspace, sid)
            return self.deliver(who, msgs, 'signal') if msgs else None
        if name != self.store.start_path(workspace, sid).name or self.store.woke_path(workspace, sid).exists():
            return None
        if alive is None:
            alive = {s['sid'] for s in self.app.live_sessions()}
        msgs = self.claim(workspace, sid, alive=alive)
        parts = [self.deliver(who, msgs, 'start')] if msgs else []
        delivered = {m.get('thread') for m in msgs}
        owed = [th for th in threads_mod.owed(self.app, workspace, sid, alive) if th['id'] not in delivered]
        if owed:
            parts.append(self.app.text('wake.owed', ids=', '.join(th['id'] for th in owed)))
        if not parts:
            return None
        touch(self.store.woke_path(workspace, sid), 'woke')
        return '\n\n'.join(parts)

    def start_touch(self, workspace, sid) -> None:
        """Touch the start file until the session woke up for it (or gave up: nothing owed, or the session ended)."""
        for wait in self.start_touches:
            time.sleep(wait)
            if self.store.woke_path(workspace, sid).exists() or not self.store.start_path(workspace, sid).exists():
                return
            touch(self.store.start_path(workspace, sid), 'touch')

    def vigil(self, who, sid) -> tuple[int, str]:
        """While the session sits idle, wait for a message to it; (2, text) wakes it. The next Stop starts a new vigil
        and this one leaves; so does the session's end (the end hook removes the mark)."""
        workspace = who['workspace']
        mark = self.store.vigil_path(workspace, sid)
        mark.parent.mkdir(parents=True, exist_ok=True)
        token = f'{os.getpid()} {time.time()}'
        mark.write_text(token, encoding='utf-8')

        def mine():
            try:
                return mark.read_text(encoding='utf-8') == token
            except OSError:
                return False

        end = time.time() + self.vigil_max
        checked = time.time()
        ticked = 0.0
        while time.time() < end:
            time.sleep(self.vigil_step)
            if not mine():
                return 0, ''
            if time.time() - ticked > 10:
                ticked = time.time()
                for fn in self.app.registry.hook_handlers.get('idle_tick', []):
                    try:
                        fn(self.app, {'session_id': sid, 'who': who}, who)
                    except Exception:  # noqa: BLE001
                        pass
            msgs = self.claim(workspace, sid)
            if msgs:
                text = self.deliver(who, msgs, 'vigil')
                if mine():
                    mark.unlink(missing_ok=True)
                return 2, text
            if time.time() - checked > 60:
                checked = time.time()
                if not self.app.host.session_alive(sid):
                    if mine():
                        mark.unlink(missing_ok=True)
                    return 0, ''
        if mine():
            mark.unlink(missing_ok=True)  # the mark says the session listens: it leaves with the vigil
        return 0, ''
