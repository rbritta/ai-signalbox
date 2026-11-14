"""Host adapters: how sessions of one AI coding tool are found, read and hooked. Claude Code is the first."""
from __future__ import annotations


def make_host(app):
    kind = app.cfg['host'].get('type') or 'claude_code'
    factory = app.registry.hosts.get(kind)
    if factory:
        return factory(app)
    if kind == 'claude_code':
        from .claude_code import ClaudeCodeHost
        return ClaudeCodeHost(app)
    raise ValueError(f'unknown host type: {kind}')
