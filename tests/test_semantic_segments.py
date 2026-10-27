import pytest

from app.stage2c.models import (
    ActiveConstraint,
    Interpretation,
    LocationAction,
    PlaceSpec,
    SemanticSegment,
)
from app.stage2c.semantic import (
    SemanticConsistencyError,
    merge_active_constraints,
    validate_segment_command_consistency,
)
from app.stage2c.engine import merge_preferences
from app.stage2c.models import Preferences
from app.stage2c.models import Draft, MoveAction


def test_constraint_segment_keeps_scope_and_provenance():
    segment = SemanticSegment(
        type='constraint',
        name='place_type',
        value='cafe',
        scope='visit:2',
        hardness='hard',
        status='understood',
        source='user_explicit',
        quote='第二站换成咖啡馆',
    )

    assert segment.scope == 'visit:2'
    assert segment.name == 'place_type'
    assert segment.quote == '第二站换成咖啡馆'


def test_new_constraint_replaces_an_older_constraint_in_the_same_scope():
    active = [
        ActiveConstraint(
            name='place_type', value='restaurant', scope='visit:2',
            hardness='hard', status='unresolved', source='user_explicit',
            quote='第二站找餐厅', turn_index=1,
        )
    ]
    incoming = [
        SemanticSegment(
            type='constraint', name='place_type', value='cafe', scope='visit:2',
            hardness='hard', status='understood', source='user_explicit',
            quote='第二站换成咖啡馆',
        )
    ]

    result = merge_active_constraints(active, incoming, turn_index=2)

    assert len(result) == 1
    assert result[0].value == 'cafe'
    assert result[0].turn_index == 2


def test_soft_preference_does_not_erase_an_older_hard_constraint():
    active = [ActiveConstraint(
        name='place_type', value='cafe', scope='visit:2', hardness='hard',
        status='understood', source='user_explicit', quote='必须是咖啡馆', turn_index=1,
    )]
    incoming = [SemanticSegment(
        type='constraint', name='place_type', value='restaurant', scope='visit:2',
        hardness='soft', status='understood', source='user_explicit', quote='最好是餐厅',
    )]

    result = merge_active_constraints(active, incoming, turn_index=2)

    assert {item.value for item in result} == {'cafe', 'restaurant'}
    assert any(item.hardness == 'hard' for item in result)


def test_explicit_constraint_removal_clears_the_active_scope():
    active = [ActiveConstraint(
        name='max_walk_minutes', value=10, scope='global', hardness='hard',
        status='understood', source='user_explicit', quote='每段不超过10分钟', turn_index=1,
    )]
    incoming = [SemanticSegment(
        type='constraint', name='max_walk_minutes', value=None, scope='global',
        hardness='hard', status='understood', source='user_explicit', quote='取消步行限制',
    )]

    assert merge_active_constraints(active, incoming, turn_index=2) == []


def test_conflicting_hard_constraints_are_kept_as_conflict():
    incoming = [
        SemanticSegment(
            type='constraint', name='place_type', value='cafe', scope='visit:2',
            hardness='hard', status='understood', source='user_explicit',
            quote='第二站必须是咖啡馆',
        ),
        SemanticSegment(
            type='constraint', name='place_type', value='museum', scope='visit:2',
            hardness='hard', status='understood', source='user_explicit',
            quote='第二站必须是博物馆',
        ),
    ]

    result = merge_active_constraints([], incoming, turn_index=3)

    assert len(result) == 2
    assert {item.status for item in result} == {'conflicting'}


def test_a_soft_alternative_in_the_same_turn_cannot_replace_or_conflict_with_hard_value():
    incoming = [
        SemanticSegment(type='constraint', name='place_type', value='cafe', scope='visit:2',
                        hardness='hard', quote='必须是咖啡馆'),
        SemanticSegment(type='constraint', name='place_type', value='restaurant', scope='visit:2',
                        hardness='soft', quote='如果可能也看看餐厅'),
    ]

    result = merge_active_constraints([], incoming, turn_index=1)

    assert {(item.value, item.hardness, item.status) for item in result} == {
        ('cafe', 'hard', 'understood'), ('restaurant', 'soft', 'understood'),
    }


def test_provider_cannot_claim_opening_hours_were_satisfied():
    incoming = [SemanticSegment(
        type='constraint', name='must_be_open', value='14:00', scope='visit:2',
        hardness='hard', status='satisfied', source='user_explicit',
        quote='下午两点必须营业',
    )]

    result = merge_active_constraints([], incoming, turn_index=3)

    assert result[0].status == 'unresolved'


def test_unsupported_constraint_does_not_become_understood_in_the_ledger():
    segment = SemanticSegment(type='constraint', name='wheelchair_access', value='no_stairs',
                              hardness='hard', status='unsupported', quote='必须全程无台阶')

    result = merge_active_constraints([], [segment], turn_index=1)

    assert result[0].status == 'unsupported'


def test_matching_hard_constraint_requires_a_supporting_command():
    segments = [
        SemanticSegment(
            type='constraint', name='place_type', value='cafe', scope='visit:2',
            hardness='hard', status='understood', source='user_explicit',
            quote='第二站换成咖啡馆',
        )
    ]
    parsed = Interpretation(
        acknowledgement='替换第二站',
        actions=[
            LocationAction(
                op='replace_visit', target_id='visit-2',
                place=PlaceSpec(categories=['food'], keywords=['cafe']),
                quote='第二站换成咖啡馆',
            )
        ],
    )

    validate_segment_command_consistency(segments, parsed)


def test_hard_constraint_without_command_or_question_fails_closed():
    segments = [
        SemanticSegment(
            type='constraint', name='place_type', value='cafe', scope='visit:2',
            hardness='hard', status='understood', source='user_explicit',
            quote='第二站换成咖啡馆',
        )
    ]
    parsed = Interpretation(acknowledgement='未生成修改')

    with pytest.raises(SemanticConsistencyError):
        validate_segment_command_consistency(segments, parsed)


def test_matching_quote_does_not_excuse_the_wrong_place_type():
    segment = SemanticSegment(
        type='constraint', name='place_type', value='cafe', scope='visit:2',
        hardness='hard', quote='第二站换成咖啡馆',
    )
    parsed = Interpretation(acknowledgement='替换第二站', actions=[LocationAction(
        op='replace_visit', target_id='visit-2',
        place=PlaceSpec(categories=['nature']), quote='第二站换成咖啡馆',
    )])

    with pytest.raises(SemanticConsistencyError):
        validate_segment_command_consistency([segment], parsed)


def test_a_hard_trip_budget_is_supported_by_the_time_command_value():
    from app.stage2c.models import TimeAction

    segment = SemanticSegment(type='constraint', name='budget_minutes', value=240,
                              hardness='hard', quote='整个行程最多四小时')
    parsed = Interpretation(acknowledgement='预算', actions=[TimeAction(
        op='set_time', mode='budget', budget_minutes=240, quote='整个行程最多四小时',
    )])

    validate_segment_command_consistency([segment], parsed)
    parsed.actions[0].budget_minutes = 120
    with pytest.raises(SemanticConsistencyError):
        validate_segment_command_consistency([segment], parsed)


def test_conflicting_hard_constraint_is_safe_when_a_question_is_returned():
    parsed = Interpretation(acknowledgement='需要澄清', questions=['第二站要咖啡馆还是博物馆？'])
    segments = [SemanticSegment(
        type='constraint', name='place_type', value='cafe', scope='visit:2',
        hardness='hard', status='conflicting', source='user_explicit',
        quote='第二站必须是咖啡馆',
    )]

    validate_segment_command_consistency(segments, parsed)


def test_preference_merge_keeps_active_constraints_as_structured_state():
    parsed = Interpretation(
        acknowledgement='记录咖啡馆要求',
        semantic_segments=[SemanticSegment(
            type='constraint', name='place_type', value='cafe', scope='visit:2',
            hardness='hard', status='understood', source='user_explicit',
            quote='第二站换成咖啡馆',
        )],
    )

    result = merge_preferences(Preferences(), parsed, turn_index=4)

    assert len(result.active_constraints) == 1
    assert result.active_constraints[0].scope == 'visit:2'
    assert result.active_constraints[0].turn_index == 4


def test_visit_constraint_follows_the_same_visit_after_reordering():
    draft = Draft.model_validate({'visits': [
        {'visit_id': f'stop-{index}', 'point': {
            'label': f'Synthetic {index}', 'latitude': 1.3, 'longitude': 103.8,
            'source': 'user_map', 'confirmed': True,
        }} for index in (1, 2)
    ]})
    old = Preferences(active_constraints=[ActiveConstraint(
        name='place_type', value='cafe', scope='visit:2', hardness='hard',
        quote='第二站是咖啡馆', turn_index=1,
    )])
    move = Interpretation(acknowledgement='调整顺序', actions=[MoveAction(
        op='move_visit', target_id='stop-2', position=1, quote='第二站移到第一位',
    )])
    after_move = merge_preferences(old, move, draft=draft, turn_index=2)
    draft.visits.reverse()
    replace = Interpretation(acknowledgement='换餐厅', semantic_segments=[SemanticSegment(
        type='constraint', name='place_type', value='restaurant', scope='visit:1',
        hardness='hard', quote='现在第一站改餐厅',
    )])

    after_replace = merge_preferences(after_move, replace, draft=draft, turn_index=3)

    assert len(after_replace.active_constraints) == 1
    assert after_replace.active_constraints[0].scope == 'visit_id:stop-2'
    assert after_replace.active_constraints[0].value == 'restaurant'


def test_removing_a_visit_removes_only_its_scoped_constraints():
    from app.stage2c.models import RemoveAction
    draft = Draft.model_validate({'visits': [{'visit_id': 'stop-2', 'point': {
        'label': 'Synthetic', 'latitude': 1.3, 'longitude': 103.8,
        'source': 'user_map', 'confirmed': True,
    }}]})
    old = Preferences(active_constraints=[
        ActiveConstraint(name='place_type', value='cafe', scope='visit_id:stop-2',
                         hardness='hard', quote='咖啡馆', turn_index=1),
        ActiveConstraint(name='max_walk_minutes', value=10, hardness='hard',
                         quote='最多10分钟', turn_index=1),
    ])
    parsed = Interpretation(acknowledgement='删除', actions=[RemoveAction(
        op='remove_visit', target_id='stop-2', quote='删除第一站',
    )])

    result = merge_preferences(old, parsed, draft=draft, turn_index=2)

    assert [item.name for item in result.active_constraints] == ['max_walk_minutes']


def test_new_visit_constraints_bind_to_the_reserved_visit_identifier():
    parsed = Interpretation(acknowledgement='添加咖啡馆', semantic_segments=[SemanticSegment(
        type='constraint', name='place_type', value='cafe', scope='visit:new',
        hardness='hard', quote='加一家咖啡馆',
    )])

    result = merge_preferences(Preferences(), parsed, draft=Draft(),
                               new_visit_ids=['reserved-stop'], turn_index=1)

    assert result.active_constraints[0].scope == 'visit_id:reserved-stop'


def test_a_preserve_segment_blocks_a_conflicting_location_edit():
    draft = Draft.model_validate({'visits': [{'visit_id': 'first', 'point': {
        'label': 'Synthetic', 'latitude': 1.3, 'longitude': 103.8,
        'source': 'user_map', 'confirmed': True,
    }}]})
    preserve = SemanticSegment(type='preserve', name='preserve_visit', scope='visit:1',
                               hardness='hard', quote='第一站不动')
    parsed = Interpretation(acknowledgement='替换', actions=[LocationAction(
        op='replace_visit', target_id='first', place=PlaceSpec(categories=['food']),
        quote='第一站不动',
    )])

    with pytest.raises(SemanticConsistencyError):
        validate_segment_command_consistency([preserve], parsed, draft=draft)


@pytest.mark.parametrize('name,value,action', [
    ('transport_mode', 'cycle', {'op': 'set_mode', 'value': 'cycle', 'quote': 'cycle'}),
    ('departure_at', '2026-10-28T09:00:00+08:00', {
        'op': 'set_time', 'departure_at': '2026-10-28T09:00:00+08:00', 'quote': 'departure',
    }),
])
def test_existing_executable_fields_do_not_require_an_unnecessary_question(name, value, action):
    parsed = Interpretation.model_validate({'acknowledgement': 'proposal', 'actions': [action]})
    segment = SemanticSegment(type='constraint', name=name, value=value,
                              hardness='hard', quote=action['quote'])

    validate_segment_command_consistency([segment], parsed)


def test_broad_park_intent_uses_the_existing_retrieval_alias():
    parsed = Interpretation(acknowledgement='公园', actions=[LocationAction(
        op='add_visit', place=PlaceSpec(categories=['nature']), quote='找个公园',
    )])
    segment = SemanticSegment(type='constraint', name='place_type', value='park',
                              scope='visit:new', hardness='hard', quote='公园')

    validate_segment_command_consistency([segment], parsed)


def test_preserving_the_location_allows_a_stay_edit_but_rejects_a_replacement():
    from app.stage2c.models import StayAction
    draft = Draft.model_validate({'visits': [{'visit_id': 'first', 'point': {
        'label': 'Synthetic', 'latitude': 1.3, 'longitude': 103.8,
        'source': 'user_map', 'confirmed': True,
    }}]})
    preserve = SemanticSegment(type='preserve', name='preserve_location', scope='visit:1',
                               hardness='hard', quote='Keep the same place')
    parsed = Interpretation(acknowledgement='stay', actions=[StayAction(
        op='set_stay', target_id='first', minutes=45, quote='spend 45 minutes',
    )])
    validate_segment_command_consistency([preserve], parsed, draft=draft)
    parsed.actions = [LocationAction(op='replace_visit', target_id='first',
                                     place=PlaceSpec(categories=['food']), quote='Keep the same place')]
    with pytest.raises(SemanticConsistencyError):
        validate_segment_command_consistency([preserve], parsed, draft=draft)
