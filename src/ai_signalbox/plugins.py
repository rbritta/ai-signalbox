"""Plugins: a Python module with `register(api)`.

Listed in the config (`plugins = ["my_site.signalbox_plugin", "builtin:git"]`) and imported from `plugin_paths` or the
normal import path. `builtin:<name>` loads `ai_signalbox.builtin.<name>`. Each `Api` method adds to a plug point; see
docs/PLUGINS.md.
"""
from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class GuardRule:
    name: str
    match: Callable            # (command, unquoted, cwd) for shell; (path) for path; (url) for url -> bool
    decision: str              # 'deny' or 'person'
    reason: str
    key: str | None = None     # the release key of a 'person' rule
    on: str = 'shell'          # 'shell', 'path' or 'url'


@dataclass
class Registry:
    resources: dict = field(default_factory=dict)
    lease_checks: list = field(default_factory=list)      # (resources|None, fn(app, res, mine, current) -> str|None)
    lease_blockers: list = field(default_factory=list)    # (resources|None, fn(app, res) -> str|None)
    on_take: list = field(default_factory=list)           # fn(app, res, mine, sessions, wait_left)
    on_release: list = field(default_factory=list)        # fn(app, res, current, mine, result, item)
    note_rules: list = field(default_factory=list)        # fn(app, card, change) -> refusal str|None
    text_checks: list = field(default_factory=list)       # fn(*texts) -> warning str|None
    pr_info: list = field(default_factory=list)           # fn(n) -> dict|None
    item_from_branch: list = field(default_factory=list)  # fn(app, workspace) -> item|None
    workspace_clean: list = field(default_factory=list)   # fn(app, workspace) -> (bool, why)
    branch: list = field(default_factory=list)            # fn(app, workspace) -> str|None
    commands: dict = field(default_factory=dict)          # name -> (configure(parser), run(app, args), help)
    board_sections: list = field(default_factory=list)    # (resource|None, fn(app, sessions) -> [lines])
    peek_sections: list = field(default_factory=list)     # fn(app, session) -> [lines]
    close_steps: list = field(default_factory=list)       # (order, name, fn(app, ctx) -> [lines])
    guard_rules: list = field(default_factory=list)
    guard_pre: list = field(default_factory=list)         # fn(app, data) before the rules (never decides)
    start_lines: list = field(default_factory=list)       # fn(app, who, sessions) -> [lines]
    hook_handlers: dict = field(default_factory=dict)     # event -> [fn(app, data)]
    texts: dict = field(default_factory=dict)
    status_render: Callable | None = None                 # fn(app, card) -> str: how `note` prints the status
    store_factory: Callable | None = None
    delivery_factory: Callable | None = None
    window: object | None = None
    hosts: dict = field(default_factory=dict)
    loaded: list = field(default_factory=list)


class Api:
    """What a plugin's `register(api)` receives."""

    def __init__(self, registry: Registry, cfg):
        self.r = registry
        self.cfg = cfg

    def resource(self, name, description='', shared=False, ports=(), **extra):
        self.r.resources[name] = {**extra, 'description': description, 'shared': bool(shared), 'ports': list(ports)}

    def lease_check(self, fn, resources=None):
        self.r.lease_checks.append((set(resources) if resources else None, fn))

    def lease_blocker(self, fn, resources=None):
        self.r.lease_blockers.append((set(resources) if resources else None, fn))

    def on_take(self, fn):
        self.r.on_take.append(fn)

    def on_release(self, fn):
        self.r.on_release.append(fn)

    def note_rule(self, fn):
        self.r.note_rules.append(fn)

    def text_check(self, fn):
        self.r.text_checks.append(fn)

    def pr_info(self, fn):
        self.r.pr_info.append(fn)

    def item_from_branch(self, fn):
        self.r.item_from_branch.append(fn)

    def workspace_clean(self, fn):
        self.r.workspace_clean.append(fn)

    def branch(self, fn):
        self.r.branch.append(fn)

    def command(self, name, run, configure=None, help=''):  # noqa: A002
        self.r.commands[name] = (configure, run, help)

    def board_section(self, fn, resource=None):
        self.r.board_sections.append((resource, fn))

    def peek_section(self, fn):
        self.r.peek_sections.append(fn)

    def close_step(self, name, fn, order=50):
        self.r.close_steps.append((order, name, fn))
        self.r.close_steps.sort(key=lambda s: s[0])

    def guard_rule(self, name, match, decision, reason, key=None, on='shell'):
        if decision not in ('deny', 'person'):
            raise ValueError('decision is deny or person')
        if decision == 'person' and not key:
            raise ValueError('a person rule needs its release key')
        self.r.guard_rules.append(GuardRule(name, match, decision, reason, key, on))

    def guard_pre(self, fn):
        self.r.guard_pre.append(fn)

    def start_lines(self, fn):
        self.r.start_lines.append(fn)

    def on_hook(self, event, fn):
        self.r.hook_handlers.setdefault(event, []).append(fn)

    def text(self, key, template):
        self.r.texts[key] = template

    def status_render(self, fn):
        self.r.status_render = fn

    def store(self, factory):
        self.r.store_factory = factory

    def delivery(self, factory):
        self.r.delivery_factory = factory

    def window(self, controller):
        self.r.window = controller

    def host(self, kind, factory):
        self.r.hosts[kind] = factory


def load(cfg, registry: Registry) -> None:
    for p in cfg.plugin_paths:
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    api = Api(registry, cfg)
    for name in cfg.get('plugins') or []:
        module_name = f"ai_signalbox.builtin.{name.split(':', 1)[1]}" if name.startswith('builtin:') else name
        module = importlib.import_module(module_name)
        register = getattr(module, 'register', None)
        if not callable(register):
            raise ValueError(f'plugin {name} has no register(api)')
        register(api)
        registry.loaded.append(name)
