from copy import deepcopy

from app.stage2c.models import ActiveConstraint, Draft, Preferences
from app.stage2b.models import PlanRequest
from app.verification import VerificationStore
from app.stage2c.semantic import restore_verified_preferences


def plan_request():
    point = {'label':'Synthetic start','latitude':1.3,'longitude':103.8,'source':'user_map','confirmed':True}
    return PlanRequest(session_id='a'*32, revision=4, origin=point, max_walk_minutes=10,
        visits=[{'visit_id':'coffee','point':{**point,'label':'Coffee','longitude':103.81}}])


def preferences():
    return Preferences(active_constraints=[ActiveConstraint(name='max_walk_minutes',value=10,
        hardness='hard',quote='每段最多10分钟',turn_index=1)])


def test_actual_checker_evidence_matches_only_its_current_adopted_snapshot():
    store = VerificationStore(clock=lambda:100)
    request = plan_request()
    result = {'complete_plan_created':True,'all_legs_accounted_for':True,
        'walking_constraint_check':{'status':'ok','violations':[]}}
    ref = store.record(request,result,catalogue_build_id='catalogue-1')
    draft = Draft.model_validate(request.model_dump(exclude={'session_id','revision','force_refresh'}))
    restored, diagnostic = restore_verified_preferences(store,ref,request.session_id,4,draft,preferences(),catalogue_build_id='catalogue-1')
    assert restored.active_constraints[0].status == 'satisfied'
    assert diagnostic['status'] == 'matched'
    changed = draft.model_copy(update={'max_walk_minutes':5},deep=True)
    for session, revision, snapshot in [(request.session_id,5,draft),('b'*32,4,draft),(request.session_id,4,changed)]:
        restored, diagnostic = restore_verified_preferences(store,ref,session,revision,snapshot,preferences(),catalogue_build_id='catalogue-1')
        assert restored.active_constraints[0].status != 'satisfied'
        assert diagnostic['status'] == 'unknown'


def test_catalogue_rebuild_invalidates_route_evidence():
    store = VerificationStore(clock=lambda:100)
    request = plan_request()
    ref = store.record(request,{'complete_plan_created':True,'all_legs_accounted_for':True,
        'walking_constraint_check':{'status':'ok','violations':[]}},catalogue_build_id='catalogue-1')
    draft = Draft.model_validate(request.model_dump(exclude={'session_id','revision','force_refresh'}))
    restored, diagnostic = restore_verified_preferences(store,ref,request.session_id,4,draft,preferences(),
        catalogue_build_id='catalogue-2')
    assert restored.active_constraints[0].status == 'understood'
    assert diagnostic == {'status':'unknown','reason':'catalogue_changed','checked_requirements':0}


def test_route_evidence_without_a_current_catalogue_version_stays_unknown():
    store = VerificationStore(clock=lambda:100)
    request = plan_request()
    ref = store.record(request,{'complete_plan_created':True,'all_legs_accounted_for':True,
        'walking_constraint_check':{'status':'ok','violations':[]}},catalogue_build_id='catalogue-1')
    draft = Draft.model_validate(request.model_dump(exclude={'session_id','revision','force_refresh'}))
    restored, diagnostic = restore_verified_preferences(store,ref,request.session_id,4,draft,preferences())
    assert restored.active_constraints[0].status == 'understood'
    assert diagnostic['reason'] == 'catalogue_version_unavailable'


def test_planner_evidence_uses_the_catalogue_version_referenced_by_its_points(monkeypatch):
    from fastapi import HTTPException
    from app.stage2b import api

    request = plan_request()
    data = request.model_dump(mode='json')
    data['visits'][0]['point'].update(source='catalogue_representative',entity_id='cafe-1',
        catalogue_build_id='catalogue-1')
    request = PlanRequest.model_validate(data)
    snapshots = []
    monkeypatch.setattr(api.catalogue,'snapshot',lambda build_id=None:
        (snapshots.append(build_id) or None,{'build_id':build_id or 'catalogue-current'}))
    assert api.plan_catalogue_build_id(request) == 'catalogue-1'
    assert snapshots == ['catalogue-1']

    def rebuilt(build_id=None):
        raise HTTPException(409,'Catalogue was rebuilt.')
    monkeypatch.setattr(api.catalogue,'snapshot',rebuilt)
    assert api.plan_catalogue_build_id(request) is None


def test_expiry_and_changed_condition_do_not_reuse_route_proof():
    now = [100]
    store = VerificationStore(ttl=10,clock=lambda:now[0])
    request = plan_request()
    ref = store.record(request,{'complete_plan_created':True,'all_legs_accounted_for':True,
        'walking_constraint_check':{'status':'violation','violations':[{'leg_id':'leg-1'}]}},catalogue_build_id='catalogue-1')
    draft = Draft.model_validate(request.model_dump(exclude={'session_id','revision','force_refresh'}))
    prefs, diagnostic = restore_verified_preferences(store,ref,request.session_id,4,draft,preferences(),catalogue_build_id='catalogue-1')
    assert prefs.active_constraints[0].status == 'conflicting'
    altered = preferences()
    altered.active_constraints[0].value = 5
    prefs, _ = restore_verified_preferences(store,ref,request.session_id,4,draft,altered,catalogue_build_id='catalogue-1')
    assert prefs.active_constraints[0].status == 'understood'
    now[0] = 111
    prefs, diagnostic = restore_verified_preferences(store,ref,request.session_id,4,draft,preferences(),catalogue_build_id='catalogue-1')
    assert prefs.active_constraints[0].status == 'understood'
    assert diagnostic['reason'] == 'missing_or_expired'


def test_unknown_opening_hours_never_become_satisfied_and_clear_removes_evidence():
    store = VerificationStore()
    request = plan_request()
    ref = store.record(request,{'complete_plan_created':True,'all_legs_accounted_for':True,
        'opening_hours_checked':False,'walking_constraint_check':{'status':'ok'}},catalogue_build_id='catalogue-1')
    draft = Draft.model_validate(request.model_dump(exclude={'session_id','revision','force_refresh'}))
    prefs = preferences()
    prefs.active_constraints.append(ActiveConstraint(name='opening_hours',value='14:00',
        hardness='hard',status='satisfied',source='route',quote='两点营业',turn_index=1))
    restored, _ = restore_verified_preferences(store,ref,request.session_id,4,draft,prefs,catalogue_build_id='catalogue-1')
    assert restored.active_constraints[1].status == 'unresolved'
    store.clear(request.session_id)
    _, diagnostic = restore_verified_preferences(store,ref,request.session_id,4,draft,prefs,catalogue_build_id='catalogue-1')
    assert diagnostic['status'] == 'unknown'
