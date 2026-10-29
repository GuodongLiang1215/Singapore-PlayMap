"""Offline regressions for adding a place and its explicit stay atomically."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.stage2b.models import Visit
from app.stage2c.catalogue import LocalCatalogue
from app.stage2c.engine import CallPacer, ChatError, assemble, prepare
from app.stage2c.models import (ActiveConstraint, ChatRequest, Draft, Interpretation,
                              LocationAction, Preferences, SemanticSegment)
from app.stage2c.semantic import SemanticConsistencyError, validate_segment_command_consistency


MESSAGE = ('Plan a walking outing. Visit Fort Canning Park for 30 minutes, then '
           'Maxwell Food Centre for 45 minutes, and return to the start within three hours. '
           'Keep the existing stop for 12 minutes. Buy takeaway for 15 minutes. '
           'Use 0 minutes, or 1440 minutes, for the new stop.')


def segment(name, value, scope, quote):
    return {'type': 'constraint', 'name': name, 'value': value, 'scope': scope,
            'hardness': 'hard', 'status': 'understood', 'source': 'user_explicit',
            'effect': 'set', 'quote': quote}


def output():
    return {
        'acknowledgement': 'Review both stops and their requested stays.',
        'semantic_segments': [
            segment('stay_minutes', 30, 'visit:new:1', 'Fort Canning Park for 30 minutes'),
            segment('stay_minutes', 45, 'visit:new:2', 'Maxwell Food Centre for 45 minutes'),
            segment('budget_minutes', 180, 'global', 'within three hours'),
            segment('finish_policy', 'return_to_start', 'global', 'return to the start'),
            segment('transport_mode', 'walk', 'global', 'walking'),
        ],
        'commands': {
            'add_visit': [
                {'order': 1, 'quote': 'Fort Canning Park for 30 minutes',
                 'place': {'query': 'Fort Canning Park', 'categories': ['nature']}, 'stay_minutes': 30},
                {'order': 2, 'quote': 'Maxwell Food Centre for 45 minutes',
                 'place': {'query': 'Maxwell Food Centre', 'categories': ['food']}, 'stay_minutes': 45},
            ],
            'set_mode': [{'order': 3, 'quote': 'walking', 'transport_mode': 'walk'}],
            'set_time': [{'order': 4, 'quote': 'within three hours', 'budget_minutes': 180}],
            'set_finish_policy': [{'order': 5, 'quote': 'return to the start',
                                   'finish_policy': 'return_to_start'}],
        },
    }


def single_output(minutes=30, *, scope='visit:new'):
    raw = output()
    raw['commands'] = {'add_visit': raw['commands']['add_visit'][:1]}
    raw['commands']['add_visit'][0]['stay_minutes'] = minutes
    raw['semantic_segments'] = [segment('stay_minutes', minutes, scope, 'Fort Canning Park')]
    return raw


def point(label='Original start'):
    return {'label': label, 'latitude': 1.30, 'longitude': 103.84,
            'source': 'user_map', 'confirmed': True}


def catalogue():
    rows = []
    for index, (title, category) in enumerate([
            ('Fort Canning Park', 'nature'), ('Maxwell Food Centre', 'food')]):
        rows.append({'entity_id': f'fixture-{index}', 'display_title': title,
                     'category': category, 'categories': [category], 'address': '',
                     'search_tags': [], 'search_text': title,
                     'latitude': 1.30, 'longitude': 103.85 + index / 1000,
                     'map_eligible': True, 'default_exploration_visible': True})
    return LocalCatalogue(rows=rows, build_id='offline-new-stay')


class OfflineModel:
    settings = SimpleNamespace(model='offline-new-visit-stays')

    def __init__(self, raw):
        self.raw = raw
        self.calls = 0

    def generate_json(self, **kwargs):
        self.calls += 1
        assert self.calls == 1, 'Named catalogue places must not need a selection model call'
        return {'data': deepcopy(self.raw), 'metadata': {'provider': 'offline'}}


class NoGeocoder:
    def search(self, *args, **kwargs):
        raise AssertionError('This test must use only the supplied catalogue')


def prepared(raw, *, draft=None, preferences=None, message=MESSAGE):
    body = ChatRequest(session_id='8' * 32, revision=3, message=message,
                       draft=draft or Draft(origin=point()),
                       preferences=preferences or Preferences(), consent=True)
    original = body.model_dump(mode='json')
    cat = catalogue()
    record, proposal = prepare(body, NoGeocoder(), client=OfflineModel(raw),
                               cat=cat, pace=CallPacer(0))
    assert body.model_dump(mode='json') == original
    assert not proposal['preview']['can_apply']
    assert proposal['preview']['unresolved_slots']
    assert record['body'].draft.model_dump() == body.draft.model_dump()
    return body, cat, record, proposal


def confirmed(record, cat):
    choices = {slot['slot_id']: slot['options'][0]['candidate_key'] for slot in record['slots']}
    return assemble(record, choices, strict=True, cat=cat)


def stay_ledger(result):
    return {row['scope']: row['value'] for row in result['preferences']['active_constraints']
            if row['name'] == 'stay_minutes'}


def test_two_new_stays_are_confirmed_with_budget_return_and_durable_scopes():
    body, cat, record, proposal = prepared(output())
    assert body.draft.visits == []
    with pytest.raises(ChatError) as error:
        assemble(record, {}, strict=True, cat=cat)
    assert error.value.code == 'CONFIRM_PLACE'
    result = confirmed(record, cat)
    visits = result['draft']['visits']
    assert [v['stay_minutes'] for v in visits] == [30, 45]
    assert [v['point']['label'] for v in visits] == ['Fort Canning Park', 'Maxwell Food Centre']
    assert result['draft']['time']['budget_minutes'] == 180
    assert result['draft']['time']['mode'] == 'budget'
    assert result['draft']['finish_policy'] == 'return_to_start'
    assert result['draft']['mode'] == 'walk'
    assert result['ready_to_calculate'] and result['can_apply']
    assert stay_ledger(result) == {'visit_id:' + v['visit_id']: n for v, n in zip(visits, [30, 45])}
    for row in result['preferences']['active_constraints']:
        assert row['hardness'] == 'hard' and row['status'] == 'understood'
        assert row['source'] == 'user_explicit' and row['evidence_source'] is None
    # Preparing and confirming return separate data; neither mutates the source draft.
    assert body.draft.visits == [] and body.preferences.active_constraints == []
    assert proposal['preview']['draft']['visits'] == []


def test_new_ordinals_count_additions_only_with_existing_stop_and_mixed_actions():
    raw = output()
    raw['commands']['set_mode'][0]['order'] = 1
    raw['commands']['add_visit'][0]['order'] = 2
    raw['commands']['add_visit'][1]['order'] = 5
    raw['commands']['set_finish_policy'][0]['order'] = 6
    raw['commands']['set_stay'] = [{'order': 3, 'quote': 'existing stop for 12 minutes',
                                    'target_position': 1, 'minutes': 12}]
    raw['semantic_segments'].append(segment('stay_minutes', 12, 'visit:1', 'existing stop for 12 minutes'))
    old = Visit(visit_id='original-stop', point=point('Original stop'), stay_minutes=9)
    preferences = Preferences(active_constraints=[ActiveConstraint(name='max_walk_minutes', value=12,
        scope='global', hardness='hard', status='understood', source='user_explicit',
        quote='Keep each walk within 12 minutes', turn_index=1)])
    body, cat, record, _ = prepared(raw, draft=Draft(origin=point(), visits=[old], max_walk_minutes=12),
                                  preferences=preferences)
    result = confirmed(record, cat)
    visits = result['draft']['visits']
    assert [v['stay_minutes'] for v in visits] == [12, 30, 45]
    assert visits[0]['visit_id'] == 'original-stop'
    assert set(record['new_ids']) == {1, 4}
    assert [v['visit_id'] for v in visits[1:]] == [record['new_ids'][1], record['new_ids'][4]]
    assert stay_ledger(result) == {'visit_id:' + v['visit_id']: n for v, n in zip(visits, [12, 30, 45])}
    assert result['draft']['max_walk_minutes'] == 12
    assert any(row['name'] == 'max_walk_minutes' and row['value'] == 12
               for row in result['preferences']['active_constraints'])
    assert body.draft.visits[0].stay_minutes == 9


def test_adopted_stay_constraints_follow_ids_through_a_later_reordering_and_edit():
    _, cat, record, _ = prepared(output())
    first = confirmed(record, cat)
    first_id, second_id = [v['visit_id'] for v in first['draft']['visits']]
    latest = first
    for message, command, segments in [
        ('Move stop 2 to position 1.',
         {'move_visit': [{'order': 1, 'quote': 'Move stop 2 to position 1.',
                          'target_position': 2, 'position': 1}]}, []),
        ('Set stop 1 to 20 minutes.',
         {'set_stay': [{'order': 1, 'quote': 'Set stop 1 to 20 minutes.',
                        'target_position': 1, 'minutes': 20}]},
         [segment('stay_minutes', 20, 'visit:1', 'Set stop 1 to 20 minutes.')]),
    ]:
        body = ChatRequest(session_id='6' * 32, revision=4, message=message,
                           draft=Draft.model_validate(latest['draft']),
                           preferences=Preferences.model_validate(latest['preferences']), consent=True)
        before = body.model_dump()
        raw = {'acknowledgement': 'Review the edit.', 'commands': command, 'semantic_segments': segments}
        edit, _ = prepare(body, NoGeocoder(), client=OfflineModel(raw), cat=cat, pace=CallPacer(0))
        latest = assemble(edit, {}, strict=True, cat=cat)
        assert body.model_dump() == before
    assert [v['visit_id'] for v in latest['draft']['visits']] == [second_id, first_id]
    assert stay_ledger(latest) == {'visit_id:' + first_id: 30, 'visit_id:' + second_id: 20}
    assert [v['stay_minutes'] for v in latest['draft']['visits']] == [20, 30]


@pytest.mark.parametrize('minutes', [0, 1440])
def test_boundary_stays_are_retained_in_visit_and_ledger(minutes):
    _, cat, record, _ = prepared(single_output(minutes))
    result = confirmed(record, cat)
    visit = result['draft']['visits'][0]
    assert visit['stay_minutes'] == minutes
    assert stay_ledger(result) == {'visit_id:' + visit['visit_id']: minutes}


@pytest.mark.parametrize('include_null', [False, True])
def test_omitted_or_null_new_stay_keeps_initial_estimate_without_invented_ledger(include_null):
    raw = single_output(None)
    raw['semantic_segments'] = []
    if not include_null:
        raw['commands']['add_visit'][0].pop('stay_minutes')
    _, cat, record, _ = prepared(raw)
    result = confirmed(record, cat)
    assert result['draft']['visits'][0]['stay_minutes'] is None
    assert stay_ledger(result) == {}


def test_numeric_add_command_alone_creates_durable_explicit_stay():
    raw = single_output(30)
    raw['semantic_segments'] = []
    _, cat, record, _ = prepared(raw)
    result = confirmed(record, cat)
    visit = result['draft']['visits'][0]
    assert stay_ledger(result) == {'visit_id:' + visit['visit_id']: 30}


def test_takeaway_profile_does_not_erase_explicit_numeric_new_stay():
    raw = single_output(15)
    raw['commands']['add_visit'][0]['place'] = {'query': 'Maxwell Food Centre', 'categories': ['food']}
    raw['commands']['add_visit'][0]['quote'] = 'Buy takeaway for 15 minutes'
    raw['semantic_segments'][0]['quote'] = 'Buy takeaway for 15 minutes'
    raw['semantic_segments'].append(segment('takeaway', True, 'visit:new', 'Buy takeaway'))
    _, cat, record, _ = prepared(raw)
    result = confirmed(record, cat)
    visit = result['draft']['visits'][0]
    assert visit['stay_profile'] == 'takeaway' and visit['stay_minutes'] == 15
    assert stay_ledger(result) == {'visit_id:' + visit['visit_id']: 15}
    assert not any('using a short stop estimate' in item for item in result['changes'])


@pytest.mark.parametrize('bad_value', [-1, 1441, 30.5, True, '30'])
def test_numeric_new_stay_is_strict_and_bounded(bad_value):
    with pytest.raises(ValidationError):
        LocationAction(op='add_visit', quote='Fort Canning Park',
                       place={'query': 'Fort Canning Park'}, stay_minutes=bad_value)


@pytest.mark.parametrize('operation', ['set_origin', 'set_finish', 'replace_visit'])
def test_explicit_new_stay_field_is_not_allowed_on_other_location_operations(operation):
    fields = {'target_id': 'original-stop'} if operation == 'replace_visit' else {}
    with pytest.raises(ValidationError):
        LocationAction(op=operation, quote='Fort Canning Park',
                       place={'query': 'Fort Canning Park'}, stay_minutes=30, **fields)


@pytest.mark.parametrize('scope', ['global', 'future_visits', 'origin', 'finish',
                                  'visit:1', 'visit:new:2'])
def test_new_stay_rejects_wrong_or_unresolved_scope_without_changing_the_draft(scope):
    body = ChatRequest(session_id='9' * 32, revision=1, message=MESSAGE,
                       draft=Draft(origin=point()), consent=True)
    before = body.model_dump()
    with pytest.raises(ChatError) as error:
        prepare(body, NoGeocoder(), client=OfflineModel(single_output(scope=scope)),
                cat=catalogue(), pace=CallPacer(0))
    assert error.value.code == 'SEMANTIC_COMMAND_MISMATCH'
    assert body.model_dump() == before


@pytest.mark.parametrize('fault', ['ambiguous', 'mismatch', 'missing_command_value'])
def test_new_stay_cannot_be_ambiguous_or_disagree_with_its_add_command(fault):
    raw = output()
    if fault == 'ambiguous':
        raw['semantic_segments'][0]['scope'] = 'visit:new'
    elif fault == 'mismatch':
        raw['semantic_segments'][0]['value'] = 45
    else:
        raw['commands']['add_visit'][0].pop('stay_minutes')
    body = ChatRequest(session_id='7' * 32, revision=1, message=MESSAGE,
                       draft=Draft(origin=point()), consent=True)
    before = body.model_dump()
    with pytest.raises(ChatError) as error:
        prepare(body, NoGeocoder(), client=OfflineModel(raw), cat=catalogue(), pace=CallPacer(0))
    assert error.value.code == 'SEMANTIC_COMMAND_MISMATCH'
    assert body.model_dump() == before


def test_conflicting_new_stays_are_not_erased_by_command_ledger_synchronization():
    raw = single_output(30)
    raw['semantic_segments'][0]['status'] = 'conflicting'
    other = segment('stay_minutes', 45, 'visit:new', '45 minutes')
    other['status'] = 'conflicting'
    raw['semantic_segments'].append(other)
    raw['questions'] = ['Should this new stop last 30 or 45 minutes?']
    _, cat, record, _ = prepared(raw)
    with pytest.raises(ChatError) as error:
        confirmed(record, cat)
    assert error.value.code == 'SEMANTIC_CONFLICT'
    rows = [row for row in record['preferences'].active_constraints if row.name == 'stay_minutes']
    assert {row.value for row in rows} == {30, 45}
    assert all(row.status == 'conflicting' for row in rows)


def captured_attempt_two_output():
    # Exact public provider data from live_attempt2_matching_mode.json (2026-10-29),
    # expressed using the common fixture fields. It intentionally has no set_mode.
    raw = output()
    raw['acknowledgement'] = ('Review your walking outing with stops at Fort Canning Park and '
                              'Maxwell Food Centre, returning to the start within three hours.')
    raw['semantic_segments'] = [raw['semantic_segments'][i] for i in [4, 0, 1, 3, 2]]
    del raw['commands']['set_mode']
    raw['commands']['set_finish_policy'][0]['order'] = 3
    return raw


def test_captured_two_additions_need_no_redundant_set_mode_when_draft_is_already_walk():
    message = ('Plan a walking outing from this starting point. Visit Fort Canning Park for '
               '30 minutes, then Maxwell Food Centre for 45 minutes, and return to the start '
               'within three hours.')
    _, cat, record, _ = prepared(captured_attempt_two_output(), message=message)
    result = confirmed(record, cat)
    assert result['draft']['mode'] == 'walk'
    assert [v['stay_minutes'] for v in result['draft']['visits']] == [30, 45]
    assert result['draft']['time']['budget_minutes'] == 180
    assert result['draft']['finish_policy'] == 'return_to_start'
    raw = captured_attempt_two_output()
    raw['commands']['set_mode'] = [{'order': 5, 'quote': 'walking', 'transport_mode': 'cycle'}]
    with pytest.raises(ChatError) as error:
        prepared(raw, message=message)
    assert error.value.code == 'SEMANTIC_COMMAND_MISMATCH'


@pytest.mark.parametrize('takeaway', [None, True, False])
def test_retained_existing_stay_supports_location_replacement_unless_profile_resets_it(takeaway):
    message = 'Replace stop 1 with Maxwell Food Centre. Keep its 45-minute stay. Takeaway.'
    raw = {'acknowledgement': 'Review the replacement.',
           'commands': {'replace_visit': [{'order': 1, 'quote': 'Replace stop 1 with Maxwell Food Centre.',
                                         'target_position': 1, 'place': {'query': 'Maxwell Food Centre'}}]},
           'semantic_segments': [segment('stay_minutes', 45, 'visit:1', '45-minute stay')]}
    if takeaway is not None:
        raw['semantic_segments'].append(segment('takeaway', takeaway, 'visit:1', 'Takeaway'))
    draft = Draft(origin=point(), visits=[Visit(visit_id='existing-food', point=point('Old food'), stay_minutes=45)])
    if takeaway is not None:
        with pytest.raises(ChatError) as error:
            prepared(raw, draft=draft, message=message)
        assert error.value.code == 'SEMANTIC_COMMAND_MISMATCH'
    else:
        _, cat, record, _ = prepared(raw, draft=draft, message=message)
        result = confirmed(record, cat)
        visit = result['draft']['visits'][0]
        assert visit['point']['label'] == 'Maxwell Food Centre'
        assert visit['stay_minutes'] == 45 and visit['visit_id'] == 'existing-food'
        assert stay_ledger(result) == {'visit_id:existing-food': 45}


@pytest.mark.parametrize('name,value,draft_fields,mutation', [
    ('transport_mode', 'walk', {}, {'actions': [{'op': 'set_mode', 'value': 'cycle', 'quote': 'Keep settings'}]}),
    ('finish_policy', 'return_to_start', {'finish_policy': 'return_to_start'},
     {'actions': [{'op': 'set_finish', 'place': {'query': 'New end'}, 'quote': 'Keep settings'}]}),
    ('budget_minutes', 180, {'time': {'mode': 'budget', 'budget_minutes': 180}},
     {'actions': [{'op': 'set_time', 'mode': 'estimate', 'quote': 'Keep settings'}]}),
    ('departure_at', '2026-10-29T02:00:00+00:00', {'time': {'departure_at': '2026-10-29T10:00:00+08:00'}},
     {'actions': [{'op': 'set_time', 'clear_departure': True, 'quote': 'Keep settings'}]}),
    ('finish_by', '2026-10-29T04:00:00+00:00', {'time': {'mode': 'window',
     'departure_at': '2026-10-29T10:00:00+08:00', 'finish_by': '2026-10-29T12:00:00+08:00'}},
     {'actions': [{'op': 'set_time', 'mode': 'estimate', 'quote': 'Keep settings'}]}),
    ('max_walk_minutes', 15, {'max_walk_minutes': 15}, {'max_walk_minutes': None}),
])
def test_retained_global_parameter_requires_no_relevant_mutator(name, value, draft_fields, mutation):
    draft = Draft.model_validate(draft_fields)
    row = SemanticSegment(**segment(name, value, 'global', 'Keep settings'))
    parsed = Interpretation(acknowledgement='Keep settings', semantic_segments=[row])
    validate_segment_command_consistency([row], parsed, draft=draft)
    conflicting = Interpretation.model_validate({'acknowledgement': 'Keep settings',
        'semantic_segments': [row.model_dump()], **mutation})
    with pytest.raises(SemanticConsistencyError):
        validate_segment_command_consistency([row], conflicting, draft=draft)


@pytest.mark.parametrize('mutation', ['clear_visits', 'remove_visit'])
def test_existing_stay_cannot_be_retained_when_its_visit_is_removed(mutation):
    draft = Draft(visits=[Visit(visit_id='old-stop', point=point(), stay_minutes=45)])
    row = SemanticSegment(**segment('stay_minutes', 45, 'visit:1', 'Keep settings'))
    action = {'op': mutation, 'quote': 'Keep settings'}
    if mutation == 'remove_visit':
        action['target_id'] = 'old-stop'
    parsed = Interpretation.model_validate({'acknowledgement': 'Keep settings',
        'semantic_segments': [row.model_dump()], 'actions': [action]})
    with pytest.raises(SemanticConsistencyError):
        validate_segment_command_consistency([row], parsed, draft=draft)


def captured_attempt_three_output():
    # Exact public provider data saved in live_attempt3_missing_finish.json.
    raw = captured_attempt_two_output()
    raw['acknowledgement'] = 'Review your walking outing plan with two stops and a return to the start.'
    raw['commands']['add_visit'][0]['place']['categories'] = ['nature', 'heritage']
    del raw['commands']['set_finish_policy']
    raw['commands']['set_mode'] = [{'order': 3, 'quote': 'walking', 'transport_mode': 'walk'}]
    raw['commands']['clear_visits'] = []
    return raw


def test_captured_missing_finish_command_compiles_then_requires_candidate_confirmation():
    message = ('Plan a walking outing from this starting point. Visit Fort Canning Park for '
               '30 minutes, then Maxwell Food Centre for 45 minutes, and return to the start '
               'within three hours.')
    raw = captured_attempt_three_output()
    original = deepcopy(raw)
    body, cat, record, proposal = prepared(raw, message=message)
    assert raw == original and body.draft.finish_policy == 'last_stop'
    assert proposal['operation_adaptation']['parameter_actions_compiled'] == 1
    finish = [a for a in record['parsed'].actions if a.op == 'set_finish_policy']
    assert len(finish) == 1 and finish[0].value == 'return_to_start'
    assert finish[0].quote == 'return to the start'
    with pytest.raises(ChatError) as error:
        assemble(record, {}, strict=True, cat=cat)
    assert error.value.code == 'CONFIRM_PLACE'
    result = confirmed(record, cat)
    assert [v['stay_minutes'] for v in result['draft']['visits']] == [30, 45]
    assert result['draft']['finish_policy'] == 'return_to_start'
    assert result['draft']['time']['budget_minutes'] == 180
    assert result['draft']['mode'] == 'walk'


def test_global_parameter_compilation_integrates_with_parser_and_pending_roundtrip():
    from app.stage2c.command_contract import pending_to_commands
    from app.stage2c.operation_adapter import parse_provider_interpretation

    draft = Draft()
    raw = {'acknowledgement': 'Review cycling and return.', 'commands': {}, 'semantic_segments': [
        segment('transport_mode', 'cycle', 'global', 'Cycle'),
        segment('finish_policy', 'return_to_start', 'global', 'Return to the start'),
    ]}
    before = deepcopy(raw)
    parsed, norm = parse_provider_interpretation(raw, draft, turn_index=1)
    assert norm['parameter_actions_compiled'] == 2
    assert [(a.op, a.value, a.quote) for a in parsed.actions] == [
        ('set_mode', 'cycle', 'Cycle'), ('set_finish_policy', 'return_to_start', 'Return to the start')]
    assert 'max_walk_minutes' not in parsed.model_fields_set
    validate_segment_command_consistency(parsed.semantic_segments, parsed, draft=draft)
    pending = pending_to_commands(parsed, draft)
    assert pending['semantic_segments'][0]['turn_index'] == 1
    # Pending is local context, not a new provider response. A subsequent wire
    # response repeats the meaning/commands but cannot author local turn_index.
    next_response = deepcopy(pending)
    for item in next_response['semantic_segments']:
        item.pop('turn_index')
    again, norm = parse_provider_interpretation(next_response, draft, turn_index=2)
    assert norm['parameter_actions_compiled'] == 0
    assert [a.model_dump() for a in again.actions] == [a.model_dump() for a in parsed.actions]
    assert raw == before and draft.mode == 'walk' and draft.finish_policy == 'last_stop'


@pytest.mark.parametrize('change', [
    {'hardness': 'soft'}, {'source': 'inferred'}, {'status': 'unresolved'},
    {'type': 'preference'}, {'scope': 'visit:1'}, {'effect': 'clear', 'value': None},
    {'value': 'Cycle'}, {'value': ['cycle']},
    {'name': 'finish_policy', 'value': 'custom'},
    {'name': 'budget_minutes', 'value': 180},
])
def test_parameter_compiler_never_infers_authority_enum_scope_or_unsupported_field(change):
    from app.stage2c.semantic import compile_global_parameter_actions

    row = SemanticSegment(**{**segment('transport_mode', 'cycle', 'global', 'Cycle'), **change})
    parsed = Interpretation(acknowledgement='Review', semantic_segments=[row])
    before = parsed.model_dump()
    compiled, count = compile_global_parameter_actions(parsed, Draft())
    assert count == 0 and compiled.actions == []
    assert parsed.model_dump() == before


@pytest.mark.parametrize('competing', [
    {'value': 'drive'}, {'effect': 'clear', 'value': None}, {'status': 'unresolved'},
    {'hardness': 'soft'}, {'source': 'inferred'},
])
def test_parameter_compiler_does_not_choose_between_competing_same_name_rows(competing):
    from app.stage2c.semantic import compile_global_parameter_actions

    first = segment('transport_mode', 'cycle', 'global', 'Cycle')
    parsed = Interpretation(acknowledgement='Review', semantic_segments=[
        SemanticSegment(**first), SemanticSegment(**{**first, **competing})])
    compiled, count = compile_global_parameter_actions(parsed, Draft())
    assert count == 0 and compiled.actions == []
    with pytest.raises(SemanticConsistencyError):
        validate_segment_command_consistency(compiled.semantic_segments, compiled, draft=Draft())


@pytest.mark.parametrize('name,value,action', [
    ('transport_mode', 'cycle', {'op': 'set_mode', 'value': 'walk', 'quote': 'Cycle'}),
    ('finish_policy', 'return_to_start',
     {'op': 'set_finish', 'place': {'query': 'Custom end'}, 'quote': 'Return'}),
])
def test_parameter_compiler_does_not_replace_a_conflicting_existing_mutator(name, value, action):
    from app.stage2c.semantic import compile_global_parameter_actions

    parsed = Interpretation.model_validate({'acknowledgement': 'Review', 'actions': [action],
        'semantic_segments': [segment(name, value, 'global', action['quote'])]})
    compiled, count = compile_global_parameter_actions(parsed, Draft())
    assert count == 0
    assert [a.model_dump() for a in compiled.actions] == [a.model_dump() for a in parsed.actions]
    with pytest.raises(SemanticConsistencyError):
        validate_segment_command_consistency(compiled.semantic_segments, compiled, draft=Draft())


def test_compiled_parameter_still_requires_a_quote_from_the_actual_message():
    raw = {'acknowledgement': 'Review', 'commands': {}, 'semantic_segments': [
        segment('finish_policy', 'return_to_start', 'global', 'Words the user never said')]}
    with pytest.raises(ChatError) as error:
        prepared(raw, message='Keep the current itinerary.')
    assert error.value.code == 'UNSUPPORTED_EDIT_EVIDENCE'
    assert error.value.request_diagnostic['phase'] == 'action_validation'


def test_already_retained_or_duplicate_explicit_global_rows_do_not_add_duplicate_actions():
    from app.stage2c.operation_adapter import parse_provider_interpretation

    row = segment('transport_mode', 'walk', 'global', 'walking')
    raw = {'acknowledgement': 'Review', 'commands': {}, 'semantic_segments': [row, deepcopy(row)]}
    kept, norm = parse_provider_interpretation(raw, Draft())
    assert kept.actions == [] and norm['parameter_actions_compiled'] == 0
    for item in raw['semantic_segments']:
        item['value'] = 'cycle'
    changed, norm = parse_provider_interpretation(raw, Draft())
    assert len(changed.actions) == 1 and norm['parameter_actions_compiled'] == 1


@pytest.mark.parametrize('kind', ['constraint', 'preference'])
def test_new_duration_preserves_tentative_inferred_requirement_metadata(kind):
    message = 'Add Fort Canning Park. Maybe stay for 30 minutes.'
    raw = single_output(30)
    raw['commands']['add_visit'][0]['quote'] = message
    raw['semantic_segments'][0].update(type=kind, hardness='soft', source='inferred',
        quote='Maybe stay for 30 minutes')
    _, cat, record, _ = prepared(raw, message=message)
    result = confirmed(record, cat)
    rows = [row for row in result['preferences']['active_constraints'] if row['name'] == 'stay_minutes']
    assert len(rows) == 1
    assert (rows[0]['kind'], rows[0]['hardness'], rows[0]['source']) == (kind, 'soft', 'inferred')
    assert rows[0]['quote'] == 'Maybe stay for 30 minutes'
    assert result['draft']['visits'][0]['stay_minutes'] == 30
