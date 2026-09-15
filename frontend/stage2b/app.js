'use strict';
const $=id=>document.getElementById(id),T=PlayMapTrip,trip=new T.ItineraryState();
const CATEGORY={nature:'自然与公园',tourism:'景点资料',heritage:'历史地点',monument:'古迹',food:'熟食中心'};
const MODE={walk:'步行',cycle:'骑行',drive:'驾车'};
// A device reading must never be described as a point the user identified.
const SOURCE_LABEL={catalogue_representative:'来源代表点',device_location:'设备定位（含误差）',
 onemap_search:'用户确认候选',user_map:'用户确认候选',user_coordinates:'用户确认候选'};
const STAYS={nature:[30,60,120],tourism:[30,60,90],heritage:[10,20,40],monument:[15,30,60],food:[20,40,60],unknown:[15,30,60]};
let all=[],profile=null,matched=[],listLimit=30,filterTimer=null,autoTimer=null,planController=null;
let selected=null,replacement=null,searchSequence=0,searchController=null,nextSearch=null,searchText='',detailSequence=0,formInvalid=false;
const mapView=new PlayMapTripMap({onFeature:f=>selectFeature(f),onMapPoint:p=>selectPlace(p,{})});
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function button(text,handler,cls='quiet'){const b=node('button',text,cls);b.type='button';b.onclick=handler;return b;}
function reportError(e){$('input-error').hidden=!e;$('input-error').textContent=e?e.message||String(e):'';}
async function api(url,body,signal){const opt={cache:'no-store',signal};if(body!==undefined){opt.method='POST';opt.headers={'Content-Type':'application/json','X-PlayMap-Client':url.startsWith('/api/stage2b/')?'stage2b':'stage2a'};opt.body=JSON.stringify(body);}const r=await fetch(url,opt);let d;try{d=await r.json();}catch{throw Error('本地服务未返回JSON，请检查启动入口。');}if(!r.ok){const x=d.detail,e=Error(typeof x==='string'?x:(x?.message||'请求失败')+(x?.code?' ['+x.code+']':''));e.code=x?.code;throw e;}return d;}
function tab(name){for(const x of ['local','search','map']){$(x==='local'?'local-pane':x+'-pane').hidden=x!==name;$('tab-'+x).classList.toggle('selected',x===name);}}
async function refreshStatus(){try{const r=await api('/api/stage2b/status');$('credential-status').textContent=r.credentials.configured?'Token已配置 · 实际有效性以服务响应为准':'Token尚未配置：运行 scripts/onemap_login.py';$('catalogue-status').textContent=r.catalogue.ready?`全岛 ${r.catalogue.entity_count} 组 · 默认浏览 ${r.catalogue.default_exploration_count} 组 · 非已核验景点数`:'目录不可读；搜索与地图选点仍可使用。';}catch(e){$('credential-status').textContent=e.message;}}
function invalidate(){
 clearTimeout(autoTimer);if(planController){planController.abort();planController=null;}$('cancel-calculation').disabled=true;
 $('export-report').disabled=true;$('export-status').textContent='';$('undo').disabled=!trip.history.length;
 mapView.setDraft(trip.data);
 if(trip.lastSuccess){mapView.markStale();$('result-body').classList.add('is-stale');$('plan-status').className='stale';$('plan-status').textContent='清单或条件已修改；灰色路线与淡化时间轴为上一份结果，不适用于当前输入。';$('plan-version').textContent='待重算 · 编辑版本 '+trip.revision;}
 else {$('plan-status').className='';$('plan-status').textContent='已更新清单，点击“计算整条行程”。';$('plan-version').textContent='编辑版本 '+trip.revision;}
 if(trip.lastSuccess&&$('auto-plan').checked&&!formInvalid){try{trip.validate();autoTimer=setTimeout(()=>calculate(false),550);}catch{}}
}
function edit(fn){try{fn();reportError(null);renderTrip();invalidate();}catch(e){reportError(e);}}
function beginReplacement(target){replacement=target;const label=target==='origin'?'出发点':target==='finish'?'结束位置':'第'+(trip.data.visits.findIndex(v=>v.visit_id===target)+1)+'个停留点';$('replacement-notice').hidden=false;$('replacement-notice').replaceChildren(node('p','现在选择新的'+label+'。其他条件与停留时间保留。'),button('取消替换',cancelReplacement));renderSelected();$('selected-place').scrollIntoView({block:'nearest'});}
function cancelReplacement(){replacement=null;$('replacement-notice').hidden=true;renderSelected();}
function selectPlace(point,meta){selected={point,meta};renderSelected();reportError(null);}
function renderSelected(){
 const box=$('selected-place');box.hidden=!selected;if(!selected)return;const {point:p,meta}=selected;box.replaceChildren(node('h3',p.label),node('p',meta.address||'没有详细地址','small'),node('p',`${p.latitude.toFixed(6)}, ${p.longitude.toFixed(6)} · ${SOURCE_LABEL[p.source]||'用户确认候选'}，不是已核验入口。`,'place-source'));
 if(meta.hold)box.append(node('p','旧址或状态待核查。请先搜索当前位置，不直接按旧坐标加入。','warning'));
 const actions=node('div',undefined,'place-actions');
 const use=target=>{if(meta.hold)return;try{
  if(p.source==='catalogue_representative'&&!window.confirm('使用这个来源代表坐标？它可能是公园/建筑内部点，并非已核验通行入口。'))return;
  if(target==='origin')trip.setOrigin(p);else if(target==='finish')trip.setFinish(p);else if(target==='add'){
   if(trip.data.visits.some(v=>v.point.latitude===p.latitude&&v.point.longitude===p.longitude)&&!window.confirm('清单中已有这个坐标。仍然添加一次重复停留？'))return;
   trip.addVisit(p);
  }else trip.replaceVisit(target,p);
  cancelReplacement();reportError(null);renderTrip();invalidate();mapView.setPick(false);
 }catch(e){reportError(e);}};
 if(!meta.hold){if(replacement)actions.append(button('用这个位置替换指定项',()=>use(replacement),'replace-action'));actions.append(button('设为出发点',()=>use('origin')),button('加入停留点',()=>use('add')),button('设为结束位置',()=>use('finish')));}
 const lookup=button('搜索实际通行位置',()=>{tab('search');$('search-query').value=meta.searchQuery||p.label;search(1);});actions.append(lookup,button('在地图上查看',()=>mapView.focusPoint(p)));box.append(actions);
 if(meta.category)box.append(node('p','类别：'+(CATEGORY[meta.category]||meta.category),'small'));
 if(meta.detail){for(const review of meta.detail.legacy_reviews||[])box.append(node('p',review.note_zh,'small'));box.append(node('p',`已收录常规时段证据 ${meta.detail.regular_hours_evidence?.length||0} 条；本阶段不据此自动判定营业。`,'small'));}
}
async function selectFeature(f){
 const p=f.properties,[lon,lat]=f.geometry.coordinates;
 const point={latitude:lat,longitude:lon,label:p.display_title.slice(0,200),source:'catalogue_representative',confirmed:true,entity_id:p.entity_id,catalogue_build_id:profile.build_id};
 const meta={category:p.category,address:p.address,hold:!p.default_exploration_visible};selectPlace(point,meta);
 const seq=++detailSequence;try{const d=await api('/api/stage1c/entity?entity_id='+encodeURIComponent(p.entity_id)+'&build_id='+encodeURIComponent(profile.build_id));if(seq!==detailSequence||selected?.point.entity_id!==p.entity_id)return;selected.meta.detail=d.entity;const legacy=d.entity.legacy_reviews?.find(r=>r.replacement?.address||r.replacement?.name);if(legacy)selected.meta.searchQuery=legacy.replacement.address||legacy.replacement.name;renderSelected();}catch(e){if(seq===detailSequence&&selected?.point.entity_id===p.entity_id)reportError(e);}
}
function renderCatalogue(){
 const terms=$('catalogue-query').value.normalize('NFKC').toLowerCase().trim().split(/\s+/).filter(Boolean),cat=$('category').value;
 matched=all.filter(f=>{const p=f.properties;return ($('include-holds').checked||p.default_exploration_visible)&&(!cat||p.categories.includes(cat))&&terms.every(t=>(p.search_text||'').includes(t));});
 $('list-count').textContent=`匹配 ${matched.length} 组，地图可显示全部匹配点。`;
 const box=$('catalogue-list');box.replaceChildren();for(const f of matched.slice(0,listLimit)){const p=f.properties,b=button(p.display_title,()=>selectFeature(f),'catalogue-item');b.append(node('small',p.categories.map(c=>CATEGORY[c]||c).join(' / ')+(p.default_exploration_visible?'':' · 待复核')));box.append(b);}
 if(!matched.length)box.append(node('p','本地目录未匹配，不代表现实中没有。可以使用OneMap搜索。','small'));
 $('catalogue-more').hidden=listLimit>=matched.length;mapView.setCatalogue(matched,$('show-pois').checked);
}
async function loadCatalogue(){try{profile=await api('/api/stage1c/status');let offset=0,expected=null,items=[];do{const d=await api('/api/stage1c/features?view=all&limit=1000&offset='+offset+'&build_id='+encodeURIComponent(profile.build_id));if(expected===null)expected=d.matched_total;if(expected!==d.matched_total||d.build_id!==profile.build_id)throw Error('目录版本改变，请刷新。');items.push(...d.features);if(d.next_offset!==null&&d.next_offset<=offset)throw Error('目录分页未前进。');offset=d.next_offset;}while(offset!==null);if(items.length!==expected)throw Error('全岛目录没有完整读取。');all=items;renderCatalogue();}catch(e){$('list-count').textContent=e.message;}}
function resetSearch(){searchSequence++;if(searchController)searchController.abort();searchController=null;nextSearch=null;$('search-more').hidden=true;$('search-results').replaceChildren();}
async function search(page=1){
 const query=$('search-query').value.trim();if(!query)return reportError(Error('请输入地名、建筑或邮编。'));
 if(searchController)searchController.abort();const controller=searchController=new AbortController(),seq=++searchSequence;searchText=query;nextSearch=null;$('search-more').hidden=true;$('search-results').replaceChildren(node('p','正在搜索…','small'));
 try{const d=await api('/api/stage2a/search',{query,page},controller.signal);if(seq!==searchSequence||query!==$('search-query').value.trim())return;const box=$('search-results');box.replaceChildren(node('p',`第 ${d.page}/${d.total_pages||1} 页 · 共 ${d.provider_found} 条匹配，未自动选第一条。`,'small'));for(const p of d.results){const b=button(p.label,()=>{detailSequence++;selectPlace({latitude:p.latitude,longitude:p.longitude,label:p.label.slice(0,200),source:'onemap_search',confirmed:true},{address:p.address});},'result');b.append(node('small',p.address));box.append(b);}if(!d.results.length)box.append(node('p','本页没有可用候选。请换关键词，或手动选择实际位置。','small'));nextSearch=d.next_page;$('search-more').hidden=nextSearch===null;}
 catch(e){if(e.name!=='AbortError'&&seq===searchSequence)$('search-results').replaceChildren(node('p',e.message,'error'));}
}
function stayPreview(v){
 if(v.stay_minutes!==null)return `用户设定 ${v.stay_minutes} 分钟`;
 const f=all.find(x=>x.properties.entity_id===v.point.entity_id),category=f?.properties.category||'unknown';const base=STAYS[category]||STAYS.unknown,k={quick:.5,regular:1,extended:1.5}[v.stay_profile];return `初始假设 ${Math.floor(base[1]*k)} 分钟，范围 ${Math.floor(base[0]*k)}—${Math.floor(base[2]*k)}；非实测，可修改。`;
}
function renderTrip(){
 const d=trip.data;$('origin-summary').textContent=d.origin?d.origin.label+' · '+(SOURCE_LABEL[d.origin.source]||'已确认选点')+'，非核验入口':'尚未选择';$('visit-count').textContent=`${d.visits.length} 个停留点 · 当前顺序不会被自动调整`;
 const box=$('visits');box.replaceChildren();d.visits.forEach((v,i)=>{
  const card=node('div',undefined,'visit-card');card.dataset.visitId=v.visit_id;const head=node('div',undefined,'visit-head');head.append(node('span',String(i+1),'badge'),node('strong',v.point.label));card.append(head);
  const actions=node('div',undefined,'visit-actions'),up=button('上移',()=>edit(()=>trip.moveVisit(v.visit_id,-1))),down=button('下移',()=>edit(()=>trip.moveVisit(v.visit_id,1)));up.disabled=i===0;down.disabled=i===d.visits.length-1;actions.append(up,down,button('替换',()=>beginReplacement(v.visit_id)),button('删除',()=>edit(()=>{trip.removeVisit(v.visit_id);if(replacement===v.visit_id)cancelReplacement();})),button('定位',()=>mapView.focusPoint(v.point)));card.append(actions);
  const fields=node('div',undefined,'visit-fields'),left=node('div'),right=node('div'),label=node('label','停留分钟（空＝建议）'),input=node('input');input.type='number';input.min='0';input.max='1440';input.step='1';input.value=v.stay_minutes??'';input.placeholder='使用初始建议';input.setAttribute('aria-label','第'+(i+1)+'站停留分钟');label.append(input);left.append(label);
  const sel=node('select');sel.setAttribute('aria-label','第'+(i+1)+'站建议档位');for(const [key,text] of [['quick','短停'],['regular','普通'],['extended','深入']]){const o=node('option',text);o.value=key;sel.append(o);}sel.value=v.stay_profile;const label2=node('label','建议档位（仅留空时）');label2.append(sel);right.append(label2);fields.append(left,right);card.append(fields,node('p',stayPreview(v),'visit-note'));
  const apply=()=>{try{const n=T.integer(input.value,'停留分钟',{min:0,max:1440,nullable:true});formInvalid=false;edit(()=>trip.setStay(v.visit_id,n,sel.value));}catch(e){formInvalid=true;trip.cancel();invalidate();reportError(e);input.classList.add('input-error-border');}};input.onchange=sel.onchange=apply;box.append(card);
 });
 $('finish-policy').value=d.finish_policy;$('finish-panel').hidden=d.finish_policy!=='custom';$('finish-summary').textContent=d.finish?d.finish.label:'尚未选择结束位置';$('mode').value=d.mode;$('undo').disabled=!trip.history.length;
}
function timeInputs(){const t=trip.data.time;$('time-mode').value=t.mode;$('budget-minutes').value=t.budget_minutes??'';$('departure').value=t.departure_at?t.departure_at.slice(0,16):'';$('finish-by').value=t.finish_by?t.finish_by.slice(0,16):'';$('buffer-minutes').value=t.buffer_minutes;timeVisibility();}
function timeVisibility(){const m=$('time-mode').value;$('budget-row').hidden=m!=='budget';$('deadline-row').hidden=m!=='window';}
function readTime(){
 const mode=$('time-mode').value,t={mode,departure_at:T.singaporeISO($('departure').value),finish_by:mode==='window'?T.singaporeISO($('finish-by').value):null,budget_minutes:mode==='budget'?T.integer($('budget-minutes').value,'总时长预算',{min:1,max:10080}):null,buffer_minutes:T.integer($('buffer-minutes').value,'整体预留',{min:0,max:1440})};
 if(mode==='window'&&(!t.departure_at||!t.finish_by||Date.parse(t.finish_by)<=Date.parse(t.departure_at)))throw Error('请填写完整的起止日期与时间；结束必须较晚，跨午夜须显式选下一天。');return t;
}
function timeChanged(){timeVisibility();try{const t=readTime();formInvalid=false;trip.setTime(t);reportError(null);invalidate();}catch(e){formInvalid=true;trip.cancel();invalidate();reportError(e);}}
function cancelCalculation(){clearTimeout(autoTimer);trip.cancel();if(planController)planController.abort();planController=null;$('cancel-calculation').disabled=true;if(trip.lastSuccess){mapView.markStale();$('result-body').classList.add('is-stale');}$('plan-status').textContent='已取消等待；不再请求后续路段。已发出的一个服务商请求可能仍在结束处理。';$('plan-status').className='stale';$('export-report').disabled=true;}
async function calculate(force){
 clearTimeout(autoTimer);let ticket;
 try{const t=readTime();for(const card of $('visits').querySelectorAll('.visit-card')){const v=trip.data.visits.find(v=>v.visit_id===card.dataset.visitId),n=T.integer(card.querySelector('input').value,'停留分钟',{min:0,max:1440,nullable:true}),p=card.querySelector('select').value;if(v&&(v.stay_minutes!==n||v.stay_profile!==p))trip.setStay(v.visit_id,n,p);}if(JSON.stringify(t)!==JSON.stringify(trip.data.time))trip.setTime(t);formInvalid=false;ticket=trip.begin(force);reportError(null);}catch(e){formInvalid=true;reportError(e);return;}
 if(planController)planController.abort();const controller=planController=new AbortController(),viewRevision=mapView.viewRevision;
 $('cancel-calculation').disabled=false;$('export-report').disabled=true;
 if(trip.lastSuccess){mapView.markStale();$('result-body').classList.add('is-stale');}
 const n=trip.data.visits.length+(trip.data.finish_policy==='last_stop'?0:1);$('plan-status').className='';$('plan-status').textContent=`正在按顺序请求或复用 ${n} 段路径。可以继续编辑；旧请求不会覆盖新条件。`;$('plan-version').textContent='计算中 · 版本 '+ticket.revision;
 try{const r=await api('/api/stage2b/plan',ticket.payload,controller.signal);if(!trip.accepts(ticket,r))return;
  mapView.setPlan(r,mapView.viewRevision===viewRevision);if(!trip.save(ticket,r))return;renderResult(r);$('export-report').disabled=false;
 }catch(e){if(e.name==='AbortError'||ticket.sequence!==trip.sequence)return;$('plan-status').textContent=e.message+(trip.lastSuccess?' 上一份路线与时间轴仅供对照，不是本次结果。':' 没有生成完整方案。');$('plan-status').className='error';$('plan-version').textContent='本次未完成';}
 finally{if(planController===controller){planController=null;$('cancel-calculation').disabled=true;}}
}
function renderResult(r){
 $('result-body').hidden=false;$('result-body').classList.remove('is-stale');const x=r.totals;
 $('travel-total').textContent=T.duration(x.travel_s);$('stay-total').textContent=T.duration(x.stay_s);$('buffer-total').textContent=T.duration(x.buffer_s);$('overall-total').textContent=T.duration(x.reference_duration_s);
 $('time-range').textContent='参考总时长 '+T.duration(x.reference_duration_s)+' · 停留假设范围 '+T.duration(x.duration_range_s[0])+'—'+T.duration(x.duration_range_s[1]);
 const labels={no_budget:'未提供时间预算：以上是这套玩法所需时长的建议，不是在推断你的空闲时间。',all_estimates_exceed:'当前顺序与停留范围均超过时间预算。未自动删地点或缩短停留。',reference_exceeds:'参考安排超过预算，但较短停留假设可能适合。请自行修改，不自动压缩。',reference_fits_upper_exceeds:'参考安排在预算内，但较长停留假设会超时。',all_estimates_fit:'按照当前通行及停留估计，参考值和停留范围均未超预算。'};
 const check=r.budget_check;$('budget-check').textContent=labels[check.status]+(check.slack_s===null?'':` 参考${check.slack_s>=0?'剩余':'超出'} ${T.minutes(Math.abs(check.slack_s))}。`);$('budget-check').className=check.status==='no_budget'||check.status==='all_estimates_fit'?'budget-ok':'budget-warning';
 $('journey-summary').textContent=`${r.visits.length} 处停留 · ${r.legs.length} 段连接 · 服务商累计 ${(x.distance_m/1000).toFixed(2)} km · ${MODE[r.mode]} · ${r.route_calls.provider_calls} 次新路径请求 / ${r.route_calls.cache_hits} 段短时复用。`;
 $('clock-note').textContent=(r.time.departure_at?'以下为新加坡时间 UTC+8。':'未指定出发时刻：以下 +HH:MM 表示从出发开始的相对时间。')+' 出发时刻仅排时间轴；未预测未来交通，也未核验开放时间。';
 $('plan-version').textContent='当前方案版本 '+r.revision;
 const flagged=r.legs.some(l=>l.endpoint_review_required||l.diagnostics.large_detour_flag||l.diagnostics.geometry_summary_mismatch);
 $('plan-status').className=flagged?'warning':'';$('plan-status').textContent=flagged?'完整分段计算已完成，但有端点偏移或绕行等提示。时间结果仍是条件性估计。':'已按你的顺序计算通行、停留与总时间。时间预算检查不代表营业或入口已核验。';
 const panel=$('timeline');panel.replaceChildren();for(const row of r.timeline){
  const el=node('div',undefined,'timeline-row '+row.kind);el.tabIndex=0;el.setAttribute('role','button');
  const time=node('div',undefined,'time-label');time.append(node('span',T.axisLabel(row)),node('span','↓ '+T.axisLabel(row,true)));const content=node('div',undefined,'timeline-content');
  content.append(node('span',row.kind==='travel'?'通行 · '+MODE[r.mode]:row.kind==='visit'?'停留 '+(row.visit_index+1):'整体预留','timeline-type'),node('strong',row.title),node('p',T.minutes(row.duration_s),'small'));
  if(row.kind==='travel'){const leg=r.legs[row.leg_index];content.append(node('p',leg.kind==='same_selected_coordinate'?'相同选定坐标：未生成连接线，按0转移时间。':`${(leg.distance_m/1000).toFixed(2)} km · ${leg.reused_in_memory?'短时复用':'本次请求'} · 端点偏移 ${leg.endpoint_offsets_m.origin.toFixed(0)} / ${leg.endpoint_offsets_m.destination.toFixed(0)} m`,'small'));if(leg.endpoint_review_required||leg.diagnostics.large_detour_flag)content.append(node('p','请核对这一段：端点偏移或明显绕行；没有自动换方式。','leg-warning'));}
  if(row.kind==='visit'){const v=r.visits[row.visit_index];content.append(node('p',(v.stay_basis==='user_specified'?'用户指定停留':'项目初始停留假设')+` · 范围 ${v.stay_range_minutes.join('—')} 分钟 · 开放条件未核验`,'small'));}
  const focus=()=>{for(const e of panel.querySelectorAll('.is-focus'))e.classList.remove('is-focus');el.classList.add('is-focus');if(row.kind==='travel')mapView.focusLeg(row.leg_index);else if(row.kind==='visit')mapView.focusPoint(r.visits[row.visit_index].point);};el.onclick=focus;el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();focus();}};el.append(time,content);panel.append(el);
 }
 $('plan-warnings').replaceChildren(...r.cautions.map(t=>node('p',t,'small')));
}
function exportReport(){if(!trip.lastSuccess||trip.lastSuccess.revision!==trip.revision||formInvalid)return reportError(Error('请先取得当前输入下的完整方案。'));const report=T.diagnostic(trip.lastSuccess,trip.revision);const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),link=node('a');link.href=url;link.download='stage2b_itinerary_report.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);$('export-status').textContent='已下载到本机；不含精确位置、地点名称、具体时刻或Token，未自动上传。';}
/* Geolocation is OFFERED, never applied. The reading is drawn with its error
   radius first; it becomes an origin only when the user presses the button.
   confirmed:true is therefore still the user's act, not the device's. */
let deviceReading=null,deviceResolve=null;
function closeLocate(adopted){
 $('locate-card').hidden=true;$('locate-card').replaceChildren();deviceReading=null;
 const resolve=deviceResolve;deviceResolve=null;if(resolve)resolve(!!adopted);
}
function adoptDevice(){
 const r=deviceReading;if(!r)return closeLocate(false);
 try{
  trip.setOrigin({latitude:r.latitude,longitude:r.longitude,label:'当前位置（设备定位）',
   source:'device_location',accuracy_m:r.accuracy_m,confirmed:true});
  reportError(null);renderTrip();invalidate();closeLocate(true);
 }catch(e){reportError(e);closeLocate(false);}
}
function renderLocateCard(reading){
 deviceReading=reading;
 const card=$('locate-card');card.hidden=false;card.replaceChildren();
 card.append(node('p','检测到你的位置。'+PlayMapLocate.describe(reading),'small'));
 if(reading.coarse)card.append(node('p','误差偏大：地图上的蓝圈就是可能的范围。用作出发点会让时间估计同样不准，而这个误差不会计入任何估算。','small warning'));
 const row=node('div',undefined,'place-actions');
 row.append(button('用这里作为出发点',adoptDevice,'primary'),button('不用，我自己选',()=>closeLocate(false)));
 card.append(row,node('p','设备定位不是已核验入口，也不证明你此刻确实位于该点。','small'));
}
async function locateDevice(){
 $('locate-status').textContent='正在向浏览器请求定位…可能会弹出权限提示。';
 try{
  const reading=await PlayMapLocate.read();
  $('locate-status').textContent='';
  mapView.showDevice(reading);renderLocateCard(reading);
 }catch(e){
  // Five distinct causes, five honest messages; none of them change the itinerary.
  $('locate-status').textContent=e.message;mapView.clearDevice();closeLocate(false);
 }
}
/* Chat calls this before sending when no origin is set. It resolves only after
   the user answers, and resolves false on every failure path. */
function proposeDeviceOrigin(){
 if(trip.data.origin)return Promise.resolve(false);
 return new Promise(resolve=>{deviceResolve=resolve;locateDevice();});
}
function wire(){
 $('locate-me').onclick=()=>locateDevice();
 for(const t of ['local','search','map'])$('tab-'+t).onclick=()=>tab(t);
 $('search-form').onsubmit=e=>{e.preventDefault();search(1);};$('search-query').oninput=resetSearch;$('search-more').onclick=()=>{if(nextSearch)search(nextSearch);};
 $('pick-map').onclick=()=>{if(!mapView.map)return reportError(Error('地图未就绪，可以使用搜索或输入坐标。'));mapView.setPick(true);$('map').scrollIntoView({block:'nearest'});};$('cancel-pick').onclick=()=>mapView.setPick(false);document.addEventListener('keydown',e=>{if(e.key==='Escape')mapView.setPick(false);});
 $('coordinate-form').onsubmit=e=>{e.preventDefault();try{const latitude=Number($('manual-lat').value),longitude=Number($('manual-lon').value);if(!$('manual-lat').value||!$('manual-lon').value||!Number.isFinite(latitude)||!Number.isFinite(longitude)||latitude<.9||latitude>1.7||longitude<103.4||longitude>104.7)throw Error('请核对新加坡范围内的纬度与经度。');selectPlace({latitude,longitude,label:'用户输入坐标',source:'user_coordinates',confirmed:true},{});reportError(null);}catch(e){reportError(e);}};
 $('catalogue-query').oninput=()=>{clearTimeout(filterTimer);filterTimer=setTimeout(()=>{listLimit=30;renderCatalogue();},150);};$('category').onchange=$('include-holds').onchange=()=>{listLimit=30;renderCatalogue();};$('show-pois').onchange=renderCatalogue;$('catalogue-more').onclick=()=>{listLimit+=30;renderCatalogue();};
 $('finish-policy').onchange=()=>edit(()=>trip.setFinishPolicy($('finish-policy').value));$('mode').onchange=()=>edit(()=>trip.setMode($('mode').value));
 for(const id of ['time-mode','budget-minutes','departure','finish-by','buffer-minutes'])$(id).onchange=timeChanged;
 $('replace-origin').onclick=()=>beginReplacement('origin');$('replace-finish').onclick=()=>beginReplacement('finish');
 $('undo').onclick=()=>{if(!trip.undo())return;formInvalid=false;cancelReplacement();timeInputs();renderTrip();invalidate();reportError(null);};
 $('calculate').onclick=()=>calculate(false);$('fresh-routes').onclick=()=>calculate(true);$('cancel-calculation').onclick=cancelCalculation;
 $('auto-plan').onchange=()=>{clearTimeout(autoTimer);if($('auto-plan').checked&&trip.lastSuccess&&trip.lastSuccess.revision!==trip.revision&&!formInvalid)autoTimer=setTimeout(()=>calculate(false),550);};
 $('clear-trip').onclick=async()=>{if(!window.confirm('清空当前页行程？不会删除地点数据库、Token或原始数据。'))return;cancelCalculation();trip.clear();formInvalid=false;cancelReplacement();selected=null;renderSelected();timeInputs();renderTrip();mapView.clearPlan();mapView.setDraft(trip.data);$('result-body').hidden=true;$('plan-status').textContent='本页行程已清空；全岛数据保留。';$('plan-status').className='';$('plan-version').textContent='尚未计算';$('export-report').disabled=true;try{await api('/api/stage2b/clear-memory',{session_id:trip.sessionId});}catch{}}
 $('fit').onclick=()=>mapView.fitNation();$('fit-route').onclick=()=>mapView.fitRoute();$('fit-stops').onclick=()=>mapView.fitDraft();$('refresh-status').onclick=refreshStatus;$('export-report').onclick=exportReport;
}
wire();renderTrip();timeInputs();refreshStatus();loadCatalogue();mapView.init();
