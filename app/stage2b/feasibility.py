"""Shared three-state feasibility for reference itinerary calculations.

This combines catalogue opening hours, the reference total-time budget and the
walking-leg limit. It does not certify live opening, access, traffic or every
chat preference. Unrequested constraints are neutral; missing checks are unknown.
"""
from __future__ import annotations


def evaluate_feasibility(*, visits: list[dict], budget_check: dict | None,
                         walking_constraint_check: dict | None) -> dict:
    reasons, unknown_reasons = [], []
    opening_statuses = [visit.get("opening_status") for visit in visits]
    if "closed_at_arrival" in opening_statuses:
        reasons.append("opening_hours_closed")
    if "insufficient_time" in opening_statuses:
        reasons.append("opening_hours_insufficient_time")
    if any(status not in ("feasible", "closed_at_arrival", "insufficient_time")
           for status in opening_statuses):
        unknown_reasons.append("opening_hours_unknown")
    hours = False if reasons else None if unknown_reasons else True

    budget = budget_check or {}
    budget_result = budget.get("reference_within_budget")
    if budget_result is False:
        reasons.append("time_budget")
    elif budget_result is True or budget.get("status") == "no_budget":
        budget_result = True
    else:
        budget_result = None
        unknown_reasons.append("time_budget_unknown")

    walking = (walking_constraint_check or {}).get("status")
    if walking == "violation":
        walking_result = False
        reasons.append("walking_constraint")
    elif walking in ("ok", "not_requested"):
        walking_result = True
    else:
        walking_result = None
        unknown_reasons.append("walking_constraint_unknown")

    feasible = False if reasons else None if unknown_reasons else True
    return {
        "feasible": feasible,
        "status": "infeasible" if feasible is False else "unknown" if feasible is None else "feasible",
        "basis": "reference_estimates_and_catalogue_hours",
        "checks": {"opening_hours": hours, "time_budget": budget_result,
                   "walking_constraint": walking_result},
        "reasons": reasons,
        "unknown_reasons": unknown_reasons,
    }
