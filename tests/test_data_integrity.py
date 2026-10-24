"""Regression coverage for missing inputs and explicit substitution consent."""
import copy
import unittest

from src.data_integrity import audit_collected_data, data_input_sha256, verify_source_artifacts


class DataIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(race_id='r1', horse_number='1', horse_id='h1', run_index='1',
                        date='2026-10-01', course='東京', race_name='新馬', distance='1600',
                        history_surface='芝', track_condition='良', position='1', time='95.4',
                        last_3f='34.5', passing_order='4-3', popularity='2', weight='55',
                        jockey='騎手', pace='M', weather='晴', odds='5.0')
        self.data = {'rows': [self.row], 'entries': [dict(race_id='r1', horse_number='1',
                      assigned_weight='55', current_popularity='3', current_odds='120')],
                     'quality_report': {}}
        self.config = {'r1': {'career_starts_by_horse_number': {'1': 1}}}

    def approval(self, result):
        return dict(approved_by='user', status='APPROVED', race_id='r1', purpose='prediction',
                    input_sha256=result['input_sha256'], evidence='User explicitly approved these substitutions.',
                    finding_ids=[f['finding_id'] for f in result['findings']])

    def test_complete_normalized_is_not_original_source_verified(self):
        result = audit_collected_data(self.data, self.config)
        self.assertEqual(result['gate'], 'UNVERIFIED')
        self.assertEqual(result['findings'], [])
        self.data['source_audit'] = {'status': 'PASS'}
        self.assertEqual(audit_collected_data(self.data, self.config)['gate'], 'UNVERIFIED')

    def test_passing_order_neutral_requires_exact_approval(self):
        self.row['passing_order'] = ''
        result = audit_collected_data(self.data, self.config)
        self.assertEqual(result['gate'], 'BLOCKED_UNAPPROVED_IMPUTATION')
        approval = self.approval(result)
        self.assertEqual(audit_collected_data(self.data, self.config, [approval])['gate'], 'UNVERIFIED')
        for field, value in [('evidence', ''), ('approved_by', 'assistant'), ('purpose', 'research'),
                             ('race_id', 'other'), ('input_sha256', 'other'), ('finding_ids', [])]:
            wrong = dict(approval, **{field: value})
            self.assertEqual(audit_collected_data(self.data, self.config, [wrong])['gate'],
                             'BLOCKED_UNAPPROVED_IMPUTATION')

    def test_required_missing_never_approved(self):
        self.row['time'] = ''
        result = audit_collected_data(self.data, self.config)
        self.assertEqual(audit_collected_data(self.data, self.config, [self.approval(result)])['gate'],
                         'BLOCKED_MISSING_DATA')

    def test_hash_binds_current_odds_not_massive_combo_odds_or_expiry(self):
        digest = data_input_sha256(self.data, self.config)
        self.data['combo_odds'] = [dict(odds=3)]
        self.data['quality_report']['captured_at'] = 'later'
        self.assertEqual(digest, data_input_sha256(self.data, self.config))
        self.data['entries'][0]['current_odds'] = '121'
        self.assertNotEqual(digest, data_input_sha256(self.data, self.config))

    def test_nonempty_parser_fallback_detected(self):
        self.row['last_3f'] = '36.0'
        self.assertFalse(audit_collected_data(self.data, self.config)['findings'])
        self.data['quality_report']['last3f_fallback_rows'] = 1
        result = audit_collected_data(self.data, self.config)
        self.assertEqual(result['gate'], 'BLOCKED_UNAPPROVED_IMPUTATION')
        self.assertIn('parser_last3f_fallback_unlocalized', [f['method'] for f in result['findings']])

    def test_neutral_history_marker(self):
        self.row['neutral_history_fallback'] = 'true'
        self.config['r1']['career_starts_by_horse_number']['1'] = 0
        result = audit_collected_data(self.data, self.config)
        self.assertEqual(result['history_counts'][0]['collected'], 0)
        self.assertIn('neutral_history_fallback', [f['method'] for f in result['findings']])

    def test_all_and_partial_missing_are_separate_from_count(self):
        self.data['rows'].append(dict(self.row, run_index='2', passing_order=''))
        self.config['r1']['career_starts_by_horse_number']['1'] = 2
        self.row['passing_order'] = ''
        result = audit_collected_data(self.data, self.config)
        self.assertEqual(result['missing_field_counts']['passing_order'], 2)
        self.assertEqual(result['history_counts'][0]['status'], 'COMPLETE')
        self.row['passing_order'] = '1-2'
        self.assertEqual(audit_collected_data(self.data, self.config)['missing_field_counts']['passing_order'], 1)

    def test_unknown_history_count_and_no_mutation(self):
        before = copy.deepcopy(self.data)
        result = audit_collected_data(self.data, {})
        self.assertEqual(result['history_counts'][0]['status'], 'UNVERIFIED')
        self.assertEqual(before, self.data)

    def test_raw_html_reparse_can_pass_and_detect_nonempty_mismatch(self):
        import hashlib
        from pathlib import Path
        from jra_scraper.parser import JRAParser
        html = Path('tests/fixtures/horse_history.html').read_text()
        rows = JRAParser(base_url='https://jra.jp').parse_horse_last5(html, 'r1', 'h1', 'A', 'https://jra.jp/history')
        from jra_scraper.validation import _normalize_row
        rows = [_normalize_row(r) for r in rows]
        for row in rows:
            row['horse_number'] = '1'
            row['target_race_date'] = '2026-10-24'
        self.data['rows'] = rows
        self.config['r1']['career_starts_by_horse_number']['1'] = 5
        self.data['source_audit'] = {'artifacts': [dict(kind='horse_history', html=html,
            sha256=hashlib.sha256(html.encode()).hexdigest(), race_id='r1', horse_id='h1',
            horse_name='A', horse_url='https://jra.jp/history')]}
        self.assertEqual(audit_collected_data(self.data, self.config)['gate'], 'PASS')
        rows[0]['run_index'] = '99'
        self.assertEqual(audit_collected_data(self.data, self.config)['gate'], 'PASS')
        rows[0]['passing_order'] = '1-1'
        self.assertEqual(audit_collected_data(self.data, self.config)['gate'], 'UNVERIFIED')
        mismatch = audit_collected_data(self.data, self.config)
        self.assertFalse(mismatch['findings'])
        self.assertTrue(mismatch['verification_issues'])
        self.assertEqual(audit_collected_data(self.data, self.config, [self.approval(mismatch)])['gate'], 'UNVERIFIED')
        rows[0]['time'] = '100'
        self.assertEqual(audit_collected_data(self.data, self.config)['gate'], 'UNVERIFIED')
        rows[0]['target_race_date'] = rows[0]['date']
        issues = audit_collected_data(self.data, self.config)['verification_issues']
        self.assertTrue(any(i['field'] == 'source_row' for i in issues))

    def test_raw_reparse_recovers_fallback_and_explicit_approval_allows_pass(self):
        import hashlib
        from pathlib import Path
        from jra_scraper.parser import JRAParser
        from jra_scraper.validation import _normalize_row
        html = Path('tests/fixtures/horse_history.html').read_text().replace('<td>34.2</td>', '<td></td>')
        raw = JRAParser(base_url='https://jra.jp').parse_horse_last5(html, 'r1', 'h1', 'A', 'https://jra.jp/history')
        self.data['rows'] = [_normalize_row(r) for r in raw]
        for row in self.data['rows']:
            row['horse_number'] = '1'
            row['target_race_date'] = '2026-10-24'
            row.pop('last_3f_source', None)
        self.config['r1']['career_starts_by_horse_number']['1'] = 5
        self.data['source_audit'] = {'artifacts': [dict(kind='horse_history', html=html,
            sha256=hashlib.sha256(html.encode()).hexdigest(), race_id='r1', horse_id='h1',
            horse_name='A', horse_url='https://jra.jp/history')]}
        result = audit_collected_data(self.data, self.config)
        self.assertEqual(result['gate'], 'BLOCKED_UNAPPROVED_IMPUTATION')
        self.assertFalse(result['verification_issues'])
        self.assertTrue(result['recovered_imputation_provenance'])
        approved = audit_collected_data(self.data, self.config, [self.approval(result)])
        self.assertEqual(approved['gate'], 'PASS')

    def test_japanese_history_date_and_repair_action_horse_id(self):
        self.row['date'] = '2026年10月1日'
        self.data['entries'][0]['horse_id'] = 'h1'
        self.data['quality_report']['missing_data_repair_actions'] = [
            dict(horse_id='h1', required_history_count=1)]
        result = audit_collected_data(self.data, {})
        self.assertEqual(result['history_counts'][0]['status'], 'COMPLETE')
        self.assertNotEqual(result['gate'], 'BLOCKED_MISSING_DATA')

    def test_quality_fallback_localized_by_context(self):
        self.data['quality_report'] = {'last3f_fallback_rows': 1, 'issues': [
            dict(code='last3f_fallback', context={'horse_id': 'h1', 'run_index': '1'})]}
        result = audit_collected_data(self.data, self.config)
        self.assertEqual(len(result['findings']), 1)
        self.assertEqual(result['findings'][0]['race_id'], 'r1')
        self.assertEqual(result['findings'][0]['horse_id'], 'h1')

    def test_invalid_sources_and_unknown_artifact_never_prove_origin(self):
        import hashlib
        for artifact in [dict(kind='horse_history', html='x', sha256='wrong'),
                         dict(kind='race_detail', html='x', sha256=hashlib.sha256(b'x').hexdigest()),
                         dict(kind='unknown', html='x', sha256=hashlib.sha256(b'x').hexdigest())]:
            self.data['source_audit'] = {'artifacts': [artifact]}
            self.assertNotEqual(verify_source_artifacts(self.data)['status'], 'PASS')
        self.assertEqual(verify_source_artifacts({'rows': []})['status'], 'UNVERIFIED')

    def test_race_embedded_source_candidates_and_japanese_date_cutoff(self):
        import hashlib
        from types import SimpleNamespace
        from unittest.mock import patch
        html = '<html>race detail fixture</html>'
        self.row['date'] = '2026年10月1日'
        self.row['target_race_date'] = '2026-10-24'
        self.data['source_audit'] = {'artifacts': [dict(kind='race_detail', html=html,
            sha256=hashlib.sha256(html.encode()).hexdigest(), race_id='r1')]}
        horse = SimpleNamespace(horse_id='h1', horse_number='1', horse_name='A', embedded_history=[self.row])
        with patch('jra_scraper.parser.JRAParser.parse_race_detail', return_value=[horse]):
            self.assertEqual(verify_source_artifacts(self.data)['status'], 'PASS')
            self.row['target_race_date'] = '2026-10-01'
            self.assertEqual(verify_source_artifacts(self.data)['status'], 'MISMATCH')

    def test_history_count_gap_and_entry_fallbacks(self):
        self.data['entries'][0]['assigned_weight'] = ''
        result = audit_collected_data(self.data, [dict(race_id='r1', career_starts_by_horse_number={'1': 2})])
        self.assertEqual(result['gate'], 'BLOCKED_MISSING_DATA')
        self.assertTrue(any(f['field'] == 'assigned_weight' for f in result['findings']))
        self.assertEqual(audit_collected_data(self.data, None)['history_counts'][0]['status'], 'UNVERIFIED')

    def test_invalid_dates_fallback_marker_surface_and_jump_neutralization(self):
        self.row.update(date='bad-date', last_3f_source='fallback', race_name='障害未勝利',
                        history_surface='', distance='芝1600', weather='')
        result = audit_collected_data(self.data, self.config)
        self.assertEqual(result['gate'], 'BLOCKED_MISSING_DATA')
        self.assertTrue(any(f['method'] == 'neutralize_jump_sectional_36' for f in result['findings']))
        self.assertFalse(any(m['field'] == 'history_surface' for m in result['missing_fields']))
        self.assertTrue(any(m['field'] == 'weather' and not m['model_used'] for m in result['missing_fields']))

    def test_numeric_neutral_derived_from_observation_not_flagged(self):
        self.row['front_rate'] = '0.5'
        self.assertEqual(audit_collected_data(self.data, self.config)['findings'], [])


if __name__ == '__main__':
    unittest.main()
