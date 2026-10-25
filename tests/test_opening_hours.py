from app.stage2b.opening_hours import parse_simple_hours, evaluate_visit_hours


def test_parse_daily_interval():
    rule = parse_simple_hours("每日 10:00–18:00")
    assert rule["kind"] == "daily_interval"
    assert rule["open_minute"] == 600
    assert rule["close_minute"] == 1080


def test_always_open_is_feasible():
    result = evaluate_visit_hours(
        arrival_at="2026-10-25T23:00:00+08:00",
        departure_at="2026-10-26T00:30:00+08:00",
        opening_hours_display="24 小时",
    )
    assert result["status"] == "feasible"


def test_closed_at_arrival():
    result = evaluate_visit_hours(
        arrival_at="2026-10-25T19:10:00+08:00",
        departure_at="2026-10-25T19:40:00+08:00",
        opening_hours_display="每日 10:00–18:00",
    )
    assert result["status"] == "closed_at_arrival"


def test_visit_extends_past_closing():
    result = evaluate_visit_hours(
        arrival_at="2026-10-25T17:30:00+08:00",
        departure_at="2026-10-25T18:30:00+08:00",
        opening_hours_display="每日 10:00–18:00",
    )
    assert result["status"] == "insufficient_time"


def test_complex_schedule_is_unknown_not_guessed():
    result = evaluate_visit_hours(
        arrival_at="2026-10-30T18:00:00+08:00",
        departure_at="2026-10-30T19:00:00+08:00",
        opening_hours_display="每日 10:00–19:00；周五至 21:00",
    )
    assert result["status"] == "unknown"
    assert result["reason"] == "complex_schedule"


def test_no_departure_time_means_unknown():
    result = evaluate_visit_hours(
        arrival_at=None,
        departure_at=None,
        opening_hours_display="每日 10:00–18:00",
    )
    assert result["status"] == "unknown"
    assert result["reason"] == "departure_time_required"


def test_planning_integration_marks_visit_open(monkeypatch):
    from datetime import datetime
    from app.stage2b import planning
    from app.stage2b.models import PlanRequest, PlacePoint, Visit, TimeSettings

    origin = PlacePoint(latitude=1.30, longitude=103.85, label="Origin", source="user_map", confirmed=True)
    place = PlacePoint(
        latitude=1.31, longitude=103.86, label="Museum", source="catalogue_representative",
        entity_id="museum-1", catalogue_build_id="build-1", confirmed=True,
    )
    req = PlanRequest(
        session_id="c" * 32, origin=origin, visits=[Visit(visit_id="v1", point=place, stay_minutes=60)],
        mode="walk", time=TimeSettings(mode="estimate", departure_at=datetime.fromisoformat("2026-10-25T15:00:00+08:00")),
    )

    def resolver(uid, build):
        return {"entity": {
            "entity_id": uid, "default_exploration_visible": True,
            "latitude": 1.31, "longitude": 103.86, "display_title": "Museum",
            "category": "tourism", "suggested_duration": None, "regular_hours_evidence": [],
            "opening_hours_display": "每日 10:00–18:00",
        }}

    prepared = planning.prepare(req, resolver)
    leg = {
        "leg_id": "leg-1", "index": 0, "origin": origin.model_dump(), "destination": place.model_dump(),
        "mode": "walk", "kind": "provider_route", "distance_m": 700, "duration_s": 600,
        "geometry": {"type": "LineString", "coordinates": [[103.85,1.30],[103.86,1.31]]},
        "endpoint_review_required": False,
        "diagnostics": {"large_detour_flag": False, "geometry_summary_mismatch": False},
    }
    plan = planning.assemble(req, prepared, [leg])
    assert plan["visits"][0]["arrival_at"].startswith("2026-10-25T15:10")
    assert plan["visits"][0]["opening_status"] == "feasible"
    assert plan["opening_hours_checked"] is True
    assert plan["itinerary_feasible"] is True


def test_planning_without_departure_time_does_not_claim_hours_checked():
    from app.stage2b import planning
    from app.stage2b.models import PlanRequest, PlacePoint, Visit

    origin = PlacePoint(latitude=1.30, longitude=103.85, label="Origin", source="user_map", confirmed=True)
    place = PlacePoint(
        latitude=1.31, longitude=103.86, label="Museum", source="catalogue_representative",
        entity_id="museum-1", catalogue_build_id="build-1", confirmed=True,
    )
    req = PlanRequest(
        session_id="d" * 32, origin=origin,
        visits=[Visit(visit_id="v1", point=place, stay_minutes=60)], mode="walk",
    )

    def resolver(uid, build):
        return {"entity": {
            "entity_id": uid, "default_exploration_visible": True,
            "latitude": 1.31, "longitude": 103.86, "display_title": "Museum",
            "category": "tourism", "suggested_duration": None, "regular_hours_evidence": [],
            "opening_hours_display": "每日 10:00–18:00",
        }}

    prepared = planning.prepare(req, resolver)
    leg = {
        "leg_id": "leg-1", "index": 0, "origin": origin.model_dump(), "destination": place.model_dump(),
        "mode": "walk", "kind": "provider_route", "distance_m": 700, "duration_s": 600,
        "geometry": {"type": "LineString", "coordinates": [[103.85,1.30],[103.86,1.31]]},
        "endpoint_review_required": False,
        "diagnostics": {"large_detour_flag": False, "geometry_summary_mismatch": False},
    }
    plan = planning.assemble(req, prepared, [leg])
    assert plan["visits"][0]["opening_status"] == "unknown"
    assert plan["opening_hours_checked"] is False
    assert plan["itinerary_feasible"] is None


def test_onemap_matched_metadata_closes_artscience_after_1800():
    from datetime import datetime
    from app.stage2b import planning
    from app.stage2b.models import PlanRequest, PlacePoint, Visit, TimeSettings

    origin=PlacePoint(latitude=1.289842,longitude=103.778075,label="Origin",source="user_map",confirmed=True)
    museum=PlacePoint(latitude=1.286223958762737,longitude=103.8592711215884,
        label="MARINA BAY SANDS (ARTSCIENCE MUSEUM)",source="onemap_search",confirmed=True)
    req=PlanRequest(session_id="e"*32,origin=origin,
        visits=[Visit(visit_id="v1",point=museum,stay_minutes=60)],
        mode="drive",time=TimeSettings(mode="estimate",
            departure_at=datetime.fromisoformat("2026-10-25T20:11:00+08:00")))

    def matcher(label,lat,lon):
        return {"entity_id":"place:stb_attractions:1027","build_id":"build-1",
                "distance_m":24.99,"name_score":0.94}
    def resolver(uid,build):
        return {"entity":{"category":"tourism",
            "suggested_duration":{"short":45,"regular":90,"long":120,"basis":"estimate"},
            "regular_hours_evidence":[],"opening_hours_display":"每日 9:00–18:00"}}

    prepared=planning.prepare(req,resolver,matcher)
    leg={"leg_id":"leg-1","index":0,"kind":"provider_route",
        "origin":origin.model_dump(),"destination":museum.model_dump(),"provider":"OneMap","mode":"drive",
        "queried_at":None,"distance_m":12600,"duration_s":1337,
        "geometry":{"type":"LineString","coordinates":[[103.778075,1.289842],[103.8592711215884,1.286223958762737]]},
        "endpoint_offsets_m":{"origin":0.0,"destination":0.0},"endpoint_review_required":False,
        "diagnostics":{"large_detour_flag":False,"geometry_summary_mismatch":False},"reused_in_memory":False,"cache_age_s":0.0}
    plan=planning.assemble(req,prepared,[leg])
    visit=plan["visits"][0]
    assert visit["category"]=="tourism"
    assert visit["opening_hours_display"]=="每日 9:00–18:00"
    assert visit["arrival_at"].startswith("2026-10-25T20:33")
    assert visit["opening_status"]=="closed_at_arrival"
    assert plan["opening_hours_checked"] is True
    assert plan["itinerary_feasible"] is False
