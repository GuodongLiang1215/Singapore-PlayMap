"""User-ordered route aggregation. No search over POIs, route optimisation or hidden deadlines."""
from __future__ import annotations
import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Callable
from fastapi.concurrency import run_in_threadpool
from app.stage2a.models import RouteRequest, distance_m
from app.stage2a.transport import ProviderError
from app.stage2b.models import PlanRequest, PlacePoint

# Explicit, editable project assumptions. NOT empirical visitor-duration statistics.
BASE_STAYS = {
    'nature': (30, 60, 120), 'tourism': (30, 60, 90),
    'heritage': (10, 20, 40), 'monument': (15, 30, 60),
    'food': (20, 40, 60), 'unknown': (15, 30, 60),
}
PROFILE_FACTORS = {'quick':0.5, 'regular':1.0, 'extended':1.5}

class PlanningError(Exception):
    def __init__(self, code, message, status=422, **extra):
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra
    def public(self):
        return {'code':self.code, 'message':self.message, **self.extra,
                'complete_plan_created':False, 'synthetic_fallback_used':False}


def resolve_point(point: PlacePoint, resolver: Callable | None):
    if point.source != 'catalogue_representative':
        return {'point':point, 'category':'unknown', 'regular_hours_evidence':[],
                'source_checked':False, 'entrance_verified':False}
    if resolver is None:
        raise PlanningError('CATALOGUE_UNAVAILABLE', '全岛目录不可读，请恢复目录，或自行确认地图／搜索位置。', 409)
    result = resolver(point.entity_id, point.catalogue_build_id)
    entity = result['entity']
    if not entity['default_exploration_visible']:
        raise PlanningError('CATALOGUE_PLACE_ON_HOLD', '此来源记录为旧址或状态待核查，不能直接按旧代表点加入。请核查并选择实际通行位置。', 409)
    if distance_m(point.latitude, point.longitude, entity['latitude'], entity['longitude']) > 1:
        raise PlanningError('CATALOGUE_POINT_MISMATCH', '代表点与已核查目录不一致，请重新选择；未自行改动坐标。', 409)
    # Source coordinates are authoritative for this representative-point choice.
    normalized = point.model_copy(update={'latitude':entity['latitude'], 'longitude':entity['longitude'],
                                          'label':entity['display_title'][:200]})
    return {'point':normalized, 'category':entity.get('category') or 'unknown',
            'regular_hours_evidence':entity.get('regular_hours_evidence', []),
            'source_checked':True, 'entrance_verified':False}


def prepare(request, resolver=None):
    origin = resolve_point(request.origin, resolver)
    finish = resolve_point(request.finish, resolver) if request.finish else None
    visits = []
    for v in request.visits:
        place = resolve_point(v.point, resolver)
        if v.stay_minutes is None:
            base = BASE_STAYS.get(place['category'], BASE_STAYS['unknown'])
            factor = PROFILE_FACTORS[v.stay_profile]
            lo, ref, hi = [int(x * factor) for x in base]
            basis = 'project_category_assumption' if place['category'] in BASE_STAYS and place['category'] != 'unknown' else 'project_generic_assumption'
        else:
            lo = ref = hi = v.stay_minutes
            basis = 'user_specified'
        visits.append({'visit_id':v.visit_id, 'point':place['point'].model_dump(),
            'category':place['category'], 'stay_minutes':ref, 'stay_range_minutes':[lo,hi],
            'stay_basis':basis, 'stay_profile':v.stay_profile,
            'stay_note':'用户设定时长，未独立验证是否足够。' if basis == 'user_specified' else '类别／通用初始假设，可修改；不是实测游览时长或置信区间。',
            'entrance_verified':False, 'opening_status':'not_checked',
            'regular_hours_evidence_count':len(place['regular_hours_evidence']),
            'regular_hours_evidence':place['regular_hours_evidence']})
    points = [origin['point']] + [PlacePoint.model_validate(v['point']) for v in visits]
    if request.finish_policy == 'return_to_start': points.append(origin['point'])
    elif finish: points.append(finish['point'])
    return {'origin':origin['point'], 'finish':finish['point'] if finish else None,
            'visits':visits, 'points':points}


def route_diagnostics(route, a, b):
    direct = distance_m(a.latitude,a.longitude,b.latitude,b.longitude)
    ratio = route['distance_m'] / direct if direct >= 1 else None
    coords = route['geometry']['coordinates'] if route.get('geometry') else []
    geom_m = math.fsum(distance_m(p[1],p[0],q[1],q[0]) for p,q in zip(coords,coords[1:]))
    delta = abs(geom_m - route['distance_m'])
    return {'straight_line_m':round(direct,2), 'distance_to_straight_ratio':round(ratio,2) if ratio is not None else None,
            'large_detour_flag':direct >= 300 and route['distance_m'] >= 3000 and ratio is not None and ratio >= 4,
            'geometry_summary_mismatch':delta > 300 and delta/max(geom_m,route['distance_m'],1) > .35,
            'thresholds_are_project_heuristics':True, 'shortest_route_proven':False}


def validate_route(route, mode):
    try:
        if route['mode'] != mode: raise ValueError()
        for key in ('distance_m','duration_s'):
            if isinstance(route[key],bool) or not isinstance(route[key],(int,float)) or not math.isfinite(route[key]) or route[key] <= 0: raise ValueError()
        g = route['geometry']
        if g['type'] != 'LineString' or len(g['coordinates']) < 2: raise ValueError()
        for p in g['coordinates']:
            if len(p) != 2 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in p): raise ValueError()
        for key in ('origin','destination'):
            x = route['endpoint_offsets_m'][key]
            if not isinstance(x,(int,float)) or isinstance(x,bool) or not math.isfinite(x) or x < 0: raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise PlanningError('INVALID_SEGMENT_RESPONSE','一段路径的模式、线形或时间未通过检查，没有用零或直线代替。',502) from None


async def calculate(request: PlanRequest, client, cache, resolver=None, disconnected=None):
    prepared = prepare(request, resolver)
    generation = cache.generation(request.session_id)
    legs, cache_hits, provider_calls = [], 0, 0
    for index, (a,b) in enumerate(zip(prepared['points'], prepared['points'][1:])):
        if disconnected is not None and await disconnected():
            raise PlanningError('CLIENT_CANCELLED','本次页面请求已取消，未继续发出后续路段请求。',409)
        exact_same = a.latitude == b.latitude and a.longitude == b.longitude
        if exact_same:
            route = {'provider':None, 'mode':request.mode, 'queried_at':None,
                'distance_m':0.0, 'duration_s':0.0, 'geometry':None,
                'endpoint_offsets_m':{'origin':0.0,'destination':0.0}, 'endpoint_review_required':False}
            reuse, age = False, None
            kind = 'same_selected_coordinate'
        else:
            if distance_m(a.latitude,a.longitude,b.latitude,b.longitude) < 1:
                raise PlanningError('ENDPOINTS_TOO_CLOSE','相邻位置不足1米但并非完全相同；请核对或合并选点，不会自动假定可通行。',422,failed_leg_index=index,completed_leg_count=len(legs))
            key = cache.key(request.session_id,request.mode,a,b)
            cached = None if request.force_refresh else cache.get(key)
            if cached is not None:
                route, age = cached; reuse = True; cache_hits += 1
            else:
                try:
                    provider_calls += 1
                    rr = RouteRequest(origin=a.route_location(),destination=b.route_location(),mode=request.mode,revision=request.revision)
                    route = await run_in_threadpool(client.route, rr)
                except ProviderError as exc:
                    raise PlanningError(exc.code, f'第{index+1}段请求失败：'+exc.message, exc.status,
                        failed_leg_index=index, completed_leg_count=len(legs)) from None
                validate_route(route,request.mode)
                cache.put(key,route,expected_generation=generation)
                reuse, age = False, 0.0
            validate_route(route,request.mode)
            kind = 'provider_route'
        legs.append({'leg_id':f'leg-{index+1}', 'index':index, 'kind':kind,
            'origin':a.model_dump(), 'destination':b.model_dump(),
            'provider':route['provider'], 'mode':request.mode, 'queried_at':route.get('queried_at'),
            'distance_m':route['distance_m'], 'duration_s':route['duration_s'],
            'geometry':deepcopy(route['geometry']), 'endpoint_offsets_m':route['endpoint_offsets_m'],
            'endpoint_review_required':route['endpoint_review_required'],
            'diagnostics':route_diagnostics(route,a,b), 'reused_in_memory':reuse,
            'cache_age_s':round(age,2) if age is not None else None})
    if disconnected is not None and await disconnected():
        raise PlanningError('CLIENT_CANCELLED','本次页面请求已取消，结果未交付为新方案。',409)
    return assemble(request, prepared, legs, cache_hits, provider_calls, cache.ttl_seconds)


def assemble(request, prepared, legs, cache_hits=0, provider_calls=0, ttl_seconds=300):
    visits = deepcopy(prepared['visits'])
    if len(legs) != len(prepared['points'])-1:
        raise PlanningError('INCOMPLETE_ROUTE','路段数不完整，不能计算完整行程。',502)
    travel = math.fsum(l['duration_s'] for l in legs)
    stay = sum(v['stay_minutes'] for v in visits)*60
    low_stay = sum(v['stay_range_minutes'][0] for v in visits)*60
    high_stay = sum(v['stay_range_minutes'][1] for v in visits)*60
    buffer = request.time.buffer_minutes*60
    total = travel+stay+buffer
    low, high = travel+low_stay+buffer, travel+high_stay+buffer
    budget = request.time.available_seconds()
    if budget is None: status='no_budget'
    elif low > budget: status='all_estimates_exceed'
    elif total > budget: status='reference_exceeds'
    elif high > budget: status='reference_fits_upper_exceeds'
    else: status='all_estimates_fit'
    start = request.time.departure_at
    def stamp(offset): return (start+timedelta(seconds=offset)).isoformat() if start else None
    cursor, timeline = 0.0, []
    for index, leg in enumerate(legs):
        end = cursor+leg['duration_s']
        timeline.append({'kind':'travel','leg_index':index,'start_offset_s':cursor,'end_offset_s':end,
            'start_at':stamp(cursor),'end_at':stamp(end),'duration_s':leg['duration_s'],
            'title':leg['origin']['label']+' → '+leg['destination']['label']})
        cursor = end
        if index < len(visits):
            visit = visits[index]
            end = cursor+visit['stay_minutes']*60
            visit['arrival_offset_s'], visit['departure_offset_s'] = cursor, end
            visit['arrival_at'], visit['departure_at'] = stamp(cursor), stamp(end)
            timeline.append({'kind':'visit','visit_id':visit['visit_id'],'visit_index':index,
                'start_offset_s':cursor,'end_offset_s':end,'start_at':stamp(cursor),'end_at':stamp(end),
                'duration_s':end-cursor,'title':visit['point']['label']})
            cursor = end
    if buffer:
        timeline.append({'kind':'buffer','start_offset_s':cursor,'end_offset_s':cursor+buffer,
            'start_at':stamp(cursor),'end_at':stamp(cursor+buffer),'duration_s':buffer,
            'title':'整体预留（未指定给某一段；不是实测排队时间）'})
        cursor += buffer
    gaps=[]
    for index in range(len(legs)-1):
        first, second = legs[index].get('geometry'), legs[index+1].get('geometry')
        if first and second:
            a, b = first['coordinates'][-1], second['coordinates'][0]
            gap=distance_m(a[1],a[0],b[1],b[0])
            if gap>1:
                gaps.append({'visit_index':index,'gap_m':round(gap,2),'large_gap_warning':gap>50,
                    'transfer_time_included':False,'connector_drawn':False})
    cautions=[
        '按用户指定顺序逐段计算；没有自动推荐地点、优化顺序或调用LLM。',
        '通行时间沿用本次／短时复用的服务商结果；出发时刻仅用于排时间轴，不是未来时段交通预测。',
        '未核验入口、开放时段、最后入场、票务或实时关闭；时间预算通过不等于整趟行程现实可行。',
        '路线端点到所选点、场馆内部移动、驾车停车与步行接驳未单独计时；请核对位置与必要预留。',
        '建议范围仅反映设定的停留时长范围；不包含交通随机性，不是统计置信区间。',
    ]
    if any(l['endpoint_review_required'] for l in legs): cautions.append('有路段端点偏移超过50米，请核对实际通行点；没有补画连接线或填补缺失时间。')
    if any(l['diagnostics']['large_detour_flag'] for l in legs): cautions.append('有路段明显绕行，仍保留服务商原路线；没有换交通方式或认定其为最短。')
    if any(l['diagnostics']['geometry_summary_mismatch'] for l in legs): cautions.append('有路段距离摘要与线形长度差异较大，需要检查；摘要未被静默改写。')
    if any(l['kind']=='same_selected_coordinate' for l in legs): cautions.append('完全相同的选定坐标之间未请求路线，转移时间按0计算；不证明内部不同入口可瞬时到达。')
    if gaps: cautions.append('相邻两段服务商路线在停留点附近可能不衔接；差距及未计入接驳时间已列出。')
    # A device reading is a measurement with its own error. It is reported, never
    # used to correct a coordinate, and never folded into a duration estimate.
    device=[p for p in (prepared['origin'],prepared['finish']) if p is not None and p.source=='device_location']
    radius=max((p.accuracy_m for p in device if p.accuracy_m is not None),default=None)
    if device:
        cautions.append('出发点或结束点来自设备定位'+
            ('，设备自报误差约 ±'+str(round(radius))+' 米' if radius is not None else '，设备未提供误差半径')+
            '；该误差没有计入任何距离或时间估计，也不证明你此刻确实位于该点。')
    return {'stage':'2B','schema_version':1,'revision':request.revision,
        'generated_at':datetime.now(timezone.utc).isoformat(),'scope':'Singapore nationwide',
        'origin':prepared['origin'].model_dump(),'visits':visits,
        'finish_policy':request.finish_policy,'finish':prepared['finish'].model_dump() if prepared['finish'] else None,
        'mode':request.mode,'time':request.time.model_dump(mode='json'),
        'legs':legs,'timeline':timeline,'time_axis_timezone':'Asia/Singapore',
        'totals':{'distance_m':math.fsum(l['distance_m'] for l in legs), 'travel_s':travel,
            'stay_s':stay,'buffer_s':buffer,'reference_duration_s':total,'duration_range_s':[low,high],
            'estimated_end_at':stamp(total),'estimated_end_range_at':[stamp(low),stamp(high)]},
        'budget_check':{'status':status,'budget_s':budget,'reference_within_budget':None if budget is None else total<=budget,
            'slack_s':None if budget is None else budget-total,
            'range_within_budget':None if budget is None else high<=budget},
        'device_location_used':bool(device),'device_location_accuracy_m':radius,
        'device_accuracy_included_in_estimates':False,
        'schedule_status':'arithmetic_estimate_only','opening_hours_checked':False,
        'entrances_verified':False,'itinerary_feasible':None,'route_order_optimised':False,
        'live_llm':False,'complete_plan_created':True,'all_legs_accounted_for':True,
        'joins':gaps,'route_calls':{'requested_segments':len(legs),'provider_calls':provider_calls,
            'cache_hits':cache_hits,'cache_ttl_seconds':ttl_seconds,
            'cache_is_session_isolated_memory_only':True,'force_refresh_requested':request.force_refresh},
        'source_catalogues_modified':False,'private_queries_saved_to_disk':False,
        'synthetic_fallback_used':False,'cautions':cautions}
