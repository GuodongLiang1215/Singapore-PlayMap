'use strict';
// Source text is always inserted with textContent, never interpreted as HTML.
const $ = id => document.getElementById(id);
const LABEL = {tourism:'Attractions',nature:'Parks',heritage:'Heritage sites',monument:'Monuments',food:'Hawker centres'};
const COLORS = {tourism:'#b67132',nature:'#32866d',heritage:'#6e75a0',monument:'#967095',food:'#cf7765'};
let map=null, tileLayer=null, originMarker=null, baseMarkers=null, origin=null;
let allPlaces=[], matched=[], listLimit=100, profile=null, selectedRequest=0;
const overlays={}, jobs={};
const messages={};
function tell(key,message){messages[key]=message;$('progress').textContent=Object.values(messages).filter(Boolean).join(' · ');}
function el(tag,txt,cls){const n=document.createElement(tag);if(txt!==undefined)n.textContent=txt;if(cls)n.className=cls;return n;}
async function get(url){const r=await fetch(url);if(!r.ok){let m='HTTP '+r.status;try{m=(await r.json()).detail||m;}catch{}throw Error(m);}return r.json();}
function script(url,integrity){return new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=url;if(integrity){s.integrity=integrity;s.crossOrigin='anonymous';}s.onload=resolve;s.onerror=()=>{s.remove();reject(Error('Could not download the map library'));};document.head.append(s);});}
async function leaflet(){
 const css=document.createElement('link');css.rel='stylesheet';css.href='/stage1-static/vendor/leaflet.css';
 css.onerror=()=>{css.onerror=null;css.href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';css.integrity='sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=';css.crossOrigin='anonymous';};document.head.append(css);
 try{await script('/stage1-static/vendor/leaflet.js');}catch{
  await script('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js','sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=');
 }
 if(!window.L)throw Error('Leaflet did not load. Run scripts/prepare_stage1_map.py and refresh.');
}
async function fullLayer(layer){
 let items=[],offset=0,expected=null;
 do{
  const data=await get(`/api/stage1/features?layer=${encodeURIComponent(layer)}&limit=1500&offset=${offset}&build_id=${encodeURIComponent(profile.build_id)}`);
  if(expected===null)expected=data.matched_total;
  if(data.matched_total!==expected)throw Error('The data snapshot changed. Refresh the page.');
  items.push(...data.features);tell(layer,`${layer} ${items.length.toLocaleString("en-SG")} / ${expected.toLocaleString("en-SG")}`);
  if(data.next_offset!==null && data.next_offset<=offset)throw Error('Pagination did not advance. Loading stopped to avoid a loop.');
  offset=data.next_offset;
 }while(offset!==null);
 if(items.length!==expected)throw Error('The layer did not load completely. Try again.');
 return items;
}
function collection(items){return {type:'FeatureCollection',features:items};}
function popup(feature,layer){
 const box=el('div');box.append(el('strong',feature.properties.display_title),el('p','Source record · access point unverified'));
 const button=el('button','View full record');button.onclick=()=>detail(feature.properties.record_uid);box.append(button);layer.bindPopup(box);
 layer.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);detail(feature.properties.record_uid);});
}
function candidateStyle(feature,latlng){return L.circleMarker(latlng,{radius:5,color:'#fff',weight:1,fillColor:COLORS[feature.properties.category]||'#687f74',fillOpacity:.9});}
function render(){
 const q=$('search').value.normalize('NFKC').toLocaleLowerCase().trim().split(/\s+/).filter(Boolean),cat=$('category').value;
 matched=allPlaces.filter(f=>{
  const p=f.properties;const hay=[p.name,p.display_title,p.address].filter(Boolean).join(' ').normalize('NFKC').toLocaleLowerCase();
  return (!cat||p.category===cat)&&q.every(t=>hay.includes(t));
 });
 if(map){if(baseMarkers)map.removeLayer(baseMarkers);baseMarkers=L.geoJSON(collection(matched),{pointToLayer:candidateStyle,onEachFeature:popup}).addTo(map);}
 renderList();
}
function renderList(){
 const list=$('list');list.replaceChildren();
 const shown=matched.slice(0,listLimit);
 $('list-count').textContent=`Matches: ${matched.length.toLocaleString("en-SG")} records; listed: ${shown.length}. The map shows every matching point.`;
 for(const f of shown){const p=f.properties,b=el('button',p.display_title,'record-button');b.append(el('small',`${LABEL[p.category]||p.category} · ${p.source_key}`));b.onclick=()=>{if(map)map.setView([p.latitude,p.longitude],16);detail(p.record_uid);};list.append(b);}
 $('more').hidden=listLimit>=matched.length;
}
function kv(dl,label,value){dl.append(el('dt',label),el('dd',value===null||value===undefined||value===''?'Unknown / unverified':String(value)));}
function section(parent,title,value){const s=el('div',undefined,'detail-section');s.append(el('strong',title),el('div',value||'Not supplied by the source; not inferred.'));parent.append(s);}
function link(parent,label,value){if(!value)return;try{const u=new URL(value);if(!['https:','http:'].includes(u.protocol))return;const a=el('a',label);a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';parent.append(a,el('br'));}catch{}}
async function detail(uid){
 const rev=++selectedRequest;const panel=$('detail');panel.replaceChildren(el('p','Loading record…'));
 try{
  const data=await get(`/api/stage1/record?uid=${encodeURIComponent(uid)}&build_id=${encodeURIComponent(profile.build_id)}`);if(rev!==selectedRequest)return;
  const r=data.record;panel.replaceChildren(el('h3',r.display_title),el('span','Unverified · a visit is not guaranteed','badge'));
  const dl=el('dl');kv(dl,'Source',r.source_key);kv(dl,'Record ID',r.source_record_id);kv(dl,'Source period',r.provenance.source_period_label);kv(dl,'Retrieved at',r.provenance.retrieved_at);
  kv(dl,'Coordinate type',r.coordinate_kind);kv(dl,'Source coordinates',r.longitude===null?null:`${r.latitude}, ${r.longitude}`);kv(dl,'Address',r.address);kv(dl,'Original status',r.source_status_raw);panel.append(dl);
  section(panel,'Source description',r.description);section(panel,'Source opening hours (unverified)',r.opening_hours_raw);
  section(panel,'Duration / entrances / price','Not established or verified. Opening all day, free entry and access are not assumed.');
  section(panel,'Data issues',r.data_issues.length?r.data_issues.join('\n'):'No issue found by these checks. Real-world accuracy is not established.');
  const refs=el('div',undefined,'detail-section');link(refs,'Official data catalogue ↗',r.provenance.catalogue_url);link(refs,'Source detail link (may be outdated) ↗',r.external_url);link(refs,'Data licence ↗',r.provenance.licence_url);panel.append(refs);
  const raw=el('details');raw.append(el('summary','View retained source attributes'),el('pre',JSON.stringify(data.raw_source_feature.properties,null,2)));panel.append(raw);
 }catch(e){if(rev===selectedRequest)panel.replaceChildren(el('p',e.message,'error'));}
}
function bounds(){
 const pts=allPlaces.map(f=>[f.properties.latitude,f.properties.longitude]);
 if(map&&pts.length)map.fitBounds(pts,{padding:[30,30],maxZoom:12});
}
async function loadOverlay(layer,checkbox){
 if(!map){checkbox.checked=false;tell('map','The map library is unavailable; this layer cannot be displayed.');return;}
 if(overlays[layer]){checkbox.checked?overlays[layer].addTo(map):map.removeLayer(overlays[layer]);return;}
 if(!checkbox.checked||jobs[layer])return;
 jobs[layer]=true;
 try{
  const items=await fullLayer(layer);
  const styles=layer==='parks'?{color:'#4d7b66',weight:1,fillOpacity:.09}:{color:'#567b94',weight:1,opacity:.6};
  overlays[layer]=L.geoJSON(collection(items),{style:styles,pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:2.5,color:'#528888',weight:0,fillOpacity:.7}),onEachFeature:popup});
  if(checkbox.checked)overlays[layer].addTo(map);
  tell(layer,`${layer} loaded all ${items.length.toLocaleString("en-SG")} records`);
 }catch(e){checkbox.checked=false;tell(layer,`${layer}: ${e.message}`);}finally{jobs[layer]=false;}
}
async function start(){
 $('search').addEventListener('input',()=>{listLimit=100;render();});$('category').onchange=()=>{listLimit=100;render();};
 $('more').onclick=()=>{listLimit+=100;renderList();};$('fit').onclick=bounds;
 $('clear-origin').onclick=()=>{origin=null;if(map&&originMarker)map.removeLayer(originMarker);originMarker=null;$('origin').textContent='Origin cleared; it was not saved to the server.';};
 try{
  profile=await get('/api/stage1/status');
  $('sources').textContent=profile.source_count;$('records').textContent=profile.stored_record_count.toLocaleString("en-SG");$('places').textContent=profile.candidate_place_record_count.toLocaleString("en-SG");$('entrances').textContent=profile.verified_entrance_count;
  tell('status',`Nationwide processing complete · ${profile.map_eligible_count.toLocaleString("en-SG")} drawable source records`);
  allPlaces=await fullLayer('places');render();
 }catch(e){tell('status',e.message);$('map-placeholder').textContent='Run scripts/build_stage1.py first. No simulated places have been loaded.';return;}
 try{
  await leaflet();$('map-placeholder').remove();
  map=L.map('map',{preferCanvas:true}).setView([1.3521,103.8198],11);
  tileLayer=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
  tileLayer.on('tileerror',()=>tell('tiles','Basemap tiles failed to load. Local place layers remain usable; you can switch off the online basemap.'));
  $('tiles').onchange=()=>{$('tiles').checked?tileLayer.addTo(map):map.removeLayer(tileLayer);};
  map.on('click',e=>{if(!$('pick').checked)return;origin={latitude:e.latlng.lat,longitude:e.latlng.lng};
   if(originMarker)map.removeLayer(originMarker);originMarker=L.circleMarker(e.latlng,{radius:8,color:'#182d32',fillColor:'#fff',fillOpacity:1,weight:3}).addTo(map);
   $('origin').textContent=`Latitude ${origin.latitude.toFixed(6)} / Longitude ${origin.longitude.toFixed(6)}; kept only on this page. Entrance and access remain unverified.`;$('pick').checked=false;
  });
  render();bounds();
  document.querySelectorAll('[data-layer]').forEach(c=>c.onchange=()=>loadOverlay(c.dataset.layer,c));
  tell('map','All candidates displayed; supporting layers load in full on request');
 }catch(e){const placeholder=$('map-placeholder');if(placeholder)placeholder.textContent='The map library did not load. The source data list remains available. Run scripts/prepare_stage1_map.py and refresh.';tell('map',e.message);}
}
start();
