"""Exercise the real chat/retrieval/resolve chain with offline model responses."""
from copy import deepcopy
import re
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.stage2c import api, engine
from app.stage2c.catalogue import LocalCatalogue
from app.stage2c.models import ChatRequest, Draft, Preferences


def place(entity_id, title, subtype, longitude):
    return {
        'entity_id': entity_id, 'display_title': title,
        'category': 'nature', 'categories': ['nature'], 'address': '',
        'search_tags': [], 'search_text': title,
        'latitude': 1.30, 'longitude': longitude,
        'map_eligible': True, 'default_exploration_visible': True,
        'overture': {'category': subtype},
    }


def segment(**overrides):
    return {
        'type': 'constraint', 'name': 'place_type', 'value': 'park',
        'scope': 'visit:new', 'hardness': 'hard', 'status': 'understood',
        'source': 'user_explicit', 'quote': '加一个公园', 'effect': 'set',
        **overrides,
    }


def add_park():
    return {
        'acknowledgement': 'Please review the park candidates.',
        'semantic_segments': [segment()],
        'commands': {'add_visit': [{'order': 1, 'quote': '加一个公园',
                                  'place': {'categories': ['nature']}}]},
    }


class OfflineModel:
    settings = SimpleNamespace(model='offline-retrieval-regression')

    def __init__(self, interpretations):
        self.interpretations = list(interpretations)
        self.calls = []

    def generate_json(self, *, instruction, payload, schema):
        self.calls.append(deepcopy(payload))
        if 'slots' in payload:
            data = {'picks': [
                {'slot_id': slot['slot_id'],
                 'candidate_key': slot['candidates'][0]['candidate_key'],
                 'reason': 'Selected from the retrieved catalogue candidates.'}
                for slot in payload['slots']
            ]}
        else:
            data = self.interpretations.pop(0)
        return {'data': deepcopy(data), 'metadata': {'provider': 'offline_fake'}}


class NoExternalProvider:
    def search(self, *args, **kwargs):
        raise AssertionError('This regression must not call a geocoder')


@pytest.fixture
def chat_client(monkeypatch):
    catalogue = LocalCatalogue(rows=[
        place('park-a', 'Alpha Park', 'park', 103.850),
        place('park-b', 'Beta Park', 'park', 103.851),
        place('beach', 'Park Beach', 'beach', 103.852),
        place('garden', 'Park Garden', 'garden', 103.853),
    ], build_id='offline-catalogue')
    model = OfflineModel([])

    def prepare(body, geo, *, cancelled):
        return engine.prepare(body, NoExternalProvider(), client=model,
                              cat=catalogue, pace=engine.CallPacer(0), cancelled=cancelled)

    monkeypatch.setattr(api, 'prepare', prepare)
    monkeypatch.setattr(api, 'LocalCatalogue', lambda: catalogue)
    monkeypatch.setattr(api, 'STORE', engine.ProposalStore())
    monkeypatch.setattr(api, '_gate', api.SessionConcurrencyGate(capacity=1))
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield client, model


def request_body():
    origin = {'latitude': 1.30, 'longitude': 103.84, 'label': 'Synthetic origin',
              'source': 'user_map', 'confirmed': True}
    return ChatRequest(session_id='a' * 32, revision=1, message='加一个公园',
                       draft=Draft(origin=origin), preferences=Preferences(),
                       consent=True).model_dump(mode='json')


def propose_and_resolve(client, body):
    original = deepcopy(body)
    response = client.post('/api/stage2c/chat', json=body)
    assert response.status_code == 200, response.json()
    proposal = response.json()
    assert body == original
    assert proposal['preview']['can_apply'] is True
    choices = {slot['slot_id']: slot['selected_key'] for slot in proposal['slots']}
    resolved = client.post('/api/stage2c/resolve', json={
        'session_id': body['session_id'], 'revision': body['revision'],
        'ticket': proposal['ticket'], 'choices': choices,
    })
    assert resolved.status_code == 200, resolved.json()
    adopted = resolved.json()
    assert adopted['requires_client_apply'] is True
    assert adopted['generation_requests'] == 0
    assert body == original
    assert api.STORE.get(proposal['ticket'], body['session_id'], body['revision'])[
        'body'].draft.model_dump(mode='json') == body['draft']
    return proposal, adopted


def test_first_hard_place_type_retrieves_only_parks_and_resolves_without_mutating_draft(chat_client):
    client, model = chat_client
    model.interpretations.append(add_park())
    body = request_body()
    proposal, adopted = propose_and_resolve(client, body)
    options = proposal['slots'][0]['options']
    assert {option['point']['entity_id'] for option in options} == {'park-a', 'park-b'}
    assert all(option['place_types'] == ['park'] for option in options)
    assert body['draft']['visits'] == []
    assert len(adopted['draft']['visits']) == 1
    assert adopted['draft']['visits'][0]['point']['entity_id'] == 'park-a'
    requirement = adopted['preferences']['active_constraints'][0]
    assert requirement['scope'] == 'visit_id:' + adopted['draft']['visits'][0]['visit_id']
    assert {key: requirement[key] for key in ('name', 'value', 'hardness', 'source', 'status')} == {
        'name': 'place_type', 'value': 'park', 'hardness': 'hard',
        'source': 'user_explicit', 'status': 'understood',
    }
    assert len(model.calls) == 2  # INTERPRET + SELECT; resolve adds no model call.


def test_retrieval_after_adoption_keeps_existing_scoped_requirements(chat_client):
    client, model = chat_client
    model.interpretations.extend([add_park(), {
        'acknowledgement': '请确认替换候选。', 'semantic_segments': [],
        'commands': {'replace_visit': [{'order': 1, 'target_position': 1,
            'quote': '第一站换一个', 'place': {'categories': ['nature']}}]},
    }])
    first_body = request_body()
    _, first = propose_and_resolve(client, first_body)
    second_body = {**first_body, 'revision': 2, 'message': '第一站换一个',
                   'draft': first['draft'], 'preferences': first['preferences']}
    old_requirements = deepcopy(second_body['preferences']['active_constraints'])
    proposal, second = propose_and_resolve(client, second_body)
    assert [option['point']['entity_id'] for option in proposal['slots'][0]['options']] == ['park-b']
    assert second['draft']['visits'][0]['visit_id'] == first['draft']['visits'][0]['visit_id']
    assert second['draft']['visits'][0]['point']['entity_id'] == 'park-b'
    assert second['preferences']['active_constraints'] == old_requirements
    assert second_body['draft']['visits'][0]['point']['entity_id'] == 'park-a'
    assert len(model.calls) == 4


def test_adding_a_park_does_not_apply_an_existing_visits_type_to_the_new_slot(chat_client):
    client, model = chat_client
    model.interpretations.append(add_park())
    body = request_body()
    body['draft']['visits'] = [{'visit_id': 'existing-museum',
        'point': {**body['draft']['origin'], 'label': 'Existing museum', 'longitude': 103.83},
        'stay_minutes': 30, 'stay_profile': 'regular'}]
    old_requirement = {
        'kind': 'constraint', 'name': 'place_type', 'value': 'museum',
        'scope': 'visit_id:existing-museum', 'hardness': 'hard',
        'status': 'understood', 'source': 'inferred', 'evidence_source': None,
        'quote': '先参观博物馆', 'turn_index': 1,
    }
    body['preferences']['active_constraints'] = [deepcopy(old_requirement)]
    proposal, adopted = propose_and_resolve(client, body)
    assert {option['point']['entity_id'] for option in proposal['slots'][0]['options']} == {'park-a', 'park-b'}
    assert adopted['draft']['visits'][0] == body['draft']['visits'][0]
    requirements = adopted['preferences']['active_constraints']
    assert next(row for row in requirements if row['scope'] == 'visit_id:existing-museum') == old_requirement
    new_visit_id = adopted['draft']['visits'][1]['visit_id']
    assert next(row for row in requirements if row['scope'] == 'visit_id:' + new_visit_id)['value'] == 'park'
    selection_requirements = model.calls[1]['slots'][0]['applicable_preferences']['active_constraints']
    assert all(row['scope'] != 'visit_id:existing-museum' for row in selection_requirements)


def test_provider_cannot_inject_ledger_evidence_metadata(chat_client):
    client, model = chat_client
    output = add_park()
    output['semantic_segments'][0]['evidence_source'] = 'catalogue'
    model.interpretations.append(output)
    response = client.post('/api/stage2c/chat', json=request_body())
    assert response.status_code == 502
    assert response.json()['detail']['code'] == 'MODEL_SCHEMA'
    assert response.json()['detail']['old_itinerary_unchanged'] is True
    assert len(model.calls) == 1


def test_chinese_request_keeps_original_evidence_but_application_messages_are_english(chat_client):
    client, model = chat_client
    output = add_park()
    output['commands']['add_visit'][0]['place']['query'] = '公园'
    model.interpretations.append(output)
    proposal, adopted = propose_and_resolve(client, request_body())
    assert {item['point']['entity_id'] for item in proposal['slots'][0]['options']} == {'park-a', 'park-b'}
    assert adopted['preferences']['active_constraints'][0]['quote'] == '加一个公园'
    visible = [proposal['acknowledgement'], proposal['notice'], adopted['notice']]
    for state in (proposal['preview'], adopted):
        visible.extend(state['changes'])
        visible.extend(state['warnings'])
        visible.extend(item['message'] for item in state['requirement_outcomes'])
    for slot in proposal['slots']:
        visible.extend(slot['notices'])
        visible.extend(item['basis'] for item in slot['options'])
    assert visible and all(not re.search(r'[\u3400-\u9fff]', text) for text in visible)
