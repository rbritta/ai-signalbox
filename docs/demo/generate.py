"""Generate the live demo page: a scripted scenario run against the real ai-signalbox code.

    python docs/demo/generate.py [--site DIR]

Three fake sessions, each a real (idle) Python process registered in a temporary host registry, open, write their
status cards, take, queue for and release leases, get a message from the person and answer it, run a job, and the
person reads the board. Every command runs `python -m ai_signalbox` (what the `signalbox` script runs) from this
checkout's `src/`, and the page shows each command with the output it really printed.

Nothing outside a temporary folder is read or written: the config, the state root, the host registry and the
workspaces all live in it, `HOME`/`USERPROFILE` point into it, and `SIGNALBOX_ROOT`, `SIGNALBOX_CONFIG` and
`CLAUDE_CONFIG_DIR` are removed from the environment of every command. No network, no model.

Output (`--site`, default `_site`): `index.html` (a link to the demo) and `demo/index.html` + `demo/demo.css`, a static
page with no script, readable on a phone, that passes `default-src 'self'`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
FLOW = ROOT / 'examples' / 'flow.md'
REPO_URL = 'https://github.com/rbritta/ai-signalbox'
PERSON = 'person'
CLEARED_ENV = ('SIGNALBOX_ROOT', 'SIGNALBOX_CONFIG', 'CLAUDE_CONFIG_DIR', 'SIGNALBOX_VIGIL_STEP', 'PYTHONHOME')

# workspace, session name, session id, host status
SESSIONS = (
    ('ws1', 'auth-fix', '00000000-0000-4000-8000-000000000001', 'idle'),
    ('ws2', 'search-ui', '00000000-0000-4000-8000-000000000002', 'busy'),
    ('ws3', 'release', '00000000-0000-4000-8000-000000000003', 'busy'),
)

CONFIG = """\
# The config of this run. $DEMO is the temporary folder.
root = '{root}/state'
plugins = []

[workspaces]
rules = [{{ pattern = '[\\\\/]work[\\\\/](ws\\d+)(?:[\\\\/]|$)', name = '$1' }}]
glob = ['{root}/work/ws*']

[host]
config_dirs = ['{root}/host']      # the fake host registry: sessions/<pid>.json
process_name = 'python'            # each fake session is a Python process

[resources.app]
description = "the dev server: one session at a time"

[resources.db]
description = "the local database: shared for reads, exclusive for migrations"
shared = true

[workflow]
guide = 'flow.md'
"""

# A fake session: a process that runs the commands it is handed, so each command is its child, as a command a real
# session runs is. One JSON request per line on stdin, one JSON result per line on stdout.
RUNNER = r"""
import json, subprocess, sys
for line in sys.stdin:
    req = json.loads(line)
    r = subprocess.run([sys.executable, '-m', 'ai_signalbox', *req['argv']], cwd=req['cwd'],
                       input=req['stdin'].encode('utf-8'), capture_output=True)
    sys.stdout.write(json.dumps({'code': r.returncode, 'out': r.stdout.decode('utf-8', 'replace'),
                                 'err': r.stderr.decode('utf-8', 'replace')}) + '\n')
    sys.stdout.flush()
"""


@dataclass
class Step:
    section: str
    title: str
    why: str
    actor: str
    argv: list
    files: dict = field(default_factory=dict)
    stdin: str = ''
    wide: bool = False      # a table-like output (the board): keep its lines, scroll sideways on a phone
    code: int = 0
    out: str = ''
    err: str = ''

    @property
    def command(self) -> str:
        return shlex.join(['signalbox', *self.argv])


class Session:
    def __init__(self, workspace, name, sid, status, root: Path, env: dict):
        self.workspace, self.name, self.sid = workspace, name, sid
        self.cwd = root / 'work' / workspace / 'app'
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen([sys.executable, '-c', RUNNER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     cwd=str(self.cwd), env=env)
        registry = root / 'host' / 'sessions'
        registry.mkdir(parents=True, exist_ok=True)
        started = int(time.time() * 1000)
        (registry / f'{self.proc.pid}.json').write_text(json.dumps({
            'pid': self.proc.pid, 'sessionId': sid, 'cwd': str(self.cwd), 'name': name, 'status': status,
            'startedAt': started, 'statusUpdatedAt': started}), encoding='utf-8')

    @property
    def label(self) -> str:
        return f'{self.workspace} {self.name}'

    def run(self, argv, stdin=''):
        self.proc.stdin.write((json.dumps({'argv': argv, 'cwd': str(self.cwd), 'stdin': stdin}) + '\n').encode())
        self.proc.stdin.flush()
        reply = json.loads(self.proc.stdout.readline())
        return reply['code'], reply['out'], reply['err']

    def stop(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
        finally:
            self.proc.stdout.close()


class Run:
    """One run of the scenario in a temporary folder; `steps` holds what each command printed."""

    def __init__(self, root: Path):
        self.root = root
        self.steps: list[Step] = []
        self.sessions: dict[str, Session] = {}
        home = root / 'home'
        home.mkdir(parents=True, exist_ok=True)
        (root / 'state').mkdir()
        shutil.copyfile(FLOW, root / 'flow.md')
        (root / 'config.toml').write_text(CONFIG.format(root=root.as_posix()), encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if k not in CLEARED_ENV}
        env.update(SIGNALBOX_CONFIG=str(root / 'config.toml'), HOME=str(home), USERPROFILE=str(home),
                   PYTHONPATH=os.pathsep.join([str(SRC), *filter(None, [os.environ.get('PYTHONPATH')])]),
                   PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
        self.env = env

    def open_sessions(self):
        for ws, name, sid, status in SESSIONS:
            self.sessions[ws] = Session(ws, name, sid, status, self.root, self.env)

    def close(self):
        for s in self.sessions.values():
            s.stop()

    def step(self, section, title, why, actor, argv, files=None, stdin='', expect=0, wide=False) -> Step:
        st = Step(section, title, why, actor, list(argv), dict(files or {}), stdin, wide)
        where = self.root if actor == PERSON else self.sessions[actor].cwd
        for name, text in st.files.items():
            (where / name).write_text(text, encoding='utf-8')
        if actor == PERSON:
            r = subprocess.run([sys.executable, '-m', 'ai_signalbox', *st.argv], cwd=str(self.root), env=self.env,
                               input=stdin.encode('utf-8'), capture_output=True)
            st.code, st.out, st.err = r.returncode, r.stdout.decode('utf-8', 'replace'), r.stderr.decode('utf-8',
                                                                                                         'replace')
        else:
            st.code, st.out, st.err = self.sessions[actor].run(st.argv, stdin)
        if st.code != expect:
            raise RuntimeError(f'step "{title}": {st.command} exited {st.code}, expected {expect}\n{st.out}\n{st.err}')
        self.steps.append(st)
        return st

    def hook(self, section, title, why, ws, name, payload, expect=0) -> Step:
        """The host runs a hook in session `ws`: `name` is the hook (start, wake), its input JSON on stdin."""
        s = self.sessions[ws]
        event = {'start': 'SessionStart', 'wake': 'FileChanged'}[name]
        data = {'session_id': s.sid, 'cwd': str(s.cwd), 'hook_event_name': event, **payload}
        st = self.step(section, title, why, ws, ['hook', name], stdin=json.dumps(data), expect=expect)
        if 'signalbox' in st.err and 'hook' in st.err and not st.out:
            raise RuntimeError(f'hook {name} failed: {st.err}')  # a hook never fails the host, so check its stderr
        return st

    def display(self, text: str) -> str:
        """The temporary folder reads as $DEMO, in every spelling an output may use."""
        for spelling in sorted({str(self.root), self.root.as_posix(), json.dumps(str(self.root))[1:-1]},
                               key=len, reverse=True):
            text = text.replace(spelling, '$DEMO')
        return text


def scenario(run: Run) -> None:  # noqa: PLR0915 - one scripted story, step by step
    run.step('Setup', 'The version under test', 'The code this page ran, straight from the checkout.',
             PERSON, ['--version'])
    run.step('Setup', "The team's workflow guide", 'The config points at a markdown guide; signalbox reads its flow '
             'block, and every status note is checked against it.', PERSON, ['flow'], wide=True)
    run.open_sessions()
    for ws, name, _sid, _status in SESSIONS:
        run.hook('Three sessions open', f'{ws} opens', f'The host runs the start hook when session {name} opens: it '
                 'learns who it is, who else is working, and delivery is armed (the files it watches come back in '
                 '`watchPaths`).', ws, 'start', {'source': 'startup'})

    run.step('Status cards', 'ws1 starts its analysis', 'Each session says what it is doing. Free text always comes '
             'from a file, never from the shell line.', 'ws1',
             ['note', '--item', '101', '--phase', 'analysis', '--text-file', 'note.txt'],
             {'note.txt': 'Login fails after a token refresh: reproducing it'})
    run.step('Status cards', 'ws2 is implementing', '', 'ws2',
             ['note', '--item', '102', '--phase', 'implement', '--text-file', 'note.txt'],
             {'note.txt': 'Search box: highlight the matched words'})
    run.step('Status cards', 'ws3 starts its item', '', 'ws3',
             ['note', '--item', '103', '--phase', 'init', '--text-file', 'note.txt'],
             {'note.txt': 'Release checklist for 2.4'})
    run.step('Status cards', 'ws3 jumps from init to implement', 'The guide puts analysis and plan in between. '
             'signalbox warns and marks the card; it never blocks.', 'ws3',
             ['note', '--phase', 'implement', '--text-file', 'note.txt'],
             {'note.txt': 'Bump the version and write the changelog'})

    run.step('Leases', 'ws2 takes the dev server', 'An exclusive lease: one holder at a time.', 'ws2',
             ['hold', 'app', 'serve the search page'])
    run.step('Leases', 'ws3 queues for it', 'The resource is busy, so ws3 joins its queue.', 'ws3',
             ['want', 'app', 'run the end-to-end tests'])
    run.step('Leases', 'ws1 uses the database in shared mode', 'Shared mode: many readers at once.', 'ws1',
             ['hold', 'db', '--shared', 'read the schema'])
    run.step('Leases', 'ws2 releases the dev server', 'The next one in the queue is named, with the message to send '
             'it.', 'ws2', ['release', 'app'])
    run.step('Leases', 'ws3 takes it', '', 'ws3', ['hold', 'app', 'run the end-to-end tests'])
    run.step('Leases', 'ws2 asks the person for a review', 'An ask sits on the card until the person answers it.',
             'ws2', ['ask', '--type', 'review', '--text-file', 'ask.txt'],
             {'ask.txt': 'The search highlight is ready: review the diff on branch search-highlight?'})

    sent = run.step('A message', 'The person writes to ws1', 'The message opens a thread and touches the signal file '
                    'every live session of ws1 watches.', PERSON, ['send', 'ws1', '--text-file', 'message.txt'],
                    {'message.txt': 'Use the staging tenant for the repro, please.'})
    tid = json.loads(sent.out)['id']
    signal = run.root / 'state' / 'box' / 'ws1' / '.signal'
    run.hook('A message', 'ws1 wakes up', 'The host sees the touched file and runs the wake hook in idle session '
             'auth-fix. Exit code 2 wakes the session with the text on stderr: this is what it reads.', 'ws1',
             'wake', {'file_path': str(signal)}, expect=2)
    run.step('A message', 'ws1 answers in the thread', '', 'ws1', ['thread', tid, '--text-file', 'reply.txt'],
             {'reply.txt': 'Done: it fails on staging too. The refresh token is dropped when the clock skews.'})
    run.step('A message', 'The person reads the thread', '', PERSON, ['thread', tid])

    run.step('A job', 'ws3 opens a job with two steps', 'A long job shows on the card, one badge per step.', 'ws3',
             ['job', 'e2e', '--steps', 'build,test'])
    time.sleep(1.2)  # a step of 0 s is refused: let the build really take a moment
    run.step('A job', 'The build step closes, the tests start', 'The line closing a step says what it produced.',
             'ws3', ['job', 'e2e', '--step', 'test', '--done-file', 'done.txt'], {'done.txt': 'build ok: 1 package'})
    time.sleep(1.2)
    run.step('A job', 'The job ends', '', 'ws3', ['job', 'e2e', '--end', 'ok', '--done-file', 'done.txt'],
             {'done.txt': 'test ok: 48 passed'})

    run.step('An offer', 'A tracker offers a piece of work to any workspace', 'An offer waits until a session takes '
             'it; `--from` names who posted it.', PERSON, ['offer', '--from', 'tracker', '--text-file', 'offer.txt'],
             {'offer.txt': 'Bump the date library to its latest patch and run the tests.'})
    run.step('An offer', 'ws2 lists what it may claim', "Its own workspace's offers and the ones for any.", 'ws2',
             ['offers'])
    run.step('An offer', 'ws2 claims the oldest one', 'The claim becomes the message and thread a send would have '
             'made, printed at once; of two sessions claiming together, one gets it.', 'ws2', ['claim', '--next'])
    run.step('An offer', 'The person sees who took it', '', PERSON, ['offers', '--history'])

    run.step('The board', 'The person looks at the board', 'Every session, lease, queue and open thread at once '
             '(run outside any session, so `you` is `?`).', PERSON, ['board'], wide=True)
    run.step('The board', 'And peeks at one session', 'One card in full, with its open asks.', PERSON,
             ['peek', 'ws2'])


# ---------------------------------------------------------------- the page

CSS = """\
:root { --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --line: #d1d9e0; --code: #f6f8fa; --accent: #0969da;
  --warn: #9a6700; --person: #8250df; --ws1: #1a7f37; --ws2: #bc4c00; --ws3: #0969da; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --line: #3d444d; --code: #151b23; --accent: #4493f8;
    --warn: #d29922; --person: #ab7df8; --ws1: #3fb950; --ws2: #f0883e; --ws3: #4493f8; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 16px; }
h1 { font-size: 1.6rem; margin: 0.5rem 0; }
h2 { font-size: 1.25rem; margin: 2rem 0 0.5rem; padding-bottom: 0.25rem; border-bottom: 1px solid var(--line); }
h3 { font-size: 1rem; margin: 0; }
a { color: var(--accent); }
.banner { border: 1px solid var(--line); border-left: 4px solid var(--accent); border-radius: 6px; padding: 12px;
  margin: 1rem 0; background: var(--code); }
.muted { color: var(--muted); }
.step { border: 1px solid var(--line); border-radius: 6px; padding: 12px; margin: 12px 0; }
.head { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; }
.n { color: var(--muted); font-variant-numeric: tabular-nums; }
.who { font-size: 0.8rem; font-weight: 600; border: 1px solid currentColor; border-radius: 999px; padding: 0 8px; }
.who.person { color: var(--person); } .who.ws1 { color: var(--ws1); } .who.ws2 { color: var(--ws2); }
.who.ws3 { color: var(--ws3); }
.why { margin: 6px 0; }
pre { margin: 6px 0; padding: 8px 10px; background: var(--code); border: 1px solid var(--line); border-radius: 6px;
  overflow-x: auto; font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: pre; }
pre.cmd { font-weight: 600; }
pre.wrap { white-space: pre-wrap; overflow-wrap: break-word; }
code { font: 0.9em ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.label { font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; margin-top: 8px; }
.label code { text-transform: none; letter-spacing: 0; }
.exit { font-size: 0.8rem; color: var(--muted); }
.exit.nonzero { color: var(--warn); }
details > summary { cursor: pointer; color: var(--muted); font-size: 0.85rem; }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
th, td { text-align: left; border-bottom: 1px solid var(--line); padding: 4px 8px 4px 0; vertical-align: top; }
footer { margin: 2rem 0 1rem; color: var(--muted); font-size: 0.85rem; }
@media (max-width: 480px) { pre { font-size: 12px; } h1 { font-size: 1.35rem; } }
"""


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _pre(text, cls='') -> str:
    return f'<pre{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>{_esc(text.rstrip())}</pre>'


def _prose(text) -> str:
    """Escaped text with `code` spans."""
    return re.sub(r'`([^`]+)`', lambda m: f'<code>{m.group(1)}</code>', _esc(text))


def _context(out: str):
    """The context a start hook hands the host, decoded from its JSON (what the session reads)."""
    try:
        return json.loads(out)['hookSpecificOutput']['additionalContext']
    except (ValueError, KeyError, TypeError):
        return None


def _origin() -> tuple[str, str]:
    """Who generated the page: a GitHub Actions run (with its link) or a local run."""
    if os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_RUN_ID'):
        server = os.environ.get('GITHUB_SERVER_URL', 'https://github.com')
        repo = os.environ.get('GITHUB_REPOSITORY', 'rbritta/ai-signalbox')
        url = f"{server}/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        sha = (os.environ.get('GITHUB_SHA') or '')[:7]
        commit = f' at commit <a href="{_esc(f"{server}/{repo}/commit/{sha}")}"><code>{_esc(sha)}</code></a>' if sha \
            else ''
        return 'ci', f'Generated by CI (<a href="{_esc(url)}">GitHub Actions run</a>){commit}'
    return 'local', 'Generated locally, not by CI'


def render(run: Run, version: str, when: dt.datetime) -> str:
    origin, origin_html = _origin()
    sessions = ''.join(f'<tr><td><span class="who {ws}">{ws}</span></td><td>{_esc(name)}</td><td>{status}</td></tr>'
                       for ws, name, _sid, status in SESSIONS)
    parts, section, n = [], None, 0
    for st in run.steps:
        if st.section != section:
            section = st.section
            parts.append(f'<h2>{_esc(section)}</h2>')
        n += 1
        who = 'person' if st.actor == PERSON else st.actor
        hook = st.argv[0] == 'hook'
        who_label = 'the person' if st.actor == PERSON else (
            f'host, in {run.sessions[st.actor].label}' if hook else run.sessions[st.actor].label)
        where = '$DEMO' if st.actor == PERSON else run.display(str(run.sessions[st.actor].cwd))
        body = [f'<div class="head"><span class="n">{n}.</span><h3>{_esc(st.title)}</h3>'
                f'<span class="who {who}">{_esc(who_label)}</span></div>']
        if st.why:
            body.append(f'<p class="why">{_prose(st.why)}</p>')
        for name, text in st.files.items():
            body.append(f'<div class="label"><code>{_esc(name)}</code></div>{_pre(text, "wrap")}')
        if st.stdin:
            pretty = json.dumps(json.loads(st.stdin), indent=1, ensure_ascii=False)
            body.append(f'<div class="label">stdin, from the host</div>{_pre(run.display(pretty), "wrap")}')
        body.append(f'<div class="label">command, in <code>{_esc(where)}</code></div>'
                    f'{_pre("$ " + st.command, "cmd wrap")}')
        out = run.display(st.out)
        if out.strip():
            label = 'stdout'
            block = _pre(out, '' if st.wide else 'wrap')
            if hook and _context(st.out):
                body.append(f'<div class="label">what the session reads (the context in that JSON)</div>'
                            f'{_pre(run.display(_context(st.out)), "wrap")}')
                block = f'<details><summary>raw stdout</summary>{_pre(out, "wrap")}</details>'
                label = ''
            body.append((f'<div class="label">{label}</div>' if label else '') + block)
        if st.err.strip():
            body.append(f'<div class="label">stderr{" (what wakes the session)" if hook else ""}</div>'
                        f'{_pre(run.display(st.err), "wrap" if hook else "")}')
        body.append(f'<div class="exit{" nonzero" if st.code else ""}">exit code {st.code}</div>')
        parts.append(f'<section class="step">{"".join(body)}</section>')
    config = run.display((run.root / 'config.toml').read_text(encoding='utf-8'))
    stamp = when.strftime('%Y-%m-%d %H:%M UTC')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'">
<meta name="generator" content="ai-signalbox docs/demo/generate.py ({origin})">
<link rel="icon" href="data:,">
<title>ai-signalbox live demo</title>
<link rel="stylesheet" href="demo.css">
</head>
<body>
<main>
<h1>ai-signalbox: a real run</h1>
<p>Three AI coding sessions share one machine. signalbox shows what each one is doing, hands out the resources they
share, routes the person's message to the right session and keeps one board of it all. This page is that, run for real.
</p>
<div class="banner">
<strong>A real run, not a mock-up.</strong> {origin_html} on {_esc(stamp)} from <strong>{_esc(version)}</strong>.
Every command below ran against the real code of this repository, and the output under it is what the command printed.
The sessions are fake (each one an idle Python process in a temporary host registry, no AI model involved) and the
state lives in a temporary folder, shown as <code>$DEMO</code>.
</div>
<p><a href="{REPO_URL}">Repository</a> · <a href="{REPO_URL}#readme">README</a> ·
<a href="{REPO_URL}/blob/main/docs/PROTOCOL.md">Protocol</a> ·
<a href="{REPO_URL}/blob/main/docs/demo/generate.py">The script that made this page</a></p>
<h2>The setup</h2>
<table><thead><tr><th>Workspace</th><th>Session</th><th>Host status</th></tr></thead>
<tbody>{sessions}</tbody></table>
<details><summary>config.toml</summary>{_pre(config)}</details>
{''.join(parts)}
<footer>Generated by <code>python docs/demo/generate.py</code>. {_esc(len(run.steps))} commands, all run in one go;
regenerated on every push to main. MIT licensed.</footer>
</main>
</body>
</html>
"""


INDEX = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'">
<meta http-equiv="refresh" content="0; url=demo/">
<link rel="icon" href="data:,">
<title>ai-signalbox</title>
</head>
<body><p><a href="demo/">ai-signalbox live demo</a></p></body>
</html>
"""


def build(site: Path, when: dt.datetime | None = None) -> list[Step]:
    """Run the scenario in a fresh temporary folder and write the site into `site`. Returns the steps."""
    when = when or dt.datetime.now(dt.timezone.utc)
    tmp = Path(tempfile.mkdtemp(prefix='signalbox-demo-')).resolve()
    run = Run(tmp)
    try:
        scenario(run)
        version = run.steps[0].out.strip()
        page = render(run, version, when)
    finally:
        run.close()
        shutil.rmtree(tmp, ignore_errors=True)
    demo = site / 'demo'
    demo.mkdir(parents=True, exist_ok=True)
    (demo / 'index.html').write_text(page, encoding='utf-8', newline='\n')
    (demo / 'demo.css').write_text(CSS, encoding='utf-8', newline='\n')
    (site / 'index.html').write_text(INDEX, encoding='utf-8', newline='\n')
    return run.steps


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    p.add_argument('--site', default=str(ROOT / '_site'), help='output folder (default: _site in the repository)')
    args = p.parse_args(argv)
    site = Path(args.site)
    steps = build(site)
    print(f'{len(steps)} steps -> {site / "demo" / "index.html"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
