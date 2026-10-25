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
from app.stage2b.alternatives import suggest_alternatives
from app.stage2b.recommendations import recommend_nearby

router = APIRouter(prefix='/api/stage2b', tags=['Stage2B manually ordered itineraries'])
cache = SegmentCache()
_lock = threading.Lock()
_counts = {'plans_completed':0,'plans_failed':0,'plans_cancelled':0}


class ClearRequest(Contract):
    session_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class SpatialRecommendationRequest(Contract):
    session_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    revision: int = Field(default=0, ge=0)
    anchor: __import__('app.stage2b.models', fromlist=['PlacePoint']).PlacePoint
    mode: str = Field(default='walk', pattern=r'^(walk|drive|cycle)$')
    radius_m: float = Field(default=1000, gt=0, le=5000)
    category: str = Field(default='', max_length=40)
    q: str = Field(default='', max_length=160)
    limit: int = Field(default=20, ge=1, le=100)
    max_suggestions: int = Field(default=5, ge=1, le=20)
    max_travel_minutes: int | None = Field(default=None, gt=0, le=1440)
    force_refresh: bool = False


class AlternativeRequest(Contract):
    plan_request: PlanRequest
    current_plan: dict
    leg_id: str = Field(pattern=r'^leg-[1-9][0-9]*$')
    radius_m: float = Field(default=1000, gt=0, le=5000)
    category: str = Field(default='', max_length=40)
    limit: int = Field(default=20, ge=1, le=100)
    max_suggestions: int = Field(default=5, ge=1, le=20)


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
            disconnected=request.is_disconnected,
            search_point_matcher=catalogue.match_search_point)
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


@router.post('/recommend-nearby')
async def spatial_recommendations(body: SpatialRecommendationRequest, request: Request):
    """Independent nearby recommendation, verified and ranked by real route time."""
    try:
        return await recommend_nearby(
            anchor=body.anchor,session_id=body.session_id,revision=body.revision,mode=body.mode,
            nearby=catalogue.nearby,client=geo.client,cache=cache,radius_m=body.radius_m,
            category=body.category,q=body.q,limit=body.limit,max_suggestions=body.max_suggestions,
            max_travel_minutes=body.max_travel_minutes,force_refresh=body.force_refresh,
            disconnected=request.is_disconnected,
        )
    except HTTPException:
        raise
    except (KeyError,TypeError,ValueError) as exc:
        raise HTTPException(422,detail={'code':'INVALID_SPATIAL_RECOMMENDATION',
            'message':'附近推荐参数或候选数据无法处理；没有修改行程。','itinerary_modified':False}) from exc


@router.post('/alternatives')
async def alternatives(body: AlternativeRequest, request: Request):
    """Suggest feasible nearby replacements; never modifies the submitted itinerary."""
    if body.plan_request.mode != 'walk' or body.plan_request.max_walk_minutes is None:
        raise HTTPException(422, detail={
            'code':'WALKING_CONSTRAINT_REQUIRED',
            'message':'替代建议当前只处理已设置最大步行时间的步行方案。',
            'itinerary_modified':False,
        })

    violations = (body.current_plan.get('walking_constraint_check') or {}).get('violations') or []
    if not any(v.get('leg_id') == body.leg_id for v in violations):
        raise HTTPException(422, detail={
            'code':'LEG_NOT_REPORTED_AS_VIOLATION',
            'message':'指定路段不在当前方案的步行超限结果中；未生成替代建议。',
            'itinerary_modified':False,
        })

    async def recalculate(trial):
        return await calculate(
            trial, geo.client, cache,
            resolver=lambda uid,build: catalogue.entity(uid,build),
            disconnected=request.is_disconnected,
            search_point_matcher=catalogue.match_search_point,
        )

    try:
        return await suggest_alternatives(
            request=body.plan_request,
            current_plan=body.current_plan,
            leg_id=body.leg_id,
            nearby=catalogue.nearby,
            calculate_plan=recalculate,
            radius_m=body.radius_m,
            category=body.category,
            limit=body.limit,
            max_suggestions=body.max_suggestions,
        )
    except PlanningError as exc:
        raise HTTPException(exc.status, detail=exc.public()) from None
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, detail={
            'code':'INVALID_ALTERNATIVE_CONTEXT',
            'message':'当前方案与超限路段无法对应到可替换停留点；原行程未修改。',
            'itinerary_modified':False,
        }) from exc


@router.post('/clear-memory')
def clear_memory(body: ClearRequest):
    cache.clear_session(body.session_id)
    return {'cleared':True,'scope':'this browser session only','disk_files_modified':False}
)
    revision: int = Field(default=0, ge=0)
    anchor: __import__('app.stage2b.models',fromlist=['PlacePoint']).PlacePoint
    mode: str = Field(default='walk', pattern=r'^(walk|drive|cycle)
    plan_request: PlanRequest
    current_plan: dict
    leg_id: str = Field(pattern=r'^leg-[1-9][0-9]*$')
    radius_m: float = Field(default=1000, gt=0, le=5000)
    category: str = Field(default='', max_length=40)
    limit: int = Field(default=20, ge=1, le=100)
    max_suggestions: int = Field(default=5, ge=1, le=20)


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
            disconnected=request.is_disconnected,
            search_point_matcher=catalogue.match_search_point)
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


@router.post('/alternatives')
async def alternatives(body: AlternativeRequest, request: Request):
    """Suggest feasible nearby replacements; never modifies the submitted itinerary."""
    if body.plan_request.mode != 'walk' or body.plan_request.max_walk_minutes is None:
        raise HTTPException(422, detail={
            'code':'WALKING_CONSTRAINT_REQUIRED',
            'message':'替代建议当前只处理已设置最大步行时间的步行方案。',
            'itinerary_modified':False,
        })

    violations = (body.current_plan.get('walking_constraint_check') or {}).get('violations') or []
    if not any(v.get('leg_id') == body.leg_id for v in violations):
        raise HTTPException(422, detail={
            'code':'LEG_NOT_REPORTED_AS_VIOLATION',
            'message':'指定路段不在当前方案的步行超限结果中；未生成替代建议。',
            'itinerary_modified':False,
        })

    async def recalculate(trial):
        return await calculate(
            trial, geo.client, cache,
            resolver=lambda uid,build: catalogue.entity(uid,build),
            disconnected=request.is_disconnected,
            search_point_matcher=catalogue.match_search_point,
        )

    try:
        return await suggest_alternatives(
            request=body.plan_request,
            current_plan=body.current_plan,
            leg_id=body.leg_id,
            nearby=catalogue.nearby,
            calculate_plan=recalculate,
            radius_m=body.radius_m,
            category=body.category,
            limit=body.limit,
            max_suggestions=body.max_suggestions,
        )
    except PlanningError as exc:
        raise HTTPException(exc.status, detail=exc.public()) from None
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, detail={
            'code':'INVALID_ALTERNATIVE_CONTEXT',
            'message':'当前方案与超限路段无法对应到可替换停留点；原行程未修改。',
            'itinerary_modified':False,
        }) from exc


@router.post('/clear-memory')
def clear_memory(body: ClearRequest):
    cache.clear_session(body.session_id)
    return {'cleared':True,'scope':'this browser session only','disk_files_modified':False}
)
    radius_m: float = Field(default=1000, gt=0, le=5000)
    category: str = Field(default='', max_length=40)
    q: str = Field(default='', max_length=160)
    limit: int = Field(default=20, ge=1, le=100)
    max_suggestions: int = Field(default=5, ge=1, le=20)
    max_travel_minutes: int | None = Field(default=None, gt=0, le=1440)
    force_refresh: bool = False


class AlternativeRequest(Contract):
    plan_request: PlanRequest
    current_plan: dict
    leg_id: str = Field(pattern=r'^leg-[1-9][0-9]*$')
    radius_m: float = Field(default=1000, gt=0, le=5000)
    category: str = Field(default='', max_length=40)
    limit: int = Field(default=20, ge=1, le=100)
    max_suggestions: int = Field(default=5, ge=1, le=20)


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
            disconnected=request.is_disconnected,
            search_point_matcher=catalogue.match_search_point)
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


@router.post('/alternatives')
async def alternatives(body: AlternativeRequest, request: Request):
    """Suggest feasible nearby replacements; never modifies the submitted itinerary."""
    if body.plan_request.mode != 'walk' or body.plan_request.max_walk_minutes is None:
        raise HTTPException(422, detail={
            'code':'WALKING_CONSTRAINT_REQUIRED',
            'message':'替代建议当前只处理已设置最大步行时间的步行方案。',
            'itinerary_modified':False,
        })

    violations = (body.current_plan.get('walking_constraint_check') or {}).get('violations') or []
    if not any(v.get('leg_id') == body.leg_id for v in violations):
        raise HTTPException(422, detail={
            'code':'LEG_NOT_REPORTED_AS_VIOLATION',
            'message':'指定路段不在当前方案的步行超限结果中；未生成替代建议。',
            'itinerary_modified':False,
        })

    async def recalculate(trial):
        return await calculate(
            trial, geo.client, cache,
            resolver=lambda uid,build: catalogue.entity(uid,build),
            disconnected=request.is_disconnected,
            search_point_matcher=catalogue.match_search_point,
        )

    try:
        return await suggest_alternatives(
            request=body.plan_request,
            current_plan=body.current_plan,
            leg_id=body.leg_id,
            nearby=catalogue.nearby,
            calculate_plan=recalculate,
            radius_m=body.radius_m,
            category=body.category,
            limit=body.limit,
            max_suggestions=body.max_suggestions,
        )
    except PlanningError as exc:
        raise HTTPException(exc.status, detail=exc.public()) from None
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, detail={
            'code':'INVALID_ALTERNATIVE_CONTEXT',
            'message':'当前方案与超限路段无法对应到可替换停留点；原行程未修改。',
            'itinerary_modified':False,
        }) from exc


@router.post('/clear-memory')
def clear_memory(body: ClearRequest):
    cache.clear_session(body.session_id)
    return {'cleared':True,'scope':'this browser session only','disk_files_modified':False}
