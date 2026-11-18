"""The fixes of 0.2.0 outside the offers: serve's offers and bad requests, the relay's pid rule, the idle tick's
signature, hooks over a broken config, atomic writes against a reader, the example config."""
import contextlib
import http.client
import io
import json
import os
import shutil
import tempfile
import threading
import time
import tomllib
import unittest
from pathlib import Path

from helpers import ROOT, Case

from ai_signalbox import cli, util
from ai_signalbox.config import Config
from ai_signalbox.util import touch
from ai_signalbox.workspaces import Workspaces


class ServeOffersTest(Case):
    def setUp(self):
        super().setUp()
        from ai_signalbox.serve import Server
        self.me = self.session('ws1', name='alpha')
        touch(self.app.store.start_path('ws1', self.me['sid']))
        self.server = Server(self.app, port=0, token='secret').start(background=True)
        self.addCleanup(self.server.stop)

    def call(self, method, path, body=None, raw=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=5)
        data = raw if raw is not None else (json.dumps(body) if body is not None else None)
        conn.request(method, path, data, {'Content-Type': 'application/json', 'Authorization': 'Bearer secret'})
        r = conn.getresponse()
        out = r.read()
        conn.close()
        return r.status, json.loads(out or b'{}')

    def test_offers_over_rest(self):
        self.assertEqual(self.call('GET', '/api/offers'), (200, {'offers': []}))
        code, o = self.call('POST', '/api/offers', {'text': 'Look at the header', 'workspace': 'ws1', 'from': 'app'})
        self.assertEqual((code, o['workspace'], o['from'], o['title']), (200, 'ws1', 'app', 'Look at the header'))
        code, data = self.call('GET', '/api/offers')
        self.assertEqual([x['id'] for x in data['offers']], [o['id']])
        self.assertEqual(self.call('POST', f"/api/offers/{o['id']}/claim", {})[0], 400)
        code, got = self.call('POST', f"/api/offers/{o['id']}/claim", {'sid': self.me['sid']})
        self.assertEqual((code, got['id'], got['message'], got['sid']), (200, o['id'], o['id'], self.me['sid']))
        code, data = self.call('GET', '/api/offers?state=claimed')
        self.assertEqual([x['id'] for x in data['offers']], [o['id']])
        self.assertEqual(self.call('POST', f"/api/offers/{o['id']}/claim", {'sid': 'sid-ws1-nobody0'})[0], 409)
        code, other = self.call('POST', '/api/offers', {'text': 'Never mind'})
        code, w = self.call('POST', f"/api/offers/{other['id']}/withdraw", {})
        self.assertEqual((code, w['id']), (200, other['id']))
        self.assertEqual(self.call('POST', f"/api/offers/{other['id']}/withdraw", {})[0], 409)
        self.assertEqual(self.call('GET', '/api/offers?state=lost')[0], 400)
        self.assertEqual(self.call('POST', f"/api/offers/{other['id']}/explode", {})[0], 404)
        self.assertEqual(self.call('POST', '/api/offers', {'text': ''})[0], 409)

    def test_bad_requests_answer_400(self):
        code, msg = self.call('POST', '/api/chat/ws1', {'text': 'hello'})
        tid = msg['tag']
        self.assertEqual(self.call('GET', f'/api/chat/{tid}?limit=abc')[0], 400)
        self.assertEqual(self.call('POST', '/api/chat/ws1', raw='[1, 2]')[0], 400)
        self.assertEqual(self.call('POST', '/api/chat/ws1', raw='{not json')[0], 400)

    def test_the_stream_names_offer_events(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=5)
        conn.request('GET', '/api/events?token=secret')
        r = conn.getresponse()
        time.sleep(0.2)
        code, o = self.call('POST', '/api/offers', {'text': 'Streamed offer'})
        lines, deadline = [], time.time() + 5
        while time.time() < deadline and not any(x.startswith(b'data:') for x in lines):
            lines.append(r.fp.readline())
        conn.close()
        self.assertIn(b'event: offer\n', lines)
        payload = json.loads(next(x for x in lines if x.startswith(b'data:'))[5:])
        self.assertEqual(payload, {'offer': o['id'], 'action': 'open', 'state': 'open'})

    def test_concurrent_posts(self):
        results = []

        def post(i):
            results.append(self.call('POST', '/api/chat/ws1', {'text': f'message {i}'})[0])
        ths = [threading.Thread(target=post, args=(i,)) for i in range(8)]
        for t in ths:
            t.start()
        for t in ths:
            t.join(10)
        self.assertEqual(results, [200] * 8)
        self.assertEqual(len(self.app.store.list_threads()), 8)


class RelayPidTest(Case):
    def test_no_start_time_no_kill(self):
        from ai_signalbox.builtin.relay import Relay
        cfg = Config.from_toml(self.toml(f"[relay]\nroot = '{(self.tmp / 'relay').as_posix()}'\ntimeout = 0.2\n"),
                               self.tmp)
        me = self.session('ws1', name='alpha')
        result = Relay(cfg).close_later(self.app, 'ws1', me['sid'], 0, 'tab', f"{me['pid']}:", 'alpha')
        self.assertNotIn(me['pid'], self.procs.killed)
        self.assertEqual(result, 'failed')
        self.assertIn(f"left running (no start time to check the pid against): pid {me['pid']}",
                      self.events('close')[-1]['text'])


class IdleTickTest(Case):
    def test_idle_tick_gets_app_data_who(self):
        seen = []
        self.app.registry.hook_handlers['idle_tick'] = [lambda app, data, who: seen.append((data['session_id'], who))]
        s = self.session('ws1')
        box = self.app.delivery
        box.vigil_max, box.vigil_step = 0.3, 0.05
        box.vigil({'workspace': 'ws1', 'sid': s['sid']}, s['sid'])
        self.assertEqual(seen[0][0], s['sid'])
        self.assertEqual(seen[0][1]['workspace'], 'ws1')


class HookOverBrokenConfigTest(Case):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_a_hook_never_fails_and_a_command_says_why(self):
        bad = self.write('bad.toml', "plugins = ['no_such_plugin_here']\n")
        code, _, err = self.run_cli('--config', bad, 'hook', 'prompt')
        self.assertEqual(code, 0)
        self.assertIn('signalbox: ModuleNotFoundError', err)
        code, _, err = self.run_cli('--config', bad, 'board')
        self.assertEqual(code, 1)
        self.assertIn('no_such_plugin_here', err)
        broken = self.write('broken.toml', 'root = [unclosed\n')
        self.assertEqual(self.run_cli('--config', broken, 'hook', 'stop')[0], 0)


class AtomicWriteTest(Case):
    def test_unique_temp_names_and_none_left(self):
        target = self.tmp / 'x.json'
        errors = []

        def write(i):
            try:
                for k in range(20):
                    util.write_atomic(target, {'i': i, 'k': k})
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        ths = [threading.Thread(target=write, args=(i,)) for i in range(6)]
        for t in ths:
            t.start()
        for t in ths:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(p.name for p in self.tmp.glob('.x.json.*')), [])
        self.assertIn('i', json.loads(target.read_text(encoding='utf-8')))

    @unittest.skipUnless(os.name == 'nt', 'Windows refuses to rename over a file a reader holds open')
    def test_a_reader_holding_the_file_delays_the_write(self):
        target = self.tmp / 'held.json'
        util.write_atomic(target, {'v': 1})
        fh = open(target, encoding='utf-8')  # noqa: SIM115
        threading.Timer(0.2, fh.close).start()
        util.write_atomic(target, {'v': 2})
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')), {'v': 2})


class ExampleConfigTest(unittest.TestCase):
    def test_the_example_loads_and_its_pattern_matches_both_path_styles(self):
        raw = (ROOT / 'examples' / 'config.toml').read_text(encoding='utf-8')
        data = tomllib.loads(raw)
        self.assertNotIn('example_plugin', data.get('plugins') or [], 'the quick start copies only the config')
        tmp = Path(tempfile.mkdtemp(prefix='signalbox-example-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = Config.from_toml(raw, tmp)
        ws = Workspaces(cfg)
        self.assertEqual(ws.of(r'C:\Users\me\src\ws1\app'), 'ws1')
        self.assertEqual(ws.of('/home/me/src/ws2/app'), 'ws2')
        self.assertEqual(ws.of('/home/me/src/ws3'), 'ws3')


class OffersInViewsTest(Case):
    extra_toml = '[offers]\nnotify = true\n[resources.app]\ndescription = "the app"\n'

    def post(self, text, *argv):
        code, out, err = self.cli('offer', *argv, '--text-file', self.write('o.txt', text))
        self.assertEqual(code, 0, err)
        return json.loads(out)['id']

    def test_board_start_and_notice(self):
        me = self.session('ws1', name='alpha')
        oid = self.post('Fix the footer', 'ws1', '--from', 'app')
        code, out, _ = self.cli('board')
        self.assertIn('Open offers', out)
        self.assertIn(f'{oid}  for ws1', out)
        code, out, _ = self.cli('board', '--json')
        self.assertEqual([o['id'] for o in json.loads(out)['offers']], [oid])
        (_, notice), = self.app.store.box_list('ws1')
        self.assertEqual(notice['notice'], 'offer')
        self.assertIn(f'signalbox claim {oid}', notice['text'])
        code, out, _ = self.cli('hook', 'start', stdin=json.dumps({
            'session_id': me['sid'], 'cwd': str(self.tmp / 'work' / 'ws1' / 'app'), 'source': 'startup'}))
        ctx = json.loads(out)['hookSpecificOutput']['additionalContext']
        self.assertIn(f'Offers you may claim (`signalbox claim <id>`, or `claim --next`): {oid} Fix the footer', ctx)

    def test_board_watch_redraws(self):
        from ai_signalbox import views
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            views.watch_board(self.app, 0.5, as_json=True, rounds=2)
        self.assertEqual(out.getvalue().count('"workspaces"'), 2)

    def test_overdue_lease(self):
        s = self.session('ws1', name='alpha')
        clock = self.freeze()
        self.act_as(s)
        self.assertEqual(self.cli('hold', 'app', 'demo', '--minutes', '10')[0], 0)
        import datetime as dt
        clock[0] += dt.timedelta(minutes=11)
        code, out, _ = self.cli('board')
        self.assertIn('(overdue since 10:10)', out)
        code, out, _ = self.cli('board', '--json')
        self.assertTrue(json.loads(out)['resources'][0]['holder']['overdue'])


class PruneTest(Case):
    def test_prune_removes_only_what_is_done_and_old(self):
        st = self.app.store
        st.box_put('ws1', {'id': '20260101-000000-aaaaaa', 'workspace': 'ws1', 'text': 'waiting'})
        st.box_put('ws1', {'id': '20260101-000000-bbbbbb', 'workspace': 'ws1', 'text': 'read'})
        st.box_claim('ws1', '20260101-000000-bbbbbb.json', {'to_sid': 'x'})
        st.put_thread({'id': '20260101-000000-cccccc', 'workspace': 'ws1', 'state': 'closed', 'turns': []})
        st.put_thread({'id': '20260101-000000-dddddd', 'workspace': 'ws1', 'state': 'open', 'turns': []})
        st.offer_put({'id': '20260101-000000-eeeeee', 'workspace': None, 'text': 'x', 'title': 'x'})
        st.offer_move('20260101-000000-eeeeee', 'open', 'withdrawn', {})
        st.offer_put({'id': '20260101-000000-ffffff', 'workspace': None, 'text': 'y', 'title': 'y'})
        (st.root / '.leftover.json.1-2-ab.tmp').write_text('x', encoding='utf-8')
        old = time.time() - 40 * 86400
        for p in st.root.rglob('*'):
            if p.is_file():
                os.utime(p, (old, old))
        code, out, _ = self.cli('prune', '--days', '30', '--dry-run')
        self.assertEqual(json.loads(out)['delivered'], 1)
        self.assertEqual(len(list(st.delivered())), 1, 'a dry run removes nothing')
        code, out, _ = self.cli('prune', '--days', '30')
        res = json.loads(out)
        self.assertEqual((res['delivered'], res['threads'], res['offers'], res['temp']), (1, 1, 1, 1))
        self.assertEqual([m['id'] for _, m in st.box_list('ws1')], ['20260101-000000-aaaaaa'])
        self.assertEqual([t['id'] for t in st.list_threads()], ['20260101-000000-dddddd'])
        self.assertEqual([o['id'] for o in st.offer_list()], ['20260101-000000-ffffff'])
        self.assertEqual(self.cli('prune', '--days', '0.5')[0], 1)
        self.assertEqual(self.events('prune')[-1]['delivered'], 1)


class LeaseLockTest(Case):
    extra_toml = '[resources.app]\ndescription = "the app"\n'

    def test_hold_refuses_without_the_lock(self):
        import contextlib as cl
        s = self.session('ws1')
        self.act_as(s)

        @cl.contextmanager
        def busy():
            yield False
        self.app.store.leases_lock = busy
        code, _, err = self.cli('hold', 'app', 'demo')
        self.assertEqual(code, 1)
        self.assertIn('locked by another command', err)
        self.assertIsNone(self.app.store.get_lease('app'))


class PeekOffersTest(Case):
    def post(self, text, *argv):
        code, out, err = self.cli('offer', *argv, '--text-file', self.write('o.txt', text))
        self.assertEqual(code, 0, err)
        return json.loads(out)['id']

    def test_peek_shows_what_the_session_may_claim_and_what_it_claimed(self):
        a = self.session('ws1', name='alpha')
        self.tick(5)
        taken = self.post('Fix the footer', 'ws1', '--from', 'app')
        mine = self.post('Check the header', 'ws1')
        other = self.post('For ws2 only', 'ws2')
        anyone = self.post('For anyone')
        self.act_as(a)
        self.assertEqual(self.cli('claim', taken)[0], 0)
        code, out, _ = self.cli('peek', 'alpha')
        self.assertEqual(code, 0)
        self.assertIn('offers it may claim (signalbox claim <id>, or claim --next):', out)
        self.assertIn(f'  {mine}  for ws1', out)
        self.assertIn(f'  {anyone}  for any workspace', out)
        self.assertNotIn(other, out)
        self.assertIn('offers it claimed:', out)
        self.assertIn(f'  {taken}  claimed ', out)
        self.assertIn('by ws1 alpha (not answered yet): Fix the footer', out)
        j = json.loads(self.cli('peek', 'alpha', '--json')[1])[0]['offers']
        self.assertEqual(([o['id'] for o in j['open']], [o['id'] for o in j['claimed']]), ([mine, anyone], [taken]))
        code, out, _ = self.cli('peek', 'ws2')
        self.assertIn(other, out, 'a workspace with no session: what a session of it may claim')
        self.assertNotIn('offers it claimed', out)
        self.act_as(None)
        self.assertEqual(self.cli('offers', '--history')[1].count(taken), 1)
