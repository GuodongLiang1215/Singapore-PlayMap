"""Read-only catalogue API. All selections and incomplete data are explicit."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from app.config import ROOT
from app.stage1.build import read_json, safe_path
from app.stage1.normalise import normal_name
from app.stage1b.build import ro_connect

router = APIRouter(prefix='/api/stage1b',tags=['Stage1B catalogue and candidate associations'])
LAYERS = {'places':("f.role=?",'candidate_places'),
          'parks':("f.source_key=?",'nparks_boundaries'),
          'facilities':("f.source_key=?",'nparks_facilities'),
          'access_points':("e.facility_class=?",'ACCESS POINT'),
          'tracks':("f.source_key=?",'nparks_tracks')}
GROUPS={'access','activity','support','transport','information','food','unknown'}


def snapshot(build_id=None):
    try:
        ptr=read_json(ROOT/'data'/'processed'/'stage1b_current.json')
        path=safe_path(ROOT,ptr['database_relative_path'],Path('data')/'processed'/'stage1b')
        rp=safe_path(ROOT,ptr['report_relative_path'],Path('data')/'processed'/'stage1b')
        report=read_json(rp)
        if not path.is_file() or report['build_id']!=ptr['build_id']:
            raise ValueError('Incomplete snapshot')
    except (OSError,ValueError,KeyError,RuntimeError) as exc:
        raise HTTPException(409,'Run scripts/build_stage1b.py; a complete Stage1B snapshot is required.') from exc
    if build_id is not None and build_id!=report['build_id']:
        raise HTTPException(409,'Catalogue was rebuilt. Reload the page to avoid mixed snapshots.')
    return path,report


@router.get('/status')
def status():
    return snapshot()[1]


@router.get('/features')
def features(layer:str='places', q:str=Query('',max_length=160),category:str=Query('',max_length=40),
             facility_group:str=Query('',max_length=40),view:str='all',
             offset:int=Query(0,ge=0),limit:int=Query(1500,ge=1,le=2000),build_id:str|None=None):
    if layer not in LAYERS or view not in ('all','explore'):
        raise HTTPException(422,'Invalid layer or view')
    if facility_group and facility_group not in GROUPS:
        raise HTTPException(422,'Unknown facility group')
    if view=='explore' and layer!='places':
        raise HTTPException(422,'Exploration view applies only to destination candidates')
    path,report=snapshot(build_id)
    condition,value=LAYERS[layer]
    conditions=[condition,'f.map_eligible=1'];args=[value]
    if category: conditions.append('f.category=?');args.append(category)
    if facility_group: conditions.append('e.facility_group=?');args.append(facility_group)
    if view=='explore': conditions.append('e.default_exploration_visible=1')
    for term in normal_name(q).split():
        conditions.append('instr(e.search_text,?)>0');args.append(term)
    where=' AND '.join(conditions)
    table='features f JOIN enrichment e ON f.record_uid=e.record_uid'
    with ro_connect(path) as db:
        total=db.execute('SELECT COUNT(*) FROM '+table+' WHERE '+where,args).fetchone()[0]
        rows=db.execute('SELECT f.normalised_json,e.data_json FROM '+table+' WHERE '+where+
                        ' ORDER BY f.source_key,f.source_row LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
    items=[]
    for row in rows:
        r=json.loads(row[0]);e=json.loads(row[1])
        p={k:r.get(k) for k in ('record_uid','source_key','role','category','name','address','longitude',
            'latitude','coordinate_kind','planning_ready','opening_verification')}
        p.update({k:e.get(k) for k in ('display_title','search_tags','search_text','facility_class','facility_group',
                 'facility_group_label','source_status_interpreted','source_period_label','review_hold',
                 'default_exploration_visible','coordinate_role','source_permissions','is_staircase_source')})
        items.append({'type':'Feature','id':r['record_uid'],'properties':p,'geometry':r['geometry']})
    nxt=offset+len(items) if offset+len(items)<total else None
    return {'type':'FeatureCollection','features':items,'build_id':report['build_id'],'layer':layer,
            'view':view,'matched_total':total,'returned_count':len(items),'next_offset':nxt,
            'pagination_complete':nxt is None,'geographic_filter':None,
            'note':'Source records and candidate links; NOT confirmed operational destinations or entrances.'}


@router.get('/record')
def record(uid:str=Query(...,min_length=1,max_length=512),build_id:str|None=None):
    path,report=snapshot(build_id)
    with ro_connect(path) as db:
        row=db.execute('SELECT f.normalised_json,f.raw_feature_json,e.data_json FROM features f JOIN enrichment e '
                       'ON f.record_uid=e.record_uid WHERE f.record_uid=?',(uid,)).fetchone()
        if row is None: raise HTTPException(404,'Record not found')
        r,raw,e=map(json.loads,row)
        boundaries=[];access=[]
        if r['source_key']=='nparks_parks':
            links=e['park_boundary_candidates']
            boundary_ids=[b['boundary_uid'] for b in links if b['access_propagation_candidate']]
        elif r['source_key']=='nparks_boundaries':
            boundary_ids=[uid]
        else:
            boundary_ids=[]
        if r['source_key']=='nparks_facilities':
            boundaries=e['access_boundary_candidates']
        for boundary_uid in sorted(set(boundary_ids)):
            for x in db.execute('SELECT a.data_json,f.normalised_json,e.data_json FROM access_links a '
                                'JOIN features f ON f.record_uid=a.access_uid '
                                'JOIN enrichment e ON e.record_uid=a.access_uid WHERE a.boundary_uid=? '
                                'ORDER BY a.access_uid',(boundary_uid,)):
                link,ar,ae=map(json.loads,x)
                access.append({**link,'longitude':ar['longitude'],'latitude':ar['latitude'],
                               'display_title':ae['display_title']})
        pairs=[p for p in report['duplicate_pairs'] if uid in (p['left_uid'],p['right_uid'])]
    return {'record':r,'raw_source_feature':raw,'enrichment':e,'build_id':report['build_id'],
            'access_point_candidates':access,'boundary_candidates':boundaries,'duplicate_candidates':pairs,
            'candidate_links_verified':False}
