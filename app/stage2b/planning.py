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
from app.english import english_name, english_view
from .spatial_constraints import validate_max_walking_time
from .opening_hours import evaluate_visit_hours
from .feasibility import evaluate_feasibility

# Explicit, editable project assumptions. NOT empirical visitor-duration statistics.
BASE_STAYS = {
    'nature': (30, 60, 120), 'tourism': (30, 60, 90),
    'heritage': (10, 20, 40), 'monument': (15, 30, 60),
    'food': (20, 40, 60), 'unknown': (15, 30, 60),
}
PROFILE_FACTORS = {'takeaway':0.5, 'quick':0.5, 'regular':1.0, 'extended':1.5}

class PlanningError(Exception):
    def __init__(self, code, message, status=422, **extra):
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra
    def public(self):
        return {'code':self.code, 'message':self.message, **self.extra,
                'complete_plan_created':False, 'synthetic_fallback_used':False}


def resolve_point(point: PlacePoint, resolver: Callable | None, search_point_matcher: Callable | None = None):
    if point.source != 'catalogue_representative':
        if point.source == 'onemap_search' and resolver is not None and search_point_matcher is not None:
            match = search_point_matcher(point.label, point.latitude, point.longitude)
            if match:
                result = resolver(match['entity_id'], match['build_id'])
                entity = result['entity']
                return {'point':point, 'category':entity.get('category') or 'unknown',
                        'suggested_duration':entity.get('suggested_duration'),
                        'regular_hours_evidence':entity.get('regular_hours_evidence', []),
                        'opening_hours_display':entity.get('opening_hours_display'),
                        'entrance_points':entity.get('entrance_points') or [],
                        'source_checked':True, 'entrance_verified':False,
                        'catalogue_metadata_match':match}
        return {'point':point, 'category':'unknown', 'regular_hours_evidence':[], 'opening_hours_display':None,
                'entrance_points':[], 'source_checked':False, 'entrance_verified':False}
    if resolver is None:
        raise PlanningError('CATALOGUE_UNAVAILABLE', 'The nationwide catalogue cannot be read. Restore it or confirm a map point or search result.', 409)
    result = resolver(point.entity_id, point.catalogue_build_id)
    entity = result['entity']
    if not entity['default_exploration_visible']:
        raise PlanningError('CATALOGUE_PLACE_ON_HOLD', 'This record refers to a former location or has an unresolved status. Check the place and select its current access location before adding it.', 409)
    if distance_m(point.latitude, point.longitude, entity['latitude'], entity['longitude']) > 1:
        raise PlanningError('CATALOGUE_POINT_MISMATCH', 'The representative point does not match the reviewed catalogue. Select it again; the coordinates were not changed automatically.', 409)
    # Source coordinates are authoritative for this representative-point choice.
    normalized = point.model_copy(update={'latitude':entity['latitude'], 'longitude':entity['longitude'],
                                          'label':english_name(entity['display_title'])[:200]})
    return {'point':normalized, 'category':entity.get('category') or 'unknown',
            'suggested_duration':entity.get('suggested_duration'),
            'regular_hours_evidence':entity.get('regular_hours_evidence', []),
            'opening_hours_display':entity.get('opening_hours_display'),
            'entrance_points':entity.get('entrance_points') or [],
            'source_checked':True, 'entrance_verified':False}


def prepare(request, resolver=None, search_point_matcher=None):
    origin = resolve_point(request.origin, resolver, search_point_matcher)
    finish = resolve_point(request.finish, resolver, search_point_matcher) if request.finish else None
    visits = []
    for v in request.visits:
        place = resolve_point(v.point, resolver, search_point_matcher)
        reviewed = place.get('suggested_duration')
        if v.stay_minutes is None and v.stay_profile == 'takeaway' and reviewed and reviewed.get('takeaway'):
            # Just buying something: short fixed time, no range.
            lo = ref = hi = reviewed['takeaway']
            basis = 'place_takeaway'
        elif v.stay_minutes is None and reviewed:
            # Place-specific short / regular / long: reviewed core attraction, or detailed-category default.
            lo, hi = reviewed['short'], reviewed['long']
            # takeaway without a takeaway time (e.g. a museum) falls back to the short value
            ref = {'takeaway': lo, 'quick': lo, 'regular': reviewed['regular'], 'extended': hi}[v.stay_profile]
            basis = 'place_' + reviewed.get('basis', 'estimate')
        elif v.stay_minutes is None:
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
            'stay_note':'User-set duration; its adequacy has not been independently verified.' if basis == 'user_specified' else ('A short, editable stop for takeaway or a quick purchase.' if basis == 'place_takeaway' else 'An editable initial estimate for this place category, not a measured visit duration.' if basis == 'place_category_default' else ('Suggested visit duration; you can change it.' if basis.startswith('place_') else 'An editable category or general estimate, not a measured visit duration or confidence interval.')),
            'entrance_verified':False, 'entrance_candidates':place.get('entrance_points') or [],
            'selected_entrance':None, 'routing_point':place['point'].model_dump(),
            'entrance_selection_basis':'representative_point_no_candidate',
            'opening_status':'not_checked',
            'opening_hours_display':place.get('opening_hours_display'),
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
        raise PlanningError('INVALID_SEGMENT_RESPONSE','A route segment failed validation of its travel mode, geometry or time. No zero value or straight-line substitute was used.',502) from None


def _entrance_point(candidate, visit_label):
    try:
        lat, lon = candidate['latitude'], candidate['longitude']
        if isinstance(lat,bool) or isinstance(lon,bool) or not isinstance(lat,(int,float)) or not isinstance(lon,(int,float)):
            return None
        label=(candidate.get('name') or (visit_label+' suggested access point'))[:200]
        return PlacePoint(latitude=lat, longitude=lon, label=label, source='user_map', confirmed=True)
    except (KeyError, TypeError, ValueError):
        return None


async def _select_visit_entrances(request, prepared, client, cache, generation, disconnected=None):
    """Choose arrival access points conservatively.

    One candidate is used directly. With multiple candidates, choose the reachable
    candidate with the shortest provider-reported travel time from the previous
    routing point. The same selected point is also used when leaving the visit;
    no internal park traversal is invented.
    """
    previous=prepared['origin']
    selection_calls=0
    for visit in prepared['visits']:
        candidates=[p for p in (_entrance_point(x,visit['point']['label']) for x in visit.get('entrance_candidates',[])) if p]
        if not candidates:
            chosen=PlacePoint.model_validate(visit['point'])
            basis='representative_point_no_candidate'
        elif len(candidates)==1:
            chosen=candidates[0]
            basis='single_entrance_candidate'
        else:
            ranked=[]
            for candidate in candidates:
                if disconnected is not None and await disconnected():
                    raise PlanningError('CLIENT_CANCELLED','The request was cancelled. No further entrance candidates were compared.',409)
                if distance_m(previous.latitude,previous.longitude,candidate.latitude,candidate.longitude)<1:
                    ranked.append((0.0,candidate));continue
                key=cache.key(request.session_id,request.mode,previous,candidate)
                cached=None if request.force_refresh else cache.get(key)
                if cached is not None:
                    route,_age=cached
                else:
                    try:
                        selection_calls+=1
                        rr=RouteRequest(origin=previous.route_location(),destination=candidate.route_location(),
                            mode=request.mode,revision=request.revision)
                        route=await run_in_threadpool(client.route,rr)
                        validate_route(route,request.mode)
                        cache.put(key,route,expected_generation=generation)
                    except ProviderError:
                        continue
                try:
                    validate_route(route,request.mode)
                    ranked.append((route['duration_s'],candidate))
                except PlanningError:
                    continue
            if ranked:
                ranked.sort(key=lambda x:x[0])
                chosen=ranked[0][1]
                basis='fastest_reachable_entrance_candidate'
            else:
                chosen=PlacePoint.model_validate(visit['point'])
                basis='representative_point_all_candidates_unreachable'
        visit['routing_point']=chosen.model_dump()
        visit['selected_entrance']=chosen.model_dump() if basis!='representative_point_no_candidate' and not basis.startswith('representative_point_all') else None
        visit['entrance_selection_basis']=basis
        previous=chosen
    points=[prepared['origin']]+[PlacePoint.model_validate(v['routing_point']) for v in prepared['visits']]
    if request.finish_policy=='return_to_start': points.append(prepared['origin'])
    elif prepared['finish']: points.append(prepared['finish'])
    prepared['points']=points
    return selection_calls


async def calculate(request: PlanRequest, client, cache, resolver=None, disconnected=None, search_point_matcher=None):
    prepared = prepare(request, resolver, search_point_matcher)
    generation = cache.generation(request.session_id)
    entrance_provider_calls = await _select_visit_entrances(request, prepared, client, cache, generation, disconnected)
    legs, cache_hits, provider_calls = [], 0, entrance_provider_calls
    for index, (a,b) in enumerate(zip(prepared['points'], prepared['points'][1:])):
        if disconnected is not None and await disconnected():
            raise PlanningError('CLIENT_CANCELLED','The request was cancelled. No further route segments were requested.',409)
        exact_same = a.latitude == b.latitude and a.longitude == b.longitude
        if exact_same:
            route = {'provider':None, 'mode':request.mode, 'queried_at':None,
                'distance_m':0.0, 'duration_s':0.0, 'geometry':None,
                'endpoint_offsets_m':{'origin':0.0,'destination':0.0}, 'endpoint_review_required':False}
            reuse, age = False, None
            kind = 'same_selected_coordinate'
        else:
            if distance_m(a.latitude,a.longitude,b.latitude,b.longitude) < 1:
                raise PlanningError('ENDPOINTS_TOO_CLOSE','Adjacent points are less than 1 metre apart but are not identical. Check or merge the points; access is not assumed.',422,failed_leg_index=index,completed_leg_count=len(legs))
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
                    raise PlanningError(exc.code, f'Leg {index+1} failed: '+exc.message, exc.status,
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
        raise PlanningError('CLIENT_CANCELLED','The request was cancelled. Its result was not applied as a new plan.',409)
    return assemble(request, prepared, legs, cache_hits, provider_calls, cache.ttl_seconds)


def assemble(request, prepared, legs, cache_hits=0, provider_calls=0, ttl_seconds=300):
    visits = deepcopy(prepared['visits'])
    if len(legs) != len(prepared['points'])-1:
        raise PlanningError('INCOMPLETE_ROUTE','Some route segments are missing. A complete itinerary cannot be calculated.',502)
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
    if request.max_walk_minutes is not None and request.mode == "walk":
        walking_check = validate_max_walking_time(
            legs=legs,
            max_minutes=request.max_walk_minutes
        )
    else:
        walking_check = {
            "status": "not_requested",
            "violations": []
        }
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
            hours_check = evaluate_visit_hours(
                arrival_at=visit['arrival_at'], departure_at=visit['departure_at'],
                opening_hours_display=visit.get('opening_hours_display'))
            visit['opening_hours_check'] = hours_check
            visit['opening_status'] = hours_check['status']
            timeline.append({'kind':'visit','visit_id':visit['visit_id'],'visit_index':index,
                'start_offset_s':cursor,'end_offset_s':end,'start_at':stamp(cursor),'end_at':stamp(end),
                'duration_s':end-cursor,'title':visit['point']['label']})
            cursor = end
    if buffer:
        timeline.append({'kind':'buffer','start_offset_s':cursor,'end_offset_s':cursor+buffer,
            'start_at':stamp(cursor),'end_at':stamp(cursor+buffer),'duration_s':buffer,
            'title':'General buffer (not assigned to a leg; not measured queue time)'})
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
        'Route segments follow your chosen order. This calculation does not recommend places, optimise the order or call an LLM.',
        'Travel times use current or briefly cached provider results. The departure time sets the timeline; it does not predict future traffic.',
        'Static opening-hour checks use catalogue schedules that can be parsed. Complex schedules, last admission, temporary closures and tickets still need checking.',
        'Endpoint gaps, movement inside venues, parking and connecting walks are not timed separately. Check the locations and add any necessary buffer.',
        'The range reflects only the selected visit-duration assumptions. It excludes traffic variability and is not a statistical confidence interval.',
    ]
    if any(l['endpoint_review_required'] for l in legs): cautions.append('A route endpoint is over 50 metres from its selected point. Check the access location; no connecting line or missing travel time was added.')
    if any(l['diagnostics']['large_detour_flag'] for l in legs): cautions.append('A route segment has a large detour. The original provider route and travel mode are retained; the route is not proven shortest.')
    if any(l['diagnostics']['geometry_summary_mismatch'] for l in legs): cautions.append('A reported segment distance differs substantially from its geometry length. Check the result; the reported distance has not been altered.')
    if any(l['kind']=='same_selected_coordinate' for l in legs): cautions.append('No route was requested between identical selected coordinates, so transfer time is zero. This does not imply instant travel between different entrances inside a place.')
    if gaps: cautions.append('Adjacent provider routes may not connect near a stop. The gaps and uncounted transfer time are shown.')
    # A device reading is a measurement with its own error. It is reported, never
    # used to correct a coordinate, and never folded into a duration estimate.
    device=[p for p in (prepared['origin'],prepared['finish']) if p is not None and p.source=='device_location']
    radius=max((p.accuracy_m for p in device if p.accuracy_m is not None),default=None)
    if device:
        cautions.append('The start or end point uses device location'+
            (', with device-reported accuracy of approximately ±'+str(round(radius))+' m' if radius is not None else ', with no reported accuracy radius')+
            '. This uncertainty is not included in distance or time estimates, and the reading does not verify your current location.')
    budget_check = {'status':status,'budget_s':budget,
        'reference_within_budget':None if budget is None else total<=budget,
        'slack_s':None if budget is None else budget-total,
        'range_within_budget':None if budget is None else high<=budget}
    feasibility_check = evaluate_feasibility(visits=visits, budget_check=budget_check,
        walking_constraint_check=walking_check)
    # The checks above used the original source schedules. Translate only the
    # result displayed to the user, never the evidence fed into those checks.
    return english_view({'stage':'2B','schema_version':1,'revision':request.revision,
        'generated_at':datetime.now(timezone.utc).isoformat(),'scope':'Singapore nationwide',
        'origin':prepared['origin'].model_dump(),'visits':visits,
        'finish_policy':request.finish_policy,'finish':prepared['finish'].model_dump() if prepared['finish'] else None,
        'mode':request.mode,'time':request.time.model_dump(mode='json'),
        'legs':legs,'timeline':timeline,'time_axis_timezone':'Asia/Singapore',
        'totals':{'distance_m':math.fsum(l['distance_m'] for l in legs), 'travel_s':travel,
            'stay_s':stay,'buffer_s':buffer,'reference_duration_s':total,'duration_range_s':[low,high],
            'estimated_end_at':stamp(total),'estimated_end_range_at':[stamp(low),stamp(high)]},
        'budget_check':budget_check,
        'walking_constraint_check': walking_check,
        'device_location_used':bool(device),'device_location_accuracy_m':radius,
        'device_accuracy_included_in_estimates':False,
        'schedule_status':'arithmetic_estimate_only',
        'opening_hours_checked':bool(start) and any(v.get('opening_hours_check',{}).get('status')!='unknown' for v in visits),
        'opening_hours_summary':{
            'feasible':sum(v.get('opening_status')=='feasible' for v in visits),
            'closed_at_arrival':sum(v.get('opening_status')=='closed_at_arrival' for v in visits),
            'insufficient_time':sum(v.get('opening_status')=='insufficient_time' for v in visits),
            'unknown':sum(v.get('opening_status')=='unknown' for v in visits),
        },
        'entrances_verified':False,
        'itinerary_feasible':feasibility_check['feasible'],
        'feasibility_check':feasibility_check,'route_order_optimised':False,
        'live_llm':False,'complete_plan_created':True,'all_legs_accounted_for':True,
        'joins':gaps,'route_calls':{'requested_segments':len(legs),'provider_calls':provider_calls,
            'cache_hits':cache_hits,'cache_ttl_seconds':ttl_seconds,
            'cache_is_session_isolated_memory_only':True,'force_refresh_requested':request.force_refresh},
        'source_catalogues_modified':False,'private_queries_saved_to_disk':False,
        'synthetic_fallback_used':False,'cautions':cautions})
