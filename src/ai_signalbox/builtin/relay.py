"""`builtin:relay`: closes a session's tab and window through claude-session-relay's file inbox.

claude-session-relay (a VS Code extension) gives every window an inbox: `<root>/<workspace>/<id>.json` holding
`{"action": "close", "session": "<id>"}` or `{"action": "closeWindow"}`, answered in `<id>.done.json` with
`{"ok", "error", "tabs"}`. Name the workspaces with the same `{pattern, name}` rule in both tools.

On `close`, a detached process waits `delay` seconds (the session sends its report first), asks the window to close
this session's tab and, when no other live session stays in the workspace, the window; then it stops the session's
process (checked against its start time, so a reused pid is left alone).

    [relay]
    root = "~/.claude-session-relay"   # default: %LOCALAPPDATA%\\claude-session-relay on Windows
    timeout = 20
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..util import read_json


def default_root() -> Path:
    if os.name == 'nt' and os.environ.get('LOCALAPPDATA'):
        return Path(os.environ['LOCALAPPDATA']) / 'claude-session-relay'
    return Path.home() / '.claude-session-relay'


class Relay:
    def __init__(self, cfg):
        r = cfg['relay'] if 'relay' in cfg.data else {}
        root = r.get('root')
        self.root = Path(os.path.expandvars(os.path.expanduser(root))) if root else default_root()
        self.timeout = float(r.get('timeout') or 20)

    def request(self, workspace, action, session=None, timeout=None):
        """(ok, error) of one request to the window of the workspace."""
        box = self.root / workspace
        rid = f'{int(time.time() * 1000)}-signalbox-{os.getpid()}'
        req, done, tmp = box / f'{rid}.json', box / f'{rid}.done.json', box / f'.{rid}.json.tmp'
        try:
            box.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({'action': action, **({'session': session} if session else {})}),
                           encoding='utf-8')
            os.replace(tmp, req)
        except OSError as err:
            return False, f'the inbox did not take the request ({err})'
        end = time.time() + (timeout or self.timeout)
        while time.time() < end:
            time.sleep(0.25)
            if done.exists():
                r = read_json(done, {}) or {}
                try:
                    done.unlink()
                except OSError:
                    pass
                return (True, None) if r.get('ok') else (False, r.get('error') or 'the window did not do it')
        try:
            req.unlink()
        except OSError:
            pass
        return False, 'no window answered (is claude-session-relay installed and listening as this workspace?)'

    # ------------------------------------------------------------ close
    @staticmethod
    def procs_of(app, mine, ctx):
        pids = [mine.get('pid')] + list(ctx.get('extra_pids') or [])
        out = []
        for pid in pids:
            if pid and app.procs.alive(pid):
                out.append(f'{pid}:{app.procs.create_time(pid) or ""}')
        return out

    def plan_close(self, app, ctx):
        mine, ws, delay = ctx['mine'], ctx['workspace'], ctx['delay']
        mode = 'tab' if ctx['siblings'] else 'window'
        procs = self.procs_of(app, mine, ctx)
        what = "this session's tab" + ('' if mode == 'tab' else f' and the window of {ws}')
        if ctx['dry']:
            return [f'window: in {delay} s it would close {what} and stop the session process'], False
        ok = app.detach('window-close', ws, mine['sid'], str(delay), mode, ','.join(procs), mine.get('name') or '')
        ctx['report']['window'] = mode
        return [f'window: in {delay} s the relay closes {what}' if ok
                else 'window: the closing did NOT start; close the tab by hand'], ok

    def close_later(self, app, ws, sid, delay, mode, procs, name):
        time.sleep(float(delay))
        who = {'workspace': ws, 'name': name, 'sid': sid}
        tab_ok, tab_err = self.request(ws, 'close', sid)
        win_ok = win_err = None
        if mode == 'window':
            win_ok, win_err = self.request(ws, 'closeWindow', timeout=15)
        time.sleep(2)
        stopped, alive = [], []
        for item in [p for p in (procs or '').split(',') if p]:
            pid, _, started = item.partition(':')
            if not app.procs.alive(pid, float(started) if started else None):
                continue
            (stopped if app.procs.kill(int(pid)) else alive).append(pid)
        result = 'ok' if tab_ok and win_ok is not False and not alive else 'partial' if tab_ok or stopped else 'failed'
        text = (f"tab {'closed' if tab_ok else f'did not close ({tab_err})'}"
                + ('' if win_ok is None else f"; window {'closed' if win_ok else f'did not close ({win_err})'}")
                + (f"; stopped pid {', '.join(stopped)}" if stopped else '')
                + (f"; still alive: pid {', '.join(alive)}" if alive else ''))
        app.emit('close', who, text, action='window', result=result, tab='closed' if tab_ok else 'failed',
                 tab_error=tab_err, window=None if win_ok is None else ('closed' if win_ok else 'failed'),
                 window_error=win_err, stopped=stopped or None, alive=alive or None)
        return result


def register(api):
    api.window(Relay(api.cfg))
