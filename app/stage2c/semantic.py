"""Deep semantic seams for auditable dialogue constraints.

The provider-facing interpretation remains a proposal.  This module owns the
small deterministic rules that decide which constraints stay active and whether
the executable command set accounts for explicit hard requirements.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime

from .models import ActiveConstraint, FinishAction, Interpretation, ModeAction, SemanticSegment, StayAction
from .retrieval_rules import ALIAS_CATEGORY, normalise, canonical_place_type
from .requirement_fields import FIELDS


COMMAND_CONSTRAINT_FIELDS = {
    'budget_minutes': ('set_time', 'budget_minutes'),
    'departure_at': ('set_time', 'departure_at'),
    'finish_by': ('set_time', 'finish_by'),
    'transport_mode': ('set_mode', 'value'),
    'travel_mode': ('set_mode', 'value'),
    'stay_minutes': ('set_stay', 'minutes'),
    'finish_policy': ('set_finish_policy', 'value'),
}
PRESERVED_OPERATIONS = {
    'preserve_origin': {'set_origin'},
    'preserve_visit': {'replace_visit', 'remove_visit', 'move_visit', 'set_stay', 'clear_visits'},
    'preserve_visits': {'replace_visit', 'remove_visit', 'move_visit', 'set_stay', 'clear_visits'},
    'preserve_location': {'replace_visit', 'remove_visit', 'clear_visits'},
}


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
    capability = FIELDS.get(segment.name)
    if segment.name in {'opening_hours', 'must_be_open', 'last_admission'} or capability and capability.capability in {'unverified_attribute', 'spatial'}:
        return 'unresolved'
    if capability is None or capability.capability == 'unsupported':
        return 'unsupported'
    if segment.name in {'departure_at','finish_by'}:
        try:
            instant=datetime.fromisoformat(segment.value)
            if instant.utcoffset() is None:return 'unresolved'
        except (TypeError,ValueError):
            return 'unresolved'
    if segment.status=='conflicting':
        return 'conflicting'
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
    incoming = [segment for segment in segments if segment.type in {'constraint', 'preference'}
                and segment.effect != 'none' and (segment.effect == 'clear' or segment.value is not None)]
    grouped = defaultdict(list)
    for segment in incoming:
        grouped[(segment.type, segment.name, segment.scope)].append(segment)

    for key, updates in grouped.items():
        field = FIELDS.get(key[1])
        old_rows = [item for item in current if (item.kind, item.name, item.scope) == key]
        if field and field.cardinality == 'set':
            kept = old_rows
            for row in updates:
                if row.effect == 'clear':
                    if row.value is None:
                        kept = [item for item in kept if row.hardness != 'hard' and item.hardness == 'hard']
                    else:
                        kept = [item for item in kept if _value_key(item.value) != _value_key(row.value)
                                or row.hardness != 'hard' and item.hardness == 'hard']
                    continue
                if row.value is None:
                    continue
                kept = [item for item in kept if _value_key(item.value) != _value_key(row.value)
                        or row.hardness != 'hard' and item.hardness == 'hard']
                kept.append(ActiveConstraint(kind=row.type, name=row.name, value=deepcopy(row.value),
                    scope=row.scope, hardness=row.hardness, status=_deterministic_status(row, hard_conflict=False),
                    source=row.source, quote=row.quote, turn_index=turn_index))
            current = [item for item in current if (item.kind, item.name, item.scope) != key] + kept
            continue

        kept = old_rows
        rows = []
        for row in updates:
            if row.effect == 'clear':
                kept = [item for item in kept if row.hardness != 'hard' and item.hardness == 'hard']
                rows = []
            else:
                rows.append(row)
        incoming_hard = any(row.hardness == 'hard' for row in rows)
        # Soft changes can replace soft state but cannot cancel a hard value.
        kept = [item for item in kept if item.hardness == 'hard' and not incoming_hard]
        hard_rows = [row for row in rows if row.hardness == 'hard']
        other_rows = [row for row in rows if row.hardness != 'hard']
        hard_conflict = len({_value_key(row.value) for row in hard_rows}) > 1
        selected = (hard_rows if hard_conflict else hard_rows[-1:]) + other_rows[-1:]
        current = [item for item in current if (item.kind, item.name, item.scope) != key] + kept
        for row in selected:
            current.append(ActiveConstraint(
                kind=row.type,
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


def synchronize_action_constraints(active, parsed, draft, *, new_visit_ids=(), turn_index: int):
    """Synchronize numeric stay commands in the same atomic proposal.

    Takeaway rows already come from the semantic merge. Rebuilding them from
    compiled actions would lose their source and bypass hardness protection.
    """
    current = [row.model_copy(deep=True) for row in active]
    new_ids = iter(new_visit_ids)
    for action in parsed.actions:
        scope = None
        if action.op == 'add_visit':
            scope = 'visit_id:' + next(new_ids, '')
        elif action.op in {'replace_visit', 'set_stay'}:
            scope = 'visit_id:' + action.target_id
        if not scope or scope == 'visit_id:':
            continue
        if action.op == 'add_visit' and action.stay_minutes is not None:
            # Keep the precise semantic quote/provenance when it already accounts
            # for this command. Do not erase conflicting rows awaiting clarification.
            # A typed proposal does not upgrade a tentative/inferred semantic
            # request into a mandatory, user-explicit requirement.
            if not any(row.name == 'stay_minutes' and row.scope == scope and
                       row.value == action.stay_minutes for row in current):
                current.append(ActiveConstraint(name='stay_minutes', value=action.stay_minutes,
                    scope=scope, hardness='hard', status='understood', source='user_explicit',
                    quote=action.quote, turn_index=turn_index))
        if action.op == 'set_stay':
            current = [row for row in current if not (row.name == 'stay_minutes' and row.scope == scope)]
            if action.minutes is not None:
                current.append(ActiveConstraint(name='stay_minutes', value=action.minutes,
                    scope=scope, hardness='hard', status='understood', source='user_explicit',
                    quote=action.quote, turn_index=turn_index))
    return current


def bind_constraint_scopes(rows, draft, *, new_visit_ids=()):
    """Resolve snapshot ordinals once so later moves cannot retarget memory."""
    scopes = {f'visit:{i}': f'visit_id:{visit.visit_id}' for i, visit in enumerate(draft.visits, 1)}
    scopes.update({f'visit:new:{i}': f'visit_id:{uid}' for i, uid in enumerate(new_visit_ids, 1)})
    if len(new_visit_ids) == 1:
        scopes['visit:new'] = f'visit_id:{new_visit_ids[0]}'
    return [row.model_copy(update={'scope': scopes.get(row.scope, row.scope)}, deep=True) for row in rows]


def _matches_scope(segment, action, draft, parsed=None):
    if segment.scope in {'global', 'future_visits'}:
        return True
    if segment.scope == 'origin':
        return action.op == 'set_origin'
    if segment.scope == 'finish':
        return action.op == 'set_finish'
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


def _new_visit_for_scope(scope, parsed):
    """Resolve new-stop ordinals among additions, never among all commands."""
    additions = [action for action in parsed.actions if action.op == 'add_visit']
    if scope == 'visit:new':
        return additions[0] if len(additions) == 1 else None
    if scope.startswith('visit:new:'):
        ordinal = scope.removeprefix('visit:new:')
        if ordinal.isdigit() and 1 <= int(ordinal) <= len(additions):
            return additions[int(ordinal) - 1]
    return None


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
        broad_category = ALIAS_CATEGORY.get(normalise(value))
        fine_type = canonical_place_type(value)
        if fine_type:
            broad_category = {'park': 'nature', 'cafe': 'food', 'restaurant': 'food', 'museum': 'tourism'}[fine_type]
        matches = value in haystack or broad_category is not None and broad_category in place.categories
        if matches and _matches_scope(segment, action, draft, parsed):
            return True
    return False


def compile_takeaway_actions(parsed, draft):
    """Compile scoped takeaway meaning into the locally validated action list."""
    actions = list(parsed.actions)
    for segment in parsed.semantic_segments:
        if (segment.type != 'constraint' or segment.name != 'takeaway' or
                segment.hardness != 'hard' or segment.effect not in {'set', 'clear'}):
            continue
        profile = 'regular' if segment.effect == 'clear' or segment.value is False else 'takeaway'
        matching = [index for index, action in enumerate(actions)
                    if action.op in {'add_visit', 'replace_visit', 'set_stay'} and
                    _matches_scope(segment, action, draft, parsed)]
        if matching:
            index = next((i for i in matching if actions[i].op == 'set_stay'), matching[0])
            actions[index] = actions[index].model_copy(update={'stay_profile': profile}, deep=True)
            continue
        if segment.scope.startswith('visit:'):
            position = segment.scope.removeprefix('visit:')
            if position.isdigit() and 1 <= int(position) <= len(draft.visits):
                visit = draft.visits[int(position) - 1]
                actions.append(StayAction(op='set_stay', target_id=visit.visit_id,
                    minutes=None, stay_profile=profile, quote=segment.quote))
    return parsed.model_copy(update={'actions': actions}, deep=True)


def compile_global_parameter_actions(parsed: Interpretation, draft):
    """Compile two explicit global requirements without inferring missing intent."""
    actions = list(parsed.actions)
    count = 0
    supported = {
        'transport_mode': (ModeAction, 'set_mode', {'walk', 'drive', 'cycle', 'public_transport'}, {'set_mode'}),
        'finish_policy': (FinishAction, 'set_finish_policy', {'last_stop', 'return_to_start'},
                          {'set_finish_policy', 'set_finish'}),
    }
    for name, (model, operation, values, mutators) in supported.items():
        rows = [row for row in parsed.semantic_segments if row.name == name]
        if not rows or any(action.op in mutators for action in actions):
            continue
        # A soft/inferred/uncertain/clearing/other-scope row cannot lend its
        # authority to a stronger row. Exact duplicate explicit rows are harmless.
        if any(row.type != 'constraint' or row.scope != 'global' or row.hardness != 'hard' or
               row.status != 'understood' or row.source != 'user_explicit' or row.effect != 'set' or
               not isinstance(row.value, str) or row.value not in values for row in rows):
            continue
        if any(row.value != rows[0].value for row in rows):
            continue
        row = rows[0]
        if _retained_parameter_supported(row, parsed, draft):
            continue
        actions.append(model(op=operation, value=row.value, quote=row.quote))
        count += 1
    # Revalidate the whole action list (including takeaway additions), and retain
    # omitted-vs-explicit-null fields such as max_walk_minutes.
    data = parsed.model_dump(exclude_unset=True)
    data['actions'] = [action.model_dump(exclude_unset=True) for action in actions]
    return Interpretation.model_validate(data), count


def _retained_parameter_supported(segment, parsed, draft):
    """An already matching setting needs no no-op command if it cannot be changed."""
    if draft is None or segment.effect != 'set' or segment.value is None:
        return False
    name, actions = segment.name, parsed.actions
    if name == 'stay_minutes':
        position = segment.scope.removeprefix('visit:')
        if not segment.scope.startswith('visit:') or not position.isdigit():
            return False
        if not 1 <= int(position) <= len(draft.visits):
            return False
        visit = draft.visits[int(position) - 1]
        for action in actions:
            if action.op == 'clear_visits':
                return False
            if getattr(action, 'target_id', None) != visit.visit_id:
                continue
            if action.op in {'remove_visit', 'set_stay'}:
                return False
            if action.op == 'replace_visit' and action.stay_profile is not None:
                return False  # Both regular/takeaway profile edits reset the override.
        return visit.stay_minutes == segment.value
    if segment.scope != 'global':
        return False
    if name in {'transport_mode', 'travel_mode'}:
        return not any(a.op == 'set_mode' for a in actions) and draft.mode == segment.value
    if name == 'finish_policy':
        return (not any(a.op in {'set_finish_policy', 'set_finish'} for a in actions) and
                draft.finish_policy == segment.value)
    if name == 'max_walk_minutes':
        return ('max_walk_minutes' not in parsed.model_fields_set and
                draft.max_walk_minutes == segment.value)
    if name in {'budget_minutes', 'departure_at', 'finish_by'}:
        # Even mode-only time commands can clear fields. Keep this fallback
        # conservative; explicit commands still use their normal matching path.
        if any(a.op == 'set_time' for a in actions):
            return False
        expected = segment.value
        if name in {'departure_at', 'finish_by'}:
            try:
                expected = datetime.fromisoformat(expected)
                if expected.utcoffset() is None:
                    return False
            except (TypeError, ValueError):
                return False
        return getattr(draft.time, name) == expected
    return False


def _constraint_supported(segment: SemanticSegment, parsed: Interpretation, draft) -> bool:
    if segment.effect == 'none':
        return True
    if _retained_parameter_supported(segment, parsed, draft):
        return True
    if segment.name == 'stay_minutes' and segment.scope.startswith('visit:new'):
        action = _new_visit_for_scope(segment.scope, parsed)
        return (segment.effect == 'set' and action is not None and
                action.stay_minutes is not None and action.stay_minutes == segment.value)
    if segment.name == 'max_walk_minutes':
        if segment.value is None:
            return parsed.max_walk_minutes is None and 'max_walk_minutes' in parsed.model_fields_set
        try:
            return parsed.max_walk_minutes == int(segment.value)
        except (TypeError, ValueError):
            return False
    if segment.effect == 'clear' and segment.name in COMMAND_CONSTRAINT_FIELDS:
        if segment.name in {'budget_minutes', 'finish_by'}:
            return any(action.op=='set_time' and action.mode=='estimate' for action in parsed.actions)
        if segment.name == 'departure_at':
            return any(action.op=='set_time' and action.clear_departure for action in parsed.actions)
        if segment.name == 'stay_minutes':
            return any(action.op=='set_stay' and action.minutes is None and _matches_scope(segment,action,draft,parsed) for action in parsed.actions)
        return False
    if segment.name in {'place_type', 'category', 'keyword'}:
        return _location_constraint_supported(segment, parsed, draft)
    if segment.name == 'takeaway':
        expected = 'regular' if segment.effect == 'clear' or segment.value is False else 'takeaway'
        return any(action.op in {'add_visit', 'replace_visit', 'set_stay'} and
                   getattr(action, 'stay_profile', None) == expected and
                   _matches_scope(segment, action, draft, parsed) for action in parsed.actions)
    if segment.name in {'departure_at','finish_by'}:
        try:
            instant=datetime.fromisoformat(segment.value)
            if instant.utcoffset() is None:return True
        except (TypeError,ValueError):
            return True
    if segment.name in COMMAND_CONSTRAINT_FIELDS:
        operation, field = COMMAND_CONSTRAINT_FIELDS[segment.name]
        return segment.value is not None and any(
            action.op == operation and getattr(action, field) == segment.value and
            _matches_scope(segment, action, draft, parsed) for action in parsed.actions)
    if segment.name == 'excluded_topic':
        return segment.effect == 'clear' or segment.value in parsed.excluded_add or segment.effect == 'set'
    # Unsupported facts stay explicit in the ledger. requirement_outcomes owns
    # their local capability result; an unrelated model question proves nothing.
    return True


def requirements_for_action(requirements, action, draft, parsed):
    return [row for row in requirements if _matches_scope(row, action, draft, parsed)]


def unverified_preferences(preferences):
    clean = preferences.model_copy(deep=True)
    for row in clean.active_constraints:
        if row.status == 'satisfied' or row.evidence_source is not None or row.source in {'route', 'catalogue'}:
            row.status = _deterministic_status(row.model_copy(update={'status':'understood'}), hard_conflict=False)
            row.evidence_source = None
    return clean


def preference_intent(preferences):
    """Compare adopted meaning independently of checker and turn metadata.

    Rechecking evidence, repeating a quote, or changing list order is not a new
    requested edit. Hardness and declared/inferred source still affect meaning.
    """
    import json
    fields=('kind','name','value','scope','hardness','source')
    rows={json.dumps({key:getattr(row,key) for key in fields},sort_keys=True,
                     ensure_ascii=False,separators=(',',':'))
          for row in preferences.active_constraints}
    return tuple(tuple(sorted(set(getattr(preferences,key))))
                 for key in ('preferred','excluded','notes'))+(tuple(sorted(rows)),)


def restore_verified_preferences(store,ref,session_id,revision,draft,preferences,*,catalogue_build_id=None):
    from app.verification import condition_fingerprint
    clean=unverified_preferences(preferences)
    evidence,reason=store.query(ref,session_id,revision,draft,catalogue_build_id=catalogue_build_id)
    count=0
    if evidence is not None:
        for row in clean.active_constraints:
            check=evidence['checks'].get(condition_fingerprint(row.name,row.value,row.scope))
            if check is not None:
                row.status,row.evidence_source=check['status'],check['source']
                count+=1
    return clean,{'status':'matched' if evidence is not None else 'unknown','reason':reason,'checked_requirements':count}


def requirement_outcomes(preferences):
    outcomes = []
    for row in preferences.active_constraints:
        rule = FIELDS.get(row.name)
        capability = rule.capability if rule else 'unsupported'
        status = row.status
        if capability == 'unsupported':
            status = 'unsupported'
        elif capability in {'unverified_attribute', 'spatial'}:
            status = 'unresolved'
        message = {
            'unsupported': 'This requirement is retained, but the app cannot currently verify it.',
            'unverified_attribute': 'There is not enough evidence to verify this place attribute.',
            'spatial': 'The spatial requirement is recorded. Its distance and reference location still need checking.',
            'route': 'The limit is saved and must be checked against each leg of the current route.',
            'budget': 'The budget is saved and must be checked against the current route and stay estimates.',
        }.get(capability, 'Recorded; real-world conditions have not been verified.')
        if row.name == 'wheelchair_access':
            message = 'Reliable accessibility data and step-free route checks are unavailable, so this requirement cannot be guaranteed.'
        outcomes.append({'name': row.name, 'kind': row.kind, 'scope': row.scope,
            'hardness': row.hardness, 'status': status, 'capability': capability,
            'message': message})
    return outcomes


def parameter_mismatches(preferences,draft):
    actual={'max_walk_minutes':draft.max_walk_minutes,'transport_mode':draft.mode,
        'finish_policy':draft.finish_policy,'budget_minutes':draft.time.budget_minutes,
        'departure_at':draft.time.departure_at,'finish_by':draft.time.finish_by}
    mismatches=[]
    for row in preferences.active_constraints:
        if row.kind!='constraint' or row.hardness!='hard' or row.status in {'unresolved','unsupported'}:
            continue
        if row.name == 'stay_minutes' and row.scope.startswith('visit_id:'):
            visit_id = row.scope.removeprefix('visit_id:')
            visit = next((item for item in draft.visits if item.visit_id == visit_id), None)
            if visit is None or visit.stay_minutes != row.value:
                mismatches.append(row.name + ':' + visit_id)
        elif row.scope=='global' and row.name in actual:
            expected=row.value
            if row.name in {'departure_at','finish_by'} and isinstance(expected,str):
                try:expected=datetime.fromisoformat(expected)
                except ValueError:pass
            if actual[row.name]!=expected:
                mismatches.append(row.name)
    return mismatches


def _legacy_constraint_supported(segment,parsed,draft):
    if segment.name=='max_walk_minutes':
        if segment.value is None:
            return parsed.max_walk_minutes is None and 'max_walk_minutes' in parsed.model_fields_set
        try:return parsed.max_walk_minutes==int(segment.value)
        except (TypeError,ValueError):return False
    if segment.name in {'place_type','category','keyword'}:
        return _location_constraint_supported(segment,parsed,draft)
    if segment.name=='takeaway':
        return type(segment.value) is bool and any(action.op in {'add_visit','replace_visit'} and
            _matches_scope(segment,action,draft,parsed) for action in parsed.actions)
    if segment.name in COMMAND_CONSTRAINT_FIELDS and segment.status not in {'unsupported','unresolved'}:
        operation,field=COMMAND_CONSTRAINT_FIELDS[segment.name]
        return segment.value is not None and any(action.op==operation and getattr(action,field)==segment.value and
            _matches_scope(segment,action,draft,parsed) for action in parsed.actions)
    if segment.name=='excluded_topic':return segment.value in parsed.excluded_add
    return bool(parsed.questions)


def validate_segment_command_consistency(segments, parsed: Interpretation, *, draft=None, historical=False):
    """Fail closed when an explicit hard segment has no safe representation."""
    conflicts = [segment for segment in segments
                 if segment.type == 'conflict' or segment.status == 'conflicting']
    if conflicts and not parsed.questions:
        raise SemanticConsistencyError('Conflicting semantic constraints need a question')
    if not historical and parsed.max_walk_minutes is not None and not any(segment.name=='max_walk_minutes' and
            segment.type=='constraint' and segment.effect!='none' and segment.value==parsed.max_walk_minutes
            for segment in segments):
        raise SemanticConsistencyError('Walking parameter has no matching requirement')
    for segment in segments:
        if (not historical and segment.type=='operation' and segment.name=='replace_visit'
                and segment.status=='understood' and not any(
                    action.op=='replace_visit' and _matches_scope(segment,action,draft,parsed)
                    for action in parsed.actions)):
            raise SemanticConsistencyError('Understood replacement has no matching command')
        if not historical and segment.name == 'stay_minutes' and segment.effect != 'none':
            position = segment.scope.removeprefix('visit:')
            current_visit = segment.scope.startswith('visit:') and position.isdigit() and int(position) >= 1
            if not current_visit and _new_visit_for_scope(segment.scope, parsed) is None:
                raise SemanticConsistencyError('Stay duration must identify one current or new visit')
        if segment.type == 'preserve':
            for action in parsed.actions:
                forbidden = PRESERVED_OPERATIONS.get(segment.name, set())
                applies = action.op in {'set_origin', 'clear_visits'} or _matches_scope(segment, action, draft, parsed)
                if action.op in forbidden and applies:
                    raise SemanticConsistencyError('Preserved state has a conflicting command')
        if segment.type != 'constraint' or segment.hardness != 'hard':
            continue
        if segment.status == 'conflicting':
            if parsed.questions:
                continue
            raise SemanticConsistencyError('Conflicting hard constraint needs a question')
        supported=(_legacy_constraint_supported(segment,parsed,draft) if historical else
                   _constraint_supported(segment, parsed, draft))
        if not supported:
            raise SemanticConsistencyError('Hard constraint has no supporting command')
    return None
