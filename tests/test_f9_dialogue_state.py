from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.stage2c.catalogue import LocalCatalogue
from app.stage2c.engine import CallPacer, ChatError, prepare
from app.stage2c.models import ChatRequest, Draft, Preferences
from app.stage2c.engine import assemble, merge_preferences, selection_preferences
from app.stage2c.models import ActiveConstraint, Interpretation, SemanticSegment
from app.stage2c.operation_adapter import parse_provider_interpretation, OperationFormatError
from app.stage2c.time_updates import merge_time_updates
from app.stage2c.models import TimeAction
from app.stage2b.models import TimeSettings
from app.stage2c.models import LocationAction, PlaceSpec
from app.stage2b.models import Visit
from app.stage2c.semantic import unverified_preferences


def walking_preferences():
    return Preferences(active_constraints=[ActiveConstraint(
        name='max_walk_minutes', value=10, hardness='hard', quote='每段最多10分钟', turn_index=1)])


def test_null_without_explicit_clear_does_not_erase_a_saved_requirement():
    parsed = Interpretation(acknowledgement='少走路', semantic_segments=[SemanticSegment(
        **segment('max_walk_minutes', None, '少走路', effect='none'))])
    result = merge_preferences(walking_preferences(), parsed, turn_index=2)
    assert result.active_constraints[0].value == 10


def test_explicit_clear_updates_both_draft_and_ledger_without_mutating_inputs():
    message = '取消每段步行限制'
    body, record = interpret({
        'acknowledgement': '撤回限制', 'commands': {}, 'max_walk_minutes': None,
        'semantic_segments': [segment('max_walk_minutes', None, message, effect='clear')],
    }, message, draft=Draft(max_walk_minutes=10), preferences=walking_preferences())
    result = assemble(record[0], {}, strict=True, cat=LocalCatalogue(rows=[]))
    assert result['draft']['max_walk_minutes'] is None
    assert result['preferences']['active_constraints'] == []
    assert body.draft.max_walk_minutes == 10
    assert body.preferences.active_constraints[0].value == 10


def test_soft_clear_cannot_erase_a_hard_walk_limit_from_memory_or_draft():
    message = '如果可以，取消每段步行限制'
    _, (record, _) = interpret({
        'acknowledgement': '保留明确上限', 'commands': {},
        'semantic_segments': [segment('max_walk_minutes', None, message,
                                       hardness='soft', effect='clear')],
    }, message, draft=Draft(max_walk_minutes=10), preferences=walking_preferences())

    result = assemble(record, {}, strict=True)

    assert result['draft']['max_walk_minutes'] == 10
    assert [(row['name'], row['value'], row['hardness']) for row in
            result['preferences']['active_constraints']] == [('max_walk_minutes', 10, 'hard')]


def test_explicit_stay_command_updates_its_visit_scoped_constraint():
    message = '第一站改成停留60分钟'
    draft = Draft(visits=[Visit(visit_id='stop-1', stay_minutes=30, point={
        'label': 'Cafe', 'latitude': 1.3, 'longitude': 103.8,
        'source': 'user_map', 'confirmed': True,
    })])
    preferences = Preferences(active_constraints=[ActiveConstraint(
        name='stay_minutes', value=30, scope='visit_id:stop-1', hardness='hard',
        quote='第一站停留30分钟', turn_index=1,
    )])
    _, (record, _) = interpret({
        'acknowledgement': '更新第一站时长',
        'commands': {'set_stay': [{'order': 1, 'quote': message,
                                   'target_position': 1, 'minutes': 60}]},
        'semantic_segments': [],
    }, message, draft=draft, preferences=preferences)

    result = assemble(record, {}, strict=True)

    assert result['draft']['visits'][0]['stay_minutes'] == 60
    assert [(row['scope'], row['value']) for row in result['preferences']['active_constraints']
            if row['name'] == 'stay_minutes'] == [('visit_id:stop-1', 60)]


def test_zero_minutes_is_a_valid_visit_stay_value():
    message = '第一站停留0分钟'
    draft = Draft(visits=[Visit(visit_id='stop-1', stay_minutes=30, point={
        'label': 'Cafe', 'latitude': 1.3, 'longitude': 103.8,
        'source': 'user_map', 'confirmed': True,
    })])
    _, (record, _) = interpret({
        'acknowledgement': '停留时间设为0分钟',
        'commands': {'set_stay': [{'order': 1, 'quote': message,
                                   'target_position': 1, 'minutes': 0}]},
        'semantic_segments': [segment('stay_minutes', 0, message, scope='visit:1')],
    }, message, draft=draft)

    result = assemble(record, {}, strict=True)

    assert result['draft']['visits'][0]['stay_minutes'] == 0
    assert [(row['scope'], row['value']) for row in result['preferences']['active_constraints']
            if row['name'] == 'stay_minutes'] == [('visit_id:stop-1', 0)]


def test_local_exclusion_scope_is_rejected_instead_of_becoming_global():
    message = '第一站不要博物馆'
    with pytest.raises(OperationFormatError):
        parse_provider_interpretation({
            'acknowledgement': '按站点排除', 'commands': {},
            'semantic_segments': [segment('excluded_topic', 'museum', message,
                                          scope='visit:1')],
        }, Draft())


def test_soft_exclusion_is_not_promoted_to_a_hard_catalogue_filter():
    parsed = Interpretation(acknowledgement='尽量避开博物馆', semantic_segments=[SemanticSegment(
        **segment('excluded_topic', 'museum', '尽量避开博物馆', hardness='soft')
    )])

    result = merge_preferences(Preferences(), parsed, turn_index=1, draft=Draft())

    assert result.excluded == []
    assert result.active_constraints[0].hardness == 'soft'


def test_structured_themes_accumulate_and_replaying_the_same_update_is_idempotent():
    parsed = Interpretation(acknowledgement='亲子摄影', semantic_segments=[
        SemanticSegment(**segment('theme', theme, quote, type='preference', hardness='soft'))
        for theme, quote in [('family', '亲子'), ('photography', '摄影')]
    ])
    first = merge_preferences(Preferences(), parsed, turn_index=3)
    repeated = merge_preferences(first, parsed, turn_index=3)
    assert {(row.kind, row.value) for row in repeated.active_constraints} == {
        ('preference', 'family'), ('preference', 'photography')}
    assert len(repeated.active_constraints) == 2


class RecordedInterpreter:
    settings = SimpleNamespace(model='offline-recorded-interpreter')

    def __init__(self, output):
        self.output = output

    def generate_json(self, **kwargs):
        return {'data': deepcopy(self.output), 'metadata': {'provider': 'offline'}}


def segment(name, value, quote, **changes):
    return {
        'type': 'constraint', 'name': name, 'value': value, 'scope': 'global',
        'hardness': 'hard', 'status': 'understood', 'source': 'user_explicit',
        'quote': quote, 'effect': 'set', **changes,
    }


def interpret(output, message, *, draft=None, preferences=None, pending=None):
    body = ChatRequest(
        session_id='f' * 32, revision=0, message=message,
        draft=draft or Draft(), preferences=preferences or Preferences(),
        pending=pending, consent=True,
    )
    result = prepare(body, None, client=RecordedInterpreter(output),
                     cat=LocalCatalogue(rows=[]), pace=CallPacer(interval=0))
    return body, result


def test_new_model_output_cannot_assert_a_verified_constraint():
    message = '每段步行最多10分钟'
    output = {
        'acknowledgement': '记录上限', 'commands': {}, 'max_walk_minutes': 10,
        'semantic_segments': [segment('max_walk_minutes', 10, message)],
    }
    interpret(output, message)
    output['semantic_segments'][0]['status'] = 'satisfied'

    with pytest.raises(ChatError) as error:
        interpret(output, message)

    assert error.value.code == 'MODEL_SCHEMA'


def test_an_unrelated_question_does_not_verify_or_hide_an_accessibility_requirement():
    message = '必须全程无台阶'
    _, (record, response) = interpret({
        'acknowledgement': '保证全程无台阶', 'commands': {},
        'semantic_segments': [segment('wheelchair_access', 'no_stairs', message)],
        'questions': ['想吃什么？'],
    }, message)
    outcome = response['preview']['requirement_outcomes'][0]
    assert outcome['status'] == 'unsupported'
    assert outcome['name'] == 'wheelchair_access'
    assert any('accessibility' in value for value in response['preview']['warnings'])
    assert 'guaranteed' not in response['acknowledgement']


def test_new_message_turn_increases_without_adopting_a_new_revision():
    output = {'acknowledgement': '轻松一点', 'commands': {}, 'semantic_segments': [
        segment('pace', 'relaxed', '轻松一点', type='preference', hardness='soft')]}
    _, (_, first) = interpret(output, '轻松一点')
    _, (_, second) = interpret(output, '轻松一点')
    assert second['interpretation']['semantic_segments'][0]['turn_index'] > first['interpretation']['semantic_segments'][0]['turn_index']


def test_client_cannot_restore_a_satisfied_status_without_server_evidence():
    prefs = walking_preferences()
    prefs.active_constraints[0].status = 'satisfied'
    prefs.active_constraints[0].source = 'route'
    _, (_, response) = interpret({'acknowledgement': '继续', 'commands': {}, 'semantic_segments': []},
                                '继续', draft=Draft(max_walk_minutes=10), preferences=prefs)
    assert response['preview']['preferences']['active_constraints'][0]['status'] != 'satisfied'


def test_expired_verification_does_not_replace_inferred_intent_source():
    prefs = Preferences(active_constraints=[ActiveConstraint(
        name='budget_minutes', value=180, hardness='hard', status='satisfied',
        source='inferred', evidence_source='route', quote='大概三小时', turn_index=1,
    )])

    clean = unverified_preferences(prefs)

    assert clean.active_constraints[0].source == 'inferred'
    assert clean.active_constraints[0].evidence_source is None
    assert clean.active_constraints[0].status == 'understood'


def test_matching_verification_records_evidence_separately_from_intent_source():
    from app.stage2c.semantic import restore_verified_preferences
    from app.verification import condition_fingerprint

    preferences = Preferences(active_constraints=[ActiveConstraint(
        name='budget_minutes', value=180, hardness='hard', source='inferred',
        quote='差不多三小时', turn_index=1,
    )])
    fingerprint = condition_fingerprint('budget_minutes', 180, 'global')

    class Store:
        def query(self, *args, **kwargs):
            return {'checks': {fingerprint: {'status': 'satisfied', 'source': 'route'}}}, 'matched'

    clean, result = restore_verified_preferences(
        Store(), 'a' * 32, 'b' * 32, 0, Draft(), preferences)

    assert clean.active_constraints[0].source == 'inferred'
    assert clean.active_constraints[0].evidence_source == 'route'
    assert clean.active_constraints[0].status == 'satisfied'
    assert result['checked_requirements'] == 1


def test_future_only_exclusion_preserves_an_existing_museum_visit():
    draft = Draft.model_validate({'visits': [{'visit_id': 'old-museum', 'point': {
        'label': 'Museum', 'latitude': 1.3, 'longitude': 103.8, 'source': 'user_map', 'confirmed': True}}]})
    message = '之后不要再加博物馆，原来的保留'
    _, (record, _) = interpret({'acknowledgement': '仅排除后续新增', 'commands': {},
        'semantic_segments': [segment('excluded_topic', 'museum', message, scope='future_visits')]}, message, draft=draft)
    result = assemble(record, {}, strict=True)
    assert result['draft']['visits'][0]['visit_id'] == 'old-museum'
    assert result['preferences']['excluded'] == ['museum']


def test_cancelling_takeaway_updates_existing_visit_and_removes_stale_note():
    draft = Draft.model_validate({'visits': [{'visit_id': 'coffee', 'stay_profile': 'takeaway', 'point': {
        'label': 'Coffee', 'latitude': 1.3, 'longitude': 103.8, 'source': 'user_map', 'confirmed': True}}]})
    prefs = Preferences(notes=['外带咖啡，买完就走'], active_constraints=[ActiveConstraint(
        name='takeaway', value=True, scope='visit_id:coffee', hardness='hard', quote='外带', turn_index=1)])
    message = '第一站取消外带'
    _, (record, _) = interpret({'acknowledgement': '取消外带', 'commands': {},
        'semantic_segments': [segment('takeaway', None, message, scope='visit:1', effect='clear')]},
        message, draft=draft, preferences=prefs)
    result = assemble(record, {}, strict=True)
    assert result['draft']['visits'][0]['stay_profile'] == 'regular'
    assert result['preferences']['notes'] == []
    assert result['preferences']['active_constraints'] == []


@pytest.mark.parametrize('effect,value', [('clear', None), ('set', False)])
def test_soft_takeaway_changes_preserve_the_hard_profile_and_note(effect, value):
    draft = Draft(visits=[{'visit_id': 'coffee', 'stay_profile': 'takeaway', 'point': {
        'label': 'Coffee', 'latitude': 1.3, 'longitude': 103.8,
        'source': 'user_map', 'confirmed': True}}])
    prefs = Preferences(notes=['外带咖啡，买完就走'], active_constraints=[ActiveConstraint(
        name='takeaway', value=True, scope='visit_id:coffee', hardness='hard', quote='外带', turn_index=1)])
    message = '第一站或许可以坐下喝'
    _, (record, _) = interpret({'acknowledgement': '记录偏好', 'commands': {},
        'semantic_segments': [segment('takeaway', value, message, scope='visit:1',
                                      effect=effect, hardness='soft', source='inferred')]},
        message, draft=draft, preferences=prefs)

    result = assemble(record, {}, strict=True)

    assert result['draft']['visits'][0]['stay_profile'] == 'takeaway'
    assert result['preferences']['notes'] == prefs.notes
    hard_rows = [row for row in result['preferences']['active_constraints'] if row['hardness'] == 'hard']
    assert len(hard_rows) == 1
    assert hard_rows[0]['value'] is True
    if effect == 'set':
        soft_row = next(row for row in result['preferences']['active_constraints'] if row['hardness'] == 'soft')
        assert soft_row['source'] == 'inferred'
        assert soft_row['value'] is False


def test_compiled_takeaway_preserves_the_semantic_source():
    draft = Draft(visits=[{'visit_id': 'coffee', 'point': {
        'label': 'Coffee', 'latitude': 1.3, 'longitude': 103.8,
        'source': 'user_map', 'confirmed': True}}])
    message = '第一站买完就走'
    _, (record, _) = interpret({'acknowledgement': '外带', 'commands': {},
        'semantic_segments': [segment('takeaway', True, message, scope='visit:1', source='inferred')]},
        message, draft=draft)

    result = assemble(record, {}, strict=True)

    assert result['draft']['visits'][0]['stay_profile'] == 'takeaway'
    row, = result['preferences']['active_constraints']
    assert row['source'] == 'inferred'
    assert row['hardness'] == 'hard'


def test_preserving_a_visit_blocks_an_uncommanded_takeaway_change():
    message = '保留第一站，取消外带'
    preserve = {
        'type': 'preserve', 'name': 'preserve_visit', 'value': None, 'scope': 'visit:1',
        'hardness': 'unknown', 'status': 'understood', 'source': 'user_explicit',
        'quote': '保留第一站', 'effect': 'none',
    }
    with pytest.raises(ChatError):
        interpret({'acknowledgement': '取消外带', 'commands': {},
            'semantic_segments': [preserve, segment('takeaway', None, '取消外带',
                                                     scope='visit:1', effect='clear')]},
            message, draft=Draft(visits=[{'visit_id': 'coffee', 'stay_profile': 'takeaway', 'point': {
                'label': 'Coffee', 'latitude': 1.3, 'longitude': 103.8,
                'source': 'user_map', 'confirmed': True}}]))


@pytest.mark.parametrize('update',[
    {'source':'route'},{'status':'satisfied'},{'quote':'  '},{'turn_index':4},
    {'scope':'visit_id:invented'},{'effect':'set','value':None},{'value':True},
])
def test_live_provider_permissions_are_stricter_than_saved_state(update):
    row={**segment('max_walk_minutes',10,'最多10分钟'),**update}
    with pytest.raises(OperationFormatError):
        parse_provider_interpretation({'acknowledgement':'proposal','commands':{},'semantic_segments':[row]},Draft())


def test_equivalent_offset_instants_do_not_create_a_time_conflict():
    actions=[TimeAction(op='set_time',quote='出发时间',departure_at=value) for value in
             ('2026-10-28T01:00:00+00:00','2026-10-28T09:00:00+08:00')]
    result=merge_time_updates(TimeSettings(),actions)
    assert result.settings.departure_at.isoformat()=='2026-10-28T09:00:00+08:00'


def test_undated_deadline_stays_pending_without_inventing_a_date():
    message='六点之前结束'
    _,(record,response)=interpret({'acknowledgement':'需要日期','commands':{},'questions':['是哪一天？'],
        'semantic_segments':[segment('finish_by','18:00',message,status='unresolved')]},message)
    assert response['preview']['can_apply'] is False
    assert response['preview']['draft']['time']['finish_by'] is None
    with pytest.raises(ChatError):
        assemble(record,{},strict=True)


def test_ambiguous_new_visit_scope_cannot_silently_drop_a_hard_requirement():
    parsed=Interpretation(acknowledgement='two cafes',semantic_segments=[SemanticSegment(
        **segment('place_type','cafe','咖啡馆',scope='visit:new'))],actions=[
        LocationAction(op='add_visit',place=PlaceSpec(categories=['food']),quote='咖啡馆') for _ in range(2)])
    with pytest.raises(ChatError):
        merge_preferences(Preferences(),parsed,draft=Draft(),new_visit_ids=['new-a','new-b'],turn_index=1)


def test_expired_route_violation_does_not_turn_into_a_user_semantic_conflict():
    prefs=Preferences(active_constraints=[ActiveConstraint(name='budget_minutes',value=180,
        hardness='hard',status='conflicting',source='route',quote='最多三小时',turn_index=1)])
    clean=unverified_preferences(prefs)
    assert clean.active_constraints[0].status=='understood'


def test_soft_parameter_change_cannot_replace_an_older_hard_executable_limit():
    message='最好放宽到20分钟'
    _,(record,response)=interpret({'acknowledgement':'放宽建议','commands':{},'max_walk_minutes':20,
        'semantic_segments':[segment('max_walk_minutes',20,message,hardness='soft')]},
        message,draft=Draft(max_walk_minutes=10),preferences=walking_preferences())
    assert response['preview']['can_apply'] is False
    with pytest.raises(ChatError):
        assemble(record,{},strict=True)


def test_each_new_visit_requirement_binds_to_a_different_reserved_identity():
    parsed=Interpretation(acknowledgement='three interests',semantic_segments=[
        SemanticSegment(**segment('drink',value,value,type='preference',scope=f'visit:new:{i}',hardness='soft'))
        for i,value in enumerate(['coffee','tea','juice'],1)])
    result=merge_preferences(Preferences(),parsed,draft=Draft(),new_visit_ids=['a','b','c'],turn_index=1)
    assert {(row.scope,row.value) for row in result.active_constraints}=={
        ('visit_id:a','coffee'),('visit_id:b','tea'),('visit_id:c','juice')}


def test_selection_context_keeps_visit_preferences_on_their_own_candidate_slot():
    preferences = Preferences(active_constraints=[
        ActiveConstraint(name='drink', value='coffee', scope='visit_id:cafe-slot',
                         hardness='soft', quote='coffee', turn_index=1),
        ActiveConstraint(name='drink', value='tea', scope='visit_id:tea-slot',
                         hardness='soft', quote='tea', turn_index=1),
    ])

    cafe = selection_preferences(preferences, {
        'operation': 'add_visit', 'target_visit_id': 'cafe-slot',
    })
    tea = selection_preferences(preferences, {
        'operation': 'add_visit', 'target_visit_id': 'tea-slot',
    })

    assert [row['value'] for row in cafe['active_constraints']] == ['coffee']
    assert [row['value'] for row in tea['active_constraints']] == ['tea']


@pytest.mark.parametrize('status',['unsupported','unresolved'])
def test_model_status_cannot_bypass_a_supported_numeric_budget(status):
    message='整个行程最多三小时'
    with pytest.raises(ChatError) as error:
        interpret({'acknowledgement':'记录预算','commands':{},'questions':['想吃什么？'],
            'semantic_segments':[segment('budget_minutes',180,message,status=status)]},message)
    assert error.value.code=='SEMANTIC_COMMAND_MISMATCH'


def test_provider_can_clear_one_set_member_without_clearing_the_whole_field():
    message = '取消博物馆排除'
    row = segment('excluded_topic', 'museum', message, effect='clear')

    parsed, _ = parse_provider_interpretation({
        'acknowledgement': '仅取消博物馆', 'commands': {},
        'semantic_segments': [row],
    }, Draft())

    assert parsed.semantic_segments[0].value == 'museum'
    assert parsed.semantic_segments[0].effect == 'clear'


def test_provider_requires_null_when_clearing_a_scalar_field():
    message = '取消步行上限'
    row = segment('max_walk_minutes', 10, message, effect='clear')

    with pytest.raises(OperationFormatError):
        parse_provider_interpretation({
            'acknowledgement': '取消上限', 'commands': {},
            'semantic_segments': [row],
        }, Draft())
