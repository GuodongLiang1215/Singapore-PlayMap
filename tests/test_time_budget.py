from app.stage2b.models import PlanRequest, PlacePoint, Visit, TimeSettings
from app.stage2b import planning


def _plan(budget_minutes, stay_minutes=None):
    origin=PlacePoint(latitude=1.30,longitude=103.80,label="Origin",source="user_map",confirmed=True)
    place=PlacePoint(latitude=1.31,longitude=103.81,label="Park",source="user_map",confirmed=True)
    req=PlanRequest(
        session_id="f"*32, origin=origin,
        visits=[Visit(visit_id="v1",point=place,stay_minutes=stay_minutes)],
        mode="walk",
        time=TimeSettings(mode="budget",budget_minutes=budget_minutes),
    )
    prepared=planning.prepare(req)
    leg={"leg_id":"leg-1","index":0,"kind":"provider_route",
        "origin":origin.model_dump(),"destination":place.model_dump(),"provider":"OneMap","mode":"walk",
        "queried_at":None,"distance_m":1000,"duration_s":600,
        "geometry":{"type":"LineString","coordinates":[[103.80,1.30],[103.81,1.31]]},
        "endpoint_offsets_m":{"origin":0.0,"destination":0.0},"endpoint_review_required":False,
        "diagnostics":{"large_detour_flag":False,"geometry_summary_mismatch":False},
        "reused_in_memory":False,"cache_age_s":0.0}
    return planning.assemble(req,prepared,[leg])


def test_budget_all_estimates_fit():
    # user-fixed 30 min stay + 10 min travel = 40 min
    plan=_plan(60,stay_minutes=30)
    assert plan["budget_check"]["status"]=="all_estimates_fit"
    assert plan["budget_check"]["reference_within_budget"] is True
    assert plan["budget_check"]["slack_s"]==1200


def test_budget_all_estimates_exceed():
    # unknown place range 15/30/60 + 10 travel; even 25 min minimum exceeds 20.
    plan=_plan(20)
    assert plan["budget_check"]["status"]=="all_estimates_exceed"
    assert plan["budget_check"]["reference_within_budget"] is False


def test_budget_reference_exceeds_but_shorter_stay_can_fit():
    # unknown place: total range 25/40/70 min. 35 min budget fits short, not reference.
    plan=_plan(35)
    assert plan["budget_check"]["status"]=="reference_exceeds"
    assert plan["budget_check"]["reference_within_budget"] is False


def test_budget_reference_fits_but_upper_range_exceeds():
    # unknown place: total range 25/40/70 min. 50 min fits reference, not upper range.
    plan=_plan(50)
    assert plan["budget_check"]["status"]=="reference_fits_upper_exceeds"
    assert plan["budget_check"]["reference_within_budget"] is True
    assert plan["budget_check"]["range_within_budget"] is False
