from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.stage2b import api as stage2b_api
from app.stage2b.models import PlanRequest, PlacePoint, Visit
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
    client = TestClient(app)
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
    client = TestClient(app)
    response = client.post("/api/stage2b/alternatives", json={
        "plan_request": plan_request().model_dump(mode="json"),
        "current_plan": plan,
        "leg_id": "leg-1",
    })
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "LEG_NOT_REPORTED_AS_VIOLATION"


def test_alternatives_endpoint_requires_walk_constraint():
    req = plan_request().model_copy(update={"max_walk_minutes": None})
    client = TestClient(app)
    response = client.post("/api/stage2b/alternatives", json={
        "plan_request": req.model_dump(mode="json"),
        "current_plan": current_plan(),
        "leg_id": "leg-1",
    })
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "WALKING_CONSTRAINT_REQUIRED"
