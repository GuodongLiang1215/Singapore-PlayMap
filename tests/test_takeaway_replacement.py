import pytest
from app.stage2c.engine import assemble
from app.stage2c.models import ChatRequest, Draft, Interpretation, LocationAction, PlaceSpec, Preferences, SemanticSegment
from app.stage2c.semantic import compile_takeaway_actions


@pytest.mark.parametrize('message,action_quote,segment_value,expected_profile', [
    ('第二站换成咖啡馆，顺路买杯带走', '第二站换成咖啡馆，顺路买杯带走', True, 'takeaway'),
    ('第二站换成咖啡馆，顺路买杯带走', '第二站换成咖啡馆', True, 'takeaway'),
    ('第二站换成咖啡馆，不要外带', '第二站换成咖啡馆，不要外带', False, 'regular'),
])
def test_replacing_a_stop_respects_the_scoped_takeaway_intent(message, action_quote, segment_value, expected_profile):
    body = ChatRequest(
        session_id='a' * 32,
        revision=1,
        message=message,
        draft=Draft.model_validate({'visits': [
            {'visit_id': 'visit-1', 'point': {'label': 'A', 'latitude': 1.30, 'longitude': 103.80,
                                               'source': 'user_map', 'confirmed': True}},
            {'visit_id': 'visit-2', 'point': {'label': 'B', 'latitude': 1.31, 'longitude': 103.81,
                                               'source': 'user_map', 'confirmed': True}},
        ]}),
        preferences=Preferences(),
        consent=True,
    )
    parsed = Interpretation(
        acknowledgement='替换第二站',
        semantic_segments=[SemanticSegment(
            type='constraint', name='takeaway', value=segment_value, scope='visit:2',
            hardness='hard', quote=message, effect='set',
        )],
        actions=[LocationAction(
            op='replace_visit', target_id='visit-2',
            place=PlaceSpec(categories=['food'], keywords=['cafe']),
            quote=action_quote,
        )],
    )
    parsed = compile_takeaway_actions(parsed, body.draft)
    option_point = {'label': 'Cafe C', 'latitude': 1.32, 'longitude': 103.82,
                    'source': 'user_map', 'confirmed': True}
    record = {
        'body': body,
        'parsed': parsed,
        'preferences': body.preferences,
        'slots': [{'action_index': 0, 'slot_id': 's0', 'options': [
            {'candidate_key': 's0c0', 'point': option_point}
        ], 'selected_key': 's0c0'}],
        'new_ids': {},
        'warnings': [],
    }

    result = assemble(record, {}, cat=None)

    assert result['draft']['visits'][1]['stay_profile'] == expected_profile
    assert result['draft']['visits'][1]['stay_minutes'] is None
