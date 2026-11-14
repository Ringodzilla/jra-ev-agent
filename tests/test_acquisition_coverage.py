"""Observed fields cannot be omitted because another source has an empty cell.

Revised 2026-11-13 for the history measurement contract (Issue #39):
past-run odds, pace and weather are not measured for history rows, so
they emit no coverage records; the protections below now stand on the
fields the contract still measures.
"""
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

    def test_observed_value_is_required_even_when_model_does_not_use_it(self):
        self.row['last_3f'] = ''
        with self.sources([{'last_3f': ''}, {'last_3f': '34.5'}]):
            a = audit_collected_data(self.data, self.config)
        records = a['acquisition_coverage']['records']
        self.assertEqual(next(r['state'] for r in records if r['field'] == 'last_3f'),
                         'AVAILABLE_BUT_NOT_COLLECTED')
        self.assertTrue(any(x.get('reason_code') == 'AVAILABLE_BUT_NOT_COLLECTED'
                            for x in a['verification_issues']))
        # The omitted measured field also leaves its imputation finding,
        # which outranks the acquisition block in gate precedence.
        self.assertEqual(a['gate'], 'BLOCKED_UNAPPROVED_IMPUTATION')

    def test_history_contract_fields_emit_no_coverage_records(self):
        with self.sources([{}]):
            a = audit_collected_data(self.data, self.config)
        fields = {r['field'] for r in a['acquisition_coverage']['records']}
        self.assertTrue(fields.isdisjoint({'odds', 'pace', 'weather'}))
        self.assertIn('last_3f', fields)
        contract = a['history_measurement_contract']
        self.assertEqual(contract['not_measured_fields'],
                         ['odds', 'pace', 'weather'])
        self.assertEqual(contract['decided'], '2026-11-13')
        self.assertTrue(any('not measured by contract' in limit
                            for limit in a['limitations']))

    def test_incompatible_matched_source_semantics_are_not_silently_coalesced(self):
        with self.sources([
            {'last_3f': '', 'last_3f_status': 'NOT_APPLICABLE'},
            {'last_3f': '34.5', 'last_3f_status': 'OBSERVED'},
        ]):
            result = verify_source_artifacts(self.data)
        self.assertEqual(result['status'], 'MISMATCH')
        self.assertTrue(any(x.get('reason_code') == 'SOURCE_SEMANTIC_CONFLICT'
                            for x in result['mismatches']))

    def test_number_without_an_observed_source_is_substituted_even_when_nonempty(self):
        source = dict(self.row, last_3f='', _source_artifact={'path': 'original'})
        record = field_coverage(self.row, self.row, 'last_3f', [source], self.data)
        self.assertEqual(record['state'], 'SUBSTITUTED')
        self.assertEqual(record['observed_values'], [])

    def test_reparse_recovers_fallback_provenance_removed_by_caller(self):
        self.row['last_3f'] = '36'
        with self.sources([{'last_3f': '36', 'last_3f_source': 'fallback'}]):
            result = audit_collected_data(self.data, self.config)
        self.assertTrue(any(x['method'] == 'parser_last3f_fallback'
                            for x in result['findings']))
        self.assertEqual(result['gate'], 'BLOCKED_UNAPPROVED_IMPUTATION')

    def test_weather_omission_is_outside_history_coverage_by_contract(self):
        # Weather is a contract field since 2026-11-13: an omitted past-run
        # weather value produces no coverage record and no mismatch. The
        # pre-contract protection is preserved for measured fields by
        # test_observed_value_is_required_even_when_model_does_not_use_it.
        self.row['weather'] = ''
        with self.sources([{'weather': ''}, {'weather': '雨'}]):
            result = verify_source_artifacts(self.data)
        self.assertEqual(result['status'], 'PASS')
        self.assertFalse(any(r['field'] == 'weather'
                             for r in result['acquisition_coverage']['records']))

    def test_no_raw_cannot_self_attest_unavailability(self):
        self.row['pace'] = ''
        self.data['source_audit'] = {'status': 'PASS', 'unavailable': True}
        result = audit_collected_data(self.data, self.config)
        self.assertEqual(result['gate'], 'UNVERIFIED')
        self.assertEqual(result['acquisition_coverage']['counts'], {'SOURCE_UNVERIFIED': 13})

    def test_raw_no_boundary_reason_is_distinct_from_unattempted(self):
        # field_coverage still classifies the validated 600m-boundary case
        # as NOT_MEASURED; at audit level pace is a contract field since
        # 2026-11-13 and emits no coverage record at all.
        row = dict(self.row, pace='')
        original = dict(row,
                        _pace_missing_reason='NO_600M_BOUNDARY_PARTIAL_INTERVAL_NOT_ESTIMATED',
                        _source_artifact={'path': 'original'})
        record = field_coverage(row, row, 'pace', [original], self.data)
        self.assertEqual(record['state'], 'NOT_MEASURED')
        self.row['pace'] = ''
        with self.sources([{'pace_missing_reason': 'NO_600M_BOUNDARY_PARTIAL_INTERVAL_NOT_ESTIMATED'}]):
            result = audit_collected_data(self.data, self.config)
        self.assertEqual(result['gate'], 'PASS')
        self.assertFalse(any(r['field'] == 'pace'
                             for r in result['acquisition_coverage']['records']))
        with self.sources([{}]):
            self.assertEqual(audit_collected_data(self.data, self.config)['gate'],
                             'PASS')

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
        self.assertEqual(result['acquisition_coverage']['counts'], {'SOURCE_UNVERIFIED': 13})

    def test_standing_method_approval_covers_matching_finding_only(self):
        # Fixture field is popularity: past-run odds left the audited
        # model inputs on 2026-11-14 and can no longer produce a finding.
        self.row['popularity'] = ''
        standing = {'kind': 'standing_method', 'approved_by': 'user',
                    'status': 'APPROVED', 'field': 'popularity',
                    'method': 'default_10',
                    'evidence': 'User decision 2026-11-13'}
        self.data['source_audit'] = {'artifacts': [
            dict(kind='horse_history', race_id='r1', horse_id='h1',
                 html='fixture-0',
                 sha256=hashlib.sha256(b'fixture-0').hexdigest(),
                 path='fixture-0.html')]}
        with patch('jra_scraper.parser.JRAParser.parse_horse_last5',
                   side_effect=lambda *a, **k: [dict(self.row)]):
            base = audit_collected_data(self.data, self.config)
            approved = audit_collected_data(self.data, self.config, [standing])
            no_evidence = audit_collected_data(
                self.data, self.config, [dict(standing, evidence=' ')])
        self.assertEqual(base['gate'], 'BLOCKED_UNAPPROVED_IMPUTATION')
        self.assertEqual(base['standing_approvals_applied'], [])
        # The standing approval covers the imputation finding, but the
        # acquisition layer still records the source-missing field, so
        # the gate moves to the coverage stop rather than PASS (unlike
        # the retired odds fixture, popularity is not a contract field).
        self.assertEqual(approved['gate'], 'BLOCKED_ACQUISITION_INCOMPLETE')
        self.assertEqual(approved['standing_approvals_applied'],
                         [{'field': 'popularity',
                           'method': 'default_10'}])
        self.assertEqual(no_evidence['gate'], 'BLOCKED_UNAPPROVED_IMPUTATION')

    def test_source_unsupported_field_blocks_without_standing_approval(self):
        self.row['passing_order'] = ''
        with self.sources([{'passing_order': '',
                            'passing_order_status': 'NOT_OBSERVED'}]):
            result = audit_collected_data(self.data, self.config)
        unsupported = [x for x in result['findings']
                       if x['field'] == 'passing_order']
        self.assertEqual(len(unsupported), 1)
        self.assertEqual(unsupported[0]['method'], 'unsupported_source_observation')
        self.assertTrue(unsupported[0]['required_missing'])
        self.assertFalse(unsupported[0]['approved'])
        self.assertEqual(result['gate'], 'BLOCKED_MISSING_DATA')

    def test_source_unsupported_field_uses_standing_approved_substitution(self):
        # User decision 2026-11-14: a source that cannot carry a field is
        # the same informational situation as a missing value; where the
        # field's substitution is standing-approved, it applies.
        self.row['passing_order'] = ''
        standing = {'kind': 'standing_method', 'approved_by': 'user',
                    'status': 'APPROVED', 'field': 'passing_order',
                    'method': 'neutral_0.5',
                    'evidence': 'User decision 2026-11-13 (Arima scope)'}
        with self.sources([{'passing_order': '',
                            'passing_order_status': 'NOT_OBSERVED'}]):
            result = audit_collected_data(self.data, self.config, [standing])
        findings = [x for x in result['findings'] if x['field'] == 'passing_order']
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['method'], 'neutral_0.5')
        self.assertTrue(findings[0]['approved'])
        self.assertFalse(findings[0]['required_missing'])
        self.assertEqual(result['standing_approvals_applied'],
                         [{'field': 'passing_order', 'method': 'neutral_0.5'}])
        # The finding no longer blocks as missing data. The acquisition
        # coverage layer is a separate stop (SOURCE_FIELD_MISSING record)
        # and is not lifted by a substitution approval.
        self.assertEqual(result['gate'], 'BLOCKED_ACQUISITION_INCOMPLETE')

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
