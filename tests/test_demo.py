"""The live demo generator (docs/demo/generate.py): it runs its scenario against the real CLI in a temp folder and
writes a page that holds every step. The caller's own signalbox and host settings are never read or written."""
import html
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from helpers import ROOT

from ai_signalbox import __version__

# Every command of the scenario, in the order the page shows them.
EXPECTED = [
    'signalbox --version',
    'signalbox flow',
    'signalbox hook start',
    'signalbox note --item 101 --phase analysis --text-file note.txt',
    'signalbox note --item 102 --phase implement --text-file note.txt',
    'signalbox note --item 103 --phase init --text-file note.txt',
    'signalbox note --phase implement --text-file note.txt',
    "signalbox hold app 'serve the search page'",
    "signalbox want app 'run the end-to-end tests'",
    "signalbox hold db --shared 'read the schema'",
    'signalbox release app',
    "signalbox hold app 'run the end-to-end tests'",
    'signalbox ask --type review --text-file ask.txt',
    'signalbox send ws1 --text-file message.txt',
    'signalbox hook wake',
    'signalbox job e2e --steps build,test',
    'signalbox job e2e --step test --done-file done.txt',
    'signalbox job e2e --end ok --done-file done.txt',
    'signalbox board',
    'signalbox peek ws2',
]


def load_generator():
    path = ROOT / 'docs' / 'demo' / 'generate.py'
    spec = importlib.util.spec_from_file_location('signalbox_demo_generate', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DemoPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gen = load_generator()
        cls.tmp = Path(tempfile.mkdtemp(prefix='signalbox-demo-test-'))
        # decoys: a caller whose own config, state root and host registry point here must leave them untouched
        cls.decoy = cls.tmp / 'decoy'
        env = {'SIGNALBOX_ROOT': str(cls.decoy / 'state'), 'SIGNALBOX_CONFIG': str(cls.decoy / 'missing.toml'),
               'CLAUDE_CONFIG_DIR': str(cls.decoy / 'host')}
        made = []
        real_mkdtemp = tempfile.mkdtemp

        def mkdtemp(*args, **kwargs):
            made.append(Path(real_mkdtemp(*args, **kwargs)))
            return str(made[-1])
        with mock.patch.dict(os.environ, env), mock.patch.object(cls.gen.tempfile, 'mkdtemp', mkdtemp):
            os.environ.pop('GITHUB_ACTIONS', None)  # the CI label has a test of its own
            cls.steps = cls.gen.build(cls.tmp / 'site')
        cls.workdirs = made
        cls.page = (cls.tmp / 'site' / 'demo' / 'index.html').read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_step_is_on_the_page_in_order(self):
        commands = [s.command for s in self.steps]
        at = 0
        for want in EXPECTED:
            self.assertIn(want, commands)
            found = self.page.find(html.escape('$ ' + want), at)
            self.assertGreaterEqual(found, 0, f'missing or out of order: {want}')
            at = found
        for s in self.steps:
            self.assertIn(html.escape(s.title), self.page)

    def test_three_sessions_open_and_the_wake_hook_wakes_one(self):
        starts = [s for s in self.steps if s.argv == ['hook', 'start']]
        self.assertEqual(['ws1', 'ws2', 'ws3'], [s.actor for s in starts])
        wake = next(s for s in self.steps if s.argv == ['hook', 'wake'])
        self.assertEqual(2, wake.code)
        self.assertIn('Use the staging tenant for the repro, please.', wake.err)

    def test_the_outputs_are_real(self):
        by_command = {s.command: s for s in self.steps}
        self.assertEqual(f'ai-signalbox {__version__}', by_command['signalbox --version'].out.strip())
        self.assertIn('off the guide', by_command['signalbox note --phase implement --text-file note.txt'].out)
        self.assertIn('next: ws3 release', by_command['signalbox release app'].out)
        board = by_command['signalbox board'].out
        for line in ('auth-fix', 'search-ui', 'release', 'Resources', 'Open threads'):
            self.assertIn(line, board)
        self.assertNotIn('Traceback', ''.join(s.out + s.err for s in self.steps))

    def test_the_page_is_static_and_labelled(self):
        self.assertIn(f'ai-signalbox {__version__}', self.page)
        self.assertIn('A real run, not a mock-up.', self.page)
        self.assertIn('Generated locally, not by CI', self.page)
        self.assertIn("default-src 'self'", self.page)
        self.assertNotIn('<script', self.page.lower())
        self.assertNotIn(' style=', self.page)
        self.assertIn('name="viewport"', self.page)
        self.assertTrue((self.tmp / 'site' / 'demo' / 'demo.css').is_file())
        self.assertIn('url=demo/', (self.tmp / 'site' / 'index.html').read_text(encoding='utf-8'))

    def test_the_temp_folder_is_shown_as_demo_and_removed(self):
        self.assertIn('$DEMO', self.page)
        self.assertNotIn(tempfile.gettempdir(), self.page)
        self.assertNotIn(Path(tempfile.gettempdir()).as_posix(), self.page)
        self.assertEqual(1, len(self.workdirs))
        self.assertFalse(self.workdirs[0].exists())

    def test_the_callers_settings_are_never_touched(self):
        self.assertFalse(self.decoy.exists())

    def test_a_ci_run_links_its_run_and_commit(self):
        env = {'GITHUB_ACTIONS': 'true', 'GITHUB_RUN_ID': '42', 'GITHUB_SHA': 'abcdef1234567',
               'GITHUB_SERVER_URL': 'https://github.com', 'GITHUB_REPOSITORY': 'owner/repo'}
        with mock.patch.dict(os.environ, env):
            origin, text = self.gen._origin()
        self.assertEqual('ci', origin)
        self.assertIn('Generated by CI', text)
        self.assertIn('https://github.com/owner/repo/actions/runs/42', text)
        self.assertIn('https://github.com/owner/repo/commit/abcdef1', text)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual('local', self.gen._origin()[0])


if __name__ == '__main__':
    unittest.main()
