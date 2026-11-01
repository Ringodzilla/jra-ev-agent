"""Bind exact source metadata in the ResearchSequence list-shaped contract."""
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).parent

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

plan = json.loads((OUT.parent/'research_plan.json').read_text())
source_root = Path(plan['source_catalog']['path']).parent
labels = json.loads((OUT/'labels.json').read_text())
feature = json.loads(Path(labels['races'][0]['feature_artifact']['path']).read_text())
nar_root = Path(feature['nar_integration']['observation_artifact']).parent
manifests = [source_root/'manifest.json',source_root/'history_manifest.json',nar_root/'manifest.json']
records = []
for manifest_path in manifests:
    source_manifest = {'path':str(manifest_path.resolve()),'sha256':sha(manifest_path)}
    for item in json.loads(manifest_path.read_text()):
        if sha(item['path'])!=item['sha256']:
            raise ValueError('Original bytes mismatch')
        records.append({**item,'original_manifest':source_manifest})
environment = json.loads((OUT/'environment.json').read_text())
for name,item in environment['sources'].items():
    if sha(item['raw_path'])!=item['sha256']:
        raise ValueError('Environment original bytes mismatch')
    records.append({'url':item['url'],'path':item['raw_path'],'sha256':item['sha256'],
                    'encoding':item['response_encoding'],'method':item['method'],
                    'captured_at':item['received_at'],'source_kind':'official_environment_original_bytes',
                    'original_environment_source_key':name})
previous = OUT/'source_manifests.json'
preserved = OUT/'adapter_source_provenance.json'
if preserved.exists():
    raise ValueError('Previous adapter provenance already preserved')
previous.rename(preserved)
with previous.open('x') as stream:
    json.dump(records,stream,ensure_ascii=False,indent=2)
    stream.write('\n')
print(json.dumps({'source_manifest_records':len(records),'source_manifests_sha256':sha(previous),
                  'preserved_adapter_provenance_sha256':sha(preserved)},ensure_ascii=False))
