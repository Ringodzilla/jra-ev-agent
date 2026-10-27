#!/usr/bin/env python3
"""Read-only retrospective batch audit; writes only a fresh audit_run directory."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.data_integrity import audit_collected_data, verify_source_artifacts
from jra_scraper.validation import _normalize_row


def date_key(value):
    parts = re.findall(r'\d+', str(value))
    return f'{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}' if len(parts) >= 3 else ''


def day_matches(value, expected):
    return date_key(value) == expected


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-dir', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--environment', type=Path)
    parser.add_argument('--enriched', action='store_true', help='Audit enriched_races; preserve original races unchanged')
    parser.add_argument('--input-dir', help='Named batch subdirectory, e.g. enriched_v3')
    parser.add_argument('--manifest', default='manifest.json', help='Batch source manifest filename')
    parser.add_argument('--race-id', action='append', help='Independent numeric race ID; repeat to audit a fixed subset')
    args = parser.parse_args()
    base = args.batch_dir.resolve()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = base / ('audit_run_' + stamp)
    output.mkdir(exist_ok=False)
    manifest_bytes = (base / args.manifest).read_bytes()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = json.loads(manifest_bytes)
    manifest_snapshots = [{'path': str((base / args.manifest).resolve()), 'sha256': manifest_sha}]
    if args.manifest != 'manifest.json':
        original_manifest_bytes = (base / 'manifest.json').read_bytes()
        manifest = json.loads(original_manifest_bytes) + manifest
        manifest_snapshots.insert(0, {'path': str((base / 'manifest.json').resolve()),
                                      'sha256': hashlib.sha256(original_manifest_bytes).hexdigest()})
    indexed = {a['url']: a for a in manifest}
    environment_path = args.environment or sorted((ROOT / 'report/research/20261024_environment_reconstruction').glob('observations*.json'))[-1]
    environments = json.loads(environment_path.read_text())
    env_source_checks = []
    for source in environments.get('sources', {}).values():
        path = Path(source['raw_path'])
        env_source_checks.append(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == source['sha256'])

    def load(url, issues):
        artifact = indexed.get(url)
        if artifact is None:
            issues.append({'code': 'MANIFEST_SOURCE_MISSING', 'url': url})
            return '', {}
        raw = Path(artifact['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != artifact['sha256']:
            issues.append({'code': 'SOURCE_SHA_MISMATCH', 'url': url})
            return '', artifact
        if artifact.get('bytes') is not None and artifact['bytes'] != len(raw):
            issues.append({'code': 'SOURCE_BYTE_LENGTH_MISMATCH', 'url': url})
        return raw.decode(artifact.get('encoding') or 'utf-8'), artifact

    summary = []
    race_directory = base / (args.input_dir or ('enriched_races' if args.enriched else 'races'))
    for race_path in sorted(race_directory.glob('*.json')):
        if not re.fullmatch(r'\d{8}_\d{2}_\d{2}\.json', race_path.name):
            continue
        if args.race_id and race_path.stem not in args.race_id:
            continue
        race = json.loads(race_path.read_text())
        issues = []
        card, card_meta = load(race['racecard_url'], issues)
        result_html, result_meta = load(race['result_url'], issues)
        soup = BeautifulSoup(card, 'html.parser')
        result_soup = BeautifulSoup(result_html, 'html.parser')
        # Limit fact extraction to the race header, away from result rows and navigation.
        time_node = result_soup.find(string=lambda text: text and '発走時刻' in text)
        raw_header = time_node.parent.parent if time_node else None
        header = raw_header.get_text(' ', strip=True) if raw_header else ''
        time_match = re.search(r'発走時刻[：:]?\s*(\d{1,2})時(\d{1,2})分', header)
        weather_match = re.search(r'天候\s*(小雨|小雪|晴|曇|雨|雪)', header)
        condition_match = re.search(r'(芝|ダート|障害)\s*(不良|稍重|良|重)', header)
        if not all((time_match, weather_match, condition_match)):
            issues.append({'code': 'RESULT_HEADER_FACTS_UNVERIFIED', 'header': header})
        horses = race.get('horses', [])
        race_id = horses[0]['race_id'] if horses else race['race_id']
        target_date = horses[0].get('target_race_date', '2026-10-24') if horses else '2026-10-24'
        post_time = f'{target_date}T{int(time_match[1]):02d}:{int(time_match[2]):02d}:00+09:00' if time_match else ''
        rows, entries, career, career_audit, artifacts = [], [], {}, [], []
        collection_attempts = []
        if card:
            artifacts.append(dict(kind='race_detail', html=card, sha256=hashlib.sha256(card.encode()).hexdigest(),
                                  race_id=race_id, path=card_meta.get('path', ''), original_source=card_meta))
        zero_history = []
        for horse in horses:
            number = str(horse['horse_number'])
            entry = {k: v for k, v in horse.items() if not isinstance(v, (dict, list))}
            entry.update(target_weather=weather_match[1] if weather_match else '',
                         target_track_condition=condition_match[2] if condition_match else '',
                         target_conditions_captured_at=result_meta.get('captured_at', ''))
            entries.append(entry)
            card_rows = [tr for tr in soup.select('tr') if any(horse['horse_id'] in a.get('href', '') for a in tr.select('a[href]'))]
            own_text = card_rows[0].get_text(' ', strip=True) if len(card_rows) == 1 else ''
            tuple_match = re.search(r'[（(]\s*(\d+)\s*[.．,，]\s*(\d+)\s*[.．,，]\s*(\d+)\s*[.．,，]\s*(\d+)\s*[）)]', own_text)
            if tuple_match:
                total = sum(map(int, tuple_match.groups()))
                career[number] = total
                career_audit.append(dict(horse_number=number, status='VERIFIED_CARD_TUPLE', count=total, evidence=tuple_match[0]))
            elif horse.get('career_verification', {}).get('status') == 'VERIFIED_COMPLETE_OFFICIAL_HISTORY':
                from repair_official_facts import complete_career
                detail_url_for_career = horse.get('horse_detail_url') or horse.get('horse_url')
                career_html, career_source = load(detail_url_for_career, issues)
                verified_career = complete_career(career_html, target_date) if career_html else {'status': 'UNVERIFIED'}
                if verified_career['status'] == 'VERIFIED_COMPLETE_OFFICIAL_HISTORY':
                    career[number] = verified_career['career_before_target']
                    career_audit.append(dict(horse_number=number, status='VERIFIED_COMPLETE_OFFICIAL_HISTORY',
                                             count=career[number], source=career_source, proof=verified_career))
                else:
                    career_audit.append(dict(horse_number=number, status='UNVERIFIED', count=None,
                                             reason='Rechecking full history versus official totals did not verify career'))
            elif '新馬' in str(horse.get('race_name', '')):
                career[number] = 0
                career_audit.append(dict(horse_number=number, status='VERIFIED_DEBUT', count=0, evidence=horse['race_name']))
            else:
                career_audit.append(dict(horse_number=number, status='UNVERIFIED', count=None,
                                         reason='No unambiguous pre-target career tuple in own card row'))
            histories = horse.get('history_pre_target', [])
            if not histories:
                zero_history.append(number)
            ordered = sorted(histories, key=lambda r: date_key(r.get('date')), reverse=True)
            recovered_artifact_urls = set()
            for index, history in enumerate(ordered, 1):
                historical_date = date_key(history.get('date'))
                if not historical_date or historical_date >= target_date:
                    issues.append(dict(code='NON_PAST_OR_UNKNOWN_HISTORY', horse_number=number, date=history.get('date')))
                row = _normalize_row({**entry, **{k:v for k,v in history.items() if k not in entry or k in {'date','course','race_name','distance','history_surface','position','time','weight','jockey','pace','last_3f','track_condition','weather','passing_order','odds','popularity'}}, 'run_index': str(index)})
                row['date'] = historical_date
                row['last_3f_source'] = history.get('last_3f_source', '')
                for metadata_field in ('pace_missing_reason', 'pace_source', 'pace_scope', 'interval_seconds',
                                       'interval_lengths_m', 'pace_metadata', 'field_provenance', 'field_collection_attempts'):
                    if metadata_field in history:
                        row[metadata_field] = history[metadata_field]
                rows.append(row)
                provenance_items = list(history.get('field_provenance', {}).items())
                provenance_items += [(field, attempt) for attempt in history.get('field_collection_attempts', [])
                                     for field in attempt.get('fields', [])]
                for field, provenance in provenance_items:
                    source_url = provenance.get('url')
                    source_kind = provenance.get('source_kind')
                    if source_kind not in {'official_past_result_observation', 'official_historical_final_odds'} or not source_url:
                        issues.append(dict(code='UNKNOWN_RECOVERY_PROVENANCE', horse_number=number, date=historical_date, field=field))
                        continue
                    recovered_html, recovered_meta = load(source_url, issues)
                    if not recovered_html:
                        continue
                    if recovered_meta.get('sha256') != provenance.get('sha256'):
                        issues.append(dict(code='RECOVERY_PROVENANCE_SHA_MISMATCH', horse_number=number, date=historical_date, field=field))
                        continue
                    match = provenance.get('match') or {}
                    same_name = re.sub(r'\s+', '', str(match.get('horse_name', ''))) == re.sub(r'\s+', '', horse['horse_name'])
                    href = str(match.get('horse_href', ''))
                    registration_match = re.search(r'dud\d{2}(\d{10})', href)
                    target_registration = re.search(r'dud\d{2}(\d{10})', horse['horse_id'])
                    same_registration = (registration_match is not None and target_registration is not None
                                         and registration_match[1] == target_registration[1])
                    artifact_kind = 'history_result'
                    if source_kind == 'official_historical_final_odds':
                        odds_soup = BeautifulSoup(recovered_html, 'html.parser')
                        page_dates = odds_soup.select('.cell.date')
                        odds_horses = [tr for tr in odds_soup.select('tr') if tr.select_one('td.horse') is not None
                                       and tr.select_one('td.horse').get_text(strip=True) == horse['horse_name']]
                        raw_number = odds_horses[0].select_one('td.num') if len(odds_horses) == 1 else None
                        actual_number = raw_number.get_text(strip=True) if raw_number else ''
                        same_registration = (len(page_dates) == 1 and day_matches(page_dates[0].get_text(' ', strip=True), historical_date)
                                             and len(odds_horses) == 1 and actual_number == str(match.get('horse_number'))
                                             and date_key(match.get('date')) == historical_date)
                        artifact_kind = 'history_odds'
                    if match.get('match_count') != 1 or not same_name or not same_registration:
                        issues.append(dict(code='RECOVERY_HORSE_MATCH_UNVERIFIED', horse_number=number, date=historical_date, field=field, match=match))
                        continue
                    html_sha256 = hashlib.sha256(recovered_html.encode()).hexdigest()
                    collection_attempts.append(dict(race_id=race_id, horse_id=horse['horse_id'],
                        horse_number=number, date=historical_date, fields=[field], artifact_sha256=html_sha256,
                        declared_state=provenance.get('state'), reason=provenance.get('reason'),
                        source_url=source_url, original_bytes_sha256=recovered_meta.get('sha256')))
                    if source_url not in recovered_artifact_urls:
                        artifacts.append(dict(kind=artifact_kind, html=recovered_html,
                            sha256=hashlib.sha256(recovered_html.encode()).hexdigest(), race_id=race_id,
                            horse_id=horse['horse_id'], horse_name=horse['horse_name'], path=recovered_meta.get('path', ''), original_source=recovered_meta))
                        recovered_artifact_urls.add(source_url)
            detail_url = horse.get('horse_detail_url') or horse.get('horse_url')
            # Full embedded histories may legitimately avoid a horse-detail request.
            # A recorded explicit detail request must still have its manifest proof.
            detail, detail_meta = ('', {})
            if detail_url in indexed or horse.get('horse_detail_url'):
                detail, detail_meta = load(detail_url, issues)
            if detail:
                artifacts.append(dict(kind='horse_history', html=detail, sha256=hashlib.sha256(detail.encode()).hexdigest(),
                    race_id=race_id, horse_id=horse['horse_id'], horse_name=horse['horse_name'], horse_url=detail_url,
                    path=detail_meta.get('path', ''), original_source=detail_meta))
        config = dict(race_id=race_id, career_starts_by_horse_number=career)
        collected = dict(rows=rows, entries=entries, quality_report={'issues': race.get('parser_issues', [])},
                         source_audit={'artifacts': artifacts, 'attempts': collection_attempts})
        repair_actions = []
        pre_source_check = verify_source_artifacts(collected)
        mismatched_horse_dates = {(str(item['row'].get('horse_id', '')), date_key(item['row'].get('date')))
                                  for item in pre_source_check.get('mismatches', [])}
        for horse_check in career_audit:
            if horse_check['status'] != 'UNVERIFIED':
                continue
            number = horse_check['horse_number']
            own_rows = [row for row in rows if str(row.get('horse_number')) == number]
            dates = {row.get('date') for row in own_rows}
            if len(own_rows) != 5 or len(dates) != 5 or not all(day and day < target_date for day in dates):
                continue
            horse_id = own_rows[0]['horse_id']
            if pre_source_check['status'] not in {'PASS', 'MISMATCH'}:
                continue
            if any((horse_id, day) in mismatched_horse_dates for day in dates):
                continue
            horse_check.update(status='VERIFIED_HISTORY_LOWER_BOUND', lower_bound=5,
                               required_history_count=5,
                               reason='Five distinct pre-target dates reproduced from hash-verified official source artifacts; total career remains unknown.')
            repair_actions.append(dict(race_id=race_id, horse_id=own_rows[0]['horse_id'],
                                       horse_number=number, required_history_count=5,
                                       evidence='VERIFIED_HISTORY_LOWER_BOUND',
                                       source_check='PASS', verified_dates=sorted(dates),
                                       career_total=None))
        collected['quality_report']['missing_data_repair_actions'] = repair_actions
        audit = audit_collected_data(collected, [config], purpose='environment_training')
        track = horses[0].get('target_track', '') if horses else ''
        observations = environments.get('tracks', {}).get(track, {})
        selected = {}
        for name in ('cushion', 'moisture'):
            candidates = [o for o in observations.get(name + '_history', []) if str(o.get('measured_at', '')).startswith(target_date)]
            selected[name] = max(candidates, key=lambda o:o['measured_at']) if candidates else None
        measurement_ok = bool(post_time) and all(selected.values()) and all(env_source_checks) and bool(env_source_checks)
        if measurement_ok:
            measurement_ok = all(datetime.fromisoformat(o['measured_at']) < datetime.fromisoformat(post_time) for o in selected.values())
        surface = horses[0].get('target_surface', '') if horses else ''
        environment_gate = 'PASS' if measurement_ok else 'UNVERIFIED_ENVIRONMENT'
        surface_gate = 'PASS' if surface == '芝' else 'NOT_SUPPORTED_SURFACE'
        conflicts = race.get('history_conflicts', [])
        verified_nonstarter_names = set()
        if race.get('non_starters'):
            from repair_official_facts import matched_result_row
            for nonstarter in race['non_starters']:
                original_result = matched_result_row(result_html, nonstarter)
                status_cell = original_result.select_one('td.place') if original_result is not None else None
                number_cell = original_result.select_one('td.num') if original_result is not None else None
                official_status = status_cell.get_text(strip=True) if status_cell else ''
                official_number = number_cell.get_text(strip=True) if number_cell else ''
                if (official_status in {'取消', '除外'} and official_status == nonstarter.get('entry_status')
                        and official_number == str(nonstarter.get('horse_number'))
                        and nonstarter.get('participation_provenance', {}).get('sha256') == result_meta.get('sha256')):
                    verified_nonstarter_names.add(nonstarter['horse_name'])
                else:
                    issues.append(dict(code='NONSTARTER_STATUS_UNVERIFIED', horse_name=nonstarter.get('horse_name')))
        conflict_audit = []
        blocking_conflicts = []
        model_fields = {'date', 'course', 'distance', 'history_surface', 'position', 'time', 'weight',
                        'jockey', 'pace', 'last_3f', 'last_3f_source', 'track_condition',
                        'passing_order', 'odds', 'popularity'}
        rejected_detail_fallbacks = {(c.get('horse'), date_key(c.get('date'))) for c in conflicts
            if c.get('field') == 'last_3f_source' and c.get('detail') == 'fallback'}
        for horse in horses:
            for history in horse.get('history_pre_target', []):
                provenance = history.get('field_provenance', {}).get('last_3f', {})
                if (provenance.get('previous_source') == 'fallback'
                        and provenance.get('source_kind') == 'official_past_result_observation'):
                    rejected_detail_fallbacks.add((horse['horse_name'], date_key(history.get('date'))))
        for conflict in conflicts:
            field = conflict.get('field', '')
            left, right = conflict.get('embedded_or_first'), conflict.get('detail')
            equivalent = False
            if field in model_fields:
                normalized_left = _normalize_row({field: left or ''}).get(field, '')
                normalized_right = _normalize_row({field: right or ''}).get(field, '')
                if field == 'date':
                    normalized_left, normalized_right = date_key(left), date_key(right)
                if field == 'last_3f_source':
                    normalized_left, normalized_right = str(left or ''), str(right or '')
                equivalent = normalized_left == normalized_right
            fallback_candidate = (conflict.get('horse'), date_key(conflict.get('date'))) in rejected_detail_fallbacks
            selected_histories = [history for horse in horses if horse.get('horse_name') == conflict.get('horse')
                                  for history in horse.get('history_pre_target', [])
                                  if date_key(history.get('date')) == date_key(conflict.get('date'))]
            selected_is_observed = bool(selected_histories) and all(
                history.get('last_3f_source') in {'observed', 'embedded', 'official_past_result_observation'}
                or history.get('field_provenance', {}).get('last_3f', {}).get('source_kind') == 'official_past_result_observation'
                for history in selected_histories)
            jockey_proof_verified = False
            if field == 'jockey' and conflict.get('fact_resolution', {}).get('status') == 'VERIFIED_SAME_OFFICIAL_JOCKEY_ID':
                from repair_official_facts import jockey_identity_proof
                own_horse = next((h for h in horses if h.get('horse_name') == conflict.get('horse')), None)
                proof = conflict['fact_resolution']
                if own_horse is not None:
                    proof_detail, proof_detail_meta = load(proof['detail_source']['url'], issues)
                    proof_result, proof_result_meta = load(proof['result_source']['url'], issues)
                    rechecked = jockey_identity_proof(proof_detail, proof_result, own_horse, date_key(conflict.get('date')))
                    jockey_proof_verified = (proof_detail_meta.get('sha256') == proof['detail_source'].get('sha256')
                                             and proof_result_meta.get('sha256') == proof['result_source'].get('sha256')
                                             and rechecked['status'] == 'VERIFIED_SAME_OFFICIAL_JOCKEY_ID'
                                             and rechecked['jockey_id'] == proof.get('jockey_id')
                                             and bool(selected_histories)
                                             and all(h.get('jockey') == rechecked['canonical_name'] for h in selected_histories))
            if conflict.get('horse') in verified_nonstarter_names:
                blocks = False
                classification = 'OFFICIAL_NONSTARTER_METADATA_ONLY'
            elif jockey_proof_verified:
                blocks = False
                classification = 'VERIFIED_SAME_OFFICIAL_JOCKEY_ID'
            elif field in {'last_3f', 'last_3f_source'} and fallback_candidate and selected_is_observed:
                blocks = False
                classification = 'REJECTED_FALLBACK_CANDIDATE'
            elif field == 'race_name':
                # Names feed jump-surface inference; preserve any jump classification disagreement.
                jump_pattern = r'障害|ジャンプ|J(?:S)?$'
                blocks = bool(re.search(jump_pattern, str(left))) != bool(re.search(jump_pattern, str(right)))
                classification = 'JUMP_CLASSIFICATION_CONFLICT' if blocks else 'NAME_LABEL_VARIANT'
            elif field in model_fields:
                blocks = not equivalent
                classification = 'NORMALIZED_EQUIVALENT' if equivalent else 'MODEL_INPUT_CONFLICT'
            else:
                blocks = False
                classification = 'NON_MODEL_METADATA_DIFFERENCE'
            checked = dict(conflict, classification=classification, blocks_model=blocks)
            conflict_audit.append(checked)
            if blocks:
                blocking_conflicts.append(checked)
        v4_conflicts = race.get('official_observation_conflicts_v4', [])
        unresolved_v4 = [c for c in v4_conflicts if c.get('fact_classification') == 'UNRESOLVED_OFFICIAL_OBSERVATION_CONFLICT']
        gate = audit['gate']
        if zero_history:
            gate = 'BLOCKED_ZERO_HISTORY_NEUTRAL_FORBIDDEN'
        elif issues or blocking_conflicts or unresolved_v4:
            gate = 'UNVERIFIED'
        elif gate == 'PASS' and surface_gate != 'PASS':
            gate = surface_gate
        elif gate == 'PASS' and environment_gate != 'PASS':
            gate = environment_gate
        model_ready = gate == 'PASS' and environment_gate == 'PASS' and surface_gate == 'PASS'
        cancelled_markers = [{'horse_number': h.get('horse_number'), 'horse_name': h.get('horse_name'),
                              'status': h.get('status') or h.get('entry_status')}
                             for h in horses if h.get('status') or h.get('entry_status')]
        payload = dict(race_id=race_id, numeric_race_id=race['race_id'], gate=gate, model_ready=model_ready,
            record_type='retrospective_reconstructed', production_learning_eligible=False,
            lineup_audit={'card_entries': len(horses) + len(race.get('non_starters', [])),
                          'active_entries': len(horses), 'official_nonstarters': race.get('non_starters', []),
                          'explicit_status_markers': cancelled_markers,
                          'withdrawal_completeness': 'UNVERIFIED',
                          'note': 'Collector entries retained; cancellation/exclusion coverage requires independent lineup comparison.'},
            metadata_origin='posthoc_official_result_header', post_time=post_time,
            metadata_source=result_meta, source_issues=issues, history_conflicts=conflicts,
            history_conflict_classification=conflict_audit, blocking_history_conflicts=blocking_conflicts,
            official_observation_conflicts_v4=v4_conflicts, unresolved_official_observation_conflicts_v4=unresolved_v4,
            zero_history_horse_numbers=zero_history, career_audit=career_audit, required_history_evidence=repair_actions,
            surface_gate=surface_gate, environment_gate=environment_gate, environment=selected,
            environment_source_path=str(environment_path), collected_audit=audit,
            verified_field_collection_attempts=collection_attempts,
            source_provenance=[a['original_source'] for a in artifacts])
        dump(output / (race_path.stem + '_audit.json'), payload)
        # Input artifact retains all horses, including unsupported surfaces and zero histories.
        dump(output / (race_path.stem + '_normalized.json'), dict(rows=rows, entries=entries, race_configs=[config],
            quality_report=collected['quality_report']))
        summary.append(dict(race_id=race_id, numeric_race_id=race['race_id'], gate=gate,
            model_ready=model_ready, horses=len(horses), histories=len(rows), findings=len(audit['findings']),
            source_mismatches=len(audit['verification_issues']), surface_gate=surface_gate, environment_gate=environment_gate))
    dump(output / 'summary.json', dict(audited_at=stamp, source_manifest_sha256=manifest_sha, source_manifest_snapshots=manifest_snapshots,
        input_directory=str(race_directory), enriched_mode=args.enriched,
        race_count=len(summary), expected_race_count=36, batch_complete=len(summary)==36,
        model_ready_race_ids=[r['race_id'] for r in summary if r['model_ready']], races=summary,
        approvals_used=[], model_calculations_executed=False))
    print(json.dumps(dict(output_directory=str(output), races=len(summary), model_ready=sum(r['model_ready'] for r in summary))))


if __name__ == '__main__':
    main()
