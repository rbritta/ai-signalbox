# TODO

Plans, not promises: none of this is in the code yet.

- Delivery through ai-threads (`api.delivery`), the filebox staying the default, written in Python through its file
  protocol. It needs from signalbox: a command to mark a message delivered, an idle tick, the texts around a delivery,
  footers, a retract, and thread refs for a router's own conversations.
- `builtin:relay`: open a session (claude-session-relay's `new` action), not only close it.
- Host adapters for other AI coding tools with hooks (the `hosts/` interface is there; only Claude Code ships, and
  `register` covers the tools with none).
- A small reference viewer for the board, the threads and the offers, built on `serve`.
- Answer an ask from chat-thread with a reply markup (quick replies), not only free text.
- chat-thread's REST store: follow `/api/events` (SSE) or poll, so a new turn shows without a reload.
- Move the remaining inline sentences (board, threads, close, serve) to `texts.py` keys.
