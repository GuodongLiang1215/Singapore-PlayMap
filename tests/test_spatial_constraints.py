from app.stage2b.spatial_constraints import validate_max_walking_time
from app.stage2b.models import PlanRequest, PlacePoint, Visit
from app.stage2b.planning import assemble

def test_maximum_walking_time_violation():
    legs = [
        {
            "leg_id": "leg-1",
            "duration_s": 12 * 60
        }
    ]

    result = validate_max_walking_time(
        legs=legs,
        max_minutes=10
    )

    assert result["status"] == "violation"

def test_maximum_walking_time_ok():
    legs = [
        {
            "leg_id": "leg-1",
            "duration_s": 8 * 60
        }
    ]


    result = validate_max_walking_time(
        legs=legs,
        max_minutes=10
    )

    assert result["status"] == "ok"

def test_multiple_walking_time_violations():
    legs = [
        {"leg_id": "leg-1", "duration_s": 12 * 60},
        {"leg_id": "leg-2", "duration_s": 8 * 60},
        {"leg_id": "leg-3", "duration_s": 17 * 60},
    ]
    

    result = validate_max_walking_time(
        legs=legs,
        max_minutes=10
    )

    
    assert result["status"] == "violation"
    assert len(result["violations"]) == 2
    assert result["violations"][0]["leg_id"] == "leg-1"
    assert result["violations"][0]["duration_minutes"] == 12
    assert result["violations"][0]["exceeded_by_minutes"] == 2

def test_planning_assemble_reports_walking_violation():
    origin = PlacePoint(
        latitude=1.3000,
        longitude=103.8500,
        label="Origin",
        source="user_map",
        confirmed=True
    )

    destination = PlacePoint(
        latitude=1.3100,
        longitude=103.8600,
        label="Park A",
        source="user_map",
        confirmed=True
    )

    visit = Visit(
        visit_id="visit-1",
        point=destination,
        stay_minutes=30
    )

    request = PlanRequest(
        session_id="a" * 32,
        origin=origin,
        visits=[visit],
        mode="walk",
        max_walk_minutes=10
    )

    prepared = {
    "origin": origin,
    "finish": None,
    "visits": [
        {
            "visit_id": "visit-1",
            "point": destination.model_dump(),
            "stay_minutes": 30,
            "stay_range_minutes": [30, 30]
        }
    ],
    "points": [origin, destination]
    }

    legs = [
    {
        "leg_id": "leg-1",
        "index": 0,
        "kind": "provider_route",
        "origin": origin.model_dump(),
        "destination": destination.model_dump(),
        "provider": "OneMap",
        "mode": "walk",
        "queried_at": None,
        "distance_m": 1000.0,
        "duration_s": 12 * 60,
        "geometry": None,
        "endpoint_offsets_m": {
            "origin": 0.0,
            "destination": 0.0
        },
        "endpoint_review_required": False,
        "diagnostics": {
            "large_detour_flag": False,
            "geometry_summary_mismatch": False
        },
        "reused_in_memory": False,
        "cache_age_s": 0.0
    }
    ]

    result = assemble(
    request=request,
    prepared=prepared,
    legs=legs
    )

    assert result["walking_constraint_check"]["status"] == "violation"
    assert len(result["walking_constraint_check"]["violations"]) == 1
    assert (
        result["walking_constraint_check"]["violations"][0]["exceeded_by_minutes"]
        == 2
    )