# Plugins

A plugin is a Python module with `register(api)`. List it in the config and say where it lives:

```toml
plugins = ["builtin:git", "builtin:ports", "builtin:relay", "my_site_plugin"]
plugin_paths = ["~/my-site"]
```

`builtin:<name>` loads one of the plugins that ship with signalbox. A plugin is how a deployment adds what only it has
(its resources, its rules, its wording, its own commands) without forking the library. See
[examples/example_plugin.py](../examples/example_plugin.py).

## The api

| Call | What it adds |
|---|---|
| `api.resource(name, description='', shared=False, ports=(), **extra)` | a resource for `hold`; config resources win over plugin ones |
| `api.lease_check(fn, resources=None)` | `fn(app, res, mine, current) -> str or None`: a refusal before a lease is taken |
| `api.lease_blocker(fn, resources=None)` | `fn(app, res) -> str or None`: something outside signalbox holds it (an old lock); `--wait` waits for it |
| `api.on_take(fn)` | `fn(app, res, mine, sessions, wait_left)` after a lease is taken |
| `api.on_release(fn)` | `fn(app, res, current, mine, result, item)` after it is given back |
| `api.note_rule(fn)` | `fn(app, card, change) -> str or None`: a refusal of a note |
| `api.text_check(fn)` | `fn(*texts) -> str or None`: a warning on the text of a note or a done line, never a block |
| `api.pr_info(fn)` | `fn(n) -> {repo, url} or None`: the link of `note --pr N` |
| `api.status_render(fn)` | `fn(app, card) -> str`: how `note` prints the status it wrote (`note.printed` frames it); JSON by default |
| `api.item_from_branch(fn)` | `fn(app, workspace) -> item or None` |
| `api.branch(fn)` | `fn(app, workspace) -> branch or None`, shown on the board |
| `api.workspace_clean(fn)` | `fn(app, workspace) -> (ok, why)`: part of the delivered-and-clean priority check |
| `api.command(name, run, configure=None, help='')` | `signalbox <name>`: `configure(parser)` adds its arguments, `run(app, args) -> exit code` |
| `api.board_section(fn, resource=None)` | `resource=None`: `fn(app, sessions) -> lines` after the resources; `'*'` or a name: `fn(app, sessions, res) -> lines` under that resource |
| `api.peek_section(fn)` | `fn(app, session) -> lines` in `peek` |
| `api.close_step(name, fn, order=50)` | `fn(app, ctx) -> lines`, a step of `close` (see below) |
| `api.guard_rule(name, match, decision, reason, key=None, on='shell')` | a rule of the guard (see below) |
| `api.guard_pre(fn)` | `fn(app, data)` before the guard decides; never decides itself |
| `api.start_lines(fn)` | `fn(app, who, sessions) -> lines` in every session's start context |
| `api.on_hook(event, fn)` | `fn(app, data, who)` on a hook; for `prompt`, a returned string is printed into the session; `idle_tick` runs every 10 s in the idle vigil; `wake` runs before a touched file is looked at |
| `api.text(key, template)` | overrides a wording ([texts.py](../src/ai_signalbox/texts.py) lists the keys; `[texts]` in the config wins over plugins) |
| `api.store(factory)` | `factory(app) -> Store`: keep the state in another layout (an existing deployment keeps its files); see "A store" below |
| `api.delivery(factory)` | `factory(app)` with `send`, `claim`, `deliver`, `listens`, `keeps_with`, `release`, `wake_text`, `vigil` |
| `api.window(controller)` | the window control of `close`: `plan_close(app, ctx) -> (lines, closes)` and `close_later(...)` |
| `api.host(kind, factory)` | a host adapter for another AI coding tool (`[host] type = kind`) |

## A store

A store subclasses `ai_signalbox.store.Store` (or `FileStore`, overriding only what it maps elsewhere) and implements:
the locks (`status_lock`, `leases_lock`, `threads_lock`, `status_lock_depth`); the events (`append_event`,
`read_events`, `scan_events`, `events_size`, `tail_events`); cards (`get_status`, `put_status`, `list_status`);
leases (`get_lease`, `put_lease`, `get_queue`, `put_queue`, `get_shared`, `put_shared`); `get_priority`,
`put_priority`; threads (`get_thread`, `put_thread`, `list_threads`); the box (`box_put`, `box_list`, `box_claim`,
`box_update`, `delivered`, `signal_path`, `start_path`, `woke_path`, `vigil_path`); offers (`offer_put`, `offer_get`,
`offer_list`, `offer_move(oid, src, dst, extra, drop=())`, `offer_update`); requests (`get_request`, `put_request`,
`list_requests`); the inbox and the log (`inbox_append`, `inbox_take`, `inbox_pending`, `log_append`, `log_read`);
`get_snapshot`, `put_snapshot`, `get_state`, `put_state`; and `prune(before, dry)`.

A claim must be decided by a lock, not by a rename alone: on Windows, processes renaming one file at the same time can
all succeed. `store.claim_file(src, dst, update, lock)` does it (read again inside the lock, rename, write the claimed
record atomically) and is what `FileStore.box_claim` and `offer_move` use; a store with its own layout calls it with its
own paths.

## Close steps

`ctx` holds `mine` (the session), `who`, `sessions`, `siblings` (other live sessions of the workspace that are not
closing too), `dry`, `no_sync`, `delay`, `workspace`, `root` (the workspace folder), `done`, `synced` (repos the git step
moved: `{repo, before, after}`), `report` (a dict that ends up in the `close` event), and anything an earlier step put
there (`extra_pids`: more processes the window controller stops with the session). With siblings, a step should not
pull the ground from under them (a sync, stopping their servers). Built-in orders: `ports` 20, `git` 80.

## Guard rules

- `on='shell'`: `match(command, unquoted, cwd)`, where `unquoted` is the command without its quoted text plus every
  script handed to a shell with `-c`; `on='path'`: `match(file_path)`; `on='url'`: `match(url)`.
- `match` returns something truthy to hit; a string replaces the rule's reason.
- `decision='deny'`: refused with the reason.
- `decision='person'` with a `key`: refused with how to ask (an ask of type `authorization` whose text starts with
  `[release <key>]`), until the person's yes to it, delivered from the app or typed as the next prompt in the IDE,
  which lets the rule pass for `[guard] release_minutes`.
- The first rule that hits decides; rules run in the order plugins register them.

## Built-in plugins

| Plugin | What it does |
|---|---|
| `builtin:git` | branch and item of a workspace, its clean check (main branch, no tracked change), `close` syncs its repos by fast-forward, `peek` shows the tree; `merged(app, ws, item)` for a site check |
| `builtin:ports` | refuses a lease when another workspace listens on its ports, shows the listeners on the board, stops this workspace's servers on `close` (`stop_on_close = true`) |
| `builtin:relay` | closes the session's tab and, with no sibling, its window through claude-session-relay's inbox, then stops the session's process |
