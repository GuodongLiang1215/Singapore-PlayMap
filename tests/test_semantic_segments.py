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


def test_provider_cannot_claim_opening_hours_were_satisfied():
    incoming = [SemanticSegment(
        type='constraint', name='must_be_open', value='14:00', scope='visit:2',
        hardness='hard', status='satisfied', source='user_explicit',
        quote='下午两点必须营业',
    )]

    result = merge_active_constraints([], incoming, turn_index=3)

    assert result[0].status == 'unresolved'


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
