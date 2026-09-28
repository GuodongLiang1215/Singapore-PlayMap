def validate_max_walking_time(legs, max_minutes):
    violations = []

    for leg in legs:
        duration_min = leg["duration_s"] / 60

        if duration_min > max_minutes:
            exceeded_by = duration_min - max_minutes

            violations.append({
                "leg_id": leg["leg_id"],
                "duration_minutes": duration_min,
                "exceeded_by_minutes": exceeded_by
            })

    if violations:
        return {
            "status": "violation",
            "violations": violations
        }

    return {
        "status": "ok",
        "violations": []
    }