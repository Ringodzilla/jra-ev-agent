"""Screening orchestration tests (synthetic pool only).

Properties: rolling-origin folds never train on the future; the full
60-hypothesis screening produces a record for every hypothesis (held
and diagnostic ones included in N=60); shortlist caps hold; and the
whole screening is invariant to a permutation of final odds (poison
test — final odds are benchmark-only, never an estimation input).
"""
import copy
import unittest

import funnel_screening as fs
from test_funnel_engine import make_race

CONFIRMATION_COUNTS = {'validation': {'芝': 56, 'ダート': 45},
                       'test': {'芝': 44, 'ダート': 19}}


def make_pool(train_days=16, reference_days=4):
    races = []
    venues = ['東京', '京都', '中山', '阪神']
    distances = [1200, 1600, 2000, 2500]
    index = 0
    for block, days in (('train', train_days),
                        ('reference', reference_days)):
        for day in range(days):
            for slot in range(4):
                surface = '芝' if slot % 2 == 0 else 'ダート'
                race = make_race(
                    1000 + index, surface=surface,
                    venue=venues[(day + slot) % 4],
                    distance=distances[(day + slot) % 4],
                    winner=index % 8,
                    date=f'2026-02-{1 + day:02d}')
                race['race_id'] = f'P{index:04d}'
                race['pool_block'] = block
                race['split'] = 'train' if block == 'train' else 'test'
                races.append(race)
                index += 1
    return races


class FoldTests(unittest.TestCase):
    def test_rolling_origin_never_uses_future(self):
        races = [r for r in make_pool() if r['pool_block'] == 'train']
        folds = fs.make_folds(races)
        self.assertEqual(len(folds), 4)
        for fold in folds:
            self.assertLess(max(fold['train_days']),
                            min(fold['eval_days']))
            train_ids = {r['race_id'] for r in fold['train']}
            eval_ids = {r['race_id'] for r in fold['eval']}
            self.assertFalse(train_ids & eval_ids)

    def test_too_few_days_blocked(self):
        races = [make_race(1, date='2026-01-01'),
                 make_race(2, date='2026-01-02')]
        with self.assertRaises(fs.Blocked):
            fs.make_folds(races)


class KillVerdictTests(unittest.TestCase):
    def test_k1_k2_k3_thresholds(self):
        spec = {'kills': ('K1', 'K2', 'K3')}
        verdicts = fs._kill_verdicts(
            spec, [0.02, 0.02, -0.01, 0.02], [0.001] * 4, [], {}, 0)
        self.assertTrue(verdicts['K1']['passed'])
        self.assertTrue(verdicts['K2']['passed'])
        self.assertTrue(verdicts['K3']['passed'])
        verdicts = fs._kill_verdicts(
            spec, [0.001] * 4, [0.01] * 4, [], {}, 0)
        self.assertFalse(verdicts['K1']['passed'])
        self.assertFalse(verdicts['K3']['passed'])

    def test_k7_blocks_confirmation_only(self):
        record = {'status': 'evaluated', 'surface': '芝',
                  'kill_verdicts': {'K1': {'passed': True},
                                    'K7': {'passed': False}}}
        fs._finalize_status(record, CONFIRMATION_COUNTS)
        self.assertEqual(record['status'], 'screening_only')

    def test_dirt_pass_becomes_screening_only(self):
        record = {'status': 'evaluated', 'surface': 'ダート',
                  'kill_verdicts': {'K1': {'passed': True}}}
        fs._finalize_status(record, CONFIRMATION_COUNTS)
        self.assertEqual(record['status'], 'screening_only')
        self.assertIn('dirt_confirmation_block_below_minimum',
                      record['kill_reasons'])


class FullScreeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = fs.run_screening(
            make_pool(train_days=12, reference_days=3),
            CONFIRMATION_COUNTS)

    def test_all_sixty_records_present(self):
        self.assertEqual(self.results['n_hypotheses'], 60)
        statuses = {r['id']: r['status']
                    for r in self.results['hypotheses']}
        for hid in ('PACE-03', 'PACE-06', 'PACE-07'):
            self.assertEqual(statuses[hid], 'held')
        for hid in ('UP-01', 'UP-07'):
            self.assertEqual(statuses[hid], 'diagnostic')
        self.assertFalse(self.results['production_eligible'])

    def test_shortlist_caps(self):
        shortlist = self.results['shortlist']['shortlist']
        self.assertLessEqual(len(shortlist), 8)
        families = {}
        for entry in shortlist:
            families[entry['family']] = families.get(entry['family'],
                                                     0) + 1
        for count in families.values():
            self.assertLessEqual(count, 3)

    def test_dirt_never_shortlisted_from_screening_only(self):
        shortlist_ids = {s['id'] for s in
                         self.results['shortlist']['shortlist']}
        for record in self.results['hypotheses']:
            if record['status'] == 'screening_only':
                self.assertNotIn(record['id'], shortlist_ids)


class PoisonTests(unittest.TestCase):
    def test_screening_invariant_to_final_odds_permutation(self):
        # Permute the final odds of non-winners only.  The winner's odds
        # are a label-side quantity (the K7 longshot population and the
        # market benchmark are defined on them, protocol sections 5.2 /
        # 9); everything estimated or decided by the screening must be
        # invariant to the market values estimation may never use.
        pool = make_pool(train_days=10, reference_days=3)
        first = fs.run_screening(pool, CONFIRMATION_COUNTS)
        permuted = copy.deepcopy(pool)
        for race in permuted:
            winners = set(race['winners'])
            keys = [k for k in race['final_odds'] if k not in winners]
            values = [race['final_odds'][k] for k in keys]
            for key, value in zip(keys, values[::-1]):
                race['final_odds'][key] = value
            import numpy as np
            market = np.array([1.0 / race['final_odds'][k]
                               for k in [r['horse_number']
                                         for r in race['rows']]])
            race['market_probs'] = (market / market.sum()).tolist()
        second = fs.run_screening(permuted, CONFIRMATION_COUNTS)
        self.assertEqual(first['status_counts'], second['status_counts'])
        for a, b in zip(first['hypotheses'], second['hypotheses']):
            if a['status'] in ('held', 'diagnostic'):
                continue
            self.assertEqual(a['selected_grid'], b['selected_grid'],
                             a['id'])
            self.assertAlmostEqual(a['mean_log_loss'],
                                   b['mean_log_loss'], places=8,
                                   msg=a['id'])
            self.assertEqual(a['status'], b['status'], a['id'])
        self.assertEqual(
            [s['id'] for s in first['shortlist']['shortlist']],
            [s['id'] for s in second['shortlist']['shortlist']])


if __name__ == '__main__':
    unittest.main()
