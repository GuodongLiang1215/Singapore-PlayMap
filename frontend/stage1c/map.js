'use strict';
// All source text is rendered with textContent. No source HTML is executed.
const $ = id => document.getElementById(id);
const LABEL={tourism:'Attractions / shopping / entertainment',nature:'Parks / nature',heritage:'Heritage sites',monument:'Monuments',food:'Food'};
const COLORS={tourism:'#af783b',nature:'#31846b',heritage:'#6474a1',monument:'#9b7297',food:'#ce7764'};
const ACCESS_LABEL={name_supports_target:'Name supports target · still unverified',explicit_target_differs:'Different named target · not selected automatically',broader_area_context:'Broader-area context only',broad_target_label:'Broad place name · target is ambiguous',unspecified_or_unresolved:'Target not named'};
let profile=null,all=[],matched=[],map=null,markers=null,tiles=null,originMarker=null,listLimit=100,detailRevision=0;
const messages={};
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function tell(key,text){messages[key]=text;$('progress').textContent=Object.values(messages).filter(Boolean).join(' · ');}
async function get(url){const r=await fetch(url);if(!r.ok){let m='HTTP '+r.status;try{m=(await r.json()).detail||m;}catch{}throw Error(m);}return r.json();}
function safeLink(parent,label,url){if(!url)return;try{const u=new URL(url);if(!['https:','http:'].includes(u.protocol))return;const a=node('a',label);a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';parent.append(a,node('br'));}catch{}}
function section(parent,title,text){const d=node('div',undefined,'detail-section');d.append(node('strong',title),node('div',text||'Unknown / unverified'));parent.append(d);}
function kv(dl,label,value){dl.append(node('dt',label),node('dd',value===null||value===undefined||value===''?'Unknown / unverified':String(value)));}
function clock(v){return String(Math.floor(v/60)).padStart(2,'0')+':'+String(v%60).padStart(2,'0');}
function script(src,integrity){return new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=src;if(integrity){s.integrity=integrity;s.crossOrigin='anonymous';}s.onload=resolve;s.onerror=()=>{s.remove();reject(Error('Could not load the map library'));};document.head.append(s);});}
async function leaflet(){
 const c=node('link');c.rel='stylesheet';c.href='/stage1-static/vendor/leaflet.css';c.onerror=()=>{c.onerror=null;c.href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';c.integrity='sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=';c.crossOrigin='anonymous';};document.head.append(c);
 try{await script('/stage1-static/vendor/leaflet.js');}catch{await script('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js','sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=');}
 if(!window.L)throw Error('Run scripts/prepare_stage1_map.py and refresh. The data list is still available.');
}
async function loadAll(){
 let items=[],offset=0,total=null;
 do{const d=await get('/api/stage1c/features?view=all&limit=1000&offset='+offset+'&build_id='+encodeURIComponent(profile.build_id));
  if(total===null)total=d.matched_total;if(total!==d.matched_total)throw Error('The data version changed. Refresh the page.');
  items.push(...d.features);if(d.next_offset!==null&&d.next_offset<=offset)throw Error('Pagination did not advance');offset=d.next_offset;
 }while(offset!==null);
 if(items.length!==total)throw Error('The catalogue did not load completely');return items;
}
function render(){
 const terms=$('search').value.normalize('NFKC').toLowerCase().trim().split(/\s+/).filter(Boolean),cat=$('category').value,include=$('include-holds').checked;
 matched=all.filter(f=>{const p=f.properties;return(include||p.default_exploration_visible)&&(!cat||p.categories.includes(cat))&&terms.every(t=>(p.search_text||'').includes(t));});
 if(map){if(markers)map.removeLayer(markers);markers=L.geoJSON({type:'FeatureCollection',features:matched},{pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:5.5,weight:1,color:'#fff',fillColor:f.properties.default_exploration_visible?(COLORS[f.properties.category]||'#54796b'):'#8e8e8e',fillOpacity:.88}),onEachFeature:(f,l)=>{
  const p=f.properties,box=node('div');box.append(node('strong',p.display_title),node('p',p.visit_status==='legacy_record_hold'?'Historical coordinates, not the current venue location':'Source reference point, not a verified entrance'));const b=node('button','View sources and reviews');b.onclick=()=>detail(p.entity_id);box.append(b);l.bindPopup(box);
  l.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);detail(p.entity_id);});
 }}).addTo(map);}
 renderList();
}
function focusEntity(id){const f=all.find(f=>f.id===id);if(f&&map)map.setView([f.properties.latitude,f.properties.longitude],16);detail(id);}
function renderList(){
 const list=$('list');list.replaceChildren();const shown=matched.slice(0,listLimit);
 $('list-count').textContent=`Matches: ${matched.length} groups; listed: ${shown.length}. The map shows every matching place.`;
 if(!matched.length)list.append(node('p','No matching records. Try another name or category, or include archived records. This does not prove the place does not exist.','small'));
 for(const f of shown){const p=f.properties,b=node('button',p.display_title,'record-button');b.append(node('small',p.categories.map(c=>LABEL[c]||c).join(' / ')+` · Sources: ${p.member_count}`+(p.default_exploration_visible?'':' · Archived / pending review')));b.onclick=()=>focusEntity(p.entity_id);list.append(b);}
 $('more').hidden=listLimit>=matched.length;
}
function renderSchedule(panel,h){
 const card=node('div',undefined,'review-card');card.append(node('strong','Published regular hours · '+(h.freshness==='within_review_period'?'within review period':'review needed')));
 const identical=new Set(Object.values(h.weekly_minutes).map(v=>JSON.stringify(v))).size===1;
 if(identical)card.append(node('p','Every day: '+h.weekly_minutes['0'].map(p=>clock(p[0])+'—'+clock(p[1])).join(', ')));
 else for(let i=0;i<7;i++)card.append(node('p',['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][i]+': '+h.weekly_minutes[String(i)].map(p=>clock(p[0])+'—'+clock(p[1])).join(', ')));
 card.append(node('p',h.note_zh),node('p',`Reviewed: ${h.checked_on}; recheck suggested: ${h.recheck_on}. Temporary closures, special dates and live opening status remain unverified.`,'small'));
 h.web_sources.forEach(u=>safeLink(card,'Evidence source ↗',u));panel.append(card);
}
async function detail(id){
 const revision=++detailRevision,panel=$('detail');panel.replaceChildren(node('p','Loading…'));
 try{const d=await get('/api/stage1c/entity?entity_id='+encodeURIComponent(id)+'&build_id='+encodeURIComponent(profile.build_id));if(revision!==detailRevision)return;
  const e=d.entity;panel.replaceChildren(node('h3',e.display_title));panel.append(node('span',`Sources: ${e.member_count} · ${e.member_count>1?'grouped by evidence':'single-source record'}`,'badge'));
  panel.append(node('p',e.visit_status==='legacy_record_hold'?'This marker uses historical coordinates. Current venue information below is not assigned to this old location.':'Grouping does not establish current opening status, access or suitability for this trip.','stage-tip'));
  for(const x of e.legacy_reviews){const card=node('div',undefined,'review-card warning');card.append(node('strong','Reviewed risk in an old record'),node('p',x.note_zh),node('p','Successor / current venue: '+x.replacement.name),node('p','Current address: '+(x.replacement.address||'Not recorded in this review')),node('p',`Reviewed on: ${x.checked_on}. The current location has not been geocoded; the old marker has not been renamed as the new venue.`,'small'));x.web_sources.forEach(u=>safeLink(card,'Operator / official evidence ↗',u));panel.append(card);}
  if(e.source_status_holds.length)section(panel,'Source status needs review','Historical source data says Under Construction. The current status is unconfirmed.');
  const dl=node('dl');kv(dl,'Display group ID',e.entity_id);kv(dl,'Coordinate source',e.coordinate_source_uid);kv(dl,'Coordinate role',e.coordinate_kind);kv(dl,'Source coordinates',`${e.latitude}, ${e.longitude}`);kv(dl,'Source address',e.address);panel.append(dl);
  section(panel,'Source names / aliases',e.aliases.join('\n'));
  if(e.regular_hours_evidence.length)e.regular_hours_evidence.forEach(h=>renderSchedule(panel,h));else section(panel,'Current opening hours','Not verified in this review. Historical hours are retained in source details and are not a current guarantee.');
  section(panel,'Suggested duration and entrances','Visit duration has not been established and entrances are unverified. Published hours do not establish route feasibility.');
  const sources=node('div',undefined,'detail-section');sources.append(node('strong','Retained source details'));
  for(const r of d.source_records){const card=node('details',undefined,'source-card');card.append(node('summary',r.source_key+' · '+r.record_uid));const dl=node('dl');kv(dl,'Name',r.name);kv(dl,'Source period',r.provenance.source_period_label);kv(dl,'Address',r.address);kv(dl,'Coordinates',`${r.latitude}, ${r.longitude}`);card.append(dl);
   section(card,'Original source description',r.stage1b?.display_description||r.description);section(card,'Source hours (current validity unverified)',r.stage1b?.opening_hours_display||r.opening_hours_raw);safeLink(card,r.source_key==='overture_places'?'About Overture data ↗':'Official data catalogue ↗',r.provenance.catalogue_url);safeLink(card,'Original detail link (may be outdated) ↗',r.external_url);safeLink(card,'Data licence ↗',r.provenance.licence_url);sources.append(card);}
  panel.append(sources);
  if(e.related_records.length){const box=node('div',undefined,'detail-section');box.append(node('strong','Related records kept separate'));
   for(const rel of e.related_records){const f=all.find(x=>x.id===rel.entity_id);const b=node('button',f?.properties.display_title||rel.entity_id,'related-button');b.onclick=()=>focusEntity(rel.entity_id);box.append(b,node('p',rel.reason_zh,'small'));}panel.append(box);}
  if(e.access_point_candidates.length){const box=node('details',undefined,'detail-section');box.append(node('summary',`Spatial access clues: ${e.access_point_candidates.length} links (not unique entrance counts)`));
   for(const a of e.access_point_candidates){const row=node('div',undefined,'access-row'),s=a.semantic_review;row.append(node('strong',a.access_name||'Unnamed access point'),node('small','Linked boundary: '+a.boundary_name),node('small',ACCESS_LABEL[s.status]||s.status),node('small',s.explanation_zh));safeLink(row,'View original Stage 1B access-point record ↗',location.origin+'/api/stage1b/record?uid='+encodeURIComponent(a.access_uid)+'&build_id='+encodeURIComponent(profile.source_stage1b_build_id));box.append(row);}panel.append(box);
  }else section(panel,'Possible access points','No access point is listed by the current candidate rules. This does not mean the place has no entrance.');
 }catch(err){if(revision===detailRevision)panel.replaceChildren(node('p',err.message,'error'));}
}
function fit(){if(map&&all.length)map.fitBounds(all.map(f=>[f.properties.latitude,f.properties.longitude]),{padding:[24,24]});}
async function start(){
 $('search').oninput=$('category').onchange=$('include-holds').onchange=()=>{listLimit=100;render();};$('more').onclick=()=>{listLimit+=100;renderList();};$('fit').onclick=fit;
 $('clear-origin').onclick=()=>{if(originMarker&&map)map.removeLayer(originMarker);originMarker=null;$('origin').textContent='Not set. Kept only on this page; no location request or route calculation.';};
 try{profile=await get('/api/stage1c/status');all=await loadAll();$('records').textContent=profile.input_candidate_source_records;$('entities').textContent=profile.entity_display_count;$('groups').textContent=profile.reviewed_group_count;$('holds').textContent=profile.legacy_hold_entities+profile.historical_source_status_hold_entities;
  $('hold-note').textContent=`Default view: ${profile.default_exploration_entity_count} groups; current opening status is unverified. Enable archived records to see the rest.`;render();tell('data',`Nationwide: ${all.length} place groups loaded`);
 }catch(e){tell('data',e.message);$('list-count').textContent='The complete catalogue is unavailable. Run scripts/build_stage1c.py first.';return;}
 try{await leaflet();$('map').replaceChildren();map=L.map('map',{preferCanvas:true}).setView([1.3521,103.8198],11);
  tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
  tiles.on('tileerror',()=>tell('tiles','The basemap did not load. The local catalogue remains available; you can switch off the online basemap.'));
  $('tiles').onchange=()=>{$('tiles').checked?tiles.addTo(map):map.removeLayer(tiles);};
  map.on('click',e=>{if(!$('pick').checked)return;if(originMarker)map.removeLayer(originMarker);originMarker=L.circleMarker(e.latlng,{radius:8,color:'#193c35',weight:3,fillColor:'#fff',fillOpacity:1}).addTo(map);$('origin').textContent=`Temporary origin: ${e.latlng.lat.toFixed(6)}, ${e.latlng.lng.toFixed(6)}. No route calculated.`;$('pick').checked=false;});
  render();fit();tell('map','Display groups mapped; original coordinates and sources retained');
 }catch(e){const ph=$('map-placeholder');if(ph)ph.textContent='The map library did not load. The source catalogue remains available. Run scripts/prepare_stage1_map.py and refresh.';tell('map',e.message);}
}
start();
