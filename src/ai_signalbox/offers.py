"""Offers: work an app or the person puts up for a session to take, delivered the way a `send` delivers.

`send` pushes a message to a workspace and the delivery picks the session. An offer waits instead, for one workspace or
for any, until a session takes it: `offer` posts it, `offers` lists what the calling session may take, `claim <id>`
takes it. Claiming turns the offer into the message and thread a `send` to the claiming session would have made, so
the session reads it, answers in the thread and is followed like any message of the person. It happens once: the
offer file moves to `offers/claimed/` by one atomic rename before anything is delivered, and of two sessions racing for
it one gets it and the other is refused. `offer --withdraw <id>` takes an open offer back.

Offer file (`offers/<id>.json`): id, workspace (null: any workspace), title, text, from, at. Claimed
(`offers/claimed/<id>.json`): also claimed_at, sid, name, claimed_workspace, message (the id of the message and the
thread it became). Withdrawn (`offers/withdrawn/<id>.json`): also withdrawn_at.
"""
from __future__ import annotations

import json
import re

from . import threads
from .util import ID_RE, Refuse, clean_text, first_line, hhmm, local_hhmm, new_id, now

OPEN, CLAIMED, WITHDRAWN = 'open', 'claimed', 'withdrawn'


def check_id(app, oid):
    if not ID_RE.fullmatch(oid or ''):
        raise app.refuse('offer.bad_id', id=oid)


def check_workspace(app, workspace):
    """None (any workspace) or a workspace `send` would take."""
    if workspace in (None, ''):
        return None
    workspace = workspace.lower()
    if workspace not in app.workspaces.all() and not re.fullmatch(r'[\w.-]{1,64}', workspace):
        raise app.refuse('offer.bad_workspace', workspace=workspace)
    return workspace


def for_whom(app, o) -> str:
    return o.get('workspace') or app.text('offer.anyone')


def offer(app, text, workspace=None, title=None, source=None):
    """Post a piece of work for a workspace (or any, with none) to claim. Prints `{ok, id}`."""
    workspace = check_workspace(app, workspace)
    text = clean_text(text)
    if not text:
        raise app.refuse('offer.empty')
    cap = app.cfg['caps']['text']
    if len(text) > cap:
        raise app.refuse('offer.too_long', size=len(text), cap=cap)
    title = first_line(clean_text(title) if title else text, 120)
    o = {'id': new_id(), 'workspace': workspace, 'title': title, 'text': text, 'from': source or None,
         'at': now().isoformat()}
    app.store.offer_put(o)
    app.emit('offer', None, app.text('offer.ev_open', to=for_whom(app, o), title=title), action='open', id=o['id'],
             to_workspace=workspace, by=source or None)
    app.log_line(app.text('offer.log_open', time=f'{now():%H:%M:%S}', who=source or app.cfg['person'].get('source')
                          or threads.PERSON, to=for_whom(app, o), title=title))
    out = {'ok': True, 'id': o['id']}
    print(json.dumps(out))
    return o


def withdraw(app, oid):
    """Take an open offer back: it moves to `withdrawn/` and no session can claim it."""
    check_id(app, oid)
    o = app.store.offer_move(oid, OPEN, WITHDRAWN, {'withdrawn_at': now().isoformat()})
    if not o:
        raise _not_open(app, oid)
    app.emit('offer', None, app.text('offer.ev_withdrawn', title=o.get('title')), action='withdraw', id=oid,
             to_workspace=o.get('workspace'))
    out = {'ok': True, 'id': oid, 'withdrawn_at': o['withdrawn_at']}
    print(json.dumps(out))
    return o


def mine_open(app, workspace) -> list[dict]:
    """The open offers a session of this workspace may claim: its own and the ones for any workspace."""
    return [o for o in app.store.offer_list(OPEN) if o.get('workspace') in (None, workspace)]


def listing(app, everything=False, as_json=False, sid=None):
    """The open offers the calling session may claim (`everything`: all of them, no session needed)."""
    offers = app.store.offer_list(OPEN) if everything else mine_open(app, app.who_of(sid)['workspace'])
    if as_json:
        print(json.dumps(offers, ensure_ascii=False))
        return offers
    for o in offers:
        print(app.text('offer.list_line', id=o['id'], to=for_whom(app, o), time=local_hhmm(o.get('at')),
                       by=app.text('offer.list_by', who=o['from']) if o.get('from') else '', title=o.get('title')))
    if not offers:
        print(app.text('offer.none_open'))
    return offers


def _not_open(app, oid) -> Refuse:
    """Why an offer that is not open cannot be taken: claimed (by whom, when), withdrawn, or unknown."""
    c = app.store.offer_get(oid, CLAIMED)
    if c:
        return app.refuse('offer.taken', id=oid, workspace=c.get('claimed_workspace'), name=c.get('name') or '',
                          time=hhmm(c.get('claimed_at')))
    w = app.store.offer_get(oid, WITHDRAWN)
    if w:
        return app.refuse('offer.withdrawn', id=oid, time=hhmm(w.get('withdrawn_at')))
    return app.refuse('offer.unknown', id=oid)


def claim(app, oid, sid=None):
    """The calling session takes an open offer: it becomes the message and thread a `send` to this session would have
    made, once. Prints `{ok, id, message, sid}`."""
    check_id(app, oid)
    mine = app.who_of(sid)
    o = app.store.offer_get(oid, OPEN)
    if not o:
        raise _not_open(app, oid)
    if o.get('workspace') not in (None, mine['workspace']):
        raise app.refuse('offer.not_yours', id=oid, workspace=o['workspace'], mine=mine['workspace'])
    got = app.store.offer_move(oid, OPEN, CLAIMED, {'claimed_at': now().isoformat(), 'sid': mine['sid'],
                                                    'name': mine.get('name'), 'claimed_workspace': mine['workspace']})
    if not got:
        raise _not_open(app, oid)  # another session won the rename
    try:
        sent = threads.send(app, mine['workspace'], got['text'], sid=mine['sid'], source=got.get('from'), echo=False)
    except Exception:
        # nothing was delivered: the offer goes back to open, as it was
        back = app.store.offer_move(oid, CLAIMED, OPEN, {})
        if back:
            app.store.offer_put({k: back[k] for k in ('id', 'workspace', 'title', 'text', 'from', 'at') if k in back})
        raise
    got['message'] = sent['id']
    app.store.offer_move(oid, CLAIMED, CLAIMED, {'message': sent['id']})  # same folder: only adds the field
    app.emit('claim', mine, got.get('title') or '', id=oid, message=sent['id'], by=got.get('from'),
             offered_to=got.get('workspace'))
    out = {'ok': True, 'id': oid, 'message': sent['id'], 'sid': mine['sid']}
    print(json.dumps(out))
    return got
