import pytest
from app.stage2b.recommendations import recommend_nearby
from app.stage2b.models import PlacePoint
from app.stage2b.cache import SegmentCache
from app.stage2a.transport import ProviderError


def anchor():
    return PlacePoint(latitude=1.30,longitude=103.80,label="Origin",source="user_map",confirmed=True)


def nearby(**kwargs):
    return {"build_id":"build-1","distance_kind":"straight_line_not_walking_route","places":[
        {"entity_id":"near","display_title":"Near but slow","category":"nature","latitude":1.301,"longitude":103.801,"distance_m":100},
        {"entity_id":"far","display_title":"Far but fast","category":"nature","latitude":1.302,"longitude":103.802,"distance_m":200},
        {"entity_id":"bad","display_title":"Unreachable","category":"nature","latitude":1.303,"longitude":103.803,"distance_m":300},
    ]}


class Client:
    def route(self,rr):
        if rr.destination.label=="Unreachable":
            raise ProviderError("NO_ROUTE","no route",422)
        sec=600 if rr.destination.label=="Near but slow" else 300
        return {"provider":"OneMap","mode":"walk","queried_at":None,"distance_m":1000 if sec==600 else 1200,
            "duration_s":sec,"geometry":{"type":"LineString","coordinates":[[103.80,1.30],[rr.destination.longitude,rr.destination.latitude]]},
            "endpoint_offsets_m":{"origin":0.0,"destination":0.0},"endpoint_review_required":False}


@pytest.mark.anyio
async def test_recommendations_rank_by_route_time_not_straight_line():
    r=await recommend_nearby(anchor=anchor(),session_id="c"*32,revision=0,mode="walk",
        nearby=nearby,client=Client(),cache=SegmentCache())
    assert [x["entity_id"] for x in r["recommendations"]]==["far","near"]
    assert r["evaluated_count"]==2
    assert r["provider_calls"]==3
    assert r["itinerary_modified"] is False


@pytest.mark.anyio
async def test_recommendations_apply_max_travel_minutes():
    r=await recommend_nearby(anchor=anchor(),session_id="d"*32,revision=0,mode="walk",
        nearby=nearby,client=Client(),cache=SegmentCache(),max_travel_minutes=6)
    assert [x["entity_id"] for x in r["recommendations"]]==["far"]
    assert r["feasible_count"]==1
    assert r["max_travel_minutes"]==6
