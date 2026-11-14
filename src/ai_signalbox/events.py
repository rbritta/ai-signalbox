"""The event queue: one JSON object per line, only ever appended.

Every command that changes shared state and every hook appends one event, so a listener (a panel, `signalbox watch`)
wakes on the file change instead of polling. Envelope: `ts` (local ISO time with offset, ms), `kind`, `src` (`cli`,
`hook`, `transcript`), `workspace`, `name`, `sid` (the session it came from, when it came from one), `text` (one line,
240 characters), then the fields of the kind. Contract: docs/PROTOCOL.md.
"""
from __future__ import annotations

from .util import first_line, stamp

TEXT_WIDTH = 240


def who_fields(who) -> dict:
    if not who:
        return {}
    return {'workspace': who.get('workspace'), 'name': who.get('name'), 'sid': who.get('sid')}


class EventLog:
    def __init__(self, store):
        self.store = store

    def build(self, kind, who=None, text='', src='cli', ts=None, **fields) -> dict:
        event = {'ts': ts or stamp(), 'kind': kind, 'src': src}
        event.update(who_fields(who))
        event['text'] = first_line(text, TEXT_WIDTH)
        event.update({k: v for k, v in fields.items() if v not in (None, '')})
        return event

    def emit(self, kind, who=None, text='', src='cli', ts=None, **fields) -> None:
        """Append one event. Never raises: losing an event beats failing a command or a hook."""
        try:
            self.store.append_event(self.build(kind, who, text, src, ts, **fields))
        except Exception:  # noqa: BLE001
            pass

    def read(self, offset: int = 0):
        return self.store.read_events(offset)

    def all(self) -> list[dict]:
        return self.store.read_events(0)[0]
