"""The real process layer (procs.Procs), on this test's own process and on children it starts: liveness, the parent
chain, the name, kill. Without psutil (CI installs none) it is the stdlib fallback; the answers that need psutil are
unknown, never guessed."""
import os
import subprocess
import sys
import unittest

import helpers  # noqa: F401 - puts src on the path

from ai_signalbox import procs

SLEEPER = [sys.executable, '-c', 'import time; time.sleep(60)']


class ProcsTest(unittest.TestCase):
    def setUp(self):
        self.p = procs.Procs()

    def child(self):
        proc = subprocess.Popen(SLEEPER, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait(timeout=30)))
        return proc

    def test_this_process_is_alive_and_a_finished_one_is_not(self):
        self.assertTrue(self.p.alive(os.getpid()))
        done = subprocess.Popen([sys.executable, '-c', 'pass'])
        done.wait(timeout=30)
        self.assertFalse(self.p.alive(done.pid))

    def test_a_pid_that_is_not_a_number_or_not_positive_is_not_alive(self):
        self.assertFalse(self.p.alive('not a pid'))
        self.assertFalse(self.p.alive(None))
        self.assertFalse(procs._fallback_alive(0))
        self.assertFalse(procs._fallback_alive(-5))

    def test_the_parent_chain_starts_at_the_pid(self):
        me = os.getpid()
        self.assertEqual(os.getppid(), self.p.parent(me))
        chain = self.p.chain(me)
        self.assertEqual([me, os.getppid()], chain[:2])
        self.assertIn(os.getppid(), self.p.ancestors(me))
        self.assertNotIn(me, self.p.ancestors(me))

    def test_a_child_names_this_process_as_its_parent(self):
        kid = self.child()
        self.assertEqual(os.getpid(), self.p.parent(kid.pid))
        self.assertEqual(kid.pid, self.p.chain(kid.pid)[0])

    def test_the_name_of_a_python_process(self):
        self.assertIn('python', (self.p.name(os.getpid()) or '').lower())

    def test_kill_ends_a_child(self):
        kid = self.child()
        self.assertTrue(self.p.alive(kid.pid))
        self.assertTrue(self.p.kill(kid.pid))
        kid.wait(timeout=30)
        self.assertFalse(self.p.alive(kid.pid))

    @unittest.skipIf(procs.psutil is not None, 'psutil is installed: the details are known')
    def test_without_psutil_the_details_are_unknown_not_guessed(self):
        me = os.getpid()
        self.assertFalse(procs.Procs.has_details)
        self.assertIsNone(self.p.create_time(me))
        self.assertTrue(self.p.alive(me, started=12345.0))  # no start time to compare: liveness alone
        self.assertEqual('', self.p.where(me))
        self.assertIsNone(self.p.cwd(me))
        self.assertEqual([], self.p.children(me))
        self.assertEqual({}, self.p.listeners([8765]))
        self.assertEqual([], self.p.processes())

    def test_detached_processes_get_no_console_and_their_own_group(self):
        flags = procs.detach_flags()
        if os.name == 'nt':
            self.assertTrue(flags['creationflags'] & subprocess.CREATE_NO_WINDOW)
            self.assertTrue(flags['creationflags'] & subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            self.assertEqual({'start_new_session': True}, flags)


if __name__ == '__main__':
    unittest.main()
