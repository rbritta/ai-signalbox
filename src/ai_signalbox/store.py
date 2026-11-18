"""Where the shared state lives.

`Store` is the interface every module writes through; records are plain dicts in the core schema (docs/PROTOCOL.md).
`FileStore` keeps them as JSON files under the root. A plugin can register another store (`api.store(...)`) that maps
the same records to a different layout and field names, which is how an existing deployment keeps its files while it
moves to this library.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

from . import locks
from .util import now, read_json, replace_retrying, write_atomic


def claim_file(src, dst, update, lock):
    """Move `src` to `dst` once among any number of processes and threads, and write `update(record)` there. Returns
    the written record, or None when another one took it first (or it is gone, or the lock could not be had).

    The rename can't decide alone: on Windows, processes renaming the same source at the same time can all succeed.
    The lock decides, and inside it the source is read again: whoever comes second finds it gone."""
    src, dst = Path(src), Path(dst)
    try:
        with locks.file_lock(lock, required=True):
            record = read_json(src)
            if not record:
                return None
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                replace_retrying(src, dst)
            except FileNotFoundError:
                return None
            record = update(record)
            write_atomic(dst, record)
            return record
    except locks.LockTimeout:
        return None


class Store:
    """The records and the locks around them. Every method is overridable."""

    root: Path

    # ------------------------------------------------------------ locks
    def status_lock(self):
        raise NotImplementedError

    def leases_lock(self):
        raise NotImplementedError

    def threads_lock(self):
        raise NotImplementedError

    def status_lock_depth(self) -> int:
        return 0

    # ------------------------------------------------------------ events
    def append_event(self, event: dict) -> None:
        raise NotImplementedError

    def read_events(self, offset: int = 0) -> tuple[list[dict], int]:
        raise NotImplementedError

    def scan_events(self, contains: tuple[str, ...] = ()) -> Iterator[dict]:
        """Every event, oldest first, rotated file included; `contains` are substrings of the raw line that a line must
        have (a fast prefilter, applied to the core serialization)."""
        raise NotImplementedError

    def events_size(self) -> int:
        return 0

    def tail_events(self, size: int) -> list[dict]:
        """The events in the last `size` bytes of the current file (the first, partial line dropped)."""
        raise NotImplementedError

    # ------------------------------------------------------------ status cards
    def get_status(self, sid: str) -> dict: raise NotImplementedError
    def put_status(self, sid: str, card: dict) -> None: raise NotImplementedError
    def list_status(self) -> dict[str, dict]: raise NotImplementedError

    # ------------------------------------------------------------ leases
    def get_lease(self, res: str): raise NotImplementedError
    def put_lease(self, res: str, data) -> None: raise NotImplementedError
    def get_queue(self, res: str) -> list: raise NotImplementedError
    def put_queue(self, res: str, queue: list) -> None: raise NotImplementedError
    def get_shared(self, res: str) -> list: raise NotImplementedError
    def put_shared(self, res: str, users: list) -> None: raise NotImplementedError

    # ------------------------------------------------------------ priority
    def get_priority(self) -> dict: raise NotImplementedError
    def put_priority(self, data: dict) -> None: raise NotImplementedError

    # ------------------------------------------------------------ threads (conversations)
    def get_thread(self, tid: str): raise NotImplementedError
    def put_thread(self, thread: dict) -> None: raise NotImplementedError
    def list_threads(self) -> list[dict]: raise NotImplementedError

    # ------------------------------------------------------------ delivery box
    def box_put(self, workspace: str, msg: dict) -> None: raise NotImplementedError
    def box_list(self, workspace: str) -> list[tuple[str, dict]]: raise NotImplementedError
    def box_claim(self, workspace: str, key: str, extra: dict): raise NotImplementedError
    def box_update(self, workspace: str, key: str, msg: dict) -> None: raise NotImplementedError
    def delivered(self, since: float = 0) -> Iterator[dict]: raise NotImplementedError
    def signal_path(self, workspace: str) -> Path: raise NotImplementedError
    def start_path(self, workspace: str, sid: str) -> Path: raise NotImplementedError
    def woke_path(self, workspace: str, sid: str) -> Path: raise NotImplementedError
    def vigil_path(self, workspace: str, sid: str) -> Path: raise NotImplementedError

    # ------------------------------------------------------------ offers
    def offer_put(self, offer: dict) -> None: raise NotImplementedError
    def offer_get(self, oid: str, state: str = 'open'): raise NotImplementedError
    def offer_list(self, state: str = 'open') -> list[dict]: raise NotImplementedError
    def offer_move(self, oid: str, src: str, dst: str, extra: dict, drop=()): raise NotImplementedError
    def offer_update(self, oid: str, state: str, extra: dict): raise NotImplementedError

    # ------------------------------------------------------------ requests to the person-facing app
    def get_request(self, rid: str): raise NotImplementedError
    def put_request(self, req: dict) -> None: raise NotImplementedError
    def list_requests(self) -> list[dict]: raise NotImplementedError

    # ------------------------------------------------------------ inbox, log, snapshots, plugin state
    def inbox_append(self, workspace: str, line: str) -> None: raise NotImplementedError
    def inbox_take(self, workspace: str): raise NotImplementedError
    def inbox_pending(self) -> list[str]: raise NotImplementedError
    def log_append(self, text: str) -> None: raise NotImplementedError
    def log_read(self, day: str): raise NotImplementedError
    def get_snapshot(self, sid: str): raise NotImplementedError
    def put_snapshot(self, sid: str, data: dict) -> None: raise NotImplementedError
    def get_state(self, name: str) -> dict: raise NotImplementedError
    def put_state(self, name: str, data: dict) -> None: raise NotImplementedError
    def path(self, *parts) -> Path:
        return self.root.joinpath(*parts)


class FileStore(Store):
    """JSON files under the root:

    events.jsonl (+ events.prev.jsonl), status/<sid>.json, leases/ queues/ shared/<res>.json, priority.json,
    threads/<id>.json, box/<workspace>/<id>.json (+ delivered/), offers/<id>.json (+ claimed/, withdrawn/),
    requests/<id>.json, inbox/<workspace>.md, log/<day>.md, snapshots/<sid>.json, state/<name>.json, and a .lock file
    per group.
    """

    def __init__(self, root, events_max_bytes: int = 5_000_000):
        self.root = Path(root)
        self.events_max = events_max_bytes

    # ------------------------------------------------------------ locks
    def status_lock(self):
        return locks.reentrant_lock(self.root / 'status.lock')

    def status_lock_depth(self) -> int:
        return locks.depth(self.root / 'status.lock')

    def leases_lock(self):
        return locks.file_lock(self.root / 'leases.lock')

    def threads_lock(self):
        return locks.file_lock(self.root / 'threads' / '.lock')

    # ------------------------------------------------------------ events
    @property
    def events_file(self) -> Path:
        return self.root / 'events.jsonl'

    @property
    def events_prev(self) -> Path:
        return self.root / 'events.prev.jsonl'

    def append_event(self, event: dict) -> None:
        line = (json.dumps(event, ensure_ascii=False) + '\n').encode('utf-8')
        self.root.mkdir(parents=True, exist_ok=True)
        with locks.file_lock(self.root / 'events.lock'):
            if self.events_file.exists() and self.events_file.stat().st_size > self.events_max:
                try:
                    self.events_file.replace(self.events_prev)
                except OSError:
                    pass  # a reader holds it open; rotate on a later write
            with open(self.events_file, 'ab') as fh:
                fh.write(line)

    def read_events(self, offset: int = 0):
        """(events, next offset). A half-written last line waits for the next read; a file smaller than the offset was
        rotated, so it is read from the start."""
        try:
            if self.events_file.stat().st_size < offset:
                offset = 0
            with open(self.events_file, 'rb') as fh:
                fh.seek(offset)
                data = fh.read()
        except OSError:
            return [], offset
        end = data.rfind(b'\n') + 1
        out = []
        for raw in data[:end].splitlines():
            try:
                out.append(json.loads(raw.decode('utf-8', 'replace')))
            except ValueError:
                pass
        return out, offset + end

    def scan_events(self, contains=()):
        for path in (self.events_prev, self.events_file):
            try:
                with open(path, encoding='utf-8', errors='replace') as fh:
                    for line in fh:
                        if any(c not in line for c in contains):
                            continue
                        try:
                            yield json.loads(line)
                        except ValueError:
                            continue
            except OSError:
                continue

    def events_size(self) -> int:
        try:
            return self.events_file.stat().st_size
        except OSError:
            return 0

    def tail_events(self, size):
        try:
            total = self.events_file.stat().st_size
            with open(self.events_file, 'rb') as fh:
                fh.seek(max(0, total - size))
                lines = fh.read().decode('utf-8', 'replace').splitlines()
        except OSError:
            return []
        if total > size:
            lines = lines[1:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    # ------------------------------------------------------------ status
    def get_status(self, sid):
        return read_json(self.root / 'status' / f'{sid}.json', {}) or {}

    def put_status(self, sid, card):
        write_atomic(self.root / 'status' / f'{sid}.json', card)

    def list_status(self):
        folder = self.root / 'status'
        return {f.stem: read_json(f, {}) or {} for f in folder.glob('*.json')} if folder.exists() else {}

    # ------------------------------------------------------------ leases
    def get_lease(self, res):
        return read_json(self.root / 'leases' / f'{res}.json')

    def put_lease(self, res, data):
        path = self.root / 'leases' / f'{res}.json'
        if data is None:
            path.unlink(missing_ok=True)
        else:
            write_atomic(path, data)

    def get_queue(self, res):
        return read_json(self.root / 'queues' / f'{res}.json', []) or []

    def put_queue(self, res, queue):
        write_atomic(self.root / 'queues' / f'{res}.json', queue)

    def get_shared(self, res):
        return read_json(self.root / 'shared' / f'{res}.json', []) or []

    def put_shared(self, res, users):
        write_atomic(self.root / 'shared' / f'{res}.json', users)

    # ------------------------------------------------------------ priority
    def get_priority(self):
        return read_json(self.root / 'priority.json') or {}

    def put_priority(self, data):
        write_atomic(self.root / 'priority.json', data)

    # ------------------------------------------------------------ threads
    def get_thread(self, tid):
        return read_json(self.root / 'threads' / f'{tid}.json')

    def put_thread(self, thread):
        write_atomic(self.root / 'threads' / f"{thread['id']}.json", thread)

    def list_threads(self):
        folder = self.root / 'threads'
        if not folder.exists():
            return []
        return [t for t in (read_json(f) for f in folder.glob('*.json')) if t]

    # ------------------------------------------------------------ box
    def box_dir(self, workspace):
        return self.root / 'box' / workspace

    def box_put(self, workspace, msg):
        write_atomic(self.box_dir(workspace) / f"{msg['id']}.json", msg)

    def box_list(self, workspace):
        """The messages waiting in a workspace's box, oldest first (ids of the same second by the time they were
        written)."""
        folder = self.box_dir(workspace)
        try:
            names = [n for n in os.listdir(folder) if n.endswith('.json')]
        except OSError:
            return []
        out = []
        for name in names:
            m = read_json(folder / name)
            if m:
                try:
                    written = (folder / name).stat().st_mtime_ns
                except OSError:
                    written = 0
                out.append((name[:15], written, name, m))
        return [(name, m) for _, _, name, m in sorted(out, key=lambda r: r[:3])]

    def box_claim(self, workspace, key, extra):
        """Move one message to delivered/ once: two hooks of a session, or two sessions, never deliver it twice."""
        folder = self.box_dir(workspace)
        return claim_file(folder / key, folder / 'delivered' / key, lambda m: {**m, **extra}, folder / '.claim.lock')

    def box_update(self, workspace, key, msg):
        write_atomic(self.box_dir(workspace) / key, msg)

    def delivered(self, since=0):
        for path in (self.root / 'box').glob('*/delivered/*.json'):
            try:
                if path.stat().st_mtime < since:
                    continue
            except OSError:
                continue
            m = read_json(path)
            if m:
                yield m

    def signal_path(self, workspace):
        return self.box_dir(workspace) / '.signal'

    def start_path(self, workspace, sid):
        return self.box_dir(workspace) / f'.start-{sid}'

    def woke_path(self, workspace, sid):
        return self.box_dir(workspace) / f'.woke-{sid}'

    def vigil_path(self, workspace, sid):
        return self.box_dir(workspace) / f'.vigil-{sid}'

    # ------------------------------------------------------------ offers
    def offer_dir(self, state='open'):
        return self.root / 'offers' if state == 'open' else self.root / 'offers' / state

    def offer_put(self, offer):
        write_atomic(self.offer_dir() / f"{offer['id']}.json", offer)

    def offer_get(self, oid, state='open'):
        return read_json(self.offer_dir(state) / f'{oid}.json')

    def offer_list(self, state='open'):
        folder = self.offer_dir(state)
        if not folder.exists():
            return []
        found = (o for o in (read_json(f) for f in folder.glob('*.json')) if o)
        return sorted(found, key=lambda o: (o.get('at') or '', o.get('id') or ''))

    def offer_lock(self):
        return self.offer_dir() / '.lock'

    def offer_move(self, oid, src, dst, extra, drop=()):
        """Move an offer from one state folder to another once: of two sessions claiming it (or one claiming and one
        withdrawing), one gets the offer and the other None. `extra` adds fields, `drop` removes them."""
        def update(o):
            o = {k: v for k, v in o.items() if k not in drop}
            o.update(extra)
            return o
        return claim_file(self.offer_dir(src) / f'{oid}.json', self.offer_dir(dst) / f'{oid}.json', update,
                          self.offer_lock())

    def offer_update(self, oid, state, extra):
        """Add fields to an offer where it is. Returns it, or None when it is not in that state."""
        try:
            with locks.file_lock(self.offer_lock(), required=True):
                path = self.offer_dir(state) / f'{oid}.json'
                o = read_json(path)
                if not o:
                    return None
                o.update(extra)
                write_atomic(path, o)
                return o
        except locks.LockTimeout:
            return None

    # ------------------------------------------------------------ requests
    def get_request(self, rid):
        return read_json(self.root / 'requests' / f'{rid}.json')

    def put_request(self, req):
        write_atomic(self.root / 'requests' / f"{req['id']}.json", req)

    def list_requests(self):
        folder = self.root / 'requests'
        return [r for r in (read_json(f) for f in folder.glob('*.json')) if r] if folder.exists() else []

    # ------------------------------------------------------------ inbox, log
    def inbox_append(self, workspace, line):
        path = self.root / 'inbox' / f'{workspace}.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(line)

    def inbox_take(self, workspace):
        path = self.root / 'inbox' / f'{workspace}.md'
        if not path.exists():
            return None
        text = path.read_text(encoding='utf-8')
        with open(self.root / 'inbox' / f'{workspace}.read.md', 'a', encoding='utf-8') as fh:
            fh.write(text)
        path.unlink()
        return text

    def inbox_pending(self):
        folder = self.root / 'inbox'
        return sorted(f.stem for f in folder.glob('*.md') if not f.stem.endswith('.read')) if folder.exists() else []

    #: the first line of a day's log file
    log_title = '# Message log, {day}\n\n'

    def log_path(self, day: str) -> Path:
        return self.root / 'log' / f'{day}.md'

    def log_append(self, text):
        day = f'{now():%Y-%m-%d}'
        path = self.log_path(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(self.log_title.format(day=day), encoding='utf-8')
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(text)

    def log_read(self, day):
        path = self.log_path(day)
        return path.read_text(encoding='utf-8') if path.exists() else None

    # ------------------------------------------------------------ snapshots, state
    def get_snapshot(self, sid):
        return read_json(self.root / 'snapshots' / f'{sid}.json')

    def put_snapshot(self, sid, data):
        write_atomic(self.root / 'snapshots' / f'{sid}.json', data)

    def get_state(self, name):
        return read_json(self.root / 'state' / f'{name}.json', {}) or {}

    def put_state(self, name, data):
        write_atomic(self.root / 'state' / f'{name}.json', data)
