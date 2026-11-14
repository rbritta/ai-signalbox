"""`builtin:git`: the branch and item of a workspace, its clean check, and the repo sync step of `close`.

The repos of a workspace are `[close] repos` (relative to its root; `.` by default). Config:

    [close]
    repos = [".", "web"]
    main_branch = "main"
    remote = "origin"
    [git]
    item_pattern = '-(\\d+)(?:-|$)'     # the item a branch name carries

Sync never throws anything away: the main branch, or a branch with no commit of its own, moves by fast-forward; a
branch with its own commits stays where it is, and so does a repo where git refuses.
"""
from __future__ import annotations

import os
import re
import subprocess

from ..util import first_line

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


def git(repo, *args, timeout=60):
    """(exit code, output): stdout, or stderr when stdout is empty."""
    try:
        p = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=timeout, creationflags=NO_WINDOW)
        return p.returncode, p.stdout.strip() or p.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as err:
        return 1, str(err)


def repos(app, ws):
    root = app.workspaces.root(ws)
    if not root:
        return []
    return [(root / r).resolve() if r != '.' else root for r in app.cfg['close'].get('repos') or ['.']]


def branch(app, ws):
    for repo in repos(app, ws):
        if (repo / '.git').exists():
            code, out = git(repo, 'rev-parse', '--abbrev-ref', 'HEAD', timeout=5)
            return out if code == 0 and out else None
    return None


def item_from_branch(app, ws):
    m = re.search(app.cfg['git'].get('item_pattern') or r'-(\d+)(?:-|$)', branch(app, ws) or '')
    return m.group(1) if m else None


def workspace_clean(app, ws):
    """Every repo on the main branch with nothing modified in the tree; untracked files do not count, and a repo the
    workspace does not have counts as clean."""
    main = app.cfg['close'].get('main_branch') or 'main'
    for repo in repos(app, ws):
        if not (repo / '.git').exists():
            continue
        code, br = git(repo, 'branch', '--show-current', timeout=15)
        if code or br != main:
            return False, f"{repo.name} on {br or '?'}"
        code, out = git(repo, 'status', '--porcelain', '--untracked-files=no', timeout=30)
        if code or out:
            return False, f'{repo.name} with changes in the tree'
    return True, None


def count(repo, spec):
    code, out = git(repo, 'rev-list', '--count', spec)
    return int(out) if code == 0 and out.isdigit() else 0


def sync_repo(app, repo, dry=False):
    """(report line, {repo, before, after} when it moved)."""
    c = app.cfg['close']
    main, remote = c.get('main_branch') or 'main', c.get('remote') or 'origin'
    up = f'{remote}/{main}'
    if not (repo / '.git').exists():
        return f'{repo.name}: missing', None
    code, out = git(repo, 'fetch', remote, '--prune', '--quiet', timeout=180)
    if code:
        return f'{repo.name}: fetch failed ({first_line(out, 120)})', None
    cur = git(repo, 'rev-parse', '--abbrev-ref', 'HEAD')[1]
    if cur == 'HEAD':
        return f'{repo.name}: detached HEAD, left as it is', None
    behind, ahead = count(repo, f'HEAD..{up}'), count(repo, f'{up}..HEAD')
    if not behind:
        return f'{repo.name}: {cur} up to date with {up}' + (f', {ahead} commit(s) of its own' if ahead else ''), None
    if cur != main and ahead:
        return f'{repo.name}: {cur} with {ahead} commit(s) of its own and {behind} behind {up}, left as it is', None
    if dry:
        return f'{repo.name}: {cur} would move +{behind} commit(s) of {up} (fast-forward)', None
    before = git(repo, 'rev-parse', 'HEAD')[1]
    code, out = git(repo, 'merge', '--ff-only', up, timeout=120)
    if code:
        return f'{repo.name}: {cur}, git refused the fast-forward ({first_line(out, 120)}), left as it is', None
    after = git(repo, 'rev-parse', 'HEAD')[1]
    return f'{repo.name}: {cur} +{behind} commit(s) of {up}', {'repo': str(repo), 'before': before, 'after': after}


def sync_step(app, ctx):
    if ctx['siblings']:
        return [f"sync: skipped, {', '.join(s.get('name') or s['workspace'] for s in ctx['siblings'])} lives in the "
                "workspace"]
    if ctx['no_sync']:
        return ['sync: skipped (--no-sync)']
    lines = []
    for repo in repos(app, ctx['workspace']):
        line, moved = sync_repo(app, repo, ctx['dry'])
        lines.append(f'sync {line}')
        if moved:
            ctx['synced'].append(moved)
    ctx['report']['sync'] = [l[5:] for l in lines]
    return lines


def peek_section(app, s):
    for repo in repos(app, s['workspace']):
        if (repo / '.git').exists():
            code, out = git(repo, 'status', '--short', timeout=15)
            changed = out.splitlines() if code == 0 and out else []
            return [f'tree: {len(changed)} files changed'] + [f'  {l}' for l in changed[:15]]
    return []


def merged(app, ws, item):
    """The merge commit of an item on the remote main branch (`[git] merged_grep` with {item}), after a fetch."""
    grep = app.cfg['git'].get('merged_grep')
    if not item or not grep:
        return None
    c = app.cfg['close']
    for repo in repos(app, ws):
        if (repo / '.git').exists():
            git(repo, 'fetch', c.get('remote') or 'origin', c.get('main_branch') or 'main', '--quiet', timeout=60)
            code, out = git(repo, 'log', f"{c.get('remote') or 'origin'}/{c.get('main_branch') or 'main'}", '-E',
                            f'--grep={grep.format(item=item)}', '--format=%h %s', '-1')
            return (out.strip() or None) if code == 0 else None
    return None


def register(api):
    api.branch(branch)
    api.item_from_branch(item_from_branch)
    api.workspace_clean(workspace_clean)
    api.close_step('sync', sync_step, order=80)
    api.peek_section(peek_section)
