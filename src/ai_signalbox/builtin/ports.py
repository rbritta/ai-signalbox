"""`builtin:ports`: who really listens on a resource's ports.

- Taking a lease is refused when another workspace (or a process outside every workspace) listens on one of the
  resource's ports: the lease would say one thing and the port another.
- The board shows the listeners next to each resource, and a conflict when the holder is not the one listening.
- `close` stops the servers this workspace started on the ports of resources marked `stop_on_close = true`, unless
  another live session stays in the workspace.

The owner of a listener is the workspace its executable, working folder or command line points into. Without psutil
(the `proc` extra) port owners are unknown and these checks are skipped.
"""
from __future__ import annotations


def listeners(app):
    """port -> (workspace, pid, name) for every port a resource names."""
    ports = app.ports()
    found = {}
    if not ports:
        return found
    for port, pid in app.procs.listeners(list(ports)).items():
        where = ' '.join(x for x in (app.procs.where(pid) or '', app.procs.cwd(pid) or '') if x)
        found[port] = (app.workspaces.of(where), pid, app.procs.name(pid) or '?')
    return found


def lease_check(app, res, mine, current):
    ports = app.ports()
    busy = {p: l for p, l in listeners(app).items() if ports.get(p) == res and l[0] != mine.get('workspace')}
    if busy:
        desc = ', '.join(app.text('ports.busy_one', port=p, owner=l[0] or app.text('ports.no_workspace'), pid=l[1])
                         for p, l in busy.items())
        return app.text('ports.busy', ports=desc)
    return None


def board_facts(app, sessions, res):
    from .. import leases
    ports = app.ports()
    real = {p: l for p, l in listeners(app).items() if ports.get(p) == res}
    if not real:
        return []
    line = 'actually: ' + ', '.join(f"{p} {l[0] or '?'} pid {l[1]}" for p, l in sorted(real.items()))
    current = leases.lease(app, res, sessions)
    if current and any(l[0] and l[0] != current.get('workspace') for l in real.values()):
        line += '  CONFLICT: a port with another workspace'
    return [line]


def stop_step(app, ctx):
    if ctx['siblings']:
        return [f"servers of this workspace: they stay, "
                f"{', '.join(s.get('name') or s['workspace'] for s in ctx['siblings'])} lives in it"]
    resources = app.resources()
    ports = app.ports()
    out = []
    for port, (owner, pid, name) in sorted(listeners(app).items()):
        if owner != ctx['workspace'] or not resources.get(ports.get(port), {}).get('stop_on_close'):
            continue
        if not ctx['dry']:
            ok = app.procs.kill(pid)
            out.append(f'{port} ({name}, pid {pid})' + ('' if ok else ': did not stop'))
        else:
            out.append(f'{port} ({name}, pid {pid})')
    ctx['report']['servers'] = out or None
    return [f"servers of this workspace {'to stop' if ctx['dry'] else 'stopped'}: {', '.join(out) if out else 'none up'}"]


def register(api):
    api.lease_check(lease_check)
    api.board_section(board_facts, resource='*')
    api.close_step('stop servers', stop_step, order=20)
