"""Conservative opening-hours feasibility checks for itinerary visits.

Only rules that can be interpreted unambiguously are evaluated. Complex or
facility-specific displays remain unknown rather than being guessed.
"""
from __future__ import annotations

import re
from datetime import datetime, time

DAILY_RE = re.compile(r"(?:每日\s*)?(\d{1,2}):(\d{2})[–-](?:次日\s*)?(\d{1,2}):(\d{2})")
ALWAYS_TERMS = ("24 小时", "24小时", "全天开放")


def _minutes(h: int, m: int) -> int:
    return h * 60 + m


def parse_simple_hours(display: str | None) -> dict:
    if not display:
        return {"kind": "unknown", "reason": "hours_missing"}
    text = display.strip()
    if any(term in text for term in ALWAYS_TERMS):
        # Mixed facility descriptions such as "全天开放（信息柜台...）" still
        # describe the selected public place as always open.
        return {"kind": "always_open"}

    # Do not pretend a single interval represents a complex rule.
    if any(token in text for token in ("；", "、", "周五", "周六", "周日", "闭馆", "维护")):
        return {"kind": "unknown", "reason": "complex_schedule", "display": text}

    m = DAILY_RE.search(text)
    if not m:
        return {"kind": "unknown", "reason": "unparsed_schedule", "display": text}
    oh, om, ch, cm = map(int, m.groups())
    if oh > 23 or ch > 23 or om > 59 or cm > 59:
        return {"kind": "unknown", "reason": "invalid_clock", "display": text}
    return {
        "kind": "daily_interval",
        "open_minute": _minutes(oh, om),
        "close_minute": _minutes(ch, cm),
        "overnight": "次日" in m.group(0) or _minutes(ch, cm) <= _minutes(oh, om),
        "display": text,
    }


def evaluate_visit_hours(*, arrival_at: str | None, departure_at: str | None,
                         opening_hours_display: str | None) -> dict:
    rule = parse_simple_hours(opening_hours_display)
    base = {"opening_hours_display": opening_hours_display, "rule_kind": rule["kind"]}
    if not arrival_at or not departure_at:
        return {**base, "status": "unknown", "reason": "departure_time_required"}
    if rule["kind"] == "unknown":
        return {**base, "status": "unknown", "reason": rule.get("reason", "unknown")}
    if rule["kind"] == "always_open":
        return {**base, "status": "feasible"}

    try:
        arrival = datetime.fromisoformat(arrival_at)
        departure = datetime.fromisoformat(departure_at)
    except ValueError:
        return {**base, "status": "unknown", "reason": "invalid_itinerary_time"}

    start = arrival.hour * 60 + arrival.minute
    end = departure.hour * 60 + departure.minute
    open_m, close_m = rule["open_minute"], rule["close_minute"]

    if rule["overnight"]:
        # Map after-midnight times onto the same service day.
        if start < open_m:
            start += 1440
        if end < open_m or departure.date() > arrival.date():
            end += 1440
        close_m += 1440
    elif departure.date() > arrival.date():
        return {**base, "status": "insufficient_time", "reason": "visit_extends_past_closing"}

    if start < open_m or start >= close_m:
        return {**base, "status": "closed_at_arrival", "reason": "arrival_outside_open_interval"}
    if end > close_m:
        return {**base, "status": "insufficient_time", "reason": "visit_extends_past_closing"}
    return {**base, "status": "feasible"}
