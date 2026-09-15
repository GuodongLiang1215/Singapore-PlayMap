"""Build a new full Stage1B snapshot; the Stage1A/raw sources remain unchanged."""
from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from app.db import ro_connect  # Re-exported: existing callers of this module still work.
from app.stage1.build import BuildError, dump_json, read_json, safe_path, write_csv
from app.stage1.normalise import duplicate_candidates
from app.stage1b.rules import interpret, GROUP_LABELS
from app.stage1b.spatial import associate

SCHEMA = '''
CREATE TABLE enrichment (
 record_uid TEXT PRIMARY KEY REFERENCES features(record_uid),
 facility_group TEXT, facility_class TEXT, review_hold INTEGER NOT NULL,
 default_exploration_visible INTEGER NOT NULL, search_text TEXT NOT NULL,
 data_json TEXT NOT NULL
);
CREATE INDEX enrichment_group ON enrichment(facility_group);
CREATE INDEX enrichment_class ON enrichment(facility_class);
CREATE TABLE park_links (
 park_uid TEXT NOT NULL, boundary_uid TEXT NOT NULL, data_json TEXT NOT NULL,
 PRIMARY KEY(park_uid,boundary_uid)
);
CREATE TABLE access_links (
 access_uid TEXT NOT NULL, boundary_uid TEXT NOT NULL, data_json TEXT NOT NULL,
 PRIMARY KEY(access_uid,boundary_uid)
);
CREATE INDEX access_boundary ON access_links(boundary_uid);
CREATE TABLE stage1b_metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
'''


def j(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def input_snapshot(root):
    pointer = read_json(root/'data'/'processed'/'stage1a_current.json')
    path = safe_path(root, pointer['database_relative_path'], Path('data')/'processed'/'stage1a')
    profile_path = safe_path(root, pointer['report_relative_path'], Path('data')/'processed'/'stage1a')
    if not path.is_file():
        raise BuildError('Stage1A database not found; run scripts/build_stage1.py first')
    profile = read_json(profile_path)
    if profile.get('stage') != '1A' or profile.get('build_id') != pointer.get('build_id'):
        raise BuildError('Stage1A pointer/profile version mismatch')
    if not profile.get('all_sources_processed') or profile.get('geographic_filter') is not None:
        raise BuildError('A complete nationwide Stage1A snapshot is required; no pilot fallback')
    registry = read_json(root/'config'/'sources.json')['sources']
    if {s['key'] for s in registry} != {s['source_key'] for s in profile['sources']}:
        raise BuildError('Current registry differs from the Stage1A build; rebuild Stage1A first')
    with ro_connect(path) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise BuildError('Stage1A SQLite quick_check failed')
        meta = db.execute("SELECT value_json FROM build_metadata WHERE key='report'").fetchone()
        if not meta or json.loads(meta[0])['build_id'] != profile['build_id']:
            raise BuildError('Stage1A database/profile build mismatch')
        counts = dict(db.execute('SELECT source_key,COUNT(*) FROM features GROUP BY source_key'))
    expected = {s['source_key']: s['stored_record_count'] for s in profile['sources']}
    if counts != expected:
        raise BuildError('Stage1A stored records do not agree with its profile')
    return path, profile


def _json_csv_rows(rows):
    return [{k: j(v) if isinstance(v, (list, dict)) else v for k,v in r.items()} for r in rows]


def build(root, progress=print, access_radius_metres=50.0, park_radius_metres=100.0):
    root = Path(root).resolve()
    path, base_report = input_snapshot(root)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out = root/'data'/'processed'/'stage1b'/stamp
    out.mkdir(parents=True, exist_ok=False)
    dbpath = out/'catalog.sqlite3'
    db = sqlite3.connect(dbpath)
    db.row_factory = sqlite3.Row
    try:
        progress('[COPY] Preserve the complete Stage1A snapshot; input database is read-only.')
        with ro_connect(path) as previous:
            previous.backup(db)
        db.executescript(SCHEMA)
        candidates, parks, boundaries, access_points = [], [], [], []
        enrichment_by_uid = {}
        group_counts, class_counts, status_counts, changes, source_counts = (Counter() for _ in range(5))
        track_flags = {k: Counter() for k in ('allow_walking','allow_cycling','allow_pmd','allow_wheeling')}
        duplicate_details, holds = [], []
        processed = 0
        source = None
        for row in db.execute('SELECT source_key,normalised_json,raw_feature_json FROM features ORDER BY source_key,source_row'):
            if source != row['source_key']:
                source = row['source_key']
                progress('[INTERPRET] '+source)
            r = json.loads(row['normalised_json']); raw = json.loads(row['raw_feature_json'])
            e = interpret(r, raw)
            db.execute('INSERT INTO enrichment VALUES(?,?,?,?,?,?,?)',
                       (r['record_uid'],e['facility_group'],e['facility_class'],int(e['review_hold']),
                        int(e['default_exploration_visible']),e['search_text'],j(e)))
            processed += 1; source_counts[source] += 1
            if e['display_repairs']:
                changes.update(e['display_repairs'].keys())
            if r['role'] == 'candidate_places':
                candidates.append(r)
                enrichment_by_uid[r['record_uid']] = e
            if source == 'nparks_parks': parks.append(r)
            if source == 'nparks_boundaries': boundaries.append(r)
            if source == 'nea_hawker_centres':
                status_counts[e['source_status_interpreted'] or '__MISSING__'] += 1
            if e['review_hold']:
                holds.append({'record_uid':r['record_uid'],'name':r['name'],
                    'source_status':e['source_status_interpreted'],
                    'source_period_label':e['source_period_label'],
                    'current_status':'not_verified','action':'hold_from_default_exploration_but_keep_record',
                    'catalogue_url':r['provenance'].get('catalogue_url'),
                    'external_url':r.get('external_url')})
            if source == 'nparks_facilities':
                group_counts[e['facility_group']] += 1
                class_counts[e['facility_class'] or '__MISSING__'] += 1
                if e['facility_class'] == 'ACCESS POINT': access_points.append(r)
            if source == 'nparks_tracks':
                for k, value in e['source_permissions'].items(): track_flags[k][value] += 1
        db.commit()
        if processed != base_report['stored_record_count']:
            raise BuildError('Record interpretation did not preserve the full snapshot')
        progress('[SPATIAL] Park points / boundaries / source ACCESS POINT candidates. No entry verification.')
        park_links, access_links, spatial_issues, spatial_report = associate(
            parks, boundaries, access_points, access_radius_metres, park_radius_metres)
        by_park, by_access = defaultdict(list), defaultdict(list)
        for row in park_links:
            db.execute('INSERT INTO park_links VALUES(?,?,?)',(row['park_uid'],row['boundary_uid'],j(row)))
            by_park[row['park_uid']].append(row)
        for row in access_links:
            db.execute('INSERT INTO access_links VALUES(?,?,?)',(row['access_uid'],row['boundary_uid'],j(row)))
            by_access[row['access_uid']].append(row)
        for uid in sorted(set(by_park) | set(by_access)):
            e = json.loads(db.execute('SELECT data_json FROM enrichment WHERE record_uid=?',(uid,)).fetchone()[0])
            e['park_boundary_candidates'] = by_park.get(uid,[])
            e['access_boundary_candidates'] = by_access.get(uid,[])
            db.execute('UPDATE enrichment SET data_json=? WHERE record_uid=?',(j(e),uid))
            if uid in enrichment_by_uid: enrichment_by_uid[uid] = e
        progress('[REVIEW] Export every duplicate suggestion and every candidate place; no auto-merge.')
        pairs = duplicate_candidates(candidates)
        by_uid = {r['record_uid']:r for r in candidates}
        for pair in pairs:
            evidence = {}
            for side in ('left','right'):
                r = by_uid[pair[side+'_uid']]
                evidence[side] = {k:r.get(k) for k in ('record_uid','source_key','name','address','description',
                      'longitude','latitude','coordinate_kind','external_url','opening_hours_raw','provenance')}
            duplicate_details.append({**pair,'evidence':evidence})
        export_candidates = []
        for r in candidates:
            v = {k:v for k,v in r.items() if k != 'geometry'}
            v['stage1b'] = enrichment_by_uid[r['record_uid']]
            export_candidates.append(v)
        dump_json(out/'candidate_catalogue.json',{'data_kind':'actual_local_source_records_not_verified_destinations',
                  'count':len(candidates),'records':export_candidates})
        dump_json(out/'duplicate_review.json',{'automatic_merges':0,'candidate_pair_count':len(pairs),'pairs':duplicate_details})
        dump_json(out/'status_review.json',{'current_status_verified':False,'records':holds})
        dump_json(out/'spatial_issues.json',{'records_not_deleted':True,'issues':spatial_issues})
        dump_json(out/'park_boundary_candidates.json',{'identity_verified':False,'links':park_links})
        dump_json(out/'access_boundary_candidates.json',{'entrances_verified':False,'links':access_links})
        write_csv(out/'duplicate_candidates.csv',pairs,('left_uid','right_uid','left_name','right_name',
                  'left_source','right_source','straight_line_metres','name_similarity','reason','auto_merged','decision'))
        write_csv(out/'status_review.csv',holds,('record_uid','name','source_status','source_period_label','current_status','action','external_url'))
        access_fields=('access_uid','boundary_uid','boundary_name','point_relation','distance_to_polygon_metres',
                       'distance_to_boundary_metres','boundary_candidate_count_for_access_point','park_candidate_uids',
                       'entrance_verified','route_reachability_verified')
        write_csv(out/'access_boundary_candidates.csv',_json_csv_rows(access_links),access_fields)
        report = {
            'schema_version':1,'stage':'1B','build_id':stamp,'generated_at':datetime.now(timezone.utc).isoformat(),
            'scope':'Singapore nationwide','geographic_filter':None,'all_sources_processed':True,
            'source_count':base_report['source_count'],'source_build_id':base_report['build_id'],
            'source_database_relative_path':path.relative_to(root).as_posix(),
            'source_record_count':base_report['stored_record_count'],'retained_record_count':processed,
            'source_record_counts':dict(source_counts),'candidate_place_record_count':len(candidates),
            'default_exploration_visible_count':sum(e['default_exploration_visible'] for e in enrichment_by_uid.values()),
            'source_status_review_hold_count':len(holds),'source_status_review_records':holds,
            'holds_do_not_confirm_current_closure':True,
            'facility_group_counts':dict(group_counts),'facility_class_counts':dict(class_counts),
            'facility_groups_are_project_display_rules':True,'hawker_source_status_counts':dict(status_counts),
            'track_source_permission_counts':{k:dict(v) for k,v in track_flags.items()},
            'walking_permissions_are_source_metadata_not_verified_connectivity':True,
            'spatial_association':spatial_report,'duplicate_candidate_pair_count':len(pairs),
            'duplicate_pairs':[dict(p) for p in pairs],
            'display_repair_record_counts_by_field':dict(changes),
            'verified_entrance_count':0,'planning_ready_count':0,'automatic_merges':0,
            'original_sources_modified':False,'stage1a_database_modified':False,
            'live_routing':False,'live_llm':False,'hours_checked_online':False,
            'source_coverage_is_not_complete_attraction_inventory':True,
            'no_time_budget_or_dwell_time_invented':True,
            'bounds_as_lonlat':base_report.get('bounds_as_lonlat'),
            'snapshot_relative_path':out.relative_to(root).as_posix(),
            'database_relative_path':dbpath.relative_to(root).as_posix(),
            'review_bundle_relative_path':'reports/stage1b_review_bundle.zip',
            'cautions':[
                'Exploration visibility is NOT current-open, safety, or route feasibility verification.',
                'Status labels are preserved historical source claims, not live closure findings.',
                'A spatially matched ACCESS POINT is only an entrance candidate, not a verified entry.',
                'No source record is discarded, merged, or promoted from a facility to an attraction.',
                'No national membership or network topology verification is claimed.',
                'Source record IDs can change with publisher snapshots.',
            ]
        }
        db.execute('INSERT INTO stage1b_metadata VALUES(?,?)',('report',j(report)))
        db.commit(); db.close()
        dump_json(out/'stage1b_profile.json',report)
        # Build the shareable bundle from whitelisted, non-private project outputs.
        # No .env, credentials, raw user conversations or local paths are included.
        bundle = out/'review_bundle.zip'
        names = ['stage1b_profile.json','candidate_catalogue.json','duplicate_review.json','status_review.json',
                 'spatial_issues.json','park_boundary_candidates.json','access_boundary_candidates.json']
        with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for name in names: z.write(out/name,arcname=name)
        progress('[PUBLISH] All inputs preserved; derived snapshot complete.')
        # Publish the pointer only after every derived file exists.
        pointer={'build_id':stamp,'database_relative_path':report['database_relative_path'],
                 'report_relative_path':(out/'stage1b_profile.json').relative_to(root).as_posix()}
        dump_json(root/'data'/'processed'/'stage1b_current.json',pointer)
        dump_json(root/'reports'/'stage1b_profile.json',report)
        target=root/'reports'/'stage1b_review_bundle.zip'
        tmp=target.with_suffix('.zip.tmp-'+stamp)
        shutil.copyfile(bundle,tmp); tmp.replace(target)
        progress('[DONE] '+str(root/'reports'/'stage1b_profile.json'))
        progress('[BUNDLE] '+str(target))
        return report
    except Exception as exc:
        db.close()
        dump_json(out/'FAILED.json',{'error_type':type(exc).__name__,'error':str(exc),
                  'derived_snapshot_incomplete':True,'original_sources_modified':False})
        raise
