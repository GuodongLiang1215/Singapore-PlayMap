"""Constraint-aware alternative suggestions for Stage 2B.

This module never edits the user's itinerary. It takes an already-calculated
plan, identifies a visit involved in a walking-time violation, evaluates real
catalogue candidates with the existing route calculator, and returns ranked
suggestions for explicit confirmation by Stage 2C.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from app.stage2b.models import PlanRequest, PlacePoint
from app.stage2b.feasibility import evaluate_feasibility

NearbyProvider = Callable[..., dict]
CalculatePlan = Callable[[PlanRequest], Awaitable[dict]]


def violation_context(plan: dict, leg_id: str) -> dict:
    """Map a violated leg back to the visit that should be reconsidered.

    A leg runs from points[i] to points[i+1]. For leg i:
    - when i < number of visits, its destination is visit i;
    - a final return/custom-finish leg has no destination visit, so the previous
      visit is the only visit that can be reconsidered by this helper.
    """
    legs = plan.get("legs") or []
    visits = plan.get("visits") or []
    leg = next((item for item in legs if item.get("leg_id") == leg_id), None)
    if leg is None:
        raise ValueError(f"Unknown leg_id: {leg_id}")

    index = leg.get("index")
    if type(index) is not int or index < 0:
        raise ValueError("Violated leg has no valid index")
    if not visits:
        raise ValueError("Plan has no visits to reconsider")

    if index < len(visits):
        visit_index = index
        role = "destination_visit"
        anchor = leg.get("origin")
    else:
        visit_index = len(visits) - 1
        role = "visit_before_finish"
        anchor = leg.get("destination")

    visit = visits[visit_index]
    if not isinstance(anchor, dict):
        raise ValueError("Violated leg has no usable opposite endpoint")

    return {
        "leg_id": leg_id,
        "leg_index": index,
        "visit_index": visit_index,
        "visit_id": visit["visit_id"],
        "visit_label": visit["point"]["label"],
        "replacement_role": role,
        "anchor": anchor,
    }


def _point_from_candidate(candidate: dict, build_id: str | None) -> PlacePoint:
    entity_id = candidate.get("entity_id")
    if not entity_id:
        raise ValueError("Nearby candidate has no entity_id")
    if not build_id:
        raise ValueError("Catalogue build_id is required for a catalogue candidate")
    return PlacePoint(
        latitude=candidate["latitude"],
        longitude=candidate["longitude"],
        label=(candidate.get("display_title") or "Nearby place")[:200],
        source="catalogue_representative",
        entity_id=entity_id,
        catalogue_build_id=build_id,
        confirmed=True,
    )


def _request_with_replacement(request: PlanRequest, visit_index: int, point: PlacePoint) -> PlanRequest:
    updated = request.model_copy(deep=True)
    updated.visits[visit_index].point = point
    return PlanRequest.model_validate(updated.model_dump())


def _feasibility_label(check: dict) -> str:
    if check["feasible"] is True:
        return "Passes current feasibility checks"
    labels = {"opening_hours": "Opening hours need checking",
              "time_budget": "Time budget needs checking",
              "walking_constraint": "Walking limit needs checking"}
    return "; ".join(label for name, label in labels.items() if check["checks"][name] is None)


async def suggest_alternatives(
    *,
    request: PlanRequest,
    current_plan: dict,
    leg_id: str,
    nearby: NearbyProvider,
    calculate_plan: CalculatePlan,
    radius_m: float = 1000,
    category: str = "",
    limit: int = 20,
    max_suggestions: int = 5,
) -> dict:
    """Return checked or explicitly unknown replacements without mutating the itinerary.

    Nearby search is only candidate generation (straight-line distance). Every
    candidate is evaluated by recalculating the complete itinerary with the
    existing Stage 2B route calculator, so route time and the total time budget
    remain authoritative. Known failures at any stop are excluded; incomplete
    evidence remains visible as unknown, after candidates that pass all checks.
    """
    context = violation_context(current_plan, leg_id)
    anchor = context["anchor"]

    nearby_result = nearby(
        lat=anchor["latitude"],
        lon=anchor["longitude"],
        radius_m=radius_m,
        category=category,
        q="",
        limit=limit,
    )
    build_id = nearby_result.get("build_id")
    candidates = nearby_result.get("places") or []

    original_entity = request.visits[context["visit_index"]].point.entity_id
    evaluated = []

    for candidate in candidates:
        if candidate.get("entity_id") == original_entity:
            continue
        try:
            point = _point_from_candidate(candidate, build_id)
            trial_request = _request_with_replacement(request, context["visit_index"], point)
            trial_plan = await calculate_plan(trial_request)
        except Exception as exc:
            evaluated.append({
                "entity_id": candidate.get("entity_id"),
                "label": candidate.get("display_title"),
                "feasible": False,
                "reasons": ["route_or_plan_error"],
                "error_type": type(exc).__name__,
            })
            continue

        budget_check = trial_plan.get("budget_check") or {}
        check = evaluate_feasibility(
            visits=trial_plan["visits"], budget_check=budget_check,
            walking_constraint_check=trial_plan.get("walking_constraint_check"))
        trial_visit = trial_plan["visits"][context["visit_index"]]
        # A final finish/return leg follows the replaced visit; its index differs
        # from the visit index. Report and rank the leg that triggered the request.
        affected_leg = trial_plan["legs"][context["leg_index"]]
        evaluated.append({
            "entity_id": candidate.get("entity_id"),
            "label": candidate.get("display_title"),
            "category": candidate.get("category"),
            "straight_line_distance_m": candidate.get("distance_m"),
            "route_duration_min": round(affected_leg["duration_s"] / 60, 2),
            "route_distance_m": affected_leg["distance_m"],
            "total_itinerary_min": round(trial_plan["totals"]["reference_duration_s"] / 60, 2),
            "budget_slack_min": (
                None if budget_check.get("slack_s") is None
                else round(budget_check["slack_s"] / 60, 2)
            ),
            "visit_duration_min": trial_visit["stay_minutes"],
            "feasible": check["feasible"],
            "status": check["status"],
            "reasons": check["reasons"],
            "unknown_reasons": check["unknown_reasons"],
            "feasibility_check": check,
            "feasibility_label": _feasibility_label(check),
            "replacement_point": point.model_dump(mode="json"),
        })

    feasible_count = sum(item["feasible"] is True for item in evaluated)
    unknown_count = sum(item["feasible"] is None for item in evaluated)
    suggestions = [item for item in evaluated if item["feasible"] is not False]
    suggestions.sort(key=lambda item: (
        item["feasible"] is not True,
        item["route_duration_min"],
        item["total_itinerary_min"],
        item["straight_line_distance_m"] if item["straight_line_distance_m"] is not None else float("inf"),
        item["entity_id"] or "",
    ))

    return {
        "status": ("suggestions_available" if feasible_count else
                   "suggestions_need_review" if unknown_count else "no_feasible_suggestion"),
        "problem": context,
        "candidate_generation": {
            "kind": nearby_result.get("distance_kind", "straight_line"),
            "radius_m": radius_m,
            "returned_count": len(candidates),
        },
        "suggestions": suggestions[:max_suggestions],
        "evaluated_count": len(evaluated),
        "feasible_count": feasible_count,
        "unknown_count": unknown_count,
        "itinerary_modified": False,
        "confirmation_required": True,
        "note": "Checks use reference route estimates and catalogue hours, not live opening or verified access. Unknown checks need review. Suggestions are not adopted automatically; confirm a replacement first.",
    }
