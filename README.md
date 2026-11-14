# ai-signalbox

A signal box for many AI coding sessions working side by side: it shows what each one is doing, routes the person's
messages to the right session, hands out the resources they share (a dev server's ports, a local database, a debugging
browser), and keeps them on the workflow the team wrote down.

Each session runs in its own workspace (one clone of the project per workspace). signalbox keeps one shared folder of
plain files: status cards, asks for the person, threads, leases and their queues, jobs, and an append-only event log.
Sessions write it through the CLI, hooks deliver what waits for them, and any app can read it.

- **Status**: `signalbox note` sets the session's item, phase, task and who it waits for; `board` shows every
  session, lease and queue at once; `watch` follows the event log.
- **Messages**: `send` opens a thread with a workspace; the session that should get it is woken (even when idle) and
  answers with `thread <id> --text-file <file>`. `serve` exposes the threads in [chat-thread](https://github.com/rbritta/chat-thread)'s REST
  protocol, so a chat UI plugs in with no glue code.
- **Resources**: `hold`, `want`, `release`, with shared and exclusive modes, queues, waiting holders and a delivery
  order that settles ties.
- **Workflow**: a ```` ```flow ```` block in a markdown guide lists the phases and their order; a note that skips a
  step warns and marks the card, never blocks.
- **Guard**: a PreToolUse hook that refuses what a rule forbids, or holds it until the person says yes.
- **Close**: `close` gives the leases back, syncs the repos, stops the servers and closes the window, in steps
  plugins can add to.

Python 3.11+, standard library only. Claude Code is the host it ships with; the host layer is a plugin point.

## Install

```
pip install .            # or: pip install ".[proc]" for psutil-based process checks
```

## Quick start

1. Copy [examples/config.toml](examples/config.toml) to `~/.signalbox/config.toml` (or point `$SIGNALBOX_CONFIG` at
   it) and set the workspace rule, the resources and the plugins.
2. Write the guide's flow block (see [examples/flow.md](examples/flow.md)) and point `[workflow] guide` at it.
3. `signalbox hooks install` adds the hooks to the host's settings (`--no-guard` leaves the guard out;
   `signalbox hooks remove` takes them all back out).
4. Open the sessions. Each one starts with its card and what waits for it; `signalbox board` shows them all.

## Commands

| Command | |
|---|---|
| `board`, `peek <session>` | everything at once; what one session or workspace is doing |
| `note` | the calling session's status: `--item`, `--phase`, `--task`, `--target`, `--waiting`, `--pr`, `--done`, `--requested` |
| `ask`, `ask --close N` | an ask of the session for the person (`--type`, `--alert`); no text lists them |
| `send <workspace>`, `thread [id]` | the person's message (opens a thread); read a thread, write a turn, list the open ones |
| `job <name>` | a long job with steps: `--steps a,b,c`, `--step`, `--wait session\|person`, `--end ok\|failed`, `--toggle` |
| `flow` | the guide's flow, as signalbox reads it (`--json`) |
| `hold <res> [why]`, `want`, `release` | leases: `--shared`, `--minutes`, `--wait SECONDS`, `--pid` for a script |
| `priority` | the delivery order; with workspaces, set it |
| `post`, `inbox`, `log` | notes for a workspace with no live session; the message log of the day |
| `events`, `watch` | events of every session; one line per new one |
| `close` | close the calling session for good (`--dry-run`), or ask another to close |
| `hooks install\|remove\|status` | the host hooks |
| `serve` | the threads over HTTP (chat-thread protocol), with a token |

Free text goes in a file (`--text-file`), never in the shell command: quotes, pipes and heredocs break across shells.
The built-in guard rule enforces it for signalbox's own commands.

## Works with

The projects below share no code and each works alone; they meet in plain files and one naming rule (the workspace
`{pattern, name}` rules in the config).

| Project | How it plugs in |
|---|---|
| [chat-thread](https://github.com/rbritta/chat-thread) | the chat UI: its REST store points at `signalbox serve` |
| [claude-session-relay](https://github.com/rbritta/claude-session-relay) | closes a session's tab and window on `close` (`builtin:relay`, through its inbox) |
| [ai-threads](https://github.com/rbritta/ai-threads) | delivery into one session and its numbered threads; planned as a delivery plugin through its file protocol (see TODO), the built-in filebox until then |

## Docs

- [docs/PROTOCOL.md](docs/PROTOCOL.md): the files, their fields, the event kinds, the hooks and the chat-thread mapping.
- [docs/PLUGINS.md](docs/PLUGINS.md): the plugin api and the built-in plugins.
- [AGENTS.md](AGENTS.md): working on this repo.

## License

MIT
