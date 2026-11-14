"""The lines written into the event log can be reworded through texts: a deployment that keeps another language's
event log (an existing app reads it) sets them in `[texts]`."""
import io
import contextlib

from helpers import Case

from ai_signalbox import jobs, leases, status, threads

WORDING = """
[texts]
'status.part_task' = 'ticket {task}'
'status.part_waiting' = 'blocked on {waiting}'
'job.end_failed' = 'broke'
'job.what_waits_person' = 'needs the person'
'lease.ev_took' = 'grabbed {res}{in_mode}{until}: {why}'
'lease.ev_in_mode' = ' as {mode}'
'lease.mode_exclusive' = 'sole owner'
'lease.ev_released' = 'let go of {res}{result}{next}'
'ask.ev_closed' = 'settled #{n}: {text}{note}'
[resources.db]
shared = true
"""


class WordingTest(Case):
    extra_toml = WORDING

    def texts(self, kind):
        return [e['text'] for e in self.app.store.scan_events() if e['kind'] == kind]

    def test_event_lines_follow_the_texts(self):
        s = self.session('ws1')
        self.act_as(s)
        self.tick(30)  # every step lasts a while: a 0 s step is refused
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status.note(self.app, item='7', phase='plan', task='101', text='Doing it')
            status.note(self.app, waiting='person')
            jobs.job(self.app, 'b1', steps='build')
            jobs.job(self.app, 'b1', wait='person')
            jobs.job(self.app, 'b1', step='build')
            jobs.job(self.app, 'b1', end='failed')
            leases.hold(self.app, 'db', 'migrate')
            leases.release(self.app, 'db', result='ok')
            threads.ask(self.app, text='Which name?')
            threads.ask(self.app, close='1', note='answered')
        self.assertEqual(self.texts('status')[:2], ['7 · ticket 101 · plan · Doing it',
                                                    '7 · ticket 101 · plan · blocked on person · Doing it'])
        self.assertIn('b1: needs the person', self.texts('job'))
        self.assertEqual(self.texts('job')[-1], 'b1: broke')
        self.assertEqual(self.texts('lease'), ['grabbed db as sole owner: migrate', 'let go of db (ok)'])
        self.assertEqual(self.texts('ask')[-1], 'settled #1: Which name? (answered)')

    def test_defaults_stay_english(self):
        app = self.make_app()
        self.assertEqual(app.text('status.part_waiting', waiting='person'), 'waiting person')
        self.assertEqual(app.text('lease.ev_released', res='db', result=' (ok)', next=''), 'released db (ok)')


MORE = r"""
[texts]
'inbox.note_line' = "- {date}, by {who}: {text}\n"
'inbox.ev_post' = '-> mailbox {workspace}: {text}'
'priority.ev' = '{order} (set by {by}{reason})'
'peek.asks' = ''
[priority]
person_by = 'panel'
[events]
prompt_kind = 'person'
"""


class MoreWordingTest(Case):
    """The inbox, priority and peek lines, the prompt kind and the priority signature follow the config too."""
    extra_toml = MORE

    def quiet(self, fn, *a, **k):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            fn(*a, **k)
        return out.getvalue()

    def test_inbox_priority_and_peek_follow_the_config(self):
        from ai_signalbox import inbox, priority, views
        s = self.session('ws1', name='alpha')
        self.act_as(s)
        self.quiet(inbox.post, self.app, 'WS2', 'pick it up')
        self.assertIn(', by ws1 alpha: pick it up', (self.app.cfg.root / 'inbox' / 'ws2.md').read_text(encoding='utf-8'))
        ev = self.events('post')[-1]
        self.assertEqual((ev['text'], ev['to_workspace']), ('-> mailbox WS2: pick it up', 'ws2'))
        self.quiet(priority.set_order, self.app, ['ws2', 'ws1', 'ws3'], by='panel')
        ev = self.events('priority')[-1]
        self.assertEqual((ev['text'], ev['by']), ('ws2 > ws1 > ws3 (set by panel)', 'panel'))
        self.assertEqual(priority.person_by(self.app), 'panel')
        self.quiet(threads.ask, self.app, text='Which name?')
        self.assertNotIn('asks', self.quiet(views.peek, self.app, 'ws1'))  # an empty template drops the line

    def test_prompt_kind_names_the_persons_prompts(self):
        import json
        import re
        s = self.session('ws1', name='alpha')
        cwd = str(self.tmp / 'work' / 'ws1' / 'app')
        folder = self.tmp / 'host' / 'projects' / re.sub(r'[:\\/]', '-', cwd)
        folder.mkdir(parents=True)
        line = {'type': 'user', 'timestamp': '2026-01-01T10:00:00Z', 'message': {'content': 'Fix the login bug'}}
        (folder / f"{s['sid']}.jsonl").write_text(json.dumps(line), encoding='utf-8')
        evs = self.app.host.transcript_events(self.app.live_sessions()[0])
        self.assertEqual([e[1] for e in evs], ['person'])

    def test_defaults(self):
        from ai_signalbox import priority
        app = self.make_app()
        self.assertEqual(app.cfg['events']['prompt_kind'], 'prompt')
        self.assertEqual(app.cfg['priority']['by_signalbox'], 'signalbox')
        self.assertEqual(priority.person_by(app), app.cfg['person'].get('source') or 'person')
        self.assertTrue(app.text('peek.asks', asks='#1'))
