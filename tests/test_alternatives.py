import asyncio
from copy import deepcopy

import pytest

from app.stage2b import planning
from app.stage2b.cache import SegmentCache

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
                {"visit_id": "v1", "point": trial.visits[0].point.model_dump(), "stay_minutes": 30, "opening_status": "feasible"},
                {"visit_id": "v2", "point": trial.visits[1].point.model_dump(), "stay_minutes": 45, "opening_status": "feasible"},
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


def test_suggest_alternatives_passes_category_to_nearby_search():
    req = request()
    seen = {}

    def nearby(**kwargs):
        seen.update(kwargs)
        return {
            "build_id": "build-1",
            "distance_kind": "straight_line_not_walking_route",
            "places": [],
        }

    async def calculate_plan(_trial):
        raise AssertionError("No candidates should be evaluated in this test")

    result = asyncio.run(suggest_alternatives(
        request=req,
        current_plan=current_plan(),
        leg_id="leg-1",
        nearby=nearby,
        calculate_plan=calculate_plan,
        category="nature",
    ))

    assert seen["category"] == "nature"
    assert result["status"] == "no_feasible_suggestion"


async def actual_trial_suggestions(candidates, *, second_hours="09:00-23:00", second_leg_minutes=8,
                                   departure="2026-10-28T19:00:00+08:00", budget=120, max_suggestions=5):
    """Exercise the production planner; only catalogue evidence and provider routes are fake."""
    req = request()
    req.time = TimeSettings(mode="budget", budget_minutes=budget, departure_at=departure)
    entities = {
        "museum": {"entity_id": "museum", "display_title": "Museum", "category": "tourism",
                   "latitude": 1.3200, "longitude": 103.8700, "opening_hours_display": second_hours,
                   "default_exploration_visible": True, "entrance_points": []},
    }
    places = []
    minutes_by_lat = {1.3200: second_leg_minutes}
    for index, (uid, hours, minutes) in enumerate(candidates):
        latitude, longitude = 1.301 + index * .001, 103.851 + index * .001
        item = {"entity_id": uid, "display_title": uid, "category": "nature",
                "latitude": latitude, "longitude": longitude, "distance_m": 100 + index * 20,
                "opening_hours_display": hours, "default_exploration_visible": True, "entrance_points": []}
        entities[uid] = item
        places.append(item)
        minutes_by_lat[latitude] = minutes

    class Routes:
        def route(self, route_request):
            origin, destination = route_request.origin, route_request.destination
            minutes = minutes_by_lat[destination.latitude]
            return {"provider": "OneMap", "mode": "walk", "queried_at": None,
                    "distance_m": minutes * 70, "duration_s": minutes * 60,
                    "geometry": {"type": "LineString", "coordinates": [
                        [origin.longitude, origin.latitude], [destination.longitude, destination.latitude]]},
                    "endpoint_offsets_m": {"origin": 0.0, "destination": 0.0}, "endpoint_review_required": False}

    trial_requests, trial_plans = [], []
    cache = SegmentCache()

    async def calculate_trial(trial):
        trial_requests.append(trial.model_dump(mode="json"))
        plan = await planning.calculate(trial, Routes(), cache,
                                        resolver=lambda uid, build: {"entity": entities[uid]})
        trial_plans.append(plan)
        return plan

    current = current_plan()
    before_request, before_current = req.model_dump(mode="json"), deepcopy(current)
    result = await suggest_alternatives(
        request=req, current_plan=current, leg_id="leg-1", calculate_plan=calculate_trial,
        nearby=lambda **kwargs: {"build_id": "build-1", "places": places}, max_suggestions=max_suggestions)
    assert req.model_dump(mode="json") == before_request
    assert current == before_current
    return result, trial_plans, trial_requests, before_request


@pytest.mark.parametrize(("hours", "opening_status"), [
    ("09:00-18:00", "closed_at_arrival"),
    ("09:00-19:20", "insufficient_time"),
])
def test_actual_planner_excludes_closed_or_overrunning_replacement(hours, opening_status):
    result, plans, _, _ = asyncio.run(actual_trial_suggestions([("candidate", hours, 5)]))
    assert plans[0]["visits"][0]["opening_status"] == opening_status
    assert result["suggestions"] == []
    assert result["status"] == "no_feasible_suggestion"
    assert result["feasible_count"] == result["unknown_count"] == 0


@pytest.mark.parametrize(("hours", "departure"), [
    (None, "2026-10-28T19:00:00+08:00"),
    ("Mon-Fri: 09:00-23:00; weekends vary", "2026-10-28T19:00:00+08:00"),
    ("09:00-23:00", None),
])
def test_actual_planner_keeps_unknown_as_explicit_review_suggestion(hours, departure):
    result, plans, _, _ = asyncio.run(actual_trial_suggestions([("unknown", hours, 5)], departure=departure))
    assert plans[0]["visits"][0]["opening_status"] == "unknown"
    item = result["suggestions"][0]
    assert item["feasible"] is None
    assert item["feasibility_check"] == plans[0]["feasibility_check"]
    assert item["feasibility_check"]["checks"]["opening_hours"] is None
    assert item["unknown_reasons"]
    assert item["feasibility_label"] == "Opening hours need checking"
    assert result["status"] == "suggestions_need_review"
    assert result["feasible_count"] == 0
    assert result["unknown_count"] == 1


def test_actual_planner_ranks_checked_above_faster_unknown_before_limit():
    result, _, _, _ = asyncio.run(actual_trial_suggestions([
        ("unknown", None, 1), ("open", "09:00-23:00", 8)], max_suggestions=1))
    assert [item["entity_id"] for item in result["suggestions"]] == ["open"]
    item = result["suggestions"][0]
    assert item["feasible"] is True
    assert item["feasibility_check"]["checks"] == {
        "opening_hours": True, "time_budget": True, "walking_constraint": True}
    assert result["status"] == "suggestions_available"
    assert result["feasible_count"] == result["unknown_count"] == 1


def test_actual_planner_rejects_candidate_when_unchanged_other_stop_closes():
    result, plans, _, _ = asyncio.run(actual_trial_suggestions(
        [("open", "09:00-23:00", 8)], second_hours="09:00-20:00"))
    assert plans[0]["visits"][0]["opening_status"] == "feasible"
    assert plans[0]["visits"][1]["opening_status"] == "insufficient_time"
    assert result["suggestions"] == []
    assert result["feasible_count"] == 0


@pytest.mark.parametrize(("budget", "second_leg_minutes", "failed_check"), [
    (85, 8, "time_budget"), (120, 12, "walking_constraint"),
])
def test_actual_planner_rechecks_budget_and_every_walk_leg(budget, second_leg_minutes, failed_check):
    result, plans, _, _ = asyncio.run(actual_trial_suggestions(
        [("open", "09:00-23:00", 8)], budget=budget, second_leg_minutes=second_leg_minutes))
    assert all(v["opening_status"] == "feasible" for v in plans[0]["visits"])
    if failed_check == "time_budget":
        assert plans[0]["budget_check"]["reference_within_budget"] is False
    else:
        assert plans[0]["walking_constraint_check"]["violations"][0]["leg_id"] == "leg-2"
    assert result["suggestions"] == []
    assert result["feasible_count"] == 0


def test_actual_planner_replacement_preserves_fixed_stays_and_all_other_request_fields():
    result, plans, trials, original = asyncio.run(actual_trial_suggestions([("open", "09:00-23:00", 8)]))
    assert result["itinerary_modified"] is False
    assert result["confirmation_required"] is True
    assert result["suggestions"][0]["feasibility_check"] == plans[0]["feasibility_check"]
    assert [visit["stay_minutes"] for visit in plans[0]["visits"]] == [30, 45]
    changed_point = trials[0]["visits"][0]["point"]
    assert changed_point["entity_id"] == "open"
    trials[0]["visits"][0]["point"] = original["visits"][0]["point"]
    assert trials[0] == original
