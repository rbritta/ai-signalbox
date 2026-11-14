"""The app object every command and hook works through: config, plugins, store, events, host, delivery."""
from __future__ import annotations

import os

from . import plugins as plugins_mod
from .config import Config
from .events import EventLog
from .procs import Procs, detach
from .store import FileStore
from .texts import DEFAULT_TEXTS, render
from .workspaces import Workspaces


class App:
    def __init__(self, cfg: Config | None = None, *, procs=None, host=None, store=None, load_plugins=True):
        self.cfg = cfg or Config.load()
        self.procs = procs or Procs()
        self.workspaces = Workspaces(self.cfg)
        self.registry = plugins_mod.Registry()
        if load_plugins:
            plugins_mod.load(self.cfg, self.registry)
        if store is not None:
            self.store = store
        elif self.registry.store_factory:
            self.store = self.registry.store_factory(self)
        else:
            self.store = FileStore(self.cfg.root, self.cfg['events_max_bytes'])
        self.events = EventLog(self.store)
        if host is not None:
            self.host = host
        else:
            from .hosts import make_host
            self.host = make_host(self)
        if self.registry.delivery_factory:
            self.delivery = self.registry.delivery_factory(self)
        else:
            from .delivery.filebox import Filebox
            self.delivery = Filebox(self)

    # ------------------------------------------------------------ helpers
    def emit(self, kind, who=None, text='', src='cli', ts=None, **fields):
        self.events.emit(kind, who, text, src, ts, **fields)

    def text(self, key: str, **values) -> str:
        template = self.cfg['texts'].get(key)
        if template is None:
            template = self.registry.texts.get(key, DEFAULT_TEXTS.get(key, ''))
        return render(template, **values)

    def refuse(self, key: str, *, tail: str = '', **values):
        """A Refuse with the text of `key` (and `tail` after it), on stdout when the key is in `cli.stdout_refusals`."""
        from .util import Refuse
        return Refuse(self.text(key, **values) + tail, stdout=key in (self.cfg.get('cli.stdout_refusals') or ()))

    def resources(self) -> dict:
        """Config resources over plugin ones: name -> {description, shared, ports, ...any other key of the spec}."""
        out = {k: dict(v) for k, v in self.registry.resources.items()}
        for name, spec in (self.cfg['resources'] or {}).items():
            out[name] = {**spec, 'description': spec.get('description', ''), 'shared': bool(spec.get('shared')),
                         'ports': [int(p) for p in spec.get('ports') or []]}
        return out

    def ports(self) -> dict[int, str]:
        return {p: res for res, spec in self.resources().items() for p in spec['ports']}

    def live_sessions(self):
        return self.host.live_sessions()

    def me(self, sessions=None, sid=None):
        return self.host.me(sessions, sid)

    def who_of(self, sid=None):
        """The calling session: by id when given (registry only), else by the process tree."""
        if sid:
            s = next((r for r in self.host.registry() if r['sid'] == sid), None)
            if not s:
                raise self.refuse('cli.not_registered', sid=sid)
            return {'workspace': s['workspace'], 'name': s.get('name'), 'sid': sid}
        mine = self.me()
        if not mine:
            raise self.refuse('cli.no_session_here')
        return mine

    def detach(self, *args) -> bool:
        env = dict(os.environ)
        env['SIGNALBOX_ROOT'] = str(self.cfg.root)
        if self.cfg.path:
            env['SIGNALBOX_CONFIG'] = str(self.cfg.path)
        return detach(list(args), env)

    def log_line(self, text: str):
        self.store.log_append(text)

    def item_of(self, workspace):
        for fn in self.registry.item_from_branch:
            try:
                item = fn(self, workspace)
            except Exception:  # noqa: BLE001
                item = None
            if item:
                return item
        return None

    def branch(self, workspace):
        for fn in self.registry.branch:
            try:
                b = fn(self, workspace)
            except Exception:  # noqa: BLE001
                b = None
            if b:
                return b
        return None
