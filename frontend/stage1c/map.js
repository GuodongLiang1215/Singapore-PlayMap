'use strict';
// All source text is rendered with textContent. No source HTML is executed.
const $ = id => document.getElementById(id);
const LABEL={tourism:'景点',nature:'公园 / 自然',heritage:'历史地点',monument:'古迹',food:'熟食中心'};
const COLORS={tourism:'#af783b',nature:'#31846b',heritage:'#6474a1',monument:'#9b7297',food:'#ce7764'};
const ACCESS_LABEL={name_supports_target:'名称支持目标 · 仍未核验',explicit_target_differs:'目标名称不同 · 不自动用作入口',broader_area_context:'只支持更大范围上下文',broad_target_label:'地名较宽 · 无法唯一确定',unspecified_or_unresolved:'名称未明确目标'};
let profile=null,all=[],matched=[],map=null,markers=null,tiles=null,originMarker=null,listLimit=100,detailRevision=0;
const messages={};
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function tell(key,text){messages[key]=text;$('progress').textContent=Object.values(messages).filter(Boolean).join(' · ');}
async function get(url){const r=await fetch(url);if(!r.ok){let m='HTTP '+r.status;try{m=(await r.json()).detail||m;}catch{}throw Error(m);}return r.json();}
function safeLink(parent,label,url){if(!url)return;try{const u=new URL(url);if(!['https:','http:'].includes(u.protocol))return;const a=node('a',label);a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';parent.append(a,node('br'));}catch{}}
function section(parent,title,text){const d=node('div',undefined,'detail-section');d.append(node('strong',title),node('div',text||'未知 / 未核验'));parent.append(d);}
function kv(dl,label,value){dl.append(node('dt',label),node('dd',value===null||value===undefined||value===''?'未知 / 未核验':String(value)));}
function clock(v){return String(Math.floor(v/60)).padStart(2,'0')+':'+String(v%60).padStart(2,'0');}
function script(src,integrity){return new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=src;if(integrity){s.integrity=integrity;s.crossOrigin='anonymous';}s.onload=resolve;s.onerror=()=>{s.remove();reject(Error('地图组件未能加载'));};document.head.append(s);});}
async function leaflet(){
 const c=node('link');c.rel='stylesheet';c.href='/stage1-static/vendor/leaflet.css';c.onerror=()=>{c.onerror=null;c.href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';c.integrity='sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=';c.crossOrigin='anonymous';};document.head.append(c);
 try{await script('/stage1-static/vendor/leaflet.js');}catch{await script('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js','sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=');}
 if(!window.L)throw Error('运行 scripts/prepare_stage1_map.py 后刷新；数据列表仍可使用。');
}
async function loadAll(){
 let items=[],offset=0,total=null;
 do{const d=await get('/api/stage1c/features?view=all&limit=1000&offset='+offset+'&build_id='+encodeURIComponent(profile.build_id));
  if(total===null)total=d.matched_total;if(total!==d.matched_total)throw Error('数据版本变化，请刷新。');
  items.push(...d.features);if(d.next_offset!==null&&d.next_offset<=offset)throw Error('分页未前进');offset=d.next_offset;
 }while(offset!==null);
 if(items.length!==total)throw Error('目录尚未完整读取');return items;
}
function render(){
 const terms=$('search').value.normalize('NFKC').toLowerCase().trim().split(/\s+/).filter(Boolean),cat=$('category').value,include=$('include-holds').checked;
 matched=all.filter(f=>{const p=f.properties;return(include||p.default_exploration_visible)&&(!cat||p.categories.includes(cat))&&terms.every(t=>(p.search_text||'').includes(t));});
 if(map){if(markers)map.removeLayer(markers);markers=L.geoJSON({type:'FeatureCollection',features:matched},{pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:5.5,weight:1,color:'#fff',fillColor:f.properties.default_exploration_visible?(COLORS[f.properties.category]||'#54796b'):'#8e8e8e',fillOpacity:.88}),onEachFeature:(f,l)=>{
  const p=f.properties,box=node('div');box.append(node('strong',p.display_title),node('p',p.visit_status==='legacy_record_hold'?'历史来源坐标，非新场馆位置':'来源展示坐标，非核验入口'));const b=node('button','查看来源与核查');b.onclick=()=>detail(p.entity_id);box.append(b);l.bindPopup(box);
  l.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);detail(p.entity_id);});
 }}).addTo(map);}
 renderList();
}
function focusEntity(id){const f=all.find(f=>f.id===id);if(f&&map)map.setView([f.properties.latitude,f.properties.longitude],16);detail(id);}
function renderList(){
 const list=$('list');list.replaceChildren();const shown=matched.slice(0,listLimit);
 $('list-count').textContent=`匹配 ${matched.length} 组；列表显示 ${shown.length} 组，地图显示全部匹配地点。`;
 if(!matched.length)list.append(node('p','当前条件没有匹配项。尝试不同名称/类别，或显示旧址归档与待复核记录。不是“现实中不存在”。','small'));
 for(const f of shown){const p=f.properties,b=node('button',p.display_title,'record-button');b.append(node('small',p.categories.map(c=>LABEL[c]||c).join(' / ')+` · ${p.member_count}份来源`+(p.default_exploration_visible?'':' · 归档/待复核')));b.onclick=()=>focusEntity(p.entity_id);list.append(b);}
 $('more').hidden=listLimit>=matched.length;
}
function renderSchedule(panel,h){
 const card=node('div',undefined,'review-card');card.append(node('strong','官网常规时段 · '+(h.freshness==='within_review_period'?'在复核周期内':'需要重新核查')));
 const identical=new Set(Object.values(h.weekly_minutes).map(v=>JSON.stringify(v))).size===1;
 if(identical)card.append(node('p','每周七天：'+h.weekly_minutes['0'].map(p=>clock(p[0])+'—'+clock(p[1])).join('、')));
 else for(let i=0;i<7;i++)card.append(node('p',['周一','周二','周三','周四','周五','周六','周日'][i]+'：'+h.weekly_minutes[String(i)].map(p=>clock(p[0])+'—'+clock(p[1])).join('、')));
 card.append(node('p',h.note_zh),node('p',`核查：${h.checked_on}；建议复核：${h.recheck_on}。不确认临时关闭、特殊日期或此刻营业。`,'small'));
 h.web_sources.forEach(u=>safeLink(card,'核查来源 ↗',u));panel.append(card);
}
async function detail(id){
 const revision=++detailRevision,panel=$('detail');panel.replaceChildren(node('p','读取中…'));
 try{const d=await get('/api/stage1c/entity?entity_id='+encodeURIComponent(id)+'&build_id='+encodeURIComponent(profile.build_id));if(revision!==detailRevision)return;
  const e=d.entity;panel.replaceChildren(node('h3',e.display_title));panel.append(node('span',`${e.member_count}份来源 · ${e.member_count>1?'按证据归组':'单来源记录'}`,'badge'));
  panel.append(node('p',e.visit_status==='legacy_record_hold'?'当前标记是历史坐标。下面的现址资料没有自动沿用该坐标。':'地点分组不代表已核验营业、可进入或适合当前行程。','stage-tip'));
  for(const x of e.legacy_reviews){const card=node('div',undefined,'review-card warning');card.append(node('strong','已核实的旧记录风险'),node('p',x.note_zh),node('p','后继 / 当前场馆：'+x.replacement.name),node('p','当前地址：'+(x.replacement.address||'未在本轮整理')),node('p',`核查日期：${x.checked_on}。新位置尚未定位，不在旧坐标上直接改名。`,'small'));x.web_sources.forEach(u=>safeLink(card,'运营方 / 官方证据 ↗',u));panel.append(card);}
  if(e.source_status_holds.length)section(panel,'源状态待复核','历史源数据标记为Under Construction。尚未确认当前状态，不能表述为“现在一定在施工”。');
  const dl=node('dl');kv(dl,'展示组ID',e.entity_id);kv(dl,'坐标来自',e.coordinate_source_uid);kv(dl,'坐标角色',e.coordinate_kind);kv(dl,'来源坐标',`${e.latitude}, ${e.longitude}`);kv(dl,'来源地址',e.address);panel.append(dl);
  section(panel,'源名称 / 别名',e.aliases.join('\n'));
  if(e.regular_hours_evidence.length)e.regular_hours_evidence.forEach(h=>renderSchedule(panel,h));else section(panel,'当前开放时间','本轮没有核验；旧资料中的时段只保留在来源详情，不能作为当前保证。');
  section(panel,'建议停留与入口','尚未建立游玩时长，未核验入口。官网常规时段不等于完成路线规划。');
  const sources=node('div',undefined,'detail-section');sources.append(node('strong','保留的来源详情'));
  for(const r of d.source_records){const card=node('details',undefined,'source-card');card.append(node('summary',r.source_key+' · '+r.record_uid));const dl=node('dl');kv(dl,'原始名称',r.name);kv(dl,'资料时期',r.provenance.source_period_label);kv(dl,'地址',r.address);kv(dl,'坐标',`${r.latitude}, ${r.longitude}`);card.append(dl);
   section(card,'原来源简介',r.stage1b?.display_description||r.description);section(card,'原来源时段（未经当前核验）',r.stage1b?.opening_hours_display||r.opening_hours_raw);safeLink(card,'官方数据目录 ↗',r.provenance.catalogue_url);safeLink(card,'原详情链接（可能过时）↗',r.external_url);safeLink(card,'数据许可 ↗',r.provenance.licence_url);sources.append(card);}
  panel.append(sources);
  if(e.related_records.length){const box=node('div',undefined,'detail-section');box.append(node('strong','保留分开的相关记录'));
   for(const rel of e.related_records){const f=all.find(x=>x.id===rel.entity_id);const b=node('button',f?.properties.display_title||rel.entity_id,'related-button');b.onclick=()=>focusEntity(rel.entity_id);box.append(b,node('p',rel.reason_zh,'small'));}panel.append(box);}
  if(e.access_point_candidates.length){const box=node('details',undefined,'detail-section');box.append(node('summary',`接入点空间线索：${e.access_point_candidates.length}条关联（非唯一入口数）`));
   for(const a of e.access_point_candidates){const row=node('div',undefined,'access-row'),s=a.semantic_review;row.append(node('strong',a.access_name||'未命名接入点'),node('small','关联边界：'+a.boundary_name),node('small',ACCESS_LABEL[s.status]||s.status),node('small',s.explanation_zh));safeLink(row,'查看Stage1B原接入点记录 ↗',location.origin+'/api/stage1b/record?uid='+encodeURIComponent(a.access_uid)+'&build_id='+encodeURIComponent(profile.source_stage1b_build_id));box.append(row);}panel.append(box);
  }else section(panel,'接入点线索','当前候选关联没有可列出的接入点；不代表该地点没有入口。');
 }catch(err){if(revision===detailRevision)panel.replaceChildren(node('p',err.message,'error'));}
}
function fit(){if(map&&all.length)map.fitBounds(all.map(f=>[f.properties.latitude,f.properties.longitude]),{padding:[24,24]});}
async function start(){
 $('search').oninput=$('category').onchange=$('include-holds').onchange=()=>{listLimit=100;render();};$('more').onclick=()=>{listLimit+=100;renderList();};$('fit').onclick=fit;
 $('clear-origin').onclick=()=>{if(originMarker&&map)map.removeLayer(originMarker);originMarker=null;$('origin').textContent='未设置；本页暂存，不请求GPS，不计算路线。';};
 try{profile=await get('/api/stage1c/status');all=await loadAll();$('records').textContent=profile.input_candidate_source_records;$('entities').textContent=profile.entity_display_count;$('groups').textContent=profile.reviewed_group_count;$('holds').textContent=profile.legacy_hold_entities+profile.historical_source_status_hold_entities;
  $('hold-note').textContent=`默认浏览 ${profile.default_exploration_entity_count} 组，不等于已核验营业。其余原记录可以勾选显示。`;render();tell('data',`全岛 ${all.length} 组地点目录已读取`);
 }catch(e){tell('data',e.message);$('list-count').textContent='未读到完整目录；先运行 scripts/build_stage1c.py。';return;}
 try{await leaflet();$('map').replaceChildren();map=L.map('map',{preferCanvas:true}).setView([1.3521,103.8198],11);
  tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
  tiles.on('tileerror',()=>tell('tiles','背景地图未加载；本地目录仍可使用，可关闭在线背景。'));
  $('tiles').onchange=()=>{$('tiles').checked?tiles.addTo(map):map.removeLayer(tiles);};
  map.on('click',e=>{if(!$('pick').checked)return;if(originMarker)map.removeLayer(originMarker);originMarker=L.circleMarker(e.latlng,{radius:8,color:'#193c35',weight:3,fillColor:'#fff',fillOpacity:1}).addTo(map);$('origin').textContent=`临时起点：${e.latlng.lat.toFixed(6)}, ${e.latlng.lng.toFixed(6)}。未计算路径。`;$('pick').checked=false;});
  render();fit();tell('map','按展示组绘制；原始坐标与来源全部保留');
 }catch(e){const ph=$('map-placeholder');if(ph)ph.textContent='地图组件未加载；左侧真实目录仍可用。运行 scripts/prepare_stage1_map.py 后刷新。';tell('map',e.message);}
}
start();
