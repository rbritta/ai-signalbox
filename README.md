# ai-signalbox

A signal box for many AI coding sessions working side by side on one machine: it shows what each one is doing,
routes the person's messages to the right session, hands out the resources they share (a dev server's ports, a local
database, a debugging browser), and keeps them on the workflow the team wrote down. For anyone who runs several
Claude Code sessions at once, one per clone of a project, and wants them to stop stepping on each other.

[![CI](https://github.com/rbritta/ai-signalbox/actions/workflows/ci.yml/badge.svg)](https://github.com/rbritta/ai-signalbox/actions/workflows/ci.yml)
[![release](https://img.shields.io/github/v/release/rbritta/ai-signalbox?sort=semver)](https://github.com/rbritta/ai-signalbox/releases/latest)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![live demo](https://img.shields.io/badge/live%20demo-a%20real%20run-8250df)](https://rbritta.github.io/ai-signalbox/demo/)

**[Live demo](https://rbritta.github.io/ai-signalbox/demo/)** · [Install](#install) · [Quick start](#quick-start) ·
[Commands](#commands) · [Protocol](docs/PROTOCOL.md) · [For AI agents](AGENTS.md) · [Roadmap](TODO.md)

## What it does

Each session runs in its own workspace (one clone of the project per workspace). signalbox keeps one shared folder of
plain files: status cards, asks for the person, threads, leases and their queues, jobs, and an append-only event log.
Sessions write it through the CLI, hooks deliver what waits for them, and any app can read it.

- **Status**: `signalbox note` sets the session's item, phase, task and who it waits for; `board` shows every
  session, lease and queue at once; `watch` follows the event log.
- **Messages**: `send` opens a thread with a workspace; the session that should get it is woken (even when idle) and
  answers with `thread <id> --text-file <file>`. `serve` exposes the threads in
  [chat-thread](https://github.com/rbritta/chat-thread)'s REST protocol, so a chat UI plugs in with no glue code.
- **Offers**: an app or the person puts up a piece of work (`offer`) for a workspace or for any; a session takes it
  (`claim <id>` or `claim --next`) and it becomes a message and a thread, once, even when two sessions claim it at
  the same instant. Offers can expire, move the claiming session's card to an item, and come back when the claimer
  ended without answering.
- **Resources**: `hold`, `want`, `release`, with shared and exclusive modes, queues, waiting holders, an overdue mark
  past `--minutes`, and a delivery order that settles ties.
- **Workflow**: a ```` ```flow ```` block in a markdown guide lists the phases and their order; a note that skips a
  step warns and marks the card, never blocks.
- **Guard**: a PreToolUse hook that refuses what a rule forbids, or holds it until the person says yes.
- **Close**: `close` gives the leases back, syncs the repos, stops the servers and closes the window, in steps
  plugins can add to.
- **Tools with no hooks**: `register` puts a terminal running Aider, Codex CLI or any other tool on the board; it
  holds leases, writes its card, claims offers and reads its messages with `read`.

Python 3.12.7+ or 3.13.1+ (earlier patch releases have an argparse that refuses words after an option), standard
library only. Claude Code is the host it ships with; the host layer is a plugin point.

The [live demo](https://rbritta.github.io/ai-signalbox/demo/) is a real run, regenerated on every push to main once
CI is green: three fake sessions open, write their cards, take, queue for and release a lease, get a message from the
person and answer it, run a job, claim an offer, and the person reads the board. Every command on the page ran against the code of that commit,
and the output under it is what it printed.

## Install

```
pip install "git+https://github.com/rbritta/ai-signalbox"
pip install "ai-signalbox[proc] @ git+https://github.com/rbritta/ai-signalbox"   # with psutil
```

There is no PyPI package. Each [release](https://github.com/rbritta/ai-signalbox/releases) carries a wheel:
`pip install ai_signalbox-<version>-py3-none-any.whl`.

psutil is optional. Without it, `builtin:ports` finds no listening process, a reused pid is not told apart from the
process that first had it, and process lookups go through the OS's own tools (slower on Windows); everything else
works the same.

## Quick start

1. In one of the workspaces, `signalbox init` writes `~/.signalbox/config.toml` with every folder next to this one as
   a workspace (`--workspaces <folder>` to pick another, `--hooks` to also install the hooks). Or copy
   [examples/config.toml](examples/config.toml), which shows every section, and set the workspace rule, the
   resources and the plugins.
2. `signalbox doctor` checks that the config and plugins load, that the rule names this folder and every registered
   session, that the root is writable and that the hooks are in.
3. Write the guide's flow block (see [examples/flow.md](examples/flow.md)) and point `[workflow] guide` at it.
4. `signalbox hooks install` adds the hooks to the host's settings (`--no-guard` leaves the guard out;
   `signalbox hooks remove` takes them all back out).
5. Open the sessions. Each one starts with its card and what waits for it; `signalbox board` shows them all.

## Commands

| Command | |
|---|---|
| `board`, `peek <session>` | everything at once (`--watch`: redrawn every few seconds); what one session or workspace is doing |
| `note` | the calling session's status: `--item`, `--phase`, `--task`, `--target`, `--waiting`, `--pr`, `--done`, `--requested` |
| `ask`, `ask --close N` | an ask of the session for the person (`--type`, `--alert`); no text lists them |
| `send <workspace>`, `thread [id]` | the person's message (opens a thread); read a thread, write a turn, list the open ones |
| `offer [workspace]`, `offers`, `claim <id>` | work an app or the person puts up for a workspace (or any; `--minutes`, `--item`, `--from`, `--text-file -` for stdin); the open offers the session may take (`--history`: the claimed and withdrawn); take one (`claim --next`: the oldest), which becomes the message and thread a `send` would have made, once, and is printed (`offer --withdraw <id>` takes it back) |
| `job <name>` | a long job with steps: `--steps a,b,c`, `--step`, `--wait session\|person`, `--end ok\|failed`, `--toggle` |
| `flow` | the guide's flow, as signalbox reads it (`--json`) |
| `hold <res> [why]`, `want`, `release` | leases: `--shared`, `--minutes`, `--wait SECONDS`, `--pid` for a script |
| `priority` | the delivery order; with workspaces, set it |
| `post`, `inbox`, `log` | notes for a workspace with no live session; the message log of the day |
| `events`, `watch` | events of every session; one line per new one |
| `close` | close the calling session for good (`--dry-run`), or ask another to close |
| `hooks install\|remove\|status` | the host hooks |
| `doctor`, `init` | check the setup in one go; write a first config |
| `register`, `read`, `unregister` | a session of a tool with no hooks: on the board, and its messages read by hand |
| `prune --days N` | remove delivered messages, closed threads, claimed and withdrawn offers older than N days |
| `serve` | the threads and the offers over HTTP (chat-thread protocol), with a token |

Free text goes in a file (`--text-file`, or `-` for stdin), never in the shell command: quotes, pipes and heredocs
break across shells. The built-in guard rule enforces it for the commands in `[guard] text_commands` (`send`,
`thread`, `ask`, `post`, `request`, `close`, `offer`); a lease's short reason (`hold app 'serve the page'`) and a
job's end note still ride in the shell, so keep them short and plain.

## Configuration

A TOML file merged over the defaults: `--config`, else `$SIGNALBOX_CONFIG`, else `~/.signalbox/config.toml`.
`$SIGNALBOX_ROOT` overrides where the state lives. [examples/config.toml](examples/config.toml) shows every section
with comments; `src/ai_signalbox/config.py` holds the defaults.

## Troubleshooting

- **A session has no workspace**, or `board` lists none: the rule in `[workspaces]` does not match its folder. On
  Windows, a TOML literal string needs `[\\/]` to match a backslash (`'[\\/]src[\\/](ws\d+)(?:[\\/]|$)'`).
  `signalbox doctor` shows which folders and sessions the rule misses.
- **A message is never delivered**: `signalbox hooks status` shows whether the hooks are in the host settings; a
  session opened before they were installed "does not listen" on the board until a turn runs in it (type in it, or
  reopen the tab), and meanwhile another session of the workspace takes its messages.
- **A command says "no calling session found"**: it ran outside any session (a plain terminal). Give `--sid`, run it
  from the session, or `register` the terminal.

## Docs

- [docs/PROTOCOL.md](docs/PROTOCOL.md): the files, their fields, the event kinds, the hooks and the chat-thread mapping.
- [docs/PLUGINS.md](docs/PLUGINS.md): the plugin api, the built-in plugins and the methods a store implements.
- [docs/schemas](docs/schemas): JSON Schemas of the files (card, lease, message, thread, offer, event, session).
- [AGENTS.md](AGENTS.md): the layout, the commands and the invariants, for AI coding agents (and people).

## Part of a small family

Each works alone and shares no code with the others; they meet in plain files and one naming rule (the workspace
`{pattern, name}` rules in the config).

| Project | What it does |
|---|---|
| **[ai-signalbox](https://github.com/rbritta/ai-signalbox)** | Many sessions on one machine: who is doing what, leases, messages, jobs, the workflow. This repository. |
| [ai-threads](https://github.com/rbritta/ai-threads) | A person and one session: the threads, and getting each message into the session. Planned here as a delivery plugin through its file protocol (see [TODO](TODO.md)); the built-in filebox until then. |
| [chat-thread](https://github.com/rbritta/chat-thread) | The conversation on screen: threads, bubbles, ticks, the message box. Its REST store points at `signalbox serve`. |
| [postit-board](https://github.com/rbritta/postit-board) | A board of sticky notes for an app's home screen: people move notes, the app pins its own. |
| [claude-session-relay](https://github.com/rbritta/claude-session-relay) | VS Code: open, find and close Claude Code tabs and windows from outside. `close` uses it (`builtin:relay`, through its inbox). |

## Develop

```
git clone https://github.com/rbritta/ai-signalbox
cd ai-signalbox
pip install -e .                                  # the signalbox command, running this checkout
python -m unittest discover -s tests -t tests     # offline, in temp folders
ruff check .                                      # the lint CI runs (pip install ruff)
python docs/demo/generate.py                      # the demo page, in _site/demo/
python -m build                                   # the wheel and the sdist (pip install build)
```

CI lints, runs the suite on Ubuntu and Windows with Python 3.12, 3.13 and 3.14 (race tests included: processes
claiming one file at the same instant, exactly one wins), and fails under 80% coverage; `pages.yml` regenerates the
demo once CI passes on main; a `v*` tag publishes a GitHub Release with the CHANGELOG section as its notes, after
installing the built wheel and checking its version.

## Contributing

Issues and pull requests are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md), the
[Code of Conduct](CODE_OF_CONDUCT.md), and [SECURITY.md](SECURITY.md) for reporting a vulnerability.

## License

[MIT](LICENSE)
