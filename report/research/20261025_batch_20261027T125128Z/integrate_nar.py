"""Join independently preserved NAR observations to exact pre-target horse/date keys."""
import datetime
import hashlib
import json
from pathlib import Path
from bs4 import BeautifulSoup

OUT = Path(__file__).parent
NAR = Path('report/research/20261025_nar_recovery_20261027T130621Z/observations.json')
DEST = OUT / 'features_final'
DEST.mkdir(exist_ok=True)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

payload = json.loads(NAR.read_text())
observed = {}
for item in payload['observations']:
    key = (item['race_id'], item['horse_id'], item['date'])
    if key in observed:
        raise ValueError('duplicate NAR observation identity')
    if item['date'] >= '2026-10-25':
        raise ValueError('NAR observation not before target date')
    source = item.get('source')
    if source:
        if digest(Path(source['path'])) != source['sha256']:
            raise ValueError('NAR source SHA mismatch')
    observed[key] = item
summary = {'target_date':'2026-10-25','role':'data_collector',
           'collection_status':'COMPLETED_WITH_EXPLICIT_MISSING_OBSERVATIONS',
           'training_readiness':'PENDING_INDEPENDENT_INTEGRITY_AUDIT','analysis_or_training_executed':False,
           'mode':'official_fact_connection_only_no_imputation_no_fit',
           'generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'code_sha256':digest(Path(__file__)),
           'nar_artifact':{'path':str(NAR.resolve()),'sha256':digest(NAR)},
           'input_races':[],'races':0,'starters':0,'non_starters':0,'history_rows':0,
           'nar_targets':len(observed),'nar_connected':0,'nar_pending':[],
           'missing_fields':{},'missing_field_reasons':{},'recoveries':{},'conflicting_observations':[],
           'unobserved_last3f_fallbacks':0,'source_artifacts':[]}
allowed = ('weather','track_condition','passing_order','last_3f','odds','position',
           'time','weight','jockey','popularity','history_surface')
condition_cache = {}
ftime_headers = {}
course_texts = {}
def track_conditions(source):
    key = source['sha256']
    if key not in condition_cache:
        raw = Path(source['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != key:
            raise ValueError('JRA condition source SHA mismatch')
        soup = BeautifulSoup(raw.decode(source['encoding'],errors='replace'),'html.parser')
        values = [{'surface': '芝' if 'turf' in node.get('class',[]) else 'ダート',
                   'value':node.select_one('.txt').get_text(strip=True)}
                  for node in soup.select('.cell.baba li.turf, .cell.baba li.dirt, .cell.baba li.durt') if node.select_one('.txt')]
        condition_cache[key] = values
        course_node = soup.select_one('.cell.course')
        course_texts[key] = course_node.get_text(' ',strip=True) if course_node else ''
        ftime_header = soup.select_one('th.f_time')
        ftime_headers[key] = ftime_header.get_text(' ',strip=True) if ftime_header else ''
    return condition_cache[key]

summary['jra_condition_repairs'] = []
summary['jra_average_1f_semantic_repairs'] = []
used = set()
for path in sorted((OUT / 'features').glob('*.json')):
    race = json.loads(path.read_text())
    summary['input_races'].append({'path':str(path.resolve()),'sha256':digest(path)})
    summary['races'] += 1
    summary['starters'] += len(race['horses'])
    summary['non_starters'] += len(race.get('non_starters',[]))
    for horse in race['horses'] + race.get('non_starters',[]):
        for row in horse['history_pre_target']:
            summary['history_rows'] += 1
            date = row['normalized_date']
            jra_source = row.get('official_result_source')
            if jra_source:
                conditions = track_conditions(jra_source)
                if not row.get('passing_order'):
                    course_text = course_texts[jra_source['sha256']]
                    row['passing_order_missing_reason'] = ('NOT_APPLICABLE_STRAIGHT_COURSE_NO_CORNER_OBSERVATION'
                        if '・直' in course_text or '直線' in course_text else 'OFFICIAL_RESULT_CORNER_CELL_EMPTY')
                    row['passing_order_collection_evidence'] = {'official_course_text':course_text,'source':jra_source}
                if '平均1F' in ftime_headers[jra_source['sha256']]:
                    average = row.get('official_result_observation',{}).get('last_3f','')
                    row['official_average_1f'] = average
                    row['official_finish_time_column_header'] = ftime_headers[jra_source['sha256']]
                    row.setdefault('discarded_semantic_parser_values',{})['last_3f'] = row.get('last_3f')
                    row['last_3f'] = ''
                    row['last_3f_source'] = 'NOT_OBSERVED_RESULT_COLUMN_IS_AVERAGE_1F'
                    row.setdefault('field_provenance',{})['average_1f'] = jra_source
                    row['official_result_observation']['average_1f'] = average
                    row['official_result_observation']['last_3f'] = ''
                    summary['jra_average_1f_semantic_repairs'].append({'race_id':race['race_id'],
                        'horse_name':horse['horse_name'],'date':date,'average_1f':average,'source':jra_source})
                if conditions:
                    canonical = '/'.join(x['value'] for x in conditions)
                    row['official_track_conditions_observation'] = {'values':conditions,'source':jra_source}
                    if row.get('track_condition') != canonical:
                        summary['jra_condition_repairs'].append({'race_id':race['race_id'],
                            'horse_name':horse['horse_name'],'date':date,'previous':row.get('track_condition'),
                            'observed':canonical,'source':jra_source})
                        row.setdefault('previous_jra_parser_values',{})['track_condition'] = row.get('track_condition')
                        row['track_condition'] = canonical
                        row.setdefault('field_provenance',{})['track_condition'] = jra_source
            key = (race['race_id'], horse['horse_id'], date)
            item = observed.get(key)
            if item:
                if item['horse_name'] != horse['horse_name']:
                    raise ValueError('NAR horse name mismatch')
                source = item.get('source')
                fields = item.get('fields',{})
                evidence = item.get('match_evidence',{})
                verified = (item.get('status') == 'MATCHED_OFFICIAL_RESULT'
                            and evidence.get('horse_name_exact') is True
                            and evidence.get('date_and_course_in_official_result_heading') is True
                            and evidence.get('jra_position_matches') is True)
                if source and fields and verified:
                    used.add(key)
                    row['nar_official_observation'] = item
                    for field in allowed:
                        value = fields.get(field)
                        if value is None or str(value).strip() in ('','-','―'):
                            continue
                        previous = row.get(field)
                        if previous and str(previous).strip() != str(value).strip():
                            summary['conflicting_observations'].append({
                                'race_id':race['race_id'],'horse_name':horse['horse_name'],
                                'date':date,'field':field,'jra_value':previous,'nar_value':value,
                                'resolution':'Direct official NAR result observation retained; prior value preserved',
                                'source':source})
                            row.setdefault('previous_jra_values_before_nar',{})[field] = previous
                        if not previous:
                            summary['recoveries'][field] = summary['recoveries'].get(field,0)+1
                        row[field] = value
                        row.setdefault('field_provenance',{})[field] = source
                        if field == 'last_3f':
                            row['last_3f_source'] = 'nar_official_result_observation'
                    if not row.get('pace'):
                        row['pace_missing_reason'] = item.get('pace_600m_reason','NAR_600M_BOUNDARY_OBSERVATION_UNAVAILABLE')
                        row['pace_collection_source'] = source
                    row['jra_collection_gap_history'] = row.pop('collection_gap',None)
                    row['nar_source_missing_fields'] = item.get('missing_fields',[])
                    row['nar_source_missing_reasons'] = item.get('missing_reasons',{})
                    summary['nar_connected'] += 1
            for field in ('weather','track_condition','passing_order','last_3f','pace','odds'):
                if not row.get(field):
                    summary['missing_fields'][field] = summary['missing_fields'].get(field,0)+1
                    reasons = {'pace':row.get('pace_missing_reason'),'passing_order':row.get('passing_order_missing_reason'),
                               'last_3f':row.get('last_3f_source')}
                    reason = reasons.get(field) or row.get('nar_source_missing_reasons',{}).get(field) or 'OFFICIAL_SOURCE_FIELD_EMPTY'
                    bucket = summary['missing_field_reasons'].setdefault(field,{})
                    bucket[reason] = bucket.get(reason,0)+1
            summary['unobserved_last3f_fallbacks'] += row.get('last_3f_source') == 'fallback'
        horse['history_missing_fields'] = [
            {'index':i,'fields':[k for k in ('weather','track_condition','passing_order','last_3f','pace','odds') if not r.get(k)],
             'last_3f_source':r.get('last_3f_source'),'pace_missing_reason':r.get('pace_missing_reason',''),
             'passing_order_missing_reason':r.get('passing_order_missing_reason','')}
            for i,r in enumerate(horse['history_pre_target'])]
    race['nar_integration'] = {'observation_artifact':str(NAR.resolve()),'sha256':digest(NAR),
                               'original_jra_features':str(path.resolve()),'original_sha256':digest(path)}
    write(DEST / path.name,race)
odds_catalog_path = Path('report/research/20261025_final_odds_20261027T125420Z/collection.json')
odds_catalog = json.loads(odds_catalog_path.read_text())
odds_index = {r['race_id']:r for r in odds_catalog['races']}
summary['target_final_odds_catalog'] = {'path':str(odds_catalog_path.resolve()),'sha256':digest(odds_catalog_path),'feature_input_allowed':False}
labels_dest = OUT / 'labels_final'
labels_dest.mkdir(exist_ok=True)
for path in sorted((OUT / 'labels').glob('*.json')):
    label = json.loads(path.read_text())
    conditions = track_conditions(label['source'])
    if '平均1F' in ftime_headers[label['source']['sha256']]:
        label['observations']['finish_time_column_header'] = ftime_headers[label['source']['sha256']]
        for observation in label['observations']['rows']:
            observation['average_1f'] = observation.pop('last_3f','')
            observation['last_3f'] = ''
    if conditions:
        label['observations']['track_condition'] = '/'.join(x['value'] for x in conditions)
        label['observations']['track_conditions'] = conditions
    feature_source = json.loads((OUT / 'features' / path.name).read_text())
    declared = feature_source['horses'] + feature_source.get('non_starters',[])
    canonical = '20261025_' + declared[0]['target_track'] + '_' + label['race_id'].split('_')[-1]
    odds_reference = odds_index[canonical]
    if digest(Path(odds_reference['artifact_path'])) != odds_reference['sha256']:
        raise ValueError('target final odds artifact SHA mismatch')
    label['target_final_odds_artifact'] = odds_reference
    label['target_final_odds_feature_input_allowed'] = False
    label['original_label_artifact'] = {'path':str(path.resolve()),'sha256':digest(path)}
    write(labels_dest / path.name,label)
summary['nar_pending'] = [{'race_id':k[0],'horse_id':k[1],'date':k[2]} for k in observed if k not in used]
summary['source_artifacts'] = [{'path':str(p.resolve()),'sha256':digest(p)} for p in sorted(DEST.glob('*.json'))]
write(OUT / 'final_collection_summary.json',summary)
print(json.dumps({k:summary[k] for k in ('races','starters','non_starters','history_rows','nar_connected','nar_pending','missing_fields','unobserved_last3f_fallbacks')},ensure_ascii=False))
