"""Read views: board (everything at once), peek (one session), events (from the transcripts), watch (the queue)."""
from __future__ import annotations

import datetime as dt
import json
import sys
import time

from . import leases, offers, priority, threads
from .sessions import find_target, label, waiting_for_person
from .status import guide_mark
from .util import Refuse, aware, hhmm, local_hhmm, now, stamp


def inferred(s, events):
    """The phase the transcript shows, when it is newer than the card the session wrote itself."""
    last = next((e for e in reversed(events) if e[3]), None)
    if not last:
        return None
    card_time = aware((s.get('card') or {}).get('updated') or '')
    event_time = aware(last[0])
    if card_time and event_time and event_time <= card_time:
        return None
    return last


def _sections(app, res, sessions):
    out = []
    for target, fn in app.registry.board_sections:
        if target == '*' or target == res:
            try:
                out += list(fn(app, sessions, res) or [])
            except Exception as err:  # noqa: BLE001
                out.append(f'({err})')
    return out


def board(app, as_json=False):
    from .workflow import Workflow
    sessions = app.live_sessions()
    mine = app.me(sessions)
    g = Workflow(app).read()
    prio = priority.read(app)
    names = list(dict.fromkeys(app.workspaces.all() + sorted({s['workspace'] for s in sessions})))
    data = {'at': now().isoformat(), 'you': mine and {k: mine.get(k) for k in ('workspace', 'name', 'sid')},
            'guide': g and g['version'], 'workspaces': [], 'priority': prio, 'resources': [], 'threads': [],
            'offers': [], 'inbox': app.store.inbox_pending()}
    lines = [f"SIGNALBOX  {now():%Y-%m-%d %H:%M}   you: {label(mine)}   guide "
             f"{g['version'] if g else '(no flow block)'}\n", 'Sessions']
    for ws in names:
        br = app.branch(ws) or ''
        pos = priority.position_text(app, ws, prio)
        of_ws = [s for s in sessions if s['workspace'] == ws]
        entry = {'workspace': ws, 'branch': br or None, 'priority': priority.position(app, ws, prio), 'sessions': []}
        data['workspaces'].append(entry)
        if not of_ws:
            lines.append(f'  {ws:5} (no session)  {br}' + (f'  {pos}' if pos else ''))
        for i, s in enumerate(of_ws):
            c = s.get('card') or {}
            card = ' · '.join(str(x) for x in [c.get('item'), c.get('task') and f"task {c['task']}", c.get('phase'),
                                               guide_mark(app, c),
                                               f"waiting: {c['waiting']}" if c.get('waiting') else None] if x)
            text = f"  \"{c['text']}\"" if c.get('text') else ''
            when = f" ({hhmm(c['updated'])})" if c.get('updated') else ''
            lines.append(f"  {ws:5} {s.get('name') or '':12} {s.get('status') or '?':5} "
                         f"{br if i == 0 else '(same tree)':40} {card or 'no status'}{f' · {pos}' if pos else ''}{when}"
                         f"{text}")
            listens = app.delivery.listens(ws, s['sid'], sessions)
            if not listens:
                lines.append('        does not listen (opened before delivery was armed, no turn since): a message '
                             'goes to another session of the workspace; reopen the tab or type in it')
            events = app.host.transcript_events(s, 1_500_000)
            auto = inferred(s, events)
            if auto:
                s['auto'] = auto
                lines.append(f"        phase from the transcript: {auto[3]} ({auto[1]} {auto[2]}, "
                             f"{local_hhmm(auto[0])}); status out of date")
            if events:
                lines.append(f"        last event: {local_hhmm(events[-1][0])} {events[-1][1]} {events[-1][2]}")
            entry['sessions'].append({'sid': s['sid'], 'name': s.get('name'), 'status': s.get('status'), 'card': c,
                                      'listens': listens, 'inferred': auto and auto[3]})
    if prio['order']:
        lines.append(f"\nDelivery priority: {' > '.join(prio['order'])} (by {prio['by']}, "
                     f"{(prio['at'] or '?')[:16].replace('T', ' ')}). It only settles an impasse: with no answer in "
                     f"{app.cfg['priority']['wait_minutes']} min, the lower number goes on")
    lines.append('\nResources')
    for res, spec in app.resources().items():
        current = leases.lease(app, res, sessions)
        q = leases.queue(app, res)
        line = f'  {res:9} {leases.holder_text(current)}'
        users = leases.users(app, res, sessions) if spec.get('shared') else []
        if users:
            line += f"   shared: {', '.join(label(u) for u in users)}"
        ahead = []
        if q:
            line += f"   queue: {', '.join(label(w) + (' (' + w['mode'] + ')' if w.get('mode') else '') for w in q)}"
            ahead = priority.ahead_in_queue(app, res, current, sessions, prio) if current and current.get('alive') else []
            if ahead:
                line += (f"   higher priority in the queue: {', '.join(label(w) for w in ahead)} (after "
                         f"{app.cfg['priority']['wait_minutes']} min waiting for the person, the holder releases)")
        lines.append(line)
        extra = _sections(app, res, sessions)
        lines += [f'            {x}' for x in extra]
        data['resources'].append({'name': res, 'description': spec.get('description'), 'holder': current,
                                  'queue': q, 'shared': users, 'ahead': [label(w) for w in ahead], 'facts': extra})
    for target, fn in app.registry.board_sections:
        if target is None:
            try:
                lines += list(fn(app, sessions) or [])
            except Exception as err:  # noqa: BLE001
                lines.append(f'({err})')
    words = app.cfg['person'].get('waiting_words') or []
    waiting = [s for s in sessions if waiting_for_person(s.get('card'), words) and not s.get('auto')]
    if waiting:
        lines.append('\nWaiting for the person')
        for s in waiting:
            lines.append(f"  {label(s)}: {s['card'].get('item') or ''} {s['card'].get('text') or s['card'].get('waiting')}")
    open_ = threads.open_threads(app)
    if open_:
        lines.append('\nOpen threads')
        for t in open_:
            lines.append(f"  {t.get('workspace'):5} {threads.line(app, t)}")
        data['threads'] = [threads.to_chat(app, t) for t in open_]
    data['offers'] = offers.still_open(app)
    if data['offers']:
        lines.append('\nOpen offers (signalbox claim <id>, or claim --next)')
        lines += [f'  {offers.line(app, o)}' for o in data['offers']]
    if data['inbox']:
        lines.append(f"\nInbox with notes for: {', '.join(data['inbox'])}")
    log = app.store.log_read(f'{now():%Y-%m-%d}')
    if log:
        n = sum(1 for l in log.splitlines() if l.startswith('- '))
        data['log_today'] = n
        lines.append(f'\nLog of today: {n} messages (signalbox log)')
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=1, default=str))
    else:
        print('\n'.join(lines))
    return data


def watch_board(app, seconds, as_json=False, rounds=None) -> int:
    """`board --watch`: the board again every few seconds, the screen cleared in between, until Ctrl+C (or `rounds`,
    for a test)."""
    n = 0
    try:
        while rounds is None or n < rounds:
            if not as_json and sys.stdout.isatty():
                print('\x1b[2J\x1b[H', end='')
            board(app, as_json)
            n += 1
            if rounds is None or n < rounds:
                time.sleep(max(0.5, float(seconds)))
    except KeyboardInterrupt:
        pass
    return 0


def peek(app, target, as_json=False):
    sessions = app.live_sessions()
    hits = find_target(sessions, target)
    if not hits:
        ws = target.lower()
        if ws in app.workspaces.all():
            hits = [{'workspace': ws, 'name': app.text('peek.no_session'), 'card': {}, 'sid': None}]
        else:
            raise Refuse(app.text('peek.not_found', target=target, live=', '.join(label(s) for s in sessions)))
    out = []
    for s in hits:
        br = app.branch(s['workspace']) or ''
        lines = [app.text('peek.header', label=label(s), status=s.get('status') or '', branch=br)]
        prio = priority.read(app)
        pos = priority.position_text(app, s['workspace'], prio)
        if pos:
            lines.append(app.text('peek.priority', position=pos, order=' > '.join(prio['order']), by=prio['by'],
                                  time=hhmm(prio['at'])))
        c = s.get('card') or {}
        if c:
            lines.append(app.text('peek.card', item=c.get('item') or '-', task=c.get('task') or '-',
                                  phase=c.get('phase') or '-', waiting=c.get('waiting') or '-', text=c.get('text') or '',
                                  time=hhmm(c.get('updated'))))
            mark = guide_mark(app, c, long=True)
            lines.append(app.text('peek.guide', guide=c.get('guide') or '-',
                                  mark=app.text('peek.guide_mark', mark=mark) if mark else ''))
            if c.get('tasks'):
                lines.append(app.text('peek.tasks', tasks=', '.join(
                    app.text('peek.task', task=k, phase=v.get('phase'), time=hhmm(v.get('ts')))
                    for k, v in c['tasks'].items())))
            asks = app.text('peek.asks', asks=' | '.join(f"#{a['n']} {a['type']}: {a['text']}"
                                                          for a in c.get('asks') or []))
            if c.get('asks') and asks:  # an empty template leaves the line out
                lines.append(asks)
        for fn in app.registry.peek_sections:
            try:
                lines += list(fn(app, s) or [])
            except Exception as err:  # noqa: BLE001
                lines.append(f'({err})')
        words = prompt = None
        if s.get('sid'):
            t = app.host.transcript(s)
            if t:
                words, prompt = app.host.last_words(t)
                if prompt:
                    lines.append(app.text('peek.last_prompt', at=prompt[0], text=prompt[1]))
                for ts, text in words:
                    lines.append(app.text('peek.words', at=ts, text=text))
        out.append({'session': {k: s.get(k) for k in ('workspace', 'name', 'sid', 'status')}, 'branch': br or None,
                    'card': c, 'priority': pos, 'last_prompt': prompt, 'last_words': words, 'lines': lines})
        if not as_json:
            print('\n'.join(lines) + '\n')
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return out


def fmt_event(s, event):
    return f"{local_hhmm(event[0])} {s['workspace']} {s.get('name') or '':11} {event[1]:8} {event[2]}"


def events(app, n=40, workspace=None, as_json=False):
    sessions = [s for s in app.live_sessions() if not workspace or s['workspace'] == workspace]
    rows = sorted(((e, s) for s in sessions for e in app.host.transcript_events(s)), key=lambda r: r[0][0])[-n:]
    if as_json:
        print(json.dumps([{'ts': e[0], 'kind': e[1], 'text': e[2], 'phase': e[3], 'workspace': s['workspace'],
                           'name': s.get('name'), 'sid': s['sid']} for e, s in rows], ensure_ascii=False))
    else:
        for event, s in rows:
            print(fmt_event(s, event))
    return rows


def fmt_queued(e):
    return (f"{local_hhmm(e.get('ts'))} {e.get('workspace') or '-':5} {e.get('name') or '':11} {e.get('kind'):8} "
            f"{e.get('text') or ''}")


PUMP_KIND = {'prompt': 'prompt', 'sent': 'msg'}


def is_hooked(e):
    """An event only the hooks write: that session writes its own events, so the pump leaves it alone. A `msg` does
    not count: an older message hook may write it too."""
    return e.get('src') == 'hook' and e.get('kind') not in ('msg', 'wait')


def watch(app, interval=5.0, rounds=None):
    """One line per new event of the other sessions, read from the queue. While this session holds the
    `watch.pump_resource` lease, it also pumps into the queue what sessions without hooks cannot write themselves (their
    skills, commands, prompts and messages, read from the transcripts)."""
    history, offset = app.events.read(0)
    hooked = {e.get('sid') for e in history if is_hooked(e)}
    hook_msgs = {(e.get('sid'), e.get('head')) for e in history if e.get('kind') == 'msg' and e.get('src') == 'hook'}
    pumped = {}
    for e in history:
        t = aware(e.get('ts') or '')
        if e.get('src') == 'transcript' and t and (e.get('sid') not in pumped or t > pumped[e.get('sid')]):
            pumped[e.get('sid')] = t
    start = dt.datetime.now().astimezone()
    last_kind = {e.get('sid'): e.get('kind') for e in history}
    pump = app.cfg['watch'].get('pump_resource')
    n = 0
    while rounds is None or n < rounds:
        n += 1
        sessions = app.live_sessions()
        mine = app.me(sessions)
        new, offset = app.events.read(offset)
        for e in new:
            repeated = e.get('kind') == 'idle' and last_kind.get(e.get('sid')) == 'idle'
            last_kind[e.get('sid')] = e.get('kind')
            if is_hooked(e):
                hooked.add(e.get('sid'))
            if e.get('kind') == 'msg' and e.get('src') == 'hook':
                hook_msgs.add((e.get('sid'), e.get('head')))
            with_me = mine and (e.get('to') == label(mine) or (e.get('text') or '').startswith(f'<- {label(mine)}:'))
            if not repeated and not with_me and (not mine or e.get('sid') != mine['sid']):
                print(fmt_queued(e), flush=True)
        holder = leases.lease(app, pump, sessions) if pump else None
        if mine and holder and holder.get('sid') == mine['sid']:
            for s in sessions:
                sid = s['sid']
                if sid in hooked:
                    continue
                for ts, kind, text, phase in app.host.transcript_events(s, 600_000):
                    t = aware(ts)
                    if not t or t <= pumped.get(sid, start):
                        continue
                    pumped[sid] = t
                    head = text.split(': ', 1)[-1][:80] if kind == 'sent' else None
                    if head and (sid, head) in hook_msgs:
                        continue
                    pump_kind = {**PUMP_KIND, (app.cfg['events'].get('prompt_kind') or 'prompt'): 'prompt'}
                    app.emit(pump_kind.get(kind, kind), s, text, 'transcript', ts=stamp(t), phase=phase,
                             **({kind: text} if kind in ('skill', 'agent', 'command') else {}))
        if rounds is None or n < rounds:
            time.sleep(interval)
