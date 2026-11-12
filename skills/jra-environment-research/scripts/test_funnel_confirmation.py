"""Confirmation-layer tests for the Issue #40 funnel (synthetic only).

Properties: the validation block list is bound by the handover hash
definition (known-answer); the confirmation loader refuses test-split
entries and block-hash mismatches before opening anything, and loads
only validation races from a fabricated source that also contains
train/test entries (the test entry's audit file is deliberately
corrupt, so any attempt to open it fails the test); the validation
gate implements the protocol section 8 wording with margins; the
selection-fragile flag follows section 7; and a confirmation-style
evaluation is invariant to a permutation of final odds (poison test —
final odds are benchmark-only, never an estimation input).
"""
import copy
import gzip
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import funnel_confirmation as fc
import funnel_registry as fr
from test_funnel_data import _race_fixture
from test_funnel_engine import make_race, make_train


class BlockHashTests(unittest.TestCase):
    ROWS = [{'race_id': '20260201_東京_01', 'surface': '芝'},
            {'race_id': '20260201_京都_02', 'surface': 'ダート'}]
    EXPECTED = ('cd2e40dbe6ac807881e7fdb09257498a1703a94a5a247aaad5'
                'ddb755f92d9680')

    def test_known_answer(self):
        self.assertEqual(fc.block_list_sha256(self.ROWS), self.EXPECTED)

    def test_order_insensitive(self):
        self.assertEqual(fc.block_list_sha256(self.ROWS[::-1]),
                         self.EXPECTED)


class EntryGuardTests(unittest.TestCase):
    def _entry(self, split):
        return {'race_id': '20260315_東京_01',
                'source': fc.EXPANSION_SOURCE, 'split': split,
                'pool_block': 'confirmation_' + split, 'surface': '芝',
                'venue': '東京', 'distance': 1600,
                'band': 'マイル（1500–1800m）', 'date': '2026-03-15',
                'audit_path': '/nonexistent', 'audit_sha256': 'x'}

    def test_test_split_entry_refused(self):
        with self.assertRaises(fc.Blocked):
            fc.check_block_entries([self._entry('test')])

    def test_train_split_entry_refused(self):
        with self.assertRaises(fc.Blocked):
            fc.check_block_entries([self._entry('train')])

    def test_foreign_source_refused(self):
        entry = self._entry('validation')
        entry['source'] = 'migrated_20260502_20260927'
        with self.assertRaises(fc.Blocked):
            fc.check_block_entries([entry])

    def test_validation_entries_accepted(self):
        fc.check_block_entries([self._entry('validation')])


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class LoaderFixtureTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.audit_dir = self.root / 'audit_run_full'
        self.audit_dir.mkdir(parents=True)
        self.dataset = self.root / 'dataset'
        self.dataset.mkdir(parents=True)
        entries, outcomes = [], []
        for rid, surface, split in (
                ('20251004_東京_01', '芝', 'train'),
                ('20260207_東京_02', '芝', 'validation'),
                ('20260208_京都_03', 'ダート', 'validation')):
            entry, outcome = _race_fixture(self.audit_dir, rid, surface,
                                           split)
            entries.append(entry)
            outcomes.append(outcome)
        # A test-split entry whose audit file is corrupt: the loader
        # must never open it.
        corrupt = self.audit_dir / 'corrupt_audit.json'
        corrupt.write_text('this is not json', encoding='utf-8')
        entries.append({
            'race_id': '20260315_東京_09', 'split': 'test',
            'segment': {'surface': '芝', 'venue': '東京',
                        'distance': 1600, 'band': 'マイル（1500–1800m）'},
            'research_admission': {'status': 'ADMITTED_RESEARCH'},
            'audit': {'path': str(corrupt), 'sha256': 'deadbeef'}})
        outcomes.append({'race_id': '20260315_東京_09',
                         'observations': {'rows': []},
                         'final_odds': {'rows': []}})
        self.summary_path = self.audit_dir / 'summary.json'
        self.summary_path.write_text(json.dumps({'races': entries},
                                                ensure_ascii=False),
                                     encoding='utf-8')
        with gzip.open(self.dataset / 'outcomes_2026.jsonl.gz', 'wt',
                       encoding='utf-8') as handle:
            for outcome in outcomes:
                handle.write(json.dumps(outcome, ensure_ascii=False)
                             + '\n')
        self.summary = json.loads(
            self.summary_path.read_text(encoding='utf-8'))
        self.addCleanup(self._tmp.cleanup)

    def _load(self, entries, expected_sha):
        return fc.load_confirmation_races(
            entries, self.summary_path, _digest(self.summary_path),
            self.dataset, self.audit_dir, expected_sha)

    def test_loads_validation_only(self):
        entries = fc.build_block_entries(self.summary, 'validation')
        self.assertEqual(len(entries), 2)
        races = self._load(entries, fc.block_list_sha256(entries))
        self.assertEqual([r['race_id'] for r in races],
                         ['20260207_東京_02', '20260208_京都_03'])
        self.assertTrue(all(r['split'] == 'validation' for r in races))
        dirt = [r for r in races if r['surface'] == 'ダート'][0]
        self.assertIsNone(dirt['environment']['cushion'])

    def test_block_hash_mismatch_refused(self):
        entries = fc.build_block_entries(self.summary, 'validation')
        with self.assertRaises(fc.Blocked):
            self._load(entries, '0' * 64)

    def test_test_entry_refused_before_any_open(self):
        entries = fc.build_block_entries(self.summary, 'validation')
        entries.append({'race_id': '20260315_東京_09',
                        'source': fc.EXPANSION_SOURCE, 'split': 'test',
                        'pool_block': 'confirmation_test',
                        'surface': '芝', 'venue': '東京',
                        'distance': 1600, 'band': 'マイル（1500–1800m）',
                        'date': '2026-03-15',
                        'audit_path': '/nonexistent',
                        'audit_sha256': 'x'})
        with self.assertRaises(fc.Blocked):
            self._load(entries, fc.block_list_sha256(entries))


def _metrics(log_loss, brier, roi):
    return {'race_count': 101, 'log_loss': log_loss, 'brier': brier,
            'top1_hit_rate': 0.1, 'win_top1_policy_roi': roi}


class GateTests(unittest.TestCase):
    def test_pass_with_margins(self):
        gate = fc.validation_gate(_metrics(2.0, 0.20, 0.95),
                                  _metrics(2.2, 0.21, 0.95), 550, 101)
        self.assertEqual(gate['verdict'], 'proceed_to_test')
        self.assertAlmostEqual(gate['log_loss_margin'], 0.2)
        self.assertAlmostEqual(gate['roi_margin'], 0.0)
        self.assertEqual(gate['failed_checks'], [])

    def test_zero_improvement_fails(self):
        gate = fc.validation_gate(_metrics(2.2, 0.20, 1.0),
                                  _metrics(2.2, 0.21, 0.9), 550, 101)
        self.assertEqual(gate['verdict'], 'gate_failed_at_validation')
        self.assertIn('log_loss_improves_over_base',
                      gate['failed_checks'])

    def test_roi_worsening_fails(self):
        gate = fc.validation_gate(_metrics(2.0, 0.20, 0.89),
                                  _metrics(2.2, 0.21, 0.9), 550, 101)
        self.assertEqual(gate['verdict'], 'gate_failed_at_validation')
        self.assertIn('win_policy_roi_not_worse', gate['failed_checks'])

    def test_counts_below_minimum_fail(self):
        gate = fc.validation_gate(_metrics(2.0, 0.20, 1.0),
                                  _metrics(2.2, 0.21, 0.9), 99, 101)
        self.assertIn('train_count_min_100', gate['failed_checks'])
        gate = fc.validation_gate(_metrics(2.0, 0.20, 1.0),
                                  _metrics(2.2, 0.21, 0.9), 550, 19)
        self.assertIn('validation_count_min_20', gate['failed_checks'])


class FragileTests(unittest.TestCase):
    def test_variability_statistics(self):
        stats = fc.fold_variability([0.02, 0.04])
        self.assertAlmostEqual(stats['std'], 0.01)
        self.assertAlmostEqual(stats['range'], 0.02)

    def test_fragile_boundaries(self):
        stats = fc.fold_variability([0.0, 0.04])
        self.assertTrue(fc.selection_fragile(0.01, stats))
        self.assertFalse(fc.selection_fragile(0.05, stats))
        self.assertFalse(fc.selection_fragile(-0.01, stats))
        self.assertFalse(fc.selection_fragile(0.0, stats))


class EvaluateConfirmationTests(unittest.TestCase):
    def _eval_races(self, count=6):
        races = []
        for i in range(count):
            race = make_race(500 + i, surface='芝' if i % 2 == 0
                             else 'ダート', winner=i % 8,
                             date=f'2026-02-{7 + i:02d}')
            race['race_id'] = f'V{i:03d}'
            race['split'] = 'validation'
            race['pool_block'] = 'confirmation_validation'
            races.append(race)
        return races

    def test_record_structure(self):
        train = make_train(24)
        record = fc.evaluate_confirmation(
            train, self._eval_races(), fr.BY_ID['CAL-02'],
            {'ridge': 1.0}, [0.20, 0.25, 0.19, 0.21])
        self.assertEqual(record['fit_races'], 24)
        self.assertEqual(record['eval_races'], 6)
        self.assertIn(record['gate_validation_side']['verdict'],
                      ('proceed_to_test', 'gate_failed_at_validation'))
        self.assertEqual(record['candidate']['race_count'], 6)
        self.assertEqual(record['market']['race_count'], 6)
        diag = record['longshot_diagnostic_report_only']
        self.assertIn('race_count',
                      diag['longshot_winners_odds_ge_10'])
        self.assertFalse(record['production_eligible'])

    def test_evaluation_invariant_to_final_odds_permutation(self):
        train = make_train(24)
        evals = self._eval_races()
        spec = fr.BY_ID['CAL-02']
        first = fc.evaluate_confirmation(train, evals, spec,
                                         {'ridge': 1.0}, [0.2] * 4)
        poisoned_train = copy.deepcopy(train)
        poisoned_evals = copy.deepcopy(evals)
        for race in poisoned_train + poisoned_evals:
            keys = list(race['final_odds'])
            values = list(race['final_odds'].values())
            race['final_odds'] = dict(zip(keys, values[::-1]))
        second = fc.evaluate_confirmation(poisoned_train, poisoned_evals,
                                          spec, {'ridge': 1.0},
                                          [0.2] * 4)
        self.assertAlmostEqual(first['candidate']['log_loss'],
                               second['candidate']['log_loss'], places=12)
        self.assertAlmostEqual(first['candidate']['brier'],
                               second['candidate']['brier'], places=12)
        self.assertAlmostEqual(first['base_form']['log_loss'],
                               second['base_form']['log_loss'], places=12)


if __name__ == '__main__':
    unittest.main()
