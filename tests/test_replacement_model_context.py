"""Give the model sourced stop types without treating broad food as a cafe."""
import json

from app.stage2c.catalogue import LocalCatalogue
from app.stage2c.engine import model_context
from app.stage2c.models import ChatRequest, Draft
from app.stage2c.operation_adapter import parse_provider_interpretation
from app.stage2c.prompts import INTERPRET


def test_context_distinguishes_cafe_from_restaurant_without_exporting_coordinates():
    cat = LocalCatalogue(rows=[
        dict(entity_id='c', display_title='Cafe', categories=['food'], overture={'category':'cafe'}),
        dict(entity_id='r', display_title='Restaurant', categories=['food'], overture={'category':'restaurant'}),
    ], build_id='test')
    visits = [dict(visit_id=uid, point=dict(label=title, latitude=1.312345,
        longitude=103.876543, source='catalogue_representative', entity_id=uid,
        catalogue_build_id='test', confirmed=True), stay_minutes=30)
        for uid,title in [('c','Cafe'),('r','Restaurant')]]
    visits.append(dict(visit_id='manual', point=dict(label='Private cafe-like map label',
        latitude=1.312345, longitude=103.876543, source='user_map', confirmed=True)))
    body = ChatRequest(session_id='a'*32, revision=1, message='change another cafe',
                       draft=Draft(visits=visits), consent=True)
    context = model_context(body,cat)
    cafe,restaurant,manual = context['visits']
    assert cafe['place_types']==['cafe'] and restaurant['place_types']==['restaurant']
    assert cafe['categories']==restaurant['categories']==['food']
    assert cafe['place_type_basis']=='overture.category'
    assert manual['place_types']==[] and manual['place_type_basis'] is None
    payload=json.dumps(context)
    assert '1.312345' not in payload and '103.876543' not in payload
    assert 'Private cafe-like map label' not in payload
    assert [v['position'] for v in context['visits']]==[1,2,3]


def test_generic_replacement_example_targets_the_existing_stop_and_requests_discovery():
    start=INTERPRET.index('{"acknowledgement"',INTERPRET.index('Generic replacement example:'))
    raw,_=json.JSONDecoder().raw_decode(INTERPRET[start:])
    point=dict(label='Public point', latitude=1.3, longitude=103.8, source='user_map', confirmed=True)
    draft=Draft(visits=[dict(visit_id='park',point=point,stay_minutes=20),
                        dict(visit_id='cafe',point=point,stay_minutes=45)])
    parsed,_=parse_provider_interpretation(raw,draft,turn_index=2)
    action,=parsed.actions
    assert action.op=='replace_visit' and action.target_id=='cafe'
    assert action.place.query=='' and action.place.keywords==['cafe']
    assert draft.visits[1].stay_minutes==45
    assert any(row.name=='place_type' and row.value=='cafe' and row.scope=='visit:2'
               for row in parsed.semantic_segments)
