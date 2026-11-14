r"""Workspaces: the folders sessions run in, each with a short name (`api`, `web`, `ws2`).

`rules`: `[{pattern, name}]`, tried in order, the first match wins; `pattern` is a regex searched in the path and `name`
may use `$1` (the default). It is the same rule claude-session-relay uses to name its windows, so one rule serves both.
`pattern`: the short form of one rule whose name is group 1, e.g. `[\\/]src[\\/](ws\d+)(?:[\\/]|$)`.
`glob`: the workspace roots, so workspaces with no session still show; a root's name comes from the pattern, or is its
folder name. With neither, a session's workspace is the last folder of its cwd.
"""
from __future__ import annotations

import glob as globmod
import re
from pathlib import Path


class Workspaces:
    def __init__(self, cfg):
        ws = cfg['workspaces']
        rules = [{'pattern': ws['pattern'], 'name': '$1'}] if ws.get('pattern') else []
        rules += list(ws.get('rules') or [])
        self.rules = [(re.compile(r['pattern'], re.I), r.get('name') or '$1') for r in rules]
        self.pattern = bool(self.rules)
        self.lower = ws.get('lowercase', True)
        self.globs = list(cfg.workspace_globs)

    def _norm(self, name):
        return name.lower() if (name and self.lower) else name

    def roots(self) -> dict[str, Path]:
        out = {}
        for g in self.globs:
            for p in sorted(globmod.glob(g)):
                path = Path(p)
                if not path.is_dir():
                    continue
                name = self._from_pattern(str(path)) or self._norm(path.name)
                out.setdefault(name, path)
        return out

    def _from_pattern(self, path: str):
        for rx, name in self.rules:
            m = rx.search(str(path or ''))
            if m:
                out = re.sub(r'\$(\d)', lambda g: m.group(int(g.group(1))) or '', name)
                return self._norm(out) or None
        return None

    def of(self, path):
        """The workspace a path belongs to, or None when it is outside every workspace."""
        if not path:
            return None
        if self.pattern:
            return self._from_pattern(path)
        p = Path(str(path))
        for name, root in self.roots().items():
            try:
                p.resolve().relative_to(root.resolve())
                return name
            except (ValueError, OSError):
                continue
        if self.globs:
            return None
        return self._norm(p.name) or None

    def all(self) -> list[str]:
        return sorted(self.roots())

    def root(self, name):
        return self.roots().get(name)
