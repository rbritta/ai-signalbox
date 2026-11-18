"""Process introspection: is a pid alive (and the same process), who is its parent, what listens on a port.

`psutil` when installed (the `proc` extra); otherwise a stdlib fallback that answers liveness and the parent chain
(ctypes on Windows, /proc or `ps` elsewhere). Without psutil, start times, port owners and process listings are
unknown: checks that need them are skipped rather than guessed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

try:  # pragma: no cover - depends on the environment
    import psutil  # type: ignore
except ImportError:  # pragma: no cover
    psutil = None


class Procs:
    """The real processes of this machine. Tests replace it with a fake that answers from a table."""

    has_details = psutil is not None

    # ------------------------------------------------------------ one process
    def alive(self, pid, started=None) -> bool:
        """Alive, and when `started` is given, still the same process (its start time within 2 s): a pid reused by
        someone else does not count."""
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        if psutil is not None:
            try:
                p = psutil.Process(pid)
                return started is None or abs(p.create_time() - float(started)) < 2
            except (psutil.Error, TypeError, ValueError):
                return False
        return _fallback_alive(pid)

    def create_time(self, pid):
        if psutil is None:
            return None
        try:
            return psutil.Process(int(pid)).create_time()
        except (psutil.Error, TypeError, ValueError):
            return None

    def name(self, pid):
        if psutil is not None:
            try:
                return psutil.Process(int(pid)).name()
            except (psutil.Error, TypeError, ValueError):
                return None
        return _fallback_name(int(pid))

    def parent(self, pid):
        if psutil is not None:
            try:
                p = psutil.Process(int(pid)).parent()
                return p.pid if p else None
            except (psutil.Error, TypeError, ValueError):
                return None
        return _fallback_parent(int(pid))

    def ancestors(self, pid) -> set[int]:
        out, seen = set(), set()
        cur = self.parent(pid)
        while cur and cur not in seen and len(seen) < 64:
            seen.add(cur)
            out.add(cur)
            cur = self.parent(cur)
        return out

    def chain(self, pid):
        """The pid and its ancestors, nearest first."""
        out = [int(pid)]
        cur = self.parent(pid)
        while cur and cur not in out and len(out) < 64:
            out.append(cur)
            cur = self.parent(cur)
        return out

    def where(self, pid) -> str:
        """exe, cwd and command line of a process, joined: enough to tell which workspace it runs in."""
        if psutil is None:
            return ''
        try:
            p = psutil.Process(int(pid))
            return ' '.join([p.exe() or '', p.cwd() or ''] + p.cmdline())
        except (psutil.Error, TypeError, ValueError):
            return ''

    def cwd(self, pid):
        if psutil is None:
            return None
        try:
            return psutil.Process(int(pid)).cwd()
        except (psutil.Error, TypeError, ValueError):
            return None

    def children(self, pid) -> list[int]:
        if psutil is None:
            return []
        try:
            return [k.pid for k in psutil.Process(int(pid)).children(recursive=True)]
        except (psutil.Error, TypeError, ValueError):
            return []

    def kill(self, pid) -> bool:
        """Kill a process and its children. True when it is gone."""
        if psutil is not None:
            try:
                p = psutil.Process(int(pid))
                for kid in p.children(recursive=True):
                    try:
                        kid.kill()
                    except psutil.Error:
                        pass
                p.kill()
                return True
            except psutil.NoSuchProcess:
                return True
            except psutil.Error:
                return False
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(int(pid))], capture_output=True, timeout=30)
            else:
                import signal
                os.kill(int(pid), signal.SIGKILL)
            return True
        except (OSError, subprocess.SubprocessError, ValueError):
            return False

    # ------------------------------------------------------------ the machine
    def listeners(self, ports) -> dict[int, int]:
        """port -> pid of the process listening on it, for the ports asked."""
        found = {}
        if psutil is None:
            return found
        try:
            conns = psutil.net_connections(kind='tcp')
        except psutil.Error:
            return found
        ports = set(ports)
        for c in conns:
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port in ports and c.pid:
                found[c.laddr.port] = c.pid
        return found

    def processes(self) -> list[dict]:
        """Every process as {pid, ppid, name, exe, cmdline}: for plugins that look for their own servers."""
        if psutil is None:
            return []
        out = []
        for p in psutil.process_iter(['pid', 'ppid', 'name', 'exe', 'cmdline']):
            out.append(dict(p.info))
        return out


# ---------------------------------------------------------------- stdlib fallback

def _fallback_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


#: one process snapshot serves a command's lookups for this long (a parent chain walk asks many times)
SNAPSHOT_TTL = 2.0
_snapshot_cache: list = [0.0, None]


def _win_snapshot():
    """pid -> (ppid, exe name) for every process, taken once per `SNAPSHOT_TTL` seconds."""
    at, data = _snapshot_cache
    if data is not None and time.monotonic() - at < SNAPSHOT_TTL:
        return data
    data = _win_snapshot_now()
    _snapshot_cache[:] = [time.monotonic(), data]
    return data


def _win_snapshot_now():
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.c_void_p), ('th32ModuleID', wintypes.DWORD),
                    ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                    ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD), ('szExeFile', ctypes.c_wchar * 260)]

    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    out = {}
    if snap in (0, -1):
        return out
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return out


def _fallback_parent(pid: int):
    if os.name == 'nt':
        try:
            entry = _win_snapshot().get(pid)
            return entry[0] if entry and entry[0] else None
        except Exception:  # noqa: BLE001
            return None
    try:
        with open(f'/proc/{pid}/stat', encoding='utf-8') as fh:
            fields = fh.read().rsplit(')', 1)[1].split()
            return int(fields[1]) or None
    except (OSError, IndexError, ValueError):
        pass
    try:
        out = subprocess.run(['ps', '-o', 'ppid=', '-p', str(pid)], capture_output=True, text=True, timeout=5).stdout
        return int(out.strip()) or None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _fallback_name(pid: int):
    if os.name == 'nt':
        try:
            entry = _win_snapshot().get(pid)
            return entry[1] if entry else None
        except Exception:  # noqa: BLE001
            return None
    try:
        with open(f'/proc/{pid}/comm', encoding='utf-8') as fh:
            return fh.read().strip()
    except OSError:
        pass
    try:
        out = subprocess.run(['ps', '-o', 'comm=', '-p', str(pid)], capture_output=True, text=True, timeout=5).stdout
        return out.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def detach_flags() -> dict:
    """Popen arguments for a process that outlives the caller with no console window."""
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}


def detach(args, env=None) -> bool:
    """Run `python -m ai_signalbox <args>` in a process of its own. A hidden console on Windows, so nothing it runs
    opens a window."""
    try:
        subprocess.Popen([sys.executable, '-m', 'ai_signalbox', *args], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, env=env,
                         **detach_flags())
        return True
    except OSError:
        return False
