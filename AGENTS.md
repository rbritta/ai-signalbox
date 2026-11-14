# Working on ai-signalbox

- Python 3.11+, standard library only in the core; `psutil` is an optional extra and every use of it has a fallback.
- Tests: `python -m unittest discover -s tests -t tests`. They run offline in temp folders with a fake host registry
  and fake processes; a test never reads the real home, the real host registry or the network.
- Layout: `src/ai_signalbox/` is the core, `builtin/` the plugins that ship with it, `hosts/` the host adapters,
  `delivery/` how messages reach a session. Plugins register through `plugins.Api` only.
- The files under the state root are a contract ([docs/PROTOCOL.md](docs/PROTOCOL.md)): a field or event kind changes
  only with a CHANGELOG line, and readers must keep accepting what older writers wrote.
- Wording lives in `texts.py` under a key, so a deployment can override it; code does not build user-facing sentences
  inline.
- Hooks never fail the host: an error is one line on stderr and exit 0. The guard only denies, never prompts.
- Writes go through `util.write_json` / `write_atomic` (temp file and rename), except the watched files, which are
  rewritten in place.
- Nothing deployment-specific belongs here: no organization, product, people, paths or private tools in code, tests,
  docs or examples. That goes in a site plugin.
- `.editorconfig`: 4 spaces, LF, UTF-8.
