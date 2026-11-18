"""Leases with a queue, the delivery priority, and jobs."""
import datetime as dt
import json
import os
import shutil
import subprocess
import threading
import time
import unittest

from helpers import Case

from ai_signalbox import priority
from ai_signalbox.util import now

RESOURCES = '''
[resources.db]
description = "the shared database"
shared = true
[resources.app]
description = "the dev servers"
ports = [8000, 8001]
stop_on_close = true
[resources.cache]
description = "the build cache"
'''


class LeaseTest(Case):
    extra_toml = RESOURCES

    def setUp(self):
        super().setUp()
        os.environ['SIGNALBOX_LEASE_POLL'] = '0.05'
        self.addCleanup(os.environ.pop, 'SIGNALBOX_LEASE_POLL', None)

    def hold(self, *argv):
        return self.cli('hold', *argv)

    def lease(self, res):
        return self.app.store.get_lease(res)

    def test_shared_exclusive_queue_and_holders(self):
        a, b, c, d = (self.script() for _ in range(4))
        self.assertEqual(self.hold('db', 'plan', '--shared', '--pid', str(a), '--as', 'ws1 plan')[0], 0)
        code, out, _ = self.hold('db', 'fixture', '--shared', '--pid', str(b), '--as', 'ws3 fixture')
        self.assertEqual(code, 0)
        self.assertIn('ws1 plan', out)
        self.assertEqual(len(self.app.store.get_shared('db')), 2)
        code, out, _ = self.hold('db', 'migrate', '--pid', str(c), '--as', 'ws2 up')
        self.assertEqual(code, 0)
        self.assertEqual(self.lease('db')['mode'], 'exclusive')
        self.assertIn('send a notice', out)
        self.assertIn('ws1 plan', out)
        self.assertIn('ws3 fixture', out)
        code, _, err = self.hold('db', 'read', '--shared', '--pid', str(d), '--as', 'ws4 read')
        self.assertEqual(code, 1)
        self.assertIn('held exclusively', err)
        code, _, err = self.hold('db', 'import', '--pid', str(d), '--as', 'ws4 import')
        self.assertEqual(code, 1)
        self.assertIn('busy', err)
        self.assertEqual(self.cli('release', 'db', '--pid', str(d))[0], 1)
        self.assertEqual(self.lease('db')['as'], 'ws2 up')
        # --wait: in the queue, and it takes it by itself when released
        out = []
        th = threading.Thread(target=lambda: out.append(self.hold('db', 'import', '--pid', str(d), '--as', 'ws4 import',
                                                                  '--wait', '10')))
        th.start()
        deadline = time.time() + 5
        while time.time() < deadline and not self.app.store.get_queue('db'):
            time.sleep(0.02)
        self.assertEqual([w['as'] for w in self.app.store.get_queue('db')], ['ws4 import'])
        self.assertEqual(self.cli('release', 'db', '--pid', str(c))[0], 0)
        th.join(10)
        self.assertEqual(out[0][0], 0)
        self.assertIn('in the queue', out[0][1])
        self.assertEqual(self.lease('db')['as'], 'ws4 import')
        # a script that died holds nothing
        self.procs.table.pop(d)
        code, out, _ = self.hold('db', 'drop', '--pid', str(c), '--as', 'ws2 drop')
        self.assertEqual(code, 0)
        self.assertIn('replaced', out)

    def test_child_works_under_the_holder(self):
        parent = self.script()
        child = self.script(ppid=parent)
        self.assertEqual(self.hold('cache', 'refresh', '--pid', str(parent), '--as', 'refresh')[0], 0)
        code, out, _ = self.hold('cache', 'refresh', '--pid', str(child), '--as', 'inner')
        self.assertEqual(code, 0)
        self.assertIn('works under it', out)
        self.assertEqual(self.lease('cache')['as'], 'refresh')
        code, out, _ = self.cli('release', 'cache', '--pid', str(child), '--result', 'ok')
        self.assertIn('stays with', out)
        self.assertEqual(self.lease('cache')['as'], 'refresh')
        seen = []
        self.app.registry.on_release.append(lambda app, res, cur, mine, result, item: seen.append((res, result, item)))
        self.assertEqual(self.cli('release', 'cache', '--pid', str(parent), '--result', 'failed', '--item', '10007')[0], 0)
        self.assertEqual(seen, [('cache', 'failed', '10007')])
        self.assertIsNone(self.lease('cache'))
        ev = self.events('lease')[-1]
        self.assertEqual((ev['action'], ev['result'], ev['item']), ('release', 'failed', '10007'))

    def test_refusals_checks_and_hooks(self):
        p = self.script()
        code, _, err = self.hold('app', 'up', '--shared', '--pid', str(p))
        self.assertEqual(code, 1)
        self.assertIn('has no shared mode', err)
        self.assertEqual(self.hold('nope', '--pid', str(p))[0], 1)
        self.assertEqual(self.hold('cache')[0], 1)  # no session and no --pid
        self.app.registry.lease_checks.append((None, lambda app, res, mine, cur: 'not before the merge'))
        code, _, err = self.hold('cache', '--pid', str(p))
        self.assertEqual((code, err.strip()), (1, 'not before the merge'))
        self.app.registry.lease_checks.clear()
        self.app.registry.lease_blockers.append(({'cache'}, lambda app, res: 'an old lock (pid 1)'))
        code, _, err = self.hold('cache', '--pid', str(p))
        self.assertIn('locked by an old lock (pid 1)', err)
        self.app.registry.lease_blockers.clear()
        taken = []
        self.app.registry.on_take.append(lambda app, res, mine, sessions, left: taken.append((res, mine['pid'])))
        self.assertEqual(self.hold('cache', '--pid', str(p))[0], 0)
        self.assertEqual(taken, [('cache', p)])

    def test_sessions_want_and_impasse(self):
        s1 = self.session('ws1', name='alpha')
        s2 = self.session('ws2', name='beta')
        self.app.store.put_priority({'order': ['ws2', 'ws1'], 'at': now().isoformat(), 'by': 'person'})
        self.act_as(s1)
        self.assertEqual(self.hold('cache', 'build')[0], 0)
        self.assertEqual(self.lease('cache')['sid'], s1['sid'])
        self.act_as(s2)
        code, _, err = self.hold('cache', 'build too')
        self.assertEqual(code, 1)
        self.assertIn('ws2 goes on and ws1 yields', err)
        code, out, _ = self.cli('want', 'cache', 'my turn')
        self.assertIn('position 1', out)
        self.assertIn('ws2 goes on', out)
        self.act_as(s1)
        self.app.store.put_status(s1['sid'], {'phase': 'review'})
        code, out, _ = self.cli('note', '--waiting', 'person')
        self.assertIn('higher priority in the queue', out)
        code, out, _ = self.cli('release', 'cache')
        self.assertIn('next: ws2 beta (my turn)', out)
        self.assertIn('SendMessage to="beta"', out)


class PriorityTest(Case):
    def setUp(self):
        super().setUp()
        (self.tmp / 'work' / 'ws4' / 'app').mkdir(parents=True)
        self.s1 = self.session('ws1', name='alpha')
        self.s2 = self.session('ws2', name='beta')
        self.s3 = self.session('ws3', name='gamma')

    def notices(self, ws):
        return [m for _, m in self.app.store.box_list(ws) if m.get('notice')]

    def test_set_refuse_notify_impasse_queue(self):
        self.assertEqual(json.loads(self.cli('priority')[1]), {'order': [], 'at': None, 'by': None})
        for order, by, why in ((['ws2', 'ws2'], 'person', 'repeated'), (['ws9'], 'person', 'outside the priority'),
                               (['ws1'], 'boss', '--by'), (['ws1'], None, 'none was found')):
            code, _, err = self.cli('priority', *order, *(['--by', by] if by else []))
            self.assertEqual(code, 1, (order, err))
            self.assertIn(why, err)
        self.assertEqual(self.app.store.get_priority(), {})
        code, out, _ = self.cli('priority', 'WS2', 'ws3', 'ws1', 'ws4', '--by', 'person')
        r = json.loads(out)
        self.assertEqual((code, r['ok'], r['order'], r['by']), (0, True, ['ws2', 'ws3', 'ws1', 'ws4'], 'person'))
        self.assertEqual(self.app.store.get_priority(), {'order': r['order'], 'at': r['at'], 'by': 'person'})
        e = self.events('priority')[-1]
        self.assertEqual((e['order'], e['by'], e['changed'], e['told']),
                         (r['order'], 'person', ['ws1', 'ws2', 'ws3', 'ws4'], ['ws1 alpha', 'ws2 beta', 'ws3 gamma']))
        n1 = self.notices('ws1')
        self.assertEqual((len(n1), n1[0]['sid'], n1[0]['from']), (1, self.s1['sid'], 'person'))
        self.assertTrue(n1[0]['text'].startswith('[person->ws1] NOTICE:'))
        self.assertIn('is 3 of 4', n1[0]['text'])
        self.assertFalse(self.notices('ws4'))
        text = self.app.delivery.deliver(self.s1, self.app.delivery.claim('ws1', self.s1['sid']), 'signal')
        self.assertIn(n1[0]['text'], text)
        self.assertNotIn('person speaking', text)
        self.assertNotIn('thread', text)
        self.assertIsNone(self.app.store.get_thread(n1[0]['id']))
        for ws, s in (('ws2', self.s2), ('ws3', self.s3)):
            self.app.delivery.claim(ws, s['sid'])
        self.assertEqual((priority.position_text(self.app, 'ws2'), priority.position_text(self.app, 'ws1'),
                          priority.position_text(self.app, 'ws9')), ('priority 1 of 4', 'priority 3 of 4', None))
        self.assertIn('ws2 goes on and ws1 yields', priority.impasse(self.app, 'ws1', 'ws2'))
        self.assertIn('ws2 goes on and ws1 yields', priority.impasse(self.app, 'ws2', 'ws1'))
        self.assertEqual((priority.impasse(self.app, 'ws1', 'ws1'), priority.impasse(self.app, 'ws1', 'ws9')),
                         (None, None))
        sessions = self.app.live_sessions()
        self.app.store.put_queue('cache', [{'workspace': 'ws2', 'name': 'beta', 'sid': self.s2['sid']},
                                           {'workspace': 'ws4', 'name': 'x', 'sid': 'gone'}])
        self.assertEqual([w['workspace'] for w in priority.ahead_in_queue(self.app, 'cache', {'workspace': 'ws1'},
                                                                           sessions)], ['ws2'])
        self.assertEqual(priority.ahead_in_queue(self.app, 'cache', {'workspace': 'ws2'}, sessions), [])
        # workspaces reorder among themselves: only the ones that moved get the notice
        self.act_as(self.s2)
        code, out, err = self.cli('priority', 'ws3', 'ws2', 'ws1', 'ws4')
        self.assertEqual((code, json.loads(out)['by']), (0, 'ws2'))
        self.assertIn('by agreement', err)
        self.assertEqual([len(self.notices(w)) for w in ('ws1', 'ws2', 'ws3')], [0, 1, 1])
        self.assertTrue(self.notices('ws3')[0]['text'].startswith('[ws2->ws3] NOTICE:'))
        self.assertIn('(was 2)', self.notices('ws3')[0]['text'])
        self.assertEqual(self.events('priority')[-1]['before'], ['ws2', 'ws3', 'ws1', 'ws4'])

    def test_home_number_rule(self):
        f = lambda order, busy: priority.on_home_number(self.app, order, set(busy))  # noqa: E731
        self.assertEqual(f(['ws3', 'ws1', 'ws2', 'ws4'], {'ws3'}), ['ws3', 'ws2', 'ws1', 'ws4'])
        self.assertEqual(f(['ws1', 'ws2', 'ws3', 'ws4'], {'ws1', 'ws2', 'ws3'}), ['ws1', 'ws2', 'ws3', 'ws4'])
        self.assertEqual(f(['ws2', 'ws1', 'ws3', 'ws4'], {'ws3', 'ws1'}), ['ws2', 'ws1', 'ws3', 'ws4'])

    @unittest.skipUnless(shutil.which('git'), 'git is not installed')
    def test_delivered_and_clean(self):
        app = self.make_app("[priority]\ndelivered_phases = ['handoff', 'cleanup']\n[close]\nmain_branch = 'main'\n")
        app.cfg.data['plugins'] = ['builtin:git']
        from ai_signalbox import plugins
        app.registry = plugins.Registry()
        plugins.load(app.cfg, app.registry)
        self.app = app

        def run(*a, cwd):
            subprocess.run(['git', *a], cwd=cwd, capture_output=True, text=True, check=True)
        for ws in ('ws1', 'ws2', 'ws3'):
            repo = self.tmp / 'work' / ws
            run('init', '-b', 'main', cwd=repo)
            for k, v in (('user.email', 't@example.test'), ('user.name', 't'), ('commit.gpgsign', 'false')):
                run('config', k, v, cwd=repo)
            (repo / 'a.txt').write_text('a', encoding='utf-8')
            run('add', 'a.txt', cwd=repo)
            run('commit', '-m', 'a', cwd=repo)

        def card(s, **fields):
            app.store.put_status(s['sid'], {**app.store.get_status(s['sid']), **fields})

        def who(s):
            return {'workspace': s['workspace'], 'name': s['name'], 'sid': s['sid']}
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            priority.set_order(app, ['ws2', 'ws3', 'ws1', 'ws4'], 'person')
        for ws, s in (('ws1', self.s1), ('ws2', self.s2), ('ws3', self.s3)):
            app.delivery.claim(ws, s['sid'])
        card(self.s2, item='10015', phase='post')
        self.assertIsNone(priority.delivered(app, who(self.s2)))
        card(self.s2, phase='handoff')
        run('checkout', '-b', 'feature-10015-x', cwd=self.tmp / 'work' / 'ws2')
        self.assertIsNone(priority.delivered(app, who(self.s2)))
        self.assertEqual(priority.clean(app, 'ws2'), (False, 'ws2 on feature-10015-x'))
        run('checkout', 'main', cwd=self.tmp / 'work' / 'ws2')
        (self.tmp / 'work' / 'ws2' / 'a.txt').write_text('b', encoding='utf-8')
        self.assertEqual(priority.clean(app, 'ws2'), (False, 'ws2 with changes in the tree'))
        (self.tmp / 'work' / 'ws2' / 'a.txt').write_text('a', encoding='utf-8')
        s5 = self.session('ws2', name='other')
        card(s5, item='10016', phase='implement')
        self.assertIsNone(priority.delivered(app, who(self.s2)))
        self.end(s5)
        self.assertNotIn('priority_delivered', app.store.get_status(self.s2['sid']))
        (self.tmp / 'work' / 'ws2' / 'loose.log').write_text('x', encoding='utf-8')
        card(self.s3, item='10014', phase='validate')
        from ai_signalbox import hooks
        hooks.stop(app, {'session_id': self.s2['sid'], 'last_assistant_message': 'handoff done'})
        p = priority.read(app)
        self.assertEqual((p['order'], p['by']), (['ws1', 'ws3', 'ws2', 'ws4'], 'signalbox'))
        e = [x for x in app.events.all() if x['kind'] == 'priority'][-1]
        self.assertEqual((e['by'], e['reason'], e['changed'], e['workspace']),
                         ('signalbox', 'ws2 delivered and clean; the free workspaces go back to their number (ws2 is 3: '
                                       'place 2 is taken)', ['ws1', 'ws2'], 'ws2'))
        self.assertEqual([len(self.notices_of(app, w)) for w in ('ws1', 'ws2', 'ws3')], [1, 1, 0])
        n1 = self.notices_of(app, 'ws1')[0]['text']
        self.assertTrue(n1.startswith('[signalbox->ws1] NOTICE: ws2 delivered and clean'))
        self.assertIn('is 1 of 4 (was 3)', n1)
        self.assertTrue(app.store.get_status(self.s2['sid']).get('priority_delivered'))
        card(self.s3, item=None, phase='cleanup')
        new = priority.delivered(app, who(self.s3))
        self.assertEqual(new['order'], ['ws1', 'ws2', 'ws3', 'ws4'])
        self.assertIsNone(priority.delivered(app, who(self.s2)))
        # new work clears the mark; infra does not
        self.act_as(self.s3)
        self.app = app
        self.cli('note', '--item', '-', '--phase', 'infra', 'request')
        self.assertTrue(app.store.get_status(self.s3['sid']).get('priority_delivered'))
        self.cli('note', '--item', '10017', '--phase', 'init', 'init')
        self.assertNotIn('priority_delivered', app.store.get_status(self.s3['sid']))
        card(self.s3, phase='handoff')
        before = len([x for x in app.events.all() if x['kind'] == 'priority'])
        self.assertIsNone(priority.delivered(app, who(self.s3)))
        self.assertEqual(len([x for x in app.events.all() if x['kind'] == 'priority']), before)
        self.assertTrue(app.store.get_status(self.s3['sid']).get('priority_delivered'))
        self.assertIsNone(priority.delivered(app, {'workspace': 'ws9', 'sid': self.s1['sid']}))
        self.assertIsNone(priority.delivered(app, {'workspace': None, 'sid': self.s1['sid']}))

    @staticmethod
    def notices_of(app, ws):
        return [m for _, m in app.store.box_list(ws) if m.get('notice')]


JOBS = '''
[workflow]
neutral = ['free', 'infra']
[jobs]
types = ['export', 'import', 'seed', 'sanitize', 'build', 'test', 'test-api', 'test-web']
synonyms = {exporting = 'export', tests = 'test', api-tests = 'test-api', web-tests = 'test-web'}
done_expected = ['build', 'test', 'test-api', 'test-web']
foreign_labels = 'AC-\\d+|Task[ -]?\\d+'
'''


class JobCase(Case):
    extra_toml = JOBS

    def setUp(self):
        super().setUp()
        self.me = self.session('ws1', name='alpha')
        self.act_as(self.me)
        self.app.store.put_status(self.me['sid'], {'workspace': 'ws1', 'name': 'alpha', 'item': '10011',
                                                   'phase': 'validate'})

    def job(self, name):
        return next(j for j in self.card(self.me)['jobs'] if j['id'] == name)

    def line(self, *argv):
        return self.cli('job', *argv)


class JobTest(JobCase):
    def test_steps_wait_rerun_done_at_skip_close(self):
        self.tick()
        today = now().date().isoformat()
        other = self.session('ws1', name='omega')
        self.app.store.put_status(other['sid'], {'jobs': [{'id': 'copy-x', 'start': f'{today}T00:00:00', 'st': 'ok',
                                                           'steps': []}]})
        base = len(self.events(sid=self.me['sid']))
        self.assertEqual(self.line('copy-x', '--steps', 'export,import,sanitize', '--pid', '4242', '--from', 'copy-x')[0], 0)
        j = self.job('copy-x')
        self.assertEqual((j['item'], j['pid'], j['st'], j['from'], [e['st'] for e in j['steps']]),
                         (10011, 4242, 'running', f"{other['sid']}|copy-x", ['running', 'todo', 'todo']))
        self.assertEqual(self.line('copy-x', '--wait', 'person', '--done', 'Exported the backup')[0], 0)
        j = self.job('copy-x')
        self.assertEqual((j['waits'], j['pid'], j['steps'][0]['st'], self.card(self.me)['waiting']),
                         ('person', None, 'ok', 'person'))
        self.assertEqual(self.line('copy-x', '--step', 'import', '--pid', '5151')[0], 0)
        j = self.job('copy-x')
        self.assertEqual((j['waits'], j['pid'], j['steps'][1]['st'], self.card(self.me)['waiting']),
                         (None, 5151, 'running', None))
        self.assertEqual(self.line('copy-x', '--wait')[0], 0)
        self.assertEqual((self.job('copy-x')['waits'], self.card(self.me)['waiting']), ('session', None))
        self.assertEqual(self.line('copy-x', '--wait')[0], 1)
        self.assertEqual(self.line('copy-x', '--step', 'sanitize')[0], 0)
        self.assertEqual(self.line('copy-x', '--end', 'failed', 'sanitize broke', '--done', 'Sanitize failed')[0], 0)
        j = self.job('copy-x')
        self.assertEqual((j['st'], j['note'], [e['st'] for e in j['steps']]), ('failed', 'sanitize broke',
                                                                              ['ok', 'ok', 'failed']))
        self.assertEqual(self.line('copy-x', '--step', 'sanitize')[0], 0)  # a failed step runs again, a new line
        j = self.job('copy-x')
        self.assertEqual((j['st'], j['note'], [(e['name'], e['st']) for e in j['steps'][2:]]),
                         ('running', None, [('sanitize', 'failed'), ('sanitize', 'running')]))
        self.assertEqual(self.line('copy-x', '--end', 'ok', 'again')[0], 0)
        self.assertEqual(self.line('copy-x', '--step', 'seed')[0], 1)
        self.assertEqual(self.line('copy-x', '--steps', 'export')[0], 1)
        dones = [e for e in self.events(sid=self.me['sid'])[base:] if e['kind'] == 'done']
        self.assertEqual([(f['job'], f['step'], f['type'], f['item'], f['text']) for f in dones],
                         [('copy-x', 'export', 'export', '10011', 'Exported the backup'),
                          ('copy-x', 'sanitize', 'sanitize', '10011', 'Sanitize failed')])
        self.assertEqual([e.get('waiting') for e in self.events(sid=self.me['sid'])[base:] if e['kind'] == 'status'],
                         ['person', None])
        code, out, _ = self.line()
        self.assertIn('copy-x · 10011 · ok', out)
        self.assertEqual(self.line('--end', 'ok')[0], 1)
        self.assertEqual(self.line('x', '--steps', 'build', '--done', 'Built')[0], 1)
        # --at: the real time of a step, never before the last one
        self.freeze(dt.datetime.fromisoformat(f'{today}T00:00:10'))
        self.assertEqual(self.line('build-1', '--steps', 'build,test', '--item', '-', '--at', f'{today}T00:00:02')[0], 0)
        self.assertEqual(self.line('build-1', '--step', 'test', '--at', f'{today}T00:00:01')[0], 1)
        self.assertEqual(self.line('build-1', '--step', 'test', '--at', f'{today}T00:00:03')[0], 0)
        self.assertEqual((self.job('build-1')['item'], self.job('build-1')['steps'][0]['end']),
                         (None, f'{today}T00:00:03'))
        self.tick()
        self.assertEqual(self.line('skip', '--steps', 'export,import,sanitize,seed', '--item', '-')[0], 0)
        self.assertEqual(self.line('skip', '--step', 'sanitize')[0], 0)
        self.assertEqual([e['st'] for e in self.job('skip')['steps']], ['ok', 'skipped', 'running', 'todo'])
        self.assertEqual((self.job('skip')['steps'][1]['start'], self.job('skip')['steps'][1]['end']), (None, None))
        self.assertEqual(self.line('skip', '--step', 'check')[0], 0)
        self.assertEqual(self.line('skip', '--end', 'ok')[0], 0)
        self.assertEqual([e['st'] for e in self.job('skip')['steps']], ['ok', 'skipped', 'ok', 'todo', 'ok'])
        self.assertEqual(self.line('wait-p', '--steps', 'export,import')[0], 0)
        self.assertEqual(self.line('wait-p', '--wait', 'person')[0], 0)
        from ai_signalbox import jobs
        self.assertEqual(sorted(jobs.close_all(self.app, self.me)),
                         ['build-1 failed (session closed)', 'wait-p ok (stopped waiting for the person)'])
        self.assertEqual((self.job('build-1')['st'], self.job('build-1')['steps'][1]['st'], self.job('wait-p')['note'],
                          self.job('wait-p')['steps'][1]['st']),
                         ('failed', 'failed', 'stopped waiting for the person', 'todo'))
        self.assertEqual(jobs.close_all(self.app, self.me), [])

    def test_zero_seconds_duration_types(self):
        clock = self.freeze(now().replace(hour=0, minute=59, second=0))
        t = clock[0].isoformat()[:11]

        def walk(seconds):
            clock[0] += dt.timedelta(seconds=seconds)
        self.assertEqual(self.line('check-1', '--steps', 'build,api-tests,test-web')[0], 0)
        self.assertEqual([(e['type'], e['name']) for e in self.job('check-1')['steps']],
                         [('build', 'build'), ('test-api', 'api-tests'), ('test-web', 'test-web')])
        code, _, err = self.line('check-1', '--step', 'test-api')
        self.assertEqual((code, '0 s' in err and '--duration' in err), (1, True))
        self.assertEqual([e['st'] for e in self.job('check-1')['steps']], ['running', 'todo', 'todo'])
        self.assertEqual(self.line('check-1', '--step', 'test-api', '--duration', '49')[0], 0)
        b = self.job('check-1')
        self.assertEqual((b['start'], b['steps'][0]['start'], b['steps'][0]['end'], b['steps'][1]['start']),
                         (f'{t}00:58:11', f'{t}00:58:11', f'{t}00:59:00', f'{t}00:59:00'))
        walk(15)
        self.assertEqual(self.line('check-1', '--step', 'web-tests')[0], 0)
        e = self.job('check-1')['steps']
        self.assertEqual((e[1]['st'], e[1]['end'], e[2]['st'], e[2]['start']),
                         ('ok', f'{t}00:59:15', 'running', f'{t}00:59:15'))
        walk(5)
        code, _, err = self.line('check-1', '--end', 'ok', '--duration', '30')
        self.assertEqual((code, 'before the end of the one before' in err), (1, True))
        self.assertEqual(self.line('check-1', '--end', 'ok', '134 specs')[0], 0)
        self.assertEqual([x['st'] for x in self.job('check-1')['steps']], ['ok', 'ok', 'ok'])
        self.assertEqual(self.line('broke', '--steps', 'build', '--item', '-')[0], 0)
        self.assertEqual(self.line('broke', '--end', 'failed', 'compile error')[0], 0)  # a failure closes as it came
        self.assertEqual(self.job('broke')['steps'][0]['st'], 'failed')
        self.assertEqual(self.line('other', '--steps', 'build', '--duration', '5')[0], 1)
        self.assertEqual(self.line('other', '--steps', 'build', '--item', '-')[0], 0)
        self.assertEqual(self.line('other', '--step', 'test-api', '--duration', 'x')[0], 1)
        self.assertEqual(self.line('other', '--wait', '--duration', '20')[0], 0)
        self.assertEqual(self.job('other')['steps'][0]['start'], f'{t}00:59:00')
        self.assertEqual(self.line('other', '--end', 'ok', '--duration', '20')[0], 1)

    def test_toggles_and_foreign_done_lines(self):
        self.tick()
        base = len(self.events(sid=self.me['sid']))
        self.assertEqual(self.line('round', '--steps', 'build,up', '--toggle', 'email')[0], 1)
        self.assertEqual(self.line('round', '--steps', 'build,up,AC-12,AC-14,down', '--toggle', 'email=off')[0], 0)
        j = self.job('round')
        self.assertEqual(j['toggles'], {'email': [{'st': 'off', 'at': j['start'], 'step': 'build'}]})
        self.assertEqual(self.line('round', '--step', 'up', '--done', 'Compiled the solution: 0 errors')[0], 0)
        self.assertEqual(self.line('round', '--step', 'AC-12', '--done', 'Started the server on 8001')[0], 0)
        self.assertEqual(self.line('round', '--toggle', 'email=on')[0], 0)
        self.assertEqual(self.line('round', '--toggle', ' email = ON ')[0], 0)  # spaces and case: the same switch, no-op
        j = self.job('round')
        self.assertEqual(([x['st'] for x in j['toggles']['email']], [e['st'] for e in j['steps']]),
                         (['off', 'on'], ['ok', 'ok', 'running', 'todo', 'todo']))
        self.assertEqual(self.line('round', '--toggle', 'email=on', '--item', '10010')[0], 1)
        self.assertEqual(self.line('nojob', '--toggle', 'email=on')[0], 1)
        ac12 = j['steps'][2]['start']
        before = (dt.datetime.fromisoformat(ac12) - dt.timedelta(seconds=60)).isoformat()
        code, _, err = self.line('round', '--step', 'AC-14', '--at', before)
        self.assertEqual((code, 'step AC-12 opened' in err and 'stop the sequence' in err), (1, True))
        code, _, err = self.line('round', '--step', 'down', '--done', 'AC-14 ok: Validated the error log')
        self.assertEqual((code, 'belongs to AC-14' in err and 'closes step AC-12' in err), (1, True))
        self.assertEqual([e['st'] for e in self.job('round')['steps']], ['ok', 'ok', 'running', 'todo', 'todo'])
        self.assertEqual(self.line('round', '--wait', 'person', '--toggle', 'email=off', '--done',
                                   'AC-12 ok: Validated 11 rows')[0], 0)
        self.assertEqual(self.line('round', '--step', 'AC-14', '--done', 'ac14 ok: Validated the log')[0], 1)
        self.assertEqual((self.job('round')['waits'], self.card(self.me)['waiting']), ('person', 'person'))
        self.assertEqual(self.line('round', '--step', 'AC-14', '--toggle', 'email=on')[0], 0)
        self.assertEqual(self.line('round', '--step', 'down', '--done', 'AC-14 ok: Validated the error log')[0], 0)
        self.assertEqual(self.line('round', '--end', 'ok', '--toggle', 'email=off', '--done', 'Down ok: stopped all')[0], 0)
        j = self.job('round')
        self.assertEqual((j['st'], j['toggles']['email'][-1]['at']), ('ok', j['end']))
        self.assertEqual(self.line('round', '--toggle', 'email=on')[0], 0)
        self.assertEqual([(x['st'], x['step']) for x in self.job('round')['toggles']['email']],
                         [('off', 'build'), ('on', 'AC-12'), ('off', 'AC-12'), ('on', 'AC-14'), ('off', 'down'),
                          ('on', 'down')])
        evs = self.events(sid=self.me['sid'])[base:]
        self.assertEqual([(f['step'], f['text'][:6]) for f in evs if f['kind'] == 'done'],
                         [('build', 'Compil'), ('up', 'Starte'), ('AC-12', 'AC-12 '), ('AC-14', 'AC-14 '),
                          ('down', 'Down o')])
        jobs_ev = [e for e in evs if e['kind'] == 'job']
        self.assertEqual((jobs_ev[0]['toggle'], jobs_ev[-1]['text'], 'toggle' in jobs_ev[1]),
                         ({'name': 'email', 'st': 'off'}, 'round: email on', False))

    def test_services(self):
        self.tick()
        base = len(self.events(sid=self.me['sid']))
        self.assertEqual(self.line('rev', '--steps', 'build,up,AC-01,down', '--services', 'api:8001')[0], 1)
        self.assertEqual(self.line('rev', '--steps', 'build,up,AC-01,down')[0], 0)
        self.assertEqual(self.line('rev', '--services', 'api:8001')[0], 0)
        code, _, err = self.line('rev', '--step', 'up', '--done', 'Solution (api): 0 errors, 3 warnings')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(self.line('rev', '--step', 'AC-01', '--services', 'api:8001, web:8000,docs:8500,tunnel',
                                   '--done', 'Started everything')[0], 0)
        self.assertEqual(self.line('rev', '--services', 'cdp:9222')[0], 0)
        self.assertEqual(self.line('rev', '--services', 'api 8001')[0], 1)
        self.assertEqual(self.line('rev', '--services', ',')[0], 1)
        self.assertEqual(self.line('rev', '--services', 'cdp:9222', '--done', 'x')[0], 1)
        self.assertEqual(self.line('rev', '--step', 'down', '--done', 'AC-01 ok: Validated the mail', '--services',
                                   'api:8001')[0], 0)
        self.assertEqual(self.line('rev', '--end', 'ok', '--services', 'api:8001,web:8000', '--done', 'Down ok')[0], 0)
        steps = {e['name']: e.get('services') for e in self.job('rev')['steps']}
        self.assertEqual(steps, {'build': ['api:8001'], 'up': ['api:8001', 'web:8000', 'docs:8500', 'tunnel'],
                                 'AC-01': ['cdp:9222', 'api:8001'], 'down': ['api:8001', 'web:8000']})
        self.assertEqual(self.line('rev', '--services', 'api:8001')[0], 1)
        jobs_ev = [e for e in self.events(sid=self.me['sid'])[base:] if e['kind'] == 'job']
        self.assertEqual([(e.get('services') or {}).get('step') for e in jobs_ev],
                         [None, 'build', None, 'up', 'AC-01', 'AC-01', 'down'])
        self.assertEqual(jobs_ev[4]['text'], 'rev: services cdp:9222')
        self.assertEqual(self.line('check', '--steps', 'build,test-api', '--item', '-')[0], 0)
        code, _, err = self.line('check', '--step', 'test-api')
        self.assertEqual((code, 'step build closed without --done' in err), (0, True))
        code, _, err = self.line('check', '--end', 'failed', 'broke')
        self.assertEqual((code, err), (0, ''))
