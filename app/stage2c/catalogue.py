"""Nationwide read-only retrieval; shortlist caps are NOT geographic filters."""
from __future__ import annotations
import json, re, unicodedata
from app.stage1c import api as catalogue
from app.db import ro_connect
from app.stage2a.models import distance_m
from app.stage2a.transport import ProviderError
from .retrieval_rules import canonical_spec

TOPICS={'park':['park','公园','garden'],'nature':['park','garden','nature','自然','公园'],
 'food':['hawker','food','market','熟食','餐饮'], 'museum':['museum','博物馆'],
 'shopping':['shopping','mall','购物','商场'],'heritage':['historic','heritage','历史'],
 'architecture':['architecture','building','建筑'],'art':['museum','gallery','art','艺术'],
 'photography':['摄影','拍照']}
TOPIC_CATEGORIES={'park':['nature'],'nature':['nature'],'food':['food'],'heritage':['heritage','monument'],
 'architecture':['monument','heritage'],'museum':['tourism'],'art':['tourism'],'shopping':['tourism']}
def norm(s):
    return ' '.join(re.sub(r'[^\w\s]',' ',unicodedata.normalize('NFKC',str(s)).casefold()).split())
def text_has(text,term):
    term=norm(term)
    if not term:return False
    if re.fullmatch(r'[a-z0-9 ]+',term):return re.search(r'(?<!\w)'+re.escape(term)+r'(?!\w)',text) is not None
    return term in text

def blocked(entity,excluded):
    text=norm(' '.join([entity.get('display_title',''), ' '.join(entity.get('search_tags',[]))]))
    cats=entity.get('categories',[entity.get('category')])
    for topic in excluded:
        if topic in ('park','nature') and 'nature' in cats:return True
        if topic=='food' and 'food' in cats:return True
        if topic=='heritage' and any(x in cats for x in ('heritage','monument')):return True
        if any(text_has(text,x) for x in TOPICS.get(topic,[topic])):return True
    return False

class LocalCatalogue:
    def __init__(self,rows=None,build_id=None):
        if rows is None:
            path,report=catalogue.snapshot()
            self.build_id=report['build_id']
            with ro_connect(path) as db:
                rows=[json.loads(r[0]) for r in db.execute('SELECT data_json FROM entities ORDER BY title,entity_id')]
        else:self.build_id=build_id or 'test-only'
        self.rows=rows
        self.by_id={r['entity_id']:r for r in rows}

    def validate_point(self,point):
        if point is None or point.source!='catalogue_representative':return
        e=self.by_id.get(point.entity_id)
        if (point.catalogue_build_id!=self.build_id or e is None
            or e.get('latitude')!=point.latitude or e.get('longitude')!=point.longitude
            or not e.get('default_exploration_visible')):
            raise ValueError('Catalogue point was changed or held')

    def search(self,spec,prefs,anchor,existing,limit=8):
        # Backward-compatible interface for earlier callers and regression tests.
        effective,normalization=canonical_spec(spec)
        options,notices,held,_=self.search_detailed(effective,prefs,anchor,existing,limit,normalization)
        return options,notices,held

    def search_detailed(self,spec,prefs,anchor,existing,limit=8,normalization=None):
        query=norm(spec.query); tokens=query.split();explicit=bool(query)
        diagnostic={
            'version':'2C1-F3','catalogue_rows':len(self.rows),'eligible_rows':0,
            'after_exclusions':0,'category_matches':0,'name_matches':0,
            'keyword_matches':0,'returned_local_options':0,
            'explicit_query_present':explicit,'effective_categories':list(spec.categories),
            'generic_intent_terms_normalized':0,'specific_keyword_count':len(spec.keywords),
            'input_keyword_count':len(spec.keywords),'generic_query_normalized':False,
            'specific_requirements_relaxed':False,'held_name_match':False,
        }
        diagnostic.update(normalization or {})
        if explicit:
            held=[r for r in self.rows if not r.get('default_exploration_visible') and norm(r['display_title'])==query]
            if held:
                diagnostic['held_name_match']=True
                return [],['此名称匹配旧址或状态待核查记录。请确认当前场所，不按旧址自动规划。'],True,diagnostic
        rows=[]
        for e in self.rows:
            if not e.get('map_eligible',True) or not e.get('default_exploration_visible'):continue
            if not isinstance(e.get('latitude'),(int,float)) or not isinstance(e.get('longitude'),(int,float)):continue
            diagnostic['eligible_rows']+=1
            if blocked(e,prefs.excluded):continue
            if not explicit and e['entity_id'] in existing:continue
            diagnostic['after_exclusions']+=1
            cats=e.get('categories',[e.get('category')]); name=norm(e['display_title'])
            text=norm(' '.join([e.get('display_title',''),e.get('address') or '',e.get('search_text',''),
                               ' '.join(e.get('search_tags',[]))]))
            if spec.categories and not any(c in cats for c in spec.categories):continue
            diagnostic['category_matches']+=1
            if tokens and not all(text_has(text,t) for t in tokens):continue
            diagnostic['name_matches']+=1
            if spec.keywords and not any(text_has(text,w) for w in spec.keywords):continue
            diagnostic['keyword_matches']+=1
            exact=explicit and name==query
            score=1000 if exact else (100 if explicit and query in name else 0)
            for word in spec.keywords:
                if text_has(text,word):score+=10
            for topic in prefs.preferred:
                if any(c in cats for c in TOPIC_CATEGORIES.get(topic,[])):score+=3
            dist=distance_m(anchor.latitude,anchor.longitude,e['latitude'],e['longitude']) if anchor else None
            rows.append(((-score, dist if dist is not None else 0, name),e,dist,exact))
        rows.sort(key=lambda x:x[0]); result=[]
        for _,e,d,exact in rows[:limit]:
            result.append({'identity':'catalogue:'+e['entity_id'],'label':e['display_title'],
                'categories':e.get('categories',[e.get('category')]),'address':e.get('address') or '',
                'basis':'官方来源目录，坐标为来源代表点，入口和营业未核验',
                'distance_hint_m':round(d) if d is not None else None,'exact_name_match':exact,
                'point':{'latitude':e['latitude'],'longitude':e['longitude'],'label':e['display_title'][:200],
                    'source':'catalogue_representative','confirmed':True,'entity_id':e['entity_id'],
                    'catalogue_build_id':self.build_id}})
        diagnostic['returned_local_options']=len(result)
        notices=[]
        if diagnostic['generic_intent_terms_normalized']:
            notices.append('通用游玩或用餐表达按目录类别检索，不再要求地点名称含有“吃饭”等泛化词；具体关键词仍保留。')
        if not result and spec.keywords and diagnostic['name_matches']:
            notices.append('对应类别有目录记录，但本次具体关键词没有匹配。没有删除这些关键词来凑齐候选；可明确修改要求或手动选定地点。')
        if 'food' in spec.categories:
            notices.append('当前餐饮候选主要来自熟食中心目录，不覆盖所有餐厅、咖啡馆；营业、菜品及饮食限制仍未核验。')
        return result,notices,False,diagnostic

def retrieve_slot(index,action,cat,prefs,anchor,existing,geo):
    spec,normalization=canonical_spec(action.place,action.op)
    options,notices,held,diagnostic=cat.search_detailed(spec,prefs,anchor,existing,normalization=normalization)
    diagnostic['geocoder_attempted']=False
    # Generic slots never go to a geocoder, and never invent model-only destinations.
    if spec.query and not held:
        exact=any(o['exact_name_match'] for o in options)
        if action.op in ('set_origin','set_finish') or not exact:
            try:
                diagnostic['geocoder_attempted']=True
                result=geo.search(spec.query,1)
                for x in result['results'][:8]:
                    if blocked({'display_title':x['label'],'categories':[]},prefs.excluded):continue
                    o={'identity':'onemap:'+str(x['latitude'])+':'+str(x['longitude']), 'label':x['label'],
                       'categories':[],'address':x.get('address',''),'basis':'OneMap地名候选；不是核验入口或景点资格证明',
                       'distance_hint_m':round(distance_m(anchor.latitude,anchor.longitude,x['latitude'],x['longitude'])) if anchor else None,
                       'exact_name_match':False,
                       'point':{'latitude':x['latitude'],'longitude':x['longitude'],'label':x['label'][:200],
                                'source':'onemap_search','confirmed':True}}
                    if not any(y['identity']==o['identity'] for y in options):options.append(o)
                if result.get('next_page'):notices.append('OneMap仅取第1页候选；可在原搜索区继续查找，不代表全部匹配结果。')
            except ProviderError as e:
                notices.append('地名搜索未完成 ['+e.code+']；可在地图或原搜索区手动选点，不补造位置。')
    options=options[:12]
    diagnostic['returned_options']=len(options)
    for j,o in enumerate(options):o['candidate_key']=f's{index}c{j}'
    return {'slot_id':f's{index}','action_index':index,'operation':action.op,'query':spec.query,
            'categories':spec.categories,'options':options,'recommended_key':None,'selected_key':None,
            'reason':None,'notices':notices,'explicit_location_confirmation':bool(spec.query),
            'selection_basis':'not_selected','shortlist_limit':12,'whole_catalogue_scanned':len(cat.rows),'retrieval_diagnostic':diagnostic}
