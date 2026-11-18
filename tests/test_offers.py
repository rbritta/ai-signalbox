"""Offers: work put up for a session to claim, delivered the way a `send` delivers, once."""
import json

from helpers import Case

from ai_signalbox import offers, threads
from ai_signalbox.guard import Guard
from ai_signalbox.util import Refuse, touch


class OfferTest(Case):
    extra_toml = '[delivery]\nstart_touches = [0, 0, 0]\n'

    def post(self, text, *argv):
        code, out, err = self.cli('offer', *argv, '--text-file', self.write('offer.txt', text))
        self.assertEqual(code, 0, err)
        return json.loads(out)['id']

    def listen(self, s):
        touch(self.app.store.start_path(s['workspace'], s['sid']), 'created')

    def delivered(self):
        return list(self.app.store.delivered())

    def test_offer_claim_becomes_the_message_and_thread_of_a_send(self):
        s = self.session('ws1', name='alpha')
        self.listen(s)
        oid = self.post('Check the header spacing\nOnly the panel, no repo.', 'ws1', '--from', 'app', '--title', 'T5')
        o = self.app.store.offer_get(oid)
        self.assertEqual((o['workspace'], o['title'], o['from'], o['text']),
                         ('ws1', 'T5', 'app', 'Check the header spacing\nOnly the panel, no repo.'))
        ev = self.events('offer')[-1]
        self.assertEqual((ev['action'], ev['id'], ev['to_workspace'], ev['by'], ev['text']),
                         ('open', oid, 'ws1', 'app', 'for ws1: T5'))
        self.assertEqual(self.app.store.box_list('ws1'), [], 'an offer delivers nothing until it is claimed')
        self.act_as(s)
        code, out, err = self.cli('claim', oid)
        self.assertEqual(code, 0, err)
        # the claim prints the message, the way a hook would deliver it, and takes it out of the box
        self.assertIn('[app -> ws1', out)
        self.assertIn(f'(opens thread {oid})', out)
        self.assertIn('Check the header spacing', out)
        self.assertEqual(self.app.store.box_list('ws1'), [])
        (m,) = self.delivered()
        self.assertEqual((m['id'], m['sid'], m['thread'], m['turn'], m['from'], m['to_sid']),
                         (oid, s['sid'], oid, 1, 'app', s['sid']))
        t = self.app.store.get_thread(oid)
        self.assertEqual((t['workspace'], t['sid'], t['state'], t['turns'][0]['text']),
                         ('ws1', s['sid'], 'open', 'Check the header spacing\nOnly the panel, no repo.'))
        # the offer left the open folder, with who took it
        self.assertIsNone(self.app.store.offer_get(oid))
        c = self.app.store.offer_get(oid, offers.CLAIMED)
        self.assertEqual((c['sid'], c['name'], c['claimed_workspace'], c['message'], c['delivered']),
                         (s['sid'], 'alpha', 'ws1', oid, True))
        ev = self.events('claim')[-1]
        self.assertEqual((ev['id'], ev['message'], ev['sid'], ev['by'], ev['offered_to'], ev['text']),
                         (oid, oid, s['sid'], 'app', 'ws1', 'claimed: T5'))
        self.assertEqual(len(self.events('send')), 1)
        log = self.app.store.log_read(f'{self.app.store.offer_get(oid, offers.CLAIMED)["claimed_at"][:10]}')
        self.assertIn('**app -> ws1**', log, 'the log names who offered, not the person')
        self.assertIn('claims the offer of app', log)
        # the same session asking again gets the same answer, and nothing more is delivered
        code, out, _ = self.cli('claim', oid, '--json')
        self.assertEqual(code, 0)
        res = json.loads(out)
        self.assertEqual((res['ok'], res['id'], res['message'], res['sid'], res['title']),
                         (True, oid, oid, s['sid'], 'T5'))
        self.assertEqual(len(self.delivered()), 1)
        self.assertEqual(len(self.events('claim')), 1)

    def test_claimed_once(self):
        a, b = self.session('ws1', name='alpha'), self.session('ws1', name='beta')
        oid = self.post('Only one of you')
        self.act_as(a)
        self.assertEqual(self.cli('claim', oid)[0], 0)
        self.act_as(b)
        code, _, err = self.cli('claim', oid)
        self.assertEqual(code, 1)
        self.assertIn(f'offer {oid} was already claimed by ws1 alpha', err)
        self.assertEqual(len(self.delivered()) + len(self.app.store.box_list('ws1')), 1,
                         'the second claim delivers nothing')

    def test_the_lock_decides(self):
        self.post('Raced for')
        oid = self.app.store.offer_list()[0]['id']
        first = self.app.store.offer_move(oid, offers.OPEN, offers.CLAIMED, {'sid': 'a'})
        second = self.app.store.offer_move(oid, offers.OPEN, offers.CLAIMED, {'sid': 'b'})
        third = self.app.store.offer_move(oid, offers.OPEN, offers.WITHDRAWN, {})
        self.assertEqual(first['sid'], 'a')
        self.assertIsNone(second)
        self.assertIsNone(third)
        self.assertEqual(self.app.store.offer_get(oid, offers.CLAIMED)['sid'], 'a')
        self.assertEqual(self.app.store.offer_update(oid, offers.CLAIMED, {'message': 'm'})['message'], 'm')
        self.assertIsNone(self.app.store.offer_update(oid, offers.OPEN, {'message': 'm'}))

    def test_offer_for_a_workspace_is_not_for_another(self):
        mine, other = self.session('ws1'), self.session('ws2')
        self.tick(5)  # the list goes oldest first
        oid = self.post('For ws1 only', 'ws1')
        anyone = self.post('For any workspace')
        ev = self.events('offer')[-1]
        self.assertEqual((ev['any_workspace'], 'to_workspace' in ev), (True, False))
        self.act_as(other)
        code, out, _ = self.cli('offers')
        self.assertEqual(code, 0)
        self.assertIn(anyone, out)
        self.assertIn('for any workspace', out)
        self.assertNotIn(oid, out)
        code, _, err = self.cli('claim', oid)
        self.assertEqual(code, 1)
        self.assertIn(f'offer {oid} is for ws1, not for ws2', err)
        self.assertIsNotNone(self.app.store.offer_get(oid), 'a refused claim leaves it open')
        self.act_as(mine)
        code, out, _ = self.cli('offers', '--json')
        self.assertEqual([o['id'] for o in json.loads(out)], [oid, anyone])
        self.assertEqual(self.cli('claim', anyone)[0], 0)
        code, out, _ = self.cli('offers', '--all')
        self.assertIn(oid, out)
        self.assertNotIn(anyone, out)

    def test_offers_outside_a_session_lists_all_and_says_so(self):
        oid = self.post('For ws2', 'ws2')
        self.act_as(None)
        code, out, _ = self.cli('offers')
        self.assertEqual(code, 0)
        self.assertIn('no calling session here', out)
        self.assertIn(oid, out)

    def test_withdraw(self):
        s = self.session('ws1')
        oid = self.post('Changed my mind', 'ws1')
        code, _, err = self.cli('offer', 'ws1', '--withdraw', oid, '--title', 'X')
        self.assertEqual(code, 1, 'withdraw takes the id alone')
        self.assertIn('--withdraw takes only the offer id, not workspace, --title', err)
        self.assertIsNotNone(self.app.store.offer_get(oid))
        code, out, _ = self.cli('offer', '--withdraw', oid)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)['withdrawn_at'])
        self.assertEqual(self.events('offer')[-1]['action'], 'withdraw')
        self.act_as(s)
        code, _, err = self.cli('claim', oid)
        self.assertEqual(code, 1)
        self.assertIn(f'offer {oid} was withdrawn', err)
        code, _, err = self.cli('offer', '--withdraw', oid)
        self.assertEqual(code, 1)
        self.assertIn('was withdrawn', err)

    def test_refusals(self):
        s = self.session('ws1')
        self.assertEqual(self.cli('offer', 'ws1', '--text-file', self.write('e.txt', '   '))[0], 1)
        code, _, err = self.cli('offer', 'ws1', '--text-file', self.write('big.txt', 'x' * 12001))
        self.assertEqual(code, 1)
        self.assertIn('cap 12000', err)
        code, _, err = self.cli('offer', 'no/such', '--text-file', self.write('w.txt', 'x'))
        self.assertEqual(code, 1)
        self.assertIn('invalid workspace', err)
        code, _, err = self.cli('offer', 'ws1', '--minutes', 'soon', '--text-file', self.write('m.txt', 'x'))
        self.assertEqual(code, 1)
        self.assertIn('--minutes takes a positive number', err)
        self.act_as(s)
        code, _, err = self.cli('claim', 'nope')
        self.assertEqual(code, 1)
        self.assertIn('invalid offer id', err)
        code, _, err = self.cli('claim', '20260101-000000-abcdef')
        self.assertEqual(code, 1)
        self.assertIn('does not exist', err)
        code, _, err = self.cli('claim')
        self.assertEqual(code, 1)
        self.assertIn('an offer id, or --next', err)
        code, out, _ = self.cli('offers')
        self.assertIn('no open offer', out)
        self.assertEqual(self.app.store.offer_list(), [])

    def test_a_workspace_not_found_is_noted_and_normalized(self):
        code, out, err = self.cli('offer', 'WS9', '--text-file', self.write('t.txt', 'for a typo'))
        self.assertEqual(code, 0)
        self.assertIn('no workspace named ws9 was found', err)
        self.assertEqual(self.app.store.offer_get(json.loads(out)['id'])['workspace'], 'ws9')

    def test_text_from_stdin(self):
        code, out, err = self.cli('offer', 'ws1', '--text-file', '-', stdin='Piped in\nby an app')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.app.store.offer_get(json.loads(out)['id'])['text'], 'Piped in\nby an app')

    def test_the_guard_keeps_offer_text_out_of_the_shell(self):
        g = Guard(self.app)
        self.assertTrue(g.cli_text_in_shell('signalbox offer ws1 --title "fix the header" --text-file t.txt'))
        self.assertFalse(g.cli_text_in_shell('signalbox offer ws1 --text-file t.txt'))

    def test_a_failed_delivery_puts_the_offer_back(self):
        s = self.session('ws1')
        oid = self.post('Back if it fails', 'ws1', '--from', 'app')
        real = threads.send

        def broken(*a, **k):
            raise Refuse('the box is gone')
        threads.send = broken
        self.addCleanup(setattr, threads, 'send', real)
        self.act_as(s)
        code, _, err = self.cli('claim', oid)
        self.assertEqual(code, 1)
        self.assertIn('the box is gone', err)
        o = self.app.store.offer_get(oid)
        self.assertEqual((o['text'], o['from'], 'sid' in o, 'message' in o), ('Back if it fails', 'app', False, False))
        self.assertIsNone(self.app.store.offer_get(oid, offers.CLAIMED))
        threads.send = real
        self.assertEqual(self.cli('claim', oid)[0], 0)

    def test_an_interrupt_before_the_box_puts_the_offer_back(self):
        s = self.session('ws1')
        oid = self.post('Ctrl+C', 'ws1')
        real = threads.send

        def interrupted(*a, **k):
            raise KeyboardInterrupt
        threads.send = interrupted
        self.addCleanup(setattr, threads, 'send', real)
        self.act_as(s)
        with self.assertRaises(KeyboardInterrupt):
            offers.claim(self.app, oid)
        self.assertIsNotNone(self.app.store.offer_get(oid))
        self.assertIsNone(self.app.store.offer_get(oid, offers.CLAIMED))

    def test_a_failure_after_the_box_keeps_the_claim_and_never_delivers_twice(self):
        s = self.session('ws1')
        oid = self.post('Half way', 'ws1')
        real = threads.open_thread

        def broken(*a, **k):
            raise OSError('disk full')
        threads.open_thread = broken
        self.addCleanup(setattr, threads, 'open_thread', real)
        self.act_as(s)
        with self.assertRaises(OSError):
            offers.claim(self.app, oid)
        self.assertIsNone(self.app.store.offer_get(oid), 'the box has the message: the offer stays claimed')
        self.assertEqual(len(self.app.store.box_list('ws1')), 1)
        threads.open_thread = real
        code, out, err = self.cli('claim', oid)  # the same session again: the same message, now with its thread
        self.assertEqual(code, 0, err)
        self.assertEqual(self.app.store.box_list('ws1'), [])
        self.assertEqual([m['id'] for m in self.delivered()], [oid])
        self.assertIsNotNone(self.app.store.get_thread(oid))

    def test_claim_next_takes_the_oldest_for_this_session(self):
        s = self.session('ws1')
        self.tick(5)
        other = self.post('For ws2', 'ws2')
        first = self.post('First for ws1', 'ws1')
        second = self.post('Then any')
        self.act_as(s)
        code, out, _ = self.cli('claim', '--next', '--json')
        self.assertEqual((code, json.loads(out)['id']), (0, first))
        code, out, _ = self.cli('claim', '--next', '--json')
        self.assertEqual((code, json.loads(out)['id']), (0, second))
        code, _, err = self.cli('claim', '--next')
        self.assertEqual(code, 1)
        self.assertIn('no open offer for this session to claim', err)
        self.assertIsNotNone(self.app.store.offer_get(other))

    def test_minutes_expire_an_offer(self):
        s = self.session('ws1')
        clock = self.freeze()
        oid = self.post('Only for a while', 'ws1', '--minutes', '30')
        self.assertTrue(self.app.store.offer_get(oid)['expires_at'])
        self.act_as(s)
        code, out, _ = self.cli('offers')
        self.assertIn(', until ', out)
        import datetime as dt
        clock[0] += dt.timedelta(minutes=31)
        code, out, _ = self.cli('offers')
        self.assertNotIn(oid, out)
        code, _, err = self.cli('claim', oid)
        self.assertEqual(code, 1)
        self.assertIn(f'offer {oid} expired at', err)
        self.assertEqual(self.events('offer')[-1]['action'], 'expire')
        code, out, _ = self.cli('offers', '--history')
        self.assertIn(f'{oid}  withdrawn', out)
        self.assertIn('(expired)', out)

    def test_item_moves_the_card_of_the_claiming_session(self):
        s = self.session('ws1')
        oid = self.post('With an item', 'ws1', '--item', 'T7')
        self.act_as(s)
        code, out, err = self.cli('claim', oid, '--json')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['item'], 'T7')
        self.assertEqual(self.card(s).get('item'), 'T7')

    def test_an_offer_comes_back_when_its_claimer_ended_without_answering(self):
        a, b = self.session('ws1', name='alpha'), self.session('ws1', name='beta')
        self.tick(5)
        oid = self.post('Anyone?')
        self.act_as(a)
        self.assertEqual(self.cli('claim', oid)[0], 0)
        self.end(a)
        self.act_as(b)
        code, out, _ = self.cli('offers')
        self.assertIn(oid, out, 'alpha ended without a turn in the thread: open again')
        self.assertEqual(self.events('offer')[-1]['action'], 'reopen')
        self.assertEqual(self.app.store.get_thread(oid)['state'], 'closed')
        code, out, _ = self.cli('claim', oid, '--json')
        res = json.loads(out)
        self.assertEqual(code, 0)
        self.assertNotEqual(res['message'], oid, 'a new message, a new thread')
        self.assertEqual(self.app.store.offer_get(oid, offers.CLAIMED)['reopened'], 1)

    def test_an_answered_offer_stays_claimed(self):
        a, b = self.session('ws1', name='alpha'), self.session('ws1', name='beta')
        oid = self.post('Answered')
        self.act_as(a)
        self.assertEqual(self.cli('claim', oid)[0], 0)
        self.assertEqual(self.cli('thread', oid, '--text-file', self.write('r.txt', 'on it'))[0], 0)
        self.end(a)
        self.act_as(b)
        code, out, _ = self.cli('offers')
        self.assertNotIn(oid, out)
        code, out, _ = self.cli('offers', '--history')
        self.assertIn(f'{oid}  claimed', out)
        self.assertNotIn('not answered', out)
