'use strict';
// Source text is always inserted with textContent, never interpreted as HTML.
const $ = id => document.getElementById(id);
const LABEL = {tourism:'景点',nature:'公园',heritage:'历史地点',monument:'古迹',food:'熟食中心'};
const COLORS = {tourism:'#b67132',nature:'#32866d',heritage:'#6e75a0',monument:'#967095',food:'#cf7765'};
let map=null, tileLayer=null, originMarker=null, baseMarkers=null, origin=null;
let allPlaces=[], matched=[], listLimit=100, profile=null, selectedRequest=0;
const overlays={}, jobs={};
const messages={};
function tell(key,message){messages[key]=message;$('progress').textContent=Object.values(messages).filter(Boolean).join(' · ');}
function el(tag,txt,cls){const n=document.createElement(tag);if(txt!==undefined)n.textContent=txt;if(cls)n.className=cls;return n;}
async function get(url){const r=await fetch(url);if(!r.ok){let m='HTTP '+r.status;try{m=(await r.json()).detail||m;}catch{}throw Error(m);}return r.json();}
function script(url,integrity){return new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=url;if(integrity){s.integrity=integrity;s.crossOrigin='anonymous';}s.onload=resolve;s.onerror=()=>{s.remove();reject(Error('地图组件未能下载'));};document.head.append(s);});}
async function leaflet(){
 const css=document.createElement('link');css.rel='stylesheet';css.href='/stage1-static/vendor/leaflet.css';
 css.onerror=()=>{css.onerror=null;css.href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';css.integrity='sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=';css.crossOrigin='anonymous';};document.head.append(css);
 try{await script('/stage1-static/vendor/leaflet.js');}catch{
  await script('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js','sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=');
 }
 if(!window.L)throw Error('Leaflet未加载。运行 scripts/prepare_stage1_map.py 后刷新。');
}
async function fullLayer(layer){
 let items=[],offset=0,expected=null;
 do{
  const data=await get(`/api/stage1/features?layer=${encodeURIComponent(layer)}&limit=1500&offset=${offset}&build_id=${encodeURIComponent(profile.build_id)}`);
  if(expected===null)expected=data.matched_total;
  if(data.matched_total!==expected)throw Error('数据快照变化，请刷新页面。');
  items.push(...data.features);tell(layer,`${layer} ${items.length.toLocaleString()} / ${expected.toLocaleString()}`);
  if(data.next_offset!==null && data.next_offset<=offset)throw Error('分页没有前进，停止以避免循环。');
  offset=data.next_offset;
 }while(offset!==null);
 if(items.length!==expected)throw Error('图层未完整加载，请重试。');
 return items;
}
function collection(items){return {type:'FeatureCollection',features:items};}
function popup(feature,layer){
 const box=el('div');box.append(el('strong',feature.properties.display_title),el('p','源记录 · 未核验通行入口'));
 const button=el('button','查看完整记录');button.onclick=()=>detail(feature.properties.record_uid);box.append(button);layer.bindPopup(box);
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
 $('list-count').textContent=`匹配 ${matched.length.toLocaleString()} 条；列表显示 ${shown.length} 条，地图显示全部匹配点。`;
 for(const f of shown){const p=f.properties,b=el('button',p.display_title,'record-button');b.append(el('small',`${LABEL[p.category]||p.category} · ${p.source_key}`));b.onclick=()=>{if(map)map.setView([p.latitude,p.longitude],16);detail(p.record_uid);};list.append(b);}
 $('more').hidden=listLimit>=matched.length;
}
function kv(dl,label,value){dl.append(el('dt',label),el('dd',value===null||value===undefined||value===''?'未知 / 未核验':String(value)));}
function section(parent,title,value){const s=el('div',undefined,'detail-section');s.append(el('strong',title),el('div',value||'来源未提供；本阶段不自动补齐。'));parent.append(s);}
function link(parent,label,value){if(!value)return;try{const u=new URL(value);if(!['https:','http:'].includes(u.protocol))return;const a=el('a',label);a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';parent.append(a,el('br'));}catch{}}
async function detail(uid){
 const rev=++selectedRequest;const panel=$('detail');panel.replaceChildren(el('p','读取记录…'));
 try{
  const data=await get(`/api/stage1/record?uid=${encodeURIComponent(uid)}&build_id=${encodeURIComponent(profile.build_id)}`);if(rev!==selectedRequest)return;
  const r=data.record;panel.replaceChildren(el('h3',r.display_title),el('span','未核验 · 不能据此保证可游玩','badge'));
  const dl=el('dl');kv(dl,'来源',r.source_key);kv(dl,'记录ID',r.source_record_id);kv(dl,'资料时期',r.provenance.source_period_label);kv(dl,'获取时间',r.provenance.retrieved_at);
  kv(dl,'坐标类型',r.coordinate_kind);kv(dl,'源坐标',r.longitude===null?null:`${r.latitude}, ${r.longitude}`);kv(dl,'地址',r.address);kv(dl,'原始状态',r.source_status_raw);panel.append(dl);
  section(panel,'源数据简介',r.description);section(panel,'来源中的开放时间（未核验）',r.opening_hours_raw);
  section(panel,'停留时间 / 入口 / 费用','尚未建立或核验。不默认全天开放、免费或可进入。');
  section(panel,'数据问题',r.data_issues.length?r.data_issues.join('\n'):'未发现本阶段检查规则覆盖的问题；不代表现实信息已经正确。');
  const refs=el('div',undefined,'detail-section');link(refs,'官方数据目录 ↗',r.provenance.catalogue_url);link(refs,'来源中的详情链接（可能过时）↗',r.external_url);link(refs,'数据许可 ↗',r.provenance.licence_url);panel.append(refs);
  const raw=el('details');raw.append(el('summary','查看保留的原始属性'),el('pre',JSON.stringify(data.raw_source_feature.properties,null,2)));panel.append(raw);
 }catch(e){if(rev===selectedRequest)panel.replaceChildren(el('p',e.message,'error'));}
}
function bounds(){
 const pts=allPlaces.map(f=>[f.properties.latitude,f.properties.longitude]);
 if(map&&pts.length)map.fitBounds(pts,{padding:[30,30],maxZoom:12});
}
async function loadOverlay(layer,checkbox){
 if(!map){checkbox.checked=false;tell('map','地图组件不可用，无法显示图层。');return;}
 if(overlays[layer]){checkbox.checked?overlays[layer].addTo(map):map.removeLayer(overlays[layer]);return;}
 if(!checkbox.checked||jobs[layer])return;
 jobs[layer]=true;
 try{
  const items=await fullLayer(layer);
  const styles=layer==='parks'?{color:'#4d7b66',weight:1,fillOpacity:.09}:{color:'#567b94',weight:1,opacity:.6};
  overlays[layer]=L.geoJSON(collection(items),{style:styles,pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:2.5,color:'#528888',weight:0,fillOpacity:.7}),onEachFeature:popup});
  if(checkbox.checked)overlays[layer].addTo(map);
  tell(layer,`${layer} 已载入全部 ${items.length.toLocaleString()} 条`);
 }catch(e){checkbox.checked=false;tell(layer,`${layer}: ${e.message}`);}finally{jobs[layer]=false;}
}
async function start(){
 $('search').addEventListener('input',()=>{listLimit=100;render();});$('category').onchange=()=>{listLimit=100;render();};
 $('more').onclick=()=>{listLimit+=100;renderList();};$('fit').onclick=bounds;
 $('clear-origin').onclick=()=>{origin=null;if(map&&originMarker)map.removeLayer(originMarker);originMarker=null;$('origin').textContent='起点已清除；没有保存到服务器。';};
 try{
  profile=await get('/api/stage1/status');
  $('sources').textContent=profile.source_count;$('records').textContent=profile.stored_record_count.toLocaleString();$('places').textContent=profile.candidate_place_record_count.toLocaleString();$('entrances').textContent=profile.verified_entrance_count;
  tell('status',`全岛处理完成 · ${profile.map_eligible_count.toLocaleString()} 条可绘制源记录`);
  allPlaces=await fullLayer('places');render();
 }catch(e){tell('status',e.message);$('map-placeholder').textContent='请先运行 scripts/build_stage1.py；当前未加载任何模拟景点。';return;}
 try{
  await leaflet();$('map-placeholder').remove();
  map=L.map('map',{preferCanvas:true}).setView([1.3521,103.8198],11);
  tileLayer=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
  tileLayer.on('tileerror',()=>tell('tiles','背景瓦片加载失败；本地点图层仍可使用，可关闭在线背景地图。'));
  $('tiles').onchange=()=>{$('tiles').checked?tileLayer.addTo(map):map.removeLayer(tileLayer);};
  map.on('click',e=>{if(!$('pick').checked)return;origin={latitude:e.latlng.lat,longitude:e.latlng.lng};
   if(originMarker)map.removeLayer(originMarker);originMarker=L.circleMarker(e.latlng,{radius:8,color:'#182d32',fillColor:'#fff',fillOpacity:1,weight:3}).addTo(map);
   $('origin').textContent=`纬度 ${origin.latitude.toFixed(6)} / 经度 ${origin.longitude.toFixed(6)}；仅本页临时保存，未进行入口或通行核验。`;$('pick').checked=false;
  });
  render();bounds();
  document.querySelectorAll('[data-layer]').forEach(c=>c.onchange=()=>loadOverlay(c.dataset.layer,c));
  tell('map','候选点完整显示；辅助图层按需完整载入');
 }catch(e){const placeholder=$('map-placeholder');if(placeholder)placeholder.textContent='地图组件未能加载；左侧真实数据列表仍可使用。先运行 scripts/prepare_stage1_map.py 后刷新。';tell('map',e.message);}
}
start();
