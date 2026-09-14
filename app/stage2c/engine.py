"""Interpret -> retrieve -> propose -> explicit confirmation -> existing Stage2B calculator.
No model-generated coordinates, silently dropped constraints, online retries or fine-tuning.
"""
from __future__ import annotations
import copy, json, re, secrets, threading, time
from datetime import datetime
from pydantic import ValidationError
from app.config import ROOT
from app.llm_connect.client import GeminiREST
from app.llm_connect.config import load_settings
from app.llm_connect.errors import LLMConnectionError
from app.stage2b.models import TimeSettings, Visit, PlacePoint, SGT
from .time_updates import merge_time_updates, safe_time_diagnostic, TimeUpdateError
from .models import (ChatRequest,Draft,Preferences,Interpretation,LocationAction,TimeAction,Selection)
from .catalogue import LocalCatalogue, retrieve_slot, blocked
from .command_contract import pending_to_commands, CONTRACT_NAME
from .proposal_status import acknowledgement, slot_label
from .prompts import INTERPRET, SELECT, PROMPT_VERSION
from .wire_schema import interpretation_schema, selection_schema, schema_stats, WIRE_VERSION
from .operation_adapter import parse_interpretation, OperationFormatError, validation_from_exception, safe_validation

class ChatError(Exception):
    def __init__(self,code,message,status=422):self.code,self.message,self.status=code,message,status;super().__init__(message)
    def public(self):
        result={'code':self.code,'message':self.message,'old_itinerary_unchanged':True}
        if getattr(self,'request_diagnostic',None) is not None:
            result['request_diagnostic']=self.request_diagnostic
        return result

class CallPacer:
    """Conservative process-local spacing, not a claim about provider quota."""
    def __init__(self,interval=4.5):self.interval=interval;self.last=0.;self.lock=threading.Lock()
    def wait(self,cancelled):
        with self.lock:
            while (remaining:=self.interval-(time.monotonic()-self.last))>0:
                if cancelled():raise ChatError('CANCELLED','已取消本次等待；已发出的请求可能仍计入服务商用量。',409)
                time.sleep(min(.1,remaining))
            if cancelled():raise ChatError('CANCELLED','已取消本次请求。',409)
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
                self.values.pop(token,None);raise ChatError('PROPOSAL_EXPIRED','草案已过期或服务器已重启，请重新发送需求。',409)
            rec=item[1]
            if rec['body'].session_id!=session or rec['body'].revision!=revision:
                raise ChatError('PROPOSAL_STALE','行程或会话已改变；旧草案不能覆盖新操作，请重新发送。',409)
            return copy.deepcopy(rec)
    def clear(self,session):
        with self.lock:self.values={k:v for k,v in self.values.items() if v[1]['body'].session_id!=session}
STORE=ProposalStore()


def merge_preferences(old,parsed):
    d=old.model_dump()
    for field in ('preferred','excluded','notes'):
        remove=getattr(parsed,field+'_remove');add=getattr(parsed,field+'_add')
        d[field]=list(dict.fromkeys([x for x in d[field] if x not in remove]+add))
    if set(d['preferred'])&set(d['excluded']):raise ChatError('PREFERENCE_CONFLICT','同一偏好同时被要求和排除，请明确以哪一条为准。')
    if any(len(x)>240 for x in d['notes']):raise ChatError('NOTE_TOO_LONG','偏好说明过长，请拆分需求。')
    return Preferences.model_validate(d)

def model_context(body,cat=None):
    def point(p):
        if not p:return None
        # Raw user-coordinate labels have no reason to leave the local backend.
        return {'label':p.label if p.source in ('catalogue_representative','onemap_search') else '已在地图确认的位置',
                'source':p.source,'has_confirmed_coordinates':True,'entrance_verified':False}
    def categories(v):
        e=cat.by_id.get(v.point.entity_id) if cat is not None and v.point.source=='catalogue_representative' else None
        return [c for c in e.get('categories',[]) if c in ('nature','food','heritage','monument','tourism')] if e else []
    return {'origin':point(body.draft.origin),
            'visits':[{'visit_id':v.visit_id,'position':i+1,'place':point(v.point),
                       'categories':categories(v),'stay_minutes':v.stay_minutes,'stay_profile':v.stay_profile} for i,v in enumerate(body.draft.visits)],
            'finish_policy':body.draft.finish_policy,'finish':point(body.draft.finish),
            'mode':body.draft.mode,'time':body.draft.time.model_dump(mode='json')}

def validate_actions(body,parsed):
    quotes=[body.message]+([body.pending.user_message] if body.pending else [])
    ids={v.visit_id for v in body.draft.visits}; removed=set(); mutated=set()
    place_count=0
    for a in parsed.actions:
        if not any(a.quote in t for t in quotes):raise ChatError('UNSUPPORTED_EDIT_EVIDENCE','模型提出的修改没有对应原话，未应用。请用更明确的短句重试。')
        target=getattr(a,'target_id',None)
        if target and target not in ids:raise ChatError('UNKNOWN_VISIT','模型引用了不存在的停留点，未修改任何行程。')
        if target:
            if target in removed or (a.op=='remove_visit' and target in mutated):raise ChatError('CONFLICTING_EDITS','同一站点同时被删除和修改，请分两步表达。')
            mutated.add(target)
            if a.op=='remove_visit':removed.add(target)
        if isinstance(a,LocationAction):place_count+=1
        if a.op=='set_mode' and a.value=='public_transport':
            raise ChatError('MODE_NOT_CONNECTED','公交和地铁尚未接入。这次没有改成步行；请在需求中明确选步行、骑行或驾车。')
    if place_count>8:raise ChatError('TOO_MANY_LOOKUPS','单轮最多处理8个地点查询；可以分轮添加，不限制全岛范围。')
    if sum(a.op=='set_origin' for a in parsed.actions)>1:raise ChatError('AMBIGUOUS_ORIGIN','出现多个起点设置，请明确一个。')


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


def assemble(record,choices,*,strict=False):
    body,parsed=record['body'],record['parsed'];d=body.draft.model_copy(deep=True)
    time_update=resolved_time_update(body.draft.time,parsed.actions)
    d.time=time_update.settings
    time_changes_added=False
    slots={s['action_index']:s for s in record['slots']};prefs=record['preferences'];changes=[];missing=[]
    expected={s['slot_id'] for s in record['slots']}
    if not set(choices).issubset(expected):raise ChatError('UNKNOWN_CHOICE','选择项不属于这份草案。')
    def find(uid):
        found=next((v for v in d.visits if v.visit_id==uid),None)
        if found is None:raise ChatError('CONFLICTING_EDITS','修改步骤引用了已经删除的停留点。')
        return found
    for i,a in enumerate(parsed.actions):
        if isinstance(a,LocationAction):
            s=slots[i];key=choices.get(s['slot_id'],s.get('selected_key'))
            option=next((o for o in s['options'] if o['candidate_key']==key),None)
            if option is None:
                if key:raise ChatError('UNTRUSTED_PLACE','地点选择不在本次真实检索结果中。')
                missing.append(s['slot_id']);continue
            p=PlacePoint.model_validate(option['point'])
            if a.op=='set_origin':d.origin=p;changes.append('起点 → '+p.label)
            elif a.op=='set_finish':d.finish=p;d.finish_policy='custom';changes.append('结束位置 → '+p.label)
            elif a.op=='replace_visit':find(a.target_id).point=p;changes.append('替换停留地点 → '+p.label+'（原停留时间保留）')
            else:
                # Generic recommendations cannot silently duplicate a current visit.
                if not s['explicit_location_confirmation'] and any((v.point.entity_id and v.point.entity_id==p.entity_id) or
                    (v.point.latitude==p.latitude and v.point.longitude==p.longitude) for v in d.visits):
                    raise ChatError('DUPLICATE_SUGGESTION','候选与已有停留重复，请在草案中选择另一处。')
                d.visits.append(Visit(visit_id=record['new_ids'][i],point=p));changes.append('新增停留 → '+p.label+'（停留先用可改初值）')
        elif a.op=='remove_visit':d.visits=[v for v in d.visits if v.visit_id!=a.target_id];changes.append('删除指定停留点')
        elif a.op=='move_visit':
            v=find(a.target_id)
            if a.position>len(d.visits):raise ChatError('INVALID_POSITION','新顺序超出当前停留数量。')
            d.visits.remove(v);d.visits.insert(a.position-1,v);changes.append('调整指定地点到第'+str(a.position)+'站')
        elif a.op=='set_stay':find(a.target_id).stay_minutes=a.minutes;changes.append('调整指定站停留 → '+('类别初始估计' if a.minutes is None else str(a.minutes)+'分钟'))
        elif a.op=='set_finish_policy':d.finish_policy=a.value;d.finish=None;changes.append('结束方式 → '+('返回出发点' if a.value=='return_to_start' else '最后一站结束'))
        elif a.op=='set_mode':d.mode=a.value;changes.append('交通方式 → '+a.value)
        elif a.op=='set_time':
            if not time_changes_added:
                changes.extend(time_update.changes);time_changes_added=True
        elif a.op=='clear_visits':d.visits=[];changes.append('清空停留清单（起点、模式和时间要求不变）')
    if len(d.visits)>20:raise ChatError('TOO_MANY_VISITS','当前单条行程最多20个停留点，请减少或分段。')
    d=Draft.model_validate(d.model_dump())
    if strict and missing:raise ChatError('CONFIRM_PLACE','请先为每个有歧义或尚未确定的地点选择真实候选。')
    warnings=list(record.get('warnings',[]))
    if not d.origin:warnings.append('尚无已确认起点：可以先采用停留草案，然后在地图选起点，或继续说出起点名称。')
    if d.finish_policy=='custom' and not d.finish:warnings.append('还需要明确最后结束位置。')
    if prefs.notes:warnings.append('以下要求仅记录、尚无完整校验能力：'+'；'.join(prefs.notes))
    conflicts=[]
    for v in d.visits:
        if blocked({'display_title':v.point.label,'categories':[]},prefs.excluded):conflicts.append(v.point.label)
    if conflicts:warnings.append('清单存在可能违反排除要求的地点，请替换或修改偏好：'+'、'.join(conflicts))
    if strict and conflicts:raise ChatError('EXCLUDED_VISIT_REMAINS','清单中仍有被排除类别的可识别地点，请先修改草案或明确允许。')
    return {'draft':d.model_dump(mode='json'),'preferences':prefs.model_dump(),'changes':changes,
        'unresolved_slots':missing,'warnings':list(dict.fromkeys(warnings)),
        'can_apply':not missing and not conflicts,
        'ready_to_calculate':bool(d.origin and d.visits and (d.finish_policy!='custom' or d.finish)) and not missing and not conflicts,
        'time_update':time_update.diagnostic,
        'time_budget_is_user_constraint':d.time.mode!='estimate', 'opening_hours_checked':False,
        'entrances_verified':False,'order_optimisation':False,'route_calculated':False}


def _prepare(body,geo,*,client=None,cat=None,pace=None,cancelled=lambda:False,_trace):
    if not body.consent:raise ChatError('CONSENT_REQUIRED','先阅读并勾选发送说明。开发测试请只使用公开地点和虚构需求。',403)
    settings=None
    if client is None:settings=load_settings(ROOT);client=ChatGeminiREST(settings)
    source_text=body.message+(' '+body.pending.user_message if body.pending else '')
    secret=settings.key if settings else ''
    if (secret and secret in source_text) or re.search(r'(?:AIza[\w-]{20,}|sk-[\w-]{20,}|eyJ[A-Za-z0-9_-]{15,}\.)',source_text):
        raise ChatError('POSSIBLE_SECRET','输入疑似包含密钥，未发送。请移除敏感内容。')
    cat=cat or LocalCatalogue()
    for point in [body.draft.origin,body.draft.finish]+[v.point for v in body.draft.visits]:
        try:cat.validate_point(point)
        except ValueError:raise ChatError('CATALOGUE_CHANGED','当前行程引用的目录已改变或存在暂缓地点，请刷新并重新选点。',409) from None
    pace=pace or PACER;call_metadata=[];generation_attempts=0
    def generate(instruction,payload,schema):
        nonlocal generation_attempts
        _trace['phase']='interpretation_request' if instruction==INTERPRET else 'selection_request'
        _trace['schema']=schema_stats(schema)
        pace.wait(cancelled)
        if cancelled():raise ChatError('CANCELLED','已取消等待。',409)
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
        if cancelled():raise ChatError('CANCELLED','已取消等待；已发送的调用可能仍计入额度。',409)
        return result['data']
    payload={'user_message':body.message,'current_draft':model_context(body,cat=cat),
             'preferences':body.preferences.model_dump(),'pending_request':({'user_message':body.pending.user_message,
                 'interpretation':pending_to_commands(body.pending.interpretation,body.draft)} if body.pending else None),
             'singapore_now':datetime.now(SGT).replace(microsecond=0).isoformat()}
    raw=generate(INTERPRET,payload,interpretation_schema())
    _trace['phase']='interpretation_validation'
    try:parsed,adaptation=parse_interpretation(raw,body.draft)
    except OperationFormatError as error:
        _trace['validation']=error.validation
        _trace['normalization']=error.normalization
        raise ChatError('MODEL_SCHEMA','模型操作格式未通过本地检查；原行程保留。请导出字段诊断，无需为此简化正常需求。',502) from None
    _trace['normalization']=adaptation
    _trace['phase']='action_validation'
    validate_actions(body,parsed)
    _trace['phase']='time_merge_validation'
    # Reject incomplete/conflicting time changes before a second model call.
    time_update=resolved_time_update(body.draft.time,parsed.actions)
    _trace['time_update']=time_update.diagnostic
    _trace['phase']='preference_merge'
    prefs=merge_preferences(body.preferences,parsed)
    _trace['phase']='retrieval'
    anchor=None if any(a.op=='set_origin' for a in parsed.actions) else body.draft.origin
    existing={v.point.entity_id for v in body.draft.visits if v.point.entity_id}
    slots=[];warnings=[]
    if anchor is None:warnings.append('起点尚未确定：当前通用候选来自全岛发现，不是已确认的附近推荐。可以先确认起点，再让系统按该起点推荐。')
    for i,a in enumerate(parsed.actions):
        if not isinstance(a,LocationAction):continue
        if cancelled():raise ChatError('CANCELLED','已取消后续检索。',409)
        slot=retrieve_slot(i,a,cat,prefs,anchor,existing,geo);slots.append(slot);warnings.extend(slot['notices'])
        if not slot['options']:warnings.append('尚未补齐：'+slot_label(slot)+'候选。可能是具体条件无匹配或目录覆盖不足；不是已确认现实中没有。未补齐前不省略此项采用。')
    generic=[s for s in slots if not s['explicit_location_confirmation'] and s['options']]
    if generic:
        # No selected coordinates, raw source descriptions, or private route geometries leave the backend.
        short=[{'slot_id':s['slot_id'],'categories':s['categories'],
            'candidates':[{k:o[k] for k in ('candidate_key','identity','label','categories','distance_hint_m')} for o in s['options']]} for s in generic]
        raw_selection=generate(SELECT,{'slots':short,'preferences':prefs.model_dump()},selection_schema())
        _trace['phase']='selection_validation'
        try:selected=Selection.model_validate(raw_selection)
        except ValidationError as error:
            _trace['validation']=validation_from_exception(error)
            raise ChatError('SELECTION_SCHEMA','候选选择输出不符合格式，请保留字段诊断；本轮没有改变行程。',502) from None
        ids=[x.slot_id for x in selected.picks]
        if len(ids)!=len(set(ids)) or set(ids)!={s['slot_id'] for s in generic}:raise ChatError('INVALID_SELECTION','模型没有为每项真实候选给出唯一选择，未自动补选。',502)
        used=set()
        for pick in selected.picks:
            s=next(s for s in generic if s['slot_id']==pick.slot_id)
            o=next((o for o in s['options'] if o['candidate_key']==pick.candidate_key),None)
            if o is None or o['identity'] in used:raise ChatError('INVALID_SELECTION','模型选择了不存在或重复的候选，未编造替代地点。',502)
            used.add(o['identity']);s.update(recommended_key=pick.candidate_key,selected_key=pick.candidate_key,reason=pick.reason,selection_basis='llm_suggestion_from_retrieved_candidates')
    rec={'body':body,'parsed':parsed,'preferences':prefs,'slots':slots,'warnings':warnings,
         'new_ids':{i:secrets.token_hex(16) for i,a in enumerate(parsed.actions) if a.op=='add_visit'},
         'build_id':cat.build_id,'calls':call_metadata}
    _trace['phase']='proposal_assembly'
    preview=assemble(rec,{})
    return rec,{'acknowledgement':acknowledgement(parsed,slots,preview,body.message),'questions':parsed.questions,'slots':slots,
        'preview':preview,'interpretation':parsed.model_dump(mode='json'),
        'base_revision':body.revision,'catalogue_build_id':cat.build_id,
        'usage':{'generation_requests':len(call_metadata),'calls':call_metadata},
        'prompt_version':PROMPT_VERSION,'wire_version':WIRE_VERSION,'command_contract':CONTRACT_NAME,
        'operation_adaptation':adaptation,'time_update':time_update.diagnostic,'model':client.settings.model if hasattr(client,'settings') else 'OFFLINE_TEST_DOUBLE',
        'privacy':{'selected_coordinates_sent_to_llm':False,'raw_chat_text_sent_to_llm':True,
                   'named_search_queries_sent_to_onemap':True,'credentials_exposed':False},
        'notice':'草案未应用。建议候选不代表入口、营业或时间预算已经满足；采用后由现有程序计算。'}


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
        'normalization':{k:norm[k] for k in ('neutral_fields_omitted','target_positions_resolved','scoped_commands_compiled')
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
        wrapped=ChatError('MODEL_SCHEMA','待采用操作的结构与当前行程不一致，未调用后续计算；请导出字段诊断。',502)
        wrapped.request_diagnostic=_safe_trace(trace)
        raise wrapped from None
    except ValidationError as error:
        trace['validation']=validation_from_exception(error)
        wrapped=ChatError('INVALID_PROPOSAL','本地草案检查未通过，请导出字段诊断；原行程未更改。')
        wrapped.request_diagnostic=_safe_trace(trace)
        raise wrapped from None
    except Exception:
        wrapped=ChatError('LOCAL_CHAT_ERROR','本地对话模块未完成本轮请求，请导出诊断；原行程未修改。',500)
        wrapped.request_diagnostic=_safe_trace(trace)
        raise wrapped from None
