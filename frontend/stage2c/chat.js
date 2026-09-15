/* Natural-language proposals + confirmed changes, reusing Stage2B calculations. */
(function(){'use strict';
 const el=id=>document.getElementById(id),bridge=PlayMapChatBridge,clone=x=>JSON.parse(JSON.stringify(x));
 const gate=new PlayMapChatState.Gate();
 let controller=null,proposal=null,pending=null,preferences={preferred:[],excluded:[],notes:[]},prefHistory=[],applying=false,originAsked=false;
 const metrics={completed:0,failed:0,calls:0,applied:0,cancelled:0,tokens:{},model:null,error:null,lastFailure:null,failedAttempts:0,failedUnknown:0,lastAdaptation:null};
 const names={park:'公园',food:'吃饭',museum:'博物馆',shopping:'购物',heritage:'历史文化',architecture:'建筑',art:'艺术',nature:'自然',photography:'拍照'};
 function make(tag,text,cls){const x=document.createElement(tag);if(text!==undefined)x.textContent=text;if(cls)x.className=cls;return x;}
 function button(text,fn){const b=make('button',text,'quiet');b.type='button';b.onclick=fn;return b;}
 function status(text,cls=''){el('chat-status').textContent=text;el('chat-status').className=cls;}
 const log=new PlayMapChatState.ConversationLog();
 function renderLog(){
  const box=el('chat-log'),past=!log.isViewingActive();
  box.replaceChildren(...log.viewing().messages.map(m=>{
   const p=make('div',undefined,'chat-message '+m.role);
   p.append(make('strong',m.role==='user'?'你':'PlayMap'),make('p',m.text));return p;}));
  // A past thread is a transcript, not a restorable plan: hide the composer
  // rather than let a send land in a conversation the user is only reading.
  el('chat-history-banner').hidden=!past;el('chat-form').hidden=past;
  if(!past)box.scrollTop=box.scrollHeight;
 }
 function renderHistory(){
  const box=el('chat-history');box.replaceChildren();
  for(const t of log.list()){
   const b=make('button',t.title,'chat-thread'+(t.viewing?' selected':''));
   b.type='button';b.append(make('small',t.count+' 条'+(t.active?' · 当前':'')));
   b.onclick=()=>{if(t.active)log.resume();else log.view(t.id);renderLog();renderHistory();};
   box.append(b);
  }
 }
 function message(role,text){log.add(role,text);renderLog();renderHistory();}
 function showPrefs(){const p=preferences;el('chat-preferences').textContent='已采用需求：'+(p.preferred.length?'偏好 '+p.preferred.map(x=>names[x]||x).join('、')+'；':'')+(p.excluded.length?'排除 '+p.excluded.map(x=>names[x]||x).join('、')+'；':'')+(p.notes.length?'待核验 '+p.notes.join('；'):'')+(!p.preferred.length&&!p.excluded.length&&!p.notes.length?'尚无额外偏好':'');}
 async function request(path,body,signal){const r=await fetch('/api/stage2c/'+path,{method:'POST',headers:{'Content-Type':'application/json','X-PlayMap-Client':'stage2c'},body:JSON.stringify(body),signal,cache:'no-store'});let d;try{d=await r.json();}catch{throw Error('本地服务没有返回JSON，请确认新启动入口。');}if(!r.ok){const e=Error(d.detail?.message||'本次请求失败');e.code=d.detail?.code||'REQUEST_FAILED';e.safeDetail=PlayMapChatState.safeErrorDetail(d.detail);throw e;}return d;}
 function cancel(text='已取消等待。已发出的API请求可能仍会计入额度。'){
  if(controller){controller.abort();controller=null;metrics.cancelled++;}gate.invalidate();proposal=null;
  el('chat-send').disabled=false;el('chat-cancel').disabled=true;el('chat-apply').disabled=true;
  if(text)status(text,'warning');
 }
 window.addEventListener('playmap:edit',event=>{
  const kind=event.detail.kind;
  if(kind==='change'){prefHistory.push(clone(preferences));if(prefHistory.length>20)prefHistory.shift();}
  else if(kind==='undo')preferences=prefHistory.pop()||{preferred:[],excluded:[],notes:[]};
  else if(kind==='clear'){preferences={preferred:[],excluded:[],notes:[]};prefHistory=[];pending=null;}
  showPrefs();
  if(!applying&&(proposal||controller)){pending=null;cancel('地图或清单已经修改，旧聊天草案已失效。按当前行程重新发送即可。');el('chat-proposal').hidden=true;}
 });
 function selections(){const d={};for(const s of el('chat-slots').querySelectorAll('select'))if(s.value)d[s.dataset.slot]=s.value;return d;}
 function chosenEnough(){return !!proposal&&PlayMapChatState.proposalDiagnostic(proposal,selections()).all_slots_selected;}
 function updateReadiness(){
  if(!proposal)return;
  metrics.lastProposal=PlayMapChatState.proposalDiagnostic(proposal,selections());
  const area=el('chat-readiness');
  area.textContent=PlayMapChatState.readinessText(proposal,selections());
  area.className=metrics.lastProposal.unresolved_slot_count?'warning small':'small';
  el('chat-apply').disabled=!chosenEnough();
 }
 function showProposal(r){
  proposal=r;el('chat-proposal').hidden=false;const slots=el('chat-slots');slots.replaceChildren();
  for(const s of r.slots){
   const box=make('div',undefined,'chat-slot');
   const title={set_origin:'确认起点',set_finish:'确认结束位置',add_visit:'建议新增停留',replace_visit:'替换停留'}[s.operation];
   box.append(make('strong',title+(s.query?'：'+s.query:'')));
   const select=make('select');select.dataset.slot=s.slot_id;select.setAttribute('aria-label',title+'候选');
   const placeholder=make('option',s.options.length?'请选择具体位置':'此项尚无匹配候选（不是API失败）');placeholder.value='';select.append(placeholder);
   for(const o of s.options){const option=make('option',o.label+(o.point.source==='catalogue_representative'?' · 目录代表点':' · OneMap'));option.value=o.candidate_key;select.append(option);}
   select.value=s.selected_key||'';select.disabled=!s.options.length;box.append(select);
   const detail=make('p',undefined,'small');box.append(detail);
   const update=()=>{const o=s.options.find(x=>x.candidate_key===select.value);detail.textContent=o?(o.address||'未收录地址')+'。'+o.basis+(o.distance_hint_m===null?'':`；距当前参考起点直线约 ${(o.distance_hint_m/1000).toFixed(2)} km（不是实际路程）`):(s.options.length?'需要你确认具体位置，不自动使用搜索第一条。':'本项尚未找到可选地点，不能作为已完成项采用。下方列出检索范围与条件提示。');updateReadiness();};
   select.onchange=update;
   box.append(button('在地图查看此候选',()=>{const o=s.options.find(x=>x.candidate_key===select.value);if(o)bridge.focus(o.point);else status('先选择一个候选位置。','warning');}));
   if(s.reason)box.append(make('p','模型建议理由（待核对）：'+s.reason,'small'));
   for(const n of s.notices)box.append(make('p',n,'small'));
   slots.append(box);update();
  }
  el('chat-changes').replaceChildren(...r.preview.changes.map(t=>make('p',t,'small')));
  el('chat-warnings').replaceChildren(...[...r.questions,...r.preview.warnings].map(t=>make('p',t,'small')));
  const prefs=r.preview.preferences;
  el('chat-proposed-prefs').textContent='草案偏好：'+prefs.preferred.map(x=>names[x]||x).join('、')+'；排除：'+(prefs.excluded.map(x=>names[x]||x).join('、')||'无')+'。尚未证明所有偏好满足。';
  updateReadiness();
  if(!r.interpretation.actions.length&&JSON.stringify(prefs)===JSON.stringify(preferences))el('chat-apply').disabled=true;
  const empty=metrics.lastProposal.empty_slot_count;
  status(empty?`草案尚未完整 · ${empty}项没有候选；模型请求已成功，原行程未修改。`:`草案已生成 · 本轮 ${r.usage.generation_requests} 次模型请求。先核对地点和修改，再采用。`,empty?'warning':'');
 }
 async function send(event){event.preventDefault();
  const text=el('chat-input').value.trim();if(!text)return;
  if(!el('chat-consent').checked){status('请先阅读并勾选发送说明；测试时只使用公开地点与虚构需求。','warning');return;}
  // Ask BEFORE the model call: candidates are ranked against the origin, so an
  // origin adopted afterwards would not re-anchor this turn's retrieval. Asked at
  // most once per conversation, and declining is a normal answer.
  if(!bridge.hasOrigin()&&!originAsked){
   originAsked=true;el('chat-send').disabled=true;
   status('还没有出发点。请在地图上方回答是否使用当前位置；选「不用」则按全岛范围推荐。');
   try{await bridge.proposeDeviceOrigin();}catch{}
   el('chat-send').disabled=false;
   if(controller)return;
  }
  let snap;try{snap=bridge.snapshot();}catch(e){status(e.message,'error');return;}
  if(controller)return;
  const ticket=gate.begin(snap.revision),local=new AbortController();controller=local;proposal=null;
  el('chat-send').disabled=true;el('chat-cancel').disabled=false;el('chat-proposal').hidden=true;el('chat-apply').disabled=true;
  message('user',text);status('理解需求并检索真实地点…最多两次模型请求；不自动重试或换模型。');
  const pendingAtStart=pending;
  try{
   const r=await request('chat',{...snap,message:text,preferences,pending:pendingAtStart,consent:true},local.signal);
   if(!gate.accepts(ticket,bridge.currentRevision(),r))return;
   metrics.completed++;metrics.calls+=r.usage.generation_requests;metrics.model=r.model;metrics.error=null;metrics.lastAdaptation=r.operation_adaptation||null;
   for(const call of r.usage.calls)for(const [k,v] of Object.entries(call.usage||{}))metrics.tokens[k]=(metrics.tokens[k]||0)+v;
   pending={user_message:((pendingAtStart?.user_message?pendingAtStart.user_message+'\n':'')+text).slice(-3000),interpretation:r.interpretation};
   message('assistant',r.acknowledgement||'已生成可核对的修改草案。');showProposal(r);el('chat-input').value='';
  }catch(e){if(e.name==='AbortError')return;
   if(!gate.accepts(ticket,bridge.currentRevision(),{base_revision:ticket.revision}))return;
   metrics.failed++;metrics.error=e.code||'NETWORK_OR_CLIENT';metrics.lastFailure=e.safeDetail||null;
   PlayMapChatState.recordFailure(metrics,e.safeDetail);
   const hint=PlayMapChatState.errorSummary(e.safeDetail);
   status(e.message+(e.code?' ['+e.code+']':'')+(hint?'；'+hint:'')+'；原行程未修改。','error');}
  finally{if(controller===local){controller=null;el('chat-send').disabled=false;el('chat-cancel').disabled=true;}}
 }
 async function adopt(){
  if(!proposal||!chosenEnough())return;const r=proposal,rev=bridge.currentRevision();
  if(r.base_revision!==rev){cancel('草案已过期，请重新发送。');return;}
  const local=new AbortController();controller=local;el('chat-apply').disabled=true;el('chat-send').disabled=true;el('chat-cancel').disabled=false;
  status('校验所选候选并更新行程…这一步不调用Gemini。');
  try{
   const resolved=await request('resolve',{ticket:r.ticket,session_id:bridge.currentSession(),revision:rev,choices:selections()},local.signal);
   if(proposal!==r||bridge.currentRevision()!==rev||controller!==local)return;
   applying=true;try{bridge.apply(resolved.draft,rev);preferences=resolved.preferences;}finally{applying=false;}
   showPrefs();metrics.applied++;pending=null;proposal=null;el('chat-proposal').hidden=true;
   message('assistant','已应用所确认的草案。'+(resolved.ready_to_calculate?'下面使用路径服务计算通行、停留与总时间。':'还缺少起点、停留点或终点信息；可以继续对话或手动补充。'));
   status('草案已采用，地图与行程清单已同步。');
   controller=null;el('chat-cancel').disabled=true;el('chat-send').disabled=false;
   if(resolved.ready_to_calculate){const appliedRevision=bridge.currentRevision();await bridge.calculate();if(bridge.currentRevision()!==appliedRevision)return;const plan=bridge.activePlan();if(plan)message('assistant','计算完成：'+plan.visits.length+'处停留，参考总时长 '+PlayMapTrip.duration(plan.totals.reference_duration_s)+'。预算结论与时间范围请查看右侧行程总览；开放条件尚未校验。');else message('assistant','本次尚未获得当前版本的完整路线；请查看行程总览中的原因，草案仍可编辑。');}
  }catch(e){if(e.name==='AbortError')return;metrics.failed++;metrics.error=e.code||'APPLY_FAILED';metrics.lastFailure=e.safeDetail||PlayMapChatState.safeErrorDetail({request_diagnostic:{phase:'resolve_validation',wire_version:PlayMapChatState.PATCH,generation_attempts:0}});PlayMapChatState.recordFailure(metrics,{generation_attempts:0});status(e.message+(e.code?' ['+e.code+']':''),'error');}
  finally{if(controller===local){controller=null;el('chat-send').disabled=false;el('chat-cancel').disabled=true;el('chat-apply').disabled=!chosenEnough();}}
 }
 async function refresh(){try{const r=await fetch('/api/stage2c/status',{cache:'no-store'}).then(x=>x.json());el('chat-model').textContent=r.configured?'Gemini · '+r.model+' · CHAT FIX 5 · 按发送调用':'模型尚未配置：'+(r.error?.message||'请检查.env');if(r.model)metrics.model=r.model;}catch{el('chat-model').textContent='无法读取模型状态，请确认使用stage2c_main入口。';}}
 function download(){const d=PlayMapChatState.diagnose(metrics,bridge.diagnostic(),proposal,selections()),url=URL.createObjectURL(new Blob([JSON.stringify(d,null,2)],{type:'application/json'})),a=make('a');a.href=url;a.download='stage2c1_chat_report.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);status('已导出无原话、地点名、坐标或Key的本地报告；失败请求可能已消耗额度，精确用量以Google控制台为准。');}
 el('chat-form').onsubmit=send;el('chat-apply').onclick=adopt;el('chat-cancel').onclick=()=>cancel();el('chat-export').onclick=download;
 // Starting a new thread only parks the transcript; the adopted itinerary stays.
 el('chat-new').onclick=()=>{cancel(null);pending=null;log.start();renderLog();renderHistory();status('已开始新对话；地图上已采用的行程保留。');};
 el('chat-resume').onclick=()=>{log.resume();renderLog();renderHistory();};
 el('chat-reset').onclick=async()=>{if(!window.confirm('清除本页对话、偏好和待采用草案？地图中的已采用行程保留；此操作不删除Google已收到的内容。'))return;cancel(null);preferences={preferred:[],excluded:[],notes:[]};prefHistory=[];pending=null;proposal=null;originAsked=false;el('chat-proposal').hidden=true;log.clear();renderLog();renderHistory();showPrefs();try{await request('forget',{session_id:bridge.currentSession()});}catch{}status('本地对话与待采用草案已清理；已有行程保留。');};
 showPrefs();renderHistory();refresh();
})();
