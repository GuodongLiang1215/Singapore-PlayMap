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
