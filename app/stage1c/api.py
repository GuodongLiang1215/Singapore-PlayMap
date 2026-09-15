"""Read-only entity catalogue and evidence API, with snapshot version checks."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from app.config import ROOT
from app.stage1.build import read_json, safe_path
from app.stage1.normalise import normal_name
from app.db import ro_connect
from app.stage1c.review import _hours_view, local_date

router = APIRouter(prefix='/api/stage1c', tags=['Stage1C reviewed entity catalogue'])


def snapshot(build_id=None):
    try:
        ptr = read_json(ROOT/'data'/'processed'/'stage1c_current.json')
        path = safe_path(ROOT, ptr['database_relative_path'], Path('data')/'processed'/'stage1c')
        rp = safe_path(ROOT, ptr['report_relative_path'], Path('data')/'processed'/'stage1c')
        report = read_json(rp)
        if not path.is_file() or report.get('stage') != '1C' or report['build_id'] != ptr['build_id']:
            raise ValueError('Incomplete sidecar')
        with ro_connect(path) as db:
            row = db.execute("SELECT value_json FROM metadata WHERE key='report'").fetchone()
            if not row or json.loads(row[0])['build_id'] != ptr['build_id']:
                raise ValueError('Sidecar database version mismatch')
    except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(409, 'Run scripts/build_stage1c.py to create a complete reviewed catalogue.') from exc
    if build_id is not None and build_id != report['build_id']:
        raise HTTPException(409, 'Catalogue was rebuilt. Reload to avoid mixing versions.')
    return path, report


@router.get('/status')
def status():
    report = snapshot()[1]
    return {**report, 'served_on': local_date().isoformat(), 'statistics_as_of': report['as_of']}


@router.get('/features')
def features(q: str = Query('', max_length=160), category: str = Query('', max_length=40),
             view: str = 'explore', offset: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=2000),
             build_id: str | None = None):
    if view not in ('all','explore','holds'):
        raise HTTPException(422, 'Invalid view')
    if category and category not in ('nature','tourism','monument','heritage','food'):
        raise HTTPException(422, 'Unknown category')
    path, report = snapshot(build_id)
    where = ['map_eligible=1']; args = []
    if view == 'explore': where.append('visible=1')
    if view == 'holds': where.append('visible=0')
    for term in normal_name(q).split():
        where.append('instr(search_text,?)>0'); args.append(term)
    with ro_connect(path) as db:
        # The nationwide candidate catalogue is small enough to filter its explicit
        # category arrays without depending on SQLite JSON-extension availability.
        rows = db.execute('SELECT data_json FROM entities WHERE '+ ' AND '.join(where) + ' ORDER BY title,entity_id', args).fetchall()
    matched = [json.loads(r[0]) for r in rows]
    if category: matched = [e for e in matched if category in e['categories']]
    total = len(matched); selected = matched[offset:offset+limit]
    items = []
    keys = ('entity_id','primary_uid','member_count','display_title','category','categories','search_tags','search_text',
            'longitude','latitude','address','coordinate_kind','coordinate_is_entrance','visit_status','default_exploration_visible')
    for e in selected:
        props = {k:e.get(k) for k in keys}
        props['regular_hours_evidence_count'] = len(e['regular_hours_evidence'])
        props['legacy_review_count'] = len(e['legacy_reviews'])
        items.append({'type':'Feature','id':e['entity_id'],'properties':props,
                      'geometry':{'type':'Point','coordinates':[e['longitude'], e['latitude']]}})
    nxt = offset + len(items) if offset + len(items) < total else None
    return {'type':'FeatureCollection','features':items,'build_id':report['build_id'],
            'matched_total':total,'returned_count':len(items),'next_offset':nxt,
            'geographic_filter':None,'view':view,'pagination_complete':nxt is None,
            'note':'Grouped source identities, not verified operational destinations.'}


@router.get('/entity')
def entity(entity_id: str = Query(..., min_length=1, max_length=512), build_id: str | None = None):
    path, report = snapshot(build_id)
    with ro_connect(path) as db:
        row = db.execute('SELECT data_json FROM entities WHERE entity_id=?', (entity_id,)).fetchone()
        if row is None: raise HTTPException(404, 'Entity not found')
        e = json.loads(row[0])
        members = [json.loads(x[0]) for x in db.execute('SELECT r.data_json FROM source_records r JOIN members m '
                    'ON r.record_uid=m.record_uid WHERE m.entity_id=? ORDER BY r.record_uid', (entity_id,))]
    e['regular_hours_evidence'] = [_hours_view(h, local_date()) for h in e['regular_hours_evidence']]
    return {'entity':e,'source_records':members,'build_id':report['build_id'],
            'served_on':local_date().isoformat(), 'raw_records_deleted':False}


@router.get('/resolve')
def resolve(uid: str = Query(..., min_length=1, max_length=512), build_id: str | None = None):
    path, report = snapshot(build_id)
    with ro_connect(path) as db:
        row = db.execute('SELECT entity_id FROM members WHERE record_uid=?', (uid,)).fetchone()
    if row is None: raise HTTPException(404, 'Source record not found in candidate catalogue')
    return {'record_uid':uid,'entity_id':row[0],'build_id':report['build_id']}
