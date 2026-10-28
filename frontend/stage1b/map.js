'use strict';
const $=id=>document.getElementById(id);
const messages={};
const LABEL={tourism:'Attractions',nature:'Parks',heritage:'Heritage sites',monument:'Monuments',food:'Hawker centres'};
const COLORS={tourism:'#b67132',nature:'#32866d',heritage:'#6e75a0',monument:'#967095',food:'#cf7765'};
let profile=null,map=null,tiles=null,originMarker=null,selectedMarker=null,markers=null;
let places=[],matched=[],listLimit=100,detailVersion=0;
const cache={},jobs={},overlays={};
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function tell(key,text){messages[key]=text;$('progress').textContent=Object.values(messages).filter(Boolean).join(' · ');}
async function get(url){const r=await fetch(url);if(!r.ok){let m='HTTP '+r.status;try{m=(await r.json()).detail||m;}catch{}throw Error(typeof m==='string'?m:JSON.stringify(m));}return r.json();}
function collection(items){return {type:'FeatureCollection',features:items};}
function norm(text){return String(text||'').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,' ').trim();}
async function fullLayer(layer){
 if(cache[layer])return cache[layer];
 if(jobs[layer])return jobs[layer];
 jobs[layer]=(async()=>{let items=[],offset=0,total=null;
  do{const d=await get(`/api/stage1b/features?layer=${encodeURIComponent(layer)}&view=all&offset=${offset}&limit=1500&build_id=${encodeURIComponent(profile.build_id)}`);
   if(total===null)total=d.matched_total;if(total!==d.matched_total||d.build_id!==profile.build_id)throw Error('The snapshot changed. Refresh the page.');
   items.push(...d.features);tell(layer,`${layer}: ${items.length.toLocaleString("en-SG")} / ${total.toLocaleString("en-SG")}`);
   if(d.next_offset!==null&&d.next_offset<=offset)throw Error('Pagination did not advance.');offset=d.next_offset;
  }while(offset!==null);
  if(items.length!==total)throw Error('The layer did not load completely.');cache[layer]=items;return items;
 })();try{return await jobs[layer];}finally{delete jobs[layer];}
}
function script(url,integrity){return new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=url;if(integrity){s.integrity=integrity;s.crossOrigin='anonymous';}s.onload=resolve;s.onerror=()=>{s.remove();reject(Error('Could not load the map library'));};document.head.append(s);});}
async function loadLeaflet(){
 const css=el('link');css.rel='stylesheet';css.href='/stage1-static/vendor/leaflet.css';css.onerror=()=>{css.onerror=null;css.href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';css.integrity='sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=';css.crossOrigin='anonymous';};document.head.append(css);
 try{await script('/stage1-static/vendor/leaflet.js');}catch{await script('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js','sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=');}
 if(!window.L)throw Error('Run scripts/prepare_stage1_map.py and refresh.');
}
function popup(f,l){const p=f.properties;const box=el('div');box.append(el('strong',p.display_title),el('p',p.facility_class==='ACCESS POINT'?'Source access point, not a verified entrance':'Source record; current access is unverified'));
 const b=el('button','View record and evidence');b.onclick=()=>detail(p.record_uid);box.append(b);l.bindPopup(box);l.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);detail(p.record_uid);});}
function drawPoints(items){return L.geoJSON(collection(items),{pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:5,weight:1,color:f.properties.review_hold?'#6d3c18':'#fff',fillColor:COLORS[f.properties.category]||'#56756b',fillOpacity:.9}),onEachFeature:popup});}
function render(){const terms=norm($('search').value).split(' ').filter(Boolean),cat=$('category').value,include=$('include-holds').checked;
 matched=places.filter(f=>{const p=f.properties;return (!cat||p.category===cat)&&(include||!p.review_hold)&&terms.every(t=>p.search_text.includes(t));});
 if(map){if(markers)map.removeLayer(markers);markers=drawPoints(matched).addTo(map);}renderList();
}
function renderList(){const list=$('list');list.replaceChildren();const shown=matched.slice(0,listLimit);
 $('list-count').textContent=`Matches: ${matched.length} records; listed: ${shown.length}. The map shows every matching point.`;
 for(const f of shown){const p=f.properties;const b=el('button',p.display_title,'record-button'+(p.review_hold?' held':''));b.append(el('small',`${LABEL[p.category]||p.category} · ${p.review_hold?'Source status needs review':'Current opening status unverified'}`));b.onclick=()=>{focus(p);detail(p.record_uid);};list.append(b);}
 $('more').hidden=listLimit>=matched.length;
}
function focus(p){if(map&&p.latitude!==null&&p.longitude!==null){map.setView([p.latitude,p.longitude],16);}}
function kv(dl,k,v){dl.append(el('dt',k),el('dd',v===null||v===undefined||v===''?'Unknown / unverified':String(v)));}
function section(parent,title,body){const s=el('section',undefined,'detail-section');s.append(el('strong',title),el('p',body));parent.append(s);return s;}
function link(parent,url,label){if(!url)return;try{const u=new URL(url);if(!['http:','https:'].includes(u.protocol))return;const a=el('a',label);a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';parent.append(a,el('br'));}catch{}}
function focusAccess(p){if(map){focus(p);if(selectedMarker)map.removeLayer(selectedMarker);selectedMarker=L.circleMarker([p.latitude,p.longitude],{radius:9,color:'#cc7042',weight:3,fillOpacity:.3}).addTo(map);}detail(p.access_uid);}
async function detail(uid){const ticket=++detailVersion;const box=$('detail');box.replaceChildren(el('p','Loading record…'));
 try{const d=await get(`/api/stage1b/record?uid=${encodeURIComponent(uid)}&build_id=${encodeURIComponent(profile.build_id)}`);if(ticket!==detailVersion)return;
 const r=d.record,e=d.enrichment;box.replaceChildren(el('h3',e.display_title),el('span','Source record · current planning suitability unverified','badge'));
 const dl=el('dl');kv(dl,'Source',r.source_key);kv(dl,'Source period',e.source_period_label);kv(dl,'Original name',r.name);kv(dl,'Address',r.address);kv(dl,'Coordinate role',e.coordinate_role);kv(dl,'Source status',e.source_status_interpreted);kv(dl,'Verified entrances',0);box.append(dl);
 if(e.review_hold)box.append(el('p','The source marked this place Under Construction, so it is excluded from the default view. Its current status is unknown. Enable review-pending records to display it.','candidate-note'));
 if(e.facility_class)section(box,'Facility category',`${e.facility_class} → ${e.facility_group_label}. Classification comes from source attributes, not verified opening status or suitability as an attraction.`);
 if(Object.keys(e.source_permissions).length){const v={source_yes:'Source value Y',source_no:'Source value N',unknown:'Unknown / empty'};const t={allow_walking:'Walking',allow_cycling:'Cycling',allow_pmd:'PMD',allow_wheeling:'Wheeling'};section(box,'Source trail attributes',Object.entries(e.source_permissions).map(([k,x])=>`${t[k]}: ${v[x]}`).join('; ')+'. This does not establish route connectivity or wheelchair access.');}
 section(box,'Opening hours · current validity unverified',e.opening_hours_display||'No source opening hours; not assumed open all day.');
 if(e.display_description)section(box,'Source description · current access unverified',e.display_description);
 if(Object.keys(e.display_repairs).length)section(box,'Display text repairs','Known encoding issues are repaired for display only. Original fields and files are retained in the source feature below.');
 if(r.source_key==='nparks_parks'){
  const s=section(box,'Possible park boundary links',e.park_boundary_candidates.length?'These links use matching names and spatial evidence; identity is not formally confirmed.':'No boundary matches the current rules. A link is not forced by row order or proximity.');
  for(const p of e.park_boundary_candidates){s.append(el('p',`${p.boundary_name} · ${p.association_basis} · point-to-polygon distance ${p.distance_to_polygon_metres} m`,'evidence'));}
 }
 if(['nparks_parks','nparks_boundaries'].includes(r.source_key)){
  const s=section(box,'Access-point candidates',d.access_point_candidates.length?'Source ACCESS POINT records are spatially linked to boundaries. All candidates are retained; entrance validity, direction and route access are unverified.':'No access-point candidates are available. The park centre is not used as an entrance.');
  for(const p of d.access_point_candidates){const b=el('button',`${p.display_title} · ${p.point_relation} · distance to management boundary ${p.distance_to_boundary_metres} m`,'detail-action');b.onclick=()=>focusAccess(p);s.append(b);}
 }
 if(d.boundary_candidates.length){const s=section(box,'Possible management-area links','A spatial relationship does not establish that this point serves the park or is currently open to the public.');for(const b of d.boundary_candidates){const button=el('button',`${b.boundary_name} · ${b.point_relation} · distance to polygon ${b.distance_to_polygon_metres} m`,'detail-action');button.onclick=()=>detail(b.boundary_uid);s.append(button);}}
 if(d.duplicate_candidates.length){const s=section(box,'Possible duplicates · not merged','Candidate rules may include false matches or miss duplicates.');for(const p of d.duplicate_candidates){const other=p.left_uid===uid?'right':'left';const b=el('button',`${p[other+'_name']} · ${p.reason} · straight-line distance ${p.straight_line_metres} m`,'detail-action');b.onclick=()=>detail(p[other+'_uid']);s.append(b);}}
 section(box,'Visit duration / price','Unknown. Visit duration is not inferred and an unknown price is not treated as free.');
 link(box,r.external_url,'Source website (current validity unverified) ↗');link(box,r.provenance.catalogue_url,'Official data catalogue ↗');link(box,r.provenance.licence_url,'Data licence ↗');
 const original=el('details');original.append(el('summary','View original feature'),el('pre',JSON.stringify(d.raw_source_feature,null,2)));box.append(original);
 }catch(err){if(ticket===detailVersion)box.replaceChildren(el('p',err.message,'error'));}
}
async function overlay(name){if(!map)return;const checkbox=document.querySelector(`[data-layer="${name}"]`);if(overlays[name]){map.removeLayer(overlays[name]);delete overlays[name];}if(!checkbox.checked){tell('visible-'+name,'');return;}
 try{let items=await fullLayer(name);if(!checkbox.checked)return;if(overlays[name])map.removeLayer(overlays[name]);
  if(name==='facilities'){const group=$('facility-group').value;if(group)items=items.filter(f=>f.properties.facility_group===group);}
  const layer=L.geoJSON(collection(items),{pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:name==='access_points'?5:3,color:name==='access_points'?'#d06a35':'#587569',weight:1,fillOpacity:.8}),
   style:f=>name==='parks'?{color:'#57866f',weight:1,fillOpacity:.08}:{color:f.properties.source_permissions.allow_walking==='source_no'?'#a94c48':f.properties.source_permissions.allow_walking==='unknown'?'#b39956':'#7f9688',weight:2},onEachFeature:popup}).addTo(map);
  overlays[name]=layer;tell('visible-'+name,`${name} shows ${items.length.toLocaleString("en-SG")} source records`);
 }catch(e){checkbox.checked=false;tell('visible-'+name,e.message);}
}
function fit(){if(map&&places.length){const b=L.latLngBounds(places.map(f=>[f.properties.latitude,f.properties.longitude]));if(b.isValid())map.fitBounds(b.pad(.04));}}
async function start(){
 for(const id of ['search','category','include-holds'])$(id).addEventListener(id==='search'?'input':'change',()=>{listLimit=100;render();});
 $('more').onclick=()=>{listLimit+=100;renderList();};$('fit').onclick=fit;
 $('clear-origin').onclick=()=>{if(originMarker&&map)map.removeLayer(originMarker);originMarker=null;$('origin').textContent='Not set. No location request or route calculation.';};
 $('facility-group').onchange=()=>overlay('facilities');
 try{profile=await get('/api/stage1b/status');$('records').textContent=profile.retained_record_count.toLocaleString("en-SG");$('places').textContent=profile.candidate_place_record_count.toLocaleString("en-SG");$('access-count').textContent=profile.spatial_association.source_access_point_record_count;$('duplicates').textContent=profile.duplicate_candidate_pair_count;
  $('hold-note').textContent=`Retained: ${profile.candidate_place_record_count} place records; the default view excludes ${profile.source_status_review_hold_count} records whose source status needs review. Enable them to display; they are not confirmed closed.`;
  places=await fullLayer('places');render();tell('status','Complete local snapshot loaded; links are candidates, not verified entrances');
 }catch(e){tell('status',e.message);$('map-placeholder').textContent='Run scripts/build_stage1b.py first. No simulated places will be loaded.';return;}
 try{await loadLeaflet();$('map-placeholder').remove();map=L.map('map',{preferCanvas:true}).setView([1.3521,103.8198],11);
  tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
  tiles.on('tileerror',()=>tell('tiles','The online basemap did not load. Local data remains available; you can switch off the basemap.'));$('tiles').onchange=()=>{$('tiles').checked?tiles.addTo(map):map.removeLayer(tiles);};
  map.on('click',e=>{if(!$('pick').checked)return;if(originMarker)map.removeLayer(originMarker);originMarker=L.circleMarker(e.latlng,{radius:8,color:'#183d38',fillColor:'#fff',weight:3,fillOpacity:1}).addTo(map);$('origin').textContent=`Latitude ${e.latlng.lat.toFixed(6)}, Longitude ${e.latlng.lng.toFixed(6)}; temporary selection on this page. Entrance unverified.`;$('pick').checked=false;});
  render();fit();document.querySelectorAll('[data-layer]').forEach(c=>c.onchange=()=>overlay(c.dataset.layer));
 }catch(e){tell('map',e.message);const p=$('map-placeholder');if(p)p.textContent='The map library did not load. The source data list remains available. Run scripts/prepare_stage1_map.py and refresh.';}
}
start();
