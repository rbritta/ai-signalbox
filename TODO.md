# TODO

- Delivery through ai-threads (`api.delivery`), the filebox staying the default: written in Python through its file
  protocol; 0.2.0 has what it needs (a delivered command, an idle tick, texts around a delivery, footers, retract,
  thread refs for a router's own conversations).
- `builtin:relay`: open a session (claude-session-relay's `new` action), not only close it.
- Host adapters for other AI coding tools (the `hosts/` interface is there; only Claude Code ships).
- A small reference viewer for the board and the threads, built on `serve`.
- Answer an ask from chat-thread with a reply markup (quick replies), not only free text.
- chat-thread's REST store: follow `/api/events` (SSE) or poll, so a new turn shows without a reload.
