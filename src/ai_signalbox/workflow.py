"""The workflow: a ```flow block in a markdown guide, the one reader of it, and the transition check.

The block, one statement per line (`#` starts a comment, which on a `loose` line is its note):

    version 2026-01-01.1
    stage   build   plan implement verify        # a stage and its phases, in order
    chain   plan > implement > verify > review   # passages; `a|b` either, `a+b` both done before the next
    free    validate <> review                   # either order, back and forth
    parallel verify > validate                   # opens the next with any of them done
    lane    review > target > backport           # a side track that runs once per target (note --target)
    loose   hotfix                               # a phase that comes at any time, never checked
    return  * > implement (before the merge)     # going back; the condition may name a phase it must come before
    person  review validate                      # the person's steps
    gate    review                               # the steps that wait for the person's ok

Keywords can be aliased in the config (`[workflow.keywords]`, e.g. `step = "stage"`), so a guide written with other words,
or in another language, reads as is. A `## Changes` table (version | date | what changed | asked by) becomes the change log.

The check never blocks: a passage outside the guide is a warning and a mark on the status card.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

KEYWORDS = ('version', 'stage', 'chain', 'parallel', 'lane', 'free', 'loose', 'return', 'person', 'gate')


class Workflow:
    """The guide of one deployment, read through its config."""

    def __init__(self, app):
        self.app = app
        w = app.cfg['workflow']
        self.cfg = w
        self.path: Path | None = app.cfg.guide
        self.neutral = tuple(w.get('neutral') or ())
        self.variant = tuple(w.get('variant') or ())
        self.task_ok = w.get('task_ok') or 'ok'
        self.renamed = dict(w.get('renamed') or {})
        self.renamed_in = dict(w.get('renamed_in') or {})
        self.keep_target = tuple(w.get('keep_target') or ())
        self.resume = w.get('resume_phase') or 'resume'
        self.resume_next = w.get('resume_next') or 'verify'
        self.untracked = tuple(w.get('untracked_items') or ())
        aliases = {k.lower(): v for k, v in (w.get('keywords') or {}).items()}
        self.aliases = {**{k: k for k in KEYWORDS}, **aliases}

    # ------------------------------------------------------------ reading
    def new_name(self, phase):
        return self.renamed.get(phase, phase)

    def read(self, path=None):
        """The flow block, parsed. None when the file or the block is missing, so a broken guide never breaks a note:
        the vocabulary falls back to `fallback_phases`."""
        path = path or self.path
        if not path:
            return None
        try:
            text = Path(path).read_text(encoding='utf-8')
        except OSError:
            return None
        fence = re.escape(self.cfg.get('fence') or 'flow')
        block = re.search(rf'^```{fence}[ \t]*\n(.*?)^```', text, re.M | re.S)
        if not block:
            return None
        g = {'version': None, 'stages': [], 'chain': [], 'free': [], 'parallel': [], 'lane': [], 'loose': [],
             'return': [], 'person': [], 'gate': []}
        before_re = re.compile(self.cfg.get('before_pattern') or r'before (?:the )?([\w-]+)')

        def step(s):
            return {'phases': [f for f in re.split(r'[|+]', s.strip()) if f], 'all': '+' in s}

        for raw in block.group(1).splitlines():
            line, _, note = raw.partition('#')
            word, _, rest = line.strip().partition(' ')
            rest = rest.strip()
            kw = self.aliases.get(word.lower())
            if not kw:
                continue
            if kw == 'version':
                g['version'] = rest
            elif kw == 'stage':
                name, *phases = rest.split()
                g['stages'].append({'name': name, 'phases': phases})
            elif kw in ('chain', 'parallel', 'lane'):
                g[kw].append([step(s) for s in rest.split('>')])
            elif kw == 'free':
                g['free'].append({'phases': [f.strip() for f in rest.split('<>')]})
            elif kw == 'loose':
                g['loose'].append({'phase': rest.split()[0], 'note': note.strip() or None})
            elif kw == 'return':
                cond = re.search(r'\(([^)]*)\)', rest)
                src, _, dest = re.sub(r'\([^)]*\)', '', rest).partition('>')
                before = before_re.search(cond.group(1)) if cond else None
                g['return'].append({'from': step(src)['phases'], 'to': dest.strip(),
                                    'condition': cond.group(1).strip() if cond else None,
                                    'before': before.group(1) if before else None})
            elif kw in ('person', 'gate'):
                g[kw] = rest.split()
        g['phases'] = [f for s in g['stages'] for f in s['phases']] + [l['phase'] for l in g['loose']]
        heading = re.escape(self.cfg.get('changes_heading') or 'Changes')
        changes = re.search(rf'^## {heading}[ \t]*\n(.*?)(?=^## |\Z)', text, re.M | re.S)
        version_re = re.compile(self.cfg.get('version_pattern') or r'\d[\w.-]*')
        g['changes'] = []
        for row in (changes.group(1).splitlines() if changes else []):
            cells = [c.strip() for c in row.strip().strip('|').split('|')]
            if len(cells) >= 4 and version_re.fullmatch(cells[0]):
                g['changes'].append({'version': cells[0], 'date': cells[1], 'what': cells[2], 'by': cells[3]})
        return g

    # ------------------------------------------------------------ the graph
    def lane_phases(self, g) -> set:
        """The phases of the lanes that the main flow does not name: the lane's root stays in the main flow."""
        if not g:
            return set()
        main = {f for kind in ('chain', 'parallel') for chain in g[kind] for p in chain for f in p['phases']}
        main |= {f for l in g['free'] for f in l['phases']}
        return {f for chain in g.get('lane', []) for p in chain for f in p['phases']} - main

    def vocabulary(self, g=None) -> list[str]:
        """What --phase takes: the neutral phases, the guide's, the variant ones and the task's ok."""
        g = g if g is not None else self.read()
        base = g['phases'] if g else list(self.cfg.get('fallback_phases') or [])
        return (list(self.neutral) + [f for f in base if f not in self.neutral]
                + [f for f in self.variant if f not in base] + [self.task_ok])

    @staticmethod
    def graph(g):
        """(edges, predecessors): edges[(a, b)] = the phases that must be done before b (an a+b step);
        pred[b] = the phases that open b in a parallel or lane chain, any of them done."""
        edges, pred = {}, {}
        for kind in ('chain', 'parallel', 'lane'):
            for chain in g.get(kind, []):
                for before, after in zip(chain, chain[1:]):
                    need = set(before['phases']) if before['all'] else set()
                    for a in before['phases']:
                        for b in after['phases']:
                            edges[(a, b)] = need
                            if kind in ('parallel', 'lane'):
                                pred.setdefault(b, set()).add(a)
        for free in g['free']:
            for a in free['phases']:
                for b in free['phases']:
                    if a != b:
                        edges.setdefault((a, b), set())
        return edges, pred

    @staticmethod
    def loose_phases(g) -> list[str]:
        """Phases of a stage that no passage names: they come at any time (leaving the resume phase has its own
        rule in check())."""
        linked = {f for kind in ('chain', 'parallel', 'lane') for chain in g.get(kind, []) for p in chain
                  for f in p['phases']}
        linked |= {f for l in g['free'] for f in l['phases']} | {r['to'] for r in g['return']}
        linked |= {f for r in g['return'] for f in r['from']}
        return [f for s in g['stages'] for f in s['phases'] if f not in linked]

    # ------------------------------------------------------------ history and check
    def history(self, item, target=None, lane=frozenset()) -> list[str]:
        """The phases the item went through, oldest first, from the status events of every session (the item may
        have moved between workspaces), repeats collapsed, the rotated file included.

        A lane runs one track per target: with `target` None, the main flow only (no event of a target, no phase of
        the lane); with `target`, the main flow plus that target's events. `target=''` is the lane with no target
        named."""
        if not item:
            return []
        key = f'"item": {json.dumps(item, ensure_ascii=False)}'
        out = []
        for ev in self.app.store.scan_events((key, '"kind": "status"')):
            if ev.get('kind') != 'status' or ev.get('item') != item:
                continue
            phase = self.new_name(ev.get('phase'))
            if ev.get('target'):
                if target != ev['target']:
                    continue
            elif phase in lane and target != '':
                continue
            if phase and (not out or out[-1] != phase):
                out.append(phase)
        return out

    def check(self, g, before, new):
        """None when going to `new` is in the guide after the phases `before` (oldest first), else (from, reason).
        Never blocks: the caller warns and marks the card."""
        t = self.app.text
        if not g or new in self.neutral or new in self.variant or new == self.task_ok or new not in g['phases']:
            return None
        transparent = (set(self.loose_phases(g)) - {self.resume}) | {l['phase'] for l in g['loose']}
        if new == self.resume or new in transparent:
            return None
        h = [p for p in before if p not in self.neutral and p != self.task_ok]
        if h and h[-1] in self.variant:
            return None
        h = [p for p in h if p in g['phases'] and p not in transparent]
        lane = self.lane_phases(g)
        if new in lane:
            # a target enters the lane at any phase; inside it the order holds, and a target unblocked goes back to
            # its own phase with no verify
            h = [p for p in h if p != self.resume]
            if not any(p in lane for p in h):
                return None
        else:
            h = [p for p in h if p not in lane]  # the lane never moves the main flow
        if not h or h[-1] == new:
            return None
        src, done = h[-1], set(h)
        if src == self.resume:
            return None if new == self.resume_next else (src, t('flow.resume_first', resume=self.resume,
                                                                next=self.resume_next))
        if src == self.resume_next and len(h) >= 3 and h[-2] == self.resume and new == h[-3]:
            return None
        edges, pred = self.graph(g)
        if (src, new) in edges:
            missing = sorted(edges[(src, new)] - done - {src})
            return (src, t('flow.missing', phases=t('flow.and').join(missing))) if missing else None
        if pred.get(new, set()) & done:
            return None
        for r in g['return']:
            if r['to'] == new and ('*' in r['from'] or src in r['from']):
                if r['before'] and r['before'] in done:
                    return src, t('flow.return_only', phase=new, condition=r['condition'])
                return None
        return src, t('flow.outside')

    # ------------------------------------------------------------ the flow command
    def describe(self, g) -> dict:
        return {'file': str(self.path), 'version': g['version'], 'stages': g['stages'], 'phases': g['phases'],
                'passages': {k: g[k] for k in ('chain', 'free', 'parallel', 'lane', 'loose', 'return')},
                'lane_phases': [f for f in g['phases'] if f in self.lane_phases(g)],
                'person': g['person'], 'gates': g['gate'],
                'outside_graph': {'neutral': list(self.neutral), 'variant': list(self.variant),
                                  'task_ok': self.task_ok},
                'changes': g['changes']}

    def lines(self, g) -> list[str]:
        out = [f"guide {g['version']}  ({self.path})"]
        for s in g['stages']:
            out.append(f"  {s['name']:10} {' '.join(s['phases'])}")

        def fmt(chain):
            return ' > '.join(('+' if p['all'] else '|').join(p['phases']) for p in chain)
        for kind in ('chain', 'parallel', 'lane'):
            for chain in g[kind]:
                out.append(f"  {kind:9} {fmt(chain)}" + ('   (one track per target: note --target <name>)'
                                                         if kind == 'lane' else ''))
        for l in g['free']:
            out.append(f"  free      {' <> '.join(l['phases'])}")
        for r in g['return']:
            out.append(f"  return    {'|'.join(r['from'])} > {r['to']}" + (f" ({r['condition']})" if r['condition'] else ''))
        out.append(f"  person    {' '.join(g['person'])}\n  gate      {' '.join(g['gate'])}")
        from .util import first_line
        for c in g['changes']:
            out.append(f"  {c['version']}  {first_line(c['what'], 100)}  ({c['by']})")
        return out
