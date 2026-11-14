"""Jobs: long work of a session that outlives the turn (an export, an import, a slow build or test run), on its card.

A person-facing app shows a job under its item, one badge per step, the workspace marked busy while it runs.

    job <name> --steps "a,b,c" [--item I] [--pid P] [--log F] [--from NAME]   open it, the first step running
    job <name> --step S [--pid P] [--log F]          close the running step as ok and start S
    job <name> --wait [person]                       close the running step as ok; wait for the session or the person
    job <name> --end ok|failed [note]                close the job and its running step
    --done "line", --duration S, --services "api:8001" go with the command that closes a step; --at ISO gives the real
    time of a step written after it ran; --toggle name=state records a switch flipped at that step.

Job: id, item, pid, st (running | ok | failed), start, end, note, waits (session | person), from, steps [{type, name,
start, end, st (todo | running | ok | failed | skipped), log, services}], toggles {name: [{st, at, step}]}.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys

from .sessions import label
from .util import first_line, hhmm, now, stamp

WAITS = ('session', 'person')
SERVICE = re.compile(r'[A-Za-z][\w.-]{0,30}(?::\d{2,5})?')
TOGGLE = re.compile(r'\s*([A-Za-z][\w-]{0,30})\s*=\s*([\w-]{1,20})\s*')  # spaces around either side are dropped


def services_of(app, text):
    items = [s.strip() for s in (text or '').split(',') if s.strip()]
    if not items or not all(SERVICE.fullmatch(s) for s in items):
        raise app.refuse('job.bad_services')
    return items


def last_closed(job):
    return max((e for e in job['steps'] if e['st'] in ('ok', 'failed') and e['end']), key=lambda e: e['end'],
               default=None)


def new_step(app, name, at=None):
    kind = step_type(app, name)
    return {'type': kind, 'name': name, 'start': at, 'end': None, 'st': 'running' if at else 'todo'}


def step_type(app, name):
    j = app.cfg['jobs']
    kind = {k.lower(): v for k, v in (j.get('synonyms') or {}).items()}.get(name.lower(), name.lower())
    return kind if kind in (j.get('types') or []) else 'other'


def st_word(app, st):
    """How a job or step state reads in a line: `job.st_<state>`, the state itself when there is no text for it."""
    return app.text(f'job.st_{st}') or st


def steps_text(app, job):
    steps = ' > '.join(f"{e['name']}{'' if e['st'] == 'todo' else ' ' + st_word(app, e['st'])}" for e in job['steps'])
    waits = app.text(f"job.steps_waits_{job['waits']}") if job.get('waits') in WAITS else ''
    return steps + waits


def _person_value(app):
    return (app.cfg['person'].get('waiting_words') or ['person'])[0]


def set_waiting(app, card, mine, waiting):
    """`--wait person` puts the card on waiting for the person, as `note --waiting` would, and the job going on clears
    it: the same `status` event, so an app reads it the usual way."""
    card['waiting'] = waiting
    card['updated'] = now().isoformat()
    parts = (card.get('item'), card.get('pr') and app.text('status.part_pr', pr=card['pr']),
             card.get('task') and app.text('status.part_task', task=card['task']), card.get('phase'),
             waiting and app.text('status.part_waiting', waiting=waiting), card.get('text'))
    app.emit('status', mine, ' · '.join(str(x) for x in parts if x), item=card.get('item'), phase=card.get('phase'),
             waiting=waiting, task=card.get('task'), pr=card.get('pr'), pr_repo=card.get('pr_repo'),
             pr_url=card.get('pr_url'))


def release_person(app, card, mine, job):
    if job.get('waits') == 'person' and card.get('waiting') == _person_value(app):
        set_waiting(app, card, mine, None)
    job['waits'] = None


def close_step(app, job, step, st, end, duration):
    """Close the running step at `end`. A step of 0 s is refused: it opens when its command starts, --at gives the
    real time, or --duration the real length, which moves its start back, never over the step before. A failure
    closes as it came: a command can die in its first second."""
    if duration is not None:
        start = (dt.datetime.fromisoformat(end) - dt.timedelta(seconds=duration)).isoformat()
        before = max((e['end'] for e in job['steps'] if e is not step and e['end'] and e['end'] <= step['start']),
                     default=None)
        if before and start < before:
            raise app.refuse('job.duration_overlap', duration=duration, step=step['name'], time=hhmm(start),
                             before=hhmm(before))
        if job['start'] == step['start'] or start < job['start']:
            job['start'] = min(job['start'], start)
        step['start'] = start
    elif st != 'failed' and step['start'] == end:
        raise app.refuse('job.zero_step', step=step['name'], time=hhmm(end))
    step.update(st=st, end=end)


def label_of(app, text):
    m = re.match(app.cfg['jobs'].get('label_pattern') or r'\s*([\w.-]+)\s+(?:ok|failed)\s*:', text, re.I)
    return m.group(1) if m else None


def key(name):
    return re.sub(r'[\s_-]', '', name.lower())


def last_time(app, job):
    seen = [(job['start'], app.text('job.last_job_opened')), (job['end'], app.text('job.last_job_closed'))]
    for e in job['steps']:
        seen += [(e['start'], app.text('job.last_step_opened', step=e['name'])),
                 (e['end'], app.text('job.last_step_closed', step=e['name']))]
    last = (None, None)
    for t, what in seen:  # a tie names the later one: the step that opened, not the one it closed
        if t and (last[0] is None or t >= last[0]):
            last = (t, what)
    return last


def previous(app, name, my_sid):
    """`--from <name>`: the latest job with that name on another session's card, as "<sid>|<name>"."""
    found = []
    for sid, card in app.store.list_status().items():
        if sid == my_sid:
            continue
        for j in card.get('jobs') or []:
            if j.get('id') == name:
                found.append((j.get('start') or '', f'{sid}|{name}'))
    return max(found)[1] if found else None


def job(app, name=None, steps=None, step=None, end=None, note=None, item=None, pid=None, at=None, wait=None, done=None,
        from_=None, duration=None, toggle=None, services=None, log=None, sid=None):
    sessions = app.live_sessions()
    mine = app.me(sessions, sid)
    if not mine:
        raise app.refuse('cli.no_session')
    cfg = app.cfg['jobs']
    now_iso = now().isoformat()
    today = now_iso[:10]
    when = None
    if at is not None:
        try:
            when = dt.datetime.fromisoformat(at.strip()).replace(microsecond=0, tzinfo=None)
        except ValueError:
            raise app.refuse('job.bad_at') from None
        if when.isoformat() > now_iso:
            raise app.refuse('job.at_future', at=when.isoformat())
    actions = sum(x is not None for x in (steps, step, end, wait))
    fields_only = actions == 0 and (toggle is not None or services is not None)
    if not name:
        if actions or any(x is not None for x in (note, item, pid, at, done, from_, toggle, services)):
            raise app.refuse('job.no_name')
    elif not re.fullmatch(r'[\w.-]{1,60}', name):
        raise app.refuse('job.bad_name')
    elif actions != 1 and not fields_only:
        raise app.refuse('job.one_action')
    if toggle is not None:
        m = TOGGLE.fullmatch(toggle)
        if not m:
            raise app.refuse('job.bad_toggle')
        toggle = (m.group(1).lower(), m.group(2).lower())
        allowed = (cfg.get('toggle_states') or {}).get(toggle[0])
        if allowed and toggle[1] not in allowed:
            raise app.refuse('job.bad_toggle')
    if services is not None:
        services = services_of(app, services)
        if steps is not None:
            raise app.refuse('job.services_on_open')
    if fields_only and any(x is not None for x in (note, item, pid, done, from_, duration)):
        raise app.refuse('job.fields_alone')
    if note and end is None:
        raise app.refuse('job.note_needs_end')
    if (item is not None or from_ is not None) and steps is None:
        raise app.refuse('job.item_on_open')
    if pid is not None and steps is None and step is None:
        raise app.refuse('job.pid_needs_step')
    if pid is not None and not re.fullmatch(r'\d{1,7}', str(pid).strip()):
        raise app.refuse('job.bad_pid')
    if log is not None:
        if steps is None and step is None:
            raise app.refuse('job.log_needs_step')
        log = os.path.abspath(log.strip()) if log.strip() else ''
        if not log or not re.search(r'\.(log|txt|out)$', log, re.I):
            raise app.refuse('job.bad_log')
    if wait is not None and wait not in WAITS:
        raise app.refuse('job.bad_wait')
    if done is not None and (steps is not None or not done.strip()):
        raise app.refuse('job.done_on_open')
    if end is not None and end not in ('ok', 'failed'):
        raise app.refuse('job.bad_end')
    if duration is not None:
        if steps is not None or not re.fullmatch(r'\d{1,5}', str(duration).strip()) or not 0 < int(duration) <= 86400:
            raise app.refuse('job.bad_duration')
        duration = int(duration)
    with app.store.status_lock():
        card = app.store.get_status(mine['sid'])
        jobs = [j for j in card.get('jobs') or [] if not j.get('end') or j['end'][:10] == today]
        if not name:
            for j in jobs:
                print(app.text('job.list_line', id=j['id'], item=j['item'] or '-', st=st_word(app, j['st']),
                               since=hhmm(j['start']), steps=steps_text(app, j)))
            if not jobs:
                print(app.text('job.none_today'))
            return jobs
        jb = next((j for j in jobs if j['id'] == name), None)
        if when is not None:
            last, what = last_time(app, jb) if jb else (None, None)
            if last and steps is None and when.isoformat() < last:
                raise app.refuse('job.at_before_last', at=when.isoformat(), what=what, time=last[11:19])
            now_iso = when.isoformat()
        closed = None
        of_services = None
        if fields_only:
            if not jb:
                raise app.refuse('job.no_job_fields', name=name)
            of_services = next((e for e in jb['steps'] if e['st'] == 'running'), None)
            if services is not None and not of_services:
                raise app.refuse('job.services_no_running', name=name)
            what = ' · '.join(x for x in (toggle and app.text('job.what_toggle', name=toggle[0], state=toggle[1]),
                                          services and app.text('job.what_services', services=', '.join(services)))
                              if x)
        elif steps is not None:
            names = [e.strip() for e in steps.split(',') if e.strip()]
            if not names:
                raise app.refuse('job.bad_steps')
            if jb and jb['st'] == 'running':
                raise app.refuse('job.already_running', name=name)
            if jb:
                raise app.refuse('job.already_today', name=name, st=st_word(app, jb['st']), time=hhmm(jb['end']))
            origin = None
            if from_ is not None:
                origin = previous(app, from_.strip(), mine['sid'])
                if not origin:
                    raise app.refuse('job.from_not_found', origin=from_)
            target = card.get('item') if item is None else (None if item.strip() in ('', '-') else item.strip())
            jb = {'id': name, 'item': int(target) if target and str(target).isdigit() else target,
                  'pid': int(pid) if pid is not None else None, 'st': 'running', 'start': now_iso, 'end': None,
                  'note': None, 'waits': None,
                  'steps': [new_step(app, n, now_iso if i == 0 else None) for i, n in enumerate(names)]}
            if origin:
                jb['from'] = origin
            if log:
                jb['steps'][0]['log'] = log
            jobs.append(jb)
            what = jb['steps'][0]['name']
        else:
            if not jb:
                raise app.refuse('job.no_job', name=name)
            if jb['st'] == 'ok' or (jb['st'] == 'failed' and step is None):
                raise app.refuse('job.already_closed', name=name, st=st_word(app, jb['st']), time=hhmm(jb['end']),
                                 tail=app.text('job.already_closed_failed') if jb['st'] == 'failed' else '')
            if jb['st'] == 'failed':  # run a failed step again in the same job: a new line at the end
                jb.update(st='running', end=None, note=None)
            running = next((e for e in jb['steps'] if e['st'] == 'running'), None)
            if duration is not None and not running:
                raise app.refuse('job.duration_no_running', name=name)
            if services is not None and not running and not last_closed(jb):
                raise app.refuse('job.services_none_closed', name=name)
            if done is not None:  # checked before any change: the line goes on the step this command closes
                closing = running or last_closed(jb)
                if not closing:
                    raise app.refuse('job.done_none_closed', name=name)
                named = label_of(app, done)
                foreign = cfg.get('foreign_labels')
                if named and key(named) != key(closing['name']) and (
                        (foreign and re.fullmatch(foreign, named, re.I))
                        or any(key(e['name']) == key(named) for e in jb['steps'])):
                    raise app.refuse('job.done_foreign', label=named, step=closing['name'])
            if step is not None:
                if not step.strip():
                    raise app.refuse('job.bad_step')
                target = step.strip()
                kind = step_type(app, target)
                if running:
                    close_step(app, jb, running, 'ok', now_iso, duration)
                    closed = running
                nxt = next((e for e in jb['steps'] if e is not running and e['st'] == 'todo'
                            and (e['name'].lower() == target.lower() or (kind != 'other' and e['type'] == kind))), None)
                if nxt:  # the todo steps before it won't run: skipped, so the first todo is the real next one
                    for e in jb['steps'][:jb['steps'].index(nxt)]:
                        if e['st'] == 'todo':
                            e['st'] = 'skipped'
                else:
                    nxt = new_step(app, target)
                    jb['steps'].append(nxt)
                nxt.update(st='running', start=now_iso, end=None)
                if log:
                    nxt['log'] = log
                else:
                    nxt.pop('log', None)
                jb['pid'] = int(pid) if pid is not None else None
                release_person(app, card, mine, jb)
                what = nxt['name']
            elif wait is not None:
                if running:
                    close_step(app, jb, running, 'ok', now_iso, duration)
                    closed = running
                elif jb.get('waits') == wait:
                    raise app.refuse('job.already_waits', name=name, who=app.text(f'job.who_{wait}'))
                jb['pid'] = None
                if wait == 'person':
                    jb['waits'] = 'person'
                    if card.get('waiting') != _person_value(app):
                        set_waiting(app, card, mine, _person_value(app))
                else:
                    release_person(app, card, mine, jb)
                    jb['waits'] = 'session'
                what = app.text('job.what_waits_person') if wait == 'person' else app.text('job.what_waits')
            else:
                if running:
                    close_step(app, jb, running, end, now_iso, duration)
                    closed = running
                release_person(app, card, mine, jb)
                jb.update(st=end, end=now_iso, note=(note or '').strip() or None, pid=None)
                what = app.text(f'job.end_{end}')
        if done is not None:
            closed = closed or last_closed(jb)
            card.update(done=first_line(done, 240), done_at=now_iso)
        if services is not None:
            of_services = of_services or closed or last_closed(jb)
            of_services['services'] = list(dict.fromkeys((of_services.get('services') or []) + services))
        if toggle is not None:
            toggles = jb.setdefault('toggles', {})
            history = toggles.get(toggle[0]) or []
            if not history or history[-1]['st'] != toggle[1]:
                of_step = None  # the step that had started last at this time
                for e in jb['steps']:
                    if e['start'] and (of_step is None or e['start'] >= of_step['start']):
                        of_step = e
                history.append({'st': toggle[1], 'at': now_iso, 'step': of_step and of_step['name']})
            toggles[toggle[0]] = history
        card['jobs'] = jobs
        app.store.put_status(mine['sid'], card)
    ts = stamp(when.astimezone()) if when else None
    app.emit('job', mine, f'{name}: {what}', ts=ts, item=jb['item'], job=name, st=jb['st'], waits=jb.get('waits'),
             step=next((e['name'] for e in jb['steps'] if e['st'] == 'running'), None),
             toggle={'name': toggle[0], 'st': toggle[1]} if toggle else None,
             services=services and {'step': of_services['name'], 'list': services})
    if done is not None:
        app.emit('done', mine, card['done'], ts=ts, item=str(jb['item']) if jb['item'] else None,
                 phase=card.get('phase'), task=card.get('task'), job=name, step=closed['name'], type=closed['type'])
        for check in app.registry.text_checks:
            warning = check(done)
            if warning:
                print(warning, file=sys.stderr)
    elif closed and closed['type'] in (cfg.get('done_expected') or []) and closed['st'] == 'ok':
        print(app.text('job.done_missing', step=closed['name']), file=sys.stderr)
    print(app.text('job.printed', name=name, label=label(mine), st=st_word(app, jb['st']),
                   steps=steps_text(app, jb)))
    return jb


def close_all(app, mine, dry=False) -> list[str]:
    """On close: the session's open jobs close with it. A running step, or a job waiting for the session, fails as
    "session closed"; a job waiting for the person is ok, "stopped waiting for the person"."""
    lines = []
    with app.store.status_lock():
        card = app.store.get_status(mine['sid'])
        at = now().isoformat()
        open_ = [j for j in card.get('jobs') or [] if j.get('st') == 'running']
        for jb in open_:
            st, note = (('ok', app.text('job.closed_waiting_person')) if jb.get('waits') == 'person'
                        else ('failed', app.text('job.closed_session')))
            lines.append(f"{jb['id']} {st} ({note})")
            if dry:
                continue
            for e in jb['steps']:
                if e['st'] == 'running':
                    e.update(st='failed', end=at)
            jb.update(st=st, end=at, note=note, pid=None, waits=None)
        if open_ and not dry:
            app.store.put_status(mine['sid'], card)
    if not dry:
        for jb in open_:
            app.emit('job', mine, f"{jb['id']}: {jb['st']}", item=jb['item'], job=jb['id'], st=jb['st'])
    return lines
