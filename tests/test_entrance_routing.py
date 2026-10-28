import pytest
from app.stage2b import planning
from app.stage2b.models import PlanRequest, PlacePoint, Visit
from app.stage2a.transport import ProviderError
from app.stage2b.cache import SegmentCache


def _point(lat,lon,label):
    return PlacePoint(latitude=lat,longitude=lon,label=label,source="user_map",confirmed=True)


def _prepared(entrances):
    origin=_point(1.30,103.80,"Origin")
    visit=_point(1.31,103.81,"Park")
    req=PlanRequest(session_id="a"*32,origin=origin,visits=[Visit(visit_id="v1",point=visit)],mode="walk")
    p=planning.prepare(req)
    p["visits"][0]["entrance_candidates"]=entrances
    return req,p


@pytest.mark.anyio
async def test_single_entrance_candidate_becomes_routing_point_without_provider_selection_call():
    req,p=_prepared([{"name":"Gate A","latitude":1.305,"longitude":103.805}])
    class Client:
        def route(self,*args,**kwargs): raise AssertionError("single candidate selection needs no comparison call")
    calls=await planning._select_visit_entrances(req,p,Client(),SegmentCache(),0)
    v=p["visits"][0]
    assert calls==0
    assert v["routing_point"]["label"]=="Gate A"
    assert v["selected_entrance"]["label"]=="Gate A"
    assert v["entrance_selection_basis"]=="single_entrance_candidate"


@pytest.mark.anyio
async def test_multiple_entrances_choose_fastest_reachable_provider_route():
    req,p=_prepared([
        {"name":"Slow Gate","latitude":1.305,"longitude":103.805},
        {"name":"Fast Gate","latitude":1.306,"longitude":103.806},
    ])
    class Client:
        def route(self,rr):
            seconds=900 if rr.destination.label=="Slow Gate" else 300
            return {"provider":"OneMap","mode":"walk","queried_at":None,
                "distance_m":1000,"duration_s":seconds,
                "geometry":{"type":"LineString","coordinates":[[103.80,1.30],[rr.destination.longitude,rr.destination.latitude]]},
                "endpoint_offsets_m":{"origin":0.0,"destination":0.0},"endpoint_review_required":False}
    calls=await planning._select_visit_entrances(req,p,Client(),SegmentCache(),0)
    v=p["visits"][0]
    assert calls==2
    assert v["selected_entrance"]["label"]=="Fast Gate"
    assert v["entrance_selection_basis"]=="fastest_reachable_entrance_candidate"


@pytest.mark.anyio
async def test_all_entrance_candidates_unreachable_falls_back_to_representative_point():
    req,p=_prepared([
        {"name":"Gate A","latitude":1.305,"longitude":103.805},
        {"name":"Gate B","latitude":1.306,"longitude":103.806},
    ])
    class Client:
        def route(self,rr):
            raise ProviderError("NO_ROUTE","unreachable",422)
    calls=await planning._select_visit_entrances(req,p,Client(),SegmentCache(),0)
    v=p["visits"][0]
    assert calls==2
    assert v["selected_entrance"] is None
    assert v["routing_point"]["label"]=="Park"
    assert v["entrance_selection_basis"]=="representative_point_all_candidates_unreachable"


@pytest.mark.anyio
async def test_departure_from_visit_reuses_selected_arrival_entrance():
    origin=_point(1.30,103.80,"Origin")
    first=_point(1.31,103.81,"Park A")
    second=_point(1.32,103.82,"Park B")
    req=PlanRequest(session_id="b"*32,origin=origin,
        visits=[Visit(visit_id="v1",point=first),Visit(visit_id="v2",point=second)],mode="walk")
    p=planning.prepare(req)
    p["visits"][0]["entrance_candidates"]=[{"name":"Gate A","latitude":1.305,"longitude":103.805}]
    p["visits"][1]["entrance_candidates"]=[]
    class Client: pass
    await planning._select_visit_entrances(req,p,Client(),SegmentCache(),0)
    assert p["points"][1].label=="Gate A"
    assert p["points"][2].label=="Park B"
