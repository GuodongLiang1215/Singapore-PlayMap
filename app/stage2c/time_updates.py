"""F5: atomic partial time updates, independent of provider output formatting.

A stated duration determines budget semantics; callers need not repeat a redundant
UI mode. Missing/null fields are not requests to erase existing values. Mutually
inconsistent *meaningful* fields still fail. This module never parses user prose,
selects places, invents times, calls a model or mutates the adopted itinerary.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
from pydantic import ValidationError
from app.stage2b.models import TimeSettings, SGT
from .models import TimeAction

TIME_CONTRACT = 'partial_time_patch_v1'
MODES = frozenset({'estimate', 'budget', 'window'})
SOURCES = frozenset({'unchanged', 'explicit_mode', 'budget_value', 'finish_value'})
REASONS = frozenset({'conflicting_values', 'contradictory_fields', 'ambiguous_mode_switch',
                    'missing_budget', 'missing_departure', 'missing_finish', 'invalid_time'})
FLAGS = ('budget_provided', 'departure_provided', 'finish_provided',
         'clear_departure_requested', 'buffer_provided', 'mode_derived',
         'budget_present_after', 'departure_present_after', 'finish_present_after',
         'final_validation_passed')


def _enum(value, allowed):
    return value if isinstance(value, str) and value in allowed else None


def safe_time_diagnostic(value: Any) -> dict | None:
    """Only enum labels, bounded counts and booleans; never amounts/dates or text."""
    if not isinstance(value, dict):
        return None
    count = value.get('time_command_count')
    out = {
        'contract': TIME_CONTRACT,
        'time_command_count': count if type(count) is int and 0 <= count <= 12 else None,
        'base_mode': _enum(value.get('base_mode'), MODES),
        'result_mode': _enum(value.get('result_mode'), MODES),
        'mode_source': _enum(value.get('mode_source'), SOURCES),
        'failure_reason': _enum(value.get('failure_reason'), REASONS),
        'atomic': True, 'values_included': False,
    }
    for key in FLAGS:
        out[key] = value.get(key) if type(value.get(key)) is bool else None
    return out


class TimeUpdateError(ValueError):
    def __init__(self, code: str, message: str, diagnostic: dict):
        super().__init__(message)
        self.code, self.message = code, message
        self.diagnostic = safe_time_diagnostic(diagnostic)


@dataclass(frozen=True)
class TimeUpdate:
    settings: TimeSettings
    diagnostic: dict
    changes: list[str]


def merge_time_updates(old: TimeSettings, actions: Iterable[Any]) -> TimeUpdate:
    """Compose the turn's explicit time fields, then validate the complete state.

    Repeated identical fields are harmless. Different values for one field, or
    simultaneous duration+deadline restrictions that the current engine cannot
    represent, require clarification rather than last-write-wins. A switch from
    an existing hard-constraint kind needs an explicit mode; no old deadline is
    silently erased by an otherwise partial update.
    """
    commands = [a for a in actions if isinstance(a, TimeAction)]
    d = old.model_dump(mode='json')
    diagnostic = {
        'time_command_count': len(commands), 'base_mode': old.mode,
        'result_mode': old.mode, 'mode_source': 'unchanged', 'failure_reason': None,
        **{key: False for key in FLAGS},
    }
    for key, field in (('budget_provided', 'budget_minutes'), ('departure_provided', 'departure_at'),
                       ('finish_provided', 'finish_by'), ('buffer_provided', 'buffer_minutes')):
        diagnostic[key] = any(getattr(a, field) is not None for a in commands)
    diagnostic['clear_departure_requested'] = any(a.clear_departure for a in commands)

    def fail(reason, message, code='TIME_NEEDS_CLARIFICATION'):
        diagnostic.update(result_mode=None, final_validation_passed=False, failure_reason=reason)
        raise TimeUpdateError(code, message, diagnostic)

    patch = {}
    for a in commands:
        for field in ('mode', 'budget_minutes', 'departure_at', 'finish_by', 'buffer_minutes'):
            value = getattr(a, field)
            if value is None:  # null is not a request to clear an existing value
                continue
            if field in {'departure_at','finish_by'}:
                try:
                    instant=datetime.fromisoformat(value)
                    if instant.utcoffset() is None:raise ValueError('Missing offset')
                    value=instant.astimezone(SGT).isoformat()
                except (TypeError,ValueError):
                    fail('invalid_time','Provide a valid time with an explicit date and time zone. A date will not be assumed.')
            if field in patch and patch[field] != value:
                fail('conflicting_values', 'This message contains conflicting time settings. Confirm the budget or start and end times. Your itinerary is unchanged.')
            patch[field] = value
    if diagnostic['clear_departure_requested'] and 'departure_at' in patch:
        fail('contradictory_fields', 'This message both clears and sets the departure time. Please choose one.')
    has_budget, has_finish = 'budget_minutes' in patch, 'finish_by' in patch
    explicit = patch.get('mode')
    if has_budget and has_finish:
        fail('contradictory_fields', 'This message sets both a total-time budget and an end time. The planner supports one time constraint at a time. Please choose which to use.')
    if ((explicit == 'estimate' and (has_budget or has_finish)) or
        (explicit == 'budget' and has_finish) or (explicit == 'window' and has_budget)):
        fail('contradictory_fields', 'The time mode conflicts with the supplied budget or end time. Your existing requirements were not overwritten.', 'INCONSISTENT_TIME')

    mode = explicit or ('budget' if has_budget else 'window' if has_finish else old.mode)
    source = ('explicit_mode' if explicit else 'budget_value' if has_budget else
              'finish_value' if has_finish else 'unchanged')
    diagnostic.update(result_mode=mode, mode_source=source,
                      mode_derived=explicit is None and (has_budget or has_finish))
    if explicit is None and mode != old.mode and old.mode in {'budget', 'window'}:
        fail('ambiguous_mode_switch', 'A time constraint is already active. Confirm whether the new one should replace it or whether you intend to keep both. The existing requirement has not been removed.')
    d['mode'] = mode
    if mode == 'estimate':
        d.update(budget_minutes=None, finish_by=None)
    elif mode == 'budget':
        d['finish_by'] = None
        # Same-mode partial update preserves the previous budget; no hidden default.
    elif mode == 'window':
        d['budget_minutes'] = None
    for field in ('budget_minutes', 'departure_at', 'finish_by', 'buffer_minutes'):
        if field in patch:
            d[field] = patch[field]
    if diagnostic['clear_departure_requested']:
        d['departure_at'] = None
    if mode == 'budget' and d['budget_minutes'] is None:
        fail('missing_budget', 'Specify the available minutes or hours to set a total-time budget. No default budget will be assumed.')
    if mode == 'window' and d['departure_at'] is None:
        fail('missing_departure', 'An end time is set. Confirm the departure date and time; departure will not be assumed to be now.')
    if mode == 'window' and d['finish_by'] is None:
        fail('missing_finish', 'Confirm the end date and time to use a time window.')
    try:
        result = TimeSettings.model_validate(d)
    except ValidationError:
        fail('invalid_time', 'Check the dates and time zones, and make sure the end is later than the start. Trips crossing midnight must specify the next day.')
    diagnostic.update(final_validation_passed=True, budget_present_after=result.budget_minutes is not None,
                      departure_present_after=result.departure_at is not None,
                      finish_present_after=result.finish_by is not None)
    changes = []
    if commands:
        if result.model_dump() == old.model_dump():
            changes.append('Time settings are unchanged; the existing budget, departure time and buffer are retained.')
        else:
            if result.mode == 'budget':
                changes.append(f'Total-time budget → {result.budget_minutes} minutes (pending confirmation and route calculation)')
            elif result.mode == 'window':
                changes.append('Time window → '+result.departure_at.strftime('%Y-%m-%d %H:%M')+' to '+
                               result.finish_by.strftime('%Y-%m-%d %H:%M')+' (Singapore time)')
            else:
                changes.append('Time mode → no total-time limit; estimate duration from the route and stays')
            if old.mode != result.mode and old.mode == 'window':
                changes.append('This proposal replaces the existing time window and removes its end-time limit. Confirm before applying.')
            elif old.mode != result.mode and old.mode == 'budget':
                changes.append('This proposal replaces the existing total-time budget. Confirm before applying.')
            if result.departure_at != old.departure_at and result.mode != 'window':
                changes.append('Departure time → '+(result.departure_at.strftime('%Y-%m-%d %H:%M')+' (Singapore time)'
                                             if result.departure_at else 'not specified'))
            if result.buffer_minutes != old.buffer_minutes:
                changes.append(f'Extra buffer → {result.buffer_minutes} minutes')
    return TimeUpdate(result, safe_time_diagnostic(diagnostic), changes)
