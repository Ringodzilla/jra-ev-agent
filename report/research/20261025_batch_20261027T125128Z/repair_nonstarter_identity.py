"""Create a new factual snapshot after independent identity review."""
import datetime
import hashlib
import json
import re
from pathlib import Path

OUT = Path(__file__).parent

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

feature_dest = OUT / 'features_final_v2'
label_dest = OUT / 'labels_final_v2'
feature_dest.mkdir(exist_ok=False)
label_dest.mkdir(exist_ok=False)
changes = []
inputs = []
for feature_path in sorted((OUT / 'features_final').glob('*.json')):
    label_path = OUT / 'labels_final' / feature_path.name
    feature = json.loads(feature_path.read_text())
    label = json.loads(label_path.read_text())
    inputs += [{'path':str(path.resolve()),'sha256':digest(path)} for path in (feature_path,label_path)]
    source = label['source']
    if digest(Path(source['path'])) != source['sha256']:
        raise ValueError('official result source SHA mismatch')
    for horse in feature.get('non_starters',[]):
        if re.fullmatch(r'\d+',horse['horse_number']):
            continue
        registration = re.search(r'dud\d{2}(\d{10})',horse['horse_id'])
        matches = [r for r in label['observations']['rows']
                   if r['horse_name']==horse['horse_name']
                   and registration and registration[1] in r['horse_href']]
        if len(matches)!=1 or matches[0]['position'] not in ('取消','除外'):
            raise ValueError('unique official nonstarter identity not proven')
        hit = matches[0]
        if not re.fullmatch(r'\d+',hit['horse_number']):
            raise ValueError('official result number invalid')
        previous = horse['horse_number']
        horse['horse_number'] = hit['horse_number']
        evidence = {'source':source,'horse_name':hit['horse_name'],'registration_id':registration[1],
                    'horse_href':hit['horse_href'],'official_horse_number':hit['horse_number'],
                    'official_status':hit['position'],'previous_parser_value':previous,
                    'match_rule':'exact name and registration ID and unique official result row'}
        horse['horse_number_correction'] = evidence
        for declared in feature['declared_entry_set']:
            if declared['horse_id']==horse['horse_id']:
                declared['horse_number'] = hit['horse_number']
        for market in label.get('entry_market',[]):
            if market['horse_id']==horse['horse_id']:
                market['horse_number'] = hit['horse_number']
                market['horse_number_correction'] = evidence
        changes.append({'race_id':feature['race_id'],'horse_id':horse['horse_id'],
                        'horse_name':horse['horse_name'],'field':'horse_number',
                        'before':previous,'after':hit['horse_number'],'evidence':evidence})
    write(feature_dest / feature_path.name,feature)
    write(label_dest / label_path.name,label)
summary = json.loads((OUT / 'final_collection_summary.json').read_text())
summary['previous_summary'] = {'path':str((OUT/'final_collection_summary.json').resolve()),'sha256':digest(OUT/'final_collection_summary.json')}
summary['snapshot_revision'] = 2
summary['generated_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
summary['correction_code'] = {'path':str(Path(__file__).resolve()),'sha256':digest(Path(__file__))}
summary['independent_review_requested_corrections'] = changes
summary['source_artifacts'] = [{'path':str(path.resolve()),'sha256':digest(path)} for path in sorted(feature_dest.glob('*.json'))]
summary['label_artifacts'] = [{'path':str(path.resolve()),'sha256':digest(path)} for path in sorted(label_dest.glob('*.json'))]
summary['unchanged_input_snapshots'] = inputs
if any(digest(Path(item['path']))!=item['sha256'] for item in inputs):
    raise ValueError('frozen input snapshot changed')
write(OUT/'final_collection_summary_v2.json',summary)
print(json.dumps({'revision':2,'races':len(summary['source_artifacts']),'corrections':changes},ensure_ascii=False))
