"""Narrow provider compatibility must preserve semantic and adoption safeguards."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.stage2c.catalogue import LocalCatalogue
from app.stage2c.engine import CallPacer, ChatError, assemble, prepare
from app.stage2c.models import ActiveConstraint, ChatRequest, Draft, Preferences
from app.stage2c.operation_adapter import OperationFormatError, parse_provider_interpretation


def segment(kind='preserve', name='preserve_visit', value=None, **updates):
    return {'type': kind, 'name': name, 'value': value, 'scope': 'visit:1',
            'hardness': 'hard', 'status': 'understood', 'source': 'user_explicit',
            'quote': 'Keep Fort Canning Park unchanged.', 'effect': 'set', **updates}


def envelope(rows, commands=None, **updates):
    return {'acknowledgement': 'Please review the proposed change.',
            'semantic_segments': rows, 'commands': commands or {}, **updates}


def draft():
    def point(label, lat, lon):
        return {'label': label, 'latitude': lat, 'longitude': lon,
                'source': 'user_map', 'confirmed': True}
    return Draft.model_validate({
        'origin': point('Merlion Park', 1.287, 103.85455),
        'visits': [
            {'visit_id': 'park', 'point': point('Fort Canning Park', 1.29589, 103.84661),
             'stay_minutes': 30},
            {'visit_id': 'food', 'point': point('Maxwell Food Centre', 1.28033, 103.84475),
             'stay_minutes': 45},
        ], 'mode': 'walk', 'finish_policy': 'return_to_start',
        'time': {'mode': 'budget', 'budget_minutes': 180},
    })


class OfflineModel:
    settings = SimpleNamespace(model='offline-semantic-effect-regression')

    def __init__(self, raw):
        self.raw = raw
        self.calls = 0

    def generate_json(self, **kwargs):
        self.calls += 1
        assert self.calls == 1, 'Named replacement must not require another model call'
        return {'data': self.raw, 'metadata': {'provider': 'offline_fixture'}}


class NoExternalProvider:
    def search(self, *args, **kwargs):
        raise AssertionError('This regression must use its local catalogue only')


def catalogue():
    return LocalCatalogue(rows=[{
        'entity_id': 'amoy', 'display_title': 'Amoy Street Food Centre',
        'category': 'food', 'categories': ['food'], 'address': 'Telok Ayer Street',
        'search_tags': [], 'search_text': 'Amoy Street Food Centre',
        'latitude': 1.27923, 'longitude': 103.84662,
        'map_eligible': True, 'default_exploration_visible': True,
    }], build_id='offline-semantic-effects')


def request(message, preferences=None):
    return ChatRequest(session_id='e' * 32, revision=3, message=message,
                       draft=draft(), preferences=preferences or Preferences(), consent=True)


def prepared(raw, body):
    model = OfflineModel(raw)
    record, response = prepare(body, NoExternalProvider(), client=model,
                               cat=catalogue(), pace=CallPacer(0))
    assert model.calls == 1
    return record, response


@pytest.mark.parametrize('kind,name,scope,value', [
    ('operation', 'replace_visit', 'visit:2', None),
    ('operation', 'replace_visit', 'visit:2', 'Amoy Street Food Centre'),
    ('preserve', 'preserve_visit', 'visit:1', None),
    ('preserve', 'preserve_visit', 'visit:1', 'Fort Canning Park'),
    ('conflict', 'transport_mode', 'global', None),
    ('conflict', 'transport_mode', 'global', 'Walk and drive conflict'),
    ('conflict', 'transport_mode', 'global', ['walk', 'drive']),
])
def test_redundant_audit_set_changes_only_effect_on_a_fresh_copy(kind, name, scope, value):
    commands = {'replace_visit': [{'order': 1, 'target_position': 2,
                                   'quote': 'Replace the food stop.',
                                   'place': {'query': 'Amoy Street Food Centre'}}]} if kind == 'operation' else {}
    raw = envelope([segment(kind, name, value, scope=scope)], commands)
    before = deepcopy(raw)

    parsed, normalization = parse_provider_interpretation(raw, draft(), turn_index=7)

    assert parsed.semantic_segments[0].model_dump() == {
        **before['semantic_segments'][0], 'effect': 'none', 'turn_index': 7}
    assert normalization['audit_effects_normalized'] == 1
    assert raw == before
    if isinstance(value, list):
        parsed.semantic_segments[0].value.append('local change')
        assert raw == before


def test_existing_constraints_preferences_and_canonical_audit_effects_are_unchanged():
    rows = [
        segment('constraint', 'max_walk_minutes', 10, scope='global'),
        segment('preference', 'excluded_topic', 'museum', scope='global', effect='clear'),
        segment(effect='none'),
    ]
    raw = envelope(rows)
    before = deepcopy(raw)

    parsed, normalization = parse_provider_interpretation(raw, draft())

    assert [row.model_dump(exclude={'turn_index'}) for row in parsed.semantic_segments] == rows
    assert normalization['audit_effects_normalized'] == 0
    assert raw == before


def test_replacement_preserves_the_confirmed_plan_until_real_adoption():
    keep = 'Keep Fort Canning Park unchanged.'
    replace = 'Replace only Maxwell Food Centre with Amoy Street Food Centre.'
    raw = envelope([
        segment('operation', 'replace_visit', 'Amoy Street Food Centre',
                scope='visit:2', quote=replace),
        segment(quote=keep),
    ], {'replace_visit': [{'order': 1, 'target_position': 2, 'quote': replace,
                          'place': {'query': 'Amoy Street Food Centre'}}]})
    preferences = Preferences(active_constraints=[
        ActiveConstraint(name=name, value=value, scope=scope, hardness='hard',
                         quote='Previously accepted requirement', turn_index=1)
        for name, value, scope in [
            ('stay_minutes', 30, 'visit_id:park'), ('stay_minutes', 45, 'visit_id:food'),
            ('budget_minutes', 180, 'global'), ('transport_mode', 'walk', 'global'),
            ('finish_policy', 'return_to_start', 'global'),
        ]
    ])
    body = request(keep + ' ' + replace, preferences)
    original_body, original_raw = body.model_dump(), deepcopy(raw)
    _, normalization = parse_provider_interpretation(raw, body.draft)
    assert normalization['audit_effects_normalized'] == 2

    record, response = prepared(raw, body)

    assert body.model_dump() == original_body
    assert raw == original_raw
    assert response['operation_adaptation']['audit_effects_normalized'] == 2
    assert response['slots'][0]['options'][0]['point']['entity_id'] == 'amoy'
    with pytest.raises(ChatError) as exc:
        assemble(record, {}, strict=True, cat=catalogue())
    assert exc.value.code == 'CONFIRM_PLACE'
    selected = record['slots'][0]['options'][0]['candidate_key']
    adopted = assemble(record, {record['slots'][0]['slot_id']: selected}, strict=True,
                       cat=catalogue())
    result = adopted['draft']
    assert result['visits'][0] == original_body['draft']['visits'][0]
    assert result['visits'][1]['point']['entity_id'] == 'amoy'
    assert result['visits'][1]['visit_id'] == 'food'
    assert [visit['stay_minutes'] for visit in result['visits']] == [30, 45]
    for field in ['origin', 'mode', 'finish_policy', 'finish', 'time']:
        assert result[field] == original_body['draft'][field]
    assert adopted['preferences']['active_constraints'] == original_body['preferences']['active_constraints']
    assert body.model_dump() == original_body
    assert raw == original_raw


def test_preserve_location_allows_a_stay_edit_without_replacing_the_place():
    keep, stay = 'Keep the same park.', 'Stay there for 50 minutes.'
    raw = envelope([
        segment(name='preserve_location', quote=keep),
        segment('constraint', 'stay_minutes', 50, quote=stay),
    ], {'set_stay': [{'order': 1, 'target_position': 1, 'minutes': 50, 'quote': stay}]})
    body = request(keep + ' ' + stay)
    before = body.model_dump()

    record, _ = prepared(raw, body)
    adopted = assemble(record, {}, strict=True, cat=catalogue())

    assert adopted['draft']['visits'][0]['point'] == before['draft']['visits'][0]['point']
    assert adopted['draft']['visits'][0]['stay_minutes'] == 50
    assert adopted['draft']['visits'][1] == before['draft']['visits'][1]
    assert body.model_dump() == before


@pytest.mark.parametrize('op,args', [
    ('set_stay', {'target_position': 1, 'minutes': 50}),
    ('replace_visit', {'target_position': 1, 'place': {'query': 'Amoy Street Food Centre'}}),
    ('remove_visit', {'target_position': 1}),
    ('move_visit', {'target_position': 1, 'position': 2}),
    ('clear_visits', {}),
])
def test_preserve_visit_still_blocks_every_conflicting_command(op, args):
    keep, change = 'Keep Fort Canning Park unchanged.', 'Change the first stop.'
    raw = envelope([segment(quote=keep)], {op: [{'order': 1, 'quote': change, **args}]})
    body = request(keep + ' ' + change)
    before = body.model_dump()

    with pytest.raises(ChatError) as exc:
        prepared(raw, body)

    assert exc.value.code == 'SEMANTIC_COMMAND_MISMATCH'
    assert exc.value.request_diagnostic['normalization']['audit_effects_normalized'] == 1
    assert body.model_dump() == before


@pytest.mark.parametrize('questions', [[], ['Which transport mode should the plan use?']])
def test_conflict_is_not_promoted_to_a_trusted_or_adoptable_requirement(questions):
    quote = 'Walk only and drive only.'
    raw = envelope([segment('conflict', 'travel_mode', ['walk', 'drive'],
                            scope='global', status='conflicting', quote=quote)], questions=questions)
    body = request(quote)
    before = body.model_dump()
    if not questions:
        with pytest.raises(ChatError) as exc:
            prepared(raw, body)
        assert exc.value.code == 'SEMANTIC_COMMAND_MISMATCH'
    else:
        record, response = prepared(raw, body)
        assert record['parsed'].semantic_segments[0].status == 'conflicting'
        assert record['preferences'].active_constraints == []
        assert response['preview']['can_apply'] is False
        with pytest.raises(ChatError) as exc:
            assemble(record, {}, strict=True, cat=catalogue())
        assert exc.value.code == 'SEMANTIC_CONFLICT'
    assert body.model_dump() == before


@pytest.mark.parametrize('kind', ['operation', 'preserve', 'conflict'])
def test_audit_clear_remains_an_error(kind):
    with pytest.raises(OperationFormatError) as exc:
        parse_provider_interpretation(envelope([segment(kind, 'preserve_visit', effect='clear')]), draft())
    assert exc.value.validation['issues'][0]['path'] == ['semantic_segments', 0, 'effect']


@pytest.mark.parametrize('update', [
    {'type': 'unrecognized'}, {'effect': 'erase'}, {'source': 'route'}, {'source': 'catalogue'},
    {'status': 'satisfied'}, {'status': 'unrecognized'}, {'quote': '  '},
    {'scope': 'visit_id:invented'}, {'scope': 'visit:0'}, {'scope': 1},
    {'hardness': 'required'}, {'value': {'execute': 'bad'}}, {'turn_index': 99},
    {'evidence_source': 'route'}, {'unknown_field': 'DO NOT EXPORT THIS CONTENT'},
])
def test_invalid_or_injected_fields_still_reject_without_mutating_raw(update):
    raw = envelope([segment(**update)])
    before = deepcopy(raw)

    with pytest.raises(OperationFormatError) as exc:
        parse_provider_interpretation(raw, draft())

    assert raw == before
    assert 'DO NOT EXPORT THIS CONTENT' not in str(exc.value.validation)


def test_normalized_audit_quote_must_still_be_present_in_the_request():
    raw = envelope([segment(quote='A fabricated quotation.')])
    body = request('Keep the park unchanged.')
    with pytest.raises(ChatError) as exc:
        prepared(raw, body)
    assert exc.value.code == 'UNSUPPORTED_EDIT_EVIDENCE'


def test_requirement_disguised_as_operation_cannot_silently_disappear():
    message = 'Set the total budget to 180 minutes.'
    raw = envelope([segment('operation', 'budget_minutes', 180,
                            scope='global', quote=message)])
    body = request(message)
    body.draft.time.budget_minutes = 60
    before, original_raw = body.model_dump(), deepcopy(raw)

    with pytest.raises(ChatError) as exc:
        prepared(raw, body)

    assert exc.value.code == 'MODEL_SCHEMA'
    assert body.model_dump() == before
    assert raw == original_raw


def test_unknown_preserve_name_cannot_lose_a_budget_guard():
    keep, change = 'Keep the 180-minute budget.', 'Set the total budget to 60 minutes.'
    raw = envelope([segment('preserve', 'preserve_time_budget', 180,
                            scope='global', quote=keep)], {
        'set_time': [{'order': 1, 'quote': change, 'budget_minutes': 60}]})
    body = request(keep + ' ' + change)
    before, original_raw = body.model_dump(), deepcopy(raw)

    with pytest.raises(ChatError) as exc:
        prepared(raw, body)

    assert exc.value.code == 'MODEL_SCHEMA'
    assert body.model_dump() == before
    assert raw == original_raw


@pytest.mark.parametrize('scope', ['origin', 'finish', 'global', 'visit:new', 'visit:3'])
def test_preserve_visit_requires_an_existing_applicable_visit_scope(scope):
    raw = envelope([segment(scope=scope)], {'set_stay': [{
        'order': 1, 'target_position': 1, 'minutes': 50, 'quote': 'Stay for 50 minutes.'}]})
    with pytest.raises(OperationFormatError):
        parse_provider_interpretation(raw, draft())


@pytest.mark.parametrize('scope', ['visit:1', 'global', 'origin', 'visit:new'])
def test_operation_must_match_the_actual_command_target_scope(scope):
    raw = envelope([segment('operation', 'replace_visit', 'Amoy Street Food Centre', scope=scope)], {
        'replace_visit': [{'order': 1, 'target_position': 2, 'quote': 'Replace the food stop.',
                          'place': {'query': 'Amoy Street Food Centre'}}]})
    with pytest.raises(OperationFormatError):
        parse_provider_interpretation(raw, draft())


@pytest.mark.parametrize('name,value,commands', [
    ('replace_visit', 'Another Food Centre', {'replace_visit': [{
        'order': 1, 'target_position': 2, 'quote': 'Replace the food stop.',
        'place': {'query': 'Amoy Street Food Centre'}}]}),
    ('replace_visit', None, {}),
    ('set_stay', 90, {'set_stay': [{
        'order': 1, 'target_position': 2, 'minutes': 45, 'quote': 'Stay for 45 minutes.'}]}),
])
def test_operation_missing_or_different_command_value_is_not_repaired(name, value, commands):
    raw = envelope([segment('operation', name, value, scope='visit:2')], commands)
    with pytest.raises(OperationFormatError):
        parse_provider_interpretation(raw, draft())


@pytest.mark.parametrize('name,scope,value', [
    ('preserve_visit', 'visit:1', 'Another Park'),
    ('preserve_location', 'visit:1', 180),
    ('preserve_origin', 'visit:1', None),
    ('preserve_visits', 'origin', None),
])
def test_preserve_asserted_value_and_named_scope_must_match_existing_state(name, scope, value):
    with pytest.raises(OperationFormatError):
        parse_provider_interpretation(envelope([segment(name=name, scope=scope, value=value)]), draft())


def test_explicit_global_parameters_compile_with_counts_without_changing_the_input_plan():
    mode, finish = 'Cycle for this outing.', 'Return to the start.'
    raw = envelope([
        segment('constraint', 'transport_mode', 'cycle', scope='global', quote=mode),
        segment('constraint', 'finish_policy', 'return_to_start', scope='global', quote=finish),
    ])
    body = request(mode + ' ' + finish, Preferences(active_constraints=[
        ActiveConstraint(name='max_walk_minutes', value=10, scope='global', hardness='hard',
                         quote='Previous walking limit', turn_index=1)]))
    body.draft.finish_policy = 'last_stop'
    body.draft.max_walk_minutes = 10
    original_body, original_raw = body.model_dump(), deepcopy(raw)

    record, response = prepared(raw, body)

    assert response['operation_adaptation']['parameter_actions_compiled'] == 2
    assert [action.op for action in record['parsed'].actions] == ['set_mode', 'set_finish_policy']
    assert 'max_walk_minutes' not in record['parsed'].model_fields_set
    assert body.model_dump() == original_body
    adopted = assemble(record, {}, strict=True, cat=catalogue())
    assert adopted['draft']['mode'] == 'cycle'
    assert adopted['draft']['finish_policy'] == 'return_to_start'
    assert adopted['draft']['max_walk_minutes'] == 10
    assert adopted['draft']['visits'] == original_body['draft']['visits']
    assert body.model_dump() == original_body
    assert raw == original_raw


@pytest.mark.parametrize('existing_count', [11, 12])
def test_compiled_global_parameter_respects_the_total_twelve_action_limit(existing_count):
    mode, stay = 'Cycle for this outing.', 'Keep the park stay at 30 minutes.'
    raw = envelope([segment('constraint', 'transport_mode', 'cycle', scope='global', quote=mode)], {
        'set_stay': [{'order': i + 1, 'target_position': 1, 'minutes': 30, 'quote': stay}
                     for i in range(existing_count)]})
    original_raw = deepcopy(raw)
    if existing_count == 11:
        parsed, normalization = parse_provider_interpretation(raw, draft())
        assert len(parsed.actions) == 12
        assert normalization['parameter_actions_compiled'] == 1
    else:
        body = request(mode + ' ' + stay)
        original_body = body.model_dump()
        with pytest.raises(ChatError) as exc:
            prepared(raw, body)
        assert exc.value.code == 'MODEL_SCHEMA'
        assert any(issue['path'] == ['actions'] and issue['type'] == 'too_long'
                   for issue in exc.value.request_diagnostic['validation']['issues'])
        assert exc.value.request_diagnostic['normalization']['parameter_actions_compiled'] == 0
        assert body.model_dump() == original_body
    assert raw == original_raw


def test_existing_takeaway_compiler_cannot_bypass_the_action_limit():
    raw = envelope([segment('constraint', 'takeaway', True, quote='Take it away.')], {
        'set_mode': [{'order': i + 1, 'transport_mode': 'walk', 'quote': 'Keep walking.'}
                     for i in range(12)]})
    original_raw = deepcopy(raw)
    with pytest.raises(OperationFormatError) as exc:
        parse_provider_interpretation(raw, draft())
    assert any(issue['path'] == ['actions'] and issue['type'] == 'too_long'
               for issue in exc.value.validation['issues'])
    assert exc.value.normalization['parameter_actions_compiled'] == 0
    assert raw == original_raw
