"""Test helpers: a temp state root, a fake host registry and a fake process table. Nothing here touches the real home,
the real host registry, the network or a model."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from ai_signalbox import util  # noqa: E402
from ai_signalbox.app import App  # noqa: E402
from ai_signalbox.config import Config  # noqa: E402

for _var in ('SIGNALBOX_ROOT', 'SIGNALBOX_CONFIG', 'CLAUDE_CONFIG_DIR'):
    os.environ.pop(_var, None)

PATTERN = r'[\\/]work[\\/](ws\d+)(?:[\\/]|$)'


class FakeProcs:
    """A process table: pid -> {name, ppid, started, where, cwd}. Ports listen when put in `ports`."""

    has_details = True

    def __init__(self):
        self.table: dict[int, dict] = {}
        self.ports: dict[int, int] = {}
        self.killed: list[int] = []

    def add(self, pid, name='python', ppid=None, started=1000.0, where='', cwd=None):
        self.table[int(pid)] = {'name': name, 'ppid': ppid, 'started': started, 'where': where, 'cwd': cwd}
        return int(pid)

    def alive(self, pid, started=None):
        try:
            p = self.table.get(int(pid))
        except (TypeError, ValueError):
            return False
        return bool(p) and (started is None or abs(p['started'] - float(started)) < 2)

    def create_time(self, pid):
        p = self.table.get(int(pid))
        return p['started'] if p else None

    def name(self, pid):
        p = self.table.get(int(pid)) if pid is not None else None
        return p['name'] if p else None

    def parent(self, pid):
        p = self.table.get(int(pid))
        return p['ppid'] if p else None

    def ancestors(self, pid):
        out, cur = set(), self.parent(pid)
        while cur and cur not in out:
            out.add(cur)
            cur = self.parent(cur)
        return out

    def chain(self, pid):
        out = [int(pid)]
        cur = self.parent(pid)
        while cur and cur not in out:
            out.append(cur)
            cur = self.parent(cur)
        return out

    def where(self, pid):
        p = self.table.get(int(pid))
        return p['where'] if p else ''

    def cwd(self, pid):
        p = self.table.get(int(pid))
        return p['cwd'] if p else None

    def children(self, pid):
        out, todo = [], [int(pid)]
        while todo:
            cur = todo.pop()
            for k, v in self.table.items():
                if v['ppid'] == cur and k not in out:
                    out.append(k)
                    todo.append(k)
        return out

    def kill(self, pid):
        for k in self.children(pid) + [int(pid)]:
            self.table.pop(k, None)
            self.killed.append(k)
        return True

    def listeners(self, ports):
        return {p: pid for p, pid in self.ports.items() if p in set(ports)}

    def processes(self):
        return [{'pid': k, 'ppid': v['ppid'], 'name': v['name'], 'exe': '', 'cmdline': v['where'].split()}
                for k, v in self.table.items()]


class Case(unittest.TestCase):
    """A temp world per test: state root, host config dir, workspaces ws1..ws3, a fake process table."""

    extra_toml = ''

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='signalbox-test-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for ws in ('ws1', 'ws2', 'ws3'):
            (self.tmp / 'work' / ws / 'app').mkdir(parents=True)
        self.procs = FakeProcs()
        self.app = self.make_app(self.extra_toml)
        self._pid = 50000
        self._real_clock = util.clock
        self.addCleanup(setattr, util, 'clock', self._real_clock)

    def toml(self, extra=''):
        root = (self.tmp / 'state').as_posix()
        host = (self.tmp / 'host').as_posix()
        work = (self.tmp / 'work').as_posix()
        return (f"root = '{root}'\nplugins = []\n[workspaces]\npattern = '{PATTERN}'\nglob = ['{work}/ws*']\n"
                f"[host]\nconfig_dirs = ['{host}']\n" + extra)

    def make_app(self, extra='', **kw):
        cfg = Config.from_toml(self.toml(extra), self.tmp)
        return App(cfg, procs=self.procs, **kw)

    def session(self, ws='ws1', name=None, sid=None, status='idle', started=None, cwd=None):
        """Register a live session (a fake `claude` process) and return its dict."""
        self._pid += 1
        pid = self._pid
        sid = sid or f'sid-{ws}-{pid}'
        name = name or f'{ws}-s{pid % 100}'
        cwd = cwd or str(self.tmp / 'work' / ws / 'app')
        self.procs.add(pid, 'claude', ppid=1, started=float(pid), cwd=cwd)
        folder = self.tmp / 'host' / 'sessions'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'{pid}.json').write_text(json.dumps({
            'pid': pid, 'sessionId': sid, 'cwd': cwd, 'name': name, 'status': status,
            'startedAt': started or pid, 'statusUpdatedAt': pid}), encoding='utf-8')
        return {'sid': sid, 'workspace': ws, 'name': name, 'pid': pid}

    def act_as(self, s):
        """Make this test process a child of session `s`, as a command run by that session is."""
        self.procs.add(os.getpid(), 'python', ppid=s['pid'] if s else None)

    def end(self, s):
        self.procs.table.pop(s['pid'], None)

    def script(self, name='python', ppid=None, started=None):
        """A script process (a lease holder by pid)."""
        self._pid += 1
        return self.procs.add(self._pid, name, ppid=ppid, started=started or float(self._pid))

    def tick(self, seconds=1, start=None):
        """A fake clock that moves `seconds` each time it is read."""
        import datetime as dt
        state = [start or dt.datetime.now().replace(microsecond=0, hour=10, minute=0, second=0)]

        def clock():
            state[0] += dt.timedelta(seconds=seconds)
            return state[0]
        util.clock = clock
        return state

    def freeze(self, when=None):
        import datetime as dt
        state = [when or dt.datetime.now().replace(microsecond=0, hour=10, minute=0, second=0)]
        util.clock = lambda: state[0]
        return state

    def events(self, kind=None, sid=None):
        evs = self.app.events.all()
        return [e for e in evs if (kind is None or e['kind'] == kind) and (sid is None or e.get('sid') == sid)]

    def card(self, s):
        return self.app.store.get_status(s['sid'])

    def cli(self, *argv, stdin=''):
        """(exit code, stdout, stderr) of a CLI call against this test's app."""
        from ai_signalbox import cli
        out, err = io.StringIO(), io.StringIO()
        real_stdin = sys.stdin
        sys.stdin = io.TextIOWrapper(io.BytesIO(stdin.encode('utf-8')), encoding='utf-8')
        code = 0
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = cli.main(list(argv), app=self.app)
                except SystemExit as e:
                    code = e.code if isinstance(e.code, int) else 1
        finally:
            sys.stdin = real_stdin
        return code or 0, out.getvalue(), err.getvalue()

    def write(self, name, text):
        p = self.tmp / name
        p.write_text(text, encoding='utf-8')
        return str(p)
