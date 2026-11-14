"""The delivery priority of the workspaces: an order that only settles an impasse.

It does not rule everything: only an impasse (a resource, the order of a merge or a validation, a conflicting change)
that the person leaves unanswered for `wait_minutes`. Then the lower number goes on and the other yields and says so.

A workspace that delivered (its card in a `delivered_phases` phase) and is clean (every `workspace_clean` plugin
check passes) puts the free workspaces back on their home number, unless a busy workspace holds that place. It runs at
the end of each turn (Stop, SessionEnd), never mid-turn.

File: priority.json {order, at, by}.
"""
from __future__ import annotations

import json
import re
import sys

from .sessions import label
from .util import Refuse, new_id, now

BY_SIGNALBOX = 'signalbox'


def read(app) -> dict:
    p = app.store.get_priority() or {}
    return {'order': list(p.get('order') or []), 'at': p.get('at'), 'by': p.get('by')}


def position(app, ws, p=None):
    order = (p or read(app))['order']
    return order.index(ws) + 1 if ws in order else None


def position_text(app, ws, p=None):
    p = p or read(app)
    n = position(app, ws, p)
    return app.text('priority.position', n=n, total=len(p['order'])) if n else None


def impasse(app, mine, other, p=None):
    """The line a resource command adds when another workspace is in the way: who goes on if the person does not
    answer."""
    p = p or read(app)
    a, b = position(app, mine, p), position(app, other, p)
    if not a or not b or a == b:
        return None
    goes, yields = (mine, other) if a < b else (other, mine)
    return app.text('priority.impasse', mine=mine, a=a, other=other, b=b, goes=goes, yields=yields,
                    minutes=app.cfg['priority']['wait_minutes'])


def ahead_in_queue(app, res, current, sessions, p=None):
    """Who waits in the queue of a held lease with a higher priority than the holder's workspace."""
    from . import leases
    p = p or read(app)
    owner = position(app, (current or {}).get('workspace'), p)
    if not owner:
        return []
    return [w for w in leases.queue(app, res) if leases.is_alive(app, w, sessions)
            and (position(app, w.get('workspace'), p) or owner) < owner]


def valid(app) -> list[str]:
    excluded = set(app.cfg['priority'].get('exclude') or [])
    return [w for w in app.workspaces.all() if w not in excluded]


def home(app, ws) -> int:
    """The home number of a workspace: `[priority.home]` in the config, else the number its name ends with, else its
    place among the workspaces."""
    homes = app.cfg['priority'].get('home') or {}
    if ws in homes:
        return int(homes[ws])
    m = re.search(r'(\d+)$', ws or '')
    if m:
        return int(m.group(1))
    names = app.workspaces.all()
    return names.index(ws) + 1 if ws in names else 10 ** 6


def notify(app, new, before, changed, sessions, reason=None):
    """One notice per live session of each workspace whose place changed, in its box: the hooks wake the session and
    deliver it. A notice opens no thread."""
    told = []
    n = len(new['order'])
    for s in sessions:
        ws = s.get('workspace')
        if ws not in changed:
            continue
        now_pos, was = position(app, ws, new), position(app, ws, before)
        pos = app.text('priority.of', n=now_pos, total=n) if now_pos else app.text('priority.out')
        text = app.text('priority.notice', by=new['by'], workspace=ws, reason=f'{reason}; ' if reason else '',
                        order=' > '.join(new['order']), position=pos,
                        was=app.text('priority.was', n=was) if was else '', minutes=app.cfg['priority']['wait_minutes'])
        app.delivery.send({'id': new_id(), 'workspace': ws, 'sid': s['sid'], 'notice': 'priority', 'from': new['by'],
                           'text': text, 'at': now().isoformat()})
        told.append(label(s))
    return told


def write(app, order, by, mine, sessions, reason=None):
    """Write the order, the notices, the event and the log line: (new, before, changed)."""
    before = read(app)
    new = {'order': order, 'at': now().isoformat(), 'by': by}
    app.store.put_priority(new)
    changed = [w for w in valid(app) if position(app, w, before) != position(app, w, new)]
    told = notify(app, new, before, changed, sessions, reason)
    chain = ' > '.join(order)
    app.emit('priority', mine, app.text('priority.ev', order=chain, by=by,
                                        reason=app.text('priority.ev_reason', reason=reason) if reason else ''),
             order=order, by=by, before=before['order'] or None, changed=changed or None, told=told or None,
             reason=reason)
    app.log_line(app.text('priority.log_line', time=f'{now():%H:%M:%S}', by=by, order=chain,
                          reason=app.text('priority.log_reason', reason=reason) if reason else '',
                          told=app.text('priority.log_told', told=', '.join(told)) if told else ''))
    return new, before, changed


def person_by(app) -> str:
    """How an order set by the person's app is signed."""
    return app.cfg['priority'].get('person_by') or app.cfg['person'].get('source') or 'person'


def set_order(app, order=None, by=None, quiet=False):
    """No order: the current one as JSON. With workspaces: write it. Without --by, the caller's workspace; the
    person's app signs as `person_by`. What the person set changes only by agreement of the workspaces involved: the
    CLI can't check that, so it reminds. `quiet`: return the JSON instead of printing it."""
    if not order:
        out = read(app)
        if not quiet:
            print(json.dumps(out, ensure_ascii=False))
        return out
    ok = valid(app)
    order = [w.lower() for w in order]
    outside = [w for w in order if w not in ok]
    if outside:
        raise Refuse(app.text('priority.outside', outside=', '.join(outside), valid=', '.join(ok)))
    repeated = sorted({w for w in order if order.count(w) > 1})
    if repeated:
        raise Refuse(app.text('priority.repeated', repeated=', '.join(repeated)))
    sessions = app.live_sessions()
    mine = app.me(sessions)
    person = person_by(app)
    by = (by or (mine or {}).get('workspace') or '').lower()
    if by != person and by not in ok:
        raise Refuse(app.text('priority.bad_by', person=person, valid=', '.join(ok)))
    new, before, changed = write(app, order, by, mine, sessions)
    if before['by'] == person and by != person and changed:
        print(app.text('priority.person_set', person=person, changed=', '.join(changed)), file=sys.stderr)
    out = {'ok': True, **new}
    if not quiet:
        print(json.dumps(out, ensure_ascii=False))
    return out


def clean(app, ws):
    for fn in app.registry.workspace_clean:
        try:
            ok, why = fn(app, ws)
        except Exception as err:  # noqa: BLE001
            ok, why = False, str(err)
        if not ok:
            return False, why
    return True, None


def with_item(app, ws, sessions, but=None) -> bool:
    delivered = tuple(app.cfg['priority'].get('delivered_phases') or ())
    return any(s.get('workspace') == ws and s.get('sid') != but and (s.get('card') or {}).get('item')
               and (s.get('card') or {}).get('phase') not in delivered for s in sessions)


def on_home_number(app, order, busy):
    """The order with the free workspaces back on their home number, unless a busy one holds that place. Busy ones
    keep their places; each free one takes its own place when empty, and the ones whose place is held take, by
    number, the next empty one after it (else the first empty)."""
    places = [w if w in busy else None for w in order]
    free = sorted((w for w in order if w not in busy), key=lambda w: home(app, w))
    left = []
    for ws in free:
        want = home(app, ws) - 1
        if 0 <= want < len(places) and places[want] is None:
            places[want] = ws
        else:
            left.append(ws)
    for ws in left:
        empty = [i for i, x in enumerate(places) if x is None]
        places[next((i for i in empty if i >= home(app, ws) - 1), empty[0])] = ws
    return places


def delivered(app, who, sessions=None):
    """A delivered and clean workspace puts the free workspaces back on their home number (`by: signalbox`, no
    agreement needed). Moved, or already in place, the card gets `priority_delivered`, which only a note in a work
    phase clears: one check per delivery. The new order, or None."""
    sid, ws = who.get('sid'), who.get('workspace')
    if not (sid and ws):
        return None
    card = app.store.get_status(sid)
    phases = tuple(app.cfg['priority'].get('delivered_phases') or ())
    if card.get('phase') not in phases or card.get('priority_delivered'):
        return None
    p = read(app)
    if ws not in p['order']:
        return None
    sessions = sessions if sessions is not None else app.live_sessions()
    if with_item(app, ws, sessions, but=sid):
        return None
    if not clean(app, ws)[0]:
        return None
    busy = {w for w in p['order'] if w != ws and (with_item(app, w, sessions) or not clean(app, w)[0])}
    order = on_home_number(app, p['order'], busy)
    new = None
    if order != p['order']:
        mine = app.me(sessions, sid) or who
        place = order.index(ws) + 1
        reason = app.text('priority.delivered_reason', workspace=ws) + (
            '' if place == home(app, ws) else app.text('priority.delivered_held', workspace=ws, place=place,
                                                       home=home(app, ws)))
        new = write(app, order, app.cfg['priority'].get('by_signalbox') or BY_SIGNALBOX, mine, sessions, reason)[0]
    with app.store.status_lock():
        card = app.store.get_status(sid)
        card['priority_delivered'] = now().isoformat()
        app.store.put_status(sid, card)
    return new
