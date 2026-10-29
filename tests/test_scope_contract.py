"""Provider scopes use canonical tokens without guessing a requirement's target."""
from copy import deepcopy
import re

import pytest

from app.stage2c.command_contract import pending_to_commands, scoped_schema
from app.stage2c.models import Draft
from app.stage2c.operation_adapter import OperationFormatError, parse_provider_interpretation


def row(scope, name='theme', value='quiet', **updates):
    return {'type': 'preference', 'name': name, 'value': value, 'scope': scope,
            'hardness': 'soft', 'status': 'understood', 'source': 'user_explicit',
            'effect': 'set', 'quote': 'A quiet trip', **updates}


def envelope(rows, commands=None):
    return {'acknowledgement': 'Review these requirements.',
            'semantic_segments': rows, 'commands': commands or {}}


def scope_schema():
    return scoped_schema()['properties']['semantic_segments']['items']['properties']['scope']


def test_provider_enum_covers_the_existing_canonical_scope_grammar_exactly():
    expected = {'global', 'origin', 'finish', 'future_visits', 'visit:new'}
    expected.update(f'{prefix}:{n}' for prefix in ('visit', 'visit:new', 'time_window')
                    for n in range(1, 100))
    contract = scope_schema()
    assert contract['type'] == 'string'
    assert set(contract['enum']) == expected
    assert len(contract['enum']) == 302
    from app.stage2c.scope_contract import PROVIDER_SCOPES, is_provider_scope
    assert tuple(contract['enum']) == PROVIDER_SCOPES
    for scope in expected:
        assert re.fullmatch(r'global|origin|finish|future_visits|visit:(?:[1-9][0-9]?|new(?::[1-9][0-9]?)?)|time_window:[1-9][0-9]?', scope)
        assert is_provider_scope(scope)
        parsed, _ = parse_provider_interpretation(envelope([row(scope)]), Draft())
        assert parsed.semantic_segments[0].scope == scope


def test_provider_description_explains_field_specific_scopes_and_copies_are_isolated():
    first = scope_schema()
    for field in ('departure_at', 'finish_by', 'budget_minutes', 'transport_mode',
                  'finish_policy', 'max_walk_minutes', 'excluded_topic', 'takeaway'):
        assert field in first['description']
    assert 'global' in first['description']
    assert 'visit:new:N' in first['description']
    assert 'add_visit' in first['description']
    assert 'not departure time' in first['description']
    first['enum'].append('trip')
    assert 'trip' not in scope_schema()['enum']


@pytest.mark.parametrize('scope', [
    'trip', 'itinerary', 'time', 'date', 'tomorrow', '2026-10-30T14:00:00+08:00',
    'visit_id:park', 'global ', 'Global', '', 'visit:0', 'visit:01', 'visit:100',
    'visit:new:0', 'visit:new:01', 'visit:new:100', 'visit:new:1:2',
    'time_window:0', 'time_window:01', 'time_window:100',
])
def test_noncanonical_scope_is_rejected_without_rewriting_the_input(scope):
    raw = envelope([row(scope)])
    before = deepcopy(raw)
    with pytest.raises(OperationFormatError) as exc:
        parse_provider_interpretation(raw, Draft())
    assert exc.value.validation['issues'][0]['path'] == ['semantic_segments', 0, 'scope']
    assert exc.value.validation['issues'][0]['type'] == 'value_error'
    assert raw == before


@pytest.mark.parametrize('name,value', [
    ('departure_at', '2026-10-30T14:00:00+08:00'),
    ('finish_by', '2026-10-30T17:00:00+08:00'), ('budget_minutes', 180),
    ('transport_mode', 'walk'), ('finish_policy', 'return_to_start'),
    ('max_walk_minutes', 10),
])
@pytest.mark.parametrize('scope', ['origin', 'visit:new:1', 'time_window:1'])
def test_canonical_scope_on_a_global_parameter_is_still_rejected(name, value, scope):
    raw = envelope([row(scope, name, value)])
    before = deepcopy(raw)
    with pytest.raises(OperationFormatError) as exc:
        parse_provider_interpretation(raw, Draft())
    assert exc.value.validation['issues'][0]['path'] == ['semantic_segments', 0, 'scope']
    assert exc.value.validation['issues'][0]['type'] == 'value_error'
    assert raw == before


@pytest.mark.parametrize('name,value,scope', [
    ('excluded_topic', 'museum', 'visit:new:1'), ('takeaway', True, 'global'),
])
def test_existing_field_specific_scope_restrictions_remain(name, value, scope):
    with pytest.raises(OperationFormatError) as exc:
        parse_provider_interpretation(envelope([row(scope, name, value)]), Draft())
    assert exc.value.validation['issues'][0]['path'] == ['semantic_segments', 0, 'scope']
    assert exc.value.validation['issues'][0]['type'] == 'value_error'


def test_explicit_date_budget_and_distinct_new_places_survive_pending_roundtrip():
    departure = '2026-10-30T14:00:00+08:00'
    raw = envelope([
        row('global', 'departure_at', departure, type='constraint', hardness='hard', quote='Tomorrow at 14:00'),
        row('global', 'budget_minutes', 180, type='constraint', hardness='hard', quote='a 3-hour trip'),
        row('visit:new:1', 'place_type', 'cafe', type='constraint', hardness='hard', quote='a cafe'),
        row('visit:new:2', 'place_type', 'park', type='constraint', hardness='hard', quote='a park'),
    ], {
        'set_origin': [{'order': 1, 'quote': 'Dhoby Ghaut MRT', 'place': {'query': 'Dhoby Ghaut MRT'}}],
        'add_visit': [
            {'order': 2, 'quote': 'a cafe', 'place': {'categories': ['food'], 'keywords': ['cafe']}},
            {'order': 3, 'quote': 'a park', 'place': {'categories': ['nature'], 'keywords': ['park']}},
        ],
        'set_time': [{'order': 4, 'quote': 'Tomorrow at 14:00', 'departure_at': departure, 'budget_minutes': 180}],
    })
    before = deepcopy(raw)
    draft = Draft()
    draft_before = draft.model_dump()
    parsed, _ = parse_provider_interpretation(raw, draft, turn_index=1)
    pending = pending_to_commands(parsed, draft)
    repeated = deepcopy(pending)
    for segment in repeated['semantic_segments']:
        segment.pop('turn_index')
    again, _ = parse_provider_interpretation(repeated, draft, turn_index=2)
    assert [segment.scope for segment in again.semantic_segments] == [
        'global', 'global', 'visit:new:1', 'visit:new:2']
    assert [action.model_dump() for action in again.actions] == [action.model_dump() for action in parsed.actions]
    assert again.actions[-1].departure_at == departure
    assert again.actions[-1].budget_minutes == 180
    assert raw == before and draft.model_dump() == draft_before
