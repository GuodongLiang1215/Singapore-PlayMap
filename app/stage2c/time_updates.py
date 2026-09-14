"""F5: atomic partial time updates, independent of provider output formatting.

A stated duration determines budget semantics; callers need not repeat a redundant
UI mode. Missing/null fields are not requests to erase existing values. Mutually
inconsistent *meaningful* fields still fail. This module never parses user prose,
selects places, invents times, calls a model or mutates the adopted itinerary.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Iterable
from pydantic import ValidationError
from app.stage2b.models import TimeSettings
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
            if field in patch and patch[field] != value:
                fail('conflicting_values', '同一轮包含不同的时间设置。请确认最终预算或起止时间；原行程未修改。')
            patch[field] = value
    if diagnostic['clear_departure_requested'] and 'departure_at' in patch:
        fail('contradictory_fields', '这轮同时要求清除和设置出发时间，请确认保留哪一种。')
    has_budget, has_finish = 'budget_minutes' in patch, 'finish_by' in patch
    explicit = patch.get('mode')
    if has_budget and has_finish:
        fail('contradictory_fields', '这轮同时设置总时长预算和结束时刻。当前时间模块只能选择一种约束；请确认使用哪一种，不会悄悄删除另一项。')
    if ((explicit == 'estimate' and (has_budget or has_finish)) or
        (explicit == 'budget' and has_finish) or (explicit == 'window' and has_budget)):
        fail('contradictory_fields', '时间模式与给出的预算或结束时刻矛盾，未自动覆盖有实际内容的要求。', 'INCONSISTENT_TIME')

    mode = explicit or ('budget' if has_budget else 'window' if has_finish else old.mode)
    source = ('explicit_mode' if explicit else 'budget_value' if has_budget else
              'finish_value' if has_finish else 'unchanged')
    diagnostic.update(result_mode=mode, mode_source=source,
                      mode_derived=explicit is None and (has_budget or has_finish))
    if explicit is None and mode != old.mode and old.mode in {'budget', 'window'}:
        fail('ambiguous_mode_switch', '已有一种明确时间约束。本轮新增了另一种，请确认是替换原约束还是同时保留；暂不自动删除原要求。')
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
        fail('missing_budget', '要按总时长预算安排，还需要明确可用多少分钟或小时；不会使用默认预算。')
    if mode == 'window' and d['departure_at'] is None:
        fail('missing_departure', '已有结束时刻，还需要确认出发日期和时间；不会擅自按现在出发。')
    if mode == 'window' and d['finish_by'] is None:
        fail('missing_finish', '要按起止时间安排，还需要确认结束日期和时间。')
    try:
        result = TimeSettings.model_validate(d)
    except ValidationError:
        fail('invalid_time', '时间条件未通过检查：请确认有效日期、明确时区和正向的起止时间；跨午夜需明确下一天。')
    diagnostic.update(final_validation_passed=True, budget_present_after=result.budget_minutes is not None,
                      departure_present_after=result.departure_at is not None,
                      finish_present_after=result.finish_by is not None)
    changes = []
    if commands:
        if result.model_dump() == old.model_dump():
            changes.append('时间条件保持不变；未清除已有预算、出发时间或预留')
        else:
            if result.mode == 'budget':
                changes.append(f'总时长预算 → {result.budget_minutes} 分钟（待采用，随后计算是否超时）')
            elif result.mode == 'window':
                changes.append('时间窗口 → '+result.departure_at.strftime('%Y-%m-%d %H:%M')+' 至 '+
                               result.finish_by.strftime('%Y-%m-%d %H:%M')+'（新加坡时间）')
            else:
                changes.append('时间模式 → 不限制总时长，按路线和停留计算建议时间')
            if old.mode != result.mode and old.mode == 'window':
                changes.append('这份草案将替换原起止窗口，原结束时刻不再作为限制；请确认再采用')
            elif old.mode != result.mode and old.mode == 'budget':
                changes.append('这份草案将替换原总时长预算；请确认再采用')
            if result.departure_at != old.departure_at and result.mode != 'window':
                changes.append('出发时间 → '+(result.departure_at.strftime('%Y-%m-%d %H:%M')+'（新加坡时间）'
                                             if result.departure_at else '未指定'))
            if result.buffer_minutes != old.buffer_minutes:
                changes.append(f'额外预留 → {result.buffer_minutes} 分钟')
    return TimeUpdate(result, safe_time_diagnostic(diagnostic), changes)
