"""The flow block, the transition check and the status card (note)."""
import json
import subprocess
import sys

from helpers import ROOT, Case

GUIDE = '''# The development flow

```flow
version  2026-01-05.1
stage   analysis   init analysis plan
stage   dev        implement verify resume
stage   review     validate review
stage   final      pr-create pr pr-review merge post closeout backport handoff
stage   postdeploy deploy staging
chain   init > analysis > plan > implement > verify > validate|review
free    validate <> review
chain   validate+review > pr-create > pr
parallel pr > pr-review > merge > deploy > staging
parallel pr > post > closeout > handoff
loose   cleanup    # zeroes the whole workspace, outside the item
return  verify|validate|review|pr-review > implement
return  * > analysis  (before the pr)
person  review pr merge staging
gate    pr-create pr-review closeout handoff cleanup
```

## Changes

| Version | Date | What changed | Asked by |
|---|---|---|---|
| 2026-01-05.1 | 2026-01-05 | The guide becomes the flow (decisions) | the person |
'''

GUIDE_LANE = '''# guide with a lane

```flow
version  2026-01-10.4
stage   analysis   init analysis plan
stage   dev        implement verify resume
stage   review     validate review
stage   final      pr-create pr pr-review merge post closeout handoff
stage   backport   target proof rep-git rep-pr rep-review rep-post rep-merge
stage   postdeploy deploy staging
chain   init > analysis > plan > implement > verify > validate|review
free    validate <> review
chain   validate+review > pr-create > pr
parallel pr > pr-review > merge > deploy > staging
parallel pr > post > closeout > handoff
lane    pr-review > target > proof > rep-git > rep-pr > rep-review > rep-post > rep-merge
loose   cleanup    # zeroes the whole workspace
loose   blocked    # the item waits for a third party
return  blocked > resume
return  verify|validate|review|pr-review > implement
person  review merge rep-merge staging
gate    pr-create pr pr-review rep-git rep-pr closeout handoff cleanup
```
'''

FLOW_TOML = '''
[workflow]
guide = '{guide}'
neutral = ['free', 'infra']
variant = ['claim', 'loadtest', 'comment', 'sheet']
resume_phase = 'resume'
resume_next = 'verify'
renamed = {{finalize = 'pr-create', finalize-b = 'post', cherry-pick = 'target'}}
renamed_in = {{finalize = '2026-01-07.1', finalize-b = '2026-01-07.1', cherry-pick = '2026-01-10.4'}}
keep_target = ['blocked', 'resume']
untracked_items = ['study']
[status]
task_pattern = '\\d{{3,7}}'
rereview = {{phase = 'review', needs = ['implement', 'verify', 'resume']}}
item_aliases = {{study = 'study'}}
items_need_text = ['study']
[priority]
delivered_phases = ['handoff', 'cleanup']
'''


class FlowCase(Case):
    guide_text = GUIDE

    def setUp(self):
        super().setUp()
        self.write('flow.md', self.guide_text)
        self.extra = FLOW_TOML.format(guide=(self.tmp / 'flow.md').as_posix())
        self.app = self.make_app(self.extra)
        from ai_signalbox.workflow import Workflow
        self.wf = Workflow(self.app)
        self.me = self.session('ws2', name='alpha')
        self.act_as(self.me)
        self.tick()

    def note(self, *argv):
        return self.cli('note', *argv)

    def last_status(self):
        return [e for e in self.events(sid=self.me['sid']) if e['kind'] == 'status'][-1]


class GuideTest(FlowCase):
    def test_parsed(self):
        g = self.wf.read()
        self.assertEqual(g['version'], '2026-01-05.1')
        self.assertEqual([s['name'] for s in g['stages']], ['analysis', 'dev', 'review', 'final', 'postdeploy'])
        self.assertEqual(g['chain'][0][-1], {'phases': ['validate', 'review'], 'all': False})
        self.assertEqual(g['chain'][1][0], {'phases': ['validate', 'review'], 'all': True})
        self.assertEqual(g['return'][1], {'from': ['*'], 'to': 'analysis', 'condition': 'before the pr', 'before': 'pr'})
        self.assertEqual(g['loose'], [{'phase': 'cleanup', 'note': 'zeroes the whole workspace, outside the item'}])
        self.assertEqual((g['person'], g['gate'][-1]), (['review', 'pr', 'merge', 'staging'], 'cleanup'))
        self.assertEqual(g['changes'], [{'version': '2026-01-05.1', 'date': '2026-01-05',
                                         'what': 'The guide becomes the flow (decisions)', 'by': 'the person'}])
        self.assertEqual(self.wf.loose_phases(g), ['resume', 'backport'])
        v = self.wf.vocabulary(g)
        self.assertTrue({'free', 'infra', 'claim', 'loadtest', 'comment', 'sheet', 'ok', 'pr-review', 'staging',
                         'resume'} <= set(v))
        self.assertIsNone(self.wf.read(self.tmp / 'missing.md'))

    def test_keyword_aliases(self):
        self.write('alias.md', '```flow\nrev 7\nphasegroup a x y\nthen x > y\n```\n')
        app = self.make_app("[workflow]\nguide = 'alias.md'\nkeywords = {rev = 'version', phasegroup = 'stage', "
                            "then = 'chain'}")
        from ai_signalbox.workflow import Workflow
        g = Workflow(app).read()
        self.assertEqual((g['version'], g['phases'], len(g['chain'])), ('7', ['x', 'y'], 1))

    def test_passages(self):
        g = self.wf.read()

        def c(before, new):
            r = self.wf.check(g, before.split(), new)
            return r and r[0]
        self.assertIsNone(c('init analysis plan implement', 'verify'))
        self.assertEqual([self.wf.new_name(p) for p in ('finalize', 'finalize-b', 'verify')], ['pr-create', 'post', 'verify'])
        self.assertIsNone(c('', 'pr-create'))
        self.assertEqual(c('implement verify', 'pr-create'), 'verify')
        self.assertEqual(self.wf.check(g, ['verify', 'validate'], 'pr-create'), ('validate', 'missing review'))
        self.assertIsNone(c('verify validate review', 'pr-create'))
        self.assertIsNone(c('verify review', 'validate'))
        self.assertIsNone(c('pr-create pr', 'pr-review'))
        self.assertIsNone(c('pr-create pr', 'post'))
        self.assertIsNone(c('pr post', 'pr-review'))
        self.assertEqual(c('pr pr-review', 'closeout'), 'pr-review')
        self.assertIsNone(c('pr pr-review', 'implement'))
        self.assertIsNone(c('pr pr-review implement verify', 'pr-review'))
        self.assertIsNone(c('implement verify review', 'analysis'))
        self.assertEqual(self.wf.check(g, 'pr-create pr pr-review'.split(), 'analysis'),
                         ('pr-review', 'the return to analysis only holds before the pr'))
        self.assertEqual(c('implement resume', 'validate'), 'resume')
        self.assertIsNone(c('implement resume', 'verify'))
        self.assertIsNone(c('pr post resume verify', 'post'))
        self.assertIsNone(c('closeout handoff', 'backport'))
        self.assertIsNone(c('handoff', 'cleanup'))
        self.assertIsNone(c('verify infra free', 'validate'))
        self.assertIsNone(c('merge deploy loadtest', 'comment'))
        self.assertIsNone(c('comment sheet', 'closeout'))
        self.assertIsNone(c('implement', 'implement'))

    def test_flow_command(self):
        code, out, _ = self.cli('flow', '--json')
        j = json.loads(out)
        self.assertEqual((code, j['version'], j['passages']['loose'][0]['phase'], j['changes'][0]['version']),
                         (0, '2026-01-05.1', 'cleanup', '2026-01-05.1'))
        code, out, _ = self.cli('flow')
        self.assertIn('return    * > analysis (before the pr)', out)


class NoteTest(FlowCase):
    def test_task_requested_and_ok(self):
        self.note('--item', '10001', '--phase', 'implement', 'AC7')
        card = self.card(self.me)
        self.assertEqual((card['guide'], card.get('task'), card.get('off_guide')), ('2026-01-05.1', None, None))
        code, _, _ = self.note('--phase', 'verify', '--task', '10003')
        card = self.card(self.me)
        self.assertEqual((code, card['task'], card['tasks']['10003']['phase']), (0, '10003', 'verify'))
        self.assertEqual((self.last_status()['task'], self.last_status()['phase'], self.last_status()['guide']),
                         ('10003', 'verify', '2026-01-05.1'))
        code, out, _ = self.note('--phase', 'pr-create', 'no validate')
        self.assertEqual(code, 0)
        self.assertIn('off the guide', out)  # warns, never blocks
        off = self.card(self.me)['off_guide']
        self.assertEqual((off['from'], off['to'], off['task']), ('verify', 'pr-create', '10003'))
        self.assertTrue(self.last_status()['off_guide'])
        code, out, _ = self.note('--phase', 'analysis')
        self.assertNotIn('off the guide', out)
        self.assertIsNone(self.card(self.me)['off_guide'])  # return * > analysis
        self.note('--phase', 'ok', '--task', '10003')
        card = self.card(self.me)
        self.assertEqual((card['phase'], card['tasks']['10003']['phase']), ('analysis', 'ok'))
        self.assertEqual((self.last_status()['phase'], self.last_status()['task_phase']), ('analysis', 'ok'))
        self.note('--task', '-')
        self.assertEqual((self.card(self.me)['task'], list(self.card(self.me)['tasks'])), (None, ['10003']))
        self.assertEqual(self.note('--phase', 'ok')[0], 1)  # ok with no task
        code, out, _ = self.note('--phase', 'pr-create', '--requested', 'pr-create without the validate')
        self.assertEqual(code, 0)
        self.assertNotIn('off the guide', out)
        card = self.card(self.me)
        self.assertEqual((card['requested']['text'], card['requested']['phase'], card['off_guide']),
                         ('pr-create without the validate', 'pr-create', None))
        self.assertEqual(self.last_status()['requested'], 'pr-create without the validate')
        from ai_signalbox.status import guide_mark
        self.assertEqual(guide_mark(self.app, card), 'at their request')
        self.note('--item', '10002', '--phase', 'init')  # a new item clears task, tasks and requested
        card = self.card(self.me)
        self.assertEqual((card['task'], card['tasks'], card['requested']), (None, {}, None))
        self.note('--item', '10001', '--phase', 'analysis')  # coming back brings its tasks
        self.assertEqual(list(self.card(self.me)['tasks']), ['10003'])
        self.assertEqual(self.note('--task', 'abc')[0], 1)
        self.assertEqual(self.note('--requested', 'no phase')[0], 1)
        code, _, _ = self.note('--task', '10004', '--phase', 'implement', '--requested', 'AC8 now', 'x')
        card = self.card(self.me)
        self.assertEqual((code, card['task'], card['requested']['text'], card['text']), (0, '10004', 'AC8 now', 'x'))

    def test_only_on_transition_and_rereview(self):
        for phase in ('init', 'analysis', 'plan', 'implement', 'verify'):
            self.assertEqual(self.note('--item', '10012', '--phase', phase, f'{phase} of #10012')[0], 0)
        code, _, err = self.note('--phase', 'verify', 'AC 3 checked')
        self.assertEqual((code, 'the phase is already verify' in err, self.card(self.me)['text']),
                         (1, True, 'verify of #10012'))
        self.assertEqual(self.note('more text')[0], 1)
        self.assertEqual(self.note('--phase', 'verify')[0], 1)
        self.assertEqual(self.note('--waiting', 'person')[0], 0)
        self.assertEqual(self.note('--phase', 'verify', '--waiting', '')[0], 0)
        self.assertEqual(self.note('--done', 'Ran the backend suite')[0], 0)
        self.assertEqual(self.note('--phase', 'verify', '--task', '10013', 'AC-02')[0], 0)
        self.assertEqual(self.note('--phase', 'ok', '--task', '10013')[0], 0)
        self.assertEqual(self.note('--phase', 'verify', '--requested', 'write this down', 'again')[0], 0)
        self.assertEqual(self.note('--phase', 'review', '--waiting', 'person', 'Diff ready')[0], 0)
        self.assertEqual(self.note('--phase', 'validate', '--waiting', '', 'AC by AC')[0], 0)
        code, _, err = self.note('--phase', 'review', '--waiting', 'person', 'waiting for pr-create')
        self.assertEqual((code, 'needs a change since the last one' in err, self.card(self.me)['phase']),
                         (1, True, 'validate'))
        self.assertEqual(self.note('--phase', 'review', '--waiting', 'person', '--requested', 'review again', 'x')[0], 0)
        self.assertEqual(self.note('--phase', 'implement', 'Fix from the review')[0], 0)
        self.assertEqual(self.note('--phase', 'verify', 'Build and tests')[0], 0)
        self.assertEqual(self.note('--phase', 'review', '--waiting', 'person', 'Fix ready')[0], 0)
        self.assertEqual(self.note('--item', '-', '--phase', 'infra', 'Request 1')[0], 0)
        self.assertEqual(self.note('--item', '-', '--phase', 'infra', 'Request 2')[0], 0)

    def test_status_and_done_events(self):
        base = len(self.events())
        self.assertEqual(self.note('--item', '10006', '--phase', 'implement', 'offline mode')[0], 0)
        self.assertEqual(self.note('--done', 'Added the offline mode, with tests')[0], 0)
        self.assertEqual(self.note('--item', '-', '--phase', 'free', 'signal work')[0], 0)
        self.assertEqual(self.note('--done', 'Moved the cards to the app')[0], 0)
        e = [x for x in self.events()[base:] if x['kind'] in ('status', 'done')]
        self.assertEqual([x['kind'] for x in e], ['status', 'done', 'status', 'done'])  # --done alone: no status again
        self.assertEqual((e[0]['item'], e[0]['phase'], e[0]['sid'], e[0]['workspace']),
                         ('10006', 'implement', self.me['sid'], 'ws2'))
        self.assertEqual((e[1]['item'], e[1]['text']), ('10006', 'Added the offline mode, with tests'))
        self.assertNotIn('item', e[2])
        self.assertEqual((e[2]['phase'], e[2]['text']), ('free', 'free · signal work'))
        card = self.card(self.me)
        self.assertEqual((card['item'], card['done'], bool(card['done_at'])), (None, 'Moved the cards to the app', True))
        self.note('--item', '10008', '--phase', 'analysis', '--done', 'Read the item', 'reading')
        e = [x for x in self.events()[-2:]]
        self.assertEqual([(x['kind'], x.get('item'), x['text']) for x in e],
                         [('status', '10008', '10008 · analysis · reading'), ('done', '10008', 'Read the item')])
        f = self.write('note.txt', 'Card requests\nin the app, checked\n')
        self.assertEqual(self.note('--item', '-', '--phase', 'infra', '--text-file', f)[0], 0)
        x = self.events()[-1]
        self.assertNotIn('item', x)
        self.assertEqual((x['phase'], x['text']), ('infra', 'infra · Card requests'))
        self.assertEqual(self.card(self.me)['text'], 'Card requests\nin the app, checked')

    def test_untracked_item_and_text_check(self):
        app = self.app
        app.registry.text_checks.append(lambda *texts: 'warning: no tag' if any('10009' in (t or '') for t in texts)
                                        else None)
        self.assertEqual(self.note('--item', 'study', '--phase', 'analysis')[0], 1)  # needs the text, its title
        app.emit('status', {'workspace': 'ws3', 'name': 'x', 'sid': 'other'}, 'study', item='study', phase='pr')
        title = 'Conflict between #10005 and #10009'
        code, _, err = self.note('--item', 'Study', '--phase', 'analysis', title)
        card = self.card(self.me)
        self.assertEqual((code, card['item'], card['text'], card.get('off_guide')), (0, 'study', title, None))
        self.assertEqual(self.last_status()['text'], f'study · analysis · {title}')
        self.note('--item', 'study', '--phase', 'implement')
        self.assertEqual((self.card(self.me)['text'], self.card(self.me).get('off_guide')), (title, None))
        self.assertIn('warning: no tag', err)

    def test_unknown_option_is_an_error(self):
        code, _, err = self.cli('ask', '--item', '10011', '--type', 'decision', 'In October?')
        self.assertEqual(code, 2)
        self.assertIn('unrecognized arguments', err)
        self.assertFalse(self.card(self.me).get('asks'))
        code, _, _ = self.cli('ask', '--type', 'decision', '--alert', '-', stdin='Backport in October?')
        self.assertEqual(code, 0)
        self.assertEqual([(a['type'], a['text'], a['alert']) for a in self.card(self.me)['asks']],
                         [('decision', 'Backport in October?', True)])
        self.assertEqual(self.cli('note', '--stage', 'implement', 'x')[0], 2)


class LaneTest(FlowCase):
    guide_text = GUIDE_LANE

    def test_one_track_per_target(self):
        rel, hot = 'releases/v1.2', 'hotfixes/v1.1.3'
        self.app.registry.pr_info.append(lambda n: {'repo': 'web', 'url': f'https://example.test/pr/{n}'})
        g = self.wf.read()
        lane = ['target', 'proof', 'rep-git', 'rep-pr', 'rep-review', 'rep-post', 'rep-merge']
        self.assertEqual((g['lane'][0][0]['phases'], self.wf.lane_phases(g)), (['pr-review'], set(lane)))
        self.assertEqual(self.wf.loose_phases(g), [])
        self.assertTrue(set(lane) <= set(self.wf.vocabulary(g)))

        def c(before, new):
            r = self.wf.check(g, before.split(), new)
            return r and r[0]
        self.assertIsNone(c('pr pr-review merge post closeout handoff', 'target'))
        self.assertIsNone(c('pr pr-review handoff target proof', 'rep-git'))
        self.assertEqual(c('pr pr-review handoff target', 'rep-git'), 'target')
        self.assertIsNone(c('pr pr-review handoff', 'rep-review'))
        self.assertIsNone(c('pr pr-review target proof rep-git blocked resume', 'rep-pr'))
        self.assertIsNone(c('pr post closeout target proof', 'handoff'))

        for phase in ('pr', 'pr-review', 'handoff'):
            self.note('--item', '10018', '--phase', phase)
        code, _, _ = self.note('--phase', 'target', '--target', rel)
        card = self.card(self.me)
        self.assertEqual((code, card['target'], card['targets'][rel]['phase'], self.last_status()['target']),
                         (0, rel, 'target', rel))
        code, _, _ = self.note('--phase', 'proof')
        self.assertEqual((code, self.card(self.me)['targets'][rel]['phase'], self.card(self.me).get('off_guide')),
                         (0, 'proof', None))
        self.note('--phase', 'target', '--target', hot)
        code, _, _ = self.note('--phase', 'blocked', '--waiting', 'a third party')
        card = self.card(self.me)
        self.assertEqual((code, card['target'], card['targets'][hot]['phase'], self.last_status()['target']),
                         (0, hot, 'blocked', hot))
        code, out, _ = self.note('--phase', 'rep-git', '--waiting', '', '--target', rel)
        self.assertEqual(code, 0)
        self.assertNotIn('off the guide', out)
        code, _, _ = self.note('--phase', 'rep-pr', '--pr', '15959')
        card = self.card(self.me)
        self.assertEqual((code, card['targets'][rel]['pr'], card['pr'], card['pr_url'], self.last_status()['target']),
                         (0, 15959, 15959, 'https://example.test/pr/15959', rel))
        code, out, _ = self.note('--phase', 'rep-post')
        self.assertEqual(code, 0)
        self.assertIn('off the guide', out)
        self.assertEqual(self.note('--phase', 'rep-post', 'only the text')[0], 1)
        self.assertEqual(self.note('--phase', 'rep-post', '--target', hot, 'another target')[0], 0)
        card = self.card(self.me)
        self.assertEqual((card['targets'][hot]['phase'], card['targets'][rel]['phase']), ('rep-post', 'rep-post'))
        lanes = self.wf.lane_phases(g)
        self.assertEqual(set(self.wf.history('10018', rel, lanes)) & {'blocked', 'target', 'rep-git'}, {'target', 'rep-git'})
        self.assertEqual(set(self.wf.history('10018', None, lanes)) & set(lane), set())
        code, out, _ = self.note('--phase', 'closeout', '--target', rel)
        self.assertEqual(code, 0)
        self.assertIn('--target only holds', out)
        card = self.card(self.me)
        self.assertEqual((card['target'], 'target' in self.last_status(), sorted(card['targets'])), (None, False, [hot, rel]))
        code, out, _ = self.note('--phase', 'cherry-pick', '--target', rel)
        card = self.card(self.me)
        self.assertEqual((code, 'is now called target' in out, card['phase'], card['targets'][rel]['phase']),
                         (0, True, 'target', 'target'))
        self.app.emit('status', self.me, 'old', item='10018', phase='cherry-pick')
        self.assertIn('target', self.wf.history('10018', '', lanes))
        self.note('--item', '10002', '--phase', 'init')
        self.assertEqual((self.card(self.me)['target'], self.card(self.me)['targets']), (None, {}))
        self.note('--item', '10018', '--phase', 'closeout')
        self.assertEqual(sorted(self.card(self.me)['targets']), [hot, rel])
        self.assertEqual(self.note('--target', 'with space')[0], 1)
        self.assertEqual(self.note('--phase', 'rep-review', '--target', rel)[0], 0)
        self.assertEqual((self.card(self.me)['target'], self.card(self.me)['targets'][rel]['phase']), (rel, 'rep-review'))
        code, out, _ = self.cli('flow', '--json')
        j = json.loads(out)
        self.assertEqual((j['version'], j['passages']['lane'][0][1]['phases'], j['lane_phases']),
                         ('2026-01-10.4', ['target'], lane))


class StatusLockTest(Case):
    def test_four_processes_writing_one_card(self):
        s = self.session('ws1', name='alpha')
        cfg = self.tmp / 'config.toml'
        cfg.write_text(self.toml(), encoding='utf-8')
        code = (f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r})\n"
                "import contextlib, io\n"
                "from ai_signalbox.app import App\nfrom ai_signalbox.config import Config\nfrom ai_signalbox import threads\n"
                f"app = App(Config.load({str(cfg)!r}))\n"
                "with contextlib.redirect_stdout(io.StringIO()):\n"
                "    for i in range(15):\n"
                f"        threads.ask(app, text=f'p{{sys.argv[1]}}-{{i}}', sid={s['sid']!r})\n")
        ps = [subprocess.Popen([sys.executable, '-c', code, str(n)], stdout=subprocess.DEVNULL) for n in range(4)]
        for p in ps:
            p.wait(timeout=120)
        asks = self.card(s)['asks']
        self.assertEqual((len(asks), len({a['n'] for a in asks})), (60, 60))
