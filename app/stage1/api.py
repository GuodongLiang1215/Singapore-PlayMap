"""Read-only Stage1 snapshot API. Explicit pagination; no silent data sampling."""
import json
import sqlite3
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from app.config import ROOT
from app.stage1.build import read_json, safe_path
from app.stage1.normalise import normal_name

router=APIRouter(prefix='/api/stage1', tags=['Stage 1A — source inspection'])
LAYERS={
 'places':('role','candidate_places'),
 'parks':('source_key','nparks_boundaries'),
 'facilities':('source_key','nparks_facilities'),
 'tracks':('source_key','nparks_tracks'),
}

def snapshot():
    p=ROOT/'data'/'processed'/'stage1a_current.json'
    if not p.exists(): raise HTTPException(409, 'Run scripts/build_stage1.py before opening the map.')
    try:
        pointer=read_json(p)
        db=safe_path(ROOT,pointer['database_relative_path'],Path('data')/'processed'/'stage1a')
        report=safe_path(ROOT,pointer['report_relative_path'],Path('data')/'processed'/'stage1a')
        if not db.is_file() or not report.is_file(): raise ValueError('Snapshot files missing')
        return db,read_json(report)
    except (OSError,ValueError,KeyError,RuntimeError) as exc:
        raise HTTPException(409,'Stage1 snapshot is invalid. Rebuild with scripts/build_stage1.py.') from exc

def connect(path):
    # as_uri handles Windows drive letters/spaces; the snapshot is read-only.
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    return db

@router.get('/status')
def status():
    _,report=snapshot()
    return report

@router.get('/features')
def features(layer:str='places',q:str=Query('',max_length=160),category:str=Query('',max_length=40),
             offset:int=Query(0,ge=0),limit:int=Query(1500,ge=1,le=2000),build_id:str|None=None):
    if layer not in LAYERS: raise HTTPException(422,'Unknown layer')
    path,report=snapshot()
    if build_id is not None and build_id!=report['build_id']:
        raise HTTPException(409,'Data was rebuilt; reload the map to avoid mixing snapshots.')
    col,value=LAYERS[layer]
    conditions=[f'{col}=?','map_eligible=1']; args=[value]
    if category: conditions.append('category=?');args.append(category)
    for term in normal_name(q).split():
        conditions.append('instr(search_text,?)>0');args.append(term)
    where=' AND '.join(conditions)
    with connect(path) as db:
        total=db.execute('SELECT COUNT(*) FROM features WHERE '+where,args).fetchone()[0]
        rows=db.execute('SELECT normalised_json FROM features WHERE '+where+' ORDER BY source_key,source_row LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
    output=[]
    for row in rows:
        d=json.loads(row[0]);geometry=d.pop('geometry')
        # Keep large narrative text in the detail endpoint, not every map page.
        props={k:d[k] for k in ('record_uid','source_key','role','category','name','display_title','address',
            'longitude','latitude','coordinate_kind','opening_verification','access_status','planning_ready','data_issues')}
        output.append({'type':'Feature','id':d['record_uid'],'geometry':geometry,'properties':props})
    next_offset=offset+len(output) if offset+len(output)<total else None
    return {'type':'FeatureCollection','features':output,'build_id':report['build_id'],
        'layer':layer,'matched_total':total,'returned_count':len(output),'next_offset':next_offset,
        'pagination_complete':next_offset is None,'geographic_filter':None,
        'note':'Source records, not verified unique destinations. Follow next_offset for remaining records.'}

@router.get('/record')
def record(uid:str=Query(...,min_length=1,max_length=512),build_id:str|None=None):
    path,report=snapshot()
    if build_id is not None and build_id!=report['build_id']:
        raise HTTPException(409,'Data was rebuilt; reload the map.')
    with connect(path) as db:
        row=db.execute('SELECT normalised_json,raw_feature_json FROM features WHERE record_uid=?',(uid,)).fetchone()
    if row is None: raise HTTPException(404,'Record not found')
    return {'record':json.loads(row[0]),'raw_source_feature':json.loads(row[1]),'build_id':report['build_id']}
