/* Natural-language proposals + confirmed changes, reusing Stage2B calculations. */
(function(){'use strict';
 const el=id=>document.getElementById(id),bridge=PlayMapChatBridge,clone=x=>JSON.parse(JSON.stringify(x));
 const gate=new PlayMapChatState.Gate();
 let controller=null,proposal=null,pending=null,verificationRef=null,verificationSequence=0,verificationPromiseRef=null,verificationPromise=null,preferences={preferred:[],excluded:[],notes:[],active_constraints:[]},prefHistory=[],applying=false,originAsked=false;
 const metrics={completed:0,failed:0,calls:0,applied:0,cancelled:0,tokens:{},model:null,error:null,lastFailure:null,failedAttempts:0,failedUnknown:0,lastAdaptation:null};
 const names={park:'Parks',food:'Dining',museum:'Museums',shopping:'Shopping',heritage:'Heritage',architecture:'Architecture',art:'Art',nature:'Nature',photography:'Photography'};
 const requirementNames={place_type:'Place type',max_walk_minutes:'Per-leg walking limit',takeaway:'Takeaway',excluded_topic:'Excluded theme',theme:'Trip theme',drink:'Drink',pace:'Pace',environment:'Environment',walking_effort:'Walking effort',duration:'Trip duration',budget_minutes:'Total time budget',departure_at:'Departure time',finish_by:'Finish time',stay_minutes:'Stay duration',transport_mode:'Transport mode',travel_mode:'Transport mode',finish_policy:'Finish policy',opening_hours:'Opening-hours requirement',must_be_open:'Must be open',last_admission:'Last admission',wheelchair_access:'Accessibility requirement',radius_m:'Straight-line radius',distance_m:'Distance requirement',spatial_reference:'Spatial reference',area:'Area',time_window:'Time window'};
 const requirementValues={...names,cafe:'Cafe',restaurant:'Restaurant',coffee:'Coffee',tea:'Tea',indoor:'Indoors',outdoor:'Outdoors',relaxed:'Relaxed',slow:'Slow pace',leisurely:'Relaxed',family:'Family-friendly',family_friendly:'Family-friendly',child_friendly:'Family-friendly',less:'Less walking',low:'Low',one_day:'One day',walk:'Walk',drive:'Drive',cycle:'Cycle',public_transport:'Public transport',no_stairs:'No stairs',last_stop:'Finish at the last stop',return_to_start:'Return to start'};
 function make(tag,text,cls){const x=document.createElement(tag);if(text!==undefined)x.textContent=text;if(cls)x.className=cls;return x;}
 function button(text,fn){const b=make('button',text,'quiet');b.type='button';b.onclick=fn;return b;}
 function status(text,cls=''){el('chat-status').textContent=text;el('chat-status').className=cls;}
 const log=new PlayMapChatState.ConversationLog();
 let asyncGeneration=0,alternativeController=null,calculationContext=null,proposalContext=null;
 function captureContext(){return Object.freeze({generation:asyncGeneration,thread:log.activeId,
  session:bridge.currentSession(),revision:bridge.currentRevision()});}
 function contextCurrent(context,checkRevision=true){return context.generation===asyncGeneration&&
  context.thread===log.activeId&&context.session===bridge.currentSession()&&
  (!checkRevision||context.revision===bridge.currentRevision());}
 function advanceContext(){
  asyncGeneration++;verificationSequence++;verificationPromiseRef=null;verificationPromise=null;
  if(alternativeController){alternativeController.abort();alternativeController=null;}
 }
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
   b.type='button';b.append(make('small','Messages: '+t.count));
   // Opening a past thread resumes it; the user can keep talking in it.
   b.onclick=()=>{if(t.id===log.activeId)return;cancel(null);log.activate(t.id);renderLog();renderHistory();status('Switched chat. Pending requests and drafts were cancelled; the adopted itinerary and requirements are retained.');};
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
  if(scope==='global')return 'Whole itinerary';
  if(scope==='future_visits')return 'Future additions only';
  if(scope==='origin')return 'Start point';
  if(scope==='finish')return 'Finish point';
  if(scope==='visit:new')return 'New stops in this draft';
  const next=/^visit:new:(\d+)$/.exec(scope);
  if(next)return 'New stop '+next[1]+'';
  const id=/^visit_id:(.+)$/.exec(scope),ordinal=/^visit:(\d+)$/.exec(scope);
  const visit=id?(draft?.visits||[]).find(v=>v.visit_id===id[1]):ordinal?draft?.visits?.[Number(ordinal[1])-1]:null;
  return visit?'Stop '+visit.point.label:'Selected scope (to confirm)';
 }
 function requirementValue(row){
  if(Array.isArray(row.value))return row.value.map(value=>requirementValues[value]||value).join(', ');
  if(row.value===null||row.value===undefined)return 'To confirm';
  if(['max_walk_minutes','budget_minutes','stay_minutes'].includes(row.name))return row.value+' min';
  if(['departure_at','finish_by'].includes(row.name)&&typeof row.value==='string'&&/(?:Z|[+-]\d{2}:\d{2})$/.test(row.value)){
   const date=new Date(row.value);
   if(!Number.isNaN(date.getTime()))return new Intl.DateTimeFormat('en-SG',{timeZone:'Asia/Singapore',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(date)+' (Singapore time)';
  }
  if(typeof row.value==='boolean')return row.value?'Yes':'No';
  return requirementValues[row.value]||String(row.value);
 }
 function requirementText(row,draft){
  const kind=row.kind==='preference'?'Preference':'Requirement';
  const hardness={hard:'Required',soft:'Preferred',unknown:'Priority to confirm'}[row.hardness]||'Priority to confirm';
  const state={understood:'Understood, not verified',unresolved:'More information needed, not verified',satisfied:'Passed using reference data',conflicting:'Conflict; changes needed',unsupported:'Cannot currently verify'}[row.status]||'Not verified';
  const evidenceSource=row.evidence_source||(['route','catalogue'].includes(row.source)?row.source:null);
  const evidenceLabel={route:'Route check',catalogue:'Catalogue check'}[evidenceSource];
  const description=requirementNames[row.name]?requirementNames[row.name]+': '+requirementValue(row):'Other requirement: '+(row.quote||requirementValue(row));
  return kind+' · '+hardness+' · '+scopeText(row.scope,draft)+' · '+state+(evidenceLabel?' · '+evidenceLabel:'')+' — '+description;
 }
 function showRequirements(id,title,p,draft){
  const lines=[title+': '];
  for(const row of p.active_constraints||[])lines.push(requirementText(row,draft));
  if(p.preferred.length)lines.push('Preferred themes: '+p.preferred.map(x=>names[x]||x).join(', '));
  if(p.excluded.length)lines.push('Excluded themes: '+p.excluded.map(x=>names[x]||x).join(', '));
  if(p.notes.length)lines.push('Additional notes (not verified): '+p.notes.join('; '));
  if(lines.length===1)lines.push('No additional requirements or preferences');
  const box=el(id);box.style.whiteSpace='pre-wrap';box.textContent=lines.join('\n');
 }
 function showPrefs(){showRequirements('chat-preferences','Adopted requirements',preferences,bridge.currentData());}
 function invalidateVerification(){
  verificationRef=null;verificationSequence++;verificationPromiseRef=null;verificationPromise=null;
  preferences={...preferences,active_constraints:(preferences.active_constraints||[]).map(row=>{
   const evidenceSource=row.evidence_source||(['route','catalogue'].includes(row.source)?row.source:null);
   if(row.status!=='satisfied'&&!(row.status==='conflicting'&&evidenceSource))return row;
   const status=['opening_hours','must_be_open','last_admission'].includes(row.name)?'unresolved':['wheelchair_access','accessibility','no_stairs'].includes(row.name)?'unsupported':'understood';
   return {...row,status,evidence_source:null};
  })};
 }
 async function request(path,body,signal){const r=await fetch('/api/stage2c/'+path,{method:'POST',headers:{'Content-Type':'application/json','X-PlayMap-Client':'stage2c'},body:JSON.stringify(body),signal,cache:'no-store'});let d;try{d=await r.json();}catch{throw Error('The server did not return JSON. Check that the application is running.');}if(!r.ok){const e=Error(d.detail?.message||'Request failed');e.code=d.detail?.code||'REQUEST_FAILED';e.safeDetail=PlayMapChatState.safeErrorDetail(d.detail);throw e;}return d;}
 function cancel(text){
  const wasWaiting=!!controller;advanceContext();
  if(controller){controller.abort();controller=null;metrics.cancelled++;}gate.invalidate();proposal=null;proposalContext=null;pending=null;
  el('chat-proposal').hidden=true;
  el('chat-send').disabled=false;el('chat-cancel').disabled=true;el('chat-apply').disabled=true;
  if(text!==null)status(text||(wasWaiting?'Waiting and pending draft cancelled. Requests already sent may still use quota.':'Pending draft cancelled. The adopted itinerary and requirements are retained.'),'warning');
 }
 window.addEventListener('playmap:edit',event=>{
  const kind=event.detail.kind;
   if(kind==='change'){
    prefHistory.push(clone(preferences));if(prefHistory.length>20)prefHistory.shift();
    if(!applying)preferences=PlayMapChatState.reconcileManualPreferences(preferences,event.detail.previousData,bridge.currentData());
   }
  else if(kind==='undo')preferences=prefHistory.pop()||{preferred:[],excluded:[],notes:[],active_constraints:[]};
  else if(kind==='clear'){preferences={preferred:[],excluded:[],notes:[],active_constraints:[]};prefHistory=[];pending=null;}
  invalidateVerification();
  showPrefs();
  if(!applying&&(proposal||controller)){pending=null;cancel('The map or stop list changed, so the previous chat draft expired. Send a new message for the current itinerary.');el('chat-proposal').hidden=true;}
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
 function canAdopt(){return !!proposalContext&&contextCurrent(proposalContext)&&chosenEnough()&&!PlayMapChatState.adoptionBlocked(proposal)&&hasProposedChanges();}
 function updateReadiness(){
  if(!proposal)return;
  metrics.lastProposal=PlayMapChatState.proposalDiagnostic(proposal,selections());
  const area=el('chat-readiness');
  area.textContent=PlayMapChatState.readinessText(proposal,selections());
  area.className=metrics.lastProposal.unresolved_slot_count||PlayMapChatState.adoptionBlocked(proposal)?'warning small':'small';
  if(!hasProposedChanges())area.textContent='No changes to adopt in this turn. The current itinerary and requirements are unchanged.';
  el('chat-apply').disabled=!!controller||!canAdopt();
  el('chat-apply').textContent=proposal.interpretation.actions.some(action=>action.op==='add_visit')?'Confirm stops and calculate route':'Save draft and calculate route';
  showRequirements('chat-proposed-prefs','Proposed requirements',proposal.preview.preferences,proposalDraftForDisplay());
 }
 function showProposal(r){
  proposal=r;proposalContext=captureContext();el('chat-proposal').hidden=false;const slots=el('chat-slots');slots.replaceChildren();
  for(const s of r.slots){
   const box=make('div',undefined,'chat-slot');
   const title={set_origin:'Confirm start',set_finish:'Confirm finish',add_visit:'Suggested new stop',replace_visit:'Replace stop'}[s.operation];
   box.append(make('strong',title+(s.query?': '+s.query:'')));
   const select=make('select');select.dataset.slot=s.slot_id;select.setAttribute('aria-label',title+' candidate');
   const placeholder=make('option',s.options.length?'Choose a place':'No matching candidates yet (not an API failure)');placeholder.value='';select.append(placeholder);
   for(const o of s.options){const option=make('option',o.label+(o.point.source==='catalogue_representative'?' · Catalogue representative point':' · OneMap'));option.value=o.candidate_key;select.append(option);}
   select.value=s.selected_key||'';select.disabled=!s.options.length;box.append(select);
   const detail=make('p',undefined,'small');box.append(detail);
   const update=()=>{const o=s.options.find(x=>x.candidate_key===select.value);
    if(o){const parts=[o.address||'Address unavailable',o.basis];
     if(o.distance_hint_m!==null)parts.push(`Approximately ${(o.distance_hint_m/1000).toFixed(2)} km from the reference start in a straight line, not travel distance`);
     detail.textContent=parts.filter(Boolean).map(text=>text.trim().replace(/[.;]+$/,'')).join('. ')+'.';
    }else detail.textContent=s.options.length?'Confirm a place. The first search result is not used automatically.':'No candidate found for this item yet, so it cannot be adopted as complete. See the search scope and conditions below.';
    updateReadiness();};
   select.onchange=update;
   box.append(button('View candidate on map',()=>{const o=s.options.find(x=>x.candidate_key===select.value);if(o)bridge.focus(o.point);else status('Select a candidate first.','warning');}));
   if(s.reason)box.append(make('p','Model suggestion (check before using): '+s.reason,'small'));
   for(const n of s.notices)box.append(make('p',n,'small'));
   slots.append(box);update();
  }
  el('chat-changes').replaceChildren(...r.preview.changes.map(t=>make('p',t,'small')));
  el('chat-warnings').replaceChildren(...[...r.questions,...r.preview.warnings].map(t=>make('p',t,'small')));
  updateReadiness();
  const empty=metrics.lastProposal.empty_slot_count;
  status(empty?`Draft incomplete · No candidates for ${empty} ${empty===1?'item':'items'}. The model request succeeded; the original itinerary is unchanged.`:`Draft generated · Model requests this turn: ${r.usage.generation_requests}. Review the places and changes before adopting.`,empty?'warning':'');
 }
 async function send(event){event.preventDefault();
  const text=el('chat-input').value.trim();if(!text)return;
  const openingContext=captureContext();
  // No checkbox to tick: the disclosure is a standing line under the bar
  // (.chat-notice) so the request still carries consent:true behind something
  // the user can actually see. #chat-consent stays in the DOM, unused here.
  // Ask BEFORE the model call: candidates are ranked against the origin, so an
  // origin adopted afterwards would not re-anchor this turn's retrieval. Asked at
  // most once per conversation, and declining is a normal answer.
  if(!bridge.hasOrigin()&&!originAsked){
   originAsked=true;el('chat-send').disabled=true;
   status('No start point yet. Choose whether to use your current location above the map. Choose manually to explore across Singapore.');
   try{await bridge.proposeDeviceOrigin();}catch{}
   if(!contextCurrent(openingContext,false))return;
   el('chat-send').disabled=false;
   if(controller)return;
  }
  let snap;try{snap=bridge.snapshot();}catch(e){status(e.message,'error');return;}
  if(controller)return;
  advanceContext();const context=captureContext();
  const ticket=gate.begin(snap.revision),local=new AbortController();controller=local;proposal=null;
  el('chat-send').disabled=true;el('chat-cancel').disabled=false;el('chat-cancel').textContent='Cancel request';el('chat-proposal').hidden=true;el('chat-apply').disabled=true;
  message('user',text);status('Interpreting your request and finding real places… Up to two model requests; no automatic retry or model switch.');
  const pendingAtStart=pending;
  try{
   const r=await request('chat',{...snap,message:text,preferences,pending:pendingAtStart,...(verificationRef?{verification_ref:verificationRef}:{}),consent:true},local.signal);
   if(!contextCurrent(context)||!gate.accepts(ticket,bridge.currentRevision(),r))return;
   metrics.completed++;metrics.calls+=r.usage.generation_requests;metrics.model=r.model;metrics.error=null;metrics.lastAdaptation=r.operation_adaptation||null;
   for(const call of r.usage.calls)for(const [k,v] of Object.entries(call.usage||{}))metrics.tokens[k]=(metrics.tokens[k]||0)+v;
   pending={user_message:((pendingAtStart?.user_message?pendingAtStart.user_message+'\n':'')+text).slice(-3000),interpretation:r.interpretation};
   showProposal(r);el('chat-input').value='';
   message('assistant',r.acknowledgement||'A draft is ready for review. Confirm before applying it.');
  }catch(e){if(e.name==='AbortError')return;
   if(!contextCurrent(context)||!gate.accepts(ticket,bridge.currentRevision(),{base_revision:ticket.revision}))return;
   metrics.failed++;metrics.error=e.code||'NETWORK_OR_CLIENT';metrics.lastFailure=e.safeDetail||null;
   PlayMapChatState.recordFailure(metrics,e.safeDetail);
   const hint=PlayMapChatState.errorSummary(e.safeDetail);
   status(e.message+(e.code?' ['+e.code+']':'')+(hint?'; '+hint:'')+'; the original itinerary is unchanged.','error');}
  finally{if(controller===local){controller=null;el('chat-send').disabled=false;el('chat-cancel').disabled=!(proposal||pending);el('chat-cancel').textContent='Cancel draft';updateReadiness();}}
 }
 async function refreshVerification(plan){
  const ref=plan.verification_ref;
  if(typeof ref!=='string'||!ref){invalidateVerification();showPrefs();return;}
  if(ref===verificationPromiseRef&&verificationPromise)return verificationPromise;
  const context=captureContext(),sequence=++verificationSequence,session=bridge.currentSession(),revision=bridge.currentRevision(),draft=bridge.currentData();
  const adoptedPreferences=clone(preferences),fingerprint=JSON.stringify({draft,preferences:adoptedPreferences});
  const stillCurrent=()=>contextCurrent(context)&&verificationSequence===sequence&&bridge.currentSession()===session&&bridge.currentRevision()===revision&&JSON.stringify({draft:bridge.currentData(),preferences})===fingerprint;
  verificationPromiseRef=ref;
  verificationPromise=(async()=>{try{
   const result=await request('verification',{session_id:session,revision,verification_ref:ref,draft,preferences:adoptedPreferences});
   if(!stillCurrent())return false;
   preferences=result.preferences;verificationRef=result.verification.status==='matched'?ref:null;showPrefs();
   const conflict=preferences.active_constraints.some(row=>row.status==='conflicting');
   status(conflict?'The reference route conflicts with some requirements. Edit the draft and recalculate.':'Reference route generated. Review the verification status of each requirement.',conflict?'warning':'');
  }catch(e){if(!stillCurrent())return false;invalidateVerification();showPrefs();status('Reference route generated, but requirement verification is incomplete. '+e.message,'warning');}
  })();
  return verificationPromise;
 }
 window.addEventListener('playmap:plan-complete',event=>{
  const plan=event.detail?.plan;
  if(!plan||event.detail.revision!==bridge.currentRevision()||plan.revision!==bridge.currentRevision())return;
  if(calculationContext&&calculationContext.context.revision===plan.revision&&
   (calculationContext.sequence===null||calculationContext.sequence===bridge.currentSequence?.())&&
   !contextCurrent(calculationContext.context))return;
  void refreshVerification(plan);
 });
 async function suggestWalkingAlternatives(plan){
  const context=captureContext();
  if(plan?.revision!==context.revision||bridge.activePlan()!==plan)return;
  const check=plan?.walking_constraint_check;
  if(!check||check.status!=='violation'||!check.violations?.length)return;
  const violation=check.violations[0];
  const match=/^leg-([1-9][0-9]*)$/.exec(violation.leg_id||'');
  if(!match)return;
  const legIndex=Number(match[1])-1;
  const visitIndex=Math.min(legIndex,(plan.visits?.length||1)-1);
  const visit=plan.visits?.[visitIndex];
  const category=visit?.category||'';
  if(!category||category==='unknown'){
   message('assistant','This walk exceeds your limit, but the stop has no usable category for finding similar alternatives.');
   return;
  }
  const local=new AbortController(),calculationSequence=bridge.currentSequence();alternativeController=local;
  const stillCurrent=()=>contextCurrent(context)&&alternativeController===local&&
   bridge.currentSequence()===calculationSequence&&bridge.activePlan()===plan;
  try{
   const snap=bridge.snapshot();if(!stillCurrent())return;
   const response=await fetch('/api/stage2b/alternatives',{
    method:'POST',
    headers:{'Content-Type':'application/json','X-PlayMap-Client':'stage2b'},
    body:JSON.stringify({
     plan_request:{session_id:snap.session_id,revision:snap.revision,...snap.draft},
     current_plan:plan,leg_id:violation.leg_id,category,
     radius_m:1000,limit:20,max_suggestions:5
    }),
    cache:'no-store',signal:local.signal
   });
   const result=await response.json();
   if(!stillCurrent())return;
   if(!response.ok)throw Error(result.detail?.message||'Could not find alternative places');
   if(!result.suggestions?.length){
    message('assistant',`This leg takes about ${violation.duration_minutes} min, above your ${snap.draft.max_walk_minutes} min limit. No nearby replacement is available after checking the full itinerary.`);
    return;
   }
   const lines=result.suggestions.map((x,i)=>{
     const checks=x.feasibility_check?.checks||{};
     const review=[checks.opening_hours!==true?'Opening hours need checking':null,
      checks.time_budget!==true?'Time budget needs checking':null,
      checks.walking_constraint!==true?'Walking limit needs checking':null].filter(Boolean);
     const label=x.feasible===true?'Passes current feasibility checks':review.join('; ')||'Feasibility needs checking';
     return `${i+1}. ${x.label} (about ${x.route_duration_min} min for this walk) · ${label}`;
    });
    message('assistant',
    `This leg takes about ${violation.duration_minutes} min, above your ${snap.draft.max_walk_minutes} min limit. Nearby replacements after checking the full itinerary:\n${lines.join('\n')}\nChecks use reference estimates and catalogue hours, not live opening or verified access. Review any unknown checks before choosing a replacement. Tell me which one you would like to use. The itinerary has not changed.`);
  }catch(e){
   if(!stillCurrent()||e.name==='AbortError')return;
   message('assistant','This walk exceeds your limit, but the alternatives search did not finish. The itinerary is unchanged.');
  }finally{if(alternativeController===local)alternativeController=null;}
 }
 async function adopt(){
  if(controller||applying||!proposal||!canAdopt())return;const r=proposal,rev=bridge.currentRevision();
  if(r.base_revision!==rev){cancel('This draft expired. Send your request again.');return;}
  let context=captureContext();
  const local=new AbortController();controller=local;el('chat-apply').disabled=true;el('chat-send').disabled=true;el('chat-cancel').disabled=false;
  status('Validating the selected candidates and updating the itinerary… This step does not call Gemini.');
  try{
   const resolved=await request('resolve',{ticket:r.ticket,session_id:bridge.currentSession(),revision:rev,choices:selections()},local.signal);
   if(!contextCurrent(context)||proposal!==r||bridge.currentRevision()!==rev||controller!==local)return;
   applying=true;try{bridge.apply(resolved.draft,rev);preferences=resolved.preferences;}finally{applying=false;}
   context=captureContext();showPrefs();metrics.applied++;pending=null;proposal=null;el('chat-proposal').hidden=true;
   // The reply is the RESULT, not a description of how it was produced. The
   // itinerary itself renders below in #result-panel.
   if(!resolved.ready_to_calculate)
    message('assistant','A start point is still needed to calculate time. Use the location button beside search, or tell me where to start.');
   status('Unverified draft saved to the map and stop list. Saving does not mean the requirements are satisfied.');
   controller=null;el('chat-cancel').disabled=true;el('chat-send').disabled=false;
   if(resolved.ready_to_calculate){
    const appliedRevision=bridge.currentRevision(),verificationAtStart=verificationSequence;
    const calculation=bridge.calculate(),calculationOwner={context,sequence:bridge.currentSequence?.()??null};
    calculationContext=calculationOwner;
    try{await calculation;}finally{if(calculationContext===calculationOwner)calculationContext=null;}
    if(!contextCurrent(context)||bridge.currentRevision()!==appliedRevision)return;
    const plan=bridge.activePlan();
    const planVerificationRef=typeof plan?.verification_ref==='string'?plan.verification_ref:null;
    if(verificationSequence!==verificationAtStart&&verificationPromiseRef!==planVerificationRef)return;
    if(plan){if(await refreshVerification(plan)===false||!contextCurrent(context)||bridge.currentRevision()!==appliedRevision)return;message('assistant','Reference route generated for '+plan.visits.length+(plan.visits.length===1?' stop, with an estimated duration of ':' stops, with an estimated duration of ')+PlayMapTrip.duration(plan.totals.reference_duration_s)+'. Review the requirement checks above and itinerary details below.');await suggestWalkingAlternatives(plan);}
    else message('assistant','The route could not be calculated. See the reason in the itinerary overview below; you can keep editing the draft.');
   }
  }catch(e){if(e.name==='AbortError'||!contextCurrent(context))return;metrics.failed++;metrics.error=e.code||'APPLY_FAILED';metrics.lastFailure=e.safeDetail||PlayMapChatState.safeErrorDetail({request_diagnostic:{phase:'resolve_validation',wire_version:PlayMapChatState.PATCH,generation_attempts:0}});PlayMapChatState.recordFailure(metrics,{generation_attempts:0});if(['PROPOSAL_EXPIRED','PROPOSAL_STALE','CATALOGUE_CHANGED'].includes(e.code))cancel(null);status(e.message+(e.code?' ['+e.code+']':''),'error');}
  finally{if(controller===local){controller=null;el('chat-send').disabled=false;el('chat-cancel').disabled=!(proposal||pending);el('chat-cancel').textContent='Cancel draft';updateReadiness();}}
 }
 async function refresh(){try{const r=await fetch('/api/stage2c/status',{cache:'no-store'}).then(x=>x.json());el('chat-model').textContent=r.configured?'Gemini · '+r.model+' · CHAT FIX 9 · Called when you send':'Model not configured: '+(r.error?.message||'Check the server configuration');if(r.model)metrics.model=r.model;}catch{el('chat-model').textContent='Cannot read model status. Check the stage2c_main application entry point.';}}
 function download(){const d=PlayMapChatState.diagnose(metrics,bridge.diagnostic(),proposal,selections()),url=URL.createObjectURL(new Blob([JSON.stringify(d,null,2)],{type:'application/json'})),a=make('a');a.href=url;a.download='stage2c1_chat_report.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);status('Local report exported without messages, place names, coordinates or keys. Failed requests may still use quota; check the Google console for exact usage.');}
 el('chat-form').onsubmit=send;el('chat-apply').onclick=adopt;el('chat-cancel').onclick=()=>cancel();el('chat-export').onclick=download;
 // Starting a new thread only parks the transcript; the adopted itinerary stays.
 el('chat-new').onclick=()=>{cancel(null);pending=null;log.start();renderLog();renderHistory();status('New chat started. The adopted itinerary remains on the map.');};
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
  el('editor-toggle').textContent=open?'Close editor':'Edit itinerary';
 };
 el('chat-resume').onclick=()=>{log.resume();renderLog();renderHistory();};
 // Grow the bar with the text instead of scrolling it, so no scrollbar arrows.
 const grow=()=>{const t=el('chat-input');t.style.height='auto';
  t.style.height=Math.min(t.scrollHeight,76)+'px';};
 el('chat-input').addEventListener('input',grow);
 el('chat-reset').onclick=async()=>{
  if(!window.confirm('Clear this chat and pending drafts? The adopted itinerary and requirements are retained, but verification will expire. Content already received by Google is not deleted.'))return;
  cancel(null);invalidateVerification();originAsked=false;log.clear();renderLog();renderHistory();showPrefs();
  const context=captureContext();
  try{await request('forget',{session_id:context.session});if(!contextCurrent(context))return;status('Chat and pending drafts cleared. The adopted itinerary and requirements are retained and need re-verification.');}
  catch{if(!contextCurrent(context))return;status('Local chat and pending drafts cleared; the server has not confirmed cleanup. The adopted itinerary and requirements are retained.','warning');}
 };
 showPrefs();renderHistory();refresh();
})();
