"""F4 model-facing, operation-scoped commands.

Each operation owns a small array of arguments. There is no universal `value`
field. `order` preserves mixed-operation ordering without an anyOf/oneOf tree.
The compiler performs lossless structural translation only: it never infers an
operation from user text, repairs meaningful fields, retries a model or edits a
Draft. Existing domain validation and confirmation remain mandatory.
"""
from __future__ import annotations
import copy
from typing import get_args
from .models import Category, Topic, Interpretation

OPERATIONS = (
    'set_origin', 'set_finish', 'add_visit', 'replace_visit',
    'remove_visit', 'move_visit', 'set_stay', 'set_finish_policy',
    'set_mode', 'set_time', 'clear_visits',
)
TARGET_OPERATIONS = frozenset({'replace_visit', 'remove_visit', 'move_visit', 'set_stay'})
LOCATION_OPERATIONS = frozenset({'set_origin', 'set_finish', 'add_visit', 'replace_visit'})
CONTRACT_NAME = 'operation_scoped_commands_v1'
ROOT_FIELDS = frozenset(set(Interpretation.model_fields) - {'actions'})
ARGUMENTS = {
    'set_origin': {'place'}, 'set_finish': {'place'}, 'add_visit': {'place'},
    'replace_visit': {'target_position', 'place'},
    'remove_visit': {'target_position'}, 'move_visit': {'target_position', 'position'},
    'set_stay': {'target_position', 'minutes'},
    'set_finish_policy': {'finish_policy'}, 'set_mode': {'transport_mode'},
    'set_time': {'time_mode', 'budget_minutes', 'departure_at', 'finish_by',
                 'clear_departure', 'buffer_minutes'},
    'clear_visits': set(),
}
REQUIRED = {op: ({'order', 'quote'} | fields) for op, fields in ARGUMENTS.items()}
REQUIRED['set_time'] = {'order', 'quote'}
DESCRIPTIONS = {
    'set_origin': 'Explicitly named departure point only.',
    'set_finish': 'Explicitly named custom end point only.',
    'add_visit': 'One requested new visit per entry.',
    'replace_visit': 'Replace a current visit location; preserve its stay and other visits.',
    'remove_visit': 'Remove the indicated current visit.',
    'move_visit': 'Move a current visit to position; target_position refers to the original snapshot.',
    'set_stay': 'Set visit minutes; null resets to the declared initial estimate.',
    'set_finish_policy': 'Change return/end policy only.',
    'set_mode': 'Change transport mode only.',
    'set_time': 'Partial time change. A duration alone sets budget semantics. Preserve unspecified fields; do not invent a budget.',
    'clear_visits': 'Clear visits only when explicitly requested.',
}


def _object(properties, required):
    return {'type': 'object', 'properties': properties, 'required': required,
            'additionalProperties': False}


def scoped_schema():
    """Provider schema: no refs, discriminated unions or all-purpose envelopes.

    Cardinality, exact target range, timestamps and business constraints are
    still checked locally. Omitting these redundant decoder bounds keeps the
    vendor grammar simple, not the application's validator permissive.
    """
    text = {'type': 'string'}
    array = lambda item: {'type': 'array', 'items': item}
    place = _object({
        'query': text,
        'categories': array({'type': 'string', 'enum': list(get_args(Category))}),
        'keywords': array(text),
    }, [])
    specs = {
        'order': {'type': 'integer'},
        'quote': {'type': 'string'},
        'place': place,
        'target_position': {'type': 'integer'},
        'position': {'type': 'integer'},
        'minutes': {'type': ['integer', 'null']},
        'finish_policy': {'type': 'string', 'enum': ['last_stop', 'return_to_start']},
        'transport_mode': {'type': 'string', 'enum': ['walk', 'drive', 'cycle', 'public_transport']},
        'time_mode': {'type': ['string', 'null'], 'description': 'estimate, budget or window. Optional: a supplied budget implies budget mode; a supplied finish implies window. Without either, omitted/null preserves the current mode.'},
        'budget_minutes': {'type': ['integer', 'null']},
        'departure_at': {'type': ['string', 'null']},
        'finish_by': {'type': ['string', 'null']},
        'clear_departure': {'type': 'boolean'},
        'buffer_minutes': {'type': ['integer', 'null']},
    }
    commands = {}
    for op in OPERATIONS:
        fields = {k: copy.deepcopy(v) for k, v in specs.items() if k in ARGUMENTS[op] | {'order', 'quote'}}
        commands[op] = array(_object(fields, [k for k in fields if k in REQUIRED[op]]))
    fields = {'acknowledgement': copy.deepcopy(text)}
    for k in ('preferred_add', 'preferred_remove', 'excluded_add', 'excluded_remove'):
        fields[k] = array({'type': 'string', 'enum': list(get_args(Topic))})
    for k in ('notes_add', 'notes_remove', 'questions'):
        fields[k] = array(copy.deepcopy(text))
    fields['commands'] = _object(commands, [])
    return _object(fields, ['acknowledgement', 'commands'])


def _fail(path, kind, op=None):
    # Late import prevents a module cycle with the existing local adapter.
    from .operation_adapter import OperationFormatError
    raise OperationFormatError(
        {'issues': [{'path': list(path), 'type': kind, 'operation': op}], 'error_count': 1},
        {'neutral_fields_omitted': 0, 'target_positions_resolved': 0, 'scoped_commands_compiled': 0})


def compile_commands(raw):
    """Convert typed commands into the unchanged internal actions contract.

    No meaningful field is discarded: unexpected keys (even null) are rejected.
    Processing order is explicit and global, never inferred from object order.
    Returns a fresh value; incoming JSON and the user's draft remain untouched.
    """
    if not isinstance(raw, dict):
        _fail(('<root>',), 'invalid_structure')
    if 'actions' in raw:
        _fail(('commands',), 'mixed_envelope')
    for key in raw:
        if key not in ROOT_FIELDS | {'commands'}:
            _fail(('<unknown>',), 'unknown_field')
    commands = raw.get('commands')
    if not isinstance(commands, dict):
        _fail(('commands',), 'dict_type')
    ordered = []
    seen = set()
    for op, values in commands.items():
        if op not in ARGUMENTS:
            _fail(('commands', '<unknown>'), 'invalid_operation')
        if not isinstance(values, list):
            _fail(('commands', op), 'list_type', op)
        if len(values) > 12 or len(ordered) + len(values) > 12:
            _fail(('commands', op), 'too_long', op)
        for index, original in enumerate(values):
            prefix = ('commands', op, index)
            if not isinstance(original, dict):
                _fail(prefix, 'invalid_structure', op)
            allowed = ARGUMENTS[op] | {'order', 'quote'}
            for key in original:
                if key not in allowed:
                    _fail(prefix + (key,), 'unknown_command_field', op)
            for key in sorted(REQUIRED[op] - set(original)):
                _fail(prefix + (key,), 'missing', op)
            order = original['order']
            if type(order) is not int or not 1 <= order <= 12:
                _fail(prefix + ('order',), 'invalid_command_order', op)
            if order in seen:
                _fail(prefix + ('order',), 'duplicate_command_order', op)
            seen.add(order)
            if op == 'set_time' and 'clear_departure' in original and type(original['clear_departure']) is not bool:
                _fail(prefix + ('clear_departure',), 'bool_type', op)
            a = copy.deepcopy(original)
            del a['order']
            a['op'] = op
            if op == 'set_mode':
                a['value'] = a.pop('transport_mode')
            elif op == 'set_finish_policy':
                a['value'] = a.pop('finish_policy')
            elif op == 'set_time' and 'time_mode' in a:
                a['mode'] = a.pop('time_mode')
            ordered.append((order, a))
    if seen != set(range(1, len(ordered) + 1)):
        _fail(('commands',), 'incomplete_command_order')
    cooked = {key: copy.deepcopy(value) for key, value in raw.items() if key != 'commands'}
    cooked['actions'] = [a for _, a in sorted(ordered, key=lambda entry: entry[0])]
    return cooked, len(ordered)


def pending_to_commands(parsed, draft):
    """Express an unadopted LOCAL interpretation in the provider's new syntax.

    This avoids teaching the old universal `value` envelope via pending context.
    IDs are resolved to snapshot ordinals, never guessed. Not a provider parser.
    """
    data = parsed.model_dump(mode='json')
    actions = data.pop('actions')
    groups = {}
    positions = {v.visit_id: i + 1 for i, v in enumerate(draft.visits)}
    for index, original in enumerate(actions):
        a = copy.deepcopy(original)
        op = a.pop('op')
        if op in TARGET_OPERATIONS:
            uid = a.pop('target_id', None)
            if uid not in positions:
                _fail(('commands', op, 0, 'target_position'), 'target_out_of_range', op)
            a['target_position'] = positions[uid]
        else:
            a.pop('target_id', None)  # Documented local LocationAction default, not provider data.
        if op == 'set_mode':
            a['transport_mode'] = a.pop('value')
        elif op == 'set_finish_policy':
            a['finish_policy'] = a.pop('value')
        elif op == 'set_time':
            a['time_mode'] = a.pop('mode')
            a = {k: v for k, v in a.items() if v is not None and not (k == 'clear_departure' and v is False)}
        a = {'order': index + 1, **a}
        groups.setdefault(op, []).append(a)
    return {**data, 'commands': groups}
