"""The guard: a PreToolUse policy engine.

Rules come from plugins (`api.guard_rule`): a matcher over a shell command (quoted text ignored, scripts handed to a
shell with `-c` parsed), over a file path, or over a URL, and a decision:

- `deny`: refused, with the reason; the session reads it and does it another way;
- `person`: the person's call. Refused with how to ask, never with a dialog (a permission dialog freezes the session
  mid-turn and nothing else gets through while it waits). The session opens an ask tagged with the rule's release key
  (`[release <key>] what and why`); the person's yes to it, given in the app (a reply to that ask, delivered) or as
  their next prompt in the IDE, lets the same rule pass for `release_minutes`.

One rule is built in: free text for signalbox never rides in the shell (a quoted argument, a pipe, a heredoc), because
the host's own safety check can open a dialog even in bypass mode. The text goes in a file: `--text-file <file>`.

The hook fails open: anything unexpected is no decision, so a broken guard never blocks a session.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time

QUOTED = re.compile(r'''"[^"]*"|'[^']*\'''')
SHELL_SCRIPT = re.compile(r'''\b(?:bash|sh|zsh|pwsh|powershell)(?:\.exe)?\b[^"'\n;&|]*?\s-(?:\w*c|Command)\s+(["'])(.*?)\1''',
                          re.I)
EVENTS_TAIL = 512 * 1024
SHELL_TOOLS = ('Bash', 'PowerShell')
PATH_TOOLS = ('Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'Read')
URL_TOOLS = ('WebFetch',)


def executed(command: str) -> str:
    """The command without its quoted text (`grep "git add"` only reads), plus each script it hands to a shell, which
    runs (`bash -c "docker compose up"`). A shell named inside quoted text does not run."""
    quoted = [m.span() for m in QUOTED.finditer(command)]
    scripts = [m.group(2) for m in SHELL_SCRIPT.finditer(command)
               if not any(start <= m.start() < end for start, end in quoted)]
    return '\n'.join(QUOTED.sub('', text) for text in [command] + scripts)


class Guard:
    def __init__(self, app):
        self.app = app
        g = app.cfg['guard']
        self.minutes = int(g.get('release_minutes') or 60)
        self.tag = g.get('tag') or '[release {key}]'
        self.auth_type = g.get('authorization_type') or 'authorization'
        self.yes = re.compile(g.get('yes') or r'^\s*(ok|yes)\b', re.I)
        self.but = re.compile(g.get('but') or r'\b(no|wait)\b', re.I)
        self.read_only = re.compile(g.get('read_only') or r'^\s*(grep|cat)\b', re.I)
        names = '|'.join(re.escape(n) for n in (g.get('cli_names') or ['signalbox']))
        cmds = '|'.join(re.escape(c) for c in (g.get('text_commands') or []))
        self.cli_text_cmd = re.compile(rf'''(?:\b(?:{names})\b|-m\s+ai_signalbox)["']?\s+(?:{cmds})\b''') if cmds else None
        self.text_flag = g.get('text_flag') or '--text-file'

    def tag_of(self, key) -> str:
        return self.tag.format(key=key)

    # ------------------------------------------------------------ the built-in rule
    def cli_text_in_shell(self, command: str) -> bool:
        if not self.cli_text_cmd or not self.cli_text_cmd.search(command) or self.read_only.match(command):
            return False
        flag = re.escape(self.text_flag)
        if self.text_flag in command and not QUOTED.search(re.sub(rf'{flag}\s+("[^"]*"|\'[^\']*\'|\S+)', '', command)):
            return False
        return bool(QUOTED.search(command) or '<<' in command or '|' in command or re.search(r'\s-\s*$', command))

    # ------------------------------------------------------------ releases
    def released(self, sid, key) -> bool:
        if not sid:
            return False
        cutoff = time.time() - self.minutes * 60
        return self.released_by_box(sid, key, cutoff) or self.released_in_ide(sid, key, cutoff)

    def released_in_ide(self, sid, key, cutoff) -> bool:
        """The person's first prompt in this session after the tagged ask opened: a yes with no "but wait" in it."""
        asked = False
        tag = self.tag_of(key)
        for event in self.app.store.tail_events(EVENTS_TAIL):
            if event.get('sid') != sid:
                continue
            if event.get('kind') == 'ask' and event.get('src') == 'cli' and event.get('action') == 'open' \
                    and tag in (event.get('text') or ''):
                asked = True
            elif asked and event.get('kind') == 'prompt' and event.get('src') == 'hook':
                text = event.get('text') or ''
                try:
                    when = dt.datetime.fromisoformat(event.get('ts')).timestamp()
                except (TypeError, ValueError):
                    when = 0
                if when >= cutoff and self.yes.match(text) and not self.but.search(text):
                    return True
                asked = False  # they answered something else: the ask waits for a new yes
        return False

    def released_by_box(self, sid, key, cutoff) -> bool:
        """The person's yes, delivered to this session as the answer to an authorization ask tagged with the key."""
        tag = self.tag_of(key)
        for m in self.app.store.delivered(cutoff):
            reply = (m.get('text') or '').strip().splitlines()
            if (m.get('ask_type') == self.auth_type and sid in (m.get('sid'), m.get('to_sid'))
                    and tag in (m.get('ask_text') or '') and reply and self.yes.match(reply[-1])):
                return True
        return False

    def ask_text(self, reason, key) -> str:
        return self.app.text('guard.ask', reason=reason, tag=self.tag_of(key), type=self.auth_type,
                             minutes=self.minutes)

    # ------------------------------------------------------------ deciding
    def decide(self, data):
        """(decision, reason) or None. Only ever `deny`."""
        sid = data.get('session_id')
        tool = data.get('tool_name') or ''
        args = data.get('tool_input') or {}
        for fn in self.app.registry.guard_pre:
            try:
                fn(self.app, data)
            except Exception:  # noqa: BLE001
                pass
        if tool in SHELL_TOOLS or (not tool and args.get('command')):
            command = args.get('command') or ''
            if self.cli_text_in_shell(command):
                return 'deny', self.app.text('guard.cli_text')
            unquoted = executed(command)
            subject, on = (command, unquoted, data.get('cwd')), 'shell'
        elif tool in PATH_TOOLS:
            subject, on = (args.get('file_path') or args.get('notebook_path') or '',), 'path'
        elif tool in URL_TOOLS:
            subject, on = (args.get('url') or '',), 'url'
        else:
            return None
        for rule in self.app.registry.guard_rules:
            if rule.on != on:
                continue
            try:
                hit = rule.match(*subject)
            except Exception:  # noqa: BLE001
                hit = False
            if not hit:
                continue
            reason = hit if isinstance(hit, str) else rule.reason
            if rule.decision == 'deny':
                return 'deny', reason
            if self.released(sid, rule.key):
                return None
            return 'deny', self.ask_text(reason, rule.key)
        return None


def hook(app, data) -> int:
    try:
        decision = Guard(app).decide(data)
    except Exception:  # noqa: BLE001
        decision = None
    if decision:
        print(json.dumps(app.host.tool_decision(*decision)))  # ASCII: a Windows console may not be UTF-8
    return 0
