"""Independent spatial recommendations: catalogue nearby candidates verified by real provider routes."""
from __future__ import annotations
from typing import Callable, Awaitable
from app.stage2b.models import PlacePoint
from app.stage2a.models import RouteRequest
from app.stage2a.transport import ProviderError
from app.stage2b.planning import validate_route
from starlette.concurrency import run_in_threadpool

NearbyProvider = Callable[..., dict]


def _candidate_point(candidate: dict, build_id: str) -> PlacePoint:
    return PlacePoint(
        latitude=candidate["latitude"], longitude=candidate["longitude"],
        label=candidate["display_title"], source="catalogue_representative",
        entity_id=candidate["entity_id"], catalogue_build_id=build_id, confirmed=True,
    )


async def recommend_nearby(*, anchor: PlacePoint, session_id: str, revision: int, mode: str,
                           nearby: NearbyProvider, client, cache, radius_m: float=1000,
                           category: str="", q: str="", limit: int=20,
                           max_suggestions: int=5, max_travel_minutes: int|None=None,
                           force_refresh: bool=False, disconnected=None) -> dict:
    """Rank nearby catalogue places by provider-reported travel time.

    Straight-line distance is candidate generation only. A suggestion is returned
    only after a valid provider route from the anchor has been obtained.
    """
    raw=nearby(lat=anchor.latitude,lon=anchor.longitude,radius_m=radius_m,
               category=category,q=q,limit=limit)
    build_id=raw.get("build_id")
    evaluated=[]
    generation=cache.generation(session_id)
    provider_calls=cache_hits=0

    for candidate in raw.get("places") or []:
        if disconnected is not None and await disconnected():
            break
        try:
            point=_candidate_point(candidate,build_id)
            key=cache.key(session_id,mode,anchor,point)
            cached=None if force_refresh else cache.get(key)
            if cached is not None:
                route,age=cached; cache_hits+=1
            else:
                provider_calls+=1
                rr=RouteRequest(origin=anchor.route_location(),destination=point.route_location(),
                                mode=mode,revision=revision)
                route=await run_in_threadpool(client.route,rr)
                validate_route(route,mode)
                cache.put(key,route,expected_generation=generation)
                age=0.0
            validate_route(route,mode)
            minutes=route["duration_s"]/60
            within=max_travel_minutes is None or minutes<=max_travel_minutes
            evaluated.append({
                "entity_id":candidate.get("entity_id"),"label":candidate.get("display_title"),
                "category":candidate.get("category"),
                "straight_line_distance_m":candidate.get("distance_m"),
                "route_duration_min":round(minutes,2),"route_distance_m":route["distance_m"],
                "within_travel_limit":within,
                "point":point.model_dump(mode="json"),
            })
        except (ProviderError, KeyError, TypeError, ValueError):
            continue

    feasible=[x for x in evaluated if x["within_travel_limit"]]
    feasible.sort(key=lambda x:(x["route_duration_min"],x["route_distance_m"],
                                x["straight_line_distance_m"] if x["straight_line_distance_m"] is not None else float("inf"),
                                x["entity_id"] or ""))
    return {
        "status":"recommendations_available" if feasible else "no_recommendation",
        "anchor":anchor.model_dump(mode="json"),"mode":mode,
        "candidate_generation":{"kind":raw.get("distance_kind","straight_line"),"radius_m":radius_m,
                                "returned_count":len(raw.get("places") or [])},
        "recommendations":feasible[:max_suggestions],
        "evaluated_count":len(evaluated),"feasible_count":len(feasible),
        "max_travel_minutes":max_travel_minutes,
        "provider_calls":provider_calls,"cache_hits":cache_hits,
        "itinerary_modified":False,"confirmation_required":True,
        "note":"Nearby radius is candidate generation only; ranking uses provider route time.",
    }
