# Changelog

## 0.1.0 (2026-11-14)

First release.

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
