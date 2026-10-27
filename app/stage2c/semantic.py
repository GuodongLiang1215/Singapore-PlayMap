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
    if segment.status in {'unresolved', 'unsupported', 'conflicting'}:
        return segment.status
    # A model claim of satisfied is not evidence.  The reducer
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
        if all(row.value is None for row in rows):
            current = [item for item in current if (item.name, item.scope) != key]
            continue
        incoming_hard = any(row.hardness == 'hard' for row in rows)
        # A soft preference cannot silently erase an older explicit hard
        # requirement.  A new hard value is an intentional replacement and
        # therefore removes the older value before being recorded.
        current = [item for item in current if (item.name, item.scope) != key or
                   item.hardness == 'hard' and not incoming_hard]
        hard_rows = [row for row in rows if row.hardness == 'hard']
        other_rows = [row for row in rows if row.hardness != 'hard']
        hard_conflict = len({_value_key(row.value) for row in hard_rows}) > 1
        selected = (hard_rows if hard_conflict else hard_rows[-1:]) + other_rows[-1:]
        for row in selected:
            current.append(ActiveConstraint(
                name=row.name,
                value=deepcopy(row.value),
                scope=row.scope,
                hardness=row.hardness,
                status=_deterministic_status(row, hard_conflict=hard_conflict and row.hardness == 'hard'),
                source=row.source,
                quote=row.quote,
                turn_index=turn_index,
            ))
    return current


def bind_constraint_scopes(rows, draft, *, new_visit_ids=()):
    """Resolve snapshot ordinals once so later moves cannot retarget memory."""
    scopes = {f'visit:{i}': f'visit_id:{visit.visit_id}' for i, visit in enumerate(draft.visits, 1)}
    scopes.update({f'visit:new:{i}': f'visit_id:{uid}' for i, uid in enumerate(new_visit_ids, 1)})
    if len(new_visit_ids) == 1:
        scopes['visit:new'] = f'visit_id:{new_visit_ids[0]}'
    return [row.model_copy(update={'scope': scopes.get(row.scope, row.scope)}, deep=True) for row in rows]


def _matches_scope(segment, action, draft, parsed=None):
    if segment.scope == 'global':
        return True
    if segment.scope == 'visit:new':
        return action.op == 'add_visit'
    if segment.scope.startswith('visit:new:') and parsed is not None:
        ordinal = segment.scope.removeprefix('visit:new:')
        additions = [item for item in parsed.actions if item.op == 'add_visit']
        return ordinal.isdigit() and 1 <= int(ordinal) <= len(additions) and action is additions[int(ordinal) - 1]
    if segment.scope.startswith('visit_id:'):
        return getattr(action, 'target_id', None) == segment.scope.removeprefix('visit_id:')
    if draft is not None and segment.scope.startswith('visit:'):
        position = segment.scope.removeprefix('visit:')
        if position.isdigit():
            index = int(position) - 1
            return 0 <= index < len(draft.visits) and getattr(action, 'target_id', None) == draft.visits[index].visit_id
        return getattr(action, 'target_id', None) == position
    return segment.quote in getattr(action, 'quote', '')


def _location_constraint_supported(segment: SemanticSegment, parsed: Interpretation, draft) -> bool:
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
        if value in haystack and _matches_scope(segment, action, draft, parsed):
            return True
    return False


def takeaway_intent_for_action(parsed, action, draft):
    rows = [s for s in parsed.semantic_segments if s.type == 'constraint' and
            s.name == 'takeaway' and type(s.value) is bool and _matches_scope(s, action, draft, parsed)]
    return rows[-1].value if rows else None


def _constraint_supported(segment: SemanticSegment, parsed: Interpretation, draft) -> bool:
    if segment.name == 'max_walk_minutes':
        if segment.value is None:
            return parsed.max_walk_minutes is None and 'max_walk_minutes' in parsed.model_fields_set
        try:
            return parsed.max_walk_minutes == int(segment.value)
        except (TypeError, ValueError):
            return False
    if segment.name in {'place_type', 'category', 'keyword'}:
        return _location_constraint_supported(segment, parsed, draft)
    if segment.name == 'takeaway':
        return type(segment.value) is bool and any(action.op in {'add_visit', 'replace_visit'} and
                   _matches_scope(segment, action, draft, parsed)
                   for action in parsed.actions)
    if segment.name == 'budget_minutes' and segment.status not in {'unsupported', 'unresolved'}:
        return any(action.op == 'set_time' and action.budget_minutes == segment.value
                   for action in parsed.actions)
    # A question is an explicit safe outcome for a hard requirement that the
    # current command contract cannot execute yet, such as opening hours.
    return bool(parsed.questions)


def validate_segment_command_consistency(segments, parsed: Interpretation, *, draft=None):
    """Fail closed when an explicit hard segment has no safe representation."""
    conflicts = [segment for segment in segments
                 if segment.type == 'conflict' or segment.status == 'conflicting']
    if conflicts and not parsed.questions:
        raise SemanticConsistencyError('Conflicting semantic constraints need a question')
    for segment in segments:
        if segment.type == 'preserve':
            for action in parsed.actions:
                preserves_visits = segment.name in {'preserve_visit', 'preserve_visits'}
                changes_visit = getattr(action, 'target_id', None) is not None
                if preserves_visits and action.op == 'clear_visits':
                    raise SemanticConsistencyError('Preserved visits cannot be cleared')
                if changes_visit and preserves_visits and _matches_scope(segment, action, draft, parsed):
                    raise SemanticConsistencyError('Preserved visit has a conflicting command')
                if segment.name == 'preserve_origin' and action.op == 'set_origin':
                    raise SemanticConsistencyError('Preserved origin has a conflicting command')
        if segment.type != 'constraint' or segment.hardness != 'hard':
            continue
        if segment.status == 'conflicting':
            if parsed.questions:
                continue
            raise SemanticConsistencyError('Conflicting hard constraint needs a question')
        if not _constraint_supported(segment, parsed, draft):
            raise SemanticConsistencyError('Hard constraint has no supporting command')
    return None
