"""Interpret -> retrieve -> propose -> explicit confirmation -> existing Stage2B calculator.
No model-generated coordinates, silently dropped constraints, online retries or fine-tuning.
"""
from __future__ import annotations
import copy, json, os, re, secrets, threading, time
from datetime import datetime
from pydantic import ValidationError
from app.config import ROOT
from app.llm_connect.client import GeminiREST
from app.llm_connect.config import load_settings
from app.llm_connect.errors import LLMConnectionError
from app.stage2b.models import TimeSettings, Visit, PlacePoint, SGT
from .time_updates import merge_time_updates, safe_time_diagnostic, TimeUpdateError
from .models import (ChatRequest,Draft,Preferences,Interpretation,LocationAction,TimeAction,Selection,SemanticSegment)
from .catalogue import LocalCatalogue, retrieve_slot, blocked, global_exclusions_for_slot
from .command_contract import pending_to_commands, CONTRACT_NAME
from .proposal_status import acknowledgement, slot_label
from .prompts import INTERPRET, SELECT, PROMPT_VERSION
from .wire_schema import interpretation_schema, selection_schema, schema_stats, WIRE_VERSION
from .operation_adapter import parse_provider_interpretation, OperationFormatError, validation_from_exception, safe_validation
from .semantic import (bind_constraint_scopes, merge_active_constraints, synchronize_action_constraints,
    validate_segment_command_consistency, SemanticConsistencyError, requirements_for_action,
    requirement_outcomes, unverified_preferences, parameter_mismatches)
from .semantic import restore_verified_preferences
from app.verification import VERIFICATIONS
from .requirement_fields import capability_summary

# Words in the user's own request that mean "just buy something and go" (e.g. "grab a coffee to go").
TAKEAWAY_WORDS = re.compile(r'(\bto go\b|take ?-?away|\bgrab (a|an|some)\b|\bpick up (a|an|some)\b|\bbuy (a|an|some)\b|'
                            r'外带|外卖|打包|带走|买杯|买个|买瓶|买点|顺路买|顺便买)', re.I)

class ChatError(Exception):
    def __init__(self,code,message,status=422):self.code,self.message,self.status=code,message,status;super().__init__(message)
    def public(self):
        result={'code':self.code,'message':self.message,'old_itinerary_unchanged':True}
        if getattr(self,'request_diagnostic',None) is not None:
            result['request_diagnostic']=self.request_diagnostic
        return result

DEFAULT_CALL_INTERVAL=2.0

def call_interval():
    """Local spacing between provider calls, in seconds.

    A deliberately conservative project guard so a shared test instance cannot
    burst; it is NOT a measured provider quota and does not promise that a
    request will be accepted. Any unparsable, negative, non-finite or
    out-of-range value falls back to the documented default rather than
    silently becoming zero. A provider 429 is still surfaced, never retried.
    """
    try:value=float(os.environ.get('PLAYMAP_LLM_MIN_INTERVAL_S',DEFAULT_CALL_INTERVAL))
    except (TypeError,ValueError):return DEFAULT_CALL_INTERVAL
    return value if 0<=value<=60 else DEFAULT_CALL_INTERVAL

class CallPacer:
    """Conservative process-local spacing, not a claim about provider quota."""
    def __init__(self,interval=None):self.interval=call_interval() if interval is None else interval;self.last=0.;self.lock=threading.Lock()
    def wait(self,cancelled):
        with self.lock:
            while (remaining:=self.interval-(time.monotonic()-self.last))>0:
                if cancelled():raise ChatError('CANCELLED','Waiting was cancelled. Requests already sent may still count toward provider usage.',409)
                time.sleep(min(.1,remaining))
            if cancelled():raise ChatError('CANCELLED','This request was cancelled.',409)
            self.last=time.monotonic()
PACER=CallPacer()

class ChatGeminiREST(GeminiREST):
    # The larger command schema may require more output than the tiny 2C0 test.
    def _request(self,path,body=None):
        if body is not None:
            body=copy.deepcopy(body)
            body.setdefault('generationConfig',{})['maxOutputTokens']=4096
        return super()._request(path,body)


class ProposalStore:
    def __init__(self,ttl=600,capacity=128,clock=time.monotonic):
        self.ttl,self.capacity,self.clock=ttl,capacity,clock;self.values={};self.lock=threading.Lock()
    def put(self,record):
        with self.lock:
            now=self.clock();self.values={k:v for k,v in self.values.items() if v[0]+self.ttl>now}
            if len(self.values)>=self.capacity:del self.values[next(iter(self.values))]
            token=secrets.token_hex(16);self.values[token]=(now,copy.deepcopy(record));return token
    def get(self,token,session,revision):
        with self.lock:
            item=self.values.get(token)
            if item is None or item[0]+self.ttl<=self.clock():
                self.values.pop(token,None);raise ChatError('PROPOSAL_EXPIRED','The proposal has expired or the server has restarted. Please send your request again.',409)
            rec=item[1]
            if rec['body'].session_id!=session or rec['body'].revision!=revision:
                raise ChatError('PROPOSAL_STALE','The itinerary or session has changed. Please send a new request; this proposal cannot overwrite newer changes.',409)
            return copy.deepcopy(rec)
    def clear(self,session):
        with self.lock:self.values={k:v for k,v in self.values.items() if v[1]['body'].session_id!=session}
STORE=ProposalStore()


class MessageTurns:
    def __init__(self, capacity=128):
        self.capacity, self.values, self.lock = capacity, {}, threading.Lock()

    def next(self, body):
        floor = max([row.turn_index for row in body.preferences.active_constraints] +
            ([row.turn_index for row in body.pending.interpretation.semantic_segments] if body.pending else []) + [0])
        with self.lock:
            if body.session_id not in self.values and len(self.values) >= self.capacity:
                self.values.pop(next(iter(self.values)))
            index = max(self.values.get(body.session_id, 0), floor) + 1
            self.values[body.session_id] = index
            return index

    def clear(self, session):
        with self.lock:
            self.values.pop(session, None)


MESSAGE_TURNS = MessageTurns()


def merge_preferences(old, parsed, *, turn_index=0, draft=None, new_visit_ids=()):
    d=old.model_dump()
    for field in ('preferred','excluded','notes'):
        remove=getattr(parsed,field+'_remove');add=getattr(parsed,field+'_add')
        d[field]=list(dict.fromkeys([x for x in d[field] if x not in remove]+add))
    active = old.active_constraints
    segments = parsed.semantic_segments
    if draft is not None:
        active = bind_constraint_scopes(active, draft)
        segments = bind_constraint_scopes(segments, draft, new_visit_ids=new_visit_ids)
        if any(row.type in {'constraint','preference'} and row.effect!='none' and row.scope.startswith('visit:') for row in segments):
            raise ChatError('AMBIGUOUS_REQUIREMENT_SCOPE','The requirement does not identify a unique valid stop. Specify each new stop separately. Your itinerary is unchanged.')
    merged = merge_active_constraints(active, segments, turn_index=turn_index)
    removed_scopes = {'visit_id:' + a.target_id for a in parsed.actions if a.op == 'remove_visit'}
    if draft is not None and any(a.op == 'clear_visits' for a in parsed.actions):
        removed_scopes.update('visit_id:' + visit.visit_id for visit in draft.visits)
    merged = [item for item in merged if item.scope not in removed_scopes]
    if draft is not None:
        merged = synchronize_action_constraints(merged, parsed, draft,
            new_visit_ids=new_visit_ids, turn_index=turn_index)
    d['active_constraints'] = [item.model_dump(mode='json') for item in merged]
    for field, name in (('excluded', 'excluded_topic'), ('preferred', 'theme')):
        managed = {row.value for row in old.active_constraints if row.name == name and isinstance(row.value, str)}
        current = [row['value'] for row in d['active_constraints'] if row['name'] == name and
                   isinstance(row['value'], str) and row['value'] in {'park','food','museum','shopping','heritage','architecture','art','nature','photography'} and
                   (field != 'excluded' or row['hardness'] == 'hard') and
                   (row['scope'] in {'global', 'future_visits'} if field == 'excluded'
                    else row['scope'] == 'global')]
        d[field] = list(dict.fromkeys([value for value in d[field] if value not in managed] + current))
    if any(row.name == 'takeaway' and row.hardness == 'hard' and
           (row.effect == 'clear' or row.value is False) for row in segments):
        d['notes'] = [note for note in d['notes'] if not TAKEAWAY_WORDS.search(note)]
    if set(d['preferred'])&set(d['excluded']):raise ChatError('PREFERENCE_CONFLICT','The same preference is both requested and excluded. Please choose which requirement to keep.')
    if any(len(x)>240 for x in d['notes']):raise ChatError('NOTE_TOO_LONG','The preference note is too long. Please split it into shorter requests.')
    return Preferences.model_validate(d)

def model_context(body,cat=None):
    def point(p):
        if not p:return None
        # Raw user-coordinate labels have no reason to leave the local backend.
        # A device origin is named as such so the model stops asking for a start
        # point, yet is never presented as a place the user identified.
        if p.source in ('catalogue_representative','onemap_search'):label=p.label
        elif p.source=='device_location':label='Current device location (unnamed)'
        else:label='Location confirmed on the map'
        return {'label':label,'source':p.source,'has_confirmed_coordinates':True,'entrance_verified':False}
    def categories(v):
        e=cat.by_id.get(v.point.entity_id) if cat is not None and v.point.source=='catalogue_representative' else None
        return [c for c in e.get('categories',[]) if c in ('nature','food','heritage','monument','tourism')] if e else []
    return {'origin':point(body.draft.origin),
            'visits':[{'visit_id':v.visit_id,'position':i+1,'place':point(v.point),
                       'categories':categories(v),'stay_minutes':v.stay_minutes,'stay_profile':v.stay_profile} for i,v in enumerate(body.draft.visits)],
            'finish_policy':body.draft.finish_policy,'finish':point(body.draft.finish),
            'mode':body.draft.mode,'time':body.draft.time.model_dump(mode='json'),
            'max_walk_minutes':body.draft.max_walk_minutes}

DISTANCE_BANDS=((1000,'<1km'),(3000,'1-3km'),(10000,'3-10km'))


def selection_preferences(preferences, slot):
    target_scope = 'visit_id:' + str(slot.get('target_visit_id') or '')
    applies = []
    for row in preferences.active_constraints:
        if row.scope == 'global' or row.scope == target_scope:
            applies.append(row.model_dump(mode='json'))
        elif row.scope == 'future_visits' and slot.get('operation') == 'add_visit':
            applies.append(row.model_dump(mode='json'))
    return {'preferred': list(preferences.preferred),
            'excluded': global_exclusions_for_slot(preferences, slot),
            'active_constraints': applies}

def distance_band(metres):
    """Coarsen an anchor distance before it leaves the backend.

    Candidates are catalogue entries whose coordinates are public, so an exact
    geodesic distance from the anchor makes the anchor itself recoverable by
    trilateration - the start point would be exported even though no coordinate
    field is ever sent. Selection only needs relative proximity. Bands still
    constrain the anchor to a region: this lowers precision, it does not make
    the start point unknowable, and it is not a claim about road distance.
    """
    if isinstance(metres,bool) or not isinstance(metres,(int,float)):return None
    for limit,label in DISTANCE_BANDS:
        if metres<limit:return label
    return '>10km'


def validate_actions(body,parsed):
    quotes=[body.message]+([body.pending.user_message] if body.pending else [])
    if any(not any(segment.quote in text for text in quotes) for segment in parsed.semantic_segments):
        raise ChatError('UNSUPPORTED_EDIT_EVIDENCE','A requirement has no supporting text in your request. Nothing was applied. Please clarify it and try again.')
    ids={v.visit_id for v in body.draft.visits}; removed=set(); mutated=set()
    place_count=0
    for a in parsed.actions:
        if not any(a.quote in t for t in quotes):raise ChatError('UNSUPPORTED_EDIT_EVIDENCE','A proposed edit has no supporting text in your request. Nothing was applied. Please clarify it and try again.')
        target=getattr(a,'target_id',None)
        if target and target not in ids:raise ChatError('UNKNOWN_VISIT','The model referred to a stop that does not exist. Your itinerary is unchanged.')
        if target:
            if target in removed or (a.op=='remove_visit' and target in mutated):raise ChatError('CONFLICTING_EDITS','The same stop is both removed and edited. Please make these changes in separate steps.')
            mutated.add(target)
            if a.op=='remove_visit':removed.add(target)
        if isinstance(a,LocationAction):place_count+=1
        if a.op=='set_mode' and a.value=='public_transport':
            raise ChatError('MODE_NOT_CONNECTED','Bus and MRT routing are not available. Choose walking, cycling or driving; your transport mode has not been changed.')
    if place_count>8:raise ChatError('TOO_MANY_LOOKUPS','A message can include up to eight place searches. Add more in another message; searches cover all of Singapore.')
    if sum(a.op=='set_origin' for a in parsed.actions)>1:raise ChatError('AMBIGUOUS_ORIGIN','More than one starting point was specified. Please choose one.')


def resolved_time_update(old, actions):
    try:
        return merge_time_updates(old, actions)
    except TimeUpdateError as error:
        wrapped = ChatError(error.code, error.message)
        wrapped.time_update_diagnostic = error.diagnostic
        wrapped.request_diagnostic = {
            'phase':'time_merge_validation', 'wire_version':WIRE_VERSION,
            'generation_attempts':0, 'time_update':error.diagnostic,
            'automatic_retry':False, 'local_validation_enabled':True,
        }
        raise wrapped from None


def merge_time(old, action):
    # Public legacy helper delegates to the same atomic state resolver.
    return resolved_time_update(old, [action]).settings


def excluded_visits(visits,excluded,cat):
    """Judge an adopted stop by the same rule retrieval used to filter candidates.

    A display label alone cannot prove a place is outside an excluded category,
    so a catalogue visit is checked against its reviewed source row. Map and
    geocoder points keep the name-only check: their category is genuinely
    unknown here and is never guessed.
    """
    conflicts=[]
    for v in visits:
        e=cat.by_id.get(v.point.entity_id) if cat is not None and v.point.source=='catalogue_representative' else None
        if blocked(e if e is not None else {'display_title':v.point.label,'categories':[]},excluded):conflicts.append(v.point.label)
    return conflicts


def assemble(record,choices,*,strict=False,cat=None):
    body,parsed=record['body'],record['parsed'];d=body.draft.model_copy(deep=True)
    if parsed.max_walk_minutes is not None:
        d.max_walk_minutes = parsed.max_walk_minutes
    elif 'max_walk_minutes' in parsed.model_fields_set and any(
        s.type == 'constraint' and s.name == 'max_walk_minutes' and
        s.scope == 'global' and s.effect == 'clear' for s in parsed.semantic_segments
    ):
        d.max_walk_minutes = None
    time_update=resolved_time_update(body.draft.time,parsed.actions)
    d.time=time_update.settings
    time_changes_added=False
    slots={s['action_index']:s for s in record['slots']};prefs=record['preferences'];changes=[];missing=[]
    semantic_conflict = any(c.hardness == 'hard' and c.status == 'conflicting' and
        c.evidence_source != 'route' and c.source != 'route' for c in prefs.active_constraints) or any(
        s.type=='conflict' for s in parsed.semantic_segments)
    if strict and semantic_conflict:
        raise ChatError('SEMANTIC_CONFLICT','Some requirements conflict. Resolve them before confirming the proposal. Your itinerary is unchanged.')
    expected={s['slot_id'] for s in record['slots']}
    if not set(choices).issubset(expected):raise ChatError('UNKNOWN_CHOICE','The selection does not belong to this proposal.')
    def find(uid):
        found=next((v for v in d.visits if v.visit_id==uid),None)
        if found is None:raise ChatError('CONFLICTING_EDITS','An edit refers to a stop that has already been removed.')
        return found
    for i,a in enumerate(parsed.actions):
        if isinstance(a,LocationAction):
            s=slots[i];key=choices.get(s['slot_id'],s.get('selected_key'))
            option=next((o for o in s['options'] if o['candidate_key']==key),None)
            if option is None:
                if key:raise ChatError('UNTRUSTED_PLACE','The selected place is not among the retrieved candidates for this proposal.')
                missing.append(s['slot_id']);continue
            p=PlacePoint.model_validate(option['point'])
            takeaway = a.stay_profile == 'takeaway'
            if a.op=='set_origin':d.origin=p;changes.append('Starting point → '+p.label)
            elif a.op=='set_finish':d.finish=p;d.finish_policy='custom';changes.append('End point → '+p.label)
            elif a.op=='replace_visit':
                visit=find(a.target_id)
                visit.point=p
                if takeaway:
                    visit.stay_profile='takeaway'
                    visit.stay_minutes=None
                    changes.append('Replace stop → '+p.label+' (takeaway or quick purchase; using a short stop estimate)')
                elif a.stay_profile == 'regular':
                    visit.stay_profile='regular'
                    visit.stay_minutes=None
                    changes.append('Replace stop → '+p.label+' (takeaway cancelled; using the regular stop estimate)')
                else:
                    changes.append('Replace stop → '+p.label+' (existing stay duration retained)')
            else:
                # Generic recommendations cannot silently duplicate a current visit.
                if not s['explicit_location_confirmation'] and any((v.point.entity_id and v.point.entity_id==p.entity_id) or
                    (v.point.latitude==p.latitude and v.point.longitude==p.longitude) for v in d.visits):
                    raise ChatError('DUPLICATE_SUGGESTION','This candidate duplicates an existing stop. Please select another place.')
                d.visits.append(Visit(visit_id=record['new_ids'][i],point=p,stay_minutes=a.stay_minutes,
                                      stay_profile='takeaway' if takeaway else 'regular'))
                stay_note = (' ('+str(a.stay_minutes)+' minutes, as requested)' if a.stay_minutes is not None else
                    ' (takeaway or quick purchase; using a short stop estimate)' if takeaway else
                    ' (using an editable initial stay estimate)')
                changes.append('Add stop → '+p.label+stay_note)
        elif a.op=='remove_visit':d.visits=[v for v in d.visits if v.visit_id!=a.target_id];changes.append('Remove the selected stop')
        elif a.op=='move_visit':
            v=find(a.target_id)
            if a.position>len(d.visits):raise ChatError('INVALID_POSITION','The new position is outside the current list of stops.')
            d.visits.remove(v);d.visits.insert(a.position-1,v);changes.append('Move the selected stop to position '+str(a.position))
        elif a.op=='set_stay':
            visit=find(a.target_id)
            if a.stay_profile is not None:
                visit.stay_profile=a.stay_profile
            visit.stay_minutes=None if a.stay_profile=='takeaway' else a.minutes
            changes.append('Stay duration → '+('initial takeaway estimate' if a.stay_profile=='takeaway' else
                'initial category estimate' if a.minutes is None else str(a.minutes)+' minutes'))
        elif a.op=='set_finish_policy':d.finish_policy=a.value;d.finish=None;changes.append('Finish policy → '+('return to the starting point' if a.value=='return_to_start' else 'finish at the last stop'))
        elif a.op=='set_mode':d.mode=a.value;changes.append('Transport mode → '+a.value)
        elif a.op=='set_time':
            if not time_changes_added:
                changes.extend(time_update.changes);time_changes_added=True
        elif a.op=='clear_visits':d.visits=[];changes.append('Clear all stops (starting point, transport mode and time requirements unchanged)')
    if len(d.visits)>20:raise ChatError('TOO_MANY_VISITS','An itinerary can contain up to 20 stops. Remove some stops or split the trip.')
    d=Draft.model_validate(d.model_dump())
    if d.model_dump()!=body.draft.model_dump():
        prefs=unverified_preferences(prefs)
    if d.model_dump()!=body.draft.model_dump() and not changes:
        changes.append('Update itinerary settings (pending confirmation)')
    if prefs.model_dump()!=body.preferences.model_dump():
        changes.append('Update recorded requirements and preferences (pending confirmation)')
    if strict and missing:raise ChatError('CONFIRM_PLACE','Choose a retrieved candidate for every unresolved or ambiguous place first.')
    warnings=list(record.get('warnings',[]))
    if semantic_conflict:warnings.append('Some requirements conflict. Resolve them before confirming or calculating this proposal.')
    if not d.origin:warnings.append('No starting point is confirmed. You can confirm the stops first, then choose a starting point on the map or name it in chat.')
    if d.finish_policy=='custom' and not d.finish:warnings.append('Please specify the final destination.')
    if prefs.notes:warnings.append('These requirements are recorded but cannot yet be fully verified: '+'; '.join(prefs.notes))
    future_only = {row.value for row in prefs.active_constraints if row.name == 'excluded_topic' and row.scope == 'future_visits'}
    global_excluded = [topic for topic in prefs.excluded if topic not in future_only or any(
        row.name == 'excluded_topic' and row.value == topic and row.scope == 'global' for row in prefs.active_constraints)]
    conflicts=excluded_visits(d.visits,global_excluded,cat)
    if conflicts:warnings.append('Some stops may conflict with your exclusions. Replace them or update your preferences: '+', '.join(conflicts))
    if strict and conflicts:raise ChatError('EXCLUDED_VISIT_REMAINS','The itinerary still contains identifiable places from an excluded category. Edit the proposal or explicitly allow them.')
    outcomes = requirement_outcomes(prefs)
    warnings.extend(row['message'] for row in outcomes if row['hardness'] == 'hard' and row['status'] in {'unresolved', 'unsupported'})
    missing_parameters = [row for row in outcomes if row['hardness']=='hard' and row['status']=='unresolved' and row['capability']=='parameter']
    unresolved_targets = [row for row in parsed.semantic_segments if row.type=='operation' and row.status=='unresolved']
    blockers = (['semantic_conflict'] if semantic_conflict else []) + (['excluded_visit_conflict'] if conflicts else [])
    if missing_parameters or unresolved_targets:
        blockers.append('requirement_mismatch')
        warnings.append('Some edits still need a date, time or target stop. Provide these details before confirming.')
        if strict:raise ChatError('REQUIREMENT_NEEDS_CLARIFICATION','Provide the date, time or valid stop required for this edit. Your itinerary is unchanged.')
    mismatches=parameter_mismatches(prefs,d)
    if mismatches:
        blockers.append('requirement_mismatch')
        warnings.append('The proposed settings conflict with active requirements. Explicitly replace or withdraw these requirements first: '+', '.join(mismatches))
        if strict:raise ChatError('HARD_REQUIREMENT_CONFLICT','The proposed settings cannot override active requirements. Clarify or withdraw those requirements first.')
    return {'draft':d.model_dump(mode='json'),'preferences':prefs.model_dump(),'changes':changes,
        'requirement_outcomes':outcomes,'adoption_blockers':blockers,
        'unresolved_slots':missing,'warnings':list(dict.fromkeys(warnings)),
        'can_apply':not missing and not blockers,
        'ready_to_calculate':bool(d.origin and d.visits and (d.finish_policy!='custom' or d.finish)) and not missing and not blockers,
        'time_update':time_update.diagnostic,
        'time_budget_is_user_constraint':d.time.mode!='estimate', 'opening_hours_checked':False,
        'entrances_verified':False,'order_optimisation':False,'route_calculated':False}


def _prepare(body,geo,*,client=None,cat=None,pace=None,cancelled=lambda:False,_trace):
    if not body.consent:raise ChatError('CONSENT_REQUIRED','Read and accept the data-sharing notice before sending. For development tests, use public places and fictional requests.',403)
    cat=cat or LocalCatalogue()
    clean_preferences,_=restore_verified_preferences(VERIFICATIONS,body.verification_ref,
        body.session_id,body.revision,body.draft,body.preferences,
        catalogue_build_id=getattr(cat,'build_id',None))
    body=body.model_copy(update={'preferences':clean_preferences},deep=True)
    turn_index=MESSAGE_TURNS.next(body)
    settings=None
    if client is None:settings=load_settings(ROOT);client=ChatGeminiREST(settings)
    source_text=body.message+(' '+body.pending.user_message if body.pending else '')
    secret=settings.key if settings else ''
    if (secret and secret in source_text) or re.search(r'(?:AIza[\w-]{20,}|sk-[\w-]{20,}|eyJ[A-Za-z0-9_-]{15,}\.)',source_text):
        raise ChatError('POSSIBLE_SECRET','Your message appears to contain a secret and was not sent. Remove sensitive information first.')
    for point in [body.draft.origin,body.draft.finish]+[v.point for v in body.draft.visits]:
        try:cat.validate_point(point)
        except ValueError:raise ChatError('CATALOGUE_CHANGED','The itinerary refers to an updated catalogue or a place pending review. Refresh the page and select the places again.',409) from None
    pace=pace or PACER;call_metadata=[];generation_attempts=0
    def generate(instruction,payload,schema):
        nonlocal generation_attempts
        _trace['phase']='interpretation_request' if instruction==INTERPRET else 'selection_request'
        _trace['schema']=schema_stats(schema)
        pace.wait(cancelled)
        if cancelled():raise ChatError('CANCELLED','Waiting was cancelled.',409)
        generation_attempts+=1
        _trace['generation_attempts']=generation_attempts
        try:
            result=client.generate_json(instruction=instruction,payload=payload,schema=schema)
        except LLMConnectionError as error:
            # Only deterministic code metadata. No user/provider text is exported.
            error.chat_request_diagnostic={
                'phase':'interpretation' if instruction==INTERPRET else 'selection',
                'wire_version':WIRE_VERSION,'generation_attempts':generation_attempts,
                'schema':schema_stats(schema),'automatic_retry':False,
                'structured_output_enabled':True,'local_validation_enabled':True,
            }
            raise
        call_metadata.append(result['metadata'])
        _trace['completed_generation_responses']=len(call_metadata)
        if cancelled():raise ChatError('CANCELLED','Waiting was cancelled. Calls already sent may still count toward your quota.',409)
        return result['data']
    payload={'user_message':body.message,'current_draft':model_context(body,cat=cat),
             'preferences':body.preferences.model_dump(),'pending_request':({'user_message':body.pending.user_message,
                 'interpretation':pending_to_commands(body.pending.interpretation,body.draft)} if body.pending else None),
             'singapore_now':datetime.now(SGT).replace(microsecond=0).isoformat(),
             'capabilities':capability_summary()}
    raw=generate(INTERPRET,payload,interpretation_schema(has_existing_visits=bool(body.draft.visits)))
    _trace['phase']='interpretation_validation'
    if not isinstance(raw, dict) or not {'acknowledgement', 'semantic_segments', 'commands'} <= raw.keys():
        raise ChatError('MODEL_SCHEMA','The model response is missing required meaning or command fields. Your itinerary is unchanged.',502)
    try:parsed,adaptation=parse_provider_interpretation(raw,body.draft,turn_index=turn_index)
    except OperationFormatError as error:
        _trace['validation']=error.validation
        _trace['normalization']=error.normalization
        raise ChatError('MODEL_SCHEMA','The model response failed command validation. Your itinerary is unchanged. Export the field diagnostics for review.',502) from None
    _trace['normalization']=adaptation
    _trace['phase']='semantic_validation'
    try:
        validate_segment_command_consistency(parsed.semantic_segments, parsed, draft=body.draft)
    except SemanticConsistencyError:
        raise ChatError('SEMANTIC_COMMAND_MISMATCH','An explicit requirement has no matching supported action or clarification. Your itinerary is unchanged.',422) from None
    _trace['phase']='action_validation'
    validate_actions(body,parsed)
    _trace['phase']='time_merge_validation'
    # Reject incomplete/conflicting time changes before a second model call.
    time_update=resolved_time_update(body.draft.time,parsed.actions)
    _trace['time_update']=time_update.diagnostic
    _trace['phase']='preference_merge'
    new_ids={i:secrets.token_hex(16) for i,a in enumerate(parsed.actions) if a.op=='add_visit'}
    prefs=merge_preferences(body.preferences, parsed, turn_index=turn_index,
                            draft=body.draft, new_visit_ids=list(new_ids.values()))
    _trace['phase']='retrieval'
    anchor=None if any(a.op=='set_origin' for a in parsed.actions) else body.draft.origin
    existing={v.point.entity_id for v in body.draft.visits if v.point.entity_id}
    slots=[];warnings=[]
    if anchor is None:warnings.append('No starting point is confirmed. These candidates come from across Singapore. Confirm a starting point to get nearby suggestions.')
    for i,a in enumerate(parsed.actions):
        if not isinstance(a,LocationAction):continue
        if cancelled():raise ChatError('CANCELLED','Further searches were cancelled.',409)
        # Retrieval consumes intent fields; checker provenance remains on the ledger.
        ledger_rows=[SemanticSegment(type=row.kind,effect='set',
            **row.model_dump(exclude={'kind','evidence_source'})) for row in prefs.active_constraints]
        requirements=requirements_for_action(ledger_rows,a,body.draft,parsed)
        if a.op=='add_visit':
            requirements.extend(row for row in ledger_rows if row.scope=='visit_id:'+new_ids[i])
        slot=retrieve_slot(i,a,cat,prefs,anchor,existing,geo,requirements=requirements)
        slot['target_visit_id']=new_ids.get(i) if a.op=='add_visit' else a.target_id
        slots.append(slot);warnings.extend(slot['notices'])
        if not slot['options']:warnings.append('Still missing: '+slot_label(slot)+' candidates. Your conditions may have no catalogue match, or coverage may be incomplete. This does not prove that no such place exists. This item will not be omitted from the proposal.')
    generic=[s for s in slots if not s['explicit_location_confirmation'] and s['options']]
    if generic:
        # No selected coordinates, raw source descriptions, or private route geometries leave
        # the backend, and anchor distances are coarsened so the start point is not exported
        # as an exact distance set. Exact metres stay local for ranking and for this browser.
        short=[{'slot_id':s['slot_id'],'categories':s['categories'],
            'applicable_preferences': selection_preferences(prefs, s),
            'candidates':[{**{k:o[k] for k in ('candidate_key','identity','label','categories','address','basis',
                                                 'data_source','suggested_duration','opening_hours_display',
                                                 'opening_hours_source','entrance_count','place_types','place_type_basis')},
                'distance_band':distance_band(o['distance_hint_m'])} for o in s['options']]} for s in generic]
        raw_selection=generate(SELECT,{'slots':short,'preferences':{'preferred':prefs.preferred,
            'excluded':global_exclusions_for_slot(prefs, None)}},selection_schema())
        _trace['phase']='selection_validation'
        try:selected=Selection.model_validate(raw_selection)
        except ValidationError as error:
            _trace['validation']=validation_from_exception(error)
            raise ChatError('SELECTION_SCHEMA','The candidate selection response failed validation. Keep the field diagnostics for review. Your itinerary is unchanged.',502) from None
        ids=[x.slot_id for x in selected.picks]
        if len(ids)!=len(set(ids)) or set(ids)!={s['slot_id'] for s in generic}:raise ChatError('INVALID_SELECTION','The model did not select exactly one candidate for each slot. No selections were filled in automatically.',502)
        used=set()
        for pick in selected.picks:
            s=next(s for s in generic if s['slot_id']==pick.slot_id)
            o=next((o for o in s['options'] if o['candidate_key']==pick.candidate_key),None)
            if o is None or o['identity'] in used:raise ChatError('INVALID_SELECTION','The model selected an unknown or duplicate candidate. No replacement place was invented.',502)
            used.add(o['identity']);s.update(recommended_key=pick.candidate_key,selected_key=pick.candidate_key,reason=pick.reason,selection_basis='llm_suggestion_from_retrieved_candidates')
    rec={'body':body,'parsed':parsed,'preferences':prefs,'slots':slots,'warnings':warnings,
         'new_ids':new_ids,
         'build_id':cat.build_id,'calls':call_metadata}
    _trace['phase']='proposal_assembly'
    preview=assemble(rec,{},cat=cat)
    return rec,{'acknowledgement':acknowledgement(parsed,slots,preview,body.message),'questions':parsed.questions,'slots':slots,
        'preview':preview,'interpretation':parsed.model_dump(mode='json'),
        'base_revision':body.revision,'catalogue_build_id':cat.build_id,
        'usage':{'generation_requests':len(call_metadata),'calls':call_metadata},
        'prompt_version':PROMPT_VERSION,'wire_version':WIRE_VERSION,'command_contract':CONTRACT_NAME,
        'operation_adaptation':adaptation,'time_update':time_update.diagnostic,'model':client.settings.model if hasattr(client,'settings') else 'OFFLINE_TEST_DOUBLE',
        'privacy':{'selected_coordinates_sent_to_llm':False,'raw_chat_text_sent_to_llm':True,
                   'named_search_queries_sent_to_onemap':True,'credentials_exposed':False,
                   'exact_candidate_distances_sent_to_llm':False,
                   'candidate_distance_bands_sent_to_llm':True},
        'notice':'The proposal has not been applied. Candidates do not confirm entrances, opening hours or time feasibility. The itinerary is calculated after confirmation.'}


def _safe_trace(trace):
    # trace is authored locally; never copy raw responses, exceptions or request text.
    norm=trace.get('normalization') or {}
    return {
        'phase':trace.get('phase'), 'wire_version':WIRE_VERSION,
        'generation_attempts':trace.get('generation_attempts',0),
        'completed_generation_responses':trace.get('completed_generation_responses',0),
        'schema':trace.get('schema'),
        'validation':safe_validation(trace.get('validation')),
        'time_update':safe_time_diagnostic(trace.get('time_update')),
        'normalization':{k:norm[k] for k in ('neutral_fields_omitted','target_positions_resolved','scoped_commands_compiled','audit_effects_normalized','parameter_actions_compiled')
                         if type(norm.get(k)) is int and 0<=norm[k]<=1000},
        'automatic_retry':False,'structured_output_enabled':True,
        'local_validation_enabled':True,'raw_output_included':False,
    }


def prepare(body,geo,*,client=None,cat=None,pace=None,cancelled=lambda:False):
    """Keep safe failure detail for local validation as well as provider errors."""
    trace={'phase':'preflight','generation_attempts':0,'completed_generation_responses':0}
    try:
        return _prepare(body,geo,client=client,cat=cat,pace=pace,cancelled=cancelled,_trace=trace)
    except ChatError as error:
        if getattr(error,'time_update_diagnostic',None) is not None:
            trace['time_update']=error.time_update_diagnostic
        error.request_diagnostic=_safe_trace(trace)
        raise
    except LLMConnectionError as error:
        # Preserve the F1 request phase names for provider failures.
        t=dict(trace)
        t['phase']={'interpretation_request':'interpretation','selection_request':'selection'}.get(t['phase'],t['phase'])
        error.chat_request_diagnostic=_safe_trace(t)
        raise
    except OperationFormatError as error:
        trace['validation']=error.validation
        trace['normalization']=error.normalization
        wrapped=ChatError('MODEL_SCHEMA','The proposed commands do not match the current itinerary structure. No calculation was started. Export the field diagnostics for review.',502)
        wrapped.request_diagnostic=_safe_trace(trace)
        raise wrapped from None
    except ValidationError as error:
        trace['validation']=validation_from_exception(error)
        wrapped=ChatError('INVALID_PROPOSAL','The proposal failed validation. Export the field diagnostics for review. Your itinerary is unchanged.')
        wrapped.request_diagnostic=_safe_trace(trace)
        raise wrapped from None
    except Exception:
        wrapped=ChatError('LOCAL_CHAT_ERROR','The chat service could not complete this request. Export the diagnostics for review. Your itinerary is unchanged.',500)
        wrapped.request_diagnostic=_safe_trace(trace)
        raise wrapped from None
