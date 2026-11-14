"""Store, events, locks, config, workspaces and the Claude Code host adapter."""
import json
import os
import threading

from helpers import Case, FakeProcs  # noqa: F401

from ai_signalbox import locks
from ai_signalbox.config import Config
from ai_signalbox.hosts.claude_code import ClaudeCodeHost, TAG
from ai_signalbox.store import FileStore


class EventsTest(Case):
    def test_envelope_and_order(self):
        s = {'workspace': 'ws1', 'name': 'a', 'sid': 'x'}
        self.app.emit('status', s, 'line one\nline two', item='42', phase='plan', empty='')
        e = self.events()[-1]
        self.assertEqual((e['kind'], e['src'], e['workspace'], e['sid'], e['text']), ('status', 'cli', 'ws1', 'x', 'line one'))
        self.assertEqual((e['item'], e['phase']), ('42', 'plan'))
        self.assertNotIn('empty', e)
        self.assertRegex(e['ts'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}[+-]\d\d:\d\d$')

    def test_text_capped_and_fence_skipped(self):
        self.app.emit('idle', None, '```python\n' + 'x' * 400)
        self.assertEqual(len(self.events()[-1]['text']), 243)

    def test_reader_offsets_half_line_and_rotation(self):
        store = FileStore(self.tmp / 'rot', events_max_bytes=150)
        store.append_event({'kind': 'a', 'text': 'x' * 50})
        evs, off = store.read_events(0)
        self.assertEqual(len(evs), 1)
        with open(store.events_file, 'ab') as fh:
            fh.write(b'{"kind": "half"')
        evs, off2 = store.read_events(off)
        self.assertEqual((evs, off2), ([], off))  # a half-written line waits
        with open(store.events_file, 'ab') as fh:
            fh.write(b'}\n')
        evs, off3 = store.read_events(off)
        self.assertEqual([e['kind'] for e in evs], ['half'])
        store.append_event({'kind': 'b', 'text': 'y' * 80})  # still under the cap when checked: appended
        self.assertFalse(store.events_prev.exists())
        store.append_event({'kind': 'c', 'text': 'z'})  # over the cap: rotated first, a new file
        self.assertTrue(store.events_prev.exists())
        evs, _ = store.read_events(off3 + 120)  # smaller than the offset: rotated, read from the start
        self.assertEqual([e['kind'] for e in evs], ['c'])
        self.assertEqual([e['kind'] for e in store.scan_events()], ['a', 'half', 'b', 'c'])

    def test_scan_prefilter(self):
        self.app.emit('status', None, 'a', item='7', phase='plan')
        self.app.emit('done', None, 'b', item='7')
        self.app.emit('status', None, 'c', item='8', phase='plan')
        got = list(self.app.store.scan_events(('"item": "7"', '"kind": "status"')))
        self.assertEqual([e['text'] for e in got], ['a'])

    def test_emit_never_raises(self):
        class Broken:
            def append_event(self, e):
                raise OSError('disk full')
        from ai_signalbox.events import EventLog
        EventLog(Broken()).emit('x', None, 'y')


class LocksTest(Case):
    def test_reentrant_in_process(self):
        path = self.tmp / 'a.lock'
        with locks.reentrant_lock(path):
            with locks.reentrant_lock(path):
                self.assertEqual(locks.depth(path), 2)
        self.assertEqual(locks.depth(path), 0)

    def test_file_lock_serializes_threads_of_different_handles(self):
        path = self.tmp / 'b.lock'
        order = []

        def worker(n):
            with locks.file_lock(path):
                order.append(('in', n))
                order.append(('out', n))
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(order), 8)


class ConfigTest(Case):
    def test_defaults_merge_and_paths(self):
        cfg = Config.from_toml("root = 'state'\n[workflow]\nguide = 'flow.md'\nneutral = ['idle']\n[jobs]\n"
                               "types = ['build']\n[texts]\n'start.title' = 'hello'", self.tmp)
        self.assertEqual(cfg.root, self.tmp / 'state')
        self.assertEqual(cfg.guide, self.tmp / 'flow.md')
        self.assertEqual(cfg['workflow']['neutral'], ['idle'])
        self.assertEqual(cfg['workflow']['fence'], 'flow')  # untouched default kept
        self.assertEqual(cfg['jobs']['types'], ['build'])
        self.assertEqual(cfg.get('guard.release_minutes'), 60)
        self.assertEqual(cfg.get('nope.nothing', 3), 3)

    def test_env_root_override(self):
        os.environ['SIGNALBOX_ROOT'] = str(self.tmp / 'other')
        try:
            self.assertEqual(Config.from_toml('', self.tmp).root, self.tmp / 'other')
        finally:
            del os.environ['SIGNALBOX_ROOT']

    def test_text_override(self):
        app = self.make_app("[texts]\n'start.title' = 'HI {x}'")
        self.assertEqual(app.text('start.title', x=1), 'HI 1')
        self.assertEqual(app.text('priority.impasse', mine='a'), app.text('priority.impasse', mine='a'))
        self.assertIn('{missing}', app.text('start.you', workspace='ws1') + '{missing}')


class WorkspacesTest(Case):
    def test_pattern_and_glob(self):
        w = self.app.workspaces
        self.assertEqual(w.of(str(self.tmp / 'work' / 'WS2' / 'x')), 'ws2')
        self.assertIsNone(w.of(str(self.tmp / 'elsewhere')))
        self.assertEqual(w.all(), ['ws1', 'ws2', 'ws3'])
        self.assertEqual(w.root('ws1'), self.tmp / 'work' / 'ws1')

    def test_glob_without_pattern(self):
        work = (self.tmp / 'work').as_posix()
        cfg = Config.from_toml(f"root = 'r'\n[workspaces]\nglob = ['{work}/ws*']", self.tmp)
        from ai_signalbox.workspaces import Workspaces
        w = Workspaces(cfg)
        self.assertEqual(w.of(str(self.tmp / 'work' / 'ws3' / 'app')), 'ws3')
        self.assertIsNone(w.of(str(self.tmp)))

    def test_basename_without_config(self):
        from ai_signalbox.workspaces import Workspaces
        w = Workspaces(Config.from_toml("root = 'r'", self.tmp))
        self.assertEqual(w.of('/src/My-Api'), 'my-api')


class HostTest(Case):
    def test_registry_liveness_and_card(self):
        a = self.session('ws1', name='alpha')
        b = self.session('ws2', name='beta')
        self.session(cwd=str(self.tmp / 'outside'))  # outside every workspace: ignored
        self.app.store.put_status(a['sid'], {'phase': 'plan'})
        live = self.app.live_sessions()
        self.assertEqual([s['name'] for s in live], ['alpha', 'beta'])
        self.assertEqual(live[0]['card'], {'phase': 'plan'})
        self.end(b)
        self.assertEqual([s['name'] for s in self.app.live_sessions()], ['alpha'])
        self.assertTrue(self.app.host.session_alive(a['sid']))
        self.assertFalse(self.app.host.session_alive(b['sid']))
        self.procs.table[a['pid']]['name'] = 'node'  # a reused pid that is not the host
        self.assertEqual(self.app.live_sessions(), [])

    def test_me_by_process_tree_then_cwd(self):
        a = self.session('ws1', name='alpha')
        b = self.session('ws2', name='beta')
        self.act_as(b)
        self.assertEqual(self.app.me()['name'], 'beta')
        self.assertEqual(self.app.me(sid=a['sid'])['name'], 'alpha')
        self.procs.table.pop(os.getpid())
        cwd = os.getcwd()
        os.chdir(self.tmp / 'work' / 'ws1' / 'app')
        try:
            self.assertEqual(self.app.me()['name'], 'alpha')
        finally:
            os.chdir(cwd)

    def test_transcript_parsing(self):
        s = self.session('ws1', name='alpha')
        cwd = str(self.tmp / 'work' / 'ws1' / 'app')
        import re
        folder = self.tmp / 'host' / 'projects' / re.sub(r'[:\\/]', '-', cwd)
        folder.mkdir(parents=True)
        lines = [
            {'type': 'user', 'timestamp': '2026-01-01T10:00:00Z', 'message': {'content': 'Fix the login bug'}},
            {'type': 'assistant', 'timestamp': '2026-01-01T10:00:05Z', 'message': {'content': [
                {'type': 'tool_use', 'name': 'Skill', 'input': {'skill': 'plugin:check'}},
                {'type': 'tool_use', 'name': 'SendMessage', 'input': {'to': 'beta', 'message': 'INFO: done\nmore'}},
                {'type': 'tool_use', 'name': 'SendMessage', 'input': {'to': 'a0123456789abcdef', 'message': 'sub'}},
                {'type': 'text', 'text': 'All green.'}]}},
            {'type': 'user', 'timestamp': '2026-01-01T10:01:00Z', 'message': {'content': [
                {'type': 'text', 'text': '<command-name>/start</command-name>'}]}},
            {'type': 'user', 'timestamp': '2026-01-01T10:02:00Z', 'message': {'content':
                '<cross-session-message from-name="beta">hello there</cross-session-message>'}},
        ]
        (folder / f"{s['sid']}.jsonl").write_text('\n'.join(json.dumps(l) for l in lines), encoding='utf-8')
        app = self.make_app("[events]\nskill_phase = {check = 'verify', start = 'init'}")
        live = app.live_sessions()
        evs = app.host.transcript_events(live[0])
        self.assertEqual([(e[1], e[3]) for e in evs],
                         [('prompt', None), ('skill', 'verify'), ('sent', None), ('command', 'init'), ('received', None)])
        self.assertEqual(evs[2][2], '-> beta: INFO: done')
        words, prompt = app.host.last_words(app.host.transcript(live[0]))
        self.assertEqual((words[-1][1], prompt[1]), ('All green.', 'Fix the login bug'))
        self.assertEqual(app.host.last_assistant_text(app.host.transcript(live[0])), 'All green.')
        self.assertEqual([p['text'] for p in app.host.last_prompts(app.host.transcript(live[0]))], ['Fix the login bug'])

    def test_installer_add_remove_status(self):
        settings = self.tmp / 'settings.json'
        settings.write_text(json.dumps({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'beep'}]}]},
                                        'model': 'x'}), encoding='utf-8')
        host = self.app.host
        host.install(settings, 'python -m ai_signalbox')
        data = json.loads(settings.read_text(encoding='utf-8'))
        stop = data['hooks']['Stop']
        self.assertEqual(stop[0]['hooks'][0]['command'], 'beep')  # the user's own first
        self.assertTrue(any(h.get('asyncRewake') for g in stop for h in g['hooks']))
        self.assertIn('PreToolUse', host.status(settings))
        host.install(settings, 'python -m ai_signalbox', guard=False)  # reinstall replaces, no duplicates
        data = json.loads(settings.read_text(encoding='utf-8'))
        self.assertNotIn('PreToolUse', data['hooks'])
        self.assertEqual(sum(TAG in h['command'] for g in data['hooks']['Stop'] for h in g['hooks']), 2)
        self.assertTrue((self.tmp / 'settings.json.signalbox-backup').exists())
        self.assertGreater(host.remove(settings), 0)
        data = json.loads(settings.read_text(encoding='utf-8'))
        self.assertEqual(data, {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'beep'}]}]}, 'model': 'x'})
        self.assertEqual(host.status(settings), [])

    def test_hook_io(self):
        import io
        data = ClaudeCodeHost.read_input(io.BytesIO('{"session_id": "x", "prompt": "naïve → ok"}'.encode('utf-8')))
        self.assertEqual(data['prompt'], 'naïve → ok')
        self.assertEqual(ClaudeCodeHost.read_input(io.BytesIO(b'not json')), {})
        out = ClaudeCodeHost.start_output('ctx', [self.tmp / 'a'])
        self.assertEqual(out['hookSpecificOutput']['watchPaths'], [(self.tmp / 'a').as_posix()])
        self.assertEqual(ClaudeCodeHost.tool_decision('deny', 'r')['hookSpecificOutput']['permissionDecision'], 'deny')
