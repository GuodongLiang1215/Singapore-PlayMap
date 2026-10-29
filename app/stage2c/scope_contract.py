"""Canonical provider scope tokens shared by generation and local validation.

Ordinals keep the existing 1..99 wire contract. Whether a referenced visit
exists and whether a scope is meaningful for a field remain separate checks.
Saved-state visit_id scopes are deliberately not provider permissions.
"""
from __future__ import annotations


PROVIDER_SCOPES = (
    'global', 'origin', 'finish', 'future_visits', 'visit:new',
    *(f'{prefix}:{number}' for prefix in ('visit', 'visit:new', 'time_window')
      for number in range(1, 100)),
)
_PROVIDER_SCOPE_SET = frozenset(PROVIDER_SCOPES)

GLOBAL_SCOPE_REQUIREMENTS = frozenset({
    'max_walk_minutes', 'budget_minutes', 'departure_at', 'finish_by',
    'transport_mode', 'finish_policy',
})

SCOPE_DESCRIPTION = (
    'Use only the listed canonical tokens. '
    + ', '.join(sorted(GLOBAL_SCOPE_REQUIREMENTS))
    + ' always use global, including dated departure/finish requirements. '
    'origin and finish identify location endpoints; origin is not departure time. '
    'For an existing visit use visit:N, where N is its 1-based position in the current draft. '
    'For a requested new place use visit:new:N, where N counts add_visit entries in order, '
    'not all commands; keep each new place requirement on its own new-visit scope. '
    'visit:new is unambiguous only when there is exactly one new visit. '
    'Per-visit stay_minutes and takeaway use the applicable visit scope. '
    'excluded_topic uses global or future_visits. '
    'time_window:N retains a separately stated unsupported time-window requirement; '
    'it is not the scope for departure_at, finish_by or budget_minutes. '
    'Do not use dates, timestamps, place names, internal visit IDs or scope aliases.'
)


def is_provider_scope(value: object) -> bool:
    """Validate canonical syntax only, without aliases or inferred targets."""
    return isinstance(value, str) and value in _PROVIDER_SCOPE_SET
