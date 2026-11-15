# AGENTS.md

For AI coding agents working on this repository.

## What this is

ai-signalbox is a coordination layer for several AI coding sessions on one machine: status cards (who is doing what),
asks for the person, threads with the person, leases on shared resources with queues, long jobs, a workflow guide
the notes are checked against, a guard hook, and `close`. State is plain files under one root; the `signalbox` CLI
writes them, host hooks deliver what waits for a session, and any app can read them. Python 3.12.7+ or 3.13.1+,
standard library only in the core.

## Layout

| Path | What |
|---|---|
| `src/ai_signalbox/` | the core: `cli.py` (argument parsing and dispatch), `app.py` (the object every command works through), `config.py`, `store.py` (the files), `status.py`, `threads.py`, `leases.py`, `jobs.py`, `workflow.py`, `views.py` (board, peek), `hooks.py`, `guard.py`, `close.py`, `serve.py`, `texts.py` (all wording) |
| `src/ai_signalbox/builtin/` | the plugins that ship with it (git, ports, relay) |
| `src/ai_signalbox/hosts/` | host adapters; `claude_code.py` is the only one |
| `src/ai_signalbox/delivery/` | how a message reaches a session (`filebox.py`) |
| `tests/` | unittest suites; `helpers.py` builds a temp world (state root, fake host registry, fake process table) |
| `docs/PROTOCOL.md`, `docs/PLUGINS.md` | the file contract and the plugin api |
| `docs/demo/generate.py` | the live demo: a scripted scenario run against the real CLI, written as a static page |
| `scripts/release_notes.py` | the CHANGELOG section of a version, for `release.yml` |
| `examples/` | a config, a flow guide and a plugin |

## Commands

```
python -m unittest discover -s tests -t tests     # the whole suite, offline
python docs/demo/generate.py --site _site         # the demo page, in _site/demo/
python scripts/release_notes.py v0.1.2            # the notes of a release
python -m ai_signalbox --help                     # the CLI from the checkout (what `signalbox` runs)
```

## Invariants

- Standard library only in the core; `psutil` is an optional extra and every use of it has a fallback.
- Tests run offline in temp folders with a fake host registry and fake processes; a test never reads the real home,
  the real host registry or the network, and never calls a model. The demo generator follows the same rule: its
  config, state root, host registry and home are all in a temporary folder, and it clears `SIGNALBOX_ROOT`,
  `SIGNALBOX_CONFIG` and `CLAUDE_CONFIG_DIR` for the commands it runs.
- The files under the state root are a contract ([docs/PROTOCOL.md](docs/PROTOCOL.md)): a field or event kind changes
  only with a CHANGELOG line, and readers must keep accepting what older writers wrote.
- Wording lives in `texts.py` under a key, so a deployment can override it; code does not build user-facing sentences
  inline.
- Hooks never fail the host: an error is one line on stderr and exit 0. The guard only denies, never prompts.
- Writes go through `util.write_json` / `write_atomic` (temp file and rename), except the watched files, which are
  rewritten in place.
- Free text reaches the CLI from a file (`--text-file`), never from the shell line.
- Nothing deployment-specific belongs here: no organization, product, people, paths or private tools in code, tests,
  docs or examples. That goes in a site plugin.
- The version lives in `pyproject.toml` and `src/ai_signalbox/__init__.py`, and `CHANGELOG.md` has its section; a
  test checks the three agree.
- `.editorconfig`: 4 spaces for Python, 2 elsewhere, LF, UTF-8.
