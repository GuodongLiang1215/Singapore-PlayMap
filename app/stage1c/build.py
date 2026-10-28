"""A Stage1C sidecar database: original Stage1A/1B and raw files are read-only."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.stage1.build import BuildError, dump_json, read_json, safe_path
from app.db import ro_connect
from app.stage1c.review import process

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE source_records(record_uid TEXT PRIMARY KEY,source_key TEXT NOT NULL,data_json TEXT NOT NULL);
CREATE TABLE entities(entity_id TEXT PRIMARY KEY,primary_uid TEXT NOT NULL REFERENCES source_records(record_uid),
 title TEXT NOT NULL,category TEXT NOT NULL,search_text TEXT NOT NULL,visible INTEGER NOT NULL,
 map_eligible INTEGER NOT NULL,data_json TEXT NOT NULL);
CREATE TABLE members(record_uid TEXT PRIMARY KEY REFERENCES source_records(record_uid),
 entity_id TEXT NOT NULL REFERENCES entities(entity_id));
CREATE INDEX members_entity ON members(entity_id);
CREATE INDEX entity_visibility ON entities(visible,map_eligible);
CREATE TABLE access_reviews(access_uid TEXT NOT NULL,boundary_uid TEXT NOT NULL,status TEXT NOT NULL,
 data_json TEXT NOT NULL,PRIMARY KEY(access_uid,boundary_uid));
CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
'''


def j(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def load_stage1b(root: Path) -> dict:
    """Reconstruct candidates from the complete local SQLite snapshot, not report samples."""
    pointer = read_json(root/'data'/'processed'/'stage1b_current.json')
    dbpath = safe_path(root, pointer['database_relative_path'], Path('data')/'processed'/'stage1b')
    rp = safe_path(root, pointer['report_relative_path'], Path('data')/'processed'/'stage1b')
    report = read_json(rp)
    if report.get('stage') != '1B' or report.get('build_id') != pointer.get('build_id'):
        raise BuildError('Stage1B pointer/profile mismatch')
    if not report.get('all_sources_processed') or report.get('scope') != 'Singapore nationwide' or report.get('geographic_filter') is not None:
        raise BuildError('A full nationwide Stage1B snapshot is required')
    registry = read_json(root/'config'/'sources.json')['sources']
    if {s['key'] for s in registry} != set(report['source_record_counts']):
        raise BuildError('Registry differs from Stage1B; rebuild previous stages before using it')
    with ro_connect(dbpath) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise BuildError('Stage1B SQLite quick_check failed')
        meta = db.execute("SELECT value_json FROM stage1b_metadata WHERE key='report'").fetchone()
        if not meta or json.loads(meta[0]) != report:
            raise BuildError('Stage1B report and SQLite metadata disagree')
        counts = dict(db.execute('SELECT source_key,COUNT(*) FROM features GROUP BY source_key'))
        if counts != report['source_record_counts'] or sum(counts.values()) != report['retained_record_count']:
            raise BuildError('Stage1B source counts do not agree with actual records')
        records = []
        for row in db.execute("SELECT f.normalised_json,e.data_json FROM features f JOIN enrichment e ON f.record_uid=e.record_uid "
                              "WHERE f.role='candidate_places' ORDER BY f.source_key,f.source_row"):
            r, e = map(json.loads, row)
            r.pop('geometry', None)
            r['stage1b'] = e
            records.append(r)
        if len(records) != report['candidate_place_record_count']:
            raise BuildError('Candidate/enrichment join is incomplete')
        park_links = [json.loads(row[0]) for row in db.execute('SELECT data_json FROM park_links ORDER BY park_uid,boundary_uid')]
        access_links = [json.loads(row[0]) for row in db.execute('SELECT data_json FROM access_links ORDER BY access_uid,boundary_uid')]
    spatial = report['spatial_association']
    if len(park_links) != spatial['park_boundary_link_count'] or len(access_links) != spatial['access_boundary_link_count']:
        raise BuildError('Stage1B link counts are inconsistent')
    issues = read_json(rp.parent/'spatial_issues.json')['issues']
    return {'profile': report, 'records': records, 'park_links': park_links, 'access_links': access_links,
            'spatial_issues': issues, 'source_validation': 'read_only_full_stage1b_sqlite_and_profile_checked'}


def audit_markdown(report: dict, outcome: dict) -> str:
    s = outcome['stats']
    lines = [
        '# Singapore PlayMap · Stage 1C review and update results', '',
        f"Generated at: {report['generated_at']}",
        f"Input validation: `{report['source_validation']}`", '',
        '## Data scope', '',
        'Coverage is nationwide, with no geographic cropping. This organises source records; it is not a complete inventory of Singapore attractions.',
        f"Input: **{s['input_candidate_source_records']}** candidate source records; display: **{s['entity_display_count']}** place groups.",
        f"Explicitly reviewed groups: **{s['reviewed_group_count']}**; source records deleted: **0**.",
        f"The default view shows **{s['default_exploration_entity_count']}** groups. **This is not a count of places currently open or ready for planning.**", '',
        '## Review of potential duplicate pairs', '',
        '| Pair | Decision | Reason |', '|---|---|---|',
    ]
    labels = {'same_place': 'Same place: retain all sources', 'keep_separate': 'Keep separate', 'related_not_same': 'Related, but represent different visit units'}
    for d in outcome['identity_reviews']:
        names = ' / '.join(str(g['name']).replace('|', '\\|') for g in d['expected_records'])
        decision = labels[d['action']] if d['applied'] else 'Not applied: input record or date mismatch'
        lines.append(f"| {names} | {decision} | {d['reason_zh']} |")
    lines += ['', 'Only candidate pairs listed in the configuration were reviewed. Other duplicates with different names may remain; deduplication is not complete.', '',
              '## Access-point label review', '',
              f"All **{s['access_boundary_links_retained']}** input spatial links are retained. The radius is unchanged, and links are not reassigned to the nearest park.", '',
              '| Label review result | Link count |', '|---|---:|']
    access_labels = {'name_supports_target': 'Name supports the candidate target', 'explicit_target_differs': 'Name identifies another target; no automatic reassignment',
                     'broader_area_context': 'Supports broader area context only', 'broad_target_label': 'Place name is too broad to identify one target',
                     'unspecified_or_unresolved': 'Target unspecified or unresolved'}
    for k, v in s['access_semantic_status_counts'].items():
        lines.append(f'| {access_labels[k]} | {v} |')
    lines += ['', 'These compare source names with boundary names; they are not field verification. Every link retains `entrance_verified=false`.',
              f"Access points without a spatial match in the original report: {report['source_spatial_association'].get('access_points_without_boundary_candidates', 'unknown count')}. All remain in the original Stage 1B layer; they are not absent or necessarily unusable.", '',
              '## Official website checks in this review', '']
    for e in outcome['entities']:
        for x in e['legacy_reviews']:
            lines += [f"### {e['display_title']}", x['note_zh'],
                      f"Checked on: {x['checked_on']}; publisher: {x['publisher']}.", x['evidence_summary_zh']]
            lines.extend(f'- {url}' for url in x['web_sources'])
            lines += ['The old record is retained but excluded from the default view. The new address does not inherit the old coordinates; the new location has not been geocoded.', '']
        for x in e['regular_hours_evidence']:
            intervals = x['weekly_minutes']['0']
            display = ', '.join(f'{a//60:02d}:{a%60:02d}—{b//60:02d}:{b%60:02d}' for a,b in intervals)
            lines += [f"### {e['display_title']} · Regular opening hours", f'Every day: {display}.', x['note_zh'],
                      f"Checked on: {x['checked_on']}; suggested recheck: {x['recheck_on']} (the project uses a 30-day review policy)."]
            lines.extend(f'- {url}' for url in x['web_sources'])
            lines += ['Temporary closures, special events, individual businesses and live opening status are unverified.', '']
    lines += ['## Outstanding work', '',
              f"{s['historical_source_status_hold_entities']} places held for source-status review still require verification. The {len(report.get('source_spatial_issues_preserved', []))} geometry issues from the original report remain unchanged.",
              'This build obtains no new coordinates, makes no live routing or LLM calls, and adds no visit durations or free-entry labels.',
              'Website checks cover only the fields explicitly listed above. Full itinerary planning still requires routing, time checks and further data.', '']
    return '\n'.join(lines)


def write_sidecar(root: Path, payload: dict, config: dict, progress=print, *, publish=True, as_of=None) -> dict:
    """Write only new output. `payload` may also be an explicitly labelled author preview."""
    root = Path(root).resolve()
    result = process(payload['records'], payload['park_links'], payload['access_links'], config, as_of=as_of)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = root/'data'/'processed'/'stage1c'/stamp
    output.mkdir(parents=True, exist_ok=False)
    dbpath = output/'entities.sqlite3'
    base = payload['profile']
    report = {
        'schema_version': 1, 'stage': '1C', 'build_id': stamp,
        'generated_at': datetime.now(timezone.utc).isoformat(), 'scope': 'Singapore nationwide', 'geographic_filter': None,
        'source_stage1b_build_id': base['build_id'], 'source_validation': payload['source_validation'],
        'source_stage1b_record_count': base['retained_record_count'],
        'source_stage1b_record_count_is_not_sidecar_row_count': True,
        'source_record_counts': base['source_record_counts'],
        'source_spatial_association': base['spatial_association'],
        'source_spatial_issues_preserved': payload.get('spatial_issues', []),
        'source_snapshot_records_unchanged': True, 'original_sources_modified': False,
        'all_candidate_source_records_retained_in_sidecar': True,
        'as_of': result['as_of'], **result['stats'],
        'unapplied_identity_reviews': [d for d in result['identity_reviews'] if not d['applied']],
        'unapplied_field_reviews': result['field_reviews_unapplied'],
        'whole_inventory_current_status_verified': False,
        'all_duplicates_resolved': False, 'live_routing': False, 'live_llm': False,
        'verified_entrance_count': 0, 'planning_ready_count': 0,
        'source_bounds_as_lonlat': base.get('bounds_as_lonlat'),
        'snapshot_relative_path': output.relative_to(root).as_posix(),
        'database_relative_path': dbpath.relative_to(root).as_posix(),
        'cautions': ['Entity identity is not an entry or operational-status guarantee.',
                     'Display coordinates are selected source coordinates, not averaged or new geocodes.',
                     'The sidecar stores candidates and evidence, not a duplicate of all 31,518 source features.',
                     'All source features remain in the unchanged Stage1B database.',
                     'Explicitly reviewed schedules are scope-limited, dated evidence, not a live closure service.'],
    }
    db = sqlite3.connect(dbpath)
    try:
        db.executescript(SCHEMA)
        for r in payload['records']:
            db.execute('INSERT INTO source_records VALUES(?,?,?)', (r['record_uid'], r['source_key'], j(r)))
        for e in result['entities']:
            db.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?)', (e['entity_id'], e['primary_uid'], e['display_title'],
                       e['category'], e['search_text'], int(e['default_exploration_visible']), int(e['map_eligible']), j(e)))
            for uid in e['member_uids']:
                db.execute('INSERT INTO members VALUES(?,?)', (uid, e['entity_id']))
        for a in result['access_link_reviews']:
            db.execute('INSERT INTO access_reviews VALUES(?,?,?,?)',
                       (a['access_uid'], a['boundary_uid'], a['semantic_review']['status'], j(a)))
        db.execute('INSERT INTO metadata VALUES(?,?)', ('report', j(report)))
        db.execute('INSERT INTO metadata VALUES(?,?)', ('review_config', j(config)))
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise BuildError('Sidecar foreign-key check failed')
        db.commit()
        db.close()
        dump_json(output/'stage1c_profile.json', report)
        dump_json(output/'entities.json', {'count':len(result['entities']), 'entities':result['entities']})
        dump_json(output/'identity_decisions.json', {'decisions':result['identity_reviews']})
        dump_json(output/'access_semantic_reviews.json', {'links':result['access_link_reviews']})
        dump_json(output/'evidence_config.json', config)
        audit = audit_markdown(report, result)
        (output/'review_findings.md').write_text(audit, encoding='utf-8')
        if publish:
            dump_json(root/'reports'/'stage1c_profile.json', report)
            (root/'reports'/'stage1c_review_findings.md').write_text(audit, encoding='utf-8')
            dump_json(root/'data'/'processed'/'stage1c_current.json', {
                'build_id':stamp, 'database_relative_path':report['database_relative_path'],
                'report_relative_path':(output/'stage1c_profile.json').relative_to(root).as_posix(),
            })
        progress(f"[GROUPS] source_candidates={len(payload['records'])} display_entities={report['entity_display_count']} reviewed_groups={report['reviewed_group_count']}")
        progress(f"[VISIBLE] default_exploration={report['default_exploration_entity_count']} (NOT confirmed open)")
        progress(f"[EVIDENCE] legacy_holds={report['legacy_hold_entities']} regular_hours_entities={report['regular_hours_entity_count']}")
        progress('[DONE] '+str(output/'stage1c_profile.json'))
        return report
    except Exception as exc:
        db.close()
        dump_json(output/'FAILED.json', {'error_type':type(exc).__name__, 'error':str(exc),
                  'previous_snapshots_unchanged':True, 'incomplete_new_sidecar':True})
        raise


def build(root: Path, progress=print):
    root = Path(root).resolve()
    progress('[READ] Verify the full Stage1B source snapshot. No redownload, no geographic crop.')
    payload = load_stage1b(root)
    config = read_json(root/'config'/'stage1c_reviews.json')
    progress('[REVIEW] Explicit identity decisions, dated web evidence, access-label checks.')
    return write_sidecar(root, payload, config, progress)
