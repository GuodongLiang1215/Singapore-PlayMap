"""Translate the provider's flat action envelope into strict local operations.

This is an explicit wire adapter, not JSON repair or a fallback model. It:
* omits only known, irrelevant, content-free placeholders;
* resolves a supplied 1-based target_position against the current draft;
* validates each action using its actual op, then validates the whole update.
No inferred target, category, time, coordinates, quote or extra API call is used.
Raw model output/input/validation exception messages are never diagnostic output.
"""
from __future__ import annotations
import copy
import re
from pydantic import ValidationError
from .models import (Interpretation, LocationAction, RemoveAction, MoveAction,
                     StayAction, FinishAction, ModeAction, TimeAction, ClearAction,
                     SemanticSegment)

ACTION_MODELS = {
    'set_origin': LocationAction, 'set_finish': LocationAction,
    'add_visit': LocationAction, 'replace_visit': LocationAction,
    'remove_visit': RemoveAction, 'move_visit': MoveAction,
    'set_stay': StayAction, 'set_finish_policy': FinishAction,
    'set_mode': ModeAction, 'set_time': TimeAction, 'clear_visits': ClearAction,
}
TARGET_OPS = frozenset({'replace_visit', 'remove_visit', 'move_visit', 'set_stay'})
KNOWN_FIELDS = frozenset(
    {k for cls in ACTION_MODELS.values() for k in cls.model_fields} | {'target_position'})
PATH_PARTS = frozenset(KNOWN_FIELDS | set(Interpretation.model_fields) |
    {'commands', 'order', 'transport_mode', 'finish_policy', 'time_mode', *ACTION_MODELS, 'query', 'categories', 'keywords', 'picks', 'slot_id', 'candidate_key', 'reason',
     'type', 'name', 'value', 'scope', 'hardness', 'status', 'source', 'quote', 'turn_index', 'effect',
     '<unknown>', '<root>'})
ERROR_TYPES = frozenset({
    'missing', 'extra_forbidden', 'int_type', 'int_parsing', 'string_type',
    'string_too_long', 'string_too_short', 'bool_type', 'bool_parsing', 'list_type',
    'dict_type', 'model_type', 'literal_error', 'too_long', 'too_short', 'value_error',
    'greater_than', 'greater_than_equal', 'less_than', 'less_than_equal',
    'finite_number', 'invalid_operation', 'irrelevant_field_value', 'unknown_field',
    'target_out_of_range', 'target_conflict', 'invalid_target_type', 'invalid_structure',
    'validation_error', 'unknown_command_field', 'mixed_envelope',
    'invalid_command_order', 'duplicate_command_order', 'incomplete_command_order',
})
MAX_ISSUES = 12


def _safe_path(path):
    return [p if (type(p) is int and 0 <= p <= 10000) or
            (isinstance(p, str) and p in PATH_PARTS) else '<unknown>' for p in path[:8]]


def safe_validation(value):
    """Allowlist applied again at the API boundary. Never echo external strings."""
    if not isinstance(value, dict):
        return None
    issues = []
    for row in (value.get('issues') if isinstance(value.get('issues'), list) else [])[:MAX_ISSUES]:
        if not isinstance(row, dict):
            continue
        op = row.get('operation')
        kind = row.get('type')
        path = row.get('path')
        issues.append({'path': _safe_path(path if isinstance(path, (list, tuple)) else []),
                       'type': kind if isinstance(kind, str) and kind in ERROR_TYPES else 'validation_error',
                       'operation': op if isinstance(op, str) and op in ACTION_MODELS else None})
    count = value.get('error_count')
    count = count if type(count) is int and 0 <= count <= 10000 else len(issues)
    return {'issues': issues, 'error_count': count, 'truncated': count > len(issues),
            'raw_output_included': False, 'input_values_included': False,
            'exception_text_included': False}


def validation_from_exception(error, *, prefix=(), operation=None):
    # Even after excluding input/context, loc and msg can contain arbitrary values;
    # export only locally allowed path tokens and error type (NEVER msg).
    issues = []
    for e in error.errors(include_input=False, include_context=False, include_url=False):
        issues.append({'path': list(prefix) + list(e.get('loc', ())),
                       'type': e.get('type'), 'operation': operation})
    return safe_validation({'issues': issues, 'error_count': len(issues)})


class OperationFormatError(Exception):
    def __init__(self, validation, normalization=None):
        self.validation = safe_validation(validation)
        self.normalization = dict(normalization or {})
        super().__init__('Local operation contract was not satisfied')


def _fail(path, kind, op=None, norm=None):
    raise OperationFormatError({'issues': [{'path': list(path), 'type': kind, 'operation': op}],
                                'error_count': 1}, norm)


def _unused_placeholder(field, value):
    # False/0/[] are not interchangeable: 0 minutes is a meaningful instruction.
    if value is None:
        return True
    if field == 'clear_departure' and value is False:
        return True
    if field == 'place' and isinstance(value, dict):
        return (set(value) <= {'commands', 'order', 'transport_mode', 'finish_policy', 'time_mode', *ACTION_MODELS, 'query', 'categories', 'keywords'} and
                all((k == 'query' and v == '') or
                    (k in ('categories', 'keywords') and isinstance(v, list) and not v)
                    for k, v in value.items()))
    return False


def parse_interpretation(raw, draft, *, turn_index=0):
    """Return validated Interpretation and value-free adaptation counts.

    target_position is a NEW documented wire field, not a fallback for an absent
    or hallucinated ID. When both position and ID are supplied they MUST agree.
    move_visit.position is still the destination index and is never reinterpreted.
    """
    norm = {'neutral_fields_omitted': 0, 'target_positions_resolved': 0}
    if not isinstance(raw, dict):
        _fail(('<root>',), 'invalid_structure', norm=norm)
    if 'commands' in raw:
        from .command_contract import compile_commands
        raw, command_count = compile_commands(raw)
        norm['scoped_commands_compiled'] = command_count
    data = copy.deepcopy(raw)
    for key in data:
        if key not in Interpretation.model_fields:
            _fail(('<unknown>',), 'unknown_field', norm=norm)
    segments = data.get('semantic_segments', [])
    if not isinstance(segments, list):
        _fail(('semantic_segments',), 'list_type', norm=norm)
    if len(segments) > 32:
        _fail(('semantic_segments',), 'too_long', norm=norm)
    cooked_segments = []
    for index, original in enumerate(segments):
        prefix = ('semantic_segments', index)
        if not isinstance(original, dict):
            _fail(prefix, 'invalid_structure', norm=norm)
        if 'turn_index' in original:
            _fail(prefix + ('turn_index',), 'irrelevant_field_value', norm=norm)
        try:
            segment = SemanticSegment.model_validate({**original, 'turn_index': turn_index})
        except ValidationError as error:
            raise OperationFormatError(validation_from_exception(error, prefix=prefix), norm) from None
        cooked_segments.append(segment.model_dump(mode='json'))
    data['semantic_segments'] = cooked_segments
    actions = data.get('actions', [])
    if not isinstance(actions, list):
        _fail(('actions',), 'list_type', norm=norm)
    if len(actions) > 12:
        _fail(('actions',), 'too_long', norm=norm)
    cooked = []
    for index, original in enumerate(actions):
        prefix = ('actions', index)
        if not isinstance(original, dict):
            _fail(prefix, 'invalid_structure', norm=norm)
        a = copy.deepcopy(original)
        op = a.get('op')
        if not isinstance(op, str) or op not in ACTION_MODELS:
            _fail(prefix + ('op',), 'invalid_operation', norm=norm)
        for key in a:
            if key not in KNOWN_FIELDS:
                _fail(prefix + ('<unknown>',), 'unknown_field', op, norm)
        allowed = set(ACTION_MODELS[op].model_fields)
        # LocationAction shares a class, but only replacement owns a target.
        if op in ('set_origin', 'set_finish', 'add_visit'):
            allowed.discard('target_id')
        if op in TARGET_OPS:
            pos = a.pop('target_position', None)
            if pos is not None:
                if type(pos) is not int:
                    _fail(prefix + ('target_position',), 'invalid_target_type', op, norm)
                if pos < 1 or pos > len(draft.visits):
                    _fail(prefix + ('target_position',), 'target_out_of_range', op, norm)
                uid = draft.visits[pos - 1].visit_id
                if a.get('target_id') not in (None, uid):
                    _fail(prefix + ('target_id',), 'target_conflict', op, norm)
                a['target_id'] = uid
                norm['target_positions_resolved'] += 1
            if a.get('target_id') is None:
                _fail(prefix + ('target_id',), 'missing', op, norm)
        for key in list(a):
            if key not in allowed:
                if _unused_placeholder(key, a[key]):
                    del a[key]
                    norm['neutral_fields_omitted'] += 1
                else:
                    _fail(prefix + (key,), 'irrelevant_field_value', op, norm)
        try:
            # Select the operation first; don't report irrelevant union branches.
            action = ACTION_MODELS[op].model_validate(a)
        except ValidationError as error:
            raise OperationFormatError(validation_from_exception(error, prefix=prefix, operation=op), norm) from None
        cooked.append(action.model_dump(mode='json'))
    data['actions'] = cooked
    try:
        parsed = Interpretation.model_validate(data)
    except ValidationError as error:
        raise OperationFormatError(validation_from_exception(error), norm) from None
    return parsed, norm


def parse_provider_interpretation(raw, draft, *, turn_index=0):
    """Keep current model permissions narrower than saved-state permissions.

    Saved local state can contain checker evidence. A live model cannot reuse
    those permissions. Historical replay deliberately uses parse_interpretation.
    """
    if not isinstance(raw, dict):
        _fail(('<root>',), 'invalid_structure')
    required = {'type', 'name', 'value', 'scope', 'hardness', 'status', 'source', 'quote', 'effect'}
    rows = raw.get('semantic_segments')
    if not isinstance(rows, list):
        _fail(('semantic_segments',), 'list_type')
    for index, row in enumerate(rows):
        prefix = ('semantic_segments', index)
        if not isinstance(row, dict):
            _fail(prefix, 'invalid_structure')
        for field in sorted(required - row.keys()):
            _fail(prefix + (field,), 'missing')
        try:
            SemanticSegment.model_validate(row)
        except ValidationError as error:
            raise OperationFormatError(validation_from_exception(error,prefix=prefix)) from None
        if row['status'] not in {'understood', 'unresolved', 'conflicting', 'unsupported'}:
            _fail(prefix + ('status',), 'literal_error')
        if row['source'] not in {'user_explicit', 'inferred'}:
            _fail(prefix + ('source',), 'literal_error')
        if row['effect'] not in {'set', 'clear', 'none'}:
            _fail(prefix + ('effect',), 'literal_error')
        if not isinstance(row['quote'], str) or not row['quote'].strip():
            _fail(prefix + ('quote',), 'string_too_short')
        if not isinstance(row['scope'], str) or not re.fullmatch(
            r'global|origin|finish|future_visits|visit:(?:[1-9]\d?|new(?::[1-9]\d?)?)|time_window:[1-9]\d?', row['scope']
        ):
            _fail(prefix + ('scope',), 'value_error')
        if row['type'] in {'operation', 'preserve', 'conflict'} and row['effect'] != 'none':
            _fail(prefix + ('effect',), 'irrelevant_field_value')
        if row['effect'] == 'set' and row['value'] is None:
            _fail(prefix + ('value',), 'value_error')
    from .requirement_fields import FIELDS, canonical_requirement_name
    data = copy.deepcopy(raw)
    for index, row in enumerate(data['semantic_segments']):
        row['name'] = canonical_requirement_name(row['name'])
        name = row['name']
        field = FIELDS.get(name)
        if name == 'excluded_topic' and row['scope'] not in {'global', 'future_visits'}:
            _fail(('semantic_segments', index, 'scope'), 'value_error')
        if name == 'takeaway' and not (row['scope'].startswith('visit:') or row['scope'].startswith('visit:new')):
            _fail(('semantic_segments', index, 'scope'), 'value_error')
        if row['effect'] == 'clear':
            if field and field.cardinality == 'set':
                if row['value'] is not None and not isinstance(row['value'], str):
                    _fail(('semantic_segments', index, 'value'), 'string_type')
            elif row['value'] is not None:
                _fail(('semantic_segments', index, 'value'), 'irrelevant_field_value')
        if row['effect'] == 'set':
            if row['value'] is None:
                _fail(('semantic_segments', index, 'value'), 'value_error')
            if field and field.cardinality == 'set' and not isinstance(row['value'], str):
                _fail(('semantic_segments', index, 'value'), 'string_type')
        if row['name'] in {'max_walk_minutes', 'budget_minutes', 'departure_at', 'finish_by', 'transport_mode', 'finish_policy'} and row['scope'] != 'global':
            _fail(('semantic_segments', index, 'scope'), 'value_error')
        if row['effect'] != 'set':
            continue
        name, value = row['name'], row['value']
        if name in {'max_walk_minutes', 'budget_minutes', 'stay_minutes', 'radius_m'}:
            if type(value) is not int or value < (0 if name == 'stay_minutes' else 1):
                _fail(('semantic_segments', index, 'value'), 'int_type')
        if name == 'takeaway' and row['effect'] == 'set' and type(value) is not bool:
            _fail(('semantic_segments', index, 'value'), 'bool_type')
    parsed, normalization = parse_interpretation(data, draft, turn_index=turn_index)
    from .semantic import compile_takeaway_actions
    return compile_takeaway_actions(parsed, draft), normalization
