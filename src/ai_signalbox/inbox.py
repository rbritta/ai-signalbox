"""The inbox of a workspace with no live session, and the message log of the day."""
from __future__ import annotations

import os
import re

from .sessions import label
from .util import Refuse, now


def post(app, workspace, text):
    """A note left for the workspace's next session. The workspace keeps the case it was given in the log line and the
    event text; its file and the event's `to_workspace` are lower case."""
    if not (text or '').strip():
        raise Refuse(app.text('inbox.empty_note'))
    ws = app.workspaces.norm(workspace)
    mine = app.me()
    app.store.inbox_append(ws, app.text('inbox.note_line', date=f'{now():%Y-%m-%d %H:%M}', who=label(mine), text=text))
    app.log_line(app.text('inbox.log_line', time=f'{now():%H:%M:%S}', who=label(mine), workspace=workspace, text=text))
    app.emit('post', mine, app.text('inbox.ev_post', workspace=workspace, text=text), to_workspace=ws)
    print(app.text('inbox.posted', workspace=workspace))


def inbox(app, workspace=None):
    workspace = app.workspaces.norm(workspace) if workspace else app.workspaces.of(os.getcwd())
    if not workspace:
        raise Refuse(app.text('inbox.no_workspace'))
    text = app.store.inbox_take(workspace)
    print(text if text else app.text('inbox.empty'))
    return text


def log(app, n=20):
    text = app.store.log_read(f'{now():%Y-%m-%d}')
    if not text:
        print(app.text('log.none'))
        return []
    entries = re.split(r'\n(?=- )', text)
    print('\n'.join(entries[-n:]))
    return entries[-n:]
