"""The status card of a session: the item and step it is on, the signal a person-facing app builds its work view from.

`note` writes it. A note is a line and a notice on the app, so by default it goes out only on a transition: a new
phase, a new task, a new target, --waiting, --pr, --done, or the person's --requested. Each phase is checked against
the workflow guide; a passage outside it warns and marks the card, never blocks.

Card fields (docs/PROTOCOL.md): workspace, name, item, phase, waiting, text, done, done_at, updated, task, tasks,
saved_tasks, target, targets, saved_targets, guide, requested, off_guide, pr, pr_repo, pr_url, priority_delivered,
asks, ask_seq, jobs.
"""
from __future__ import annotations

import json
import re
import sys

from .sessions import label, same, waiting_for_person, who
from .util import Refuse, first_line, now


def _norm_item(app, item):
    if item is None:
        return None
    if item == '-':
        return ''
    aliases = {k.lower(): v for k, v in (app.cfg['status'].get('item_aliases') or {}).items()}
    return aliases.get(item.strip().lower(), item)


def refusal(app, wf, card, item, phase, waiting, text, done, pr, task, requested, target):
    """Why a note would not go out, or None."""
    if requested is not None:
        return None
    new_item = item is not None and (item or None) != card.get('item')
    of_item = (item or None) if item is not None else card.get('item')
    if of_item in wf.untracked:
        return None
    rr = app.cfg['status'].get('rereview') or {}
    if rr.get('phase') and phase == rr['phase'] and of_item:
        h = wf.history(of_item)
        if rr['phase'] in h:
            after = h[len(h) - h[::-1].index(rr['phase']):]
            needs = list(rr.get('needs') or [])
            if not any(p in needs for p in after):
                return app.text('note.rereview', phase=phase, needs=', '.join(needs))
    if waiting is not None or pr is not None or (done or '').strip():
        return None
    if not app.cfg['status'].get('only_on_transition', True):
        return None
    new_task = task is not None and (None if task.strip() == '-' else task.strip()) != card.get('task')
    new_target = target is not None and (None if target.strip() == '-' else target.strip()) != card.get('target')
    current = wf.new_name(card.get('phase'))
    if (not new_item and not new_task and not new_target and current and current not in wf.neutral
            and phase in (None, current) and (phase is not None or text is not None)):
        return app.text('note.only_text', phase=current)
    return None


def note(app, item=None, phase=None, waiting=None, text=None, done=None, pr=None, task=None, requested=None,
         target=None, sid=None):
    """Write the calling session's card. See the module doc and docs/PROTOCOL.md."""
    from .workflow import Workflow
    st = app.cfg['status']
    sessions = app.live_sessions()
    mine = app.me(sessions, sid)
    if not mine:
        raise app.refuse('cli.no_session')
    wf = Workflow(app)
    with app.store.status_lock():
        card = app.store.get_status(mine['sid'])
        card.update({'workspace': mine['workspace'], 'name': mine.get('name')})
        item = _norm_item(app, item)
        if item and item in (st.get('items_need_text') or []) and not (text or '').strip() and card.get('item') != item:
            raise app.refuse('note.item_needs_text', item=item)
        if task is not None and task.strip() != '-' and not re.fullmatch(st.get('task_pattern') or r'.+', task.strip()):
            raise app.refuse('note.bad_task')
        if requested is not None and not (phase and requested.strip()):
            raise app.refuse('note.requested_needs_phase')
        if target is not None and target.strip() != '-' and not re.fullmatch(st.get('target_pattern') or r'.+',
                                                                             target.strip()):
            raise app.refuse('note.bad_target')
        if phase in wf.renamed:
            print(app.text('note.renamed', old=phase, new=wf.renamed[phase], version=wf.renamed_in.get(phase, '?')))
            phase = wf.renamed[phase]
        g = wf.read()
        vocab = wf.vocabulary(g)
        lane = wf.lane_phases(g)
        if phase and phase not in vocab:
            print(app.text('note.unknown_phase', phases=', '.join(vocab)))
        why = refusal(app, wf, card, item, phase, waiting, text, done, pr, task, requested, target)
        change = {'item': item, 'phase': phase, 'waiting': waiting, 'text': text, 'done': done, 'pr': pr, 'task': task,
                  'requested': requested, 'target': target}
        for rule in app.registry.note_rules:
            why = why or rule(app, card, change)
        if why:
            raise Refuse(f"{why} {app.text('note.nothing_changed')}")
        at = now().isoformat()
        if item is not None and (item or None) != card.get('item'):
            saved = card.get('saved_tasks') or {}
            if card.get('item') and card.get('tasks'):
                saved[card['item']] = card['tasks']
            card['tasks'] = saved.pop(item, {}) if item else {}
            card['saved_tasks'] = dict(list(saved.items())[-5:])
            card.update(task=None, off_guide=None, requested=None)
            if card.get('targets') or card.get('saved_targets') or card.get('target'):
                saved_t = card.get('saved_targets') or {}
                if card.get('item') and card.get('targets'):
                    saved_t[card['item']] = card['targets']
                card['targets'] = saved_t.pop(item, {}) if item else {}
                card['saved_targets'] = dict(list(saved_t.items())[-5:])
                card['target'] = None
        if target is not None:
            card['target'] = None if target.strip() == '-' else target.strip()
        if phase and phase not in lane and phase not in wf.keep_target and phase != wf.task_ok and card.get('target'):
            if target is not None and target.strip() != '-':
                print(app.text('note.target_only_in_lane', phases=', '.join(sorted(lane)), phase=phase))
            card['target'] = None  # back on the main flow
        if phase in lane and not card.get('target'):
            print(app.text('note.lane_without_target'))
        if task is not None:
            card['task'] = None if task.strip() == '-' else task.strip()
        if phase == wf.task_ok and not card.get('task'):
            raise app.refuse('note.ok_needs_task', ok=wf.task_ok)
        of_item = item if item is not None else card.get('item')
        passage = None
        if phase and phase != wf.task_ok:
            track = (card.get('target') or '') if phase in lane else None
            passage = (wf.check(g, wf.history(of_item, track, lane), phase)
                       if of_item and of_item not in wf.untracked else None)
            if g:
                card['guide'] = g['version']
            if requested:
                card.update(requested={'text': requested.strip(), 'phase': phase, 'ts': at}, off_guide=None)
            elif phase != card.get('phase'):
                card['requested'] = None
                card['off_guide'] = ({'from': passage[0], 'to': phase, 'task': card.get('task'), 'ts': at}
                                     if passage else None)
        for key, value in (('item', item), ('phase', None if phase == wf.task_ok else phase), ('waiting', waiting),
                           ('text', text)):
            if value is not None:
                card[key] = value or None
        if phase and phase not in wf.neutral and phase not in lane and card.get('task'):
            card.setdefault('tasks', {})[card['task']] = {'phase': phase, 'ts': at}
        delivered = tuple(app.cfg['priority'].get('delivered_phases') or ())
        if phase and phase not in delivered + wf.neutral + (wf.task_ok,) and phase not in lane:
            card.pop('priority_delivered', None)  # new work: the next delivery checks the order again
        if pr is not None or item is not None:
            n = None if (pr or '-').strip() == '-' else pr.strip()
            if n and not re.fullmatch(st.get('pr_pattern') or r'\d{1,9}', n):
                raise app.refuse('note.bad_pr')
            if not n:
                for key in ('pr', 'pr_repo', 'pr_url'):
                    card.pop(key, None)
            elif str(card.get('pr')) != n or not card.get('pr_repo'):
                info = pr_info(app, n)
                card.update(pr=int(n) if n.isdigit() else n, pr_repo=info.get('repo'), pr_url=info.get('url'))
        on_target = card.get('target') if card.get('target') and (card.get('phase') in lane
                                                                   or card.get('phase') in wf.keep_target) else None
        if on_target:
            tr = card.setdefault('targets', {}).setdefault(on_target, {})
            if phase and phase != wf.task_ok:
                tr.update(phase=phase, ts=at)
            if pr is not None and pr.strip() != '-':
                tr.update(pr=card.get('pr'), pr_url=card.get('pr_url'))
        pr_fields = {k: card.get(k) for k in ('pr', 'pr_repo', 'pr_url')}
        guide_fields = {'task': card.get('task'), 'task_phase': phase if phase == wf.task_ok else None,
                        'guide': card.get('guide') if phase else None,
                        'requested': requested.strip() if requested else None,
                        'off_guide': True if passage and not requested else None}
        done = (done or '').strip()
        if done:
            card.update(done=first_line(done, 240), done_at=at)
        card['updated'] = at
        app.store.put_status(mine['sid'], card)
    if not done or any(v is not None for v in (item, phase, waiting, text, pr, task, target)):
        parts = (card.get('item'), card.get('pr') and app.text('status.part_pr', pr=card['pr']),
                 card.get('task') and app.text('status.part_task', task=card['task']),
                 on_target and app.text('status.part_target', target=on_target), card.get('phase'),
                 phase == wf.task_ok and wf.task_ok,
                 card.get('waiting') and app.text('status.part_waiting', waiting=card['waiting']), card.get('text'))
        app.emit('status', mine, ' · '.join(str(x) for x in parts if x), item=card.get('item'), phase=card.get('phase'),
                 waiting=card.get('waiting'), target=on_target, **pr_fields, **guide_fields)
    if done:
        app.emit('done', mine, card['done'], item=card.get('item'), phase=card.get('phase'), task=card.get('task'),
                 target=on_target, **pr_fields)
    if passage and not requested:
        print(app.text('note.off_guide', version=g['version'], source=passage[0], phase=phase, reason=passage[1]))
    for check in app.registry.text_checks:
        warning = check(text, done)
        if warning:
            print(warning, file=sys.stderr)
    if st.get('priority_hint', True) and waiting_for_person(card, app.cfg['person'].get('waiting_words') or []):
        from . import leases, priority
        for res in app.resources():
            cur = leases.lease(app, res, sessions)
            # the holder is compared by session, not by the host process pid a live session also carries
            higher = (priority.ahead_in_queue(app, res, cur, sessions)
                      if cur and cur.get('alive') and same(cur, who(mine)) else [])
            if higher:
                print(app.text('note.priority_hint', resource=res, peers=', '.join(label(w) for w in higher),
                               minutes=app.cfg['priority']['wait_minutes']))
    shown = app.registry.status_render(app, card) if app.registry.status_render else json.dumps(card, ensure_ascii=False)
    print(app.text('note.printed', label=label(mine), status=shown))
    return card


def pr_info(app, n) -> dict:
    for fn in app.registry.pr_info:
        try:
            info = fn(n)
        except Exception:  # noqa: BLE001
            info = None
        if info:
            return info
    return {}


def guide_mark(app, card, long=False):
    """How the card stands against the guide: off it, or off it at the person's request."""
    off, req = card.get('off_guide'), card.get('requested')
    if req:
        return app.text('status.requested_long', text=req.get('text')) if long else app.text('status.requested')
    if off:
        return (app.text('status.off_guide_long', source=off.get('from'), phase=off.get('to')) if long
                else app.text('status.off_guide'))
    return None
