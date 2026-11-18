"""Offers: work an app or the person puts up for a session to take, delivered the way a `send` delivers.

`send` pushes a message to a workspace and the delivery picks the session. An offer waits instead, for one workspace or
for any, until a session takes it: `offer` posts it, `offers` lists what the calling session may take, `claim <id>`
(or `claim --next`, the oldest one) takes it. Claiming turns the offer into the message and thread a `send` to the
claiming session would have made, and prints it, so the session reads it at once, answers in the thread and is
followed like any message of the person.

Once: the offer moves to `offers/claimed/` under the offers lock (`.lock`), which decides between two sessions racing
for it (or one claiming and one withdrawing); the second is refused. The message and the thread take the offer's id,
so a claim repeated by the same session after a crash delivers the same message, never a second one. When nothing was
delivered (the send failed before the box got the message), the offer goes back to open by one move.

An offer may expire (`offer --minutes N`: past it, it is withdrawn), carry the item the claiming session's card moves
to (`offer --item`), and comes back to open when its claimer ended without answering in the thread and the message
is no longer waiting in the box (another session takes it then). `offers --history` lists the claimed and withdrawn.

Offer file (`offers/<id>.json`): id, workspace (null: any workspace), title, text, from, at, and optionally item,
expires_at, reopened (how many times it came back), reopened_at. Claimed (`offers/claimed/<id>.json`): also
claimed_at, sid, name, claimed_workspace, message (the id of the message and the thread; the offer's own id the first
time), delivered (true once the message is in the box). Withdrawn (`offers/withdrawn/<id>.json`): also withdrawn_at,
and expired (true) when it ran out of time.
"""
from __future__ import annotations

import datetime as dt
import json
import sys

from . import threads
from .util import ID_RE, Refuse, aware, clean_text, first_line, local_hhmm, new_id, now, when_day

OPEN, CLAIMED, WITHDRAWN = 'open', 'claimed', 'withdrawn'
#: the fields a claim adds, dropped when the offer goes back to open
CLAIM_FIELDS = ('claimed_at', 'sid', 'name', 'claimed_workspace', 'message', 'delivered')


def check_id(app, oid):
    if not ID_RE.fullmatch(oid or ''):
        raise app.refuse('offer.bad_id', id=oid)


def check_workspace(app, workspace):
    """None (any workspace) or a workspace `send` would take, normalized the way the workspaces are."""
    if workspace in (None, ''):
        return None
    workspace = app.workspaces.norm(workspace)
    if not app.workspaces.valid(workspace):
        raise app.refuse('offer.bad_workspace', workspace=workspace)
    return workspace


def for_whom(app, o) -> str:
    return o.get('workspace') or app.text('offer.anyone')


def _out(data, echo):
    if echo:
        print(json.dumps(data))
    return data


def offer(app, text, workspace=None, title=None, source=None, minutes=None, item=None, echo=True):
    """Post a piece of work for a workspace (or any, with none) to claim. Prints `{ok, id}`."""
    workspace = check_workspace(app, workspace)
    text = clean_text(text)
    if not text:
        raise app.refuse('offer.empty')
    cap = app.cfg['caps']['text']
    if len(text) > cap:
        raise app.refuse('offer.too_long', size=len(text), cap=cap)
    try:
        minutes = float(minutes) if minutes not in (None, '') else None
    except ValueError:
        raise app.refuse('offer.bad_minutes', minutes=minutes) from None
    if minutes is not None and minutes <= 0:
        raise app.refuse('offer.bad_minutes', minutes=minutes)
    if workspace and not app.workspaces.known(workspace):
        print(app.text('offer.unknown_workspace', workspace=workspace, known=', '.join(app.workspaces.all())),
              file=sys.stderr)
    title = first_line(clean_text(title) if title else text, 120)
    o = {'id': new_id(), 'workspace': workspace, 'title': title, 'text': text, 'from': source or None,
         'at': now().isoformat()}
    if item:
        o['item'] = str(item).strip()
    if minutes:
        o['expires_at'] = (now() + dt.timedelta(minutes=minutes)).isoformat()
    app.store.offer_put(o)
    app.emit('offer', None, app.text('offer.ev_open', to=for_whom(app, o), title=title), action='open', id=o['id'],
             to_workspace=workspace, any_workspace=workspace is None or None, by=source or None, item=o.get('item'),
             expires_at=o.get('expires_at'))
    app.log_line(app.text('offer.log_open', time=f'{now():%H:%M:%S}', who=_who(app, source), to=for_whom(app, o),
                          title=title))
    if (app.cfg.get('offers') or {}).get('notify'):
        _notify(app, o)
    _out({'ok': True, 'id': o['id']}, echo)
    return o


def _notify(app, o):
    """`[offers] notify`: a notice in the box of the offer's workspace (or of every workspace), so an idle session
    that listens wakes to it. Never raises."""
    try:
        targets = [o['workspace']] if o.get('workspace') else app.workspaces.all()
        text = app.text('offer.notice', id=o['id'], to=for_whom(app, o), title=o.get('title'),
                        by=app.text('offer.list_by', who=o['from']) if o.get('from') else '')
        for ws in targets:
            app.delivery.send({'id': new_id(), 'workspace': ws, 'sid': None, 'notice': 'offer',
                               'from': o.get('from') or _who(app), 'text': text, 'at': now().isoformat()})
    except Exception:  # noqa: BLE001
        pass


def _who(app, source=None) -> str:
    return source or app.cfg['person'].get('source') or threads.PERSON


def withdraw(app, oid, echo=True):
    """Take an open offer back: it moves to `withdrawn/` and no session can claim it."""
    check_id(app, oid)
    o = app.store.offer_move(oid, OPEN, WITHDRAWN, {'withdrawn_at': now().isoformat()})
    if not o:
        raise _not_open(app, oid)
    app.emit('offer', None, app.text('offer.ev_withdrawn', title=o.get('title')), action='withdraw', id=oid,
             to_workspace=o.get('workspace'))
    _out({'ok': True, 'id': oid, 'withdrawn_at': o['withdrawn_at']}, echo)
    return o


# ---------------------------------------------------------------- upkeep: expired and ended

def expire(app) -> list[dict]:
    """Withdraw the open offers whose time ran out. Never raises."""
    out = []
    try:
        moment = now().astimezone()
        for o in app.store.offer_list(OPEN):
            until = aware(o.get('expires_at') or '')
            if not until or until > moment:
                continue
            gone = app.store.offer_move(o['id'], OPEN, WITHDRAWN, {'withdrawn_at': now().isoformat(), 'expired': True})
            if gone:
                app.emit('offer', None, app.text('offer.ev_expired', title=gone.get('title')), action='expire',
                         id=gone['id'], to_workspace=gone.get('workspace'))
                out.append(gone)
    except Exception:  # noqa: BLE001
        pass
    return out


def _answered(app, c) -> bool:
    t = app.store.get_thread(c.get('message')) if c.get('message') else None
    return bool(t) and any(turn.get('from') == threads.SESSION for turn in t.get('turns') or [])


def _waiting(app, c) -> bool:
    """Whether the offer's message still waits in the box (the delivery hands it to another session of the
    workspace: the offer stays claimed)."""
    ws = c.get('claimed_workspace')
    return bool(ws) and any(m.get('id') == c.get('message') for _, m in app.store.box_list(ws))


def reopen_ended(app) -> list[dict]:
    """Put back to open the claimed offers whose claimer ended without answering in the thread, once their message
    is no longer waiting in the box. The old thread is closed. Never raises."""
    out = []
    try:
        for c in app.store.offer_list(CLAIMED):
            if not c.get('sid') or app.host.session_alive(c['sid']) or _answered(app, c) or _waiting(app, c):
                continue
            back = app.store.offer_move(c['id'], CLAIMED, OPEN, {'reopened': int(c.get('reopened') or 0) + 1,
                                                                 'reopened_at': now().isoformat()},
                                        drop=CLAIM_FIELDS)
            if not back:
                continue
            if c.get('message'):
                with app.store.threads_lock():
                    t = app.store.get_thread(c['message'])
                    if t and t.get('state') != 'closed':
                        t.update(state='closed', closed_at=now().isoformat(), closed_by='offer reopened')
                        app.store.put_thread(t)
            app.emit('offer', None, app.text('offer.ev_reopened', title=back.get('title'),
                                             workspace=c.get('claimed_workspace'), name=c.get('name') or ''),
                     action='reopen', id=back['id'], to_workspace=back.get('workspace'), from_sid=c['sid'])
            out.append(back)
    except Exception:  # noqa: BLE001
        pass
    return out


def upkeep(app):
    expire(app)
    reopen_ended(app)


# ---------------------------------------------------------------- listing

def mine_open(app, workspace) -> list[dict]:
    """The open offers a session of this workspace may claim: its own and the ones for any workspace."""
    return [o for o in app.store.offer_list(OPEN) if o.get('workspace') in (None, workspace)]


def still_open(app, workspace=None) -> list[dict]:
    """The open offers that have not run out of time (for a view: it moves nothing). With a workspace, the ones a
    session of it may claim. Never raises."""
    try:
        moment = now().astimezone()
        found = app.store.offer_list(OPEN) if workspace is None else mine_open(app, workspace)
        return [o for o in found if not (aware(o.get('expires_at') or '') or moment) < moment]
    except Exception:  # noqa: BLE001
        return []


def line(app, o) -> str:
    day = when_day(o.get('at'))
    return app.text('offer.list_line', id=o['id'], to=for_whom(app, o), time=local_hhmm(o.get('at')),
                    day='' if day == 'today' else f'{day} ',
                    by=app.text('offer.list_by', who=o['from']) if o.get('from') else '',
                    until=app.text('offer.list_until', time=local_hhmm(o['expires_at'])) if o.get('expires_at') else '',
                    item=app.text('offer.list_item', item=o['item']) if o.get('item') else '',
                    title=o.get('title'))


def listing(app, everything=False, as_json=False, sid=None):
    """The open offers the calling session may claim (`everything`, or outside a session: all of them)."""
    upkeep(app)
    header = None
    if everything:
        offers = app.store.offer_list(OPEN)
    else:
        try:
            ws = app.who_of(sid)['workspace']
        except Refuse:
            ws, header = None, app.text('offer.list_all_header')
        offers = app.store.offer_list(OPEN) if ws is None else mine_open(app, ws)
    if as_json:
        print(json.dumps(offers, ensure_ascii=False))
        return offers
    if header:
        print(header)
    for o in offers:
        print(line(app, o))
    if not offers:
        print(app.text('offer.none_open'))
    return offers


def history(app, as_json=False, limit=20):
    """The claimed and withdrawn offers, the latest first."""
    done = [{**o, 'state': CLAIMED, 'when': o.get('claimed_at')} for o in app.store.offer_list(CLAIMED)]
    done += [{**o, 'state': WITHDRAWN, 'when': o.get('withdrawn_at')} for o in app.store.offer_list(WITHDRAWN)]
    done.sort(key=lambda o: o.get('when') or '', reverse=True)
    done = done[:max(1, int(limit))]
    if as_json:
        print(json.dumps(done, ensure_ascii=False))
        return done
    for o in done:
        print(history_line(app, o))
    if not done:
        print(app.text('offer.none_done'))
    return done


def history_line(app, o) -> str:
    """One claimed or withdrawn offer, as `offers --history` and `peek` print it."""
    when_iso = o.get('claimed_at') if o.get('sid') else o.get('withdrawn_at')
    day = when_day(when_iso)
    when = f"{'' if day == 'today' else day + ' '}{local_hhmm(when_iso)}"
    if o.get('sid'):
        return app.text('offer.history_claimed', id=o['id'], when=when, workspace=o.get('claimed_workspace'),
                        name=o.get('name') or '', title=o.get('title'),
                        answered='' if _answered(app, o) else app.text('offer.history_unanswered'))
    return app.text('offer.history_withdrawn', id=o['id'], when=when, title=o.get('title'),
                    expired=app.text('offer.history_expired') if o.get('expired') else '')


def claimed_by(app, sid, limit=5) -> list[dict]:
    """The offers a session claimed, the latest first (for a view: it moves nothing). Never raises."""
    try:
        mine = [o for o in app.store.offer_list(CLAIMED) if sid and o.get('sid') == sid]
        return sorted(mine, key=lambda o: o.get('claimed_at') or '', reverse=True)[:limit]
    except Exception:  # noqa: BLE001
        return []


def _not_open(app, oid) -> Refuse:
    """Why an offer that is not open cannot be taken: claimed (by whom, when), withdrawn, being claimed right now, or
    unknown."""
    c = app.store.offer_get(oid, CLAIMED)
    if c and c.get('sid'):
        day = when_day(c.get('claimed_at'))
        return app.refuse('offer.taken', id=oid, workspace=c.get('claimed_workspace'), name=c.get('name') or '',
                          time=f"{'' if day == 'today' else day + ' '}{local_hhmm(c.get('claimed_at'))}")
    w = app.store.offer_get(oid, WITHDRAWN)
    if w:
        return app.refuse('offer.expired' if w.get('expired') else 'offer.withdrawn', id=oid,
                          time=local_hhmm(w.get('withdrawn_at')))
    if c is not None or app.store.offer_get(oid, OPEN):
        return app.refuse('offer.racing', id=oid)
    return app.refuse('offer.unknown', id=oid)


# ---------------------------------------------------------------- claiming

def claim(app, oid, sid=None, as_json=False, echo=True):
    """The calling session takes an open offer: it becomes the message and thread a `send` to this session would have
    made, once, and the message is printed. The same session asking again gets the same answer. `--json`: prints
    `{ok, id, message, sid, title, text}`."""
    check_id(app, oid)
    mine = app.who_of(sid)
    expire(app)
    o = app.store.offer_get(oid, OPEN)
    if not o:
        c = app.store.offer_get(oid, CLAIMED)
        if c and c.get('sid') == mine['sid']:
            return _deliver(app, c, mine, as_json, echo)
        raise _not_open(app, oid)
    if o.get('workspace') not in (None, mine['workspace']):
        raise app.refuse('offer.not_yours', id=oid, workspace=o['workspace'], mine=mine['workspace'])
    got = app.store.offer_move(oid, OPEN, CLAIMED, {
        'claimed_at': now().isoformat(), 'sid': mine['sid'], 'name': mine.get('name'),
        'claimed_workspace': mine['workspace'], 'message': new_id() if o.get('reopened') else oid})
    if not got:
        raise _not_open(app, oid)  # another session took it, or withdrew it, first
    return _deliver(app, got, mine, as_json, echo)


def claim_next(app, sid=None, as_json=False, echo=True):
    """Claim the oldest open offer the calling session may take (its workspace's and any workspace's)."""
    mine = app.who_of(sid)
    upkeep(app)
    for o in mine_open(app, mine['workspace']):
        try:
            return claim(app, o['id'], sid=mine['sid'], as_json=as_json, echo=echo)
        except Refuse:
            continue  # taken by another session meanwhile: the next one
    raise app.refuse('offer.none_to_claim')


def _deliver(app, got, mine, as_json, echo):
    """Send the claimed offer as the message to the claiming session (unless that already happened), then take the
    message out of the box and print it."""
    oid, mid = got['id'], got.get('message') or got['id']
    if not got.get('delivered'):
        if not app.store.get_thread(mid):  # the thread is there when an earlier claim's send went through
            progress: dict = {}
            sent = False
            try:
                threads.send(app, mine['workspace'], got['text'], sid=mine['sid'], source=got.get('from'),
                             echo=False, mid=mid, progress=progress)
                sent = True
            finally:
                if not sent and not progress.get('delivered'):
                    # nothing reached the box: the offer goes back to open by one move, as it was
                    app.store.offer_move(oid, CLAIMED, OPEN, {}, drop=CLAIM_FIELDS)
        got = app.store.offer_update(oid, CLAIMED, {'message': mid, 'delivered': True}) \
            or {**got, 'message': mid, 'delivered': True}
        app.emit('claim', mine, app.text('offer.ev_claimed', title=got.get('title') or ''), id=oid, message=mid,
                 by=got.get('from'), offered_to=got.get('workspace'), any_workspace=got.get('workspace') is None or None,
                 item=got.get('item'))
        app.log_line(app.text('offer.log_claimed', time=f'{now():%H:%M:%S}', workspace=mine['workspace'],
                              name=mine.get('name') or '', who=_who(app, got.get('from')), title=got.get('title')))
        if got.get('item'):
            _move_card(app, mine, got['item'], echo)
    try:
        msgs = app.delivery.claim(mine['workspace'], mine['sid'], thread=mid)
        text = app.delivery.deliver(mine, msgs, 'claim') if msgs else app.text('offer.claimed_read', message=mid)
    except Exception:  # noqa: BLE001
        text = app.text('offer.claimed_arrives', message=mid)
    out = {'ok': True, 'id': oid, 'message': mid, 'sid': mine['sid'], 'title': got.get('title'),
           'text': got.get('text'), 'item': got.get('item')}
    if echo:
        print(json.dumps(out) if as_json else text)
    return out


def _move_card(app, mine, item, echo):
    """`offer --item`: the claiming session's card moves to the offer's item. A refusal of the card leaves the claim
    as it is."""
    import contextlib
    import io

    from . import status
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            status.note(app, item=item, sid=mine['sid'])
    except Refuse as e:
        if echo:
            print(app.text('offer.card_unchanged', item=item, why=str(e)), file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass
