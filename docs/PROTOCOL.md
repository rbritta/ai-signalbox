# Protocol

Everything signalbox knows lives in plain files under one root (`root` in the config, `$SIGNALBOX_ROOT` overrides it).
Any program can read them; writers go through the CLI (or the library) so the locks and the event log stay right.
This page is the contract: the files, their fields, the event kinds, and the mapping to chat-thread.

## Layout

```
<root>/
  events.jsonl            the event log, one JSON object per line, only appended
  events.prev.jsonl       the previous log (rotated past events_max_bytes, 5 MB by default)
  events.lock
  status/<sid>.json       the status card of each session
  status.lock
  leases/<res>.json       the holder of a lease
  queues/<res>.json       who waits for it
  shared/<res>.json       the users of a resource's shared mode
  leases.lock             one lock for leases, queues and shared users
  priority.json           the delivery order
  threads/<id>.json       a thread between the person and a workspace
  threads/.lock
  box/<workspace>/<id>.json         a message waiting for a session of the workspace
  box/<workspace>/delivered/<id>.json   the same message once claimed
  box/<workspace>/.signal           touched on every new message (watched by every live session)
  box/<workspace>/.start-<sid>      the session's start file (watched; marks that it listens)
  box/<workspace>/.woke-<sid>       the session already woke for its start (not watched)
  box/<workspace>/.vigil-<sid>      the session's idle vigil is up
  box/<workspace>/.claim.lock       the lock that decides which claimer takes a message
  offers/<id>.json        work put up for a session to claim
  offers/claimed/<id>.json          the same offer once a session claimed it
  offers/withdrawn/<id>.json        the same offer once taken back (or expired)
  offers/.lock            the lock that decides between claimers (and a withdraw)
  sessions/<pid>.json     a session of a tool with no hooks, registered by hand (`signalbox register`)
  inbox/<workspace>.md    notes for a workspace with no live session (inbox/<workspace>.read.md once read)
  log/<YYYY-MM-DD>.md     the message log of the day
  snapshots/<sid>.json    what the pre-compaction hook kept
  state/<name>.json       free state of plugins
```

JSON files are written to a temp name (unique per process and thread) and renamed over the target, so a reader never
sees half a file; on Windows the rename is retried while a reader holds the target open. The watched files
(`.signal`, `.start-<sid>`) are rewritten in place, never renamed or removed while watched: the host keeps one watcher
per session. [schemas/](schemas) has a JSON Schema for each kind of file.

Locks: one byte at offset 0 of the `.lock` file, `msvcrt.locking` on Windows and `fcntl.flock` elsewhere, tried every
10 ms for 10 s. When the lock can't be had, a hook goes on without it (a racy write beats a stuck hook). A claim never
does: a message or an offer moves to its `delivered/` or `claimed/` folder under the lock, after reading the source
again inside it, because on Windows processes renaming one file at the same time can all succeed; the one that comes
second finds it gone and gets nothing.

## The event log

One object per line:

| Field | |
|---|---|
| `ts` | local ISO time with its offset, milliseconds |
| `kind` | what happened (below) |
| `src` | `cli`, `hook` or `transcript` (pumped by `watch` for sessions without hooks) |
| `workspace`, `name`, `sid` | the session it came from, when it came from one |
| `text` | one line, up to 240 characters (a code fence never makes the line) |
| ... | the fields of the kind; empty values are left out |

A reader keeps a byte offset: a half-written last line waits for the next read, and a file smaller than the offset was
rotated, so it is read from the start. Unknown kinds pass through; plugins may add their own.

| Kind | Fields | When |
|---|---|---|
| `session` | `action` (start, end), `source`, `reason` | a session opened or closed |
| `status` | `item`, `phase`, `waiting`, `target`, `task`, `task_phase`, `guide`, `requested`, `off_guide`, `pr`, `pr_repo`, `pr_url` | a note changed the card |
| `done` | `item`, `phase`, `task`, `target`, `job`, `step`, `type`, pr fields | the line of what a stretch did |
| `ask` | `action` (open, close), `n`, `type`, `alert`, `note` | an ask opened or closed |
| `send` | `id`, `to_workspace`, `to_sid`, `ask` | the person sent a message (it opens a thread with the same id) |
| `thread` | `action` (open, turn, close), `id`, `n`, `by` (person, session), `to_workspace`, `to_sid`, `message`, `ask` | a thread moved |
| `offer` | `action` (open, withdraw, expire, reopen), `id`, `to_workspace` (absent: any), `any_workspace` (true for any), `by`, `item`, `expires_at`, `from_sid` (reopen: the claimer that ended) | an offer was put up, taken back, ran out of time or came back to open |
| `claim` | `id`, `message`, `by`, `offered_to`, `any_workspace`, `item` | a session claimed an offer; `message` is the message and thread it became |
| `prune` | `days`, `delivered`, `threads`, `offers`, `temp` | `signalbox prune` removed what was done and old |
| `delivered` | `id`, `via` (start, signal, vigil, prompt, thread, claim, read), `thread`, `ask`, `notice` | a message reached a session |
| `lease` | `res`, `action` (hold, release), `mode`, `why`, `item`, `until`, `queue`, `next`, `result` | a lease was taken or given back |
| `queue` | `res`, `action` (want), `mode`, `why`, `position` | someone joined a queue |
| `priority` | `order`, `by`, `before`, `changed`, `told`, `reason` | the delivery order changed |
| `job` | `item`, `job`, `st`, `waits`, `step`, `toggle`, `services` | a job moved |
| `post` | `to_workspace` | a note left in an inbox |
| `close` | `action` (start, done, request, window), `to`, `to_workspace`, `leases`, `siblings`, `report`, `result`, `tab`, `window` | the close pipeline |
| `idle` | | a turn ended; the text is its last words |
| `notify` | `type` | the host notified (a permission prompt, idle) |
| `prompt` | | the person's prompt |
| `command` | `command`, `phase` | a slash command |
| `received` | `from_workspace` | a message from another session arrived |
| `skill`, `agent` | `skill` / `agent`, `phase` | a skill or an agent ran (`[events] skill_phase`, `agent_phase` map them to phases) |
| `msg`, `wait` | `to`, `to_workspace`, `head` | a message sent to another session, or a wait for it to be free |

## The status card

`status/<sid>.json`, written by `note`, `ask`, `job` and `close`:

| Field | |
|---|---|
| `workspace`, `name` | the session |
| `item` | what it works on (none: work with no item) |
| `phase`, `waiting`, `text` | the step, who it waits for, one line of what it does |
| `done`, `done_at` | the last done line |
| `task`, `tasks` | the task it is on; `{task: {phase, ts}}`, saved per item in `saved_tasks` |
| `target`, `targets` | the lane target it is on; `{target: {phase, ts, pr, pr_url}}`, saved per item in `saved_targets` |
| `guide`, `requested`, `off_guide` | the guide version; `{text, phase, ts}` of the person's request; `{from, to, task, ts}` of a passage outside the guide |
| `pr`, `pr_repo`, `pr_url` | the pull request under review |
| `priority_delivered` | when the delivered-and-clean check ran for this delivery |
| `asks`, `ask_seq` | `[{n, type, text, alert, at}]` and the last number given |
| `jobs` | `[{id, item, pid, st, start, end, note, waits, from, steps, toggles}]`, see `signalbox job --help` |
| `updated` | the last write |

## Leases

`leases/<res>.json`: `{res, mode (shared | exclusive | null), workspace, name, sid, pid, pid_started, as, item, why,
since, until}`. A holder is a session (`sid`) or a script process (`pid` with its start time, so a reused pid is not
the holder). Queue and shared entries carry the same holder fields plus `why`, `mode`, `since` and, in a queue,
`waits: true` for a `hold --wait`.

## Threads and messages

`threads/<id>.json`: `{id, workspace, sid, name, state (open | closed), opened_at, closed_at, ask, ask_text, turns: [{n,
from (person | session), at, text, message, name, sid}]}`.

A box message: `{id, workspace, sid (null: any session of the workspace), ask, ask_text, ask_type, text, at, thread,
turn, from}`; a notice instead carries `notice` (its kind) and `from`. Claiming adds `delivered_at` and `to_sid`; a
session that ends hands its unread messages back with `sid: null` and `from_sid`.

Who gets a message: the session it names while that one is alive and listens (its start file or vigil exists, or it is
mid-turn) or while it answers that session's ask; otherwise any session of the workspace that claims it first.

## Offers

`offers/<id>.json`: `{id, workspace (null: any workspace), title, text, from, at}`, and optionally `item` (the item the
claiming session's card moves to), `expires_at` (`--minutes`), `reopened` (how many times it came back) and
`reopened_at`. An app or the person puts up a piece of work (`signalbox offer [workspace] --text-file <file>`, or
`POST /api/offers` on `serve`); a session lists the open ones it may take (`signalbox offers`: its workspace's and the
ones for any) and takes one (`signalbox claim <id>`, or `claim --next` for the oldest).

Claiming moves the file to `offers/claimed/<id>.json` under `offers/.lock` with `claimed_at`, `sid`, `name`,
`claimed_workspace` and `message` (the id the message and thread take: the offer's own id the first time, a new one
after a reopen). Of two sessions claiming the same offer, or one claiming and one withdrawing, exactly one gets it.
Then the offer's text goes to the claiming session exactly as `send` would send it (a box message to that sid, `from`
as the offer's `from`, and a thread with the message's id), the claim prints it and takes it out of the box, and the
record gets `delivered: true`.

- **A failure before the box has the message** (or an interrupt): the offer goes back to `offers/` by one move, with
  the claim fields dropped.
- **A failure after it**: the offer stays claimed. The same session claiming again gets the same message (the thread
  with the message's id is the proof the send went through), never a second one.
- **The claimer ends without answering** (no session turn in the thread) and the message no longer waits in the box:
  the next `offers`, `claim` or `claim --next` puts the offer back to open, closes the old thread
  (`closed_by: offer reopened`) and emits `offer` with `action: reopen`.
- **`--minutes`**: past `expires_at`, the next `offers` or `claim` moves an open offer to `offers/withdrawn/` with
  `withdrawn_at` and `expired: true`.

`signalbox offer --withdraw <id>` moves an open offer to `offers/withdrawn/<id>.json` with `withdrawn_at`.
`signalbox offers --history` lists the claimed and withdrawn ones. With `[offers] notify = true`, a new offer is also a
notice (`notice: offer`) in the box of its workspace, or of every workspace, so an idle session that listens wakes to
it. The board lists the open offers (`offers` in `board --json`), the start context names the ones the session may
claim, and `peek` shows both the ones a session may claim and the ones it claimed (`offers: {open, claimed}` in
`peek --json`).

## Sessions registered by hand

`sessions/<pid>.json` under the root has the host registry's shape (`pid`, `sessionId`, `cwd`, `name`, `status`,
`startedAt`, `statusUpdatedAt`) plus `host: manual`, `tool` and `procStart`. `signalbox register` writes it for the
terminal it runs in (the parent process, or `--pid`); the session is alive while that process is, the same one by its
start time. It has no hooks: it reads what was sent to it with `signalbox read` and takes offers with `claim`.

## chat-thread

`signalbox thread <id> --json` and `signalbox serve` speak chat-thread's message shape:

| signalbox | chat-thread |
|---|---|
| the person's turn | `from: 'me'`, `kind: 'bubble'`, `state: 'sent'` while in the box, `'delivered'` once claimed |
| the session's turn | `from: 'other'`, `author: '<workspace> <session name>'` |
| turn n of thread T | `id: 'T.n'`, `tag: 'T'` |
| a thread | `{thread, workspace, state, title, messages}` |

`serve` routes: `GET /api/chat` (`{threads: [{thread, count, last, workspace, state, title}]}`), `GET
/api/chat/<thread>?limit&before` (`{messages}`, oldest first), `POST /api/chat/<thread>` with `{text}` (a turn), `POST
/api/chat/<workspace>` with `{text}` (a new message and thread), `GET /api/events` (Server-Sent Events, one `{thread,
state, message}` per new turn). It binds to 127.0.0.1 and needs the token (`Authorization: Bearer` or `?token=`).

## Hooks (Claude Code)

`signalbox hooks install` adds these entries to a settings file, each tagged `#signalbox` so they can be removed
without touching anything else:

| Event | Command | Notes |
|---|---|---|
| SessionStart | `hook start` | context + `watchPaths` (the workspace's `.signal` and the session's `.start-<sid>`) |
| SessionStart (compact) | `hook postcompact` | the card, asks and last prompts given back |
| PreCompact | `hook precompact` | |
| UserPromptSubmit | `hook prompt` | what waits for the session, on stdout |
| PostToolUse (SendMessage, Skill, Agent, Task) | `hook tool` | |
| Stop | `hook stop`, `hook vigil` (asyncRewake) | the vigil exits 2 with the message on stderr to wake an idle session |
| FileChanged | `hook wake` (asyncRewake) | exits 2 with the text when a watched file brought something |
| Notification | `hook notify` | |
| SessionEnd | `hook end` | unread messages go back to the workspace |
| PreToolUse (Bash, PowerShell, Edit, Write, ...) | `hook guard` | `permissionDecision: deny` with the reason; never `ask` |

A hook never fails: an error is one line on stderr and exit 0. Nothing is ever delivered into a subagent (`agent_id`).
