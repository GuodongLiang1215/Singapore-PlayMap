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
        sql = ' FROM entities WHERE ' + ' AND '.join(where)
        if category:
            # Category arrays are filtered in Python, without the SQLite JSON extension.
            rows = db.execute('SELECT data_json' + sql + ' ORDER BY title,entity_id', args).fetchall()
            matched = [e for e in (json.loads(r[0]) for r in rows) if category in e['categories']]
            total = len(matched); selected = matched[offset:offset+limit]
        else:
            # Page in SQL so large catalogues (e.g. with Overture places) are not re-parsed per page.
            total = db.execute('SELECT COUNT(*)' + sql, args).fetchone()[0]
            rows = db.execute('SELECT data_json' + sql + ' ORDER BY title,entity_id LIMIT ? OFFSET ?',
                              [*args, limit, offset]).fetchall()
            selected = [json.loads(r[0]) for r in rows]
    items = []
    keys = ('entity_id','primary_uid','member_count','display_title','category','categories','search_tags','search_text',
            'longitude','latitude','address','coordinate_kind','coordinate_is_entrance','visit_status','default_exploration_visible',
            'suggested_duration','opening_hours_display')
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


# ---------------------------------------------------------------------------
# Nearby places (for spatial recommendation). Read-only; cached per snapshot.
# ---------------------------------------------------------------------------
import math
import threading

_NEARBY_CACHE = {'build_id': None, 'rows': []}
_NEARBY_LOCK = threading.Lock()
NEARBY_KEYS = ('entity_id', 'display_title', 'category', 'categories', 'address', 'longitude', 'latitude',
               'coordinate_kind', 'suggested_duration', 'opening_hours_display', 'last_admission')


def _metres(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, h))))


def _nearby_rows(path, build_id):
    """Visible, drawable entities of this snapshot, loaded once and kept in memory."""
    with _NEARBY_LOCK:
        if _NEARBY_CACHE['build_id'] != build_id:
            rows = []
            with ro_connect(path) as db:
                for (raw,) in db.execute('SELECT data_json FROM entities WHERE visible=1 AND map_eligible=1'):
                    e = json.loads(raw)
                    if isinstance(e.get('latitude'), (int, float)) and isinstance(e.get('longitude'), (int, float)):
                        item = {k: e.get(k) for k in NEARBY_KEYS}
                        item['entrance_count'] = len(e.get('entrance_points') or [])
                        item['search_text'] = e.get('search_text') or ''
                        rows.append(item)
            _NEARBY_CACHE.update(build_id=build_id, rows=rows)
        return _NEARBY_CACHE['rows']


def match_search_point(label: str, latitude: float, longitude: float, max_distance_m: float = 80.0):
    """Conservatively link a confirmed OneMap search point to one visible catalogue entity.

    Ordinary entities require close proximity. Reviewed core attractions may match
    farther away when the place name is uniquely strong; this covers large parks
    whose representative point and named access/search point are hundreds of metres apart.
    """
    path, report = snapshot()
    query=normal_name(label)
    query_tokens={t for t in query.split() if len(t)>=3}
    if not query_tokens:
        return None

    def name_score(entity):
        name=normal_name(entity.get('display_title') or '')
        if not name:
            return 0.0
        if name==query:
            return 1.0
        nt={t for t in name.split() if len(t)>=3}
        if not nt:
            return 0.0
        coverage=len(nt & query_tokens)/len(nt)
        query_coverage=len(nt & query_tokens)/len(query_tokens)
        if coverage==1.0:
            return 0.9+0.1*query_coverage
        if coverage>=0.8 and query_coverage>=0.5:
            return 0.8
        return 0.0

    close=[]
    reviewed_large=[]
    for r in _nearby_rows(path, report['build_id']):
        d=_metres(latitude, longitude, r['latitude'], r['longitude'])
        score=name_score(r)
        if score>=0.9 and d<=max_distance_m:
            close.append((score,-d,r))
            continue
        # Large reviewed destinations are allowed a wider identity radius only
        # with very strong name evidence and actual entrance metadata.
        if score>=0.94 and d<=5000 and (r.get('entrance_count') or 0)>0:
            reviewed_large.append((score,-d,r))
    candidates=close or reviewed_large
    if not candidates:
        return None
    candidates.sort(key=lambda x:(x[0],x[1]),reverse=True)
    best=candidates[0]
    if len(candidates)>1:
        second=candidates[1]
        if best[0]-second[0]<0.05 and abs(best[1]-second[1])<100:
            return None
    return {'entity_id':best[2]['entity_id'],'build_id':report['build_id'],
            'distance_m':round(-best[1],2),'name_score':round(best[0],3),
            'match_scope':'close' if -best[1]<=max_distance_m else 'reviewed_large_destination'}


@router.get('/nearby')
def nearby(lat: float = Query(..., ge=1.15, le=1.48), lon: float = Query(..., ge=103.55, le=104.1),
           radius_m: float = Query(1000, gt=0, le=5000), category: str = Query('', max_length=40),
           q: str = Query('', max_length=160), limit: int = Query(20, ge=1, le=100),
           build_id: str | None = None):
    """Places within radius_m of (lat, lon), nearest first. Straight-line distance, not walking distance."""
    if category and category not in ('nature', 'tourism', 'monument', 'heritage', 'food'):
        raise HTTPException(422, 'Unknown category')
    path, report = snapshot(build_id)
    terms = normal_name(q).split()
    # Cheap box filter before the exact distance (1 degree latitude ~ 111 km).
    dlat = radius_m / 111_000
    dlon = radius_m / (111_000 * max(0.1, math.cos(math.radians(lat))))
    found = []
    for r in _nearby_rows(path, report['build_id']):
        if abs(r['latitude'] - lat) > dlat or abs(r['longitude'] - lon) > dlon:
            continue
        if category and category not in (r['categories'] or [r['category']]):
            continue
        if terms and not all(t in r['search_text'] for t in terms):
            continue
        d = _metres(lat, lon, r['latitude'], r['longitude'])
        if d <= radius_m:
            found.append((d, r))
    found.sort(key=lambda x: (x[0], x[1]['entity_id']))
    items = [{**{k: v for k, v in r.items() if k != 'search_text'}, 'distance_m': round(d)} for d, r in found[:limit]]
    return {'build_id': report['build_id'], 'center': {'latitude': lat, 'longitude': lon}, 'radius_m': radius_m,
            'matched_total': len(found), 'returned_count': len(items), 'places': items,
            'distance_kind': 'straight_line_not_walking_route',
            'note': 'Visible catalogue places only; hidden old or duplicate records are excluded.'}
