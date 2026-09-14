"""Read-only catalogue + explicit, non-persisted provider calls."""
from __future__ import annotations
import threading
from fastapi import APIRouter, HTTPException
from app.config import ROOT
from app.stage1c import api as catalogue
from app.stage2a.models import SearchRequest, RouteRequest
from app.stage2a.provider import OneMapClient
from app.stage2a.transport import ProviderError

router = APIRouter(prefix='/api/stage2a', tags=['Stage2A geocoding and point-to-point routing'])
client = OneMapClient(ROOT)
_lock = threading.Lock()
_counts = {'search_successes':0,'route_successes':0,'search_failures':0,'route_failures':0}

@router.get('/status')
def status():
    try:
        report = catalogue.snapshot()[1]
        cat = {'ready':True,'build_id':report['build_id'],'scope':report['scope'],
               'entity_count':report['entity_display_count'],
               'default_exploration_count':report['default_exploration_entity_count'],
               'verified_entrances':report['verified_entrance_count']}
    except HTTPException:
        cat = {'ready':False,'message':'未读取到Stage1C快照；可先检查旧目录，位置搜索与手选点不依赖目录。'}
    try: credentials = client.tokens.status()
    except (ProviderError, OSError):
        credentials = {'configured':False,'state':'config_unreadable','token_value_exposed':False}
    with _lock: counters = dict(_counts)
    return {'stage':'2A','scope':'Singapore nationwide','catalogue_geographic_filter':None,
            'catalogue':cat,'credentials':credentials,'implemented_modes':['walk','drive','cycle'],
            'public_transport_implemented':False,'live_llm':False,'itinerary_planning_implemented':False,
            'runtime_counters_since_server_start':counters,
            'live_routing_verified_for_this_process':counters['route_successes']>0,
            'private_queries_saved_to_disk':False,
            'notice':'代码就绪不等于服务已接通；全岛范围不等于每个点都存在可用路径。'}

def execute(kind, function):
    try:
        result = function()
    except ProviderError as exc:
        with _lock: _counts[kind+'_failures'] += 1
        raise HTTPException(exc.status, detail=exc.public()) from None
    with _lock: _counts[kind+'_successes'] += 1
    return result

@router.post('/search')
def search(request: SearchRequest):
    return execute('search',lambda:client.search(request.query,request.page))

@router.post('/route')
def route(request: RouteRequest):
    return execute('route',lambda:client.route(request))
