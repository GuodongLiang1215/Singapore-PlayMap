"""Deep semantic seams for auditable dialogue constraints.

The provider-facing interpretation remains a proposal.  This module owns the
small deterministic rules that decide which constraints stay active and whether
the executable command set accounts for explicit hard requirements.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from .models import ActiveConstraint, Interpretation, SemanticSegment


class SemanticConsistencyError(ValueError):
    """Raised when a hard segment is not represented by a safe proposal."""

    code = 'SEMANTIC_COMMAND_MISMATCH'


def _value_key(value):
    if isinstance(value, list):
        return tuple(value)
    if isinstance(value, str):
        return value.casefold().strip()
    return value


def _deterministic_status(segment, *, hard_conflict: bool):
    if hard_conflict:
        return 'conflicting'
    # The provider cannot prove venue availability.  The local contract keeps
    # this requirement unresolved until a deterministic checker supplies it.
    if segment.name in {'opening_hours', 'must_be_open', 'last_admission'}:
        return 'unresolved'
    # A model claim of satisfied/unsupported is not evidence.  The reducer
    # starts user constraints at understood and later validators may advance it.
    return 'understood'


def merge_active_constraints(active, segments, *, turn_index: int):
    """Apply one turn's constraint segments to the active ledger.

    A later turn replaces an earlier value in the same scope.  Two different
    hard values in the same turn remain visible and are marked conflicting;
    neither is silently discarded.
    """
    current = [item.model_copy(deep=True) for item in active]
    incoming = [segment for segment in segments if segment.type == 'constraint']
    grouped = defaultdict(list)
    for segment in incoming:
        grouped[(segment.name, segment.scope)].append(segment)

    for key, rows in grouped.items():
        existing = [item for item in current if (item.name, item.scope) == key]
        if all(row.value is None for row in rows):
            current = [item for item in current if (item.name, item.scope) != key]
            continue
        incoming_hard = any(row.hardness == 'hard' for row in rows)
        # A soft preference cannot silently erase an older explicit hard
        # requirement.  A new hard value is an intentional replacement and
        # therefore removes the older value before being recorded.
        if incoming_hard or not any(item.hardness == 'hard' for item in existing):
            current = [item for item in current if (item.name, item.scope) != key]
        values = {_value_key(row.value) for row in rows}
        hard_conflict = len(values) > 1 and any(row.hardness == 'hard' for row in rows)
        for row in rows if hard_conflict else rows[-1:]:
            current.append(ActiveConstraint(
                name=row.name,
                value=deepcopy(row.value),
                scope=row.scope,
                hardness=row.hardness,
                status=_deterministic_status(row, hard_conflict=hard_conflict),
                source=row.source,
                quote=row.quote,
                turn_index=turn_index,
            ))
    return current


def _quote_supported(segment: SemanticSegment, parsed: Interpretation) -> bool:
    return any(getattr(action, 'quote', '') == segment.quote for action in parsed.actions)


def _location_constraint_supported(segment: SemanticSegment, parsed: Interpretation) -> bool:
    value = str(segment.value or '').casefold().strip()
    if not value:
        return False
    for action in parsed.actions:
        place = getattr(action, 'place', None)
        if place is None:
            continue
        haystack = ' '.join([
            place.query,
            *(place.keywords or []),
            *(place.categories or []),
        ]).casefold()
        if value in haystack and (not segment.scope.startswith('visit:') or _quote_supported(segment, parsed)):
            return True
    return False


def _constraint_supported(segment: SemanticSegment, parsed: Interpretation) -> bool:
    if segment.name == 'max_walk_minutes':
        try:
            return parsed.max_walk_minutes == int(segment.value)
        except (TypeError, ValueError):
            return False
    if segment.name in {'place_type', 'category', 'keyword', 'takeaway'}:
        return _location_constraint_supported(segment, parsed) or _quote_supported(segment, parsed)
    # A question is an explicit safe outcome for a hard requirement that the
    # current command contract cannot execute yet, such as opening hours.
    return bool(parsed.questions)


def validate_segment_command_consistency(segments, parsed: Interpretation):
    """Fail closed when an explicit hard segment has no safe representation."""
    conflicts = [segment for segment in segments
                 if segment.type == 'conflict' or segment.status == 'conflicting']
    if conflicts and not parsed.questions:
        raise SemanticConsistencyError('Conflicting semantic constraints need a question')
    for segment in segments:
        if segment.type != 'constraint' or segment.hardness != 'hard':
            continue
        if segment.status == 'conflicting':
            if parsed.questions:
                continue
            raise SemanticConsistencyError('Conflicting hard constraint needs a question')
        if not _constraint_supported(segment, parsed):
            raise SemanticConsistencyError('Hard constraint has no supporting command')
    return None
