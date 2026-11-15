## What changed

<!-- One or two sentences. Link the issue it closes, if any. -->

## Why

<!-- The problem it solves, or what it makes possible. -->

## How it was tested

<!-- The tests added or changed, and what you ran by hand (OS, Python version, host). -->

## Checklist

- [ ] Tests cover the change; a bug fix comes with a test that fails without it
- [ ] `python -m unittest discover -s tests -t tests` passes
- [ ] `CHANGELOG.md` has a line under `Unreleased`
- [ ] README, docs/PROTOCOL.md or AGENTS.md describe the change, when a command, a file or a field changed
- [ ] The demo still runs (`python docs/demo/generate.py`) and `EXPECTED` in `tests/test_demo.py` follows the scenario
- [ ] No new runtime dependency, and only made-up data in examples and tests
