from app.stage2c.command_contract import scoped_schema
from app.stage2c.operation_adapter import parse_interpretation
from app.stage2c.models import Draft


def test_provider_schema_exposes_semantic_segments():
    schema = scoped_schema()

    assert 'semantic_segments' in schema['properties']
    assert 'semantic_segments' in schema['required']
    item = schema['properties']['semantic_segments']['items']
    assert item['additionalProperties'] is False
    assert 'type' in item['required']
    assert 'quote' in item['required']


def test_operation_adapter_preserves_segments_when_compiling_commands():
    raw = {
        'acknowledgement': '替换第二站',
        'semantic_segments': [{
            'type': 'constraint', 'name': 'place_type', 'value': 'cafe',
            'scope': 'visit:2', 'hardness': 'hard', 'status': 'understood',
            'source': 'user_explicit', 'quote': '第二站换成咖啡馆',
        }],
        'commands': {
            'replace_visit': [{
                'order': 1, 'quote': '第二站换成咖啡馆', 'target_position': 2,
                'place': {'categories': ['food'], 'keywords': ['cafe']},
            }]
        },
    }
    draft = Draft.model_validate({
        'visits': [
            {'visit_id': 'visit-1', 'point': {'label': 'A', 'latitude': 1.3, 'longitude': 103.8,
                                                'source': 'user_map', 'confirmed': True}},
            {'visit_id': 'visit-2', 'point': {'label': 'B', 'latitude': 1.31, 'longitude': 103.81,
                                                'source': 'user_map', 'confirmed': True}},
        ]
    })

    parsed, _ = parse_interpretation(raw, draft, turn_index=9)

    assert parsed.semantic_segments[0].value == 'cafe'
    assert parsed.semantic_segments[0].turn_index == 9
    assert parsed.actions[0].target_id == 'visit-2'
