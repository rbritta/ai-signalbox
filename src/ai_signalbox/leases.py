"""Leases on shared resources, with a queue.

Resources come from the config (and plugins): a name, a description, `shared` (two modes: many users at once, or one
exclusive holder with a queue) and `ports` (what the resource listens on). A holder is a session, or a script process
given with --pid, freed when that process dies; a process started by the holder works under its lease.

Files: leases/<res>.json (the holder), queues/<res>.json (who waits), shared/<res>.json (the users of the shared
mode), all under one lock.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import itertools
import os
import time

from . import priority
from .sessions import holder_fields, label, same
from .util import aware, hhmm, now

SHARED, EXCLUSIVE = 'shared', 'exclusive'


def spec(app, res) -> dict:
    resources = app.resources()
    if res not in resources:
        raise app.refuse('lease.unknown', res=res,
                         resources=', '.join(resources) or app.text('lease.none_configured'))
    return resources[res]


def mode_of(app, res, shared) -> str | None:
    s = spec(app, res)
    if shared and not s.get('shared'):
        modal = [r for r, v in app.resources().items() if v.get('shared')]
        raise app.refuse('lease.no_shared', res=res, modal=', '.join(modal) or app.text('lease.none_shared'))
    return SHARED if shared else (EXCLUSIVE if s.get('shared') else None)


def mode_label(mode) -> str:
    return mode or ''


def is_alive(app, entry, sessions) -> bool:
    """A script holder (pid) lives while its process does; a session holder while its session does."""
    if entry.get('pid'):
        return app.procs.alive(entry['pid'], entry.get('pid_started'))
    return any(s['sid'] == entry.get('sid') for s in sessions)


def inherits(app, current, mine) -> bool:
    """A script run by the script that holds the lease works under it."""
    return bool(current.get('pid') and mine.get('pid') and current['pid'] in app.procs.ancestors(mine['pid']))


def caller(app, sessions, pid=None, as_=None):
    """Who asks: the session up the process tree and, with --pid, the script process itself, whose death frees what
    it holds. None when neither is there."""
    mine = app.me(sessions)
    if mine:
        who = {'workspace': mine['workspace'], 'name': mine.get('name'), 'sid': mine['sid']}
    elif pid:
        who = {'workspace': app.workspaces.of(os.getcwd()), 'name': None, 'sid': None}
    else:
        return None
    if pid:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            raise app.refuse('lease.bad_pid', pid=pid) from None
        if not app.procs.alive(pid):
            raise app.refuse('lease.no_pid', pid=pid)
        who.update(pid=pid, pid_started=app.procs.create_time(pid))
        who['as'] = as_
    return who


@contextlib.contextmanager
def _locked(app):
    """The leases lock, required: a command that can't have it refuses, because going on without it could leave two
    exclusive holders. (A hook goes on without a lock; taking a lease is never a hook's job.)"""
    with app.store.leases_lock() as held:
        if held is False:
            raise app.refuse('lease.lock_timeout')
        yield


def overdue(data) -> bool:
    """A lease taken with `--minutes` whose time is up: the holder still has it, and the board says it is late."""
    until = aware((data or {}).get('until') or '')
    return bool(until) and until < now().astimezone()


def lease(app, res, sessions):
    data = app.store.get_lease(res)
    if data:
        data['alive'] = is_alive(app, data, sessions)
        data['overdue'] = overdue(data)
    return data


def queue(app, res) -> list:
    return app.store.get_queue(res)


def users(app, res, sessions) -> list:
    return [u for u in app.store.get_shared(res) if is_alive(app, u, sessions)]


def mode_word(app, mode) -> str:
    """How a mode reads in a line: `lease.mode_shared`, `lease.mode_exclusive` (no mode reads as exclusive)."""
    return app.text('lease.mode_shared') if mode == SHARED else app.text('lease.mode_exclusive')


def holder_text(current) -> str:
    if not current:
        return 'free'
    mode = f" ({current['mode']})" if current.get('mode') else ''
    dead = '' if current.get('alive') else ' (ended: stale)'
    late = f" (overdue since {hhmm(current['until'])})" if current.get('overdue') else ''
    return (f"{label(current)}{mode} since {hhmm(current.get('since'))}{dead}{late}: "
            f"{current.get('why') or 'no reason'}")


def _checks(app, res, mine, current):
    for resources, fn in app.registry.lease_checks:
        if resources is None or res in resources:
            why = fn(app, res, mine, current)
            if why:
                raise app.refuse('lease.check', reason=why)


def _blocker(app, res):
    for resources, fn in app.registry.lease_blockers:
        if resources is None or res in resources:
            b = fn(app, res)
            if b:
                return b
    return None


def _poll(app) -> float:
    return float(os.environ.get('SIGNALBOX_LEASE_POLL') or app.cfg.get('leases.poll_seconds', 5))


def hold(app, res, why=None, minutes=None, shared=False, pid=None, as_=None, wait=None, item=None):
    mode = mode_of(app, res, shared)
    sessions = app.live_sessions()
    mine = caller(app, sessions, pid, as_)
    if not mine:
        raise app.refuse('lease.no_session')
    card = app.store.get_status(mine['sid']) if mine.get('sid') else {}
    mine['item'] = item or (card or {}).get('item') or (app.item_of(mine['workspace']) if mine.get('workspace') else None)
    if mode == SHARED:
        return hold_shared(app, res, why, mine, sessions)
    deadline = time.time() + float(wait) if wait else None
    queued = False
    while True:
        with _locked(app):
            if queued:
                sessions = app.live_sessions()
            current = lease(app, res, sessions)
            if current and current.get('alive') and inherits(app, current, mine):
                print(app.text('lease.inherited', res=res, holder=label(current), pid=current['pid']))
                return current
            busy = bool(current and current.get('alive') and not same(current, mine))
            blocker = _blocker(app, res)
            q = [w for w in queue(app, res) if is_alive(app, w, sessions)]
            # only the ones waiting with --wait keep their turn; a `want` is a note the holder answers by message
            ahead = [w for w in itertools.takewhile(lambda w: not same(w, mine), q) if w.get('waits')] if deadline else []
            holder = (label(current) if busy else blocker if blocker
                      else app.text('lease.the_queue', waiting=', '.join(label(w) for w in ahead)))
            if not busy and not blocker and not ahead:
                _checks(app, res, mine, current)
                data = {'res': res, 'mode': mode, **holder_fields(mine), 'why': why, 'since': now().isoformat(),
                        'until': (now() + dt.timedelta(minutes=minutes)).isoformat() if minutes else None}
                stale = bool(current and not current.get('alive') and not same(current, mine))
                app.store.put_lease(res, data)
                q = [w for w in queue(app, res) if not same(w, mine)]
                app.store.put_queue(res, q)
                break
            if not deadline:
                if busy:
                    imp = priority.impasse(app, mine.get('workspace'), current.get('workspace'))
                    raise app.refuse('lease.busy', res=res, holder=holder, since=hhmm(current['since']),
                                     why=current.get('why') or app.text('lease.no_reason'),
                                     tail=f'\n{imp}' if imp else '')
                raise app.refuse('lease.locked', res=res, holder=holder)
            if not queued:
                q = queue(app, res)
                if not any(same(w, mine) for w in q):
                    q.append({**holder_fields(mine), 'why': why, 'mode': mode, 'waits': True,
                              'since': now().isoformat()})
                    app.store.put_queue(res, q)
                queued = True
                app.emit('queue', mine, app.text('lease.ev_waiting', res=res, mode=mode_word(app, mode),
                                                 holder=holder, why=why or app.text('lease.no_reason')),
                         res=res, action='want', mode=mode, why=why)
                print(app.text('lease.queued', res=res, holder=holder, wait=wait), flush=True)
        if time.time() > deadline:
            with app.store.leases_lock():
                app.store.put_queue(res, [w for w in queue(app, res) if not same(w, mine)])
            raise app.refuse('lease.gave_up', res=res, wait=wait, holder=holder)
        time.sleep(_poll(app))
    until = app.text('lease.ev_until', time=hhmm(data['until'])) if data['until'] else ''
    if stale:
        print(app.text('lease.stale_replaced', holder=label(current)))
    in_mode = app.text('lease.in_mode', mode=mode_word(app, mode)) if mode else ''
    ev_text = app.text('lease.ev_took', res=res,
                       in_mode=app.text('lease.ev_in_mode', mode=mode_word(app, mode)) if mode else '',
                       until=app.text('lease.ev_until', time=hhmm(data['until'])) if data['until'] else '',
                       why=why or app.text('lease.no_reason'))
    app.emit('lease', mine, ev_text, res=res, action='hold', mode=mode,
             why=why, item=data.get('item'), until=data['until'], queue=[label(w) for w in q] or None)
    print(app.text('lease.yours', res=res, in_mode=in_mode, holder=label(mine), until=until,
                   queue=', '.join(label(w) for w in q) or app.text('lease.nobody')))
    if spec(app, res).get('shared') and spec(app, res).get('notify_users', True):
        others = [u for u in users(app, res, sessions) if not same(u, mine)]
        if others:
            print(app.text('lease.users_notice', users=', '.join(
                f"{label(u)} ({u.get('why') or app.text('lease.use_word')})" for u in others)))
    for fn in app.registry.on_take:
        fn(app, res, mine, sessions, (deadline - time.time()) if deadline else 0)
    return data


def hold_shared(app, res, why, mine, sessions):
    """Many at once. Refused while someone else holds it exclusively: the reads would see it half done."""
    with _locked(app):
        current = lease(app, res, sessions)
        if current and current.get('alive') and not same(current, mine):
            raise app.refuse('lease.held_exclusive', res=res, holder=label(current), since=hhmm(current['since']),
                             why=current.get('why') or app.text('lease.no_reason'))
        others = [u for u in users(app, res, sessions) if not same(u, mine)]
        entry = {**holder_fields(mine), 'mode': SHARED, 'why': why, 'since': now().isoformat()}
        app.store.put_shared(res, others + [entry])
    app.emit('lease', mine, app.text('lease.ev_uses', res=res, why=why or app.text('lease.no_reason')), res=res,
             action='hold', mode=SHARED, why=why)
    print(app.text('lease.in_use', res=res, holder=label(mine),
                   others=', '.join(label(u) for u in others) or app.text('lease.nobody')))
    return entry


def want(app, res, why=None, shared=False):
    mode = mode_of(app, res, shared)
    sessions = app.live_sessions()
    mine = caller(app, sessions)
    if not mine:
        raise app.refuse('cli.no_session')
    current = lease(app, res, sessions)
    if current and same(current, mine):
        raise app.refuse('lease.already_yours', res=res)
    with _locked(app):
        q = queue(app, res)
        if not any(same(w, mine) for w in q):
            q.append({**holder_fields(mine), 'why': why, 'mode': mode, 'since': now().isoformat()})
            app.store.put_queue(res, q)
    pos = next(i for i, w in enumerate(q) if same(w, mine)) + 1
    holder = label(current) if current else app.text('lease.free_holder')
    app.emit('queue', mine, app.text('lease.ev_queue', res=res, pos=pos, holder=holder,
                                     mode=app.text('lease.ev_queue_mode', mode=mode_word(app, mode)) if mode else '',
                                     why=why or app.text('lease.no_reason')),
             res=res, action='want', mode=mode, why=why, position=pos)
    print(app.text('lease.queue_position', res=res, pos=pos, holder=holder))
    imp = priority.impasse(app, mine.get('workspace'), current.get('workspace')) if current and current.get('alive') \
        else None
    if imp:
        print(imp)
    return pos


def release(app, res, shared=False, pid=None, result=None, item=None):
    mode = mode_of(app, res, shared)
    sessions = app.live_sessions()
    mine = caller(app, sessions, pid)
    if mode == SHARED:
        with app.store.leases_lock():
            alive = users(app, res, sessions)
            keep = [u for u in alive if not (mine and same(u, mine))]
            app.store.put_shared(res, keep)
        if len(keep) < len(alive):
            app.emit('lease', mine, app.text('lease.ev_stopped', res=res), res=res, action='release', mode=SHARED)
            print(app.text('lease.use_ended', res=res, others=', '.join(label(u) for u in keep) or app.text('lease.nobody')))
        else:
            print(app.text('lease.not_a_user', res=res))
        return None
    with app.store.leases_lock():
        current = lease(app, res, sessions)
        if current and mine and current.get('alive') and inherits(app, current, mine):
            print(app.text('lease.stays_with', res=res, holder=label(current)))
            return None
        if not current:
            print(app.text('lease.already_free', res=res))
        elif mine and not same(current, mine) and current.get('alive'):
            raise app.refuse('lease.not_yours', res=res, holder=label(current))
        else:
            app.store.put_lease(res, None)
            print(app.text('lease.released', res=res))
        q = [w for w in queue(app, res) if is_alive(app, w, sessions) and not (mine and same(w, mine))]
        app.store.put_queue(res, q)
    if current:
        app.emit('lease', mine, app.text('lease.ev_released', res=res,
                                         result=app.text('lease.ev_result', result=result) if result else '',
                                         next=app.text('lease.ev_next', next=label(q[0])) if q else ''),
                 res=res, action='release', mode=current.get('mode'),
                 next=label(q[0]) if q else None, result=result, item=item)
        for fn in app.registry.on_release:
            fn(app, res, current, mine, result, item)
    if q:
        nxt = q[0]
        print(app.text('lease.next', holder=label(nxt), why=nxt.get('why') or app.text('lease.no_reason')) + (
            app.text('lease.next_waits') if nxt.get('waits') else
            '. ' + app.text('lease.tell_next', name=nxt.get('name') or '', sender=(mine or {}).get('workspace') or '?',
                            workspace=nxt.get('workspace') or '', resource=res)))
    return current
