"""Conservative opening-hours feasibility checks for itinerary visits.

This is a bounded weekly grammar, not a complete OSM opening_hours parser.
Weekday lists/ranges and comma-separated clock intervals are supported. Rules
with exceptions, annotations or overlapping weekday clauses remain unknown.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8), 'Asia/Singapore')
DAY_NAMES = ('Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su')
DAY_TOKEN = r'(?:Mo|Tu|We|Th|Fr|Sa|Su)'
DAY_RANGE = DAY_TOKEN + r'(?:\s*-\s*' + DAY_TOKEN + r')?'
WEEKDAY_RULE = re.compile(r'(' + DAY_RANGE + r'(?:\s*,\s*' + DAY_RANGE + r')*)\s+(.+)')
CLOCK_INTERVAL = re.compile(r'(\d{1,2}):(\d{2})\s*[–-]\s*(次日\s*)?(\d{1,2}):(\d{2})')
ALWAYS_TERMS = {'24 小时', '24小时', '全天开放', '24/7', '24 hours', 'open all day'}


def _weekdays(selector: str) -> set[int]:
    result = set()
    for part in selector.split(','):
        ends = [DAY_NAMES.index(day.strip()) for day in part.split('-')]
        days = {(ends[0] + offset) % 7
                for offset in range((ends[-1] - ends[0]) % 7 + 1)}
        if result & days:
            raise ValueError('complex_schedule')
        result.update(days)
    return result


def _intervals(text: str) -> list[tuple[int, int]]:
    if text in {'off', 'closed'}:
        return []
    intervals = []
    for part in text.split(','):
        match = CLOCK_INTERVAL.fullmatch(part.strip())
        if not match:
            raise ValueError('complex_schedule')
        oh, om, next_day, ch, cm = match.groups()
        oh, om, ch, cm = map(int, (oh, om, ch, cm))
        if oh > 23 or ch > 24 or om > 59 or cm > 59 or ch == 24 and cm != 0:
            raise ValueError('invalid_clock')
        start, end = oh * 60 + om, ch * 60 + cm
        if next_day or end < start:
            end += 1440
        # Equal clocks are ambiguous; use 00:00-24:00 for a full day.
        if not 0 < end - start <= 1440:
            raise ValueError('invalid_clock')
        intervals.append((start, end))
    return intervals


def parse_simple_hours(display: str | None) -> dict:
    if not display:
        return {"kind": "unknown", "reason": "hours_missing"}
    text = display.strip()
    # This known suffix only identifies the source. Do not strip arbitrary
    # parentheses: they can contain closures or facility-specific exceptions.
    schedule = re.sub(r'(?:（OSM 参考）|\s*\(OSM reference\))$', '', text).strip()
    if schedule.casefold() in ALWAYS_TERMS:
        return {"kind": "always_open"}
    days = [[] for _ in range(7)]
    assigned = set()
    clause_by_day = [None] * 7
    clauses = schedule.split(';')
    try:
        for clause_index, clause in enumerate(clauses):
            clause = clause.strip()
            match = WEEKDAY_RULE.fullmatch(clause)
            if match:
                selected = _weekdays(match[1])
                intervals = _intervals(match[2].strip())
            elif len(clauses) == 1:
                selected = set(range(7))
                intervals = _intervals(re.sub(r'^(?:每日\s*|Daily\s+)', '', clause))
            else:
                raise ValueError('complex_schedule')
            # OSM semicolons can override earlier rules. Only disjoint weekday
            # clauses are supported, so an exception is never silently unioned.
            if assigned & selected:
                raise ValueError('complex_schedule')
            assigned.update(selected)
            for day in selected:
                days[day] = intervals
                clause_by_day[day] = clause_index
        # A different next-day normal clause can override an overnight spill,
        # even when that clause is not 'off'. Keep these exceptions unknown.
        if any(any(end > 1440 for _, end in intervals)
               and clause_by_day[(day + 1) % 7] not in (None, clause_by_day[day])
               for day, intervals in enumerate(days)):
            raise ValueError('complex_schedule')
    except ValueError as error:
        return {'kind': 'unknown', 'reason': str(error), 'display': text}
    result = {'kind': 'weekly_intervals', 'day_intervals': days, 'display': text}
    # Retain the existing simple-daily parser contract for its callers.
    if len(days[0]) == 1 and all(day == days[0] for day in days):
        start, end = days[0][0]
        result.update(kind='daily_interval', open_minute=start,
                      close_minute=end - 1440 if end > 1440 else end,
                      overnight=end > 1440)
    return result


def evaluate_visit_hours(*, arrival_at: str | None, departure_at: str | None,
                         opening_hours_display: str | None) -> dict:
    rule = parse_simple_hours(opening_hours_display)
    base = {"opening_hours_display": opening_hours_display, "rule_kind": rule["kind"]}
    if not arrival_at or not departure_at:
        return {**base, "status": "unknown", "reason": "departure_time_required"}
    try:
        arrival = datetime.fromisoformat(arrival_at)
        departure = datetime.fromisoformat(departure_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in (arrival, departure)):
            raise ValueError('Timezone required')
        arrival, departure = arrival.astimezone(SGT), departure.astimezone(SGT)
        if departure < arrival:
            raise ValueError('Departure must not precede arrival')
    except (ValueError, TypeError, OverflowError):
        return {**base, "status": "unknown", "reason": "invalid_itinerary_time"}
    if rule["kind"] == "unknown":
        return {**base, "status": "unknown", "reason": rule.get("reason", "unknown")}
    if rule["kind"] == "always_open":
        return {**base, "status": "feasible"}

    midnight = arrival.replace(hour=0, minute=0, second=0, microsecond=0)
    start, end = (arrival - midnight).total_seconds(), (departure - midnight).total_seconds()
    # Include yesterday's overnight spill and a full future week. Numeric
    # offsets avoid date-overflow edge cases and keep arbitrarily long visits
    # bounded: a continuous full week proves this weekly rule has no gap.
    spans = sorted((day * 86400 + lo * 60, day * 86400 + hi * 60)
                   for day in range(-1, 9)
                   for lo, hi in rule['day_intervals'][(arrival.weekday() + day) % 7])
    merged = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    containing = next((span for span in merged if span[0] <= start < span[1]), None)
    if containing is None:
        return {**base, "status": "closed_at_arrival", "reason": "arrival_outside_open_interval"}
    if end > containing[1] and containing[1] < start + 7 * 86400:
        return {**base, "status": "insufficient_time", "reason": "visit_extends_past_closing"}
    return {**base, "status": "feasible"}
