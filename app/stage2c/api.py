"""Only explicit local requests invoke the configured model. No automatic probing."""
from __future__ import annotations
import asyncio, os, threading, time
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from pydantic import ValidationError
from app.config import ROOT
from app.llm_connect.config import load_settings
from app.llm_connect.errors import LLMConnectionError
from app.stage2a import api as geo
from .models import ChatRequest, ResolveRequest, ForgetRequest
from .engine import prepare, assemble, STORE, ChatError, PACER
from .catalogue import LocalCatalogue
from .wire_schema import WIRE_VERSION

router=APIRouter(prefix='/api/stage2c',tags=['2C1 conversational proposals'])

# Compatibility sentinel used by existing diagnostics/tests. Shared deployments no
# longer serialize every user through this lock.
_busy=threading.Lock()

def _max_concurrency():
    try: value=int(os.environ.get('PLAYMAP_MAX_LLM_CONCURRENCY','2'))
    except ValueError: value=2
    return max(1,min(value,4))

class SessionConcurrencyGate:
    def __init__(self,capacity=None,ttl=1800,clock=time.monotonic):
        self.capacity=capacity or _max_concurrency();self.ttl=ttl;self.clock=clock
        self.global_slots=threading.BoundedSemaphore(self.capacity)
        self.guard=threading.Lock();self.sessions={}
    def acquire(self,session_id):
        now=self.clock()
        with self.guard:
            for key,(lock,last) in list(self.sessions.items()):
                if now-last>self.ttl and not lock.locked():self.sessions.pop(key,None)
            lock,last=self.sessions.get(session_id,(threading.Lock(),now))
            self.sessions[session_id]=(lock,now)
        if not lock.acquire(blocking=False):return None,'session'
        if not self.global_slots.acquire(blocking=False):
            lock.release();return None,'global'
        return lock,None
    def release(self,session_id,lock):
        try:self.global_slots.release()
        finally:
            lock.release()
            with self.guard:
                if session_id in self.sessions:self.sessions[session_id]=(lock,self.clock())

_gate=SessionConcurrencyGate()

@router.get('/status')
def status():
    try:
        settings=load_settings(ROOT)
        return {'configured':True,'provider':'gemini','model':settings.model,'live_call_verified':False,
            'map_chat_integrated':True,'wire_version':WIRE_VERSION,'field_diagnostics':True,'target_position_supported':True,'command_contract':'operation_scoped_commands_v1','time_contract':'partial_time_patch_v1','max_model_calls_per_turn':2,'max_concurrent_chat_sessions':_gate.capacity,'min_call_interval_s':PACER.interval,'training':False,
            'notes':'配置可读不证明额度或在线可用；仅点击发送才调用模型。'}
    except LLMConnectionError as e:return {'configured':False,'error':e.public(),'map_chat_integrated':True}

@router.post('/chat')
async def chat(body:ChatRequest,request:Request):
    session_lock,busy_scope=_gate.acquire(body.session_id)
    if session_lock is None:
        if busy_scope=='session':
            raise HTTPException(409,detail=ChatError('CHAT_BUSY','这个浏览器会话仍有模型请求在处理，请稍候；没有增加新调用。',409).public())
        raise HTTPException(429,detail=ChatError('CHAT_CAPACITY','共享测试服务正在处理其他请求，请稍候再发送；没有增加新调用。',429).public())
    cancel=threading.Event()
    task=None
    try:
        task=asyncio.create_task(run_in_threadpool(prepare,body,geo.client,cancelled=cancel.is_set))
        while not task.done():
            if await request.is_disconnected():cancel.set()
            await asyncio.sleep(.1)
        record,response=await task
        if cancel.is_set():raise ChatError('CANCELLED','请求已取消，未应用行程。',409)
        response['ticket']=STORE.put(record)
        return response
    except ChatError as e:raise HTTPException(e.status,detail=e.public()) from None
    except LLMConnectionError as e:
        raise HTTPException(429 if e.code=='RATE_LIMIT' else 502,detail={**e.public(),'old_itinerary_unchanged':True,
            'request_diagnostic':getattr(e,'chat_request_diagnostic',None)}) from None
    except HTTPException:raise
    except ValidationError:
        raise HTTPException(422,detail=ChatError('INVALID_PROPOSAL','输出或候选资料未通过本地检查，原行程未更改。').public()) from None
    except Exception:
        raise HTTPException(500,detail=ChatError('LOCAL_CHAT_ERROR','本地对话模块未完成本轮请求。请导出不含隐私的诊断；原行程未修改。',500).public()) from None
    finally:
        cancel.set()
        # A thread may be in an already sent HTTPS request. Never start a concurrent
        # replacement call just because the browser cancelled waiting.
        if task is not None and not task.done():
            try:await asyncio.shield(task)
            except BaseException:pass
        _gate.release(body.session_id,session_lock)

@router.post('/resolve')
def resolve(body:ResolveRequest):
    try:
        record=STORE.get(body.ticket,body.session_id,body.revision)
        cat=LocalCatalogue()
        if cat.build_id!=record['build_id']:raise ChatError('CATALOGUE_CHANGED','目录版本改变，请重新生成草案。',409)
        result=assemble(record,body.choices,strict=True,cat=cat)
        from .models import Draft
        d=Draft.model_validate(result['draft'])
        for pt in [d.origin,d.finish]+[v.point for v in d.visits]:cat.validate_point(pt)
        return {**result,'base_revision':body.revision,'generation_requests':0,'requires_client_apply':True,
                'notice':'已检查草案结构；路线将在本页采用后另行计算。'}
    except ChatError as e:raise HTTPException(e.status,detail=e.public()) from None
    except (ValidationError,ValueError):
        raise HTTPException(409,detail=ChatError('INVALID_RESOLUTION','选择或来源坐标已不一致，请刷新后重新选择。',409).public()) from None

@router.post('/forget')
def forget(body:ForgetRequest):
    STORE.clear(body.session_id)
    return {'cleared_local_pending_proposals':True,'provider_data_deletion_requested':False,
            'notice':'只清理本地内存草案；不是删除服务商已收到的内容。'}
