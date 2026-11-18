"""Asks and threads: what a session waits on from the person, and the back and forth between them.

An ask is opened by a session and lives on its status card (`asks`, numbered per session): a type, a red flag, one
self-contained text. A thread is opened by the person (`send`): every message the person sends to a workspace opens a
thread with the message's id, and the session and the person write turns in it until the person closes it.

Thread file (`threads/<id>.json`): id, workspace, sid, name, state (open | closed), opened_at, closed_at, ask,
ask_text, turns [{n, from (person | session), at, text, message, name, sid}].
"""
from __future__ import annotations

import json
import re

from .sessions import label
from .util import ID_RE, Refuse, clean_text, first_line, hhmm, local_hhmm, new_id, now, strip_accents

PERSON, SESSION = 'person', 'session'


# ---------------------------------------------------------------- asks

def ask_types(app) -> dict:
    """normalized key -> label."""
    return {strip_accents(k).strip(): v for k, v in (app.cfg['asks'].get('types') or {}).items()}


def ask(app, text=None, type_=None, alert=False, close=None, note=None, sid=None):
    """Open an ask on the calling session's card, close one (`close=N`), or list them (no text)."""
    mine = app.who_of(sid)
    with app.store.status_lock():
        card = app.store.get_status(mine['sid'])
        card.setdefault('workspace', mine['workspace'])
        card.setdefault('name', mine.get('name'))
        asks = card.get('asks') or []
        if close is not None:
            try:
                n = int(close)
            except (TypeError, ValueError):
                raise app.refuse('ask.bad_close') from None
            found = next((a for a in asks if a.get('n') == n), None)
            if not found:
                open_ = ', '.join(f"#{a.get('n')}" for a in asks) or app.text('ask.none_word')
                raise app.refuse('ask.not_open', n=n, open=open_)
            card['asks'] = [a for a in asks if a.get('n') != n]
            app.store.put_status(mine['sid'], card)
            app.emit('ask', mine, app.text('ask.ev_closed', n=n, text=found.get('text'),
                                           note=app.text('ask.ev_note', note=note) if note else ''), action='close',
                     n=n, type=found.get('type'), note=note)
            print(app.text('ask.closed', n=n, count=len(card['asks'])))
            return None
        if not text:
            for a in asks:
                print(app.text('ask.list_line', n=a.get('n'), type=a.get('type'),
                               red=app.text('ask.list_red') if a.get('alert') else '', time=hhmm(a.get('at')),
                               text=a.get('text')))
            if not asks:
                print(app.text('ask.none_open'))
            return None
        types = ask_types(app)
        t = types.get(strip_accents(type_ or app.cfg['asks'].get('default') or 'question').strip())
        if not t:
            raise app.refuse('ask.bad_type', type=type_, types=', '.join(types.values()))
        n = int(card.get('ask_seq') or 0) + 1
        red = set(app.cfg['asks'].get('red') or [])
        entry = {'n': n, 'type': t, 'text': text, 'alert': bool(alert or t in red), 'at': now().isoformat()}
        card['ask_seq'] = n
        card['asks'] = asks + [entry]
        app.store.put_status(mine['sid'], card)
    app.emit('ask', mine, f'#{n} {t}: {text}', action='open', n=n, type=t, alert=entry['alert'] or None)
    print(app.text('ask.opened', n=n, type=t, red=app.text('ask.opened_red') if entry['alert'] else '',
                   label=label(mine)))
    return entry


# ---------------------------------------------------------------- threads

def check_id(tid):
    if not ID_RE.fullmatch(tid or ''):
        raise Refuse(f'invalid thread id (the id of the message that opened it, YYYYMMDD-HHMMSS-xxxxxx): {tid}')


def read(app, tid):
    check_id(tid)
    t = app.store.get_thread(tid)
    if not t:
        raise Refuse(f'thread {tid} does not exist: a thread is born from a message of the person')
    return t


def check_open(t):
    if t.get('state') == 'closed':
        raise Refuse(f"the person closed thread {t['id']} at {hhmm(t.get('closed_at'))}: what is left goes as an ask")


def turn_text(app, text):
    text = clean_text(text)
    if not text:
        raise Refuse('empty turn')
    cap = app.cfg['caps']['text']
    if len(text) > cap:
        raise Refuse(f'turn with {len(text)} characters (cap {cap}): put the long part in a file and send the summary '
                     f'with its path')
    return text


def open_thread(app, msg, name=None):
    t = {'id': msg['id'], 'workspace': msg['workspace'], 'sid': msg.get('sid'), 'name': name, 'state': 'open',
         'opened_at': msg['at'], 'closed_at': None, 'ask': msg.get('ask'), 'ask_text': msg.get('ask_text'),
         'turns': [{'n': 1, 'from': PERSON, 'at': msg['at'], 'text': msg['text'], 'message': msg['id']}]}
    with app.store.threads_lock():
        app.store.put_thread(t)
    app.emit('thread', None, f"open with {msg['workspace']}: {msg['text']}", action='open', id=t['id'],
             to_workspace=msg['workspace'], to_sid=msg.get('sid'), ask=msg.get('ask'))
    return t


def open_threads(app, workspace=None):
    return sorted((t for t in app.store.list_threads()
                   if t.get('state') == 'open' and workspace in (None, t.get('workspace'))),
                  key=lambda t: t.get('opened_at') or '')


def owes_reply(t) -> bool:
    """Open, and the person spoke last: the workspace owes an answer."""
    return t.get('state') == 'open' and (t.get('turns') or [{}])[-1].get('from') == PERSON


def in_box(app, t) -> bool:
    """A message of the thread still sits in the workspace's box: no session has seen it."""
    return any(m.get('thread') == t.get('id') for _, m in app.store.box_list(str(t.get('workspace'))))


def owed(app, workspace, sid, alive) -> list[dict]:
    """The open threads of the workspace waiting for an answer that fall to this session: its own, with no session,
    with one that is gone, or with a live one that does not listen (unless it answers that one's ask). One with a
    live session that listens stays with it, even while its message still sits in the box."""
    return [t for t in open_threads(app, workspace)
            if owes_reply(t) and (t.get('sid') in (None, sid) or t.get('sid') not in alive
                                  or (not t.get('ask') and not app.delivery.listens(workspace, t['sid'])))]


def line(app, t) -> str:
    f = (t.get('turns') or [{}])[-1]
    who = PERSON if f.get('from') == PERSON else f"{t.get('workspace')} {f.get('name') or ''}".strip()
    waits = ''
    if owes_reply(t):
        waits = '  <- owes a reply' + (', message still in the box' if in_box(app, t) else '')
    return (f"{t['id']}  since {local_hhmm(t.get('opened_at'))}, {len(t.get('turns') or [])} turns, last: {who} "
            f"{hhmm(f.get('at'))} \"{first_line(f.get('text'), 90)}\"{waits}")


def follow(app, tid, who):
    """The session that received the person's words is the one the next turn goes to. Never raises."""
    try:
        with app.store.threads_lock():
            t = app.store.get_thread(tid)
            if t and who.get('sid') and t.get('sid') != who['sid']:
                t.update(sid=who['sid'], name=who.get('name') or t.get('name'))
                app.store.put_thread(t)
    except Exception:  # noqa: BLE001
        pass


def to_chat(app, t) -> dict:
    """The thread in chat-thread's message shape: the person's turns are `me`, the session's `other`."""
    source = app.cfg['person'].get('source') or PERSON
    pending = {m.get('id') for _, m in app.store.box_list(str(t.get('workspace')))}
    msgs = []
    for turn in t.get('turns') or []:
        mine = turn.get('from') == PERSON
        msgs.append({'id': f"{t['id']}.{turn.get('n')}", 'from': 'me' if mine else 'other', 'kind': 'bubble',
                     'author': source if mine else f"{t.get('workspace')} {turn.get('name') or ''}".strip(),
                     'text': turn.get('text') or '', 'at': turn.get('at'), 'tag': t['id'],
                     **({'state': 'sent' if turn.get('message') in pending else 'delivered'} if mine else {})})
    return {'thread': t['id'], 'workspace': t.get('workspace'), 'state': t.get('state'),
            'title': first_line((t.get('turns') or [{}])[0].get('text'), 80), 'messages': msgs}


# ---------------------------------------------------------------- the person's side

def send(app, workspace, text, sid=None, ask_n=None, source=None, echo=True, mid=None, progress=None):
    """The person's message to a workspace: one file in its box, picked up by the session's hooks; it opens a thread
    with the message's id. With no sid: a live session that listens, the newest card first, then the newest start;
    none listening, no owner, and the first session of the workspace that can takes it. `echo=False`: the caller
    (a claimed offer, `serve`) prints its own answer. `mid`: the message's id (a claimed offer gives its own, so a
    repeated claim delivers the same message); `progress['delivered']` turns true once the box has it."""
    workspace = app.workspaces.norm(workspace)
    if not app.workspaces.valid(workspace):
        raise Refuse(f'invalid workspace: {workspace}')
    if sid and not re.fullmatch(r'[0-9A-Za-z-]{8,64}', sid):
        raise Refuse('invalid sid')
    try:
        n = int(ask_n) if ask_n not in (None, '') else None
    except ValueError:
        raise Refuse('--ask takes the number of the ask') from None
    text = clean_text(text)
    if not text:
        raise Refuse('empty message')
    if not sid:
        try:
            of_ws = [s for s in app.live_sessions() if s.get('workspace') == workspace]
        except Exception:  # noqa: BLE001
            of_ws = []
        listening = [s for s in of_ws if app.delivery.listens(workspace, s['sid'], of_ws)]
        sid = max(listening, key=lambda s: ((s.get('card') or {}).get('updated') or '', s.get('started_at') or 0)
                  )['sid'] if listening else None
    card = app.store.get_status(sid) if sid else {}
    entry = next((a for a in card.get('asks') or [] if a.get('n') == n), None) if n else None
    mid = mid or new_id()
    msg = {'id': mid, 'workspace': workspace, 'sid': sid, 'ask': n, 'ask_text': entry.get('text') if entry else None,
           'ask_type': entry.get('type') if entry else None, 'text': text, 'at': now().isoformat(), 'thread': mid,
           'turn': 1}
    if source:
        msg['from'] = source
    app.delivery.send(msg)
    if progress is not None:
        progress['delivered'] = True
    open_thread(app, msg, card.get('name'))
    body = '\n'.join(f'  > {l}' for l in text.splitlines())
    who = source or app.cfg['person'].get('source') or PERSON
    app.log_line(f"- {now():%H:%M:%S} **{who} -> {workspace}**{f' (ask #{n})' if n else ''}\n{body}\n")
    app.emit('send', None, f'-> {workspace}: {text}', to_workspace=workspace, to_sid=sid, ask=n, id=mid)
    out = {'ok': True, 'id': mid, 'sid': sid}
    if echo:
        print(json.dumps(out))
    return out


def thread(app, tid=None, text=None, from_person=False, close=False, sid=None, as_json=False):
    """A thread the person opened: the session writes turns in it until the person closes it; the person's turns are
    delivered to the session like a message, with the thread id in the header."""
    if not tid:
        mine = app.who_of(sid)
        open_ = open_threads(app, mine['workspace'])
        if as_json:
            print(json.dumps([to_chat(app, t) for t in open_], ensure_ascii=False))
            return open_
        for t in open_:
            print(line(app, t))
        if not open_:
            print(f"no open thread in {mine['workspace']}")
        return open_
    if close:
        with app.store.threads_lock():
            t = read(app, tid)
            if t.get('state') != 'closed':
                t.update(state='closed', closed_at=now().isoformat())
                app.store.put_thread(t)
                app.emit('thread', None, f"closed with {t['workspace']}", action='close', id=tid,
                         to_workspace=t['workspace'])
        print(json.dumps({'ok': True, 'id': tid, 'closed_at': t['closed_at']}))
        return t
    t = read(app, tid)
    if text is None and not from_person:
        if as_json:
            print(json.dumps(to_chat(app, t), ensure_ascii=False))
            return t
        print(f"thread {tid} with {t['workspace']}, {t.get('state')}, since {local_hhmm(t.get('opened_at'))}")
        for f in t.get('turns') or []:
            who = PERSON if f.get('from') == PERSON else f"{t['workspace']} {f.get('name') or ''}".strip()
            print(f"\n[{f.get('n')}] {who}, {local_hhmm(f.get('at'))}\n{f.get('text')}")
        return t
    check_open(t)
    text = turn_text(app, text)
    if from_person:
        with app.store.threads_lock():
            t = read(app, tid)
            check_open(t)
            n = len(t['turns']) + 1
            # gone, or alive and deaf: the first session of the workspace that can takes it
            to = t.get('sid') if t.get('sid') and app.host.session_alive(t['sid']) \
                and app.delivery.listens(t['workspace'], t['sid']) else None
            msg = {'id': new_id(), 'workspace': t['workspace'], 'sid': to, 'ask': None, 'ask_text': None,
                   'ask_type': None, 'text': text, 'at': now().isoformat(), 'thread': tid, 'turn': n}
            t['turns'].append({'n': n, 'from': PERSON, 'at': msg['at'], 'text': text, 'message': msg['id']})
            app.store.put_thread(t)
        app.delivery.send(msg)
        body = '\n'.join(f'  > {l}' for l in text.splitlines())
        who = app.cfg['person'].get('source') or PERSON
        app.log_line(f"- {now():%H:%M:%S} **{who} -> {t['workspace']}** (thread {tid})\n{body}\n")
        app.emit('thread', None, f"{who} -> {t['workspace']}: {text}", action='turn', id=tid, n=n, by=PERSON,
                 to_workspace=t['workspace'], to_sid=to, message=msg['id'])
        out = {'ok': True, 'id': tid, 'n': n, 'message': msg['id']}
        print(json.dumps(out))
        return out
    mine = app.who_of(sid)
    if t.get('workspace') != mine['workspace']:
        raise Refuse(f"thread {tid} belongs to {t.get('workspace')}, not to {mine['workspace']}")
    with app.store.threads_lock():
        t = read(app, tid)
        check_open(t)
        n = len(t['turns']) + 1
        t['turns'].append({'n': n, 'from': SESSION, 'at': now().isoformat(), 'text': text, 'name': mine.get('name'),
                           'sid': mine['sid']})
        t.update(sid=mine['sid'], name=mine.get('name'))
        app.store.put_thread(t)
    who = app.cfg['person'].get('source') or PERSON
    app.log_line(f"- {now():%H:%M:%S} **{label(mine)} -> {who}** (thread {tid})\n"
                 + '\n'.join(f'  > {l}' for l in text.splitlines()) + '\n')
    app.emit('thread', mine, text, action='turn', id=tid, n=n, by=SESSION)
    for m in app.delivery.claim(t['workspace'], mine['sid'], thread=tid):
        app.emit('delivered', mine, f"<- {who}: {m.get('text')}", id=m.get('id'), ask=m.get('ask'), via='thread',
                 thread=tid)
        print(f"message {m['id']} of this thread was still in the box"
              f"{' (it was for ' + m['sid'] + ')' if m.get('sid') else ''}: it came along with your turn, and the other "
              'session does not answer it again')
    print(f'turn {n} in thread {tid}: the person sees it, and their next turn arrives as a message with the thread id')
    return {'n': n}
