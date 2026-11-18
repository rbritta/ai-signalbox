"""doctor, init, and the sessions registered by hand (register, read, unregister)."""
import json
import os
import tomllib
from pathlib import Path

from helpers import Case

from ai_signalbox import doctor
from ai_signalbox.config import Config
from ai_signalbox.workspaces import Workspaces


class DoctorTest(Case):
    def test_doctor_reports_each_check(self):
        self.session('ws1', name='alpha', cwd=str(self.tmp / 'elsewhere'))
        code, out, _ = self.cli('doctor', '--json')
        checks = {c['check']: c for c in json.loads(out)}
        self.assertEqual(code, 0)
        self.assertEqual(checks['root']['level'], 'ok')
        self.assertIn('outside:', checks['registered sessions']['detail'])
        self.assertEqual(checks['workspace roots']['detail'], 'ws1, ws2, ws3')
        self.assertIn(checks['psutil']['level'], ('ok', 'warn'))
        code, out, _ = self.cli('doctor')
        self.assertIn('ok    root:', out)

    def test_a_python_too_old_fails(self):
        import sys
        real = sys.version_info
        self.addCleanup(setattr, sys, 'version_info', real)
        sys.version_info = (3, 13, 0, 'final', 0)
        checks = {c['check']: c for c in doctor.checks(self.app)}
        self.assertEqual(checks['python']['level'], 'FAIL')


class InitTest(Case):
    def test_init_writes_a_config_whose_rule_names_the_folders(self):
        target = self.tmp / 'cfg' / 'config.toml'
        projects = self.tmp / 'projects'
        (projects / 'api').mkdir(parents=True)
        out = doctor.init(path=target, workspaces=projects, root=self.tmp / 'state2')
        self.assertTrue(out['ok'])
        data = tomllib.loads(target.read_text(encoding='utf-8'))
        ws = Workspaces(Config(data, target.parent, target))
        self.assertEqual(ws.of(str(projects / 'api' / 'src')), 'api')
        self.assertEqual(ws.of(str(projects / 'web').replace('\\', '/')), 'web')
        self.assertEqual(ws.all(), ['api'])
        code, _, err = self.cli('--config', str(target), 'init', '--workspaces', str(projects))
        self.assertEqual(code, 1)
        self.assertIn('--force replaces it', err)


class ManualSessionTest(Case):
    def test_register_read_unregister(self):
        shell = self.script('bash')
        os.chdir(self.tmp / 'work' / 'ws2' / 'app')
        self.addCleanup(os.chdir, Path(__file__).resolve().parent)
        code, out, err = self.cli('register', '--name', 'aider', '--tool', 'aider', '--pid', str(shell))
        self.assertEqual(code, 0, err)
        me = json.loads(out)
        self.assertEqual((me['workspace'], me['name']), ('ws2', 'aider'))
        self.assertTrue(me['sid'].startswith('manual-'))
        live = {s['sid']: s for s in self.app.live_sessions()}
        self.assertTrue(live[me['sid']]['manual'])
        code, out, _ = self.cli('board')
        self.assertIn('aider', out)
        self.assertEqual(self.cli('send', 'ws2', '--sid', me['sid'], '--text-file', self.write('m.txt', 'hello'))[0],
                         0)
        code, out, _ = self.cli('read', '--sid', me['sid'])
        self.assertIn('hello', out)
        self.assertEqual(self.app.store.box_list('ws2'), [])
        code, out, _ = self.cli('read', '--sid', me['sid'])
        self.assertIn('no message waiting', out)
        self.procs.table.pop(shell)
        self.assertNotIn(me['sid'], {s['sid'] for s in self.app.live_sessions()}, 'gone with its process')
        code, out, _ = self.cli('unregister', '--sid', me['sid'])
        self.assertEqual(json.loads(out)['removed'], [me['sid']])
        self.assertEqual(self.cli('unregister', '--sid', me['sid'])[0], 1)
