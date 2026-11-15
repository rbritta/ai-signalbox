# Contributing

Thanks for wanting to make ai-signalbox better. Issues and pull requests are welcome.

## Set up

Python 3.12.7 or later (3.13.1 or later on 3.13: earlier patch releases have an argparse that refuses words after an
option). The core uses the standard library only, so there is nothing to install to work on it:

```
git clone https://github.com/rbritta/ai-signalbox
cd ai-signalbox
pip install -e .              # optional: puts the `signalbox` command on the PATH
pip install -e ".[proc]"      # optional: psutil, for start times and port owners
```

## Test

```
python -m unittest discover -s tests -t tests
```

The tests run offline in temp folders with a fake host registry and a fake process table (`tests/helpers.py`); a test
never reads the real home, the real host registry or the network, and never calls a model. CI runs them on Ubuntu
and Windows with Python 3.12 and 3.13, without psutil, and prints a coverage summary:

```
pip install coverage
python -m coverage run --source=src/ai_signalbox -m unittest discover -s tests -t tests
python -m coverage report
```

## The demo

```
python docs/demo/generate.py            # writes _site/demo/index.html
```

It runs a scripted scenario (three fake sessions, leases, a message, a job, the board) against the real CLI in a
temporary folder and writes a static page of every command and its real output. CI regenerates and deploys it on
every push to main. When a command's output changes, the page changes with it; when you change the scenario, update
`EXPECTED` in `tests/test_demo.py`.

## Build

```
python -m pip install build
python -m build                 # dist/ai_signalbox-<version>-py3-none-any.whl and the sdist
```

## Pull requests

- A change in behavior comes with a test; a bug fix with a test that fails without it.
- Add a line under `Unreleased` in [CHANGELOG.md](CHANGELOG.md).
- The files under the state root are a contract ([docs/PROTOCOL.md](docs/PROTOCOL.md)): a field or an event kind
  changes only with a CHANGELOG line, and readers keep accepting what older writers wrote.
- Wording goes in `texts.py` under a key; see [AGENTS.md](AGENTS.md) for the rest of the invariants.
- Made-up data only, in code, tests, docs and examples.
- Commit messages: a short summary line in the imperative mood ("Add the board section api"), then the why if it is
  not obvious.

## Releasing

1. Move the `Unreleased` notes under a new `## [X.Y.Z] - YYYY-MM-DD` heading and set the same version in
   `pyproject.toml` and `src/ai_signalbox/__init__.py` (a test checks they agree).
2. Push, wait for CI, then push the tag `vX.Y.Z`. `release.yml` runs the tests, builds the wheel and the sdist, and
   publishes a GitHub Release with that CHANGELOG section as its notes. Nothing goes to PyPI.
