"""A session of a tool with no hooks (Aider, Codex CLI, Gemini CLI, a plain terminal), registered by hand.

`signalbox register` writes the calling terminal into the state root's `sessions/` folder, in the host registry's
shape, with `host: manual`: from then on it is on the board, holds leases, writes its card, claims offers (`claim`
prints the work) and reads what was sent to it with `signalbox read`. It is alive while its process is (the shell
that ran `register`, or `--pid`); `signalbox unregister` takes it off.
"""
from __future__ import annotations

import json
import os
import time

from .util import Refuse, now, read_json, write_atomic


def folder(app):
    return app.store.root / 'sessions'


def register(app, name=None, workspace=None, pid=None, tool=None, echo=True) -> dict:
    pid = int(pid) if pid else app.procs.parent(os.getpid()) or os.getpid()
    cwd = os.getcwd()
    if workspace:
        workspace = app.workspaces.norm(workspace)
        root = app.workspaces.root(workspace)
        if not root:
            raise Refuse(f'no workspace {workspace} under [workspaces] glob: run register inside its folder')
        cwd = str(root)
    ws = app.workspaces.of(cwd)
    if not ws:
        raise Refuse(f'{cwd} is in no workspace: run register inside one, or give --workspace (signalbox doctor)')
    path = folder(app) / f'{pid}.json'
    old = read_json(path) or {}
    sid = old.get('sessionId') or f'manual-{os.urandom(6).hex()}'
    stamp_ms = int(time.time() * 1000)
    raw = {'pid': pid, 'sessionId': sid, 'cwd': cwd, 'name': name or tool or f'manual-{pid}', 'status': 'idle',
           'startedAt': old.get('startedAt') or stamp_ms, 'statusUpdatedAt': stamp_ms, 'host': 'manual',
           'tool': tool or None, 'procStart': app.procs.create_time(pid)}
    write_atomic(path, raw)
    who = {'workspace': ws, 'name': raw['name'], 'sid': sid, 'pid': pid}
    app.emit('session', who, f"registered by hand ({tool or 'manual'})", action='register', tool=tool)
    out = {'ok': True, 'sid': sid, 'workspace': ws, 'name': raw['name'], 'pid': pid}
    if echo:
        print(json.dumps(out))
    return out


def unregister(app, sid=None, echo=True) -> dict:
    gone = []
    for f in folder(app).glob('*.json'):
        raw = read_json(f) or {}
        mine = raw.get('sessionId') == sid if sid else app.procs.parent(os.getpid()) == raw.get('pid')
        if mine:
            f.unlink(missing_ok=True)
            gone.append(raw.get('sessionId'))
    if not gone:
        raise Refuse('no session registered by hand here' + (f' with sid {sid}' if sid else ''))
    out = {'ok': True, 'removed': gone, 'at': now().isoformat()}
    if echo:
        print(json.dumps(out))
    return out


def read(app, sid=None) -> list[dict]:
    """The messages waiting for the calling session, taken out of the box and printed: how a session with no hooks
    reads what a `send` or a thread turn brought it."""
    mine = app.who_of(sid)
    msgs = app.delivery.claim(mine['workspace'], mine['sid'])
    print(app.delivery.deliver(mine, msgs, 'read') if msgs else 'no message waiting for this session')
    return msgs
