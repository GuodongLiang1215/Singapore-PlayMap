"""Build an append-only Stage1 snapshot from ALL registered Stage0 manifests."""
from __future__ import annotations
import csv
import json
import os
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.stage1.normalise import (CANDIDATE_SOURCES, PROFILE_FIELDS, normal_name,
    record_id, normalise_feature, duplicate_candidates)

class BuildError(RuntimeError): pass

def read_json(path):
    def reject(v): raise ValueError('Non-finite JSON constant: ' + v)
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=reject)

def dump_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()

def safe_path(root, relative, expected_folder):
    root = Path(root).resolve(); base = (root / expected_folder).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(base): raise BuildError('Path is outside ' + str(expected_folder))
    return path

def write_csv(path, rows, fields):
    # Review CSVs are for inspection only; preserve real values in JSON/SQLite.
    # Escape spreadsheet formulas when a CSV is opened by Excel.
    def cell(v):
        if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')): return "'" + v
        return v
    with Path(path).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for r in rows: w.writerow({k: cell(r.get(k)) for k in fields})

SCHEMA = '''
CREATE TABLE features (
 record_uid TEXT PRIMARY KEY, source_key TEXT NOT NULL, role TEXT NOT NULL,
 category TEXT NOT NULL, name TEXT, search_text TEXT NOT NULL,
 source_row INTEGER NOT NULL, map_eligible INTEGER NOT NULL,
 xmin REAL,ymin REAL,xmax REAL,ymax REAL,
 normalised_json TEXT NOT NULL, raw_feature_json TEXT NOT NULL
);
CREATE INDEX features_source ON features(source_key,source_row);
CREATE INDEX features_role ON features(role,map_eligible);
CREATE TABLE sources(source_key TEXT PRIMARY KEY, metadata_json TEXT NOT NULL);
CREATE TABLE build_metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
'''

def build(root, progress=print):
    root = Path(root).resolve()
    registry = read_json(root / 'config' / 'sources.json')['sources']
    if len({s['key'] for s in registry}) != len(registry): raise BuildError('Duplicate source registry keys')
    if not registry: raise BuildError('Empty source registry')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = root / 'data' / 'processed' / 'stage1a' / stamp
    output.mkdir(parents=True, exist_ok=False)
    db_path = output / 'catalog.sqlite3'
    report = {'schema_version': 1, 'stage': '1A', 'build_id': stamp,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'Singapore nationwide', 'geographic_filter': None,
        'all_registered_sources_required': True, 'sources': [],
        'auto_merged_records': 0, 'verified_entrance_count': 0,
        'planning_ready_count': 0, 'live_routing': False, 'live_llm': False,
        'source_coverage_is_not_complete_attraction_inventory': True,
        'raw_source_files_modified': False, 'topology_verified': False,
        'deduplication_policy': 'review_candidates_only_no_automatic_merging',
        'duplicate_rule': 'same normalised name, OR <=100m and SequenceMatcher>=0.82; suggestions only',
        'cautions': ['Source record counts are NOT unique attraction counts.',
          'Map visibility does not verify access, current opening hours, duration or entrances.',
          'No polygon is converted to a navigable entrance; tracks are not a routing graph.',
          'Legacy/source identifiers may change across publisher snapshots.',
          'Only basic geometry structure and degree ranges are checked; not geometry topology or national membership.']}
    records_for_review = []; global_bounds = None; all_issues = Counter()
    db = sqlite3.connect(db_path)
    try:
        db.executescript(SCHEMA)
        with (output/'data_issues.jsonl').open('w',encoding='utf-8') as issues_file:
            for source in registry:
                key = source['key']; progress('[READ] ' + key)
                manifest_file = root/'data'/'manifests'/(key+'.json')
                if not manifest_file.is_file(): raise BuildError('Missing Stage0 manifest: ' + key)
                manifest = read_json(manifest_file)
                if manifest.get('source_key') != key or manifest.get('dataset_id') != source['dataset_id']:
                    raise BuildError('Manifest does not match registry: ' + key)
                raw = safe_path(root, manifest['relative_path'], Path('data')/'raw')
                if not raw.is_file(): raise BuildError('Missing raw GeoJSON: ' + str(raw))
                if manifest.get('bytes') != raw.stat().st_size:
                    raise BuildError('Raw file size differs from download manifest: ' + key)
                doc = read_json(raw)
                if not isinstance(doc, dict) or doc.get('type') != 'FeatureCollection' or not isinstance(doc.get('features'),list):
                    raise BuildError('Not a complete FeatureCollection: ' + key)
                features = doc['features']
                expected = (manifest.get('summary') or {}).get('feature_count')
                if len(features) != expected: raise BuildError(f'{key}: manifest count {expected} != actual {len(features)}')
                for f in features:
                    if not isinstance(f,dict) or f.get('type') != 'Feature':
                        raise BuildError('Malformed feature object in '+key+'; raw file retained, no incomplete build published')
                ids = [record_id(f,key,i) for i,f in enumerate(features)]
                repeats = Counter(rid for rid,_ in ids)
                profile = {field: Counter() for field in PROFILE_FIELDS}
                issues_count = Counter(); missing = Counter(); drawable=0; samples=[]
                geometries = Counter(); property_names=set()
                for index, (feature, (rid, basis)) in enumerate(zip(features,ids)):
                    r=normalise_feature(feature,index,source,manifest,doc.get('crs'),rid,basis,repeats[rid]>1)
                    geom = r['geometry_type']; geometries[str(geom)] += 1
                    p = feature.get('properties') if isinstance(feature.get('properties'),dict) else {}
                    property_names.update(p)
                    for field in PROFILE_FIELDS:
                        if field in p:
                            value=json.dumps(p[field],ensure_ascii=False,sort_keys=True)
                            profile[field][value]+=1
                    for field in ('name','address','description','opening_hours_raw'):
                        if not r.get(field): missing[field]+=1
                    if r['map_eligible']:
                        drawable+=1; b=r['bounds']
                        if global_bounds is None: global_bounds=b[:]
                        else: global_bounds=[min(global_bounds[0],b[0]),min(global_bounds[1],b[1]),max(global_bounds[2],b[2]),max(global_bounds[3],b[3])]
                    else: b=[None]*4
                    issues_count.update(r['data_issues']); all_issues.update(r['data_issues'])
                    if r['data_issues']:
                        issues_file.write(json.dumps({'record_uid':r['record_uid'],'issues':r['data_issues']},ensure_ascii=False)+'\n')
                    search=normal_name(' '.join(str(r.get(k) or '') for k in ('name','display_title','address','description')))
                    payload=json.dumps(r,ensure_ascii=False,allow_nan=False,separators=(',',':'))
                    raw_payload=json.dumps(feature,ensure_ascii=False,allow_nan=False,separators=(',',':'))
                    db.execute('INSERT INTO features VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (r['record_uid'],key,r['role'],r['category'],r['name'],search,index,int(r['map_eligible']),*b,payload,raw_payload))
                    if r['role']=='candidate_places':
                        records_for_review.append({k:v for k,v in r.items() if k!='geometry'})
                    if len(samples)<5:
                        samples.append({k:r[k] for k in ('record_uid','name','display_title','longitude','latitude','coordinate_kind','source_status_raw','opening_hours_raw','data_issues')})
                source_report={
                    'source_key':key, 'title':source['title'], 'role':source['role'],
                    'source_period_label':manifest.get('source_period_label'),
                    'raw_relative_path':manifest['relative_path'], 'retrieved_at':manifest.get('retrieved_at'),
                    'source_record_count':len(features), 'stored_record_count':len(features),
                    'map_eligible_count':drawable, 'not_drawable_retained_count':len(features)-drawable,
                    'geometry_types':dict(geometries), 'property_names':sorted(property_names),
                    'missing_field_counts':dict(missing), 'issue_counts':dict(issues_count),
                    'repeated_id_group_count':sum(v>1 for v in repeats.values()),
                    'raw_value_distributions':{k:dict(v) for k,v in profile.items() if v},
                    'samples':samples,
                    'catalogue_url':source['catalogue_url'], 'licence_url':source.get('licence_url'),
                }
                report['sources'].append(source_report)
                db.execute('INSERT INTO sources VALUES(?,?)',(key,json.dumps(source_report,ensure_ascii=False)))
                progress(f'  preserved={len(features)}  drawable={drawable}  retained_not_drawable={len(features)-drawable}')
                # Commit only within the NEW snapshot. The published previous snapshot stays intact.
                db.commit()
                del doc, features
        progress('[REVIEW] Generate duplicate suggestions; no record will be merged.')
        pairs=duplicate_candidates(records_for_review)
        write_csv(output/'duplicate_candidates.csv',pairs,
            ('left_uid','right_uid','left_name','right_name','left_source','right_source','straight_line_metres','name_similarity','reason','auto_merged','decision'))
        write_csv(output/'verification_queue.csv',records_for_review,
            ('record_uid','source_key','name','address','longitude','latitude','coordinate_kind','opening_hours_raw','opening_verification','access_status','entrance_status','price_status','planning_ready'))
        roles=Counter()
        for s in report['sources']: roles[s['role']]+=s['stored_record_count']
        report.update({'all_sources_processed':True,
            'source_count':len(registry), 'source_record_count':sum(s['source_record_count'] for s in report['sources']),
            'stored_record_count':sum(s['stored_record_count'] for s in report['sources']),
            'map_eligible_count':sum(s['map_eligible_count'] for s in report['sources']),
            'candidate_place_record_count':roles['candidate_places'],
            'candidate_place_mappable_count':sum(s['map_eligible_count'] for s in report['sources'] if s['role']=='candidate_places'),
            'role_counts':dict(roles), 'duplicate_candidate_pair_count':len(pairs),
            'issue_counts':dict(all_issues), 'bounds_as_lonlat':global_bounds,
            'snapshot_relative_path':output.relative_to(root).as_posix(),
            'database_relative_path':db_path.relative_to(root).as_posix()})
        db.execute('INSERT INTO build_metadata VALUES(?,?)',('report',json.dumps(report,ensure_ascii=False)))
        db.commit(); db.close()
        dump_json(output/'stage1_profile.json',report)
        # The pointer changes ONLY after all sources and reports have completed.
        pointer={'build_id':stamp,'database_relative_path':report['database_relative_path'],
                 'report_relative_path':(output/'stage1_profile.json').relative_to(root).as_posix()}
        dump_json(root/'data'/'processed'/'stage1a_current.json',pointer)
        dump_json(root/'reports'/'stage1_profile.json',report)
        progress('[DONE] '+str(root/'reports'/'stage1_profile.json'))
        return report
    except Exception as exc:
        db.close()
        dump_json(output/'FAILED.json',{'error_type':type(exc).__name__,'error':str(exc),
            'published_as_current':False,'previous_published_snapshot_unchanged':True})
        raise
