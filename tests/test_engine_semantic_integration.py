from types import SimpleNamespace
import pytest

from app.stage2c.engine import CallPacer, ChatError, prepare
from app.stage2c.models import ChatRequest, Draft, Preferences


class FakeClient:
    settings = SimpleNamespace(model='test-model')

    def generate_json(self, *, instruction, payload, schema):
        return {
            'data': {
                'acknowledgement': '记录每段步行限制',
                'semantic_segments': [{
                    'type': 'constraint', 'name': 'max_walk_minutes', 'value': '10',
                    'scope': 'global', 'hardness': 'hard', 'status': 'satisfied',
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
        result['data']['semantic_segments'][0]['name'] = 'must_be_open'
        result['data']['semantic_segments'][0]['value'] = '14:00'
        result['data']['semantic_segments'][0]['quote'] = '下午两点必须营业'
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
    assert response['preview']['preferences']['active_constraints'][0]['value'] == '10'


def test_prepare_rejects_a_hard_constraint_without_a_command_or_question():
    body = ChatRequest(
        session_id='c' * 32,
        revision=0,
        message='下午两点必须营业',
        draft=Draft(),
        preferences=Preferences(),
        consent=True,
    )

    with pytest.raises(ChatError) as error:
        prepare(body, None, client=MismatchClient(), pace=CallPacer(interval=0))

    assert error.value.code == 'SEMANTIC_COMMAND_MISMATCH'
