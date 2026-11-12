"""Loader isolation tests for the Issue #40 funnel (fabricated sources).

The confirmation pool must be unreachable from screening code: these
tests fabricate two admission sources in a temporary directory and
assert that expansion validation/test entries, symlinks, paths outside
the allowlisted roots, duplicate IDs and tampered artifacts are all
rejected before (or at) load, and that turf/dirt environment handling
follows the surface base-form contracts (dirt cushion NOT_APPLICABLE,
never substituted).
"""
import gzip
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import funnel_data as fd


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _race_fixture(base: Path, rid, surface, split):
    venue = '東京' if surface == '芝' else '京都'
    target = dict(race_id=rid, horse_id='h1', horse_name='人工1',
                  horse_number='1', frame_number='1',
                  target_track=venue, target_surface=surface,
                  target_distance='1600', target_track_condition='良',
                  target_weather='晴', target_race_date='2026-06-06',
                  assigned_weight='55', current_jockey='人工騎手')
    entries, rows = [], []
    for horse in (1, 2):
        entry = dict(target, horse_id=f'h{horse}',
                     horse_name=f'人工{horse}', horse_number=str(horse),
                     frame_number=str(horse))
        entries.append(entry)
        for run in (1, 2):
            rows.append(dict(
                entry, run_index=str(run),
                date=f'2026-05-{10 + run:02d}', position=str(horse),
                time=str(94 + horse), distance='1600',
                last_3f=str(33 + horse),
                passing_order=f'{horse}-{horse}', weight='55',
                course=venue, history_surface=surface,
                track_condition='良', jockey='人工騎手',
                popularity=str(horse), odds=str(2 + horse)))
    normalized = base / f'{rid}_normalized.json'
    normalized.write_text(json.dumps({'rows': rows, 'entries': entries},
                                     ensure_ascii=False),
                          encoding='utf-8')
    if surface == '芝':
        environment = {
            'cushion': {'measured_at': '2026-06-06T07:00:00+09:00',
                        'cushion': 9.6},
            'moisture': {'measured_at': '2026-06-06T06:00:00+09:00',
                         'turf_moisture_goal': 10.2,
                         'turf_moisture_corner4': 10.4,
                         'dirt_moisture_goal': 9.1,
                         'dirt_moisture_corner4': 9.3}}
    else:
        environment = {
            'cushion': None,
            'cushion_applicability': 'NOT_APPLICABLE',
            'moisture': {'measured_at': '2026-06-06T06:00:00+09:00',
                         'turf_moisture_goal': 10.2,
                         'turf_moisture_corner4': 10.4,
                         'dirt_moisture_goal': 9.1,
                         'dirt_moisture_corner4': 9.3}}
    segment = {'surface': surface, 'venue': venue, 'distance': 1600,
               'band': 'マイル（1500–1800m）'}
    audit = {'race_id': rid, 'environment_gate': 'PASS',
             'surface_gate': 'PASS', 'split': split, 'segment': segment,
             'post_time': '2026-06-06T15:00:00+09:00',
             'environment': environment,
             'normalized_artifact': {'path': str(normalized),
                                     'sha256': _digest(normalized)},
             'official_nonstarters': []}
    audit_path = base / f'{rid}_audit.json'
    audit_path.write_text(json.dumps(audit, ensure_ascii=False),
                          encoding='utf-8')
    outcome = {
        'race_id': rid,
        'observations': {'rows': [
            {'horse_number': '1', 'position': '2', 'popularity': '2'},
            {'horse_number': '2', 'position': '1', 'popularity': '1'}]},
        'final_odds': {'field_universe_match': True, 'rows': [
            {'horse_number': '1', 'odds': '3.5'},
            {'horse_number': '2', 'odds': '1.9'}]},
    }
    summary_entry = {
        'race_id': rid, 'split': split, 'segment': segment,
        'research_admission': {'status': 'ADMITTED_RESEARCH'},
        'audit': {'path': str(audit_path), 'sha256': _digest(audit_path)},
    }
    return summary_entry, outcome


def _source_fixture(root: Path, name, specs):
    audit_dir = root / name / 'audit'
    audit_dir.mkdir(parents=True)
    dataset = root / name / 'dataset'
    dataset.mkdir(parents=True)
    entries, outcomes = [], []
    for rid, surface, split in specs:
        entry, outcome = _race_fixture(audit_dir, rid, surface, split)
        entries.append(entry)
        outcomes.append(outcome)
    summary_path = audit_dir / 'summary.json'
    summary_path.write_text(json.dumps({'races': entries},
                                       ensure_ascii=False),
                            encoding='utf-8')
    with gzip.open(dataset / 'outcomes_2026-06.jsonl.gz', 'wt',
                   encoding='utf-8') as handle:
        for outcome in outcomes:
            handle.write(json.dumps(outcome, ensure_ascii=False) + '\n')
    return {'summary_path': summary_path, 'summary_sha256':
            _digest(summary_path), 'dataset_dir': dataset,
            'audit_dir': audit_dir, 'entries': entries}


class FunnelDataTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.migrated = _source_fixture(self.root, 'migrated', [
            ('20260606_東京_01', '芝', 'train'),
            ('20260606_京都_02', 'ダート', 'validation')])
        self.expansion = _source_fixture(self.root, 'expansion', [
            ('20251004_東京_01', '芝', 'train'),
            ('20251004_京都_02', 'ダート', 'train')])
        self._sources_backup = fd.SOURCES
        fd.SOURCES = {
            'migrated_20260502_20260927': {
                'summary_path': 'migrated/audit/summary.json',
                'dataset_dir': 'migrated/dataset',
                'allowed_splits': ('train', 'validation', 'test')},
            'expansion_20251001_20260430': {
                'summary_path': 'expansion/audit/summary.json',
                'dataset_dir': 'expansion/dataset',
                'allowed_splits': ('train',)},
        }
        self.addCleanup(self._restore)

    def _restore(self):
        fd.SOURCES = self._sources_backup
        self._tmp.cleanup()

    def _manifest(self, extra_entries=()):
        sources, races = [], []
        for source_id, fixture in (
                ('migrated_20260502_20260927', self.migrated),
                ('expansion_20251001_20260430', self.expansion)):
            sources.append({
                'source_id': source_id,
                'summary_path':
                    fd.SOURCES[source_id]['summary_path'],
                'summary_sha256': fixture['summary_sha256'],
                'dataset_dir': fd.SOURCES[source_id]['dataset_dir'],
                'allowed_splits':
                    list(fd.SOURCES[source_id]['allowed_splits'])})
            for entry in fixture['entries']:
                if entry['split'] not in \
                        fd.SOURCES[source_id]['allowed_splits']:
                    continue
                segment = entry['segment']
                races.append({
                    'race_id': entry['race_id'], 'source': source_id,
                    'split': entry['split'],
                    'pool_block': 'train' if entry['split'] == 'train'
                    else 'reference',
                    'surface': segment['surface'],
                    'venue': segment['venue'],
                    'distance': segment['distance'],
                    'band': segment['band'], 'date': '2026-06-06',
                    'audit_path': entry['audit']['path'],
                    'audit_sha256': entry['audit']['sha256']})
        races.extend(extra_entries)
        manifest = {'schema': 'test', 'sources': sources,
                    'counts': {'total': len(races)}, 'races': races}
        manifest['manifest_sha256'] = fd._canonical_sha(manifest)
        return manifest

    def _roots(self):
        return [self.migrated['audit_dir'], self.expansion['audit_dir']]

    def test_loads_both_surfaces_with_surface_environment(self):
        races = fd.load_screening_races(self._manifest(), self._roots(),
                                        root=self.root)
        self.assertEqual(len(races), 4)
        dirt = [r for r in races if r['surface'] == 'ダート']
        self.assertEqual(len(dirt), 2)
        for race in dirt:
            self.assertIsNone(race['environment']['cushion'])
            self.assertAlmostEqual(
                race['environment']['moisture_goal'], 9.1)
        turf = [r for r in races if r['surface'] == '芝'][0]
        self.assertAlmostEqual(turf['environment']['cushion'], 9.6)
        self.assertAlmostEqual(
            turf['environment']['moisture_goal'], 10.2)

    def test_expansion_validation_entry_rejected(self):
        entry = {'race_id': '20260201_東京_99',
                 'source': 'expansion_20251001_20260430',
                 'split': 'validation', 'pool_block': 'reference',
                 'surface': '芝', 'venue': '東京', 'distance': 1600,
                 'band': 'マイル（1500–1800m）', 'date': '2026-02-01',
                 'audit_path': '/nonexistent/audit.json',
                 'audit_sha256': 'x'}
        with self.assertRaises(fd.Blocked):
            fd.load_screening_races(self._manifest([entry]),
                                    self._roots(), root=self.root)

    def test_duplicate_race_id_rejected(self):
        manifest = self._manifest()
        manifest['races'].append(dict(manifest['races'][0]))
        manifest['manifest_sha256'] = fd._canonical_sha(
            {k: v for k, v in manifest.items()
             if k != 'manifest_sha256'})
        with self.assertRaises(fd.Blocked):
            fd.load_screening_races(manifest, self._roots(),
                                    root=self.root)

    def test_manifest_tamper_rejected(self):
        manifest = self._manifest()
        manifest['counts'] = {'total': 999}
        with self.assertRaises(fd.Blocked):
            fd.load_screening_races(manifest, self._roots(),
                                    root=self.root)

    def test_symlink_audit_rejected(self):
        manifest = self._manifest()
        target = Path(manifest['races'][0]['audit_path'])
        link = self.root / 'linked_audit.json'
        link.symlink_to(target)
        manifest['races'][0]['audit_path'] = str(link)
        manifest['manifest_sha256'] = fd._canonical_sha(
            {k: v for k, v in manifest.items()
             if k != 'manifest_sha256'})
        with self.assertRaises(fd.Blocked):
            fd.load_screening_races(manifest, self._roots(),
                                    root=self.root)

    def test_path_outside_roots_rejected(self):
        outside = self.root / 'outside.json'
        outside.write_text('{}', encoding='utf-8')
        with self.assertRaises(fd.Blocked):
            fd._checked_path(outside, self._roots())

    def test_tampered_audit_rejected(self):
        manifest = self._manifest()
        target = Path(manifest['races'][0]['audit_path'])
        target.write_text(target.read_text(encoding='utf-8') + ' ',
                          encoding='utf-8')
        with self.assertRaises(fd.Blocked):
            fd.load_screening_races(manifest, self._roots(),
                                    root=self.root)


if __name__ == '__main__':
    unittest.main()
