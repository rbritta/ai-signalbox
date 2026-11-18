"""`signalbox prune --days N`: remove what is done and older than N days, so the start, wake and guard hooks stop
scanning it: delivered messages, closed threads, claimed and withdrawn offers, and temp files a crash left behind.
Open threads, waiting messages, open offers, cards, leases and the event log (which rotates by itself) stay."""
from __future__ import annotations

import json
import time

from .util import Refuse


def prune(app, days, dry=False, echo=True) -> dict:
    try:
        days = float(days)
    except (TypeError, ValueError):
        raise Refuse(f'--days takes a number of days, not {days!r}') from None
    if days < 1:
        raise Refuse('--days takes 1 or more: what is younger stays')
    counts = app.store.prune(time.time() - days * 86400, dry=dry)
    if not dry:
        app.emit('prune', None, ', '.join(f'{v} {k}' for k, v in counts.items()), days=days, **counts)
    out = {'ok': True, 'dry_run': bool(dry), 'days': days, **counts}
    if echo:
        print(json.dumps(out))
    return out
