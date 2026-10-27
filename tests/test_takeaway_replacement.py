from app.stage2c.engine import assemble
from app.stage2c.models import ChatRequest, Draft, Interpretation, LocationAction, PlaceSpec, Preferences


def test_replacing_a_stop_with_takeaway_updates_its_stay_profile():
    body = ChatRequest(
        session_id='a' * 32,
        revision=1,
        message='第二站换成咖啡馆，顺路买杯带走',
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
        actions=[LocationAction(
            op='replace_visit', target_id='visit-2',
            place=PlaceSpec(categories=['food'], keywords=['cafe']),
            quote='第二站换成咖啡馆，顺路买杯带走',
        )],
    )
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

    assert result['draft']['visits'][1]['stay_profile'] == 'takeaway'
    assert result['draft']['visits'][1]['stay_minutes'] is None
