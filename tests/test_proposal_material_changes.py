"""No-op dialogue must not masquerade as an adopted replacement."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.stage2c.catalogue import LocalCatalogue
from app.stage2c.engine import CallPacer, ChatError, assemble, prepare
from app.stage2c.models import ChatRequest, Draft, Preferences


def point(label, lon=103.8):
    return dict(label=label, latitude=1.3, longitude=lon, source='user_map', confirmed=True)


def request(message='change another cafe', preferences=None):
    return ChatRequest(session_id='b'*32, revision=4, message=message, consent=True,
        draft=Draft(origin=point('Start'), visits=[
            {'visit_id':'park', 'point':point('Park',103.81), 'stay_minutes':30},
            {'visit_id':'cafe', 'point':point('Current cafe',103.82), 'stay_minutes':30}],
            time={'mode':'budget','budget_minutes':90}),
        preferences=preferences or Preferences())


def segment(kind='operation', name='replace_visit', value=None, **overrides):
    row=dict(type=kind, name=name, value=value, scope='visit:2', hardness='hard',
        status='understood', source='user_explicit', quote='change another cafe',
        effect='none')
    return {**row,**overrides}


def output(rows=(), commands=None, **extras):
    return dict(acknowledgement='The model says the cafe has been changed.',
        semantic_segments=list(rows), commands=commands or {}, **extras)


class OfflineModel:
    settings=SimpleNamespace(model='offline-material-proposal')
    def __init__(self, raw): self.raw=raw; self.calls=0
    def generate_json(self, **kwargs):
        self.calls+=1
        assert self.calls==1, 'Named fixture must not request model selection'
        return {'data':deepcopy(self.raw), 'metadata':{'provider':'offline'}}


class NoProvider:
    def search(self, *args, **kwargs): raise AssertionError('No external geocoder')


def prepared(raw, body=None):
    body=body or request()
    before=body.model_dump(mode='json')
    cat=LocalCatalogue(rows=[dict(entity_id='new-cafe',display_title='Different Cafe',
        category='food',categories=['food'],search_text='Different Cafe',search_tags=[],
        address='',latitude=1.31,longitude=103.83,map_eligible=True,
        default_exploration_visible=True)],build_id='offline-material-proposal')
    record, proposal=prepare(body,NoProvider(),client=OfflineModel(raw),cat=cat,pace=CallPacer(0))
    assert body.model_dump(mode='json')==before
    return record,proposal,cat


def test_captured_clarification_shape_has_no_material_changes_or_save_claim():
    # Actual captured model response used this no-effect cafe row and question.
    # Its fixture coordinates are deliberately not needed for this state test.
    raw=output([segment('constraint','place_type','cafe',quote='cafe')],
        questions=['What alternative cafe would you like to replace stop 2 with?'])
    preferences=Preferences(active_constraints=[dict(name='budget_minutes',value=90,
        scope='global',hardness='hard',status='satisfied',source='user_explicit',
        evidence_source='route',quote='90 minutes',turn_index=1)])
    body=request(preferences=preferences)
    record,proposal,cat=prepared(raw,body)
    assert proposal['slots']==[]
    assert proposal['preview']['has_changes'] is False
    assert proposal['preview']['semantic_changes']=={'itinerary':False,'requirements':False}
    assert proposal['preview']['can_apply'] is False
    assert 'no_changes' in proposal['preview']['adoption_blockers']
    assert 'What alternative cafe' in proposal['acknowledgement']
    assert 'ready for review' not in proposal['acknowledgement']
    assert 'unchanged' in proposal['acknowledgement']
    with pytest.raises(ChatError) as error: assemble(record,{},strict=True,cat=cat)
    assert error.value.code=='NO_CHANGES'
    assert body.preferences.active_constraints[0].status=='satisfied'


def test_understood_replace_operation_requires_an_executable_matching_target():
    with pytest.raises(ChatError) as error: prepared(output([segment()]))
    assert error.value.code=='SEMANTIC_COMMAND_MISMATCH'
    # A command for a different existing visit cannot satisfy the declaration.
    wrong={'replace_visit':[dict(order=1,quote='change another cafe',target_position=1,
                                place={'query':'Different Cafe'})]}
    with pytest.raises(ChatError) as error: prepared(output([segment()],wrong))
    assert error.value.code=='SEMANTIC_COMMAND_MISMATCH'


@pytest.mark.parametrize('ack,expected',[
    ('Which cafe would you like to change, stop 2 or stop 3?','Which cafe would you like to change, stop 2 or stop 3?'),
    ('The cafe has been replaced. Which cafe do you mean?','Which cafe do you mean?'),
    ('The cafe has been replaced.','Tell me what you would like to change.'),
])
def test_ack_only_question_is_preserved_without_copying_a_success_claim(ack,expected):
    raw=output()
    raw['acknowledgement']=ack
    _,proposal,_=prepared(raw)
    assert expected in proposal['acknowledgement']
    assert 'has been replaced' not in proposal['acknowledgement']
    assert proposal['preview']['has_changes'] is False
    assert not proposal['preview']['can_apply']


def test_unresolved_replacement_is_kept_as_clarification_not_invented():
    record,proposal,cat=prepared(output([segment(status='unresolved')],
        questions=['Which stop should change?']))
    assert proposal['slots']==[] and not proposal['preview']['can_apply']
    assert 'requirement_mismatch' in proposal['preview']['adoption_blockers']
    assert proposal['questions']==['Which stop should change?']
    with pytest.raises(ChatError) as error: assemble(record,{},strict=True,cat=cat)
    assert error.value.code=='REQUIREMENT_NEEDS_CLARIFICATION'


def test_same_setting_action_and_repeated_preference_are_not_material_edits():
    body=request('Keep walking.',Preferences(preferred=['food']))
    raw=output(commands={'set_mode':[dict(order=1,quote='Keep walking.',transport_mode='walk')]},
               preferred_add=['food'])
    record,proposal,cat=prepared(raw,body)
    assert proposal['preview']['has_changes'] is False
    assert proposal['preview']['changes']==[]
    assert not proposal['preview']['can_apply']
    with pytest.raises(ChatError) as error: assemble(record,{},strict=True,cat=cat)
    assert error.value.code=='NO_CHANGES'


def test_preferences_only_update_remains_adoptable():
    record,proposal,cat=prepared(output(preferred_add=['food']),request('I prefer food.'))
    assert proposal['preview']['has_changes'] is True
    assert proposal['preview']['semantic_changes']=={'itinerary':False,'requirements':True}
    result=assemble(record,{},strict=True,cat=cat)
    assert result['can_apply'] and result['preferences']['preferred']==['food']


def test_settings_only_update_remains_adoptable():
    body=request('Use a 120-minute budget.')
    raw=output(commands={'set_time':[dict(order=1,quote=body.message,budget_minutes=120)]})
    record,proposal,cat=prepared(raw,body)
    assert proposal['preview']['has_changes'] is True
    assert proposal['preview']['semantic_changes']['itinerary'] is True
    result=assemble(record,{},strict=True,cat=cat)
    assert result['draft']['time']['budget_minutes']==120


def replacement():
    return output([segment()],{'replace_visit':[dict(order=1,quote='change another cafe',
        target_position=2,place={'query':'Different Cafe'})]})


def test_unselected_replacement_can_become_a_material_edit_after_confirmation():
    record,proposal,cat=prepared(replacement())
    assert proposal['preview']['has_changes'] is False
    assert 'no_changes' not in proposal['preview']['adoption_blockers']
    slot=record['slots'][0]
    result=assemble(record,{slot['slot_id']:slot['options'][0]['candidate_key']},strict=True,cat=cat)
    assert result['has_changes'] and result['semantic_changes']['itinerary']
    assert result['draft']['visits'][0]==record['body'].draft.visits[0].model_dump(mode='json')
    visit=result['draft']['visits'][1]
    assert visit['point']['label']=='Different Cafe'
    assert visit['visit_id']=='cafe' and visit['stay_minutes']==30


def test_strict_resolution_rechecks_replacement_identity():
    record,_,cat=prepared(replacement())
    slot=record['slots'][0]
    # Defend the resolver even if a stored/legacy candidate escaped retrieval filtering.
    slot['options'][0]['point']=record['body'].draft.visits[1].point.model_dump(mode='json')
    with pytest.raises(ChatError) as error:
        assemble(record,{slot['slot_id']:slot['options'][0]['candidate_key']},strict=True,cat=cat)
    assert error.value.code=='SAME_REPLACEMENT_PLACE'
