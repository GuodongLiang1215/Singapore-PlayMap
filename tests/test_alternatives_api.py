from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.stage2b import api as stage2b_api
from app.stage2b.models import PlanRequest, PlacePoint, Visit, TimeSettings
from app.stage2b.cache import SegmentCache
from app.stage2b_main import app


def point(label, lat, lon, entity_id=None):
    kwargs = dict(latitude=lat, longitude=lon, label=label, confirmed=True)
    if entity_id:
        kwargs.update(source="catalogue_representative", entity_id=entity_id, catalogue_build_id="build-1")
    else:
        kwargs.update(source="user_map")
    return PlacePoint(**kwargs)


def plan_request():
    return PlanRequest(
        session_id="b" * 32,
        origin=point("Origin", 1.3000, 103.8500),
        visits=[Visit(visit_id="v1", point=point("Too Far", 1.3100, 103.8600, "old"), stay_minutes=30)],
        mode="walk",
        max_walk_minutes=10,
    )


def current_plan():
    return {
        "visits": [{"visit_id": "v1", "point": {"label": "Too Far"}}],
        "legs": [{
            "leg_id": "leg-1", "index": 0,
            "origin": {"latitude": 1.3000, "longitude": 103.8500, "label": "Origin"},
            "destination": {"latitude": 1.3100, "longitude": 103.8600, "label": "Too Far"},
        }],
        "walking_constraint_check": {
            "status": "violation",
            "violations": [{"leg_id": "leg-1", "duration_minutes": 15, "exceeded_by_minutes": 5}],
        },
    }


def test_alternatives_endpoint_returns_suggestions(monkeypatch):
    async def fake_suggest(**kwargs):
        assert kwargs["leg_id"] == "leg-1"
        assert kwargs["request"].max_walk_minutes == 10
        return {
            "status": "suggestions_available",
            "suggestions": [{"entity_id": "good", "label": "Good Park"}],
            "itinerary_modified": False,
            "confirmation_required": True,
        }

    monkeypatch.setattr(stage2b_api, "suggest_alternatives", fake_suggest)
    client = TestClient(app, headers={"X-PlayMap-Client": "stage2b"})
    response = client.post("/api/stage2b/alternatives", json={
        "plan_request": plan_request().model_dump(mode="json"),
        "current_plan": current_plan(),
        "leg_id": "leg-1",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["suggestions"][0]["entity_id"] == "good"
    assert body["itinerary_modified"] is False
    assert body["confirmation_required"] is True


def test_alternatives_endpoint_rejects_nonviolating_leg():
    plan = current_plan()
    plan["walking_constraint_check"] = {"status": "ok", "violations": []}
    client = TestClient(app, headers={"X-PlayMap-Client": "stage2b"})
    response = client.post("/api/stage2b/alternatives", json={
        "plan_request": plan_request().model_dump(mode="json"),
        "current_plan": plan,
        "leg_id": "leg-1",
    })
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "LEG_NOT_REPORTED_AS_VIOLATION"


def test_alternatives_endpoint_requires_walk_constraint():
    req = plan_request().model_copy(update={"max_walk_minutes": None})
    client = TestClient(app, headers={"X-PlayMap-Client": "stage2b"})
    response = client.post("/api/stage2b/alternatives", json={
        "plan_request": req.model_dump(mode="json"),
        "current_plan": current_plan(),
        "leg_id": "leg-1",
    })
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "WALKING_CONSTRAINT_REQUIRED"


def test_alternatives_endpoint_uses_real_planner_and_serializes_three_state_checks(monkeypatch):
    """Only external catalogue evidence and routes are fake; API, suggestion and planner code run."""
    candidates = [
        {"entity_id": "unknown", "display_title": "Unknown Park", "category": "nature",
         "latitude": 1.301, "longitude": 103.851, "opening_hours_display": None},
        {"entity_id": "closed", "display_title": "Closed Park", "category": "nature",
         "latitude": 1.302, "longitude": 103.852, "opening_hours_display": "09:00-18:00"},
        {"entity_id": "open", "display_title": "Open Park", "category": "nature",
         "latitude": 1.303, "longitude": 103.853, "opening_hours_display": "09:00-23:00"},
    ]
    for candidate in candidates:
        candidate.update(default_exploration_visible=True, entrance_points=[], distance_m=100)
    by_id = {candidate["entity_id"]: candidate for candidate in candidates}
    route_calls = []

    class Routes:
        def route(self, request):
            route_calls.append(request.destination.label)
            return {"provider": "OneMap", "mode": request.mode, "queried_at": None,
                    "distance_m": 300, "duration_s": 300,
                    "geometry": {"type": "LineString", "coordinates": [
                        [request.origin.longitude, request.origin.latitude],
                        [request.destination.longitude, request.destination.latitude]]},
                    "endpoint_offsets_m": {"origin": 0.0, "destination": 0.0},
                    "endpoint_review_required": False}

    monkeypatch.setattr(stage2b_api.geo, "client", Routes())
    monkeypatch.setattr(stage2b_api, "cache", SegmentCache())
    monkeypatch.setattr(stage2b_api.catalogue, "nearby", lambda **kwargs: {
        "build_id": "build-1", "places": candidates, "distance_kind": "straight_line"})
    monkeypatch.setattr(stage2b_api.catalogue, "entity", lambda uid, build: {"entity": by_id[uid]})
    req = plan_request()
    req.time = TimeSettings(departure_at="2026-10-28T19:00:00+08:00")
    submitted = req.model_dump(mode="json")
    client = TestClient(app, headers={"X-PlayMap-Client": "stage2b"})
    response = client.post("/api/stage2b/alternatives", json={
        "plan_request": submitted, "current_plan": current_plan(), "leg_id": "leg-1"})
    assert response.status_code == 200
    result = response.json()
    assert route_calls == ["Unknown Park", "Closed Park", "Open Park"]
    assert [item["entity_id"] for item in result["suggestions"]] == ["open", "unknown"]
    checked, unknown = result["suggestions"]
    assert checked["feasible"] is True
    assert checked["feasibility_check"]["status"] == "feasible"
    assert checked["feasibility_label"] == "Passes current feasibility checks"
    assert unknown["feasible"] is None
    assert unknown["feasibility_check"]["status"] == "unknown"
    assert unknown["unknown_reasons"] == ["opening_hours_unknown"]
    assert unknown["feasibility_label"] == "Opening hours need checking"
    assert result["status"] == "suggestions_available"
    assert result["feasible_count"] == result["unknown_count"] == 1
    assert result["evaluated_count"] == 3
    assert result["itinerary_modified"] is False
    assert result["confirmation_required"] is True
    assert req.model_dump(mode="json") == submitted
