"""Business regressions through actual catalogue preparation and plan assembly."""
from datetime import datetime, timedelta

import pytest

from app.stage2b import planning
from app.stage2b.models import PlanRequest, PlacePoint, TimeSettings, Visit


def make_plan(*, hours="每日 09:00–18:00", travel_minutes=10, stay_minutes=30,
              budget_minutes=None, max_walk_minutes=None, departure=True,
              mode="walk", window_minutes=None):
    origin = PlacePoint(latitude=1.30, longitude=103.85, label="Origin",
                        source="user_map", confirmed=True)
    place = PlacePoint(latitude=1.31, longitude=103.86, label="Museum",
                       source="catalogue_representative", confirmed=True,
                       entity_id="museum-1", catalogue_build_id="fixture-build")
    start = datetime.fromisoformat("2026-10-28T10:00:00+08:00") if departure else None
    time = TimeSettings(mode="estimate", departure_at=start)
    if budget_minutes is not None:
        time = TimeSettings(mode="budget", departure_at=start, budget_minutes=budget_minutes)
    if window_minutes is not None:
        time = TimeSettings(mode="window", departure_at=start,
                            finish_by=start + timedelta(minutes=window_minutes))
    request = PlanRequest(session_id="a" * 32, origin=origin,
                          visits=[Visit(visit_id="v1", point=place, stay_minutes=stay_minutes)],
                          time=time, mode=mode, max_walk_minutes=max_walk_minutes)
    entity = {"entity_id": "museum-1", "default_exploration_visible": True,
              "latitude": place.latitude, "longitude": place.longitude,
              "display_title": "Museum", "category": "tourism",
              "opening_hours_display": hours}
    prepared = planning.prepare(request, lambda uid, build: {"entity": entity})
    leg = {"leg_id": "leg-1", "index": 0, "kind": "provider_route", "mode": mode,
           "origin": origin.model_dump(), "destination": place.model_dump(),
           "duration_s": travel_minutes * 60, "distance_m": 700,
           "geometry": {"type": "LineString", "coordinates": [[103.85, 1.30], [103.86, 1.31]]},
           "endpoint_review_required": False,
           "diagnostics": {"large_detour_flag": False, "geometry_summary_mismatch": False}}
    return planning.assemble(request, prepared, [leg])


@pytest.mark.parametrize(("settings", "expected", "failed", "unknown"), [
    ({"budget_minutes": 20}, False, "time_budget", None),
    ({"max_walk_minutes": 5}, False, "walking_constraint", None),
    ({"window_minutes": 20}, False, "time_budget", None),
    ({"hours": "每日 11:00–18:00"}, False, "opening_hours_closed", None),
    ({"hours": "每日 09:00–10:20"}, False, "opening_hours_insufficient_time", None),
    ({"hours": None, "budget_minutes": 20}, False, "time_budget", "opening_hours_unknown"),
    ({"hours": None, "max_walk_minutes": 5}, False, "walking_constraint", "opening_hours_unknown"),
    ({"hours": None, "budget_minutes": 60}, None, None, "opening_hours_unknown"),
    ({"departure": False}, None, None, "opening_hours_unknown"),
    ({"hours": "Mo-Fr 09:00-18:00; PH off"}, None, None, "opening_hours_unknown"),
    ({"budget_minutes": 40, "max_walk_minutes": 10}, True, None, None),
    ({"mode": "drive", "max_walk_minutes": 5}, True, None, None),
    ({}, True, None, None),
])
def test_overall_feasibility_agrees_with_applicable_checks(settings, expected, failed, unknown):
    plan = make_plan(**settings)
    assert plan["itinerary_feasible"] is expected
    check = plan["feasibility_check"]
    assert check["feasible"] is expected
    assert check["status"] == ("unknown" if expected is None else "feasible" if expected else "infeasible")
    assert check["basis"] == "reference_estimates_and_catalogue_hours"
    assert (failed in check["reasons"]) if failed else not check["reasons"]
    assert (unknown in check["unknown_reasons"]) if unknown else not check["unknown_reasons"]


def test_all_conflicts_remain_visible_in_the_overall_result():
    plan = make_plan(hours="每日 11:00–18:00", budget_minutes=20, max_walk_minutes=5)
    assert plan["itinerary_feasible"] is False
    assert set(plan["feasibility_check"]["reasons"]) == {
        "opening_hours_closed", "time_budget", "walking_constraint"}
    assert plan["budget_check"]["reference_within_budget"] is False
    assert plan["walking_constraint_check"]["status"] == "violation"
    assert plan["visits"][0]["opening_status"] == "closed_at_arrival"


def test_reference_pass_does_not_erase_upper_estimate_warning():
    plan = make_plan(stay_minutes=None, budget_minutes=75)
    assert plan["budget_check"]["reference_within_budget"] is True
    assert plan["budget_check"]["range_within_budget"] is False
    assert plan["budget_check"]["status"] == "reference_fits_upper_exceeds"
    assert plan["itinerary_feasible"] is True
    assert plan["feasibility_check"]["basis"] == "reference_estimates_and_catalogue_hours"


def test_missing_requested_checks_cannot_be_promoted_to_a_pass():
    from app.stage2b.feasibility import evaluate_feasibility

    check = evaluate_feasibility(visits=[{"opening_status": "feasible"}],
                                 budget_check={}, walking_constraint_check={})
    assert check["feasible"] is None
    assert set(check["unknown_reasons"]) == {"time_budget_unknown", "walking_constraint_unknown"}


def test_one_known_open_visit_does_not_prove_the_remaining_visit_is_open():
    from app.stage2b.feasibility import evaluate_feasibility

    check = evaluate_feasibility(visits=[{"opening_status": "feasible"}, {}],
                                 budget_check={"status": "no_budget"},
                                 walking_constraint_check={"status": "not_requested"})
    assert check["feasible"] is None
    assert check["unknown_reasons"] == ["opening_hours_unknown"]
