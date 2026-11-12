"""Screening-pool data access for the Issue #40 hypothesis funnel.

Structural isolation of the confirmation pool (protocol section 3.2):

- The screening code never takes a confirmation-pool path.  Races enter
  only through an explicit race-ID manifest built from admission-summary
  metadata (race IDs, splits, segments, audit references and hashes).
- The expansion source contributes its ``train`` split only.  A manifest
  entry naming an expansion validation/test race is rejected, as is any
  path or symlink resolving outside the allowlisted audit roots.
- Every audit artifact hash is re-verified before use, and admission
  status and gates are re-checked per race, mirroring
  ``condition_fit.load_pool``.  Labels are attached only after the
  feature side of a race is complete.

The loader generalizes ``load_pool`` to both surfaces: turf uses the
cushion value and turf moisture; dirt uses the dirt moisture readings
and the cushion value stays ``None`` (NOT_APPLICABLE — never required,
never substituted, per the dirt base-form contract).
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import condition_fit as cf  # noqa: E402
from structural_features import structural_features, COMPONENT_WEIGHTS  # noqa: E402

SOURCES = {
    'migrated_20260502_20260927': {
        'summary_path': 'report/research/'
                        'dataset_20260502_20260927_training_20261108T154310Z'
                        '/audit/summary.json',
        'dataset_dir': 'research/datasets/jra-20260502-20260927',
        'allowed_splits': ('train', 'validation', 'test'),
    },
    'expansion_20251001_20260430': {
        'summary_path': 'report/research/'
                        'dataset_expansion_20251001_20260430'
                        '/audit_run_full/summary.json',
        'dataset_dir': 'research/datasets/jra-20251001-20260430',
        'allowed_splits': ('train',),
    },
}


class Blocked(cf.Blocked):
    """Manifest or input verification failure; never repaired."""


def _canonical_sha(payload) -> str:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def build_manifest(root=ROOT) -> dict:
    """Build the screening-pool manifest from summary metadata only.

    No audit artifact, normalized artifact, outcome or label is opened:
    entries carry race IDs, splits, segments and the audit references
    recorded in the summaries.  Expansion validation/test races are not
    listed at all (they are the unopened confirmation pool).
    """
    root = Path(root)
    sources, races = [], []
    for source_id, config in SOURCES.items():
        summary_path = root / config['summary_path']
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        sources.append({
            'source_id': source_id,
            'summary_path': config['summary_path'],
            'summary_sha256': cf.sha256(summary_path),
            'dataset_dir': config['dataset_dir'],
            'allowed_splits': list(config['allowed_splits']),
        })
        for item in summary['races']:
            if item['research_admission']['status'] != 'ADMITTED_RESEARCH':
                continue
            split = item['split']
            if split not in config['allowed_splits']:
                continue
            segment = item['segment']
            race_id = item['race_id']
            races.append({
                'race_id': race_id,
                'source': source_id,
                'split': split,
                'pool_block': 'train' if split == 'train' else 'reference',
                'surface': segment['surface'],
                'venue': segment['venue'],
                'distance': int(segment['distance']),
                'band': segment['band'],
                'date': (f"{race_id[0:4]}-{race_id[4:6]}-{race_id[6:8]}"),
                'audit_path': item['audit']['path'],
                'audit_sha256': item['audit']['sha256'],
            })
    races.sort(key=lambda r: r['race_id'])
    if len({r['race_id'] for r in races}) != len(races):
        raise Blocked('Duplicate race ID across screening sources')
    counts = {
        'total': len(races),
        'by_surface': {s: sum(1 for r in races if r['surface'] == s)
                       for s in ('芝', 'ダート')},
        'by_source': {sid: sum(1 for r in races if r['source'] == sid)
                      for sid in SOURCES},
        'by_pool_block': {b: sum(1 for r in races
                                 if r['pool_block'] == b)
                          for b in ('train', 'reference')},
    }
    manifest = {'schema': 'funnel_screening_pool_manifest_v1',
                'sources': sources, 'counts': counts, 'races': races}
    manifest['manifest_sha256'] = _canonical_sha(manifest)
    return manifest


def verify_manifest(manifest) -> str:
    payload = {k: v for k, v in manifest.items() if k != 'manifest_sha256'}
    digest = _canonical_sha(payload)
    if digest != manifest.get('manifest_sha256'):
        raise Blocked('Screening manifest SHA-256 mismatch')
    return digest


def _checked_path(path, allowed_roots):
    """Resolve a manifest path, rejecting symlinks and root escapes."""
    candidate = Path(path)
    if candidate.is_symlink():
        raise Blocked(f'Symlink input rejected: {path}')
    absolute = candidate if candidate.is_absolute() else ROOT / candidate
    resolved = absolute.resolve()
    if resolved != absolute.absolute():
        raise Blocked(f'Path resolves through a symlink: {path}')
    roots = [Path(r).resolve() for r in allowed_roots]
    if not any(resolved == r or r in resolved.parents for r in roots):
        raise Blocked(f'Path outside allowlisted roots: {path}')
    return resolved


def _environment(audit, target, race_id, surface):
    measured = audit['environment']
    post = datetime.fromisoformat(audit['post_time'])
    moisture = measured['moisture']
    stamp = datetime.fromisoformat(moisture['measured_at'])
    if stamp.date().isoformat() != target['target_race_date'] \
            or stamp >= post:
        raise Blocked('Environment measurement not same-day pre-race: '
                      + race_id)
    if surface == '芝':
        cushion = measured['cushion']
        c_stamp = datetime.fromisoformat(cushion['measured_at'])
        if c_stamp.date().isoformat() != target['target_race_date'] \
                or c_stamp >= post:
            raise Blocked('Cushion measurement not same-day pre-race: '
                          + race_id)
        return {'cushion': float(cushion['cushion']),
                'moisture_goal': float(moisture['turf_moisture_goal']),
                'moisture_corner4': float(
                    moisture['turf_moisture_corner4']),
                'weather': target['target_weather'],
                'track_condition': target['target_track_condition']}
    if measured.get('cushion') is not None \
            or measured.get('cushion_applicability') != 'NOT_APPLICABLE':
        raise Blocked('Dirt race without NOT_APPLICABLE cushion: '
                      + race_id)
    return {'cushion': None,
            'moisture_goal': float(moisture['dirt_moisture_goal']),
            'moisture_corner4': float(moisture['dirt_moisture_corner4']),
            'weather': target['target_weather'],
            'track_condition': target['target_track_condition']}


def load_screening_races(manifest, allowed_roots, root=ROOT) -> list:
    """Load exactly the manifest's races, with full hash verification.

    ``allowed_roots`` must name the two admission-run directories; audit
    and summary paths resolving anywhere else (or through a symlink) are
    rejected before any file is opened.
    """
    verify_manifest(manifest)
    root = Path(root)
    wanted = {}
    for entry in manifest['races']:
        race_id = entry['race_id']
        if race_id in wanted:
            raise Blocked(f'Duplicate race ID in manifest: {race_id}')
        config = SOURCES.get(entry['source'])
        if config is None:
            raise Blocked(f'Unknown screening source: {entry["source"]}')
        if entry['split'] not in config['allowed_splits']:
            raise Blocked(
                f"Split {entry['split']} of source {entry['source']} is "
                'not screening-legal (confirmation pool isolation): '
                + race_id)
        wanted[race_id] = entry
    races = []
    for source in manifest['sources']:
        source_id = source['source_id']
        config = SOURCES[source_id]
        summary_path = _checked_path(root / source['summary_path'],
                                     allowed_roots)
        if cf.sha256(summary_path) != source['summary_sha256']:
            raise Blocked(f'Summary changed for source: {source_id}')
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        outcomes = cf._load_outcomes(root / config['dataset_dir'])
        entries = {item['race_id']: item for item in summary['races']
                   if item['race_id'] in wanted
                   and wanted[item['race_id']]['source'] == source_id}
        for race_id, item in entries.items():
            entry = wanted[race_id]
            if item['research_admission']['status'] != 'ADMITTED_RESEARCH':
                raise Blocked('Manifest race not admitted: ' + race_id)
            if item['split'] != entry['split'] \
                    or item['segment']['surface'] != entry['surface']:
                raise Blocked('Manifest/summary mismatch: ' + race_id)
            audit_path = _checked_path(entry['audit_path'], allowed_roots)
            if cf.sha256(audit_path) != entry['audit_sha256']:
                raise Blocked('Audit artifact changed: ' + race_id)
            audit = json.loads(audit_path.read_text(encoding='utf-8'))
            if audit['environment_gate'] != 'PASS' \
                    or audit['surface_gate'] != 'PASS':
                raise Blocked('Admitted race without PASS gates: '
                              + race_id)
            if audit['split'] != item['split'] \
                    or audit['segment'] != item['segment']:
                raise Blocked('Summary/audit segment mismatch: '
                              + race_id)
            normalized_ref = audit['normalized_artifact']
            normalized_path = _checked_path(normalized_ref['path'],
                                            allowed_roots)
            if cf.sha256(normalized_path) != normalized_ref['sha256']:
                raise Blocked('Normalized artifact changed: ' + race_id)
            normalized = json.loads(
                normalized_path.read_text(encoding='utf-8'))
            groups = defaultdict(list)
            for row in normalized['rows']:
                groups[str(row['horse_number'])].append(row)
            starters = normalized['entries']
            if set(groups) != {str(e['horse_number']) for e in starters}:
                raise Blocked('Full horse set missing: ' + race_id)
            rows = []
            for starter in starters:
                rows.append(structural_features(
                    groups[str(starter['horse_number'])], starter))
            scores = np.array([math.fsum(
                COMPONENT_WEIGHTS[k] * float(r[k])
                for k in COMPONENT_WEIGHTS) for r in rows])
            base = cf._softmax(scores)
            for row, score, prob in zip(rows, scores, base):
                row['S_base'] = float(score)
                row['win_prob'] = float(prob)
            target = starters[0]
            environment = _environment(audit, target, race_id,
                                       entry['surface'])
            nonstarters = {str(n['horse_number']): n['horse_name']
                           for n in audit.get('official_nonstarters') or []}
            labels = cf._race_labels(
                outcomes[race_id],
                {str(e['horse_number']) for e in starters}, nonstarters)
            market = np.array([1.0 / labels['final_odds'][
                str(r['horse_number'])] for r in rows])
            races.append({
                'race_id': race_id,
                'date': target['target_race_date'],
                'split': item['split'],
                'pool_block': entry['pool_block'],
                'source': source_id,
                'surface': entry['surface'],
                'venue': item['segment']['venue'],
                'distance': int(item['segment']['distance']),
                'band': item['segment']['band'],
                'rows': rows,
                'environment': environment,
                'winners': labels['winners'],
                'win_payouts': labels['win_payouts'],
                'final_odds': labels['final_odds'],
                'popularity': labels['popularity'],
                'market_probs': (market / market.sum()).tolist(),
            })
    if len(races) != len(wanted):
        missing = sorted(set(wanted) - {r['race_id'] for r in races})
        raise Blocked(f'Manifest races missing from sources: {missing}')
    races.sort(key=lambda r: (r['date'], r['race_id']))
    return races
