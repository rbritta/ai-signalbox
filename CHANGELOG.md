# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-11-17

### Added
- Offers: work an app or the person puts up for a session to take, with the same delivery as a push.
  `signalbox offer [workspace] --text-file <file>` posts it for a workspace or for any (`--title`, `--from`,
  `--minutes` to expire it, `--item` to move the claiming session's card), `signalbox offers` lists the open ones the
  calling session may take (outside a session, all of them; `--history` the claimed and withdrawn), and
  `signalbox claim <id>` or `claim --next` takes one: it becomes the message and thread a `send` to that session would
  have made, once, and the claim prints it. An offer whose claimer ended without answering comes back to open;
  `offer --withdraw <id>` takes one back. New files `offers/`, new event kinds `offer` and `claim`
  (docs/PROTOCOL.md, "Offers"). They show on the board (`offers` in `board --json`), in the start context, and over
  `serve` (`GET/POST /api/offers`, `POST /api/offers/<id>/claim|withdraw`, an `offer` event on `/api/events`);
  `[offers] notify = true` also sends a notice to the sessions of the workspace.
- `signalbox doctor`: the config, the plugins, the workspace rule against this folder and every registered session,
  the root, temp files left by a crash, the hooks, psutil and the Python version, in one go.
- `signalbox init`: a first config for the machine, every folder next to this one a workspace (`--hooks` also installs
  the hooks).
- Sessions of tools with no hooks (Aider, Codex CLI, a plain terminal): `signalbox register` puts one on the board,
  alive while its process is; `signalbox read` prints and takes what was sent to it; `signalbox unregister`.
- `signalbox prune --days N`: removes delivered messages, closed threads, claimed and withdrawn offers and temp files
  older than N days (`--dry-run` counts them).
- `signalbox board --watch [SECONDS]`: the board, redrawn until Ctrl+C.
- A lease past its `--minutes` is marked overdue on the board, in `board --json` (`overdue`) and in the start context.
- JSON Schemas of the protocol files in `docs/schemas/` (card, lease, message, thread, offer, event, session); a test
  validates the records the code writes against them.
- `--text-file -` reads stdin, in every command that takes a text file.
- Race tests: processes meeting at a barrier claim one message, or one offer (and claim against withdraw), together;
  exactly one wins, on the Windows leg of CI too.
- CI: ruff (rules in pyproject.toml), a coverage floor of 80%, Python 3.14; the release installs the wheel it built
  and checks that `signalbox --version` names the tag.

### Changed
- The `Store` interface has `offer_put`, `offer_get`, `offer_list`, `offer_move(oid, src, dst, extra, drop=())`,
  `offer_update` and `prune(before, dry)`: a store registered with `api.store` that does not subclass `FileStore`
  implements them. `store.claim_file` is the claim a store with its own layout calls.
- `threads.send` takes `echo`, `mid` and `progress`, and `threads.thread` takes `echo`: `serve` and the offers no
  longer redirect stdout (which was not safe between `serve`'s threads).
- File locks are tried every 10 ms (Windows used `LK_LOCK`, which waits a whole second between tries); the locked
  byte is the same, so older tools keep interleaving. A claim refuses instead of going on without the lock.
- `send`, `offer` and `inbox` normalize a workspace name the way `[workspaces] lowercase` says.
- The guard's `text_commands` include `offer`.
- The demo deploys only after CI passed on main, and shows an offer claimed.
- Package classifiers: Python 3.12 to 3.14, Alpha, console, developers, Windows and POSIX.

### Fixed
- Windows: processes renaming the same file at the same time can all succeed, so a message in the box, or an offer,
  could go to several sessions at once (16 of 20 offers had two or more winners in a race test). A lock now decides,
  reading the source again inside it.
- An offer whose send failed after the box had the message went back to open and was delivered twice; one put back
  by two writes could be open and claimed at once; an interrupt between the move and the send left it claimed with
  no message. Now the message takes the offer's id, a failure after the box keeps the claim, the same session
  claiming again gets the same message, and the way back is one move.
- A claimer that lost the race to a Windows sharing violation read "does not exist" or "claimed by None": a rename
  blocked by a reader is retried, and the refusal says the offer is being claimed.
- `write_atomic` failed with `PermissionError` while a reader held the target on Windows, left its temp file behind,
  and named it by pid only (two `serve` threads collided). Leases, queues and shared users were written in place, so
  a reader could see half a file.
- `hold` and `want` went on without the leases lock after 10 s of contention, so two sessions could both hold one
  resource exclusively; they now refuse and change nothing.
- A hook over a broken config or a missing plugin exited 1 with a traceback; it now prints one line and exits 0.
- `idle_tick` handlers are called with `(app, data, who)`, as docs/PLUGINS.md says.
- `serve` answers 400 to `?limit=abc` and to a body that is not a JSON object.
- `builtin:relay` no longer kills a pid it has no start time for: after the delay it may be another process.
- `[workspaces] lowercase = false` was ignored by `send` and `offer`; `inbox WS1` did not find `ws1`.
- Messages of the same second came out of the box in name order: now by the time they were written.
- Without psutil on Windows, every parent or name lookup took a full process snapshot: one serves a command for 2 s.
- The reentrant status lock counted depth per process, not per thread.
- The example config: its workspace rule `[\/]` matched only `/` (no session on Windows got a workspace), and it
  listed `example_plugin`, which the quick start did not copy, so every command failed. The plugin is commented out
  and a test loads the example and matches both path styles.
- The README promised the guard keeps all free text out of the shell; it now says which commands, and how to install
  without a clone.

## [0.1.2] - 2026-11-14

No change in what the library does: the project around it, and the Python versions it says it needs.

### Fixed
- `requires-python` is `>=3.12.7,!=3.13.0`. The argparse that refuses words after an option (0.1.1) is also in
  Python 3.12.0 to 3.12.6 and in 3.13.0, so on those `hold db --shared "why"` or `job x --end failed "the note"`
  failed with "unrecognized arguments"; pip now refuses to install there instead. A test checks the bound.

### Added
- A live demo at https://rbritta.github.io/ai-signalbox/demo/: `docs/demo/generate.py` runs a scripted scenario
  (three fake sessions open, write their cards, take, queue for and release leases, get a message from the person and
  answer it, run a job, and the person reads the board) against the real CLI in a temporary folder, and writes a
  static page of every command and its real output. `pages.yml` regenerates and deploys it on every push to main; a
  test runs the generator and checks the page holds every step.
- `release.yml`: on a `v*` tag, the tests, a wheel and an sdist, and a GitHub Release whose notes are this file's
  section for the version (`scripts/release_notes.py`, which refuses a tag that does not name the version).
- CI on Ubuntu and Windows with Python 3.12 and 3.13: the package installs, the `signalbox` script runs, and the tests
  run with a coverage summary in the job log.
- Tests for the release notes and for the version, which pyproject.toml, `ai_signalbox.__version__` and this file
  must agree on.
- CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md, issue forms, a pull request template and Dependabot for the
  workflows.

- Tests for the real process layer (`procs.Procs`) on the test's own process and its children: liveness, the parent
  chain, the name, kill, and the stdlib fallback without psutil.

### Changed
- The README follows the shape of its sibling projects: badges, a link line, what it does, install, quick start, the
  commands, the family table, and how to develop.
- Package metadata: the license is an SPDX expression (`license = "MIT"`), as setuptools 77 and later expect (the
  table form stops building in 2027); project URLs for the demo, the changelog and the issues.

## [0.1.1] - 2026-11-14

### Fixed
- Python 3.12 or later: 3.11's argparse refuses words after an option (`job x --end failed "the note"`,
  `send ws --ask 1 -`), which the CLI takes on 3.12 and later. 0.1.0 said 3.11 and failed there.
- A nested f-string in the message hook rewritten so the module also compiles before 3.12.

## [0.1.0] - 2026-11-14

First release.

### Added
- Status cards, asks, threads, the person's messages and their delivery to live or idle sessions (Claude Code hooks).
- Leases with shared and exclusive modes, queues, waiting holders and a delivery order.
- Jobs with steps, waits and toggles.
- The workflow guide's flow block: stages, chains, parallel steps, lanes per target, gates and renamed phases.
- The guard (PreToolUse) with person-released rules.
- `close` as a pipeline of steps; built-in plugins for git, ports and claude-session-relay.
- `serve`: the threads over HTTP in chat-thread's REST protocol, with Server-Sent Events.
- A plugin api for resources, rules, wording, commands, board sections, close steps, the store, delivery, the window
  controller and other hosts.
- Every line signalbox writes into the event log can be reworded through `[texts]` (`status.part_*`, `job.what_*`,
  `job.end_*`, `lease.ev_*`, `lease.mode_*`, `ask.ev_*`), so a deployment whose apps read the log in another language
  keeps it. So can the inbox and log lines (`inbox.*`, `log.none`), `peek`'s lines (`peek.*`) and the
  priority lines (`priority.*`); a template set to an empty string drops its line.
- The event kind of the person's prompts (`[events] prompt_kind`) and how a priority order is signed
  (`[priority] person_by`, `by_signalbox`) are config, and the log file's title is the store's `log_title`, for apps
  that already read these files under other names.
- What `note`, `job`, `hold`, `want`, `release` and `ask` print and refuse with is wording too (`note.*`, `job.*`,
  `lease.*`, `ask.*`, `ports.*`, `cli.*`), command and option names in the hints included; `api.status_render` sets how
  `note` prints the status; `[cli] stdout_refusals` lists the refusals that print on stdout, for callers that read a
  busy resource as a state; `[jobs] toggle_states` limits the states of a switch (`email = ["on", "off"]`).

[Unreleased]: https://github.com/rbritta/ai-signalbox/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/rbritta/ai-signalbox/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/rbritta/ai-signalbox/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/rbritta/ai-signalbox/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/rbritta/ai-signalbox/releases/tag/v0.1.0
