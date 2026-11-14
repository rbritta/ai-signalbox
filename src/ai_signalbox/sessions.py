"""Sessions and holders.

A session is a dict with `sid`, `workspace`, `name`, `pid`, `status` (busy, idle), `started_at`, `status_updated_at`,
`cwd`, `socket`, and, once loaded, `card` (its status card). The host adapter builds them.

A holder (of a lease, a queue place, a shared use) is a session, or a script process identified by `pid` and its start
time `pid_started`, with an optional display name `as`.
"""
from __future__ import annotations

HOLDER_KEYS = ('workspace', 'name', 'sid', 'pid', 'pid_started', 'as', 'item')


def label(s) -> str:
    if not s:
        return '?'
    if not s.get('workspace') and s.get('as'):
        return s['as']  # a script run outside the workspaces: its own name says who
    base = f"{s.get('workspace') or '?'} {s.get('name') or ''}".strip()
    return f"{base} ({s['as']})" if s.get('as') else base


def find_target(sessions, target: str) -> list[dict]:
    t = (target or '').lower()
    return [s for s in sessions if (s.get('name') or '').lower() == t or s.get('workspace') == t]


def who(s) -> dict | None:
    """The identity fields of a session, as events and records carry them."""
    if not s:
        return None
    return {'workspace': s.get('workspace'), 'name': s.get('name'), 'sid': s.get('sid')}


def holder_fields(h) -> dict:
    return {k: h.get(k) for k in HOLDER_KEYS}


def same(a, b) -> bool:
    """The same holder: the same script process, or the same session when neither is a script."""
    if a.get('pid') or b.get('pid'):
        return bool(a.get('pid')) and a.get('pid') == b.get('pid')
    return bool(a.get('sid')) and a.get('sid') == b.get('sid')


def waiting_for_person(card, words) -> bool:
    w = (card or {}).get('waiting') or ''
    return any(word.lower() in w.lower() for word in words)
