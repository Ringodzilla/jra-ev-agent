"""Observed fields cannot be omitted because another source has an empty cell."""
import copy
import hashlib
import unittest
from unittest.mock import patch

from src.acquisition_coverage import field_coverage
from src.data_integrity import audit_collected_data, verify_source_artifacts


class AcquisitionCoverageTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(race_id='r1', horse_id='h1', horse_number='1', run_index='1',
                        date='2026-10-01', target_race_date='2026-10-25', course='東京',
                        race_name='新馬', distance='1600', history_surface='芝',
                        track_condition='良', position='1', time='95.4', last_3f='34.5',
                        passing_order='3-2', popularity='2', weight='55', jockey='騎手',
                        pace='35', weather='晴', odds='5')
        self.config = {'r1': {'career_starts_by_horse_number': {'1': 1}}}
        self.data = dict(rows=[self.row], entries=[dict(race_id='r1', horse_number='1',
                         assigned_weight='55', current_popularity='2', current_odds='5')])

    def sources(self, observations):
        artifacts = [dict(kind='horse_history', race_id='r1', horse_id='h1',
                          html=f'fixture-{i}', sha256=hashlib.sha256(f'fixture-{i}'.encode()).hexdigest(),
                          path=f'fixture-{i}.html') for i in range(len(observations))]
        self.data['source_audit'] = {'artifacts': artifacts}
        return patch('jra_scraper.parser.JRAParser.parse_horse_last5',
                     side_effect=[[dict(self.row, **o)] for o in observations])

    def test_observed_pace_is_required_even_when_model_does_not_use_it(self):
        self.row['pace'] = ''
        with self.sources([{'pace': ''}, {'pace': '35'}]):
            a = audit_collected_data(self.data, self.config)
        self.assertEqual(a['gate'], 'BLOCKED_ACQUISITION_INCOMPLETE')
        records = a['acquisition_coverage']['records']
        self.assertEqual(next(r['state'] for r in records if r['field'] == 'pace'),
                         'AVAILABLE_BUT_NOT_COLLECTED')
        self.assertEqual(a['findings'], [])

    def test_optional_weather_omission_cannot_hide_behind_empty_source(self):
        self.row['weather'] = ''
        with self.sources([{'weather': ''}, {'weather': '雨'}]):
            result = verify_source_artifacts(self.data)
        self.assertEqual(result['status'], 'MISMATCH')
        self.assertEqual(result['mismatches'][0]['reason_code'], 'AVAILABLE_BUT_NOT_COLLECTED')

    def test_no_raw_cannot_self_attest_unavailability(self):
        self.row['pace'] = ''
        self.data['source_audit'] = {'status': 'PASS', 'unavailable': True}
        result = audit_collected_data(self.data, self.config)
        self.assertEqual(result['gate'], 'UNVERIFIED')
        self.assertEqual(result['acquisition_coverage']['counts'], {'SOURCE_UNVERIFIED': 16})

    def test_raw_no_boundary_reason_is_distinct_from_unattempted(self):
        self.row['pace'] = ''
        with self.sources([{'pace_missing_reason': 'NO_600M_BOUNDARY_PARTIAL_INTERVAL_NOT_ESTIMATED'}]):
            result = audit_collected_data(self.data, self.config)
        self.assertEqual(result['gate'], 'PASS')
        self.assertEqual(result['acquisition_coverage']['counts']['NOT_MEASURED'], 1)
        with self.sources([{}]):
            self.assertEqual(audit_collected_data(self.data, self.config)['gate'],
                             'BLOCKED_ACQUISITION_INCOMPLETE')

    def test_attempt_bound_error_is_failed_and_not_applicable_declaration_is_ignored(self):
        source = dict(self.row, pace='', _source_artifact={'path': 'x', 'kind': 'horse_history'})
        data = copy.deepcopy(self.data)
        data['source_audit'] = {'attempts': [dict(race_id='r1', horse_id='h1', date='2026-10-01',
                                  fields=['pace'], artifact_sha256='verified')],
                                'artifacts': [dict(sha256='verified', html='パラメータエラー013')]}
        self.row['pace'] = ''
        self.assertEqual(field_coverage(self.row, self.row, 'pace', [source], data)['state'],
                         'FETCH_FAILED')
        data['source_audit']['artifacts'][0]['html'] = '<html>no field</html>'
        self.assertEqual(field_coverage(self.row, self.row, 'pace', [source], data)['state'],
                         'SOURCE_FIELD_MISSING')
        data['source_audit']['attempts'] = []
        self.row['pace_missing_reason'] = 'NO_600M_BOUNDARY_PARTIAL_INTERVAL_NOT_ESTIMATED'
        self.assertEqual(field_coverage(self.row, self.row, 'pace', [source], data)['state'],
                         'NOT_ATTEMPTED')
        source['_source_artifact']['kind'] = 'history_result'
        self.assertEqual(field_coverage(self.row, self.row, 'pace', [source], data)['state'],
                         'SOURCE_FIELD_MISSING')

    def test_hash_failure_cannot_claim_not_measured(self):
        with self.sources([{}]):
            self.data['source_audit']['artifacts'][0]['sha256'] = 'tampered'
            result = verify_source_artifacts(self.data)
        self.assertEqual(result['acquisition_coverage']['counts'], {'SOURCE_UNVERIFIED': 16})

    def test_cached_sources_preserve_recovered_artifacts_and_attempts(self):
        import tempfile
        from pathlib import Path
        from src.skill_workflow import attach_history_sources
        recovered = dict(kind='history_result', html='original result', sha256='retained')
        attempt = dict(race_id='r1', horse_id='h1', date='2026-10-01', fields=['odds'],
                       artifact_sha256='retained')
        self.data['source_audit'] = dict(artifacts=[recovered], attempts=[attempt])
        before = copy.deepcopy(self.data['source_audit'])
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            (raw / 'horse_h1.html').write_text('cached horse source')
            attach_history_sources(self.data, raw)
            attach_history_sources(self.data, raw)
        self.assertEqual(self.data['source_audit']['attempts'], [attempt])
        self.assertEqual(self.data['source_audit']['artifacts'][0], recovered)
        self.assertEqual(len(self.data['source_audit']['artifacts']), 2)
        self.assertEqual(before, dict(artifacts=[recovered], attempts=[attempt]))


if __name__ == '__main__':
    unittest.main()
