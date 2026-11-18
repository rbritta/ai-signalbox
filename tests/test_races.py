"""Races between processes: of many claiming one message or one offer at the same instant, exactly one gets it.

Each test starts its workers once; every round they meet at a barrier and go for the same file together. On Windows a
bare rename let every racer win (it renames by handle), so these run on the Windows leg of CI too. Nothing here
touches the real home, the host registry or a model: the world is a temp folder.
"""
import contextlib
import io
import json
import multiprocessing as mp
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

WORKERS, ROUNDS = 6, 20
PATTERN = r'[\\/]work[\\/](ws\d+)(?:[\\/]|$)'


def _world(tmp: Path, n: int) -> Path:
    (tmp / 'work' / 'ws1' / 'app').mkdir(parents=True)
    sessions = tmp / 'host' / 'sessions'
    sessions.mkdir(parents=True)
    for i in range(n):
        (sessions / f'{90000 + i}.json').write_text(json.dumps({
            'pid': 90000 + i, 'sessionId': _sid(i), 'cwd': str(tmp / 'work' / 'ws1' / 'app'), 'name': f'c{i}',
            'status': 'busy', 'startedAt': 1, 'statusUpdatedAt': 1}), encoding='utf-8')
    cfg = (f"root = '{(tmp / 'state').as_posix()}'\nplugins = []\n[workspaces]\npattern = '{PATTERN}'\n"
           f"glob = ['{(tmp / 'work').as_posix()}/ws*']\n[host]\nconfig_dirs = ['{(tmp / 'host').as_posix()}']\n")
    (tmp / 'config.toml').write_text(cfg, encoding='utf-8')
    return tmp / 'config.toml'


def _sid(i):
    return f'sid-ws1-{i:04d}aaaa'


def _app(cfg_path):
    sys.path.insert(0, str(ROOT / 'src'))
    from ai_signalbox.app import App
    from ai_signalbox.config import Config
    return App(Config.load(cfg_path))


def box_worker(cfg_path, keys, i, barrier, out):
    store = _app(cfg_path).store
    for key in keys:
        barrier.wait()
        try:
            got = store.box_claim('ws1', key, {'to_sid': _sid(i)})
            out.put((key, i, 'won' if got else 'lost'))
        except Exception as e:  # noqa: BLE001
            out.put((key, i, f'error {e!r}'))


def offer_worker(cfg_path, oids, i, barrier, out, withdrawer=False):
    app = _app(cfg_path)
    from ai_signalbox import offers
    from ai_signalbox.util import Refuse
    for oid in oids:
        barrier.wait()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                if withdrawer:
                    offers.withdraw(app, oid)
                else:
                    offers.claim(app, oid, sid=_sid(i))
            out.put((oid, i, 'won'))
        except Refuse as e:
            out.put((oid, i, f'lost {e}'))
        except Exception as e:  # noqa: BLE001
            out.put((oid, i, f'error {e!r}'))


class RaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='signalbox-race-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = _world(self.tmp, WORKERS)
        self.app = _app(self.cfg)
        self.ctx = mp.get_context('spawn')

    def race(self, target, ids, extra=lambda i: ()):
        barrier, out = self.ctx.Barrier(WORKERS), self.ctx.Queue()
        procs = [self.ctx.Process(target=target, args=(self.cfg, ids, i, barrier, out, *extra(i)))
                 for i in range(WORKERS)]
        for p in procs:
            p.start()
        results = [out.get(timeout=120) for _ in range(WORKERS * len(ids))]
        for p in procs:
            p.join(60)
        by_id = {}
        for rid, i, kind in results:
            by_id.setdefault(rid, []).append((i, kind))
        errors = [k for v in by_id.values() for _, k in v if k.startswith('error')]
        self.assertEqual(errors, [])
        winners = {rid: [i for i, k in v if k == 'won'] for rid, v in by_id.items()}
        self.assertEqual({rid: len(w) for rid, w in winners.items()}, {rid: 1 for rid in ids},
                         'exactly one winner per round')
        return winners

    def test_box_claim_delivers_a_message_once(self):
        keys = []
        for k in range(ROUNDS):
            key = f'20260101-000000-{k:06x}'
            self.app.store.box_put('ws1', {'id': key, 'workspace': 'ws1', 'text': f'hi {k}'})
            keys.append(f'{key}.json')
        winners = self.race(box_worker, keys)
        self.assertEqual(self.app.store.box_list('ws1'), [])
        for key in keys:
            done = json.loads((self.app.store.box_dir('ws1') / 'delivered' / key).read_text(encoding='utf-8'))
            self.assertEqual(done['to_sid'], _sid(winners[key][0]), 'the winner wrote the delivered record')
        self.assertEqual([p.name for p in self.app.store.box_dir('ws1').glob('.*.tmp')], [])

    def post_offers(self):
        from ai_signalbox import offers
        oids = []
        with contextlib.redirect_stdout(io.StringIO()):
            for k in range(ROUNDS):
                oids.append(offers.offer(self.app, f'work {k}', workspace='ws1')['id'])
        return oids

    def test_claim_against_claim_one_message_per_offer(self):
        from ai_signalbox import offers
        oids = self.post_offers()
        winners = self.race(offer_worker, oids)
        self.assertEqual(self.app.store.offer_list(offers.OPEN), [])
        threads = {t['id'] for t in self.app.store.list_threads()}
        self.assertEqual(threads, set(oids), 'one thread per offer, with the offer id')
        sent = [e for e in self.app.events.read()[0] if e.get('kind') == 'send']
        self.assertEqual(len(sent), ROUNDS, 'one message per offer')
        for oid in oids:
            c = self.app.store.offer_get(oid, offers.CLAIMED)
            self.assertEqual((c['sid'], c['message'], c['delivered']), (_sid(winners[oid][0]), oid, True))

    def test_claim_against_withdraw_one_of_them(self):
        from ai_signalbox import offers
        oids = self.post_offers()
        winners = self.race(offer_worker, oids, extra=lambda i: (i == 0,))
        for oid in oids:
            claimed = self.app.store.offer_get(oid, offers.CLAIMED)
            withdrawn = self.app.store.offer_get(oid, offers.WITHDRAWN)
            self.assertTrue(bool(claimed) != bool(withdrawn), 'claimed or withdrawn, never both')
            self.assertEqual(bool(withdrawn), winners[oid] == [0])
        self.assertEqual(self.app.store.offer_list(offers.OPEN), [])
        self.assertEqual(len(self.app.store.list_threads()), len(self.app.store.offer_list(offers.CLAIMED)))


if __name__ == '__main__':
    unittest.main()
