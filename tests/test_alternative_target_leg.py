"""Alternative metrics must describe the leg that triggered the request."""
import asyncio
from copy import deepcopy

import pytest

from app.stage2a.models import distance_m
from app.stage2b.alternatives import suggest_alternatives
from app.stage2b.cache import SegmentCache
from app.stage2b.models import PlacePoint, PlanRequest, TimeSettings, Visit
from app.stage2b.planning import calculate


def point(label, latitude, entity_id=None):
    values = dict(label=label, latitude=latitude, longitude=103.85, confirmed=True, source="user_map")
    if entity_id:
        values.update(source="catalogue_representative", entity_id=entity_id, catalogue_build_id="fixture")
    return PlacePoint(**values)


async def trial_alternatives(finish_policy, visit_count):
    origin, hotel = point("Origin", 1.2990), point("Hotel", 1.3020)
    rows = [
        {"entity_id": "kept", "display_title": "Existing stop", "latitude": 1.2995},
        {"entity_id": "old", "display_title": "Original stop", "latitude": 1.3040},
        {"entity_id": "a", "display_title": "Candidate A", "latitude": 1.3005},
        {"entity_id": "b", "display_title": "Candidate B", "latitude": 1.3018},
    ]
    for row in rows:
        row.update(longitude=103.85, category="food", default_exploration_visible=True,
                   opening_hours_display="00:00-24:00", entrance_points=[], distance_m=100)
    entities = {row["entity_id"]: row for row in rows}
    visits = []
    if visit_count == 2:
        visits.append(Visit(visit_id="kept", point=point("Existing stop", 1.2995, "kept"), stay_minutes=15))
    visits.append(Visit(visit_id="replace", point=point("Original stop", 1.3040, "old"), stay_minutes=30))
    request = PlanRequest(session_id="d"*32, origin=origin, visits=visits, mode="walk", max_walk_minutes=10,
                          finish_policy=finish_policy, finish=hotel if finish_policy == "custom" else None,
                          time=TimeSettings(departure_at="2026-10-28T19:00:00+08:00"))
    incoming_origin = "Existing stop" if visit_count == 2 else "Origin"
    outgoing_finish = "Hotel" if finish_policy == "custom" else "Origin"
    minutes = {("Origin", "Existing stop"): 3,
               (incoming_origin, "Original stop"): 20 if finish_policy == "last_stop" else 5,
               (incoming_origin, "Candidate A"): 2,
               (incoming_origin, "Candidate B"): 8,
               ("Original stop", outgoing_finish): 20,
               ("Candidate A", outgoing_finish): 9,
               ("Candidate B", outgoing_finish): 1}

    class Routes:
        def route(self, route_request):
            a, b = route_request.origin, route_request.destination
            return {"provider": "OneMap", "mode": "walk", "queried_at": None,
                    "duration_s": minutes[(a.label, b.label)] * 60,
                    "distance_m": distance_m(a.latitude, a.longitude, b.latitude, b.longitude),
                    "geometry": {"type": "LineString", "coordinates": [
                        [a.longitude, a.latitude], [b.longitude, b.latitude]]},
                    "endpoint_offsets_m": {"origin": 0, "destination": 0}, "endpoint_review_required": False}

    plans, trial_requests, anchors = {}, [], []
    cache = SegmentCache()

    async def run(trial):
        trial_requests.append(trial.model_dump(mode="json"))
        plan = await calculate(trial, Routes(), cache,
                               resolver=lambda uid, build: {"entity": entities[uid]})
        plans[trial.visits[-1].point.entity_id] = plan
        return plan

    def nearby(**kwargs):
        anchors.append(kwargs)
        return {"build_id": "fixture", "places": rows[2:]}

    original = request.model_dump(mode="json")
    current = await run(request)
    current_before = deepcopy(current)
    leg_index = visit_count - 1 if finish_policy == "last_stop" else visit_count
    leg_id = f"leg-{leg_index+1}"
    assert current["walking_constraint_check"]["violations"][0]["leg_id"] == leg_id
    result = await suggest_alternatives(request=request, current_plan=current, leg_id=leg_id,
                                        nearby=nearby, calculate_plan=run, category="food")
    assert request.model_dump(mode="json") == original
    assert current == current_before
    return result, plans, trial_requests, anchors[0], original, leg_index


@pytest.mark.parametrize("finish_policy", ["custom", "return_to_start", "last_stop"])
@pytest.mark.parametrize("visit_count", [1, 2])
def test_metrics_and_ranking_follow_the_actual_violated_leg(finish_policy, visit_count):
    result, plans, trials, anchor, original, leg_index = asyncio.run(trial_alternatives(finish_policy, visit_count))
    context = result["problem"]
    assert context["leg_index"] == leg_index
    assert context["leg_id"] == f"leg-{leg_index+1}"
    assert context["visit_index"] == visit_count - 1
    assert context["visit_id"] == "replace"
    original_leg = plans["old"]["legs"][leg_index]
    expected_anchor = original_leg["origin"] if finish_policy == "last_stop" else original_leg["destination"]
    assert context["anchor"] == expected_anchor
    assert anchor["lat"] == expected_anchor["latitude"]
    assert anchor["lon"] == expected_anchor["longitude"]
    assert anchor["category"] == "food"
    expected_order = ["a", "b"] if finish_policy == "last_stop" else ["b", "a"]
    assert [item["entity_id"] for item in result["suggestions"]] == expected_order
    for item in result["suggestions"]:
        plan = plans[item["entity_id"]]
        affected_leg = plan["legs"][leg_index]
        assert affected_leg["leg_id"] == context["leg_id"]
        assert affected_leg["index"] == context["leg_index"]
        assert item["route_duration_min"] == affected_leg["duration_s"] / 60
        assert item["route_distance_m"] == pytest.approx(affected_leg["distance_m"])
        assert item["total_itinerary_min"] == plan["totals"]["reference_duration_s"] / 60
        assert item["feasibility_check"] == plan["feasibility_check"]
        assert item["feasible"] is True
    assert result["feasible_count"] == 2
    assert result["unknown_count"] == 0
    assert result["itinerary_modified"] is False
    assert result["confirmation_required"] is True
    for trial in trials[1:]:
        trial["visits"][-1]["point"] = original["visits"][-1]["point"]
        assert trial == original
