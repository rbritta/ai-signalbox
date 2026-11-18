"""`signalbox doctor`: what keeps the sessions from seeing each other, checked in one go; `signalbox init`: a first
config for this machine.

doctor checks: the config and its plugins load, the workspace rule names the current folder and every registered
session, the root is writable, temp files a crash left, the hooks in the host settings, psutil, the Python version.
Each line starts with `ok`, `warn` or `FAIL`; the exit code is 1 when a line FAILs.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

OK, WARN, FAIL = 'ok', 'warn', 'FAIL'


def _check(out, level, what, detail=''):
    out.append({'level': level, 'check': what, 'detail': detail})


def checks(app) -> list[dict]:
    out: list[dict] = []
    cfg = app.cfg
    _check(out, OK, 'config', str(cfg.path) if cfg.path else 'none found: the defaults (signalbox init writes one)')
    names = cfg.get('plugins') or []
    _check(out, OK, 'plugins', ', '.join(names) if names else 'none')
    ws = app.workspaces
    if not ws.pattern and not ws.globs:
        _check(out, WARN, 'workspaces', 'no rule and no glob: a session\'s workspace is the last folder of its cwd')
    here = ws.of(os.getcwd())
    _check(out, OK if here else WARN, 'this folder', f'workspace {here}' if here
           else f'{os.getcwd()} is in no workspace: check [workspaces] pattern or rules (on Windows a TOML literal '
                "string needs [\\\\/] to match a backslash)")
    found = ws.all()
    _check(out, OK if found else WARN, 'workspace roots', ', '.join(found) if found else 'none found by [workspaces] glob')
    try:
        raw = []
        for folder in getattr(app.host, 'registry_dirs', []):
            raw += [json.loads(f.read_text(encoding='utf-8')) for f in folder.glob('*.json')]
    except (OSError, ValueError):
        raw = []
    outside = sorted({r.get('cwd') for r in raw if r.get('cwd') and not ws.of(r.get('cwd'))})
    named = len(raw) - sum(1 for r in raw if r.get('cwd') in outside)
    if raw:
        _check(out, OK if not outside else WARN, 'registered sessions',
               f'{named} of {len(raw)} in a workspace' + (f"; outside: {', '.join(outside[:5])}" if outside else ''))
    else:
        _check(out, WARN, 'registered sessions', 'none in ' + ', '.join(str(d) for d in getattr(app.host, 'registry_dirs', [])))
    root = cfg.root
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / f'.doctor.{os.getpid()}.tmp'
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
        _check(out, OK, 'root', str(root))
    except OSError as err:
        _check(out, FAIL, 'root', f'{root} is not writable ({err})')
    temps = list(root.rglob('.*.tmp')) if root.exists() else []
    _check(out, OK if not temps else WARN, 'temp files', f'{len(temps)} left by a crash (signalbox prune removes '
                                                          'the old ones)' if temps else 'none')
    try:
        settings = cfg.config_dirs[0] / 'settings.json' if cfg.config_dirs else None
        events = app.host.status(settings) if settings and hasattr(app.host, 'status') else []
        _check(out, OK if events else WARN, 'hooks', f"{settings}: {', '.join(events)}" if events
               else f'no signalbox hook in {settings}: signalbox hooks install')
    except Exception as err:  # noqa: BLE001
        _check(out, WARN, 'hooks', f'could not read the host settings ({err})')
    try:
        import psutil  # noqa: F401
        _check(out, OK, 'psutil', 'installed: plugin ports and reused-pid checks work')
    except ImportError:
        _check(out, WARN, 'psutil', 'not installed (pip install psutil): builtin:ports finds no listener and a reused '
                                    'pid is not told apart; the rest works')
    v = sys.version_info
    good = (v[:2] == (3, 12) and v >= (3, 12, 7)) or (v[:2] >= (3, 13) and v[:3] != (3, 13, 0))
    _check(out, OK if good else FAIL, 'python', f'{v[0]}.{v[1]}.{v[2]}' + ('' if good else ': needs 3.12.7+ or 3.13.1+'))
    return out


def doctor(app, as_json=False) -> int:
    out = checks(app)
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        for c in out:
            print(f"{c['level']:4}  {c['check']}: {c['detail']}")
    return 1 if any(c['level'] == FAIL for c in out) else 0


# ---------------------------------------------------------------- init

def pattern_for(folder: Path) -> str:
    """The workspace rule for 'each folder under `folder` is a workspace', matching either path separator."""
    parts = [re.escape(p) for p in Path(folder).parts if p not in ('/', '\\') and not p.endswith(':\\')]
    tail = parts[-2:] if len(parts) >= 2 else parts
    sep = r'[\\/]'
    return sep + sep.join(tail) + sep + r'([^\\/]+)(?:[\\/]|$)'


def init(path=None, workspaces=None, root=None, force=False) -> dict:
    """Write a minimal config: every folder under `workspaces` (default: the parent of the current folder) is a
    workspace, the state under `root` (default: ~/.signalbox/state), no plugin."""
    from .util import Refuse
    target = Path(os.path.expanduser(str(path or os.environ.get('SIGNALBOX_CONFIG') or '~/.signalbox/config.toml')))
    if target.exists() and not force:
        raise Refuse(f'{target} exists: --force replaces it')
    folder = Path(workspaces).expanduser().resolve() if workspaces else Path.cwd().resolve().parent
    pattern = pattern_for(folder)
    if "'" in pattern:
        raise Refuse(f'a quote in {folder}: write the config by hand (examples/config.toml)')
    state = Path(os.path.expanduser(str(root or '~/.signalbox/state')))
    text = ('# Written by `signalbox init`; examples/config.toml has every section.\n'
            f"root = '{state.as_posix()}'\n"
            'plugins = []\n\n'
            '[workspaces]\n'
            f'# each folder under {folder.as_posix()} is a workspace\n'
            f"pattern = '{pattern}'\n"
            f"glob = ['{folder.as_posix()}/*']\n")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')
    return {'ok': True, 'config': str(target), 'workspaces': str(folder), 'pattern': pattern, 'root': str(state)}
