"""Host-neutral hook handlers. Each takes the hook's input (a dict) and returns an exit code; what goes into the
session is printed (stdout: context; stderr with exit 2: wakes the session). A hook never fails: an error is one line
on stderr and exit 0.

    start        SessionStart: who the session is, peers, leases, priority, inbox, threads, what waits; arms delivery
    prompt       UserPromptSubmit: what waits for the session, then the prompt or command as an event
    tool         PostToolUse (skills, agents, messages to other sessions)
    stop         Stop: the turn ended (its last words) and the priority check after a delivery
    vigil        Stop, asyncRewake: waits in the background while idle and wakes the session on a message
    wake         FileChanged, asyncRewake: a watched file was touched; delivers and wakes
    notify       Notification
    end          SessionEnd: what the session did not read goes back to its workspace
    precompact   PreCompact: keeps the card, the asks and the person's last prompts
    postcompact  SessionStart (compact): gives them back
    guard        PreToolUse: the guard (guard.py)
"""
from __future__ import annotations

import html
import json
import os
import re
import sys

from . import leases, priority, threads
from .sessions import label
from .util import first_line, hhmm, now


def _handlers(app, event, data, who=None):
    out = []
    for fn in app.registry.hook_handlers.get(event, []):
        try:
            r = fn(app, data, who)
        except Exception:  # noqa: BLE001
            r = None
        if r:
            out.append(r)
    return out


def start(app, data):
    ws = app.workspaces.of(data.get('cwd') or os.getcwd())
    if not ws:
        return 0
    t = app.text
    who = app.host.hook_who(data)
    sid = data.get('session_id')
    app.emit('session', who, f"session opened ({data.get('source') or 'startup'})", 'hook', action='start',
             source=data.get('source'))
    sessions = app.live_sessions()
    mine = app.me(sessions, sid)
    peers = [s for s in sessions if not mine or s['sid'] != mine['sid']]
    lines = [t('start.title'),
             t('start.you', workspace=ws, session=t('start.session', name=mine.get('name')) if mine else '')]
    if peers:
        lines.append(t('start.peers', peers='; '.join(
            f"{s['workspace']} {s.get('name')} ({s.get('status')}"
            + (f", {s['card'].get('item')} {s['card'].get('phase') or ''}" if (s.get('card') or {}).get('item') else '')
            + ')' for s in peers)))
    held = []
    for res, spec in app.resources().items():
        current = leases.lease(app, res, sessions)
        if current:
            mode = f" in {current['mode']} mode" if current.get('mode') else ''
            held.append(f"{res}{mode} with {label(current)} since {hhmm(current['since'])}")
        if spec.get('shared'):
            us = leases.users(app, res, sessions)
            if us:
                held.append(f"{res} in use by {', '.join(label(u) for u in us)}")
    if held:
        lines.append(t('start.leases', leases='; '.join(held)))
    p = priority.read(app)
    if p['order']:
        pos = priority.position(app, ws, p)
        lines.append(t('start.priority', order=' > '.join(p['order']),
                       position=(t('start.priority_you', n=pos, total=len(p['order'])) if pos
                                 else t('start.priority_out', workspace=ws)),
                       minutes=app.cfg['priority']['wait_minutes']))
    if ws in app.store.inbox_pending():
        lines.append(t('start.inbox', workspace=ws))
    if t('start.reminders'):
        lines.append(t('start.reminders'))
    for fn in app.registry.start_lines:
        try:
            lines += list(fn(app, who, sessions) or [])
        except Exception:  # noqa: BLE001
            pass
    alive = {s['sid'] for s in sessions}
    msgs = app.delivery.claim(ws, sid, alive=alive) if sid else []
    open_ = threads.open_threads(app, ws)
    if open_:
        lines.append(t('start.threads_open', workspace=ws, threads='; '.join(threads.line(app, x) for x in open_)))
    delivered = {m.get('thread') for m in msgs}
    owed = [x for x in threads.owed(app, ws, sid, alive) if x['id'] not in delivered] if sid else []
    if owed:
        lines.append(t('start.threads_owed', ids=', '.join(x['id'] for x in owed)))
    if msgs:
        lines.append(app.delivery.deliver(who, msgs, 'start'))
    watch = None
    if sid and hasattr(app.delivery, 'arm'):
        watch = app.delivery.arm(ws, sid)
        if (msgs or owed) and data.get('source') != 'compact':
            app.detach('start-touch', ws, sid)
    print(json.dumps(app.host.start_output('\n'.join(lines), watch), ensure_ascii=False))
    return 0


def prompt(app, data):
    who = app.host.hook_who(data)
    text = (data.get('prompt') or '').strip()
    if not who['workspace']:
        return 0
    if who['sid']:
        msgs = app.delivery.claim(who['workspace'], who['sid'])
        if msgs:
            print(app.delivery.deliver(who, msgs, 'prompt'))
        for line in _handlers(app, 'prompt', data, who):
            print(line)
    if not text:
        return 0
    skill_phase = app.cfg['events'].get('skill_phase') or {}
    command = re.match(r'/([\w:-]+)', text)
    if command:
        name = command.group(1)
        app.emit('command', who, text, 'hook', command=name, phase=skill_phase.get(name.split(':')[-1]))
    elif text.startswith('<cross-session-message'):
        sender = re.search(r'from-name="([^"]*)"', text)
        name = sender.group(1) if sender else '?'
        peer = next((r for r in app.host.registry() if r.get('name') == name), None)
        body = html.unescape(re.sub(r'<[^>]+>', '', text)).strip()
        app.emit('received', who, f"<- {label(peer) if peer else name}: {body}", 'hook',
                 from_workspace=peer and peer['workspace'])
    elif not text.startswith('<'):
        app.emit('prompt', who, text, 'hook')
    return 0


def tool(app, data):
    name, args = data.get('tool_name'), data.get('tool_input') or {}
    if name == 'SendMessage':
        return sent(app, data)
    if data.get('agent_id'):
        return 0  # a tool call inside a subagent, not a step of the session
    who = app.host.hook_who(data)
    if not who['workspace']:
        return 0
    if name == 'Skill':
        skill = args.get('skill') or ''
        app.emit('skill', who, f"{skill} {args.get('args') or ''}".strip(), 'hook', skill=skill,
                 phase=(app.cfg['events'].get('skill_phase') or {}).get(skill.split(':')[-1]))
    elif name in ('Agent', 'Task'):
        agent = args.get('subagent_type') or 'general-purpose'
        app.emit('agent', who, f"{agent}: {args.get('description') or ''}", 'hook', agent=agent,
                 phase=(app.cfg['events'].get('agent_phase') or {}).get(agent))
    return 0


def sent(app, data):
    tool_input = data.get('tool_input') or {}
    to = tool_input.get('to') or '?'
    message = (tool_input.get('message') or '').strip()
    if not message and not tool_input.get('notify_when_idle'):
        return 0
    if re.fullmatch(r'a[0-9a-f]{16}', to):
        return 0  # a subagent of the session itself
    sessions = app.live_sessions()
    sender = app.me(sessions, data.get('session_id')) or app.host.hook_who(data)
    target = next((s for s in sessions if s.get('name') == to.split(' [')[0]), None)
    body = '\n'.join(f'  > {l}' for l in message.splitlines()) or '  > (idle subscription only)'
    app.log_line(f"- {now():%H:%M:%S} **{label(sender)} -> {to}{f' ({target['workspace']})' if target else ''}**\n"
                 f"{body}\n")
    target_label = label(target) if target else app.host.pipes.get(to, to)
    if message:
        app.emit('msg', sender, f'-> {target_label}: {message}', 'hook', to=target_label,
                 to_workspace=target['workspace'] if target else None, head=first_line(message, 100_000)[:80])
    else:
        app.emit('wait', sender, f'waits for {target_label} to be free', 'hook', to=target_label,
                 to_workspace=target['workspace'] if target else None)
    return 0


def stop(app, data):
    who = app.host.hook_who(data)
    if who['workspace']:
        last = data.get('last_assistant_message') or app.host.last_assistant_text(data.get('transcript_path') or '')
        app.emit('idle', who, last, 'hook')
        priority.delivered(app, who)
        _handlers(app, 'stop', data, who)
    return 0


def notify(app, data):
    who = app.host.hook_who(data)
    if who['workspace']:
        app.emit('notify', who, data.get('message') or '', 'hook', type=data.get('notification_type'))
    return 0


def end(app, data):
    who = app.host.hook_who(data)
    if who['workspace']:
        app.emit('session', who, f"session closed ({data.get('reason') or '?'})", 'hook', action='end',
                 reason=data.get('reason'))
        priority.delivered(app, who)
        if who['sid']:
            app.delivery.release(who['workspace'], who['sid'])
        _handlers(app, 'end', data, who)
    return 0


def wake(app, data):
    who = app.host.hook_who(data)
    sid, ws = data.get('session_id'), who['workspace']
    if not ws or not sid or data.get('agent_id'):
        return 0
    _handlers(app, 'wake', data, who)
    text = app.delivery.wake_text(ws, sid, data.get('file_path'), who)
    if not text:
        return 0
    sys.stderr.write(text + '\n')
    sys.stderr.flush()
    return 2


def vigil(app, data):
    sid = data.get('session_id')
    who = app.host.hook_who(data)
    if not who['workspace'] or not sid or data.get('agent_id'):
        return 0
    code, text = app.delivery.vigil(who, sid)
    if code == 2:
        sys.stderr.write(text + '\n')
        sys.stderr.flush()
    return code


# ---------------------------------------------------------------- compaction

def precompact(app, data):
    ws, sid = app.workspaces.of(data.get('cwd') or os.getcwd()), data.get('session_id')
    if not ws or not sid:
        return 0
    app.store.put_snapshot(sid, {
        'workspace': ws, 'sid': sid, 'at': now().isoformat(), 'trigger': data.get('trigger'),
        'card': app.store.get_status(sid) or None,
        'prompts': app.host.last_prompts(data.get('transcript_path')) if data.get('transcript_path') else [],
    })
    return 0


def card_lines(app, c) -> list[str]:
    t, out = app.text, []
    if c.get('item'):
        out.append(t('compact.item', item=c['item'], phase=c.get('phase') or '-',
                     task=t('compact.item_task', task=c['task']) if c.get('task') else '',
                     waiting=t('compact.item_waiting', waiting=c['waiting']) if c.get('waiting') else ''))
    if c.get('text'):
        out.append(t('compact.doing', text=c['text']))
    tasks = c.get('tasks') or {}
    if tasks:
        out.append(t('compact.tasks', tasks='; '.join(f"{k} {v.get('phase') if isinstance(v, dict) else v}"
                                                       for k, v in tasks.items())))
    if c.get('done'):
        out.append(t('compact.done', at=(c.get('done_at') or '')[11:16], text=c['done']))
    asks = c.get('asks') or []
    if asks:
        out.append(t('compact.asks', asks=' | '.join(f"#{a.get('n')} {a.get('text')}" for a in asks)))
    prs = c.get('prs') or []
    if prs:
        out.append(t('compact.prs', prs=', '.join(str(p.get('n') if isinstance(p, dict) else p) for p in prs)))
    return out


def postcompact(app, data):
    if data.get('source') != 'compact':
        return 0
    ws, sid = app.workspaces.of(data.get('cwd') or os.getcwd()), data.get('session_id')
    if not ws or not sid:
        return 0
    t = app.text
    snap = app.store.get_snapshot(sid) or {}
    card = app.store.get_status(sid) or snap.get('card') or {}
    at = (snap.get('at') or '')[11:16]
    lines = [t('compact.title', workspace=ws, when=f" ({at}, {snap.get('trigger') or 'auto'})" if at else '')]
    lines += card_lines(app, card) or [t('compact.no_card')]
    for p in snap.get('prompts') or []:
        lines.append(t('compact.asked', at=(p.get('at') or '')[11:16], text=p.get('text')))
    lines.append(t('compact.footer', workspace=ws, guide=app.cfg.guide.as_posix() if app.cfg.guide else ''))
    print(json.dumps(app.host.start_output('\n'.join(lines)), ensure_ascii=False))
    return 0


HOOKS = {'start': start, 'prompt': prompt, 'tool': tool, 'stop': stop, 'notify': notify, 'end': end, 'wake': wake,
         'vigil': vigil, 'precompact': precompact, 'postcompact': postcompact}


def run(app, name, data=None) -> int:
    """Run one hook; never raises."""
    try:
        if data is None:
            data = app.host.read_input()
        if name == 'guard':
            from . import guard
            return guard.hook(app, data)
        fn = HOOKS.get(name)
        if not fn:
            print(f'signalbox: unknown hook {name}', file=sys.stderr)
            return 0
        return fn(app, data) or 0
    except Exception as err:  # noqa: BLE001
        print(f'signalbox hook {name}: {err!r}', file=sys.stderr)
        return 0
