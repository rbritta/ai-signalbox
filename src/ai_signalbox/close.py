"""Closing a session for good, when the person asks.

With a target that is not the caller: the request to close it, printed ready for the host's messaging tool, one per
live session of the target. Without: the caller's own close, a pipeline of steps:

1. the done line to the app and the closing phase (`close.phase`), the session's open jobs closed;
2. its leases, shared uses and queue places given back;
3. the plugin steps, in order (stop the workspace's own servers, sync the repos, archive, ...): each gets a context and
   returns report lines;
4. the open asks listed (they stay for the person);
5. the window: with a window controller (the `relay` built-in), the session's tab closes after `delay` seconds and, when
   no other live session of the workspace stays, its window too; another live session keeps the window, and only this
   session's process stops.

Another live session of the workspace that is not closing too keeps the workspace: the plugin steps see it in
`ctx['siblings']` and skip what would pull the ground from under it (a sync, stopping its servers).
"""
from __future__ import annotations

import contextlib
import io

from . import jobs, leases
from .sessions import find_target, label, same
from .status import note
from .util import Refuse, first_line


def closing_sids(app) -> set:
    """The sessions that ran close (its first event goes out before the slow part, so two sessions of one workspace
    closing at once each see the other)."""
    return {e.get('sid') for e in app.events.all() if e.get('kind') == 'close' and e.get('action') in ('start', 'done')}


def request(app, target, sessions, mine):
    hits = find_target(sessions, target)
    if not hits:
        raise Refuse(f'no live session in "{target}": nothing to close. Live: {", ".join(label(s) for s in sessions)}')
    sender = (mine or {}).get('workspace') or '?'
    for s in hits:
        app.emit('close', mine, f'asked to close {label(s)}', action='request', to=label(s), to_workspace=s['workspace'])
        print(f'SendMessage to="{s.get("name")}" ({label(s)}):\n'
              + app.text('close.request', sender=sender, workspace=s['workspace']) + '\n')
    return hits


def release_own(app, mine, sessions, dry=False) -> list[str]:
    out = []
    for res, spec in app.resources().items():
        current = leases.lease(app, res, sessions)
        if current and same(current, mine):
            out.append(res)
            if not dry:
                with contextlib.redirect_stdout(io.StringIO()):
                    leases.release(app, res)
        if spec.get('shared') and any(same(u, mine) for u in leases.users(app, res, sessions)):
            out.append(f'{res} (shared)')
            if not dry:
                with contextlib.redirect_stdout(io.StringIO()):
                    leases.release(app, res, shared=True)
        if any(same(w, mine) for w in leases.queue(app, res)):
            out.append(f'queue of {res}')
            if not dry:
                with app.store.leases_lock():
                    app.store.put_queue(res, [w for w in leases.queue(app, res) if not same(w, mine)])
    return out


def close(app, target=None, done=None, dry=False, no_sync=False, keep_window=False, delay=None):
    sessions = app.live_sessions()
    mine = app.me(sessions)
    if target and not (mine and target.lower() in (mine['workspace'], (mine.get('name') or '').lower())):
        return request(app, target, sessions, mine)
    if not mine:
        raise Refuse('no calling session found: close runs from inside the session that closes')
    if done is None and not dry:
        raise Refuse('tell what this session did: --done-file <file> with one line (a file holding "-" if nothing)')
    delay = int(delay if delay is not None else app.cfg['close'].get('delay') or 30)
    ws = mine['workspace']
    who = {'workspace': ws, 'name': mine.get('name'), 'sid': mine['sid']}
    done = None if (done or '').strip() in ('', '-') else done.strip()
    lines = []
    if not dry:
        app.emit('close', who, 'closing', action='start')
    leaving = closing_sids(app) | {mine['sid']}
    siblings = [s for s in sessions if s['workspace'] == ws and s['sid'] not in leaving]
    names = ', '.join(label(s) for s in siblings)
    closed = jobs.close_all(app, who, dry)
    if not dry:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                note(app, phase=app.cfg['close'].get('phase') or 'free', waiting='', done=done, sid=mine['sid'])
            except Refuse:
                pass
    lines.append(f"status: {'done ' + repr(first_line(done, 120)) if done else 'no done line'}, "
                 f"phase {app.cfg['close'].get('phase') or 'free'}")
    lines.append(f"jobs {'to close' if dry else 'closed'}: {', '.join(closed) if closed else 'none open'}")
    released = release_own(app, who, sessions, dry)
    lines.append(f"leases: {', '.join(released) if released else 'none'}")
    ctx = {'mine': mine, 'who': who, 'sessions': sessions, 'siblings': siblings, 'dry': dry, 'no_sync': no_sync,
           'delay': delay, 'workspace': ws, 'root': app.workspaces.root(ws), 'done': done, 'synced': [],
           'report': {}}
    for _order, name, fn in app.registry.close_steps:
        try:
            lines += list(fn(app, ctx) or [])
        except Exception as err:  # noqa: BLE001
            lines.append(f'{name}: failed ({err})')
    asks = app.store.get_status(mine['sid']).get('asks') or []
    if asks:
        lines.append('open asks (they stay for the person): '
                     + '; '.join(f"#{a.get('n')} {a.get('type')}: {first_line(a.get('text'), 80)}" for a in asks))
    window = app.registry.window
    closes = False
    if keep_window:
        lines.append('window: stays open (--keep-window)')
    elif window is None:
        lines.append('window: no window controller configured; close the tab by hand')
    else:
        try:
            out, closes = window.plan_close(app, ctx)
            lines += out
        except Exception as err:  # noqa: BLE001
            lines.append(f'window: the controller failed ({err}); close the tab by hand')
    if not dry:
        app.emit('close', who, f"closed: {done or 'no done line'}", action='done', leases=released or None,
                 siblings=[label(s) for s in siblings] or None, report=ctx['report'] or None)
    print(f"{'close (dry run, nothing changed)' if dry else 'closed'}: {label(mine)}"
          + (f' ({names} stays in the workspace)' if siblings else ''))
    for line in lines:
        print(f'  {line}')
    if not dry and closes:
        print(f'now: send the reply with this report to whoever asked, before {delay} s')
    return lines
