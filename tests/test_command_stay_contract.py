"""Provider/pending boundary for explicit durations on new visits."""
from copy import deepcopy
import json

import pytest

from app.stage2c.command_contract import compile_commands, pending_to_commands, scoped_schema
from app.stage2c.models import Draft
from app.stage2c.operation_adapter import OperationFormatError, parse_interpretation
from app.stage2c.prompts import INTERPRET
from app.stage2c.wire_schema import interpretation_schema


def test_new_duration_is_optional_and_owned_only_by_add_visit():
    commands = scoped_schema()['properties']['commands']['properties']
    for operation, group in commands.items():
        properties = group['items']['properties']
        assert ('stay_minutes' in properties) == (operation == 'add_visit')
    assert 'stay_minutes' not in commands['add_visit']['items']['required']


def test_pending_roundtrip_retains_distinct_additions_and_omits_foreign_defaults():
    point = {'latitude': 1.3, 'longitude': 103.85, 'label': 'Existing stop',
             'source': 'user_map', 'confirmed': True}
    draft = Draft.model_validate({'visits': [{'visit_id': 'existing', 'point': point,
                                            'stay_minutes': 60}]})
    raw = {'acknowledgement': 'Review the stops.', 'semantic_segments': [], 'commands': {
        'set_origin': [{'order': 1, 'quote': 'Start at Merlion Park',
                        'place': {'query': 'Merlion Park'}}],
        'add_visit': [
            {'order': 2, 'quote': 'Add Alpha for 0 minutes', 'place': {'query': 'Alpha'}, 'stay_minutes': 0},
            {'order': 4, 'quote': 'Add Beta for 45 minutes', 'place': {'query': 'Beta'}, 'stay_minutes': 45}],
        'replace_visit': [{'order': 3, 'quote': 'Replace the existing stop with Gamma',
                           'target_position': 1, 'place': {'query': 'Gamma'}}],
        'set_finish': [{'order': 5, 'quote': 'Finish at Delta', 'place': {'query': 'Delta'}}],
    }}
    original = deepcopy(raw)
    parsed, _ = parse_interpretation(raw, draft)
    pending = pending_to_commands(parsed, draft)
    assert [row['stay_minutes'] for row in pending['commands']['add_visit']] == [0, 45]
    for operation in ['set_origin', 'set_finish', 'replace_visit']:
        assert 'stay_minutes' not in pending['commands'][operation][0]
    assert pending['commands']['replace_visit'][0]['target_position'] == 1
    replay, _ = parse_interpretation(pending, draft)
    assert replay == parsed
    assert raw == original
    assert draft.visits[0].stay_minutes == 60


@pytest.mark.parametrize('operation,extra', [
    ('set_origin', {}), ('set_finish', {}), ('replace_visit', {'target_position': 1}),
])
def test_duration_cannot_leak_into_other_location_commands(operation, extra):
    raw = {'acknowledgement': 'Review', 'semantic_segments': [], 'commands': {
        operation: [{'order': 1, 'quote': 'Some instruction', 'place': {'query': 'Alpha'},
                     'stay_minutes': 30, **extra}]}}
    with pytest.raises(OperationFormatError):
        compile_commands(raw)


@pytest.mark.parametrize('duration', [None, 0, 30, 1440])
def test_scoped_addition_compiles_losslessly(duration):
    raw = {'acknowledgement': 'Review', 'semantic_segments': [], 'commands': {
        'add_visit': [{'order': 1, 'quote': 'Add Alpha', 'place': {'query': 'Alpha'},
                       'stay_minutes': duration}]}}
    original = deepcopy(raw)
    compiled, count = compile_commands(raw)
    assert count == 1
    assert compiled['actions'][0]['stay_minutes'] == duration
    assert raw == original


def test_empty_draft_omits_only_clear_without_changing_the_full_contract():
    full = interpretation_schema()
    empty = interpretation_schema(has_existing_visits=False)
    expected = deepcopy(full)
    del expected['properties']['commands']['properties']['clear_visits']
    assert empty == expected
    assert interpretation_schema(has_existing_visits=True) == full
    assert interpretation_schema() == full


def test_operation_purpose_is_sent_to_the_provider():
    commands = interpretation_schema()['properties']['commands']['properties']
    assert all(group.get('description') for group in commands.values())
    assert 'set_finish_policy' in commands['clear_visits']['description']


def test_complete_prompt_examples_follow_provider_schema_field_order():
    # Gemini emits schema key order. Examples must not teach a conflicting order.
    # This checks actual prompt examples at every nesting level, not just root keys.
    schema = interpretation_schema()

    def check(value, spec):
        if isinstance(value, dict):
            properties = spec['properties']
            assert list(value) == [key for key in properties if key in value]
            for key, item in value.items():
                check(item, properties[key])
        elif isinstance(value, list):
            for item in value:
                check(item, spec['items'])

    decoder, examples, start = json.JSONDecoder(), [], 0
    while (index := INTERPRET.find('{"acknowledgement"', start)) >= 0:
        example, length = decoder.raw_decode(INTERPRET[index:])
        examples.append(example)
        check(example, schema)
        start = index + length
    assert len(examples) == 4
