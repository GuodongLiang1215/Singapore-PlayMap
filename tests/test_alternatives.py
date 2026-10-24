import asyncio

from app.stage2b.alternatives import suggest_alternatives, violation_context
from app.stage2b.models import PlanRequest, PlacePoint, Visit, TimeSettings


def point(label, lat, lon, entity_id=None):
    kwargs = dict(latitude=lat, longitude=lon, label=label, confirmed=True)
    if entity_id:
        kwargs.update(source="catalogue_representative", entity_id=entity_id, catalogue_build_id="build-1")
    else:
        kwargs.update(source="user_map")
    return PlacePoint(**kwargs)


def request():
    return PlanRequest(
        session_id="a" * 32,
        origin=point("Origin", 1.3000, 103.8500),
        visits=[
            Visit(visit_id="v1", point=point("Too Far", 1.3100, 103.8600, "old"), stay_minutes=30),
            Visit(visit_id="v2", point=point("Museum", 1.3200, 103.8700, "museum"), stay_minutes=45),
        ],
        mode="walk",
        max_walk_minutes=10,
        time=TimeSettings(mode="budget", budget_minutes=120),
    )


def current_plan():
    return {
        "visits": [
            {"visit_id": "v1", "point": {"label": "Too Far"}},
            {"visit_id": "v2", "point": {"label": "Museum"}},
        ],
        "legs": [
            {"leg_id": "leg-1", "index": 0,
             "origin": {"latitude": 1.3000, "longitude": 103.8500, "label": "Origin"},
             "destination": {"latitude": 1.3100, "longitude": 103.8600, "label": "Too Far"}},
            {"leg_id": "leg-2", "index": 1,
             "origin": {"latitude": 1.3100, "longitude": 103.8600, "label": "Too Far"},
             "destination": {"latitude": 1.3200, "longitude": 103.8700, "label": "Museum"}},
        ],
    }


def test_violation_context_maps_leg_to_destination_visit():
    context = violation_context(current_plan(), "leg-1")
    assert context["visit_index"] == 0
    assert context["visit_id"] == "v1"
    assert context["replacement_role"] == "destination_visit"
    assert context["anchor"]["label"] == "Origin"


def test_suggest_alternatives_filters_and_ranks_complete_plan_results():
    req = request()

    def nearby(**kwargs):
        assert kwargs["lat"] == 1.3000
        assert kwargs["lon"] == 103.8500
        return {
            "build_id": "build-1",
            "distance_kind": "straight_line_not_walking_route",
            "places": [
                {"entity_id": "old", "display_title": "Too Far", "category": "nature",
                 "latitude": 1.3100, "longitude": 103.8600, "distance_m": 500},
                {"entity_id": "good", "display_title": "Good Park", "category": "nature",
                 "latitude": 1.3040, "longitude": 103.8540, "distance_m": 600},
                {"entity_id": "badwalk", "display_title": "Long Walk", "category": "nature",
                 "latitude": 1.3050, "longitude": 103.8550, "distance_m": 450},
                {"entity_id": "badbudget", "display_title": "Long Visit", "category": "tourism",
                 "latitude": 1.3060, "longitude": 103.8560, "distance_m": 400},
            ],
        }

    async def calculate_plan(trial):
        entity = trial.visits[0].point.entity_id
        values = {
            "good": (8, 90, True),
            "badwalk": (14, 95, True),
            "badbudget": (7, 130, False),
        }
        walk_min, total_min, budget_ok = values[entity]
        return {
            "visits": [
                {"visit_id": "v1", "point": trial.visits[0].point.model_dump(), "stay_minutes": 30},
                {"visit_id": "v2", "point": trial.visits[1].point.model_dump(), "stay_minutes": 45},
            ],
            "legs": [
                {"leg_id": "leg-1", "index": 0, "duration_s": walk_min * 60, "distance_m": walk_min * 70},
                {"leg_id": "leg-2", "index": 1, "duration_s": 5 * 60, "distance_m": 350},
            ],
            "walking_constraint_check": {
                "status": "ok" if walk_min <= 10 else "violation",
                "violations": [] if walk_min <= 10 else [{"leg_id": "leg-1"}],
            },
            "budget_check": {
                "reference_within_budget": budget_ok,
                "slack_s": (120 - total_min) * 60,
            },
            "totals": {"reference_duration_s": total_min * 60},
        }

    result = asyncio.run(suggest_alternatives(
        request=req,
        current_plan=current_plan(),
        leg_id="leg-1",
        nearby=nearby,
        calculate_plan=calculate_plan,
    ))

    assert result["status"] == "suggestions_available"
    assert result["itinerary_modified"] is False
    assert result["confirmation_required"] is True
    assert [x["entity_id"] for x in result["suggestions"]] == ["good"]
    assert result["suggestions"][0]["route_duration_min"] == 8
    assert result["evaluated_count"] == 3
    assert result["feasible_count"] == 1
