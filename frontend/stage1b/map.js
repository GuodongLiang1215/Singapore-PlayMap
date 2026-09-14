'use strict';
const $=id=>document.getElementById(id);
const messages={};
const LABEL={tourism:'景点',nature:'公园',heritage:'历史地点',monument:'古迹',food:'熟食中心'};
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
   if(total===null)total=d.matched_total;if(total!==d.matched_total||d.build_id!==profile.build_id)throw Error('快照改变，请刷新页面。');
   items.push(...d.features);tell(layer,`${layer}: ${items.length.toLocaleString()} / ${total.toLocaleString()}`);
   if(d.next_offset!==null&&d.next_offset<=offset)throw Error('分页未前进。');offset=d.next_offset;
  }while(offset!==null);
  if(items.length!==total)throw Error('图层未完整读取。');cache[layer]=items;return items;
 })();try{return await jobs[layer];}finally{delete jobs[layer];}
}
function script(url,integrity){return new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=url;if(integrity){s.integrity=integrity;s.crossOrigin='anonymous';}s.onload=resolve;s.onerror=()=>{s.remove();reject(Error('地图组件加载失败'));};document.head.append(s);});}
async function loadLeaflet(){
 const css=el('link');css.rel='stylesheet';css.href='/stage1-static/vendor/leaflet.css';css.onerror=()=>{css.onerror=null;css.href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';css.integrity='sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=';css.crossOrigin='anonymous';};document.head.append(css);
 try{await script('/stage1-static/vendor/leaflet.js');}catch{await script('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js','sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=');}
 if(!window.L)throw Error('请运行 scripts/prepare_stage1_map.py 后刷新。');
}
function popup(f,l){const p=f.properties;const box=el('div');box.append(el('strong',p.display_title),el('p',p.facility_class==='ACCESS POINT'?'源数据接入点，非已核验入口':'源记录，当前通行条件未核验'));
 const b=el('button','查看记录与证据');b.onclick=()=>detail(p.record_uid);box.append(b);l.bindPopup(box);l.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);detail(p.record_uid);});}
function drawPoints(items){return L.geoJSON(collection(items),{pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:5,weight:1,color:f.properties.review_hold?'#6d3c18':'#fff',fillColor:COLORS[f.properties.category]||'#56756b',fillOpacity:.9}),onEachFeature:popup});}
function render(){const terms=norm($('search').value).split(' ').filter(Boolean),cat=$('category').value,include=$('include-holds').checked;
 matched=places.filter(f=>{const p=f.properties;return (!cat||p.category===cat)&&(include||!p.review_hold)&&terms.every(t=>p.search_text.includes(t));});
 if(map){if(markers)map.removeLayer(markers);markers=drawPoints(matched).addTo(map);}renderList();
}
function renderList(){const list=$('list');list.replaceChildren();const shown=matched.slice(0,listLimit);
 $('list-count').textContent=`匹配 ${matched.length} 条；列表显示 ${shown.length} 条，地图显示全部匹配点。`;
 for(const f of shown){const p=f.properties;const b=el('button',p.display_title,'record-button'+(p.review_hold?' held':''));b.append(el('small',`${LABEL[p.category]||p.category} · ${p.review_hold?'来源状态待复核':'当前开放未核验'}`));b.onclick=()=>{focus(p);detail(p.record_uid);};list.append(b);}
 $('more').hidden=listLimit>=matched.length;
}
function focus(p){if(map&&p.latitude!==null&&p.longitude!==null){map.setView([p.latitude,p.longitude],16);}}
function kv(dl,k,v){dl.append(el('dt',k),el('dd',v===null||v===undefined||v===''?'未知 / 未核验':String(v)));}
function section(parent,title,body){const s=el('section',undefined,'detail-section');s.append(el('strong',title),el('p',body));parent.append(s);return s;}
function link(parent,url,label){if(!url)return;try{const u=new URL(url);if(!['http:','https:'].includes(u.protocol))return;const a=el('a',label);a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';parent.append(a,el('br'));}catch{}}
function focusAccess(p){if(map){focus(p);if(selectedMarker)map.removeLayer(selectedMarker);selectedMarker=L.circleMarker([p.latitude,p.longitude],{radius:9,color:'#cc7042',weight:3,fillOpacity:.3}).addTo(map);}detail(p.access_uid);}
async function detail(uid){const ticket=++detailVersion;const box=$('detail');box.replaceChildren(el('p','读取记录…'));
 try{const d=await get(`/api/stage1b/record?uid=${encodeURIComponent(uid)}&build_id=${encodeURIComponent(profile.build_id)}`);if(ticket!==detailVersion)return;
 const r=d.record,e=d.enrichment;box.replaceChildren(el('h3',e.display_title),el('span','源记录 · 未确认当前可用于规划','badge'));
 const dl=el('dl');kv(dl,'来源',r.source_key);kv(dl,'资料时期',e.source_period_label);kv(dl,'原始名称',r.name);kv(dl,'地址',r.address);kv(dl,'坐标角色',e.coordinate_role);kv(dl,'源状态',e.source_status_interpreted);kv(dl,'已核验入口',0);box.append(dl);
 if(e.review_hold)box.append(el('p','来源曾标为 Under Construction，因此暂不进入默认探索列表；这不是对现在仍在施工的判断。可在左侧勾选显示。','candidate-note'));
 if(e.facility_class)section(box,'设施分类',`${e.facility_class} → ${e.facility_group_label}。分类来自源属性，不作为独立景点或开放确认。`);
 if(Object.keys(e.source_permissions).length){const v={source_yes:'源值 Y',source_no:'源值 N',unknown:'未知 / 空值'};const t={allow_walking:'步行',allow_cycling:'骑行',allow_pmd:'PMD',allow_wheeling:'Wheeling'};section(box,'步道源属性',Object.entries(e.source_permissions).map(([k,x])=>`${t[k]}：${v[x]}`).join('；')+'。不等于完整路线可达或轮椅可通行证明。');}
 section(box,'开放时间 · 未做当前核验',e.opening_hours_display||'源记录没有开放时间，未按全天开放处理。');
 if(e.display_description)section(box,'源描述 · 时效与访问条件未核验',e.display_description);
 if(Object.keys(e.display_repairs).length)section(box,'显示文本处理','仅对少量已知乱码片段作展示替换，原字段与原文件不改写；下方保留原始Feature。');
 if(r.source_key==='nparks_parks'){
  const s=section(box,'公园边界关联候选',e.park_boundary_candidates.length?'以下是同名与空间位置的关联证据，不是正式身份确认。':'没有符合当前规则的边界候选，不会按行号或最近点强行关联。');
  for(const p of e.park_boundary_candidates){s.append(el('p',`${p.boundary_name} · ${p.association_basis} · 点到面 ${p.distance_to_polygon_metres} m`,'evidence'));}
 }
 if(['nparks_parks','nparks_boundaries'].includes(r.source_key)){
  const s=section(box,'接入点候选',d.access_point_candidates.length?'这些点由源 ACCESS POINT 与边界空间关联得到；多个候选会全部保留，尚未验证真实入口、通行方向及导航可达。':'当前没有可列出的接入点候选。不会以公园中心代替入口。');
  for(const p of d.access_point_candidates){const b=el('button',`${p.display_title} · ${p.point_relation} · 距管理边界 ${p.distance_to_boundary_metres} m`,'detail-action');b.onclick=()=>focusAccess(p);s.append(b);}
 }
 if(d.boundary_candidates.length){const s=section(box,'关联的管理范围候选','只表示位置关系，不保证该接入点服务于该公园或当前对公众开放。');for(const b of d.boundary_candidates){const button=el('button',`${b.boundary_name} · ${b.point_relation} · 距面 ${b.distance_to_polygon_metres} m`,'detail-action');button.onclick=()=>detail(b.boundary_uid);s.append(button);}}
 if(d.duplicate_candidates.length){const s=section(box,'疑似重复 · 尚未合并','候选规则可能有误报和漏报。');for(const p of d.duplicate_candidates){const other=p.left_uid===uid?'right':'left';const b=el('button',`${p[other+'_name']} · ${p.reason} · 直线 ${p.straight_line_metres} m`,'detail-action');b.onclick=()=>detail(p[other+'_uid']);s.append(b);}}
 section(box,'游玩时长 / 费用','当前未知；没有自动估计游玩时长，也没有把未知价格当成免费。');
 link(box,r.external_url,'源记录外部网址（时效未核验） ↗');link(box,r.provenance.catalogue_url,'官方数据目录 ↗');link(box,r.provenance.licence_url,'数据许可 ↗');
 const original=el('details');original.append(el('summary','查看原始Feature'),el('pre',JSON.stringify(d.raw_source_feature,null,2)));box.append(original);
 }catch(err){if(ticket===detailVersion)box.replaceChildren(el('p',err.message,'error'));}
}
async function overlay(name){if(!map)return;const checkbox=document.querySelector(`[data-layer="${name}"]`);if(overlays[name]){map.removeLayer(overlays[name]);delete overlays[name];}if(!checkbox.checked){tell('visible-'+name,'');return;}
 try{let items=await fullLayer(name);if(!checkbox.checked)return;if(overlays[name])map.removeLayer(overlays[name]);
  if(name==='facilities'){const group=$('facility-group').value;if(group)items=items.filter(f=>f.properties.facility_group===group);}
  const layer=L.geoJSON(collection(items),{pointToLayer:(f,ll)=>L.circleMarker(ll,{radius:name==='access_points'?5:3,color:name==='access_points'?'#d06a35':'#587569',weight:1,fillOpacity:.8}),
   style:f=>name==='parks'?{color:'#57866f',weight:1,fillOpacity:.08}:{color:f.properties.source_permissions.allow_walking==='source_no'?'#a94c48':f.properties.source_permissions.allow_walking==='unknown'?'#b39956':'#7f9688',weight:2},onEachFeature:popup}).addTo(map);
  overlays[name]=layer;tell('visible-'+name,`${name} 显示 ${items.length.toLocaleString()} 条源记录`);
 }catch(e){checkbox.checked=false;tell('visible-'+name,e.message);}
}
function fit(){if(map&&places.length){const b=L.latLngBounds(places.map(f=>[f.properties.latitude,f.properties.longitude]));if(b.isValid())map.fitBounds(b.pad(.04));}}
async function start(){
 for(const id of ['search','category','include-holds'])$(id).addEventListener(id==='search'?'input':'change',()=>{listLimit=100;render();});
 $('more').onclick=()=>{listLimit+=100;renderList();};$('fit').onclick=fit;
 $('clear-origin').onclick=()=>{if(originMarker&&map)map.removeLayer(originMarker);originMarker=null;$('origin').textContent='未设置；不请求GPS，不计算路线。';};
 $('facility-group').onchange=()=>overlay('facilities');
 try{profile=await get('/api/stage1b/status');$('records').textContent=profile.retained_record_count.toLocaleString();$('places').textContent=profile.candidate_place_record_count.toLocaleString();$('access-count').textContent=profile.spatial_association.source_access_point_record_count;$('duplicates').textContent=profile.duplicate_candidate_pair_count;
  $('hold-note').textContent=`共保留 ${profile.candidate_place_record_count} 条地点记录；默认暂不显示 ${profile.source_status_review_hold_count} 条来源状态待复核记录，可勾选恢复。未确认其当前关闭。`;
  places=await fullLayer('places');render();tell('status','完整本地快照已读取；关联是候选，非核验入口');
 }catch(e){tell('status',e.message);$('map-placeholder').textContent='请先运行 scripts/build_stage1b.py；不会载入模拟景点。';return;}
 try{await loadLeaflet();$('map-placeholder').remove();map=L.map('map',{preferCanvas:true}).setView([1.3521,103.8198],11);
  tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
  tiles.on('tileerror',()=>tell('tiles','在线底图未加载；本地数据仍可浏览，可关闭底图。'));$('tiles').onchange=()=>{$('tiles').checked?tiles.addTo(map):map.removeLayer(tiles);};
  map.on('click',e=>{if(!$('pick').checked)return;if(originMarker)map.removeLayer(originMarker);originMarker=L.circleMarker(e.latlng,{radius:8,color:'#183d38',fillColor:'#fff',weight:3,fillOpacity:1}).addTo(map);$('origin').textContent=`纬度 ${e.latlng.lat.toFixed(6)}，经度 ${e.latlng.lng.toFixed(6)}；仅本页临时选择，未核验入口。`;$('pick').checked=false;});
  render();fit();document.querySelectorAll('[data-layer]').forEach(c=>c.onchange=()=>overlay(c.dataset.layer));
 }catch(e){tell('map',e.message);const p=$('map-placeholder');if(p)p.textContent='地图组件加载失败，真实数据列表仍可使用。运行 scripts/prepare_stage1_map.py 后刷新。';}
}
start();
