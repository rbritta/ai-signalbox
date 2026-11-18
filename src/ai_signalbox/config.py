"""Configuration: a TOML file merged over the defaults below.

Lookup: the path given (`--config`), else `$SIGNALBOX_CONFIG`, else `~/.signalbox/config.toml` when it exists, else the
defaults alone. `$SIGNALBOX_ROOT` overrides `root`. Relative paths in the file are relative to the file's folder.
"""
from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

DEFAULTS: dict = {
    'root': '~/.signalbox',
    'events_max_bytes': 5_000_000,
    'workspaces': {
        'pattern': None,        # regex searched in a cwd; group 1 is the workspace name
        'rules': [],            # [{pattern, name}], tried in order, first match wins; name may use $1 (default "$1")
        'glob': [],             # workspace roots, to list workspaces with no session (names from the pattern or basename)
        'lowercase': True,
    },
    'host': {
        'type': 'claude_code',
        'config_dirs': ['~/.claude'],   # each holds sessions/ (the registry) and projects/ (the transcripts)
        'process_name': 'claude',       # a registered session lives while its pid's process name contains this
    },
    'person': {
        'source': 'person',             # how a delivered message names who sent it
        'waiting_words': ['person'],    # a status `waiting` containing one of these waits for the person
    },
    'resources': {},                    # name -> {description, shared (bool), ports [int]}
    'workflow': {
        'guide': None,                  # a markdown file holding the flow block
        'fence': 'flow',                # ```flow
        'changes_heading': 'Changes',   # the "## Changes" table: version | date | what changed | asked by
        'keywords': {},                 # aliases for the DSL keywords, e.g. {"step": "stage"}
        'before_pattern': r'before (?:the )?([\w-]+)',
        'neutral': ['free', 'infra'],   # phases with no item path: never checked, never refused
        'variant': [],                  # a side track outside the graph: never checked
        'task_ok': 'ok',                # the person's ok on a task; never the session's phase
        'renamed': {},                  # old phase -> new phase
        'renamed_in': {},               # old phase -> guide version that renamed it
        'keep_target': ['blocked', 'resume'],
        'resume_phase': 'resume',
        'resume_next': 'verify',
        'fallback_phases': [],          # the vocabulary when the guide is missing
        'untracked_items': [],          # items shared by many unrelated jobs: their history is not one path
    },
    'status': {
        'only_on_transition': True,     # refuse a note that only changes the text of the same phase
        'task_pattern': r'[\w.-]{1,40}',
        'target_pattern': r'[\w.][\w./-]*',
        'rereview': {},                 # {"phase": "review", "needs": ["implement", "verify"]}: a new review needs one
        'pr_pattern': r'\d{1,9}',
        'item_aliases': {},             # alias (lower case) -> item, e.g. {"analysis": "analysis"}
        'items_need_text': [],          # items whose first note must carry a text (it titles the item)
        'priority_hint': True,          # waiting for the person while holding a lease others wait for: say who is ahead
    },
    'asks': {
        'types': {'review': 'review', 'question': 'question', 'decision': 'decision',
                  'authorization': 'authorization', 'block': 'block', 'alert': 'alert', 'prompt': 'prompt'},
        'red': ['authorization', 'block', 'alert'],
        'default': 'question',
    },
    'jobs': {
        'types': ['download', 'export', 'import', 'build', 'test', 'deploy'],
        'synonyms': {'tests': 'test', 'testing': 'test'},
        'done_expected': ['build', 'test'],     # closing one of these without a done line warns
        'label_pattern': r'\s*([\w.-]+)\s+(?:ok|failed)\s*:',
        'foreign_labels': None,         # regex of labels that always name a step of their own
        'toggle_states': {},            # switch -> the states it takes, e.g. {"email": ["on", "off"]}; empty: any
    },
    'priority': {
        'wait_minutes': 10,
        'exclude': [],                  # workspaces never in the order
        'delivered_phases': [],         # status phases that end a workspace's turn
        'home': {},                     # workspace -> home number; default: the number its name ends with
        'person_by': None,              # how an order set by the person's app is signed; default: person.source
        'by_signalbox': 'signalbox',    # how an order signalbox set by itself (a delivered workspace) is signed
    },
    'delivery': {
        'type': 'filebox',
        'vigil_step': 2.0,
        'vigil_max': 84600.0,           # under the hook's 86400 s timeout
        'start_touches': [4, 12, 30],
    },
    'leases': {'poll_seconds': 5},
    'guard': {
        'text_flag': '--text-file',
        'release_minutes': 60,
        'tag': '[release {key}]',
        'authorization_type': 'authorization',
        'yes': r'^\s*(ok|yes|go|approved|allowed)\b',
        'but': r'\b(no|not|wait|stop|hold)\b',
        'cli_names': ['signalbox'],
        'text_commands': ['ask', 'thread', 'post', 'send', 'request', 'close', 'offer'],
        'read_only': r'^\s*(grep|rg|cat|sed|head|tail|less|Select-String|Get-Content|findstr)\b',
    },
    'close': {
        'delay': 30,
        'phase': 'free',
        'repos': ['.'],                 # relative to the workspace root
        'main_branch': 'main',
        'remote': 'origin',
    },
    'events': {
        'skill_phase': {},              # skill name -> phase it implies
        'agent_phase': {},
        'prompt_kind': 'prompt',        # the kind of a prompt the person typed in a session
    },
    'watch': {'pump_resource': None},
    'git': {
        'item_pattern': r'-(\d+)(?:-|$)',  # the item a branch name carries
        'merged_grep': None,            # a log --grep that finds the merge of an item: "^Merge .*#{item}\b"
    },
    'relay': {'root': None, 'timeout': 20},  # claude-session-relay's inbox root (default: its own default)
    'serve': {'host': '127.0.0.1', 'port': 8765, 'token': None, 'allow_origin': None},
    'texts': {},
    'cli': {'stdout_refusals': []},     # text keys whose refusal prints on stdout (a state, not an error)
    'caps': {'text': 12000, 'result': 2000},
    'plugins': [],
    'plugin_paths': [],
}

PATH_KEYS = (('root',), ('workflow', 'guide'))
_WHOLE = ('texts', 'resources', 'renamed', 'renamed_in', 'keywords', 'synonyms', 'types', 'skill_phase',
          'agent_phase', 'item_aliases', 'home')


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in _WHOLE:
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _expand(value, base: Path | None):
    if value is None:
        return None
    p = Path(os.path.expandvars(os.path.expanduser(str(value))))
    if not p.is_absolute() and base is not None:
        p = base / p
    return p


class Config:
    """The merged settings, read with `cfg['section']['key']` or `cfg.get('section.key', default)`."""

    def __init__(self, data: dict | None = None, base: Path | None = None, path: Path | None = None):
        self.data = _merge(DEFAULTS, data or {})
        self.base = base
        self.path = path
        if os.environ.get('SIGNALBOX_ROOT'):
            self.data['root'] = os.environ['SIGNALBOX_ROOT']
        self.root = _expand(self.data['root'], base)
        guide = self.data['workflow'].get('guide')
        self.guide = _expand(guide, base) if guide else None
        self.config_dirs = [_expand(d, base) for d in self.data['host']['config_dirs']]
        if os.environ.get('CLAUDE_CONFIG_DIR'):
            extra = _expand(os.environ['CLAUDE_CONFIG_DIR'], None)
            if extra not in self.config_dirs:
                self.config_dirs.append(extra)
        globs = self.data['workspaces'].get('glob') or []
        self.workspace_globs = [str(_expand(g, base)) for g in ([globs] if isinstance(globs, str) else globs)]
        self.plugin_paths = [_expand(p, base) for p in self.data.get('plugin_paths') or []]

    def __getitem__(self, key):
        return self.data[key]

    def get(self, dotted: str, default=None):
        cur = self.data
        for part in dotted.split('.'):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    @classmethod
    def load(cls, path=None) -> 'Config':
        candidates = [path, os.environ.get('SIGNALBOX_CONFIG')]
        default = Path(os.path.expanduser('~/.signalbox/config.toml'))
        for c in candidates:
            if c:
                p = Path(os.path.expanduser(str(c)))
                with open(p, 'rb') as fh:
                    return cls(tomllib.load(fh), p.parent, p)
        if default.is_file():
            with open(default, 'rb') as fh:
                return cls(tomllib.load(fh), default.parent, default)
        return cls({})

    @classmethod
    def from_toml(cls, text: str, base: Path | None = None) -> 'Config':
        return cls(tomllib.loads(text), base)
