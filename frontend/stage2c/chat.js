/* Natural-language proposals + confirmed changes, reusing Stage2B calculations. */
(function(){'use strict';
 const el=id=>document.getElementById(id),bridge=PlayMapChatBridge,clone=x=>JSON.parse(JSON.stringify(x));
 const gate=new PlayMapChatState.Gate();
 let controller=null,proposal=null,pending=null,verificationRef=null,verificationSequence=0,verificationPromiseRef=null,verificationPromise=null,preferences={preferred:[],excluded:[],notes:[],active_constraints:[]},prefHistory=[],applying=false,originAsked=false;
 const metrics={completed:0,failed:0,calls:0,applied:0,cancelled:0,tokens:{},model:null,error:null,lastFailure:null,failedAttempts:0,failedUnknown:0,lastAdaptation:null};
 const names={park:'公园',food:'吃饭',museum:'博物馆',shopping:'购物',heritage:'历史文化',architecture:'建筑',art:'艺术',nature:'自然',photography:'拍照'};
 const requirementNames={place_type:'地点类型',max_walk_minutes:'每段步行上限',takeaway:'外带',excluded_topic:'排除主题',theme:'游玩主题',drink:'饮品',pace:'节奏',environment:'环境',walking_effort:'步行强度',duration:'游玩时长',budget_minutes:'总时间预算',departure_at:'出发时间',finish_by:'结束时间',stay_minutes:'停留时间',transport_mode:'交通方式',travel_mode:'交通方式',finish_policy:'结束方式',opening_hours:'营业要求',must_be_open:'必须营业',last_admission:'最晚入场',wheelchair_access:'无障碍要求',radius_m:'直线范围',distance_m:'距离要求',spatial_reference:'空间参照',area:'活动区域',time_window:'活动时段'};
 const requirementValues={...names,cafe:'咖啡馆',restaurant:'餐厅',coffee:'咖啡',tea:'茶',indoor:'室内',outdoor:'室外',relaxed:'轻松',slow:'慢节奏',leisurely:'轻松',family:'亲子',family_friendly:'亲子',child_friendly:'亲子',less:'少走路',low:'较少',one_day:'一天',walk:'步行',drive:'驾车',cycle:'骑行',public_transport:'公共交通',no_stairs:'无台阶',last_stop:'最后一站结束',return_to_start:'返回起点'};
 function make(tag,text,cls){const x=document.createElement(tag);if(text!==undefined)x.textContent=text;if(cls)x.className=cls;return x;}
 function button(text,fn){const b=make('button',text,'quiet');b.type='button';b.onclick=fn;return b;}
 function status(text,cls=''){el('chat-status').textContent=text;el('chat-status').className=cls;}
 const log=new PlayMapChatState.ConversationLog();
 function renderLog(){
  const box=el('chat-log');
  box.replaceChildren(...log.viewing().messages.map(m=>
   make('div',m.text,'chat-message '+m.role)));
  el('chat-history-banner').hidden=true;el('chat-form').hidden=false;
  box.scrollTop=box.scrollHeight;
 }
 function renderHistory(){
  const box=el('chat-history');box.replaceChildren();
  for(const t of log.list()){
   const b=make('button',t.title,'chat-thread'+(t.viewing?' selected':''));
   b.type='button';b.append(make('small',t.count+' 条'));
   // Opening a past thread resumes it; the user can keep talking in it.
   b.onclick=()=>{log.activate(t.id);renderLog();renderHistory();status('');};
   box.append(b);
  }
 }
 function message(role,text){
  log.add(role,text);renderLog();renderHistory();
  // A new line is the reply the user is waiting for: never leave it behind a
  // card they closed or folded earlier.
  const card=document.querySelector('.ov-thread');
  if(card){card.hidden=false;card.classList.remove('is-collapsed');
   const b=el('thread-reopen');if(b)b.hidden=true;
   const c=el('thread-collapse');if(c){c.textContent='▽';c.setAttribute('aria-expanded','true');}}
 }
 function scopeText(scope,draft){
  if(scope==='global')return '整个行程';
  if(scope==='future_visits')return '仅后续新增';
  if(scope==='origin')return '出发点';
  if(scope==='finish')return '结束位置';
  if(scope==='visit:new')return '本轮新增站点';
  const next=/^visit:new:(\d+)$/.exec(scope);
  if(next)return '本轮新增第'+next[1]+'站';
  const id=/^visit_id:(.+)$/.exec(scope),ordinal=/^visit:(\d+)$/.exec(scope);
  const visit=id?(draft?.visits||[]).find(v=>v.visit_id===id[1]):ordinal?draft?.visits?.[Number(ordinal[1])-1]:null;
  return visit?'停留 '+visit.point.label:'指定范围（待确认）';
 }
 function requirementValue(row){
  if(Array.isArray(row.value))return row.value.map(value=>requirementValues[value]||value).join('、');
  if(row.value===null||row.value===undefined)return '待确认';
  if(['max_walk_minutes','budget_minutes','stay_minutes'].includes(row.name))return row.value+' 分钟';
  if(['departure_at','finish_by'].includes(row.name)&&typeof row.value==='string'&&/(?:Z|[+-]\d{2}:\d{2})$/.test(row.value)){
   const date=new Date(row.value);
   if(!Number.isNaN(date.getTime()))return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Singapore',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(date)+'（新加坡时间）';
  }
  if(typeof row.value==='boolean')return row.value?'是':'否';
  return requirementValues[row.value]||String(row.value);
 }
 function requirementText(row,draft){
  const kind=row.kind==='preference'?'偏好':'要求';
  const hardness={hard:'必须',soft:'尽量',unknown:'强度待确认'}[row.hardness]||'强度待确认';
  const state={understood:'已理解，未核验',unresolved:'信息待补充，未核验',satisfied:'按参考数据通过',conflicting:'存在冲突，需修改',unsupported:'当前无法核验'}[row.status]||'未核验';
  const evidenceSource=row.evidence_source||(['route','catalogue'].includes(row.source)?row.source:null);
  const evidenceLabel={route:'路线复核',catalogue:'目录复核'}[evidenceSource];
  const description=requirementNames[row.name]?requirementNames[row.name]+'：'+requirementValue(row):'其他要求：'+(row.quote||requirementValue(row));
  return kind+' · '+hardness+' · '+scopeText(row.scope,draft)+' · '+state+(evidenceLabel?' · '+evidenceLabel:'')+' — '+description;
 }
 function showRequirements(id,title,p,draft){
  const lines=[title+'：'];
  for(const row of p.active_constraints||[])lines.push(requirementText(row,draft));
  if(p.preferred.length)lines.push('主题偏好：'+p.preferred.map(x=>names[x]||x).join('、'));
  if(p.excluded.length)lines.push('排除主题：'+p.excluded.map(x=>names[x]||x).join('、'));
  if(p.notes.length)lines.push('补充说明（未核验）：'+p.notes.join('；'));
  if(lines.length===1)lines.push('尚无额外要求或偏好');
  const box=el(id);box.style.whiteSpace='pre-wrap';box.textContent=lines.join('\n');
 }
 function showPrefs(){showRequirements('chat-preferences','已采用需求',preferences,bridge.currentData());}
 function invalidateVerification(){
  verificationRef=null;verificationSequence++;verificationPromiseRef=null;verificationPromise=null;
  preferences={...preferences,active_constraints:(preferences.active_constraints||[]).map(row=>{
   const evidenceSource=row.evidence_source||(['route','catalogue'].includes(row.source)?row.source:null);
   if(row.status!=='satisfied'&&!(row.status==='conflicting'&&evidenceSource))return row;
   const status=['opening_hours','must_be_open','last_admission'].includes(row.name)?'unresolved':['wheelchair_access','accessibility','no_stairs'].includes(row.name)?'unsupported':'understood';
   return {...row,status,evidence_source:null};
  })};
 }
 async function request(path,body,signal){const r=await fetch('/api/stage2c/'+path,{method:'POST',headers:{'Content-Type':'application/json','X-PlayMap-Client':'stage2c'},body:JSON.stringify(body),signal,cache:'no-store'});let d;try{d=await r.json();}catch{throw Error('本地服务没有返回JSON，请确认新启动入口。');}if(!r.ok){const e=Error(d.detail?.message||'本次请求失败');e.code=d.detail?.code||'REQUEST_FAILED';e.safeDetail=PlayMapChatState.safeErrorDetail(d.detail);throw e;}return d;}
 function cancel(text){
  const wasWaiting=!!controller;
  if(controller){controller.abort();controller=null;metrics.cancelled++;}gate.invalidate();proposal=null;pending=null;
  el('chat-proposal').hidden=true;
  el('chat-send').disabled=false;el('chat-cancel').disabled=true;el('chat-apply').disabled=true;
  if(text!==null)status(text||(wasWaiting?'已取消等待和待采用草案。已发出的API请求可能仍会计入额度。':'已取消待采用草案；已采用行程和要求保留。'),'warning');
 }
 window.addEventListener('playmap:edit',event=>{
  const kind=event.detail.kind;
  if(kind==='change'){prefHistory.push(clone(preferences));if(prefHistory.length>20)prefHistory.shift();}
  else if(kind==='undo')preferences=prefHistory.pop()||{preferred:[],excluded:[],notes:[],active_constraints:[]};
  else if(kind==='clear'){preferences={preferred:[],excluded:[],notes:[],active_constraints:[]};prefHistory=[];pending=null;}
  invalidateVerification();
  showPrefs();
  if(!applying&&(proposal||controller)){pending=null;cancel('地图或清单已经修改，旧聊天草案已失效。按当前行程重新发送即可。');el('chat-proposal').hidden=true;}
 });
 function selections(){const d={};for(const s of el('chat-slots').querySelectorAll('select'))if(s.value)d[s.dataset.slot]=s.value;return d;}
 function proposalDraftForDisplay(){
  const draft=clone(proposal.preview.draft||bridge.currentData()),choices=selections();
  for(const slot of proposal.slots){
   if(!slot.target_visit_id)continue;
   const selected=slot.options.find(option=>option.candidate_key===choices[slot.slot_id]);
   if(!selected)continue;
   const visit=draft.visits.find(item=>item.visit_id===slot.target_visit_id);
   if(visit)visit.point=selected.point;
   else draft.visits.push({visit_id:slot.target_visit_id,point:selected.point});
  }
  return draft;
 }
 function chosenEnough(){return !!proposal&&PlayMapChatState.proposalDiagnostic(proposal,selections()).all_slots_selected;}
 function hasProposedChanges(){return !!proposal&&(proposal.interpretation.actions.length>0||proposal.preview.changes.length>0||JSON.stringify(proposal.preview.preferences)!==JSON.stringify(preferences));}
 function canAdopt(){return chosenEnough()&&!PlayMapChatState.adoptionBlocked(proposal)&&hasProposedChanges();}
 function updateReadiness(){
  if(!proposal)return;
  metrics.lastProposal=PlayMapChatState.proposalDiagnostic(proposal,selections());
  const area=el('chat-readiness');
  area.textContent=PlayMapChatState.readinessText(proposal,selections());
  area.className=metrics.lastProposal.unresolved_slot_count||PlayMapChatState.adoptionBlocked(proposal)?'warning small':'small';
  if(!hasProposedChanges())area.textContent='本轮没有待采用的修改；已采用行程和要求保持不变。';
  el('chat-apply').disabled=!!controller||!canAdopt();
  el('chat-apply').textContent=proposal.interpretation.actions.some(action=>action.op==='add_visit')?'确认加入行程并计算参考路线':'确认保存草案并计算参考路线';
  showRequirements('chat-proposed-prefs','待采用要求',proposal.preview.preferences,proposalDraftForDisplay());
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
  updateReadiness();
  const empty=metrics.lastProposal.empty_slot_count;
  status(empty?`草案尚未完整 · ${empty}项没有候选；模型请求已成功，原行程未修改。`:`草案已生成 · 本轮 ${r.usage.generation_requests} 次模型请求。先核对地点和修改，再采用。`,empty?'warning':'');
 }
 async function send(event){event.preventDefault();
  const text=el('chat-input').value.trim();if(!text)return;
  // No checkbox to tick: the disclosure is a standing line under the bar
  // (.chat-notice) so the request still carries consent:true behind something
  // the user can actually see. #chat-consent stays in the DOM, unused here.
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
  el('chat-send').disabled=true;el('chat-cancel').disabled=false;el('chat-cancel').textContent='取消等待';el('chat-proposal').hidden=true;el('chat-apply').disabled=true;
  message('user',text);status('理解需求并检索真实地点…最多两次模型请求；不自动重试或换模型。');
  const pendingAtStart=pending;
  try{
   const r=await request('chat',{...snap,message:text,preferences,pending:pendingAtStart,...(verificationRef?{verification_ref:verificationRef}:{}),consent:true},local.signal);
   if(!gate.accepts(ticket,bridge.currentRevision(),r))return;
   metrics.completed++;metrics.calls+=r.usage.generation_requests;metrics.model=r.model;metrics.error=null;metrics.lastAdaptation=r.operation_adaptation||null;
   for(const call of r.usage.calls)for(const [k,v] of Object.entries(call.usage||{}))metrics.tokens[k]=(metrics.tokens[k]||0)+v;
   pending={user_message:((pendingAtStart?.user_message?pendingAtStart.user_message+'\n':'')+text).slice(-3000),interpretation:r.interpretation};
   showProposal(r);el('chat-input').value='';
   message('assistant',r.acknowledgement||'已生成可核对的修改草案，请确认后采用。');
  }catch(e){if(e.name==='AbortError')return;
   if(!gate.accepts(ticket,bridge.currentRevision(),{base_revision:ticket.revision}))return;
   metrics.failed++;metrics.error=e.code||'NETWORK_OR_CLIENT';metrics.lastFailure=e.safeDetail||null;
   PlayMapChatState.recordFailure(metrics,e.safeDetail);
   const hint=PlayMapChatState.errorSummary(e.safeDetail);
   status(e.message+(e.code?' ['+e.code+']':'')+(hint?'；'+hint:'')+'；原行程未修改。','error');}
  finally{if(controller===local){controller=null;el('chat-send').disabled=false;el('chat-cancel').disabled=!(proposal||pending);el('chat-cancel').textContent='取消草案';updateReadiness();}}
 }
 async function refreshVerification(plan){
  const ref=plan.verification_ref;
  if(typeof ref!=='string'||!ref){invalidateVerification();showPrefs();return;}
  if(ref===verificationPromiseRef&&verificationPromise)return verificationPromise;
  const sequence=++verificationSequence,session=bridge.currentSession(),revision=bridge.currentRevision(),draft=bridge.currentData();
  const adoptedPreferences=clone(preferences),fingerprint=JSON.stringify({draft,preferences:adoptedPreferences});
  const stillCurrent=()=>verificationSequence===sequence&&bridge.currentSession()===session&&bridge.currentRevision()===revision&&JSON.stringify({draft:bridge.currentData(),preferences})===fingerprint;
  verificationPromiseRef=ref;
  verificationPromise=(async()=>{try{
   const result=await request('verification',{session_id:session,revision,verification_ref:ref,draft,preferences:adoptedPreferences});
   if(!stillCurrent())return false;
   preferences=result.preferences;verificationRef=result.verification.status==='matched'?ref:null;showPrefs();
   const conflict=preferences.active_constraints.some(row=>row.status==='conflicting');
   status(conflict?'参考路线存在要求冲突；请修改草案后重新计算。':'参考路线已生成；请查看各项要求的核验状态。',conflict?'warning':'');
  }catch(e){if(!stillCurrent())return false;invalidateVerification();showPrefs();status('参考路线已生成，但要求核验暂未完成；'+e.message,'warning');}
  })();
  return verificationPromise;
 }
 window.addEventListener('playmap:plan-complete',event=>{
  const plan=event.detail?.plan;
  if(!plan||event.detail.revision!==bridge.currentRevision()||plan.revision!==bridge.currentRevision())return;
  void refreshVerification(plan);
 });
 async function adopt(){
  if(controller||applying||!proposal||!canAdopt())return;const r=proposal,rev=bridge.currentRevision();
  if(r.base_revision!==rev){cancel('草案已过期，请重新发送。');return;}
  const local=new AbortController();controller=local;el('chat-apply').disabled=true;el('chat-send').disabled=true;el('chat-cancel').disabled=false;
  status('校验所选候选并更新行程…这一步不调用Gemini。');
  try{
   const resolved=await request('resolve',{ticket:r.ticket,session_id:bridge.currentSession(),revision:rev,choices:selections()},local.signal);
   if(proposal!==r||bridge.currentRevision()!==rev||controller!==local)return;
   applying=true;try{bridge.apply(resolved.draft,rev);preferences=resolved.preferences;}finally{applying=false;}
   showPrefs();metrics.applied++;pending=null;proposal=null;el('chat-proposal').hidden=true;
   // The reply is the RESULT, not a description of how it was produced. The
   // itinerary itself renders below in #result-panel.
   if(!resolved.ready_to_calculate)
    message('assistant','还差一个出发点才能算时间。可以点搜索栏旁的定位钮，或直接说出起点名称。');
   status('未核验草案已保存，地图与行程清单已同步；保存不代表要求已满足。');
   controller=null;el('chat-cancel').disabled=true;el('chat-send').disabled=false;
   if(resolved.ready_to_calculate){
    const appliedRevision=bridge.currentRevision(),verificationAtStart=verificationSequence;await bridge.calculate();
    if(bridge.currentRevision()!==appliedRevision)return;
    const plan=bridge.activePlan();
    const planVerificationRef=typeof plan?.verification_ref==='string'?plan.verification_ref:null;
    if(verificationSequence!==verificationAtStart&&verificationPromiseRef!==planVerificationRef)return;
    if(plan){if(await refreshVerification(plan)===false||bridge.currentRevision()!==appliedRevision)return;message('assistant','已生成 '+plan.visits.length+' 处停留的参考路线，参考总时长 '+PlayMapTrip.duration(plan.totals.reference_duration_s)+'。请核对上方各项要求的核验状态和下方行程详情。');}
    else message('assistant','路线本次没能算出来，原因列在下面的行程总览里；草案仍可以继续改。');
   }
  }catch(e){if(e.name==='AbortError')return;metrics.failed++;metrics.error=e.code||'APPLY_FAILED';metrics.lastFailure=e.safeDetail||PlayMapChatState.safeErrorDetail({request_diagnostic:{phase:'resolve_validation',wire_version:PlayMapChatState.PATCH,generation_attempts:0}});PlayMapChatState.recordFailure(metrics,{generation_attempts:0});if(['PROPOSAL_EXPIRED','PROPOSAL_STALE','CATALOGUE_CHANGED'].includes(e.code))cancel(null);status(e.message+(e.code?' ['+e.code+']':''),'error');}
  finally{if(controller===local){controller=null;el('chat-send').disabled=false;el('chat-cancel').disabled=!(proposal||pending);el('chat-cancel').textContent='取消草案';updateReadiness();}}
 }
 async function refresh(){try{const r=await fetch('/api/stage2c/status',{cache:'no-store'}).then(x=>x.json());el('chat-model').textContent=r.configured?'Gemini · '+r.model+' · CHAT FIX 9 · 按发送调用':'模型尚未配置：'+(r.error?.message||'请检查.env');if(r.model)metrics.model=r.model;}catch{el('chat-model').textContent='无法读取模型状态，请确认使用stage2c_main入口。';}}
 function download(){const d=PlayMapChatState.diagnose(metrics,bridge.diagnostic(),proposal,selections()),url=URL.createObjectURL(new Blob([JSON.stringify(d,null,2)],{type:'application/json'})),a=make('a');a.href=url;a.download='stage2c1_chat_report.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);status('已导出无原话、地点名、坐标或Key的本地报告；失败请求可能已消耗额度，精确用量以Google控制台为准。');}
 el('chat-form').onsubmit=send;el('chat-apply').onclick=adopt;el('chat-cancel').onclick=()=>cancel();el('chat-export').onclick=download;
 // Starting a new thread only parks the transcript; the adopted itinerary stays.
 el('chat-new').onclick=()=>{cancel(null);pending=null;log.start();renderLog();renderHistory();status('已开始新对话；地图上已采用的行程保留。');};
 // The conversation card can be folded or dismissed; closing it never touches
 // the itinerary, and a reply arriving while it is closed reopens it.
 const thread=()=>document.querySelector('.ov-thread');
 function showThread(open){
  thread().hidden=!open;el('thread-reopen').hidden=open;
  if(open)thread().classList.remove('is-collapsed');
 }
 el('thread-close').onclick=()=>showThread(false);
 el('thread-reopen').onclick=()=>showThread(true);
 el('thread-collapse').onclick=()=>{
  const folded=thread().classList.toggle('is-collapsed');
  el('thread-collapse').textContent=folded?'△':'▽';
  el('thread-collapse').setAttribute('aria-expanded',String(!folded));
 };
 el('editor-toggle').onclick=()=>{
  const drawer=el('editor-drawer'),open=drawer.hidden;
  drawer.hidden=!open;el('editor-toggle').setAttribute('aria-expanded',String(open));
  el('editor-toggle').textContent=open?'收起编辑':'编辑行程';
 };
 el('chat-resume').onclick=()=>{log.resume();renderLog();renderHistory();};
 // Grow the bar with the text instead of scrolling it, so no scrollbar arrows.
 const grow=()=>{const t=el('chat-input');t.style.height='auto';
  t.style.height=Math.min(t.scrollHeight,76)+'px';};
 el('chat-input').addEventListener('input',grow);
 el('chat-reset').onclick=async()=>{
  if(!window.confirm('清除本页对话与待确认草案？已采用行程和要求保留；核验记录将失效。此操作不删除Google已收到的内容。'))return;
  cancel(null);invalidateVerification();originAsked=false;log.clear();renderLog();renderHistory();showPrefs();
  try{await request('forget',{session_id:bridge.currentSession()});status('对话与待确认草案已清理；已采用行程和要求保留，需重新核验。');}
  catch{status('本地对话与待确认草案已清理，服务端暂未确认清理；已采用行程和要求保留。','warning');}
 };
 showPrefs();renderHistory();refresh();
})();
