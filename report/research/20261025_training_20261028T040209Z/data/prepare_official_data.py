"""Create immutable factual label/environment adapters after a frozen study plan."""
import argparse
import datetime
import hashlib
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ref(path):
    path = Path(path).resolve()
    return {'path':str(path),'sha256':sha(path)}


def write_new(path, value):
    if path.exists():
        raise ValueError('Refusing to overwrite existing study data: '+str(path))
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--plan-sha256',required=True)
    args.add_argument('--environment-collection',type=Path,required=True)
    options = args.parse_args()
    out = Path(__file__).resolve().parent
    plan_path = out.parent / 'research_plan.json'
    if sha(plan_path)!=options.plan_sha256:
        raise ValueError('Frozen study plan SHA mismatch')
    plan = json.loads(plan_path.read_text())
    if len(plan['training_dates'])!=1:
        raise ValueError('This factual adapter requires one whole training date')
    target_date = plan['training_dates'][0]
    catalog_path = Path(plan['source_catalog']['path'])
    if sha(catalog_path)!=plan['source_catalog']['sha256']:
        raise ValueError('Frozen collection catalog SHA mismatch')
    catalog = json.loads(catalog_path.read_text())
    for item in catalog['artifacts']:
        if sha(item['path'])!=item['sha256']:
            raise ValueError('Frozen source changed: '+item['path'])
    source_root = catalog_path.parent
    label_paths = sorted((source_root/'labels_final_v2').glob('*.json'))
    if len(label_paths)!=36:
        raise ValueError('Full declared collection of 36 races required')
    sources = []
    rows = []
    races = []
    input_refs = [ref(plan_path),ref(catalog_path),ref(options.environment_collection)]
    for label_path in label_paths:
        feature_path = source_root/'features_final_v2'/label_path.name
        label = json.loads(label_path.read_text())
        feature = json.loads(feature_path.read_text())
        result_source = label['source']
        if sha(result_source['path'])!=result_source['sha256']:
            raise ValueError('Official result raw SHA mismatch')
        if label['observations']['date']!=target_date:
            raise ValueError('Label outside study training date')
        soup = BeautifulSoup(Path(result_source['path']).read_bytes().decode(result_source['encoding']),'html.parser')
        winners = [tr.select_one('td.num').get_text(strip=True) for tr in soup.select('tr')
                   if tr.select_one('td.place') and tr.select_one('td.place').get_text(strip=True)=='1']
        observed_winners = [r['horse_number'] for r in label['observations']['rows'] if r['position']=='1']
        payouts = {}
        for dl in soup.select('dl'):
            dt = dl.find('dt')
            dd = dl.find('dd')
            if not dt or not dd or dt.get_text(strip=True)!='単勝':
                continue
            for line in dd.select('.line'):
                number = line.select_one('.num')
                yen = line.select_one('.yen')
                if number and yen:
                    horse_number = number.get_text(strip=True)
                    value = int(re.sub(r'\D','',yen.get_text()))
                    if horse_number in payouts:
                        raise ValueError('Duplicate official win payout row')
                    payouts[horse_number] = value
        if not winners or len(winners)!=len(set(winners)) or set(winners)!=set(payouts) or set(winners)!=set(observed_winners):
            raise ValueError('Official raw/structured winner/payout mismatch')
        entries = feature['horses']+feature.get('non_starters',[])
        declared = feature['declared_entry_set']
        if {str(e['horse_number']) for e in entries}!={r['horse_number'] for r in label['observations']['rows']}:
            raise ValueError('Whole declared/result horse universe mismatch')
        if {(e['horse_id'],e['horse_name'],e['horse_number']) for e in entries}!={(e['horse_id'],e['horse_name'],e['horse_number']) for e in declared}:
            raise ValueError('Declared identity set mismatch')
        target = entries[0]
        for key in ('target_track','target_surface','target_distance','target_race_date'):
            if any(e[key]!=target[key] for e in entries):
                raise ValueError('Race target metadata not constant')
        if target['target_race_date']!=target_date:
            raise ValueError('Declared race date outside study')
        rid = feature['race_id']
        for number,value in payouts.items():
            rows.append({'race_id':rid,'式別':'単勝','馬番':number,'払戻金':value})
        sources.append({'official_url':result_source['url'],**ref(label_path),
                        'race_id':rid,'official_raw':result_source})
        races.append({'race_id':rid,'date':target_date,'track':target['target_track'],
                      'surface':target['target_surface'],'distance_m':int(target['target_distance']),
                      'official_course_text':label['observations']['course_text'],
                      'weather':label['observations']['weather'],'track_condition':label['observations']['track_condition'],
                      'winners':sorted(winners),'win_payouts':payouts,'declared_entries':declared,
                      'starting_entries':[{'horse_id':e['horse_id'],'horse_name':e['horse_name'],'horse_number':e['horse_number']} for e in feature['horses']],
                      'official_non_starters':[{'horse_id':e['horse_id'],'horse_name':e['horse_name'],'horse_number':e['horse_number'],'status':e['entry_status']} for e in feature.get('non_starters',[])],
                      'label_artifact':ref(label_path),'feature_artifact':ref(feature_path),'official_result_source':result_source})
        input_refs.extend((ref(label_path),ref(feature_path),{'path':result_source['path'],'sha256':result_source['sha256']}))
    original_env = json.loads(options.environment_collection.read_text())
    if original_env['target_date']!=target_date or original_env['pre_race_capture_proven'] is not False:
        raise ValueError('Environment reconstruction date/status mismatch')
    env_sources = {}
    for name,source in original_env['sources'].items():
        if sha(source['raw_path'])!=source['raw_sha256'] or sha(source['decoded_path'])!=source['decoded_sha256']:
            raise ValueError('Environment raw/decoded original changed')
        env_sources[name] = {**source,'sha256':source['raw_sha256']}
        input_refs.extend(({'path':source['raw_path'],'sha256':source['raw_sha256']},
                           {'path':source['decoded_path'],'sha256':source['decoded_sha256']}))
    tracks = {track['track']:track for track in original_env['tracks']}
    if set(tracks)!={race['track'] for race in races}:
        raise ValueError('Environment/race venue set mismatch')
    for track in tracks.values():
        if track['date']!=target_date or track['pre_race_capture_proven'] is not False:
            raise ValueError('Track environment date/status mismatch')
        for kind in ('cushion','moisture'):
            observations = track[kind+'_same_day_observations']
            if not observations or any(obs not in track[kind+'_history'] for obs in observations):
                raise ValueError('Same-day environment outside original full history')
            for observation in observations:
                stamp = datetime.datetime.fromisoformat(observation['measured_at'])
                if stamp.tzinfo is None or stamp.date().isoformat()!=target_date:
                    raise ValueError('Measurement date/time invalid')
    common = {'role':'data_collector','target_date':target_date,'plan':ref(plan_path),
              'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'mode':'post_event_official_reconstruction','pre_race_capture':False,
              'imputations':[],'probability_calculation_executed':False,'fit_executed':False}
    labels = {**common,'rows':rows,'sources':sources,'races':races,
              'scope':'all 36 races and whole declared/starting/nonstarting horse universes; research selection occurs separately',
              'training_feature_input_allowed':False}
    environment = {**common,'sources':env_sources,'tracks':tracks,'original_collection':ref(options.environment_collection),
                   'original_collection_metadata':{k:v for k,v in original_env.items() if k not in ('sources','tracks')},
                   'eligible_for_production_learning':False}
    for item in input_refs:
        if sha(item['path'])!=item['sha256']:
            raise ValueError('Input changed during factual conversion')
    write_new(out/'labels.json',labels)
    write_new(out/'environment.json',environment)
    unique = {str(Path(item['path']).resolve()):item['sha256'] for item in input_refs}
    manifests = {**common,'code':ref(Path(__file__)),
                 'source_artifacts':[{'path':path,'sha256':digest} for path,digest in sorted(unique.items())],
                 'outputs':[ref(out/'labels.json'),ref(out/'environment.json')],
                 'race_count':len(races),'declared_horse_count':sum(len(r['declared_entries']) for r in races),
                 'starting_horse_count':sum(len(r['starting_entries']) for r in races),
                 'nonstarting_horse_count':sum(len(r['official_non_starters']) for r in races),
                 'winner_label_count':len(rows),'track_count':len(tracks)}
    write_new(out/'source_manifests.json',manifests)
    print(json.dumps({key:manifests[key] for key in ('race_count','declared_horse_count','starting_horse_count','nonstarting_horse_count','winner_label_count','track_count')},ensure_ascii=False))


if __name__=='__main__':
    main()
