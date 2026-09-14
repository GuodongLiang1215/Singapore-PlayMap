"""Local itinerary API. Provider calls are sequenced, cancellable between legs, and never persisted."""
from __future__ import annotations
import threading
from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from app.stage2a import api as geo
from app.stage1c import api as catalogue
from app.stage2a.models import Contract
from app.stage2b.models import PlanRequest, MAX_VISITS
from app.stage2b.cache import SegmentCache
from app.stage2b.planning import calculate, PlanningError

router = APIRouter(prefix='/api/stage2b', tags=['Stage2B manually ordered itineraries'])
cache = SegmentCache()
_lock = threading.Lock()
_counts = {'plans_completed':0,'plans_failed':0,'plans_cancelled':0}

class ClearRequest(Contract):
    session_id: str = Field(pattern=r'^[a-f0-9]{32}$')

@router.get('/status')
def status():
    base = geo.status()
    with _lock: counts = dict(_counts)
    return {'stage':'2B','scope':'Singapore nationwide','geographic_filter':None,
        'catalogue':base['catalogue'],'credentials':base['credentials'],
        'manual_itinerary_implemented':True,'automatic_selection_implemented':False,
        'order_optimisation_implemented':False,'live_llm':False,
        'implemented_modes':['walk','drive','cycle'],'public_transport_implemented':False,
        'max_visits_per_request':MAX_VISITS,'cache_ttl_seconds':cache.ttl_seconds,
        'cache_scope':'per-browser-session, RAM only','private_queries_saved_to_disk':False,
        'runtime_counters_since_server_start':counts,
        'notice':'多地点通行与停留汇总不等于入口、开放和实时交通已核验。'}

@router.post('/plan')
async def plan(body: PlanRequest, request: Request):
    try:
        result = await calculate(body,geo.client,cache,
            resolver=lambda uid,build:catalogue.entity(uid,build),
            disconnected=request.is_disconnected)
    except PlanningError as exc:
        with _lock: _counts['plans_cancelled' if exc.code=='CLIENT_CANCELLED' else 'plans_failed'] += 1
        raise HTTPException(exc.status,detail=exc.public()) from None
    except HTTPException:
        with _lock: _counts['plans_failed'] += 1
        raise HTTPException(409,detail={'code':'CATALOGUE_CHANGED_OR_UNAVAILABLE',
            'message':'核查目录版本改变或不可读。请刷新页面；原始资料没有被改动。',
            'complete_plan_created':False}) from None
    with _lock: _counts['plans_completed'] += 1
    return result

@router.post('/clear-memory')
def clear_memory(body: ClearRequest):
    cache.clear_session(body.session_id)
    return {'cleared':True,'scope':'this browser session only','disk_files_modified':False}
