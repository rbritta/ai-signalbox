"""The command line: `signalbox <command> ...` (or `python -m ai_signalbox`).

Free text never rides in the shell: every command that takes one reads it from `--text-file <file>` (or `-` for stdin,
for programs that pipe it). An option a command does not know is an error with the usage, never the text. Read commands
take `--json`. A refusal prints its reason on stderr and exits 1; nothing changed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .util import Refuse, clean_text

HIDDEN = argparse.SUPPRESS


def _text(args, attr='text', required=False, what='text'):
    """The free text of a command: --text-file, a lone `-` (stdin), or the positional words."""
    path = getattr(args, 'text_file', None)
    words = getattr(args, attr, None)
    if isinstance(words, str):
        words = [words]
    if path:
        try:
            raw = Path(path).read_bytes().decode('utf-8-sig', 'replace')
        except OSError as err:
            raise Refuse(f'--text-file: could not read {path!r} ({err})') from None
        return clean_text(raw)
    if words == ['-']:
        return clean_text(sys.stdin.buffer.read().decode('utf-8', 'replace') if hasattr(sys.stdin, 'buffer')
                          else sys.stdin.read())
    text = ' '.join(words or []).strip()
    if required and not text:
        raise Refuse(f'{what}: give it with --text-file <file>')
    return text or None


def _file_or(value, path, what):
    if path:
        try:
            return clean_text(Path(path).read_bytes().decode('utf-8-sig', 'replace'))
        except OSError as err:
            raise Refuse(f'{what}: could not read {path!r} ({err})') from None
    return value


def build_parser(app=None):
    p = argparse.ArgumentParser(prog='signalbox', description='A signal box for many AI coding sessions.',
                                allow_abbrev=False)
    p.add_argument('--version', action='version', version=f'ai-signalbox {__version__}')
    p.add_argument('--config', help='the TOML config (default: $SIGNALBOX_CONFIG or ~/.signalbox/config.toml)')
    sub = p.add_subparsers(dest='cmd', metavar='<command>')

    def cmd(name, help_, **kw):
        return sub.add_parser(name, help=help_, allow_abbrev=False, **kw)

    def text_opts(parser, positional=True):
        parser.add_argument('--text-file', help='the text, from a file (free text never rides in the shell)')
        if positional:
            parser.add_argument('text', nargs='*', help=argparse.SUPPRESS)

    c = cmd('board', 'everything at once')
    c.add_argument('--json', action='store_true')
    c = cmd('peek', 'what one session (or workspace) is doing')
    c.add_argument('target')
    c.add_argument('--json', action='store_true')

    c = cmd('note', 'the calling session\'s status: item, phase, task, waiting, done line')
    c.add_argument('--item')
    c.add_argument('--phase')
    c.add_argument('--task')
    c.add_argument('--pr')
    c.add_argument('--target')
    c.add_argument('--waiting')
    c.add_argument('--done')
    c.add_argument('--done-file')
    c.add_argument('--requested')
    c.add_argument('--requested-file')
    c.add_argument('--sid', help=HIDDEN)
    text_opts(c)

    c = cmd('ask', 'an ask of the calling session for the person; --close N; no text: list')
    c.add_argument('--type')
    c.add_argument('--alert', action='store_true')
    c.add_argument('--close', metavar='N')
    c.add_argument('--sid')
    text_opts(c)

    c = cmd('send', 'the person\'s message to a workspace (opens a thread)')
    c.add_argument('workspace')
    c.add_argument('--sid')
    c.add_argument('--ask', metavar='N')
    c.add_argument('--from', dest='source', help='how the message names who sent it')
    text_opts(c)

    c = cmd('thread', 'a thread: read it, write a turn, list the open ones')
    c.add_argument('id', nargs='?')
    c.add_argument('--from-person', action='store_true')
    c.add_argument('--close', action='store_true')
    c.add_argument('--json', action='store_true')
    c.add_argument('--sid')
    text_opts(c)

    c = cmd('job', 'a long job of the session, with steps')
    c.add_argument('name', nargs='?')
    c.add_argument('end_note', nargs='*', help=argparse.SUPPRESS)
    c.add_argument('--steps')
    c.add_argument('--step')
    c.add_argument('--wait', nargs='?', const='session', choices=['session', 'person'])
    c.add_argument('--end', choices=['ok', 'failed'])
    c.add_argument('--done')
    c.add_argument('--done-file')
    c.add_argument('--at')
    c.add_argument('--duration')
    c.add_argument('--services')
    c.add_argument('--toggle')
    c.add_argument('--item')
    c.add_argument('--pid')
    c.add_argument('--log')
    c.add_argument('--from', dest='from_')
    c.add_argument('--sid', help=HIDDEN)

    c = cmd('flow', 'the guide\'s flow block, as signalbox reads it')
    c.add_argument('--json', action='store_true')

    c = cmd('hold', 'take a lease')
    c.add_argument('res')
    c.add_argument('why', nargs='*')
    c.add_argument('--minutes', type=int)
    c.add_argument('--shared', action='store_true')
    c.add_argument('--wait', type=float, metavar='SECONDS')
    c.add_argument('--pid')
    c.add_argument('--as', dest='as_')
    c.add_argument('--item')
    c = cmd('want', 'join the queue of a lease')
    c.add_argument('res')
    c.add_argument('why', nargs='*')
    c.add_argument('--shared', action='store_true')
    c = cmd('release', 'give a lease back')
    c.add_argument('res')
    c.add_argument('--shared', action='store_true')
    c.add_argument('--pid')
    c.add_argument('--result')
    c.add_argument('--item')

    c = cmd('priority', 'the delivery order (no workspaces: show it as JSON)')
    c.add_argument('order', nargs='*')
    c.add_argument('--by')

    c = cmd('post', 'a note for a workspace with no live session')
    c.add_argument('workspace')
    text_opts(c)
    c = cmd('inbox', 'read the inbox of this workspace')
    c.add_argument('workspace', nargs='?')
    c = cmd('log', 'the message log of today')
    c.add_argument('-n', type=int, default=20)
    c = cmd('events', 'events of every session, read from the transcripts')
    c.add_argument('-n', type=int, default=40)
    c.add_argument('--workspace')
    c.add_argument('--json', action='store_true')
    c = cmd('watch', 'one line per new event of the other sessions')
    c.add_argument('--interval', type=float, default=5.0)

    c = cmd('close', 'close the calling session for good, or ask another one to close')
    c.add_argument('target', nargs='?')
    c.add_argument('--done')
    c.add_argument('--done-file')
    c.add_argument('--dry-run', action='store_true')
    c.add_argument('--no-sync', action='store_true')
    c.add_argument('--keep-window', action='store_true')
    c.add_argument('--delay', type=int)

    c = cmd('hook', 'run a hook (the host calls it)')
    c.add_argument('name')
    c = cmd('hooks', 'install, remove or check the hooks in a host settings file')
    c.add_argument('action', choices=['install', 'remove', 'status'])
    c.add_argument('--settings', help='the settings file (default: <first host config dir>/settings.json)')
    c.add_argument('--command', default=None, help='how the host runs signalbox (default: this Python)')
    c.add_argument('--no-guard', action='store_true')

    c = cmd('serve', 'the threads over HTTP, in chat-thread\'s REST store protocol')
    c.add_argument('--host')
    c.add_argument('--port', type=int)
    c.add_argument('--token')

    c = sub.add_parser('start-touch')  # detached by the start hook
    c.add_argument('workspace')
    c.add_argument('sid')
    c = sub.add_parser('window-close')  # detached by close
    for a in ('workspace', 'sid', 'delay', 'mode', 'procs', 'name'):
        c.add_argument(a, nargs='?' if a in ('procs', 'name') else None, default='')

    if app is not None:
        for name, (configure, _run, help_) in app.registry.commands.items():
            c = cmd(name, help_ or name)
            if configure:
                configure(c)
    return p


def run(app, args) -> int:  # noqa: C901 - one dispatch table
    from . import close as close_mod
    from . import hooks, inbox, jobs, leases, priority, status, threads, views
    a = args
    if a.cmd in (None, 'board'):
        views.board(app, getattr(a, 'json', False))
    elif a.cmd == 'peek':
        views.peek(app, a.target, a.json)
    elif a.cmd == 'note':
        status.note(app, item=a.item, phase=a.phase, waiting=a.waiting, text=_text(a), pr=a.pr, task=a.task,
                    done=_file_or(a.done, a.done_file, '--done-file'),
                    requested=_file_or(a.requested, a.requested_file, '--requested-file'), target=a.target, sid=a.sid)
    elif a.cmd == 'ask':
        text = _text(a)
        if a.close is not None:
            threads.ask(app, close=a.close, note=text, sid=a.sid)
        else:
            threads.ask(app, text=text, type_=a.type, alert=a.alert, sid=a.sid)
    elif a.cmd == 'send':
        threads.send(app, a.workspace, _text(a, required=True, what='the message'), sid=a.sid, ask_n=a.ask,
                     source=a.source)
    elif a.cmd == 'thread':
        text = _text(a) if (a.text_file or a.text) else None
        threads.thread(app, a.id, text, from_person=a.from_person, close=a.close, sid=a.sid, as_json=a.json)
    elif a.cmd == 'job':
        jobs.job(app, a.name, steps=a.steps, step=a.step, end=a.end, note=' '.join(a.end_note) or None, item=a.item,
                 pid=a.pid, at=a.at, wait=a.wait, done=_file_or(a.done, a.done_file, '--done-file'), from_=a.from_,
                 duration=a.duration, toggle=a.toggle, services=a.services, log=a.log, sid=a.sid)
    elif a.cmd == 'flow':
        import json
        from .workflow import Workflow
        wf = Workflow(app)
        g = wf.read()
        if not g:
            raise Refuse(f'no ```{app.cfg["workflow"].get("fence") or "flow"} block in {wf.path}')
        print(json.dumps(wf.describe(g), ensure_ascii=False, indent=2) if a.json else '\n'.join(wf.lines(g)))
    elif a.cmd == 'hold':
        leases.hold(app, a.res, ' '.join(a.why) or None, a.minutes, a.shared, a.pid, a.as_, a.wait, a.item)
    elif a.cmd == 'want':
        leases.want(app, a.res, ' '.join(a.why) or None, a.shared)
    elif a.cmd == 'release':
        leases.release(app, a.res, a.shared, a.pid, a.result, a.item)
    elif a.cmd == 'priority':
        priority.set_order(app, a.order, a.by)
    elif a.cmd == 'post':
        inbox.post(app, a.workspace, _text(a, required=True, what='the note'))
    elif a.cmd == 'inbox':
        inbox.inbox(app, a.workspace)
    elif a.cmd == 'log':
        inbox.log(app, a.n)
    elif a.cmd == 'events':
        views.events(app, a.n, a.workspace, a.json)
    elif a.cmd == 'watch':
        views.watch(app, a.interval)
    elif a.cmd == 'close':
        done = _file_or(a.done, a.done_file, '--done-file')
        close_mod.close(app, a.target, done, a.dry_run, a.no_sync, a.keep_window, a.delay)
    elif a.cmd == 'hook':
        return hooks.run(app, a.name)
    elif a.cmd == 'hooks':
        settings = Path(a.settings) if a.settings else app.cfg.config_dirs[0] / 'settings.json'
        command = a.command or f'"{sys.executable}" -m ai_signalbox' + (f' --config "{app.cfg.path}"' if app.cfg.path
                                                                       else '')
        if a.action == 'install':
            events = app.host.install(settings, command, guard=not a.no_guard)
            print(f"hooks installed in {settings}: {', '.join(events)}")
        elif a.action == 'remove':
            print(f'{app.host.remove(settings)} hook entries removed from {settings}')
        else:
            events = app.host.status(settings)
            print(f"{settings}: {', '.join(events) if events else 'no signalbox hook'}")
    elif a.cmd == 'serve':
        from .serve import Server
        Server(app, a.host, a.port, a.token).start().serve_forever()
    elif a.cmd == 'start-touch':
        if hasattr(app.delivery, 'start_touch'):
            app.delivery.start_touch(a.workspace, a.sid)
    elif a.cmd == 'window-close':
        if app.registry.window is not None and hasattr(app.registry.window, 'close_later'):
            app.registry.window.close_later(app, a.workspace, a.sid, a.delay or 0, a.mode, a.procs, a.name)
    elif a.cmd in app.registry.commands:
        return app.registry.commands[a.cmd][1](app, a) or 0
    return 0


def main(argv=None, app=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8')
            except (ValueError, OSError):
                pass
    argv = list(sys.argv[1:] if argv is None else argv)
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument('--config')
    known, rest = pre.parse_known_args(argv)
    if app is None:
        from .app import App
        from .config import Config
        try:
            app = App(Config.load(known.config))
        except (OSError, ValueError) as err:
            print(f'signalbox: {err}', file=sys.stderr)
            return 1
    parser = build_parser(app)
    if rest and rest[0] == 'hook':  # a hook never fails on its arguments
        name = rest[1] if len(rest) > 1 else ''
        from . import hooks
        code = hooks.run(app, name)
        return 2 if code == 2 else 0
    args = parser.parse_args(argv)
    try:
        return run(app, args) or 0
    except Refuse as err:
        print(str(err), file=sys.stdout if getattr(err, 'stdout', False) else sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
