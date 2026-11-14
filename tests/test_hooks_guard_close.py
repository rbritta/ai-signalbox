"""Hooks, the guard and the close pipeline."""
import json
import os
import re
import time

from helpers import Case

from ai_signalbox import hooks
from ai_signalbox.guard import Guard, executed
from ai_signalbox.util import touch


class HookTest(Case):
    extra_toml = ('[resources.cache]\ndescription = "the build cache"\n[events]\nskill_phase = {check = "verify", '
                  'start = "init"}\nagent_phase = {reviewer = "review"}\n')

    def hook(self, name, data):
        return self.cli('hook', name, stdin=json.dumps(data))

    def test_start(self):
        me = self.session('ws1', name='alpha')
        peer = self.session('ws2', name='beta')
        self.app.store.put_status(peer['sid'], {'item': '42', 'phase': 'plan'})
        self.app.store.put_lease('cache', {'res': 'cache', 'workspace': 'ws2', 'name': 'beta', 'sid': peer['sid'],
                                           'since': '2026-01-01T10:00:00'})
        self.app.store.put_priority({'order': ['ws2', 'ws1'], 'at': None, 'by': 'person'})
        self.app.store.inbox_append('ws1', '- a note\n')
        self.app.delivery.send({'id': '20260101-100000-aaaaaa', 'workspace': 'ws1', 'sid': None, 'text': 'hello there',
                                'at': '2026-01-01T10:00:00', 'thread': '20260101-100000-aaaaaa', 'turn': 1})
        self.app.store.put_thread({'id': '20260101-100000-aaaaaa', 'workspace': 'ws1', 'sid': None, 'state': 'open',
                                   'opened_at': '2026-01-01T10:00:00',
                                   'turns': [{'n': 1, 'from': 'person', 'text': 'hello there'}]})
        detached = []
        self.app.detach = lambda *a: detached.append(a) or True
        self.app.registry.start_lines.append(lambda app, who, sessions: ['SITE LINE'])
        code, out, _ = self.hook('start', {'session_id': me['sid'], 'cwd': str(self.tmp / 'work' / 'ws1' / 'app'),
                                           'source': 'startup'})
        self.assertEqual(code, 0)
        h = json.loads(out)['hookSpecificOutput']
        ctx = h['additionalContext']
        self.assertIn('You are ws1, session alpha', ctx)
        self.assertIn('Live peers: ws2 beta (idle, 42 plan)', ctx)
        self.assertIn('Leases: cache with ws2 beta since 10:00', ctx)
        self.assertIn('ws2 > ws1 (you are 2 of 2)', ctx)
        self.assertIn('The inbox of ws1 has notes', ctx)
        self.assertIn('SITE LINE', ctx)
        self.assertIn('hello there', ctx)
        self.assertIn('Open threads in ws1', ctx)
        self.assertEqual(h['watchPaths'], [p.as_posix() for p in self.app.delivery.watch_paths('ws1', me['sid'])])
        self.assertEqual(detached, [('start-touch', 'ws1', me['sid'])])
        self.assertTrue(self.app.store.start_path('ws1', me['sid']).exists())
        self.assertEqual(self.events('session')[-1]['action'], 'start')
        # outside every workspace: nothing
        code, out, _ = self.hook('start', {'session_id': 'x', 'cwd': str(self.tmp)})
        self.assertEqual((code, out), (0, ''))

    def test_prompt_tool_stop_notify_end(self):
        me = self.session('ws1', name='alpha')
        peer = self.session('ws2', name='beta')
        base = {'session_id': me['sid'], 'cwd': str(self.tmp / 'work' / 'ws1' / 'app')}
        self.app.delivery.send({'id': '20260101-100000-bbbbbb', 'workspace': 'ws1', 'sid': me['sid'], 'text': 'mid turn',
                                'at': '2026-01-01T10:00:00'})
        code, out, _ = self.hook('prompt', {**base, 'prompt': '/start 42'})
        self.assertIn('mid turn', out)
        self.assertEqual((self.events('command')[-1]['command'], self.events('command')[-1]['phase']), ('start', 'init'))
        self.hook('prompt', {**base, 'prompt': 'Fix the login'})
        self.assertEqual(self.events('prompt')[-1]['text'], 'Fix the login')
        self.hook('prompt', {**base, 'prompt': '<cross-session-message from-name="beta">done &amp; ready'
                                               '</cross-session-message>'})
        r = self.events('received')[-1]
        self.assertEqual((r['text'], r['from_workspace']), ('<- ws2 beta: done & ready', 'ws2'))
        self.hook('tool', {**base, 'tool_name': 'Skill', 'tool_input': {'skill': 'site:check'}})
        self.assertEqual(self.events('skill')[-1]['phase'], 'verify')
        self.hook('tool', {**base, 'tool_name': 'Agent', 'tool_input': {'subagent_type': 'reviewer', 'description': 'd'}})
        self.assertEqual(self.events('agent')[-1]['phase'], 'review')
        self.hook('tool', {**base, 'agent_id': 'a1', 'tool_name': 'Skill', 'tool_input': {'skill': 'x'}})
        self.assertEqual(len(self.events('skill')), 1)  # inside a subagent: not a step
        self.hook('tool', {**base, 'tool_name': 'SendMessage', 'tool_input': {'to': 'beta', 'message': 'INFO: built'}})
        m = self.events('msg')[-1]
        self.assertEqual((m['to'], m['to_workspace'], m['head']), ('ws2 beta', 'ws2', 'INFO: built'))
        self.assertIn('**ws1 alpha -> beta (ws2)**', self.app.store.log_read(self.events('msg')[-1]['ts'][:10]))
        self.hook('tool', {**base, 'tool_name': 'SendMessage', 'tool_input': {'to': 'a0123456789abcdef', 'message': 'x'}})
        self.assertEqual(len(self.events('msg')), 1)
        self.hook('stop', {**base, 'last_assistant_message': 'All green.\nmore'})
        self.assertEqual(self.events('idle')[-1]['text'], 'All green.')
        self.hook('notify', {**base, 'message': 'needs permission', 'notification_type': 'permission_prompt'})
        self.assertEqual(self.events('notify')[-1]['type'], 'permission_prompt')
        self.app.delivery.send({'id': '20260101-100000-cccccc', 'workspace': 'ws1', 'sid': me['sid'], 'text': 'unread',
                                'at': '2026-01-01T10:00:00'})
        self.hook('end', {**base, 'reason': 'exit'})
        self.assertEqual(self.events('session')[-1]['reason'], 'exit')
        self.assertIsNone(self.app.store.box_list('ws1')[0][1]['sid'])
        self.assertEqual(peer['workspace'], 'ws2')

    def test_wake_and_vigil(self):
        me = self.session('ws1', name='alpha')
        base = {'session_id': me['sid'], 'cwd': str(self.tmp / 'work' / 'ws1' / 'app')}
        self.app.delivery.send({'id': '20260101-100000-dddddd', 'workspace': 'ws1', 'sid': me['sid'], 'text': 'wake',
                                'at': '2026-01-01T10:00:00'})
        code, _, err = self.hook('wake', {**base, 'file_path': str(self.app.store.signal_path('ws1'))})
        self.assertEqual(code, 2)
        self.assertIn('wake', err)
        self.assertEqual(self.hook('wake', {**base, 'file_path': str(self.app.store.signal_path('ws1'))})[0], 0)
        self.assertEqual(self.hook('wake', {**base, 'agent_id': 'a', 'file_path': 'x'})[0], 0)
        os.environ['SIGNALBOX_VIGIL_STEP'] = '0.01'
        self.addCleanup(os.environ.pop, 'SIGNALBOX_VIGIL_STEP', None)
        self.app = self.make_app(self.extra_toml)
        self.app.delivery.send({'id': '20260101-100000-eeeeee', 'workspace': 'ws1', 'sid': me['sid'], 'text': 'vigil',
                                'at': '2026-01-01T10:00:00'})
        code, _, err = self.hook('vigil', base)
        self.assertEqual(code, 2)
        self.assertIn('vigil', err)

    def test_compaction(self):
        me = self.session('ws1', name='alpha')
        cwd = str(self.tmp / 'work' / 'ws1' / 'app')
        self.app.store.put_status(me['sid'], {'item': '10002', 'phase': 'implement', 'task': '77', 'waiting': 'person',
                                              'text': 'the fix', 'tasks': {'77': {'phase': 'implement'}},
                                              'done': 'Wrote the test', 'done_at': '2026-01-01T09:30:00',
                                              'asks': [{'n': 3, 'text': 'Ship?'}]})
        transcript = self.tmp / 't.jsonl'
        transcript.write_text('\n'.join(json.dumps(x) for x in [
            {'type': 'user', 'timestamp': '2026-01-01T09:00:00Z', 'message': {'content': 'First ask'}},
            {'type': 'user', 'timestamp': '2026-01-01T09:10:00Z', 'message': {'content': '<command-name>/x'}},
            {'type': 'user', 'timestamp': '2026-01-01T09:20:00Z', 'message': {'content': [{'type': 'text',
                                                                                          'text': 'Second ask'}]}},
        ]), encoding='utf-8')
        self.hook('precompact', {'session_id': me['sid'], 'cwd': cwd, 'trigger': 'auto',
                                 'transcript_path': str(transcript)})
        snap = self.app.store.get_snapshot(me['sid'])
        self.assertEqual([p['text'] for p in snap['prompts']], ['First ask', 'Second ask'])
        code, out, _ = self.hook('postcompact', {'session_id': me['sid'], 'cwd': cwd, 'source': 'compact'})
        ctx = json.loads(out)['hookSpecificOutput']['additionalContext']
        self.assertIn('was just compacted (', ctx)
        self.assertIn('Item 10002, phase implement, task 77, waiting for person.', ctx)
        self.assertIn('Last done (09:30): Wrote the test', ctx)
        self.assertIn('Open asks with the person: #3 Ship?', ctx)
        self.assertIn('The person asked (09:20): Second ask', ctx)
        self.assertIn('signalbox peek ws1', ctx)
        self.assertEqual(self.hook('postcompact', {'session_id': me['sid'], 'cwd': cwd, 'source': 'startup'})[1], '')
        self.app.store.put_status(me['sid'], {})
        self.app.store.put_snapshot(me['sid'], {})
        out = self.hook('postcompact', {'session_id': me['sid'], 'cwd': cwd, 'source': 'compact'})[1]
        self.assertIn('no status for this session', out)

    def test_never_fails(self):
        self.app.registry.hook_handlers['stop'] = [lambda app, data, who: 1 / 0]
        me = self.session('ws1', name='alpha')
        self.assertEqual(self.hook('stop', {'session_id': me['sid']})[0], 0)
        self.assertEqual(self.cli('hook', 'nope', stdin='{}')[0], 0)
        self.assertEqual(self.cli('hook', 'start', stdin='not json')[0], 0)
        self.app.host = None  # anything broken inside: one line on stderr, exit 0
        code, _, err = self.cli('hook', 'stop', stdin='{}')
        self.assertEqual(code, 0)
        self.assertIn('signalbox hook stop', err)


class GuardTest(Case):
    def setUp(self):
        super().setUp()
        self.me = self.session('ws1', name='alpha')
        self.g = Guard(self.app)
        self.app.registry.guard_rules.clear()
        from ai_signalbox.plugins import Api
        api = Api(self.app.registry, self.app.cfg)
        api.guard_rule('wipe', lambda cmd, unquoted, cwd: re.search(r'\brm\s+-rf\b', unquoted), 'deny',
                       'never wipe a tree')
        api.guard_rule('push', lambda cmd, unquoted, cwd: re.search(r'\bgit\s+push\b', unquoted), 'person',
                       'a push is the person\'s call.', key='git')
        api.guard_rule('secrets', lambda path: path.endswith('.env'), 'deny', 'not the secrets file', on='path')

    def decide(self, command=None, tool='Bash', **args):
        data = {'session_id': self.me['sid'], 'tool_name': tool, 'tool_input': {'command': command, **args}}
        return self.g.decide(data)

    def test_cli_text_in_shell(self):
        self.assertEqual(self.decide('signalbox ask "the text"')[0], 'deny')
        self.assertIsNone(self.decide('signalbox ask --type decision --text-file a.txt'))
        self.assertEqual(self.decide('python -m ai_signalbox thread 20260101-100000-aaaaaa -')[0], 'deny')
        self.assertEqual(self.decide('echo hi | signalbox post ws2 -')[0], 'deny')
        self.assertEqual(self.decide("signalbox send ws2 <<'EOF'\nhi\nEOF")[0], 'deny')
        self.assertIsNone(self.decide('grep "signalbox ask" notes.md'))
        self.assertIsNone(self.decide('signalbox note --phase plan "short"'))  # note is not a text command
        self.assertIn('--text-file', self.decide('signalbox ask "x"')[1])

    def test_rules_and_releases(self):
        self.assertEqual(self.decide('rm -rf build'), ('deny', 'never wipe a tree'))
        self.assertIsNone(self.decide('grep "rm -rf" notes.md'))  # quoted text only reads
        self.assertEqual(self.decide('bash -c "rm -rf build"')[0], 'deny')  # a script handed to a shell runs
        self.assertEqual(self.decide(None, tool='Write', file_path='/x/.env'), ('deny', 'not the secrets file'))
        self.assertIsNone(self.decide(None, tool='Write', file_path='/x/a.py'))
        d = self.decide('git push origin main')
        self.assertEqual(d[0], 'deny')
        self.assertIn('[release git]', d[1])
        self.assertIn('signalbox ask --type authorization --text-file', d[1])
        # the person's yes in the IDE, right after the tagged ask
        self.act_as(self.me)
        self.cli('ask', '--type', 'authorization', '--text-file', self.write('a.txt', '[release git] push the branch'))
        self.app.emit('prompt', self.me, 'ok, but wait for the build', 'hook')
        self.assertEqual(self.decide('git push')[0], 'deny')
        self.cli('ask', '--type', 'authorization', '--text-file', self.write('b.txt', '[release git] push it now'))
        self.app.emit('prompt', self.me, 'yes go', 'hook')
        self.assertIsNone(self.decide('git push'))
        # the person's yes delivered as the answer to the ask, from the app
        other = self.session('ws2', name='beta')
        g2 = Guard(self.app)
        data = {'session_id': other['sid'], 'tool_name': 'Bash', 'tool_input': {'command': 'git push'}}
        self.assertEqual(g2.decide(data)[0], 'deny')
        self.app.store.box_put('ws2', {'id': '20260101-100000-ffffff', 'workspace': 'ws2', 'sid': other['sid'],
                                       'ask': 1, 'ask_type': 'authorization', 'ask_text': '[release git] push',
                                       'text': 'Checked the diff.\nApproved', 'at': '2026-01-01T10:00:00'})
        self.app.delivery.claim('ws2', other['sid'])
        self.assertIsNone(g2.decide(data))

    def test_executed_and_hook_output(self):
        self.assertEqual(executed('grep "git add" x'), 'grep  x')
        self.assertIn('docker compose up', executed('wsl -- bash -c "docker compose up"'))
        code, out, _ = self.cli('hook', 'guard', stdin=json.dumps({'session_id': self.me['sid'], 'tool_name': 'Bash',
                                                                     'tool_input': {'command': 'rm -rf /'}}))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertEqual(self.cli('hook', 'guard', stdin='{"tool_name": "Bash", "tool_input": {"command": "ls"}}')[1], '')


class CloseTest(Case):
    extra_toml = '[resources.cache]\ndescription = "c"\n[resources.db]\nshared = true\n[close]\ndelay = 0\n'

    def test_close_pipeline(self):
        me = self.session('ws1', name='alpha')
        self.act_as(me)
        self.tick()
        self.cli('hold', 'cache', 'build')
        self.cli('hold', 'db', '--shared')
        self.cli('job', 'build-1', '--steps', 'build,test', '--item', '-')
        self.cli('ask', '--text-file', self.write('a.txt', 'Ship on Friday?'))
        calls = []
        self.app.registry.close_steps.append((50, 'site', lambda app, ctx: calls.append(ctx['siblings']) or ['site: ok']))

        class Window:
            def plan_close(self, app, ctx):
                return [f"window: closes ({'tab' if ctx['siblings'] else 'window'})"], True
        self.app.registry.window = Window()
        code, _, err = self.cli('close')
        self.assertEqual(code, 1)
        self.assertIn('--done-file', err)
        code, out, _ = self.cli('close', '--dry-run')
        self.assertEqual(code, 0)
        self.assertIn('close (dry run, nothing changed)', out)
        self.assertIsNotNone(self.app.store.get_lease('cache'))
        code, out, _ = self.cli('close', '--done-file', self.write('d.txt', 'Shipped the cache layer'))
        self.assertEqual(code, 0)
        self.assertIn('closed: ws1 alpha', out)
        self.assertIn('jobs closed: build-1 failed (session closed)', out)
        self.assertIn('leases: cache, db (shared)', out)
        self.assertIn('site: ok', out)
        self.assertIn('open asks (they stay for the person): #1 question: Ship on Friday?', out)
        self.assertIn('window: closes (window)', out)
        self.assertIn('now: send the reply', out)
        self.assertIsNone(self.app.store.get_lease('cache'))
        self.assertEqual(self.app.store.get_shared('db'), [])
        card = self.card(me)
        self.assertEqual((card['phase'], card['done']), ('free', 'Shipped the cache layer'))
        self.assertEqual([e['action'] for e in self.events('close')], ['start', 'done'])
        self.assertEqual(calls, [[], []])  # the dry run asks the steps too, with ctx['dry']

    def test_sibling_keeps_the_workspace_and_request(self):
        me = self.session('ws1', name='alpha')
        sib = self.session('ws1', name='sib')
        self.act_as(me)
        steps = []
        self.app.registry.close_steps.append((50, 'site', lambda app, ctx: steps.append([s['name'] for s in
                                                                                         ctx['siblings']])))
        code, out, _ = self.cli('close', '--done', '-', '--keep-window')
        self.assertEqual(code, 0)
        self.assertIn('(ws1 sib stays in the workspace)', out)
        self.assertIn('window: stays open (--keep-window)', out)
        self.assertEqual(steps, [['sib']])
        other = self.session('ws2', name='beta')
        code, out, _ = self.cli('close', 'ws2')
        self.assertEqual(code, 0)
        self.assertIn('SendMessage to="beta" (ws2 beta)', out)
        self.assertIn('[ws1->ws2] REQUEST: CLOSE', out)
        self.assertEqual(self.events('close')[-1]['to_workspace'], 'ws2')
        self.assertEqual(self.cli('close', 'ws3')[0], 1)
        self.assertTrue(other and sib)


class InboxLogTest(Case):
    def test_post_inbox_log(self):
        me = self.session('ws1', name='alpha')
        self.act_as(me)
        self.assertEqual(self.cli('post', 'WS3', '--text-file', self.write('p.txt', 'pick up the review'))[0], 0)
        self.assertEqual(self.app.store.inbox_pending(), ['ws3'])
        code, out, _ = self.cli('inbox', 'ws3')
        self.assertIn('from ws1 alpha: pick up the review', out)
        self.assertEqual(self.cli('inbox', 'ws3')[1].strip(), 'inbox empty')
        # the file and the event's field are lower case; the log line and the event text keep the case given
        self.assertIn('**ws1 alpha -> inbox WS3**', self.cli('log')[1])
        self.assertEqual(self.events('post')[-1]['to_workspace'], 'ws3')
        self.assertEqual(self.events('post')[-1]['text'], '-> inbox WS3: pick up the review')
        time.sleep(0)
        touch(self.tmp / 'x', 'y')
