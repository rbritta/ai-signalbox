# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- The live demo page declares its own icon, so the browser no longer asks the site root for a missing
  `favicon.ico` (one 404 in the console on the first load).

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

[Unreleased]: https://github.com/rbritta/ai-signalbox/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/rbritta/ai-signalbox/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/rbritta/ai-signalbox/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/rbritta/ai-signalbox/releases/tag/v0.1.0
