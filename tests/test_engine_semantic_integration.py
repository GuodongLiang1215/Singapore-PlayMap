from types import SimpleNamespace
import pytest

from app.stage2c.engine import CallPacer, ChatError, assemble, prepare
from app.stage2c.models import ChatRequest, Draft, Preferences


class FakeClient:
    settings = SimpleNamespace(model='test-model')

    def generate_json(self, *, instruction, payload, schema):
        return {
            'data': {
                'acknowledgement': '记录每段步行限制',
                'semantic_segments': [{
                    'type': 'constraint', 'name': 'max_walk_minutes', 'value': 10,
                    'scope': 'global', 'hardness': 'hard', 'status': 'understood','effect':'set',
                    'source': 'user_explicit', 'quote': '每段步行不要超过10分钟',
                }],
                'max_walk_minutes': 10,
                'commands': {},
            },
            'metadata': {'provider': 'test'},
        }


class MismatchClient(FakeClient):
    def generate_json(self, *, instruction, payload, schema):
        result = super().generate_json(instruction=instruction, payload=payload, schema=schema)
        result['data']['semantic_segments'][0]['name'] = 'budget_minutes'
        result['data']['semantic_segments'][0]['value'] = 180
        result['data']['semantic_segments'][0]['quote'] = '整个行程最多三小时'
        result['data']['max_walk_minutes'] = None
        return result


def test_prepare_round_trips_a_constraint_into_the_pending_preferences():
    body = ChatRequest(
        session_id='b' * 32,
        revision=0,
        message='每段步行不要超过10分钟',
        draft=Draft(),
        preferences=Preferences(),
        consent=True,
    )

    record, response = prepare(
        body, None, client=FakeClient(), pace=CallPacer(interval=0)
    )

    assert record['preferences'].active_constraints[0].name == 'max_walk_minutes'
    assert record['preferences'].active_constraints[0].status == 'understood'
    assert response['preview']['preferences']['active_constraints'][0]['value'] == 10


def test_prepare_rejects_a_hard_constraint_without_a_command_or_question():
    body = ChatRequest(
        session_id='c' * 32,
        revision=0,
        message='整个行程最多三小时',
        draft=Draft(),
        preferences=Preferences(),
        consent=True,
    )

    with pytest.raises(ChatError) as error:
        prepare(body, None, client=MismatchClient(), pace=CallPacer(interval=0))

    assert error.value.code == 'SEMANTIC_COMMAND_MISMATCH'


class ClearWalkClient(FakeClient):
    def generate_json(self, **kwargs):
        return {'data': {
            'acknowledgement': '取消步行限制', 'commands': {}, 'max_walk_minutes': None,
            'semantic_segments': [{
                'type': 'constraint', 'name': 'max_walk_minutes', 'value': None,
                'scope': 'global', 'hardness': 'hard', 'quote': '取消步行限制',
                'status':'understood','source':'user_explicit','effect':'clear',
            }],
        }, 'metadata': {'provider': 'test'}}


def test_cancelling_walk_limit_clears_the_executable_draft_limit_after_adoption():
    body = ChatRequest(
        session_id='d' * 32, revision=2, message='取消步行限制，其他条件不变。',
        draft=Draft(max_walk_minutes=10), consent=True,
    )

    record, response = prepare(body, None, client=ClearWalkClient(), pace=CallPacer(interval=0))
    adopted = assemble(record, {}, strict=True)

    assert adopted['draft']['max_walk_minutes'] is None
    assert response['preview']['preferences']['active_constraints'] == []
    assert body.draft.max_walk_minutes == 10


def test_same_quote_and_category_cannot_support_a_different_visit_target():
    from app.stage2c.models import Interpretation, SemanticSegment, LocationAction, PlaceSpec
    from app.stage2c.semantic import SemanticConsistencyError, validate_segment_command_consistency

    draft = Draft.model_validate({'visits': [
        {'visit_id': f'stop-{index}', 'point': {
            'label': f'Public test {index}', 'latitude': 1.3, 'longitude': 103.8,
            'source': 'user_map', 'confirmed': True,
        }} for index in (1, 2)
    ]})
    segment = SemanticSegment(type='constraint', name='place_type', value='cafe',
                              scope='visit:2', hardness='hard', quote='第二站换成咖啡馆')
    parsed = Interpretation(acknowledgement='替换', actions=[LocationAction(
        op='replace_visit', target_id='stop-1', quote='第二站换成咖啡馆',
        place=PlaceSpec(categories=['food'], keywords=['cafe']),
    )])

    with pytest.raises(SemanticConsistencyError):
        validate_segment_command_consistency([segment], parsed, draft=draft)


def test_constraint_quote_must_come_from_the_current_or_pending_message():
    body = ChatRequest(
        session_id='e' * 32, revision=0, message='保持原来的行程',
        draft=Draft(max_walk_minutes=10), consent=True,
    )

    with pytest.raises(ChatError) as error:
        prepare(body, None, client=ClearWalkClient(), pace=CallPacer(interval=0))

    assert error.value.code == 'UNSUPPORTED_EDIT_EVIDENCE'
    assert body.draft.max_walk_minutes == 10


class ConflictClient(FakeClient):
    def generate_json(self, **kwargs):
        return {'data': {
            'acknowledgement': '需要澄清',
            'commands': {'set_mode': [{'order': 1, 'quote': '必须步行', 'transport_mode': 'walk'}]},
            'questions': ['要全程步行还是全程驾车？'],
            'semantic_segments': [{
                'type': 'constraint', 'name': 'travel_mode', 'value': mode,
                'scope': 'global', 'hardness': 'hard', 'status': 'conflicting',
                'source':'user_explicit','effect':'set',
                'quote': quote,
            } for mode, quote in [('walk', '必须步行'), ('drive', '必须驾车')]],
        }, 'metadata': {'provider': 'test'}}


def test_conflicting_hard_requirements_cannot_be_adopted_even_with_a_question():
    body = ChatRequest(session_id='f' * 32, revision=0,
                       message='全程必须步行，同时全程必须驾车。', draft=Draft(), consent=True)
    record, response = prepare(body, None, client=ConflictClient(), pace=CallPacer(interval=0))

    assert response['preview']['can_apply'] is False
    assert response['preview']['ready_to_calculate'] is False
    with pytest.raises(ChatError) as error:
        assemble(record, {}, strict=True)
    assert error.value.code == 'SEMANTIC_CONFLICT'
    assert body.draft.max_walk_minutes is None


def test_provider_cannot_skip_the_semantic_envelope_and_still_change_the_draft():
    class MissingSegmentsClient(FakeClient):
        def generate_json(self, **kwargs):
            return {'data': {'acknowledgement': 'walking limit', 'commands': {},
                             'max_walk_minutes': 10}, 'metadata': {}}

    body = ChatRequest(session_id='1' * 32, revision=0, message='每段最多10分钟',
                       draft=Draft(), consent=True)
    with pytest.raises(ChatError) as error:
        prepare(body, None, client=MissingSegmentsClient(), pace=CallPacer(interval=0))

    assert error.value.code == 'MODEL_SCHEMA'
    assert body.draft.max_walk_minutes is None
