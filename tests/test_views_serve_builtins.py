"""Board, peek, events, watch, serve, the built-in plugins, plugin loading and the CLI odds and ends."""
import contextlib
import http.client
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import unittest

from helpers import ROOT, Case

from ai_signalbox import views
from ai_signalbox.config import Config
from ai_signalbox.util import touch


def write_transcript(case, s, entries):
    cwd = str(case.tmp / 'work' / s['workspace'] / 'app')
    folder = case.tmp / 'host' / 'projects' / re.sub(r'[:\\/]', '-', cwd)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{s['sid']}.jsonl").write_text('\n'.join(json.dumps(e) for e in entries), encoding='utf-8')


class ViewTest(Case):
    extra_toml = '[resources.cache]\ndescription = "the build cache"\n[resources.db]\nshared = true\n'

    def test_board_text_and_json(self):
        a = self.session('ws1', name='alpha')
        b = self.session('ws2', name='beta')
        touch(self.app.store.start_path('ws1', a['sid']))
        self.app.store.put_status(a['sid'], {'item': '42', 'phase': 'review', 'waiting': 'person', 'text': 'diff ready',
                                             'updated': '2099-01-01T10:00:00', 'requested': {'text': 'x'}})
        self.app.store.put_lease('cache', {'res': 'cache', 'workspace': 'ws1', 'name': 'alpha', 'sid': a['sid'],
                                           'since': '2026-01-01T10:00:00', 'why': 'build'})
        self.app.store.put_queue('cache', [{'workspace': 'ws2', 'name': 'beta', 'sid': b['sid']}])
        self.app.store.put_shared('db', [{'workspace': 'ws2', 'name': 'beta', 'sid': b['sid'], 'mode': 'shared'}])
        self.app.store.put_priority({'order': ['ws2', 'ws1'], 'at': '2026-01-01T09:00:00', 'by': 'person'})
        self.app.store.inbox_append('ws3', '- note\n')
        self.app.store.put_thread({'id': '20260101-100000-aaaaaa', 'workspace': 'ws2', 'sid': None, 'state': 'open',
                                   'opened_at': '2026-01-01T10:00:00', 'turns': [{'n': 1, 'from': 'person',
                                                                                  'text': 'question'}]})
        self.app.log_line('- 10:00:00 **x -> y**\n')
        self.app.registry.board_sections.append(('cache', lambda app, sessions, res: ['cache fact']))
        self.app.registry.board_sections.append((None, lambda app, sessions: ['\nSite section']))
        code, out, _ = self.cli('board')
        self.assertEqual(code, 0)
        self.assertIn('ws1   alpha        idle', out)
        self.assertIn('42 · review · at their request · waiting: person · priority 2 of 2', out)
        self.assertIn('ws2   beta', out)
        self.assertIn('does not listen', out)  # beta: no start file, no vigil, idle
        self.assertIn('ws3   (no session)', out)
        self.assertIn('cache     ws1 alpha since 10:00: build   queue: ws2 beta   higher priority in the queue: ws2 beta',
                      out)
        self.assertIn('            cache fact', out)
        self.assertIn('db        free   shared: ws2 beta', out)
        self.assertIn('Site section', out)
        self.assertIn('Waiting for the person\n  ws1 alpha: 42 diff ready', out)
        self.assertIn('Open threads', out)
        self.assertIn('Inbox with notes for: ws3', out)
        self.assertIn('Log of today: 1 messages', out)
        code, out, _ = self.cli('board', '--json')
        j = json.loads(out)
        self.assertEqual([w['workspace'] for w in j['workspaces']], ['ws1', 'ws2', 'ws3'])
        self.assertEqual(j['resources'][0]['ahead'], ['ws2 beta'])
        self.assertEqual(j['threads'][0]['messages'][0]['from'], 'me')

    def test_peek_events_watch(self):
        a = self.session('ws1', name='alpha')
        b = self.session('ws2', name='beta')
        self.app.store.put_status(a['sid'], {'item': '42', 'task': '7', 'phase': 'verify', 'updated': '2026-01-01T10:00',
                                             'tasks': {'7': {'phase': 'verify', 'ts': '2026-01-01T10:00:00'}},
                                             'asks': [{'n': 1, 'type': 'question', 'text': 'Ship?'}],
                                             'off_guide': {'from': 'plan', 'to': 'verify'}})
        write_transcript(self, a, [
            {'type': 'user', 'timestamp': '2026-01-01T10:00:00Z', 'message': {'content': 'Fix it'}},
            {'type': 'assistant', 'timestamp': '2026-01-01T10:01:00Z', 'message': {'content': [
                {'type': 'tool_use', 'name': 'Skill', 'input': {'skill': 'check'}},
                {'type': 'text', 'text': 'Done.'}]}}])
        self.app.registry.peek_sections.append(lambda app, s: ['site: tree clean'])
        code, out, _ = self.cli('peek', 'alpha')
        self.assertIn('status: item 42 · task 7 · phase verify', out)
        self.assertIn('guide - · off guide: plan -> verify', out)
        self.assertIn('tasks: 7 verify', out)
        self.assertIn('asks: #1 question: Ship?', out)
        self.assertIn('site: tree clean', out)
        self.assertIn('last prompt of the person', out)
        self.assertIn('Done.', out)
        self.assertIn('== ws3 (no session)', self.cli('peek', 'ws3')[1])
        self.assertEqual(self.cli('peek', 'nobody')[0], 1)
        j = json.loads(self.cli('peek', 'ws1', '--json')[1])
        self.assertEqual(j[0]['card']['item'], '42')
        code, out, _ = self.cli('events')
        self.assertIn('ws1 alpha', out)
        self.assertIn('skill', out)
        self.assertEqual(json.loads(self.cli('events', '--json')[1])[0]['kind'], 'prompt')
        # watch: one line per new event of the other sessions; while holding the pump resource, it pumps the
        # transcripts of sessions without hooks
        app = self.make_app(self.extra_toml + "[watch]\npump_resource = 'cache'\n")
        self.act_as(b)
        app.store.put_lease('cache', {'res': 'cache', 'workspace': 'ws2', 'name': 'beta', 'sid': b['sid'],
                                      'since': '2026-01-01T10:00:00'})
        app.emit('idle', a, 'from alpha', 'hook')
        out = io.StringIO()
        th = threading.Thread(target=lambda: views.watch(app, interval=0.05, rounds=3))
        with contextlib.redirect_stdout(out):
            th.start()
            time.sleep(0.02)
            app.emit('prompt', a, 'later from alpha', 'cli')
            app.emit('prompt', b, 'mine', 'cli')
            th.join(5)
        self.assertIn('later from alpha', out.getvalue())
        self.assertNotIn('mine', out.getvalue())
        pumped = [e for e in app.events.all() if e.get('src') == 'transcript']
        self.assertEqual(pumped, [])  # alpha writes hook events: the pump leaves it alone
        c = self.session('ws3', name='gamma')
        import datetime as dt
        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)).isoformat()
        write_transcript(self, c, [{'type': 'user', 'timestamp': future, 'message': {'content': 'old session ask'}}])
        views.watch(app, interval=0, rounds=1)
        pumped = [e for e in app.events.all() if e.get('src') == 'transcript']
        self.assertEqual([(e['kind'], e['text'], e['sid']) for e in pumped], [('prompt', 'old session ask', c['sid'])])


class ServeTest(Case):
    def setUp(self):
        super().setUp()
        from ai_signalbox.serve import Server
        self.me = self.session('ws1', name='alpha')
        touch(self.app.store.start_path('ws1', self.me['sid']))
        self.server = Server(self.app, port=0, token='secret').start(background=True)
        self.addCleanup(self.server.stop)

    def call(self, method, path, body=None, token='secret'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=5)
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, json.loads(data or b'{}')

    def test_rest_protocol(self):
        self.assertEqual(self.call('GET', '/api/chat', token=None)[0], 401)
        self.assertEqual(self.call('GET', '/api/chat', token='wrong')[0], 401)
        self.assertEqual(self.call('GET', '/api/chat'), (200, {'threads': []}))
        code, msg = self.call('POST', '/api/chat/ws1', {'text': 'Run the tests'})
        self.assertEqual((code, msg['from'], msg['text'], msg['state']), (200, 'me', 'Run the tests', 'sent'))
        tid = msg['tag']
        code, data = self.call('GET', '/api/chat')
        self.assertEqual((data['threads'][0]['thread'], data['threads'][0]['count'], data['threads'][0]['workspace']),
                         (tid, 1, 'ws1'))
        self.app.delivery.claim('ws1', self.me['sid'])
        self.act_as(self.me)
        with contextlib.redirect_stdout(io.StringIO()):
            from ai_signalbox import threads
            threads.thread(self.app, tid, 'All green.')
        code, data = self.call('GET', f'/api/chat/{tid}')
        self.assertEqual([(m['from'], m['text']) for m in data['messages']], [('me', 'Run the tests'),
                                                                              ('other', 'All green.')])
        self.assertEqual(data['messages'][0]['state'], 'delivered')
        code, data = self.call('GET', f'/api/chat/{tid}?limit=1')
        self.assertEqual([m['text'] for m in data['messages']], ['All green.'])
        code, data = self.call('GET', f"/api/chat/{tid}?before={tid}.2")
        self.assertEqual([m['text'] for m in data['messages']], ['Run the tests'])
        code, msg = self.call('POST', f'/api/chat/{tid}', {'text': 'Thanks'})
        self.assertEqual((code, msg['from'], msg['id']), (200, 'me', f'{tid}.3'))
        self.assertEqual(self.call('PATCH', f'/api/chat/{tid}/x', {'removed': True})[0], 405)
        self.assertEqual(self.call('GET', '/api/chat/20990101-000000-000000')[0], 404)
        self.assertEqual(self.call('POST', '/api/chat/ws1', {'text': ''})[0], 409)

    def test_event_stream(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=5)
        conn.request('GET', '/api/events?token=secret')
        r = conn.getresponse()
        self.assertEqual(r.getheader('Content-Type'), 'text/event-stream')
        time.sleep(0.2)
        self.call('POST', '/api/chat/ws1', {'text': 'Streamed'})
        line = b''
        deadline = time.time() + 5
        while time.time() < deadline and not line.startswith(b'data:'):
            line = r.fp.readline()
        conn.close()
        payload = json.loads(line[5:])
        self.assertEqual((payload['message']['text'], payload['state']), ('Streamed', 'open'))


class PortsRelayTest(Case):
    extra_toml = '[resources.app]\nports = [8000, 8001]\nstop_on_close = true\n[resources.browser]\nports = [9222]\n'

    def setUp(self):
        super().setUp()
        from ai_signalbox.builtin import ports
        from ai_signalbox.plugins import Api
        ports.register(Api(self.app.registry, self.app.cfg))

    def test_ports(self):
        mine = self.session('ws1', name='alpha')
        other = self.script('node')
        self.procs.table[other]['where'] = str(self.tmp / 'work' / 'ws2' / 'app' / 'server.js')
        self.procs.ports[8001] = other
        self.act_as(mine)
        code, _, err = self.cli('hold', 'app')
        self.assertEqual(code, 1)
        self.assertIn('port really in use: 8001 with ws2', err)
        self.assertEqual(self.cli('hold', 'browser')[0], 0)
        out = self.cli('board')[1]
        self.assertIn('actually: 8001 ws2 pid', out)
        own = self.script('node')
        self.procs.table[own]['where'] = str(self.tmp / 'work' / 'ws1' / 'app' / 'server.js')
        self.procs.ports[8000] = own
        self.procs.ports[9222] = own
        code, out, _ = self.cli('close', '--done', '-')
        self.assertIn(f'servers of this workspace stopped: 8000 (node, pid {own})', out)
        self.assertIn(own, self.procs.killed)
        self.assertNotIn(other, self.procs.killed)

    def test_relay(self):
        from ai_signalbox.builtin.relay import Relay
        cfg = Config.from_toml(self.toml(f"[relay]\nroot = '{(self.tmp / 'relay').as_posix()}'\ntimeout = 3\n"), self.tmp)
        relay = Relay(cfg)
        box = self.tmp / 'relay' / 'ws1'
        seen = []

        def window():
            deadline = time.time() + 5
            while time.time() < deadline and len(seen) < 2:
                for f in list(box.glob('*.json')):
                    if f.name.endswith('.done.json'):
                        continue
                    req = json.loads(f.read_text(encoding='utf-8'))
                    seen.append(req)
                    f.unlink()
                    done = f.with_name(f.stem + '.done.json')
                    done.write_text(json.dumps({'ok': req['action'] == 'close', 'error': 'unsaved files'}),
                                    encoding='utf-8')
                time.sleep(0.02)
        th = threading.Thread(target=window)
        th.start()
        me = self.session('ws1', name='alpha')
        result = relay.close_later(self.app, 'ws1', me['sid'], 0, 'window',
                                   f"{me['pid']}:{self.procs.create_time(me['pid'])}", 'alpha')
        th.join(5)
        self.assertEqual(seen, [{'action': 'close', 'session': me['sid']}, {'action': 'closeWindow'}])
        self.assertEqual(result, 'partial')
        self.assertIn(me['pid'], self.procs.killed)
        ev = self.events('close')[-1]
        self.assertEqual((ev['action'], ev['tab'], ev['window'], ev['window_error']),
                         ('window', 'closed', 'failed', 'unsaved files'))
        self.assertEqual(relay.request('ws9', 'tabs', timeout=0.3), (False, 'no window answered (is '
                                                                            'claude-session-relay installed and '
                                                                            'listening as this workspace?)'))
        detached = []
        self.app.detach = lambda *a: detached.append(a) or True
        ctx = {'mine': self.session('ws1', name='beta'), 'workspace': 'ws1', 'delay': 7, 'siblings': [me],
               'dry': False, 'report': {}}
        lines, ok = relay.plan_close(self.app, ctx)
        self.assertTrue(ok)
        self.assertEqual(detached[0][:5], ('window-close', 'ws1', ctx['mine']['sid'], '7', 'tab'))
        self.assertIn("in 7 s the relay closes this session's tab", lines[0])


@unittest.skipUnless(shutil.which('git'), 'git is not installed')
class GitTest(Case):
    def run_git(self, *a, cwd):
        return subprocess.run(['git', *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()

    def setUp(self):
        super().setUp()
        origin = self.tmp / 'origin.git'
        self.run_git('init', '--bare', '-b', 'main', str(origin), cwd=self.tmp)
        seed = self.tmp / 'seed'
        self.run_git('clone', str(origin), str(seed), cwd=self.tmp)
        self.cfg_repo(seed)
        (seed / 'a.txt').write_text('a', encoding='utf-8')
        self.run_git('add', 'a.txt', cwd=seed)
        self.run_git('commit', '-m', 'a', cwd=seed)
        self.run_git('push', 'origin', 'HEAD:main', cwd=seed)
        ws = self.tmp / 'work' / 'ws1'
        shutil.rmtree(ws)
        self.run_git('clone', str(origin), str(ws), cwd=self.tmp)
        self.cfg_repo(ws)
        (ws / 'app').mkdir()
        (seed / 'b.txt').write_text('b', encoding='utf-8')
        self.run_git('add', 'b.txt', cwd=seed)
        self.run_git('commit', '-m', 'b', cwd=seed)
        self.run_git('push', 'origin', 'HEAD:main', cwd=seed)
        self.ws = ws

    def cfg_repo(self, repo):
        for k, v in (('user.email', 't@example.test'), ('user.name', 't'), ('commit.gpgsign', 'false')):
            self.run_git('config', k, v, cwd=repo)

    def test_branch_item_clean_sync(self):
        from ai_signalbox.builtin import git
        from ai_signalbox.plugins import Api
        git.register(Api(self.app.registry, self.app.cfg))
        self.assertEqual(self.app.branch('ws1'), 'main')
        self.assertEqual(git.workspace_clean(self.app, 'ws1'), (True, None))
        ctx = {'siblings': [], 'no_sync': False, 'dry': True, 'workspace': 'ws1', 'synced': [], 'report': {}}
        self.assertIn('would move +1 commit(s) of origin/main', git.sync_step(self.app, ctx)[0])
        ctx['dry'] = False
        lines = git.sync_step(self.app, ctx)
        self.assertIn('main +1 commit(s) of origin/main', lines[0])
        self.assertEqual(len(ctx['synced']), 1)
        self.assertTrue((self.ws / 'b.txt').exists())
        self.run_git('checkout', '-b', 'feature-4242-x', cwd=self.ws)
        self.assertEqual(self.app.item_of('ws1'), '4242')
        (self.ws / 'c.txt').write_text('c', encoding='utf-8')
        self.run_git('add', 'c.txt', cwd=self.ws)
        self.run_git('commit', '-m', 'c', cwd=self.ws)
        self.assertEqual(git.workspace_clean(self.app, 'ws1'), (False, 'ws1 on feature-4242-x'))
        self.assertIn('up to date', git.sync_step(self.app, dict(ctx, synced=[]))[0])
        self.assertEqual(git.sync_step(self.app, dict(ctx, siblings=[{'name': 'x', 'workspace': 'ws1'}]))[0],
                         'sync: skipped, x lives in the workspace')
        self.assertIn('tree: 0 files changed', git.peek_section(self.app, {'workspace': 'ws1'})[0])


class PluginCliTest(Case):
    def test_plugin_loading_and_command(self):
        plugdir = self.tmp / 'plugs'
        plugdir.mkdir()
        (plugdir / 'my_site_plugin.py').write_text(
            "def register(api):\n"
            "    api.resource('lab', 'the lab bench')\n"
            "    api.text('start.title', '=== my site ===')\n"
            "    api.command('hello', lambda app, args: print('hello', args.who) or 0,\n"
            "                configure=lambda p: p.add_argument('who'), help='say hello')\n", encoding='utf-8')
        cfg = Config.from_toml(self.toml().replace('plugins = []', f"plugins = ['my_site_plugin', 'builtin:ports']\n"
                                                                    f"plugin_paths = ['{plugdir.as_posix()}']"),
                               self.tmp)
        from ai_signalbox.app import App
        app = App(cfg, procs=self.procs)
        self.assertEqual(app.registry.loaded, ['my_site_plugin', 'builtin:ports'])
        self.assertIn('lab', app.resources())
        self.assertEqual(app.text('start.title'), '=== my site ===')
        self.app = app
        code, out, _ = self.cli('hello', 'world')
        self.assertEqual((code, out.strip()), (0, 'hello world'))
        (plugdir / 'broken_plugin.py').write_text('x = 1\n', encoding='utf-8')
        cfg = Config.from_toml(self.toml().replace('plugins = []', f"plugins = ['broken_plugin']\n"
                                                                    f"plugin_paths = ['{plugdir.as_posix()}']"),
                               self.tmp)
        with self.assertRaises(ValueError):
            App(cfg, procs=self.procs)

    def test_hooks_installer_and_version(self):
        settings = self.tmp / 'host' / 'settings.json'
        code, out, _ = self.cli('hooks', 'install', '--settings', str(settings), '--command', 'signalbox')
        self.assertEqual(code, 0)
        self.assertIn('PreToolUse', out)
        data = json.loads(settings.read_text(encoding='utf-8'))
        self.assertEqual(data['hooks']['SessionStart'][0]['hooks'][0]['command'], 'signalbox hook start #signalbox')
        self.assertIn('Stop', self.cli('hooks', 'status', '--settings', str(settings))[1])
        self.assertIn('entries removed', self.cli('hooks', 'remove', '--settings', str(settings))[1])
        self.assertIn('no signalbox hook', self.cli('hooks', 'status', '--settings', str(settings))[1])
        code, out, _ = self.cli('--version')
        from ai_signalbox import __version__
        self.assertIn(f'ai-signalbox {__version__}', out)

    def test_config_file_and_module_entry(self):
        cfg = self.tmp / 'conf' / 'config.toml'
        cfg.parent.mkdir()
        cfg.write_text("root = 'state'\n[workspaces]\nglob = []\n", encoding='utf-8')
        c = Config.load(cfg)
        self.assertEqual((c.root, c.path), (self.tmp / 'conf' / 'state', cfg))
        env = {k: v for k, v in os.environ.items() if not k.startswith('SIGNALBOX')}
        env['PYTHONPATH'] = str(ROOT / 'src')
        p = subprocess.run([__import__('sys').executable, '-m', 'ai_signalbox', '--config', str(cfg), 'priority'],
                           capture_output=True, env=env, cwd=str(self.tmp))
        self.assertEqual((p.returncode, json.loads(p.stdout.decode('utf-8'))), (0, {'order': [], 'at': None, 'by': None}))
        p = subprocess.run([__import__('sys').executable, '-m', 'ai_signalbox', '--config', str(cfg), 'note', '--stage',
                            'x'], capture_output=True, env=env, cwd=str(self.tmp))
        self.assertEqual(p.returncode, 2)
