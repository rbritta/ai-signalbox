"""Offers: work put up for a session to claim, delivered the way a `send` delivers, once."""
import json

from helpers import Case

from ai_signalbox import offers, threads
from ai_signalbox.util import Refuse, touch


class OfferTest(Case):
    extra_toml = '[delivery]\nstart_touches = [0, 0, 0]\n'

    def post(self, text, *argv):
        code, out, err = self.cli('offer', *argv, '--text-file', self.write('offer.txt', text))
        self.assertEqual(code, 0, err)
        return json.loads(out)['id']

    def listen(self, s):
        touch(self.app.store.start_path(s['workspace'], s['sid']), 'created')

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
        res = json.loads(out)
        self.assertEqual((res['ok'], res['id'], res['sid']), (True, oid, s['sid']))
        mid = res['message']
        # the message and thread a send to this session makes
        m = next(m for _, m in self.app.store.box_list('ws1') if m['id'] == mid)
        self.assertEqual((m['sid'], m['thread'], m['turn'], m['from'], m['text']),
                         (s['sid'], mid, 1, 'app', 'Check the header spacing\nOnly the panel, no repo.'))
        t = self.app.store.get_thread(mid)
        self.assertEqual((t['workspace'], t['sid'], t['state'], t['turns'][0]['text']),
                         ('ws1', s['sid'], 'open', 'Check the header spacing\nOnly the panel, no repo.'))
        text = self.app.delivery.deliver(s, [m], 'test')
        self.assertIn(f'[app -> ws1', text)
        self.assertIn(f'(opens thread {mid})', text)
        # the offer left the open folder, with who took it
        self.assertIsNone(self.app.store.offer_get(oid))
        c = self.app.store.offer_get(oid, offers.CLAIMED)
        self.assertEqual((c['sid'], c['name'], c['claimed_workspace'], c['message']), (s['sid'], 'alpha', 'ws1', mid))
        ev = self.events('claim')[-1]
        self.assertEqual((ev['id'], ev['message'], ev['sid'], ev['by'], ev['offered_to']),
                         (oid, mid, s['sid'], 'app', 'ws1'))
        self.assertEqual(len(self.events('send')), 1)

    def test_claimed_once(self):
        a, b = self.session('ws1', name='alpha'), self.session('ws1', name='beta')
        oid = self.post('Only one of you')
        self.act_as(a)
        self.assertEqual(self.cli('claim', oid)[0], 0)
        self.act_as(b)
        code, _, err = self.cli('claim', oid)
        self.assertEqual(code, 1)
        self.assertIn(f'offer {oid} was already claimed by ws1 alpha', err)
        self.assertEqual(len(self.app.store.box_list('ws1')), 1, 'the second claim delivers nothing')

    def test_the_rename_decides_a_race(self):
        self.post('Raced for')
        oid = self.app.store.offer_list()[0]['id']
        first = self.app.store.offer_move(oid, offers.OPEN, offers.CLAIMED, {'sid': 'a'})
        second = self.app.store.offer_move(oid, offers.OPEN, offers.CLAIMED, {'sid': 'b'})
        self.assertEqual(first['sid'], 'a')
        self.assertIsNone(second)
        self.assertEqual(self.app.store.offer_get(oid, offers.CLAIMED)['sid'], 'a')

    def test_offer_for_a_workspace_is_not_for_another(self):
        mine, other = self.session('ws1'), self.session('ws2')
        self.tick(5)  # the list goes oldest first
        oid = self.post('For ws1 only', 'ws1')
        anyone = self.post('For any workspace')
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

    def test_withdraw(self):
        s = self.session('ws1')
        oid = self.post('Changed my mind', 'ws1')
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
        self.act_as(s)
        code, _, err = self.cli('claim', 'nope')
        self.assertEqual(code, 1)
        self.assertIn('invalid offer id', err)
        code, _, err = self.cli('claim', '20260101-000000-abcdef')
        self.assertEqual(code, 1)
        self.assertIn('does not exist', err)
        code, out, _ = self.cli('offers')
        self.assertIn('no open offer', out)
        self.assertEqual(self.app.store.offer_list(), [])

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
        self.assertEqual((o['text'], o['from'], 'sid' in o), ('Back if it fails', 'app', False))
        self.assertIsNone(self.app.store.offer_get(oid, offers.CLAIMED))
        threads.send = real
        self.assertEqual(self.cli('claim', oid)[0], 0)
