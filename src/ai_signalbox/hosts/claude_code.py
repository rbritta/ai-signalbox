"""Claude Code: the session registry, transcripts, hook input and output, and the settings installer.

Registry: `<config dir>/sessions/*.json`, one file per session with `pid`, `sessionId`, `cwd`, `name`, `status`,
`startedAt`, `statusUpdatedAt`, `messagingSocketPath`. A session lives while its pid's process name contains the
configured `process_name`. Transcripts: `<config dir>/projects/<cwd with : \\ / as ->/<sessionId>.jsonl`.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from ..util import first_line, local_hhmm, read_json

TAG = '#signalbox'


def tail_lines(path, size):
    with open(path, 'rb') as fh:
        fh.seek(0, 2)
        end = fh.tell()
        fh.seek(max(0, end - size))
        lines = fh.read().split(b'\n')
    return [l.decode('utf-8', 'replace') for l in (lines[1:] if end > size else lines) if l.strip()]


class ClaudeCodeHost:
    name = 'claude_code'

    def __init__(self, app):
        self.app = app
        self.dirs = list(app.cfg.config_dirs)
        self.proc_name = (app.cfg['host'].get('process_name') or 'claude').lower()
        self.pipes: dict[str, str] = {}

    # ------------------------------------------------------------ registry
    @property
    def registry_dirs(self):
        return [d / 'sessions' for d in self.dirs]

    @property
    def projects_dirs(self):
        return [d / 'projects' for d in self.dirs]

    def _session(self, raw):
        if not raw or not raw.get('sessionId'):
            return None
        ws = self.app.workspaces.of(raw.get('cwd'))
        if not ws:
            return None
        return {'sid': raw['sessionId'], 'workspace': ws, 'name': raw.get('name'), 'pid': raw.get('pid'),
                'cwd': raw.get('cwd'), 'status': raw.get('status'), 'started_at': raw.get('startedAt'),
                'status_updated_at': raw.get('statusUpdatedAt'), 'socket': raw.get('messagingSocketPath')}

    def registry(self) -> list[dict]:
        """Every registered session inside a workspace, alive or not. No process scan: hooks must be quick."""
        out = []
        for folder in self.registry_dirs:
            for f in folder.glob('*.json'):
                s = self._session(read_json(f))
                if s:
                    out.append(s)
        return out

    def is_alive(self, s) -> bool:
        name = self.app.procs.name(s.get('pid')) if s.get('pid') else None
        return bool(name) and self.proc_name in name.lower()

    def live_sessions(self) -> list[dict]:
        out = []
        for s in self.registry():
            if not self.is_alive(s):
                continue
            s['card'] = self.app.store.get_status(s['sid'])
            if s.get('socket'):
                self.pipes['uds:' + s['socket']] = f"{s['workspace']} {s['name']}"
            out.append(s)
        return sorted(out, key=lambda s: (s['workspace'], -(s.get('started_at') or 0)))

    def session_alive(self, sid) -> bool:
        s = next((r for r in self.registry() if r['sid'] == sid), None)
        return bool(s) and self.is_alive(s)

    def me(self, sessions=None, sid=None, pid=None):
        """The calling session: by id; else the session whose pid is up this process's tree; else the busiest, most
        recent session of the cwd's workspace."""
        sessions = sessions if sessions is not None else self.live_sessions()
        if sid:
            return next((s for s in sessions if s['sid'] == sid), None)
        by_pid = {}
        for s in sessions:
            try:
                by_pid[int(s['pid'])] = s
            except (TypeError, ValueError, KeyError):
                pass
        for p in self.app.procs.chain(pid or os.getpid()):
            if p in by_pid:
                return by_pid[p]
        ws = self.app.workspaces.of(os.getcwd())
        same = [s for s in sessions if s['workspace'] == ws]
        return max(same, key=lambda s: (s.get('status') == 'busy', s.get('status_updated_at') or 0), default=None)

    def transcript(self, s):
        if not s or not s.get('cwd'):
            return None
        slug = re.sub(r'[:\\/]', '-', s['cwd'])
        for folder in self.projects_dirs:
            for candidate in (folder / slug, folder / slug.lower()):
                f = candidate / f"{s['sid']}.jsonl"
                if f.exists():
                    return f
        return None

    # ------------------------------------------------------------ hook input and output
    @staticmethod
    def read_input(stream=None) -> dict:
        try:
            raw = (stream or sys.stdin.buffer).read()
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8', 'replace')
            return json.loads(raw or '{}')
        except ValueError:
            return {}

    def hook_who(self, data) -> dict:
        sid = data.get('session_id')
        s = next((r for r in self.registry() if r['sid'] == sid), None)
        if s:
            return {'workspace': s['workspace'], 'name': s.get('name'), 'sid': sid}
        return {'workspace': self.app.workspaces.of(data.get('cwd') or os.getcwd()), 'name': None, 'sid': sid}

    @staticmethod
    def is_subagent(data) -> bool:
        return bool(data.get('agent_id'))

    @staticmethod
    def start_output(context: str, watch_paths=None) -> dict:
        out = {'hookEventName': 'SessionStart', 'additionalContext': context}
        if watch_paths:
            out['watchPaths'] = [Path(p).as_posix() for p in watch_paths]
        return {'hookSpecificOutput': out}

    @staticmethod
    def tool_decision(decision: str, reason: str) -> dict:
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': decision,
                                       'permissionDecisionReason': reason}}

    WAKE_EXIT = 2

    # ------------------------------------------------------------ transcripts
    def transcript_events(self, s, size=4_000_000) -> list[tuple]:
        """(utc iso, kind, text, phase) from a session's transcript: skills and slash commands, agents, the person's
        prompts, messages sent to and received from other sessions. Needs nothing from the session itself."""
        path = self.transcript(s)
        if not path:
            return []
        skill_phase = self.app.cfg['events'].get('skill_phase') or {}
        agent_phase = self.app.cfg['events'].get('agent_phase') or {}
        prompt_kind = self.app.cfg['events'].get('prompt_kind') or 'prompt'
        try:
            lines = tail_lines(path, size)
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            ts = entry.get('timestamp') or ''
            content = (entry.get('message') or {}).get('content')
            if entry.get('type') == 'assistant' and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get('type') != 'tool_use':
                        continue
                    name, args = block.get('name'), block.get('input') or {}
                    if name == 'Skill':
                        skill = args.get('skill') or ''
                        out.append((ts, 'skill', skill, skill_phase.get(skill.split(':')[-1])))
                    elif name in ('Agent', 'Task') and args.get('subagent_type') in agent_phase:
                        out.append((ts, 'agent', args['subagent_type'], agent_phase[args['subagent_type']]))
                    elif name == 'SendMessage' and args.get('message'):
                        to = args.get('to') or '?'
                        if re.fullmatch(r'a[0-9a-f]{16}', to):
                            continue  # a subagent of the session itself
                        out.append((ts, 'sent', f"-> {self.pipes.get(to, to)}: {first_line(args['message'])}", None))
            elif entry.get('type') == 'user' and not entry.get('isMeta') and not entry.get('isCompactSummary'):
                texts = [content] if isinstance(content, str) else [
                    b.get('text', '') for b in content or [] if isinstance(b, dict) and b.get('type') == 'text']
                for text in texts:
                    text = (text or '').strip()
                    command = re.search(r'<command-name>/?([\w:-]+)</command-name>', text)
                    if command:
                        out.append((ts, 'command', command.group(1), skill_phase.get(command.group(1).split(':')[-1])))
                    elif text.startswith('<cross-session-message'):
                        sender = re.search(r'from-name="([^"]*)"', text)
                        body = re.sub(r'<[^>]+>', '', text)
                        out.append((ts, 'received', f"<- {sender.group(1) if sender else '?'}: {first_line(body)}", None))
                    elif text and not text.startswith('<') and not text.startswith('This session is being continued'):
                        out.append((ts, prompt_kind, first_line(text), None))
        return out

    @staticmethod
    def last_words(path, n=4, width=700):
        """The last n assistant texts and the person's last prompt, (local time, text)."""
        texts, prompt = [], None
        try:
            with open(path, encoding='utf-8', errors='replace') as fh:
                lines = fh.readlines()[-3000:]
        except OSError:
            return [], None
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            msg = entry.get('message') or {}
            content = msg.get('content')
            if entry.get('type') == 'assistant' and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'text' and block.get('text', '').strip():
                        texts.append((entry.get('timestamp', ''), block['text'].strip()))
            elif entry.get('type') == 'user' and not entry.get('isMeta') and not entry.get('isCompactSummary'):
                if isinstance(content, str) and content.strip() and not content.startswith('<'):
                    prompt = (entry.get('timestamp', ''), content.strip())
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get('type') == 'text' \
                                and not block.get('text', '').startswith('<'):
                            prompt = (entry.get('timestamp', ''), block['text'].strip())

        def clip(t):
            return t if len(t) <= width else t[:width] + ' [...]'
        return [(local_hhmm(ts), clip(t)) for ts, t in texts[-n:]], \
            ((local_hhmm(prompt[0]), clip(prompt[1])) if prompt else None)

    @staticmethod
    def last_assistant_text(path) -> str:
        try:
            lines = tail_lines(path, 400_000)
        except OSError:
            return ''
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            content = (entry.get('message') or {}).get('content')
            if entry.get('type') == 'assistant' and isinstance(content, list):
                texts = [b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text']
                if any(t.strip() for t in texts):
                    return next(t for t in reversed(texts) if t.strip())
        return ''

    @staticmethod
    def last_prompts(path, n=3, maxlen=600) -> list[dict]:
        """The person's last prompts: what they typed, not the hook and tool noise the user role also carries."""
        found = []
        try:
            with open(path, encoding='utf-8', errors='replace') as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get('type') != 'user' or e.get('isMeta') or e.get('isCompactSummary'):
                        continue
                    c = (e.get('message') or {}).get('content')
                    if isinstance(c, list):
                        c = '\n'.join(b.get('text', '') for b in c if isinstance(b, dict) and b.get('type') == 'text')
                    t = (c or '').strip() if isinstance(c, str) else ''
                    if not t or t.startswith(('<', 'Another Claude session', 'This session is being continued')) \
                            or 'tool_use_id' in t:
                        continue
                    found.append({'at': (e.get('timestamp') or '')[:16], 'text': t[:maxlen]})
        except OSError:
            return []
        return found[-n:]

    # ------------------------------------------------------------ settings installer
    def hook_entries(self, command: str, guard: bool = True) -> dict:
        t = self.app.text

        def cmd(sub, timeout, **extra):
            return {'type': 'command', 'command': f'{command} hook {sub} {TAG}', 'timeout': timeout, **extra}

        entries = {
            'SessionStart': [{'hooks': [cmd('start', 20)]}, {'matcher': 'compact', 'hooks': [cmd('postcompact', 10)]}],
            'PreCompact': [{'hooks': [cmd('precompact', 10)]}],
            'UserPromptSubmit': [{'hooks': [cmd('prompt', 10)]}],
            'PostToolUse': [{'matcher': 'SendMessage|Skill|Agent|Task', 'hooks': [cmd('tool', 15)]}],
            'Stop': [{'hooks': [cmd('stop', 10), cmd('vigil', 86400, asyncRewake=True,
                                                     rewakeMessage=t('hook.vigil_message'),
                                                     rewakeSummary=t('hook.vigil_summary'))]}],
            'FileChanged': [{'hooks': [cmd('wake', 30, asyncRewake=True, rewakeMessage=t('hook.wake_message'),
                                           rewakeSummary=t('hook.wake_summary'))]}],
            'Notification': [{'hooks': [cmd('notify', 10)]}],
            'SessionEnd': [{'hooks': [cmd('end', 10)]}],
        }
        if guard:
            entries['PreToolUse'] = [{'matcher': 'Bash|PowerShell', 'hooks': [cmd('guard', 10)]},
                                     {'matcher': 'Edit|Write|MultiEdit|NotebookEdit', 'hooks': [cmd('guard', 10)]}]
        return entries

    @staticmethod
    def _load_settings(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        except ValueError as err:
            raise ValueError(f'{path} is not valid JSON ({err}); nothing changed') from err

    def install(self, settings_path, command: str, guard: bool = True) -> list[str]:
        """Add the tagged hook entries after the settings' own (removing older tagged ones first). A backup of the file
        is made once, before the first change."""
        path = Path(settings_path)
        data = self._load_settings(path)
        backup = path.with_name(path.name + '.signalbox-backup')
        if path.exists() and not backup.exists():
            backup.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
        self._strip(data)
        hooks = data.setdefault('hooks', {})
        for event, groups in self.hook_entries(command, guard).items():
            hooks.setdefault(event, []).extend(groups)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return sorted(self.hook_entries(command, guard))

    def remove(self, settings_path) -> int:
        path = Path(settings_path)
        data = self._load_settings(path)
        n = self._strip(data)
        if n:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return n

    def status(self, settings_path) -> list[str]:
        data = self._load_settings(Path(settings_path))
        return sorted(event for event, groups in (data.get('hooks') or {}).items()
                      if any(TAG in (h.get('command') or '') for g in groups for h in g.get('hooks') or []))

    @staticmethod
    def _strip(data) -> int:
        removed = 0
        hooks = data.get('hooks') or {}
        for event in list(hooks):
            kept = []
            for group in hooks[event]:
                inner = [h for h in group.get('hooks') or [] if TAG not in (h.get('command') or '')]
                removed += len(group.get('hooks') or []) - len(inner)
                if inner:
                    kept.append({**group, 'hooks': inner})
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
        return removed
