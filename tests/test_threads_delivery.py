"""Asks, threads, the person's messages and the built-in delivery."""
import json

from helpers import Case

from ai_signalbox import threads
from ai_signalbox.util import touch


class ThreadCase(Case):
    extra_toml = '[delivery]\nstart_touches = [0, 0, 0]\n'

    def box(self, ws):
        return self.app.store.box_list(ws)

    def box_msg(self, ws, mid):
        return next(m for _, m in self.box(ws) if m['id'] == mid)

    def drop_box(self, ws, mid):
        (self.app.store.box_dir(ws) / f'{mid}.json').unlink()

    def listen(self, s):
        touch(self.app.store.start_path(s['workspace'], s['sid']), 'created')


class ThreadTest(ThreadCase):
    def test_send_turns_close_events(self):
        s = self.session('ws3', name='beta')
        other = self.session('ws1', name='delta')
        self.listen(s)
        self.app.store.put_status(s['sid'], {'workspace': 'ws3', 'name': 'beta', 'updated': '2026-01-01T10:00:00'})
        code, out, _ = self.cli('send', 'ws3', '--text-file', self.write('m1.txt', 'Do the signal side of the thread'))
        self.assertEqual(code, 0)
        tid = json.loads(out)['id']
        t = self.app.store.get_thread(tid)
        self.assertEqual((t['workspace'], t['sid'], t['name'], t['state'], t['closed_at']),
                         ('ws3', s['sid'], 'beta', 'open', None))
        self.assertEqual([(f['n'], f['from'], f['text'], f['message']) for f in t['turns']],
                         [(1, 'person', 'Do the signal side of the thread', tid)])
        m = self.box_msg('ws3', tid)
        self.assertEqual((m['thread'], m['turn']), (tid, 1))
        text = self.app.delivery.deliver(s, [m], 'test')
        self.assertIn(f'message {tid} (opens thread {tid})', text)
        self.assertIn('thread <id> --text-file', text)
        self.assertIn('This is the person speaking', text)
        self.drop_box('ws3', tid)
        self.act_as(s)
        code, out, _ = self.cli('thread', tid, '--text-file', self.write('t2.txt', 'Doubt: does the app read the file?'))
        self.assertEqual(code, 0)
        self.assertIn('turn 2', out)
        self.act_as(other)
        code, _, err = self.cli('thread', tid, '--text-file', self.write('t3.txt', 'intruder'))
        self.assertEqual(code, 1)
        self.assertIn('belongs to ws3', err)
        self.act_as(s)
        code, _, err = self.cli('thread', tid, '--text-file', self.write('t4.txt', 'y' * 12001))
        self.assertEqual(code, 1)
        self.assertIn('cap 12000', err)
        self.assertEqual(self.cli('thread', tid, '--text-file', self.write('t5.txt', '   '))[0], 1)
        # the person answers: a message in the workspace's box with the thread id, to the session that listens
        code, out, _ = self.cli('thread', tid, '--from-person', '--text-file', self.write('t6.txt', 'It does.'))
        r = json.loads(out)
        self.assertEqual((code, r['id'], r['n']), (0, tid, 3))
        m = self.box_msg('ws3', r['message'])
        self.assertEqual((m['thread'], m['turn'], m['text'], m['sid']), (tid, 3, 'It does.', s['sid']))
        self.assertIn(f'thread {tid}, turn 3]', self.app.delivery.deliver(s, [m], 'test'))
        t = self.app.store.get_thread(tid)
        self.assertEqual([(f['n'], f['from']) for f in t['turns']], [(1, 'person'), (2, 'session'), (3, 'person')])
        self.assertEqual((t['turns'][1]['name'], t['turns'][1]['sid']), ('beta', s['sid']))
        self.end(s)  # gone: the next session of the workspace takes the person's next turn
        code, out, _ = self.cli('thread', tid, '--from-person', '--text-file', self.write('t7.txt', 'And again.'))
        self.assertIsNone(self.box_msg('ws3', json.loads(out)['message'])['sid'])
        self.session('ws3', name='beta2')
        self.act_as(self.session('ws3', name='beta3'))
        code, out, _ = self.cli('thread')
        self.assertIn(tid, out)
        self.assertIn('4 turns', out)
        self.assertIn('last: person', out)
        self.assertIn('owes a reply, message still in the box', out)
        code, out, _ = self.cli('thread', tid)
        self.assertIn('[2] ws3 beta', out)
        self.assertIn('Doubt', out)
        code, out, _ = self.cli('thread', tid, '--json')
        chat = json.loads(out)
        self.assertEqual([(x['from'], x['tag']) for x in chat['messages']],
                         [('me', tid), ('other', tid), ('me', tid), ('me', tid)])
        self.assertEqual((chat['messages'][1]['author'], chat['messages'][-1]['state']), ('ws3 beta', 'sent'))
        # the person closes it: writing is refused on both sides, and closing again changes nothing
        code, out, _ = self.cli('thread', tid, '--close')
        closed = json.loads(out)['closed_at']
        code, _, err = self.cli('thread', tid, '--text-file', self.write('t8.txt', 'one more'))
        self.assertEqual(code, 1)
        self.assertIn('closed thread', err)
        self.assertIn('as an ask', err)
        self.assertEqual(self.cli('thread', tid, '--from-person', '--text-file', self.write('t9.txt', 'x'))[0], 1)
        code, out, _ = self.cli('thread', tid, '--close')
        self.assertEqual(json.loads(out)['closed_at'], closed)
        self.assertIn('no open thread', self.cli('thread')[1])
        code, _, err = self.cli('thread', '../../x', '--text-file', self.write('ta.txt', 'hi'))
        self.assertEqual(code, 1)
        self.assertIn('invalid', err)
        ev = [(e.get('action'), e.get('by'), e.get('n'), e.get('workspace'), e.get('to_workspace'))
              for e in self.events('thread') if e.get('id') == tid]
        self.assertEqual(ev, [('open', None, None, None, 'ws3'), ('turn', 'session', 2, 'ws3', None),
                              ('turn', 'person', 3, None, 'ws3'), ('turn', 'person', 4, None, 'ws3'),
                              ('close', None, None, None, 'ws3')])
        # a message about an ask: the thread carries it, and the first-turn footer stays out
        self.app.cfg.data['texts']['delivery.footer_first'] = 'FIRST TURN FOOTER'
        code, out, _ = self.cli('send', 'ws3', '--ask', '1', '-', stdin='Approved.\nnaïve →\n')
        tid2 = json.loads(out)['id']
        self.assertEqual(self.app.store.get_thread(tid2)['ask'], 1)
        text = self.app.delivery.deliver(s, [self.box_msg('ws3', tid2)], 'test')
        self.assertNotIn('FIRST TURN FOOTER', text)
        self.assertIn('Approved.\nnaïve →', text)
        code, out, _ = self.cli('send', 'ws3', '--text-file', self.write('m3.txt', 'A new request'))
        tid3 = json.loads(out)['id']
        self.assertIn('FIRST TURN FOOTER', self.app.delivery.deliver(s, [self.box_msg('ws3', tid3)], 'test'))

    def test_asks(self):
        s = self.session('ws1', name='alpha')
        self.act_as(s)
        code, out, _ = self.cli('ask', '--type', 'Decision', '--text-file', self.write('a.txt', 'Ship on Friday?'))
        self.assertEqual(code, 0)
        self.assertIn('ask #1 (decision)', out)
        self.cli('ask', '--type', 'authorization', '--text-file', self.write('b.txt', '[release git] push the branch'))
        asks = self.card(s)['asks']
        self.assertEqual([(a['n'], a['type'], a['alert']) for a in asks], [(1, 'decision', False),
                                                                          (2, 'authorization', True)])
        self.assertEqual(self.cli('ask', '--type', 'whim', '--text-file', self.write('c.txt', 'x'))[0], 1)
        out = self.cli('ask')[1]
        self.assertIn('#2 authorization (red)', out)
        code, _, err = self.cli('ask', '--close', '7')
        self.assertEqual(code, 1)
        self.assertIn('Open: #1, #2', err)
        self.assertEqual(self.cli('ask', '--close', '1')[0], 0)
        self.assertEqual([a['n'] for a in self.card(s)['asks']], [2])
        self.assertEqual([(e['action'], e['n']) for e in self.events('ask')], [('open', 1), ('open', 2), ('close', 1)])
        self.cli('ask', '--text-file', self.write('d.txt', 'A question'))
        self.assertEqual(self.card(s)['asks'][-1]['n'], 3)  # numbers never come back


class RoutingTest(ThreadCase):
    """A message goes to a session that listens; one that is gone or alive and deaf gives it up to one that does."""

    def test_routing(self):
        me = self.session('ws2', name='gamma', started=2)
        deaf = self.session('ws2', name='deaf', started=1)
        hears = self.session('ws2', name='hears', started=0)
        dead = self.session('ws2', name='dead')
        self.end(dead)
        self.app.store.put_status(deaf['sid'], {'updated': '2026-01-02T16:16:00'})
        vigil = self.app.store.vigil_path('ws2', deaf['sid'])

        def send(text):
            code, out, _ = self.cli('send', 'ws2', '--text-file', self.write('r.txt', text))
            mid = json.loads(out)['id']
            self.drop_box('ws2', mid)
            return json.loads(out)['sid']
        self.listen(me)
        self.assertEqual(send('Look at the login'), me['sid'])
        self.app.store.start_path('ws2', me['sid']).unlink()
        self.assertIsNone(send('Again'))
        self.listen(me)
        touch(vigil, 'vigil')
        self.assertEqual(send('One more'), deaf['sid'])  # both listen: the newest card
        vigil.unlink()
        self.listen(hears)
        for mid, to, ask in (('20260103-124630-0a0a0a', dead['sid'], None), ('20260103-124700-0f0f0f', deaf['sid'], None),
                             ('20260103-124701-1f1f1f', deaf['sid'], 2), ('20260103-124702-2f2f2f', hears['sid'], None)):
            self.app.store.box_put('ws2', {'id': mid, 'workspace': 'ws2', 'sid': to, 'ask': ask, 'text': 'x',
                                           'at': '2026-01-03T12:46:30'})
        self.assertEqual([m['id'] for m in self.app.delivery.claim('ws2', me['sid'])],
                         ['20260103-124630-0a0a0a', '20260103-124700-0f0f0f'])
        self.assertEqual(sorted(m['id'] for _, m in self.box('ws2')), ['20260103-124701-1f1f1f', '20260103-124702-2f2f2f'])
        for mid in ('20260103-124701-1f1f1f', '20260103-124702-2f2f2f'):
            self.drop_box('ws2', mid)
        for t in self.app.store.list_threads():
            (self.app.store.root / 'threads' / f"{t['id']}.json").unlink()
        base = {'workspace': 'ws2', 'state': 'open', 'opened_at': '2026-01-03T12:46:30'}
        person = [{'n': 1, 'from': 'person', 'at': '2026-01-03T12:46:30', 'text': 'question'}]
        for tid, owner, turns, ask in (
                ('20260103-124630-aaaaaa', dead['sid'], person, None), ('20260103-124631-bbbbbb', None, person, None),
                ('20260103-124632-cccccc', deaf['sid'], person, None),
                ('20260103-124633-dddddd', dead['sid'], person + [{'n': 2, 'from': 'session', 'text': 'r'}], None),
                ('20260103-124634-eeeeee', deaf['sid'], person, None), ('20260103-124635-ffffff', deaf['sid'], person, 2),
                ('20260103-124636-abcabc', hears['sid'], person, None)):
            self.app.store.put_thread(dict(base, id=tid, sid=owner, turns=turns, ask=ask))
        for tid, owner in (('20260103-124634-eeeeee', deaf['sid']), ('20260103-124636-abcabc', hears['sid'])):
            self.app.store.box_put('ws2', {'id': tid, 'workspace': 'ws2', 'sid': owner, 'text': 'question',
                                           'at': '2026-01-03T12:46:34', 'thread': tid, 'turn': 1})
        alive = {me['sid'], deaf['sid'], hears['sid']}
        self.assertEqual(sorted(t['id'] for t in threads.owed(self.app, 'ws2', me['sid'], alive)),
                         ['20260103-124630-aaaaaa', '20260103-124631-bbbbbb', '20260103-124632-cccccc',
                          '20260103-124634-eeeeee'])
        line = lambda tid: threads.line(self.app, self.app.store.get_thread(tid))  # noqa: E731
        self.assertTrue(line('20260103-124630-aaaaaa').endswith('<- owes a reply'))
        self.assertTrue(line('20260103-124634-eeeeee').endswith('message still in the box'))
        self.assertNotIn('owes a reply', line('20260103-124633-dddddd'))
        self.drop_box('ws2', '20260103-124636-abcabc')
        # who answers takes the message still in the box: the deaf one never gets it, and the thread follows
        self.act_as(me)
        code, out, _ = self.cli('thread', '20260103-124634-eeeeee', '--text-file', self.write('x.txt', 'Answered here.'))
        self.assertEqual(code, 0)
        self.assertIn('was still in the box', out)
        self.assertFalse(any(m['id'] == '20260103-124634-eeeeee' for _, m in self.box('ws2')))
        got = next(m for m in self.app.store.delivered() if m['id'] == '20260103-124634-eeeeee')
        self.assertEqual(got['to_sid'], me['sid'])
        self.assertEqual(self.app.delivery.claim('ws2', deaf['sid']), [])
        self.assertEqual(self.app.store.get_thread('20260103-124634-eeeeee')['sid'], me['sid'])
        self.assertEqual(sorted(t['id'] for t in threads.owed(self.app, 'ws2', me['sid'], alive)),
                         ['20260103-124630-aaaaaa', '20260103-124631-bbbbbb', '20260103-124632-cccccc'])


class WakeTest(ThreadCase):
    def test_signal_start_release(self):
        me = self.session('ws1', name='alpha')
        other = self.session('ws1', name='omega')
        store, fb = self.app.store, self.app.delivery
        signal, start, woke = store.signal_path('ws1'), store.start_path('ws1', me['sid']), store.woke_path('ws1', me['sid'])
        code, out, _ = self.cli('send', 'ws1', '--sid', me['sid'], '--text-file', self.write('w.txt', 'Look at the build'))
        mid = json.loads(out)['id']
        self.assertIn(mid, signal.read_text(encoding='utf-8'))
        text = fb.wake_text('ws1', me['sid'], str(signal), me)
        self.assertIn(f'message {mid}', text)
        self.assertIn('Look at the build', text)
        self.assertIsNone(fb.wake_text('ws1', me['sid'], str(signal), me))
        self.listen(other)
        fb.send({'id': '20260103-140000-abcdef', 'workspace': 'ws1', 'sid': other['sid'], 'text': 'for the other one',
                 'at': '2026-01-03T14:00:00'})
        self.assertIsNone(fb.wake_text('ws1', me['sid'], str(signal), me))
        store.start_path('ws1', other['sid']).unlink()
        self.assertIn('for the other one', fb.wake_text('ws1', me['sid'], str(signal), me))
        # the start file: an owed thread wakes once
        touch(start, 'created')
        self.assertTrue(threads.owed(self.app, 'ws1', me['sid'], {me['sid']}))
        text = fb.wake_text('ws1', me['sid'], str(start), me, alive={me['sid']})
        self.assertIn('On connecting', text)
        self.assertIn(mid, text)
        self.assertTrue(woke.exists())
        self.assertIsNone(fb.wake_text('ws1', me['sid'], str(start), me, alive={me['sid']}))
        self.assertIsNone(fb.wake_text('ws1', me['sid'], str(store.start_path('ws1', other['sid'])), me,
                                       alive={me['sid']}))
        touch(start, 'x0')
        fb.start_touch('ws1', me['sid'])
        self.assertTrue(start.read_text(encoding='utf-8').rstrip().endswith('x0'))
        woke.unlink()
        fb.start_touch('ws1', me['sid'])
        self.assertTrue(start.read_text(encoding='utf-8').rstrip().endswith('touch'))
        # the session ends: its marks go, and what it did not read goes back to the workspace
        fb.send({'id': '20260103-140100-fedcba', 'workspace': 'ws1', 'sid': me['sid'], 'text': 'left over',
                 'at': '2026-01-03T14:01:00'})
        touch(signal, 'before')
        touch(woke, 'woke')
        self.assertEqual(fb.release('ws1', me['sid']), 1)
        self.assertFalse(start.exists())
        self.assertFalse(woke.exists())
        self.assertIn('returned', signal.read_text(encoding='utf-8'))
        back = self.box_msg('ws1', '20260103-140100-fedcba')
        self.assertEqual((back['sid'], back['from_sid']), (None, me['sid']))

    def test_vigil(self):
        import os
        me = self.session('ws1', name='alpha')
        os.environ['SIGNALBOX_VIGIL_STEP'] = '0.01'
        self.addCleanup(os.environ.pop, 'SIGNALBOX_VIGIL_STEP', None)
        app = self.make_app(self.extra_toml)
        app.delivery.send({'id': '20260103-150000-aaaaaa', 'workspace': 'ws1', 'sid': me['sid'], 'text': 'wake up',
                           'at': '2026-01-03T15:00:00'})
        code, text = app.delivery.vigil(me, me['sid'])
        self.assertEqual(code, 2)
        self.assertIn('wake up', text)
        self.assertFalse(app.store.vigil_path('ws1', me['sid']).exists())
        self.assertEqual(self.events('delivered')[-1]['via'], 'vigil')
