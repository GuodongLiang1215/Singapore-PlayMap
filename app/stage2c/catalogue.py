"""Nationwide read-only retrieval; shortlist caps are NOT geographic filters."""
from __future__ import annotations
import json, re, unicodedata
from math import isfinite
from types import SimpleNamespace
from app.stage1c import api as catalogue
from app.db import ro_connect
from app.stage2a.models import distance_m
from app.stage2a.transport import ProviderError
from .retrieval_rules import ALIAS_CATEGORY, canonical_place_type, canonical_spec

TOPICS={'park':['park','公园','garden'],'nature':['park','garden','nature','自然','公园'],
 'food':['hawker','food','market','熟食','餐饮'], 'museum':['museum','博物馆'],
 'shopping':['shopping','mall','购物','商场','百货'],'heritage':['historic','heritage','历史'],
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

def source_place_type(subtype):
    subtype=norm(subtype).replace(' ','_')
    if subtype in {'park','national_park','state_park','public_park','city_park','dog_park'}:return 'park'
    if subtype=='restaurant' or subtype.endswith('_restaurant'):return 'restaurant'
    if subtype=='museum' or subtype.endswith('_museum'):return 'museum'
    if subtype in {'cafe','coffee_shop','hong_kong_style_cafe'}:return 'cafe'
    return None

def entity_place_types(entity):
    subtype=(entity.get('overture') or {}).get('category')
    if subtype:
        place_type=source_place_type(subtype)
        return ([place_type] if place_type else []), 'overture.category'
    source=str(entity.get('primary_uid') or '').partition(':')[0]
    if source=='nparks_parks':return ['park'], 'source:nparks_parks'
    place_types=sorted({place_type for tag in entity.get('search_tags',[])
                        if (place_type:=canonical_place_type(tag))})
    return place_types, 'search_tags' if place_types else None

def compile_requirements(spec,requirements,query_type=None):
    legacy_types={place_type for keyword in spec.keywords if (place_type:=canonical_place_type(keyword))}
    if query_type:legacy_types.add(query_type)
    required_types=set()
    unknown_types=set(); preferred_types=set(); hard_keywords=set()
    for row in requirements:
        kind=getattr(row,'type',getattr(row,'kind','constraint'))
        if kind not in ('constraint','preference') or getattr(row,'effect','set')!='set':continue
        if row.name not in ('place_type','keyword'):continue
        values=row.value if isinstance(row.value,list) else [row.value]
        hard=kind=='constraint' and row.hardness=='hard'
        for value in values:
            if not isinstance(value,str) or not value.strip():continue
            place_type=canonical_place_type(value)
            if place_type:
                (required_types if hard else preferred_types).add(place_type)
            elif hard and row.name=='place_type':unknown_types.add(value)
            elif hard:hard_keywords.add(value)
    return required_types | (legacy_types-preferred_types),unknown_types,preferred_types,hard_keywords

def compile_category_requirements(requirements):
    required=set(); preferred=set(); unknown=set()
    categories={'nature','food','heritage','monument','tourism'}
    for row in requirements:
        kind=getattr(row,'type',getattr(row,'kind','constraint'))
        if row.name!='category' or kind not in {'constraint','preference'}:continue
        if getattr(row,'effect','set')!='set':continue
        hard=kind=='constraint' and row.hardness=='hard'
        values=row.value if isinstance(row.value,list) else [row.value]
        for value in values:
            category=norm(value) if norm(value) in categories else ALIAS_CATEGORY.get(norm(value))
            if category:(required if hard else preferred).add(category)
            elif hard:unknown.add(str(value))
    return required,preferred,unknown

def blocked(entity,excluded):
    text=norm(' '.join([entity.get('display_title',''), ' '.join(entity.get('search_tags',[]))]))
    cats=entity.get('categories',[entity.get('category')])
    for topic in excluded:
        place_type=canonical_place_type(topic)
        if place_type:
            place_types,type_basis=entity_place_types(entity)
            if place_type in place_types:return True
            if type_basis is not None:continue
            if any(text_has(text,term) for term in TOPICS.get(topic,[topic])):return True
            continue
        if topic=='nature' and 'nature' in cats:return True
        if topic=='food' and 'food' in cats:return True
        if topic=='heritage' and any(x in cats for x in ('heritage','monument')):return True
        if any(text_has(text,x) for x in TOPICS.get(topic,[topic])):return True
    return False

def catalogue_anchor_matches(name,cat):
    matches={}
    for entity in cat.rows:
        if not entity.get('map_eligible',True) or not entity.get('default_exploration_visible'):continue
        if not all(isinstance(entity.get(key),(int,float)) for key in ('latitude','longitude')):continue
        labels=[entity['display_title'],*entity.get('aliases',[])]
        if norm(name) not in {norm(label) for label in labels}:continue
        key=(entity['latitude'],entity['longitude'])
        matches[key]=SimpleNamespace(latitude=key[0],longitude=key[1],label=name)
    return matches

def geocoded_anchor_matches(name,geo):
    result=geo.search(name,1)
    matches={}
    for row in result['results']:
        if norm(row['label'])!=norm(name):continue
        key=(row['latitude'],row['longitude'])
        matches[key]=SimpleNamespace(latitude=key[0],longitude=key[1],label=name)
    return matches,bool(result.get('next_page'))

def resolve_spatial_requirement(requirements,anchor,cat,geo):
    rows=[row for row in requirements if getattr(row,'effect','set')=='set']
    named_rows=[row for row in rows if row.name=='spatial_anchor']
    radius_rows=[row for row in rows if row.name=='radius_m']
    if not named_rows and not radius_rows:return anchor,None,None
    names={norm(row.value):row.value.strip() for row in named_rows
           if isinstance(row.value,str) and row.value.strip()}
    valid_radii=[row.value for row in radius_rows if type(row.value) in (int,float)
                 and isfinite(row.value) and row.value>0]
    radii=set(valid_radii)
    info={'status':'unresolved','message':'空间参照尚未确定。','distance_kind':'straight_line',
          'radius_m':next(iter(radii)) if len(radii)==1 else None,'anchor_label':None,
          'anchor_source':'origin' if anchor else None}
    if len(valid_radii)!=len(radius_rows) or len(names)==0 and named_rows:
        info['message']='空间参照或半径值无效，请提供具体中心和正数米数。'
        return None,None,info
    if len(names)>1 or len(radii)>1:
        info.update(status='conflicting',message='本次空间要求含有不同中心或半径，请明确选择。')
        return None,None,info
    if names:
        name=next(iter(names.values())); info['anchor_label']=name
        matches=catalogue_anchor_matches(name,cat); paged=False
        info['anchor_source']='catalogue'
        if not matches:
            info['anchor_source']='onemap'
            try:
                matches,paged=geocoded_anchor_matches(name,geo)
            except ProviderError as error:
                info['message']='空间参照搜索未完成 ['+error.code+']，请确认具体中心地点。'
                return None,None,info
        if len(matches)!=1 or paged:
            info['message']='空间参照未找到唯一精确匹配，请明确具体地点。'
            return None,None,info
        anchor=next(iter(matches.values()))
    if anchor is None:
        info['message']='距离要求缺少中心地点，请先指定空间参照或出发点。'
        return None,None,info
    info.update(status='applied',anchor_label=getattr(anchor,'label','出发点'),
                message='按直线半径筛选候选；不代表步行距离。' if radii else '按该参照点距离排序，未设定半径。')
    return anchor,info['radius_m'],info

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

    def search(self,spec,prefs,anchor,existing,limit=8,requirements=()):
        # Backward-compatible interface for earlier callers and regression tests.
        effective,normalization=canonical_spec(spec)
        options,notices,held,_=self.search_detailed(effective,prefs,anchor,existing,limit,normalization,requirements)
        return options,notices,held

    def search_detailed(self,spec,prefs,anchor,existing,limit=8,normalization=None,requirements=(),
                        radius_m=None,distance_first=False):
        query=norm(spec.query); tokens=query.split();explicit=bool(query)
        required_types,unknown_types,preferred_types,hard_keywords=compile_requirements(
            spec,requirements,(normalization or {}).get('query_place_type'))
        required_categories,preferred_categories,unknown_categories=compile_category_requirements(requirements)
        keywords=[keyword for keyword in spec.keywords if not canonical_place_type(keyword)]
        diagnostic={
            'version':'2C1-F9.1','catalogue_rows':len(self.rows),'eligible_rows':0,
            'after_exclusions':0,'category_matches':0,'name_matches':0,
            'keyword_matches':0,'returned_local_options':0,
            'explicit_query_present':explicit,'effective_categories':list(spec.categories),
            'generic_intent_terms_normalized':0,'specific_keyword_count':len(spec.keywords),
            'input_keyword_count':len(spec.keywords),'generic_query_normalized':False,
            'specific_requirements_relaxed':False,'held_name_match':False,
            'required_place_types':sorted(required_types),'unsupported_place_types':sorted(unknown_types),
            'place_type_matches':0,'hard_keyword_count':len(hard_keywords),
            'radius_m':radius_m,'spatial_matches':0,
            'required_categories':sorted(required_categories),'unsupported_categories':sorted(unknown_categories),
        }
        diagnostic.update(normalization or {})
        if explicit:
            held=[r for r in self.rows if not r.get('default_exploration_visible') and norm(r['display_title'])==query]
            # A hidden duplicate or old record only blocks the search when no visible place has this name.
            live=any(r.get('default_exploration_visible') and query in {norm(x) for x in [r['display_title'],*r.get('aliases',[])]} for r in self.rows) if held else False
            if held and not live:
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
            if unknown_categories or not required_categories.issubset(cats):continue
            diagnostic['category_matches']+=1
            if tokens and not all(text_has(text,t) for t in tokens):continue
            diagnostic['name_matches']+=1
            place_types,_=entity_place_types(e)
            if unknown_types or not required_types.issubset(place_types):continue
            diagnostic['place_type_matches']+=1
            if not all(text_has(text,w) for w in hard_keywords):continue
            if keywords and not any(text_has(text,w) for w in keywords):continue
            diagnostic['keyword_matches']+=1
            exact=explicit and name==query
            score=1000 if exact else (100 if explicit and query in name else 0)
            for word in spec.keywords:
                if text_has(text,word):score+=10
            score+=10*len(preferred_types.intersection(place_types))
            score+=3*len(preferred_categories.intersection(cats))
            for topic in prefs.preferred:
                preferred_type=canonical_place_type(topic)
                if preferred_type:
                    if preferred_type in place_types:score+=3
                elif any(c in cats for c in TOPIC_CATEGORIES.get(topic,[])):score+=3
            dist=distance_m(anchor.latitude,anchor.longitude,e['latitude'],e['longitude']) if anchor else None
            if radius_m is not None and (dist is None or dist>radius_m):continue
            diagnostic['spatial_matches']+=1
            distance_rank=dist if dist is not None else 0
            rank=(distance_rank,-score,name) if distance_first else (-score,distance_rank,name)
            rows.append((rank,e,dist,exact))
        rows.sort(key=lambda x:x[0]); result=[]
        for _,e,d,exact in rows[:limit]:
            place_types,type_basis=entity_place_types(e)
            result.append({'identity':'catalogue:'+e['entity_id'],'label':e['display_title'],
                'categories':e.get('categories',[e.get('category')]),'address':e.get('address') or '',
                'place_types':place_types,'place_type_basis':type_basis,
                'basis':('Overture第三方合并数据（非官方），坐标为来源点，入口和营业未核验' if e.get('data_source')=='overture' else '官方来源目录，坐标为来源代表点，入口和营业未核验'),
                'data_source':e.get('data_source'),'suggested_duration':e.get('suggested_duration'),
                'opening_hours_display':e.get('opening_hours_display'),
                'opening_hours_source':e.get('opening_hours_source'),
                'entrance_count':len(e.get('entrance_points') or []),
                'distance_hint_m':round(d) if d is not None else None,'exact_name_match':exact,
                'point':{'latitude':e['latitude'],'longitude':e['longitude'],'label':e['display_title'][:200],
                    'source':'catalogue_representative','confirmed':True,'entity_id':e['entity_id'],
                    'catalogue_build_id':self.build_id}})
        diagnostic['returned_local_options']=len(result)
        notices=[]
        if unknown_types or unknown_categories:
            notices.append('当前目录无法验证所需地点细类，没有将未知类型当作匹配。请调整细类要求。')
        elif not result and (required_types or required_categories):
            notices.append('没有找到具有所需细类来源证据的地点；名称或地址含相关词不能证明地点类型。')
        if diagnostic['generic_intent_terms_normalized']:
            notices.append('通用游玩或用餐表达按目录类别检索，不再要求地点名称含有“吃饭”等泛化词；具体关键词仍保留。')
        if not result and spec.keywords and diagnostic['name_matches']:
            notices.append('对应类别有目录记录，但本次具体关键词没有匹配。没有删除这些关键词来凑齐候选；可明确修改要求或手动选定地点。')
        if 'food' in spec.categories:
            notices.append('餐饮候选来自熟食中心官方目录及Overture第三方数据（餐厅、咖啡馆、酒吧等），不保证覆盖全部；营业、菜品及饮食限制仍未核验。')
        return result,notices,False,diagnostic

def global_exclusions_for_slot(preferences, slot):
    rows_by_topic = {}
    for row in preferences.active_constraints:
        if row.name == 'excluded_topic' and isinstance(row.value, str):
            rows_by_topic.setdefault(row.value, []).append(row)
    result = []
    for topic in preferences.excluded:
        rows = rows_by_topic.get(topic, [])
        hard_rows = [row for row in rows if row.hardness == 'hard']
        if not rows:
            result.append(topic)
        elif any(row.scope == 'global' for row in hard_rows):
            result.append(topic)
        elif slot and slot.get('operation') == 'add_visit' and any(
                row.scope == 'future_visits' for row in hard_rows):
            result.append(topic)
    return result


def retrieve_slot(index,action,cat,prefs,anchor,existing,geo,requirements=()):
    prefs=prefs.model_copy(update={'excluded':global_exclusions_for_slot(prefs,{'operation':action.op})},deep=True)
    requirements=tuple(requirements)
    anchor,radius_m,spatial=resolve_spatial_requirement(requirements,anchor,cat,geo)
    spec,normalization=canonical_spec(action.place,action.op)
    options,notices,held,diagnostic=cat.search_detailed(spec,prefs,anchor,existing,normalization=normalization,
                                                     requirements=requirements,radius_m=radius_m,
                                                     distance_first=bool(spatial) and not spec.query)
    spatial_pending=spatial is not None and spatial['status']!='applied'
    if spatial_pending:options=[]
    if spatial:notices.append(spatial['message'])
    diagnostic['geocoder_attempted']=False
    type_filter=bool(diagnostic['required_place_types'] or diagnostic['unsupported_place_types'] or
                     diagnostic['required_categories'] or diagnostic['unsupported_categories'])
    # OneMap names and coordinates cannot establish a required catalogue place type.
    if spec.query and not held and not type_filter and not spatial_pending:
        exact=any(o['exact_name_match'] for o in options)
        if action.op in ('set_origin','set_finish') or not exact:
            try:
                diagnostic['geocoder_attempted']=True
                result=geo.search(spec.query,1)
                _,_,_,hard_keywords=compile_requirements(spec,requirements)
                keywords=[word for word in spec.keywords if not canonical_place_type(word)]
                for x in result['results']:
                    if blocked({'display_title':x['label'],'categories':[]},prefs.excluded):continue
                    text=norm(x['label']+' '+x.get('address',''))
                    if not all(text_has(text,word) for word in hard_keywords):continue
                    if keywords and not any(text_has(text,word) for word in keywords):continue
                    distance=distance_m(anchor.latitude,anchor.longitude,x['latitude'],x['longitude']) if anchor else None
                    if radius_m is not None and (distance is None or distance>radius_m):continue
                    o={'identity':'onemap:'+str(x['latitude'])+':'+str(x['longitude']), 'label':x['label'],
                       'categories':[],'address':x.get('address',''),'basis':'OneMap地名候选；不是核验入口或景点资格证明',
                       'place_types':[],'place_type_basis':None,
                       'data_source':'onemap','suggested_duration':None,
                       'opening_hours_display':None,'opening_hours_source':None,'entrance_count':0,
                       'distance_hint_m':round(distance) if distance is not None else None,
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
            'spatial_requirement':spatial,
            'selection_basis':'not_selected','shortlist_limit':12,'whole_catalogue_scanned':len(cat.rows),'retrieval_diagnostic':diagnostic}
