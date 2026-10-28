/* Revision gate and value-free failure diagnostics. No raw model response export. */
(function(root){'use strict';
 const clone=x=>JSON.parse(JSON.stringify(x));
 const PATCH='2C1-F9.1';
 const KINDS=new Set(['SCHEMA_COMPLEXITY','SCHEMA_FIELD','SCHEMA_VALIDATION','GENERATION_PARAMETER','BILLING_OR_REGION','UNKNOWN']);
 const STATUSES=new Set(['INVALID_ARGUMENT','FAILED_PRECONDITION','UNAUTHENTICATED','PERMISSION_DENIED','NOT_FOUND','RESOURCE_EXHAUSTED','INTERNAL','UNAVAILABLE','DEADLINE_EXCEEDED','UNKNOWN']);
 const FIELDS=new Set(['generationConfig.responseJsonSchema','generationConfig.responseSchema','generationConfig.responseFormat','generationConfig.responseMimeType','generationConfig.maxOutputTokens','generationConfig.candidateCount','generationConfig.thinkingConfig','generationConfig.temperature','systemInstruction','contents','$defs','$ref','anyOf','oneOf','const','exclusiveMinimum','maxLength','minLength']);
 const PHASES=new Set(['preflight','interpretation','selection','interpretation_request','selection_request','interpretation_validation','semantic_validation','action_validation','preference_merge','retrieval','selection_validation','proposal_assembly','resolve_validation','time_merge_validation']);
 const OPS=new Set(['set_origin','set_finish','add_visit','replace_visit','remove_visit','move_visit','set_stay','set_finish_policy','set_mode','set_time','clear_visits']);
 const PATHS=new Set(['op','quote','place','target_id','target_position','position','minutes','value','mode','budget_minutes','departure_at','finish_by','clear_departure','buffer_minutes','commands','order','transport_mode','finish_policy','time_mode','semantic_segments','active_constraints','type','name','kind','effect','scope','hardness','status','source','turn_index',...OPS,'acknowledgement','preferred_add','preferred_remove','excluded_add','excluded_remove','notes_add','notes_remove','questions','actions','query','categories','keywords','picks','slot_id','candidate_key','reason','<unknown>','<root>']);
 const TYPES=new Set(['missing','extra_forbidden','int_type','int_parsing','string_type','string_too_long','string_too_short','bool_type','bool_parsing','list_type','dict_type','model_type','literal_error','too_long','too_short','value_error','greater_than','greater_than_equal','less_than','less_than_equal','finite_number','invalid_operation','irrelevant_field_value','unknown_field','target_out_of_range','target_conflict','invalid_target_type','invalid_structure','validation_error','unknown_command_field','mixed_envelope','invalid_command_order','duplicate_command_order','incomplete_command_order']);
 const integer=(v,min=0,max=10000000)=>Number.isInteger(v)&&v>=min&&v<=max?v:null;
 function safeValidation(v){
  if(!v||typeof v!=='object'||Array.isArray(v))return null;
  const issues=(Array.isArray(v.issues)?v.issues:[]).slice(0,12).filter(x=>x&&typeof x==='object').map(x=>({
   path:(Array.isArray(x.path)?x.path:[]).slice(0,8).map(p=>integer(p,0,10000)!==null?p:(PATHS.has(p)?p:'<unknown>')),
   type:TYPES.has(x.type)?x.type:'validation_error',operation:OPS.has(x.operation)?x.operation:null}));
  const count=integer(v.error_count,0,10000)??issues.length;
  return {issues,error_count:count,truncated:count>issues.length,raw_output_included:false,input_values_included:false,exception_text_included:false};
 }
 function safeNormalization(n){const r={};for(const k of ['neutral_fields_omitted','target_positions_resolved','scoped_commands_compiled']){const v=integer(n?.[k],0,1000);if(v!==null)r[k]=v;}return r;}
 function safeTimeUpdate(t){
  if(!t||typeof t!=='object'||Array.isArray(t))return null;
  const modes=new Set(['estimate','budget','window']),sources=new Set(['unchanged','explicit_mode','budget_value','finish_value']);
  const reasons=new Set(['conflicting_values','contradictory_fields','ambiguous_mode_switch','missing_budget','missing_departure','missing_finish','invalid_time']);
  const d={contract:'partial_time_patch_v1',time_command_count:integer(t.time_command_count,0,12),
   base_mode:modes.has(t.base_mode)?t.base_mode:null,result_mode:modes.has(t.result_mode)?t.result_mode:null,
   mode_source:sources.has(t.mode_source)?t.mode_source:null,failure_reason:reasons.has(t.failure_reason)?t.failure_reason:null,
   atomic:true,values_included:false};
  for(const k of ['budget_provided','departure_provided','finish_provided','clear_departure_requested','buffer_provided','mode_derived',
      'budget_present_after','departure_present_after','finish_present_after','final_validation_passed'])d[k]=typeof t[k]==='boolean'?t[k]:null;
  return d;
 }
 function safeErrorDetail(detail){
  if(!detail||typeof detail!=='object')return null;
  const p=detail.provider_diagnostic||{},r=detail.request_diagnostic||{},s=r.schema||{};
  const schema={};for(const k of ['json_bytes','references','union_nodes','schema_nodes','max_depth'])schema[k]=integer(s[k]);
  return {http_status:integer(detail.http_status,100,599),
   provider_status:STATUSES.has(p.provider_status)?p.provider_status:null,
   kind:KINDS.has(p.kind)?p.kind:null,
   field_hints:Array.isArray(p.field_hints)?p.field_hints.filter(x=>FIELDS.has(x)).slice(0,10):[],
   phase:PHASES.has(r.phase)?r.phase:null,
   wire_version:['2C1-F1','2C1-F2','2C1-F3','2C1-F4','2C1-F5','2C1-F6','2C1-F7','2C1-F8',PATCH].includes(r.wire_version)?r.wire_version:null,
   generation_attempts:integer(r.generation_attempts,0,2),
   completed_generation_responses:integer(r.completed_generation_responses,0,2),schema,
   validation:safeValidation(r.validation),normalization:safeNormalization(r.normalization),time_update:safeTimeUpdate(r.time_update),
   raw_provider_body_included:false,raw_request_included:false,raw_model_output_included:false};
 }
 function errorSummary(f){
  if(!f)return '';
  const phases={preflight:'Preflight check',interpretation:'Interpretation request',selection:'Candidate selection request',interpretation_request:'Interpretation request',selection_request:'Candidate selection request',interpretation_validation:'Model command format check',action_validation:'Edit target and evidence check',preference_merge:'Preference merge',retrieval:'Place retrieval',selection_validation:'Candidate selection check',proposal_assembly:'Draft assembly',resolve_validation:'Draft confirmation',time_merge_validation:'Time-condition merge check'};
  const kinds={unknown_command_field:'Unknown command parameter; it was not ignored',mixed_envelope:'Old and new command formats were mixed',invalid_command_order:'Command order must be a positive integer',duplicate_command_order:'Duplicate command order',incomplete_command_order:'Command order is not consecutive',missing:'Required information is missing',unknown_field:'Unknown field',extra_forbidden:'Field does not belong to this command',irrelevant_field_value:'Irrelevant field contains a value; it was not discarded',invalid_operation:'Unsupported command type',invalid_target_type:'Stop index must be an integer',target_out_of_range:'Stop index is outside the current list',target_conflict:'Stop index and place ID refer to different stops',int_type:'Expected an integer',int_parsing:'Invalid integer format',literal_error:'Value is outside the allowed set',list_type:'Expected a list',string_type:'Expected text',greater_than:'Value is below the minimum',less_than_equal:'Value exceeds the maximum'};
  const parts=[];if(phases[f.phase])parts.push('Failed stage: '+phases[f.phase]);
  for(const x of f.validation?.issues?.slice(0,3)||[]){
   let path='';for(const part of x.path)path+=typeof part==='number'?'['+part+']':(path?'.':'')+part;
   parts.push('Field '+(path||'<root>')+': '+(kinds[x.type]||x.type));
  }
  const reasons={conflicting_values:'Conflicting time values for the same field',contradictory_fields:'Time fields contradict each other',ambiguous_mode_switch:'Specify whether to replace or keep the previous time constraint',missing_budget:'An explicit budget is missing',missing_departure:'Departure time is missing',missing_finish:'Finish time is missing',invalid_time:'Invalid date, timezone or start/finish order'};
  if(reasons[f.time_update?.failure_reason])parts.push(reasons[f.time_update.failure_reason]);
  return parts.join('; ');
 }
 function recordFailure(metrics,detail){
  const count=integer(detail?.generation_attempts,0,2);
  if(count===null)metrics.failedUnknown=(metrics.failedUnknown||0)+1;
  else metrics.failedAttempts=(metrics.failedAttempts||0)+count;
 }
 const CATEGORIES=new Set(['nature','food','heritage','monument','tourism']);
 const RETRIEVAL_COUNTERS=['catalogue_rows','eligible_rows','after_exclusions','category_matches','name_matches','keyword_matches','returned_local_options','returned_options','generic_intent_terms_normalized','specific_keyword_count','input_keyword_count'];
 function safeRetrieval(value){
  if(!value||typeof value!=='object'||Array.isArray(value))return null;
  const d={};for(const key of RETRIEVAL_COUNTERS)d[key]=integer(value[key]);
  d.effective_categories=Array.isArray(value.effective_categories)?value.effective_categories.filter(x=>CATEGORIES.has(x)).slice(0,5):[];
  for(const key of ['explicit_query_present','generic_query_normalized','geocoder_attempted','specific_requirements_relaxed','held_name_match'])d[key]=typeof value[key]==='boolean'?value[key]:null;
  return d;
 }
 function proposalDiagnostic(proposal,choices){
  if(!proposal||!Array.isArray(proposal.slots))return null;
  const slots=proposal.slots.slice(0,8).map((slot,index)=>{
   const options=Array.isArray(slot.options)?slot.options:[];
   const key=choices===undefined?slot.selected_key:choices[slot.slot_id];
   return {slot_number:index+1,operation:OPS.has(slot.operation)?slot.operation:null,
    categories:(slot.categories||[]).filter(x=>CATEGORIES.has(x)).slice(0,5),
    option_count:options.length,selection_resolved:options.some(o=>o.candidate_key===key),
    retrieval:safeRetrieval(slot.retrieval_diagnostic)};
  });
  return {slot_count:slots.length,empty_slot_count:slots.filter(s=>s.option_count===0).length,
   unresolved_slot_count:slots.filter(s=>!s.selection_resolved).length,
   all_slots_selected:slots.every(s=>s.selection_resolved),slots,
   time_update:safeTimeUpdate(proposal.time_update||proposal.preview?.time_update),
   raw_query_included:false,keywords_included:false,place_names_included:false,coordinates_included:false};
 }
 function adoptionBlocked(proposal){
  const preview=proposal?.preview;
  return Array.isArray(preview?.adoption_blockers)?preview.adoption_blockers.length>0:preview?.can_apply!==true;
 }
 function readinessText(proposal,choices){
  const d=proposalDiagnostic(proposal,choices);if(!d)return '';
  const labels={nature:'Parks / nature',food:'Food and drink',heritage:'Historic places',monument:'Monuments',tourism:'Attractions / shopping / entertainment'};
  const name=s=>'Item '+s.slot_number+' ('+(s.categories.map(c=>labels[c]).join('/')||'Place')+')';
  const empty=d.slots.filter(s=>s.option_count===0);
  if(empty.length)return 'Cannot adopt yet. No candidates for '+empty.map(name).join(', ')+'. Places already found remain a draft; missing items will not be removed to enable confirmation. Give a specific place or explicitly revise that requirement.';
  const unresolved=d.slots.filter(s=>!s.selection_resolved);
  if(unresolved.length)return 'Confirm a place for: '+unresolved.map(name).join(', ')+'. Once selected, the full draft can be checked.';
  if(adoptionBlocked(proposal))return 'Cannot adopt yet: conflicts or missing execution details remain. Review the messages below. The original itinerary and adopted requirements are unchanged.';
  return d.slot_count?'Candidates selected but not applied. Adopt the draft to calculate routes and estimated duration.':'Changes have not been applied. Review and adopt the draft to continue.';
 }
 function safeProposalDiagnostic(d){
  if(!d||typeof d!=='object'||!Array.isArray(d.slots))return null;
  const slots=d.slots.slice(0,8).map((slot,index)=>({slot_number:index+1,
   operation:OPS.has(slot.operation)?slot.operation:null,
   categories:(Array.isArray(slot.categories)?slot.categories:[]).filter(x=>CATEGORIES.has(x)).slice(0,5),
   option_count:integer(slot.option_count,0,100),selection_resolved:slot.selection_resolved===true,
   retrieval:safeRetrieval(slot.retrieval)}));
  return {slot_count:slots.length,empty_slot_count:slots.filter(s=>s.option_count===0).length,
   unresolved_slot_count:slots.filter(s=>!s.selection_resolved).length,
   all_slots_selected:slots.every(s=>s.selection_resolved),slots,time_update:safeTimeUpdate(d.time_update),
   raw_query_included:false,keywords_included:false,place_names_included:false,coordinates_included:false};
 }
 class Gate{
  constructor(){this.sequence=0;this.pending=null;}
  begin(revision){this.pending=null;return {sequence:++this.sequence,revision};}
  accepts(ticket,revision,result){return ticket.sequence===this.sequence&&ticket.revision===revision&&result.base_revision===revision;}
  invalidate(){this.sequence++;this.pending=null;}
 }
 function diagnose(metrics,plan,activeProposal=null,choices){
  const f=metrics.lastFailure;
  const failure=f?safeErrorDetail({http_status:f.http_status,
    provider_diagnostic:{kind:f.kind,provider_status:f.provider_status,field_hints:f.field_hints},
    request_diagnostic:{phase:f.phase,wire_version:f.wire_version,generation_attempts:f.generation_attempts,
     completed_generation_responses:f.completed_generation_responses,schema:f.schema,
     validation:f.validation,normalization:f.normalization,time_update:f.time_update}}):null;
  const unknown=integer(metrics.failedUnknown)||0,known=integer(metrics.failedAttempts)||0;
  const safe={stage:'2C1',schema_version:6,patch:PATCH,exported_at:new Date().toISOString(),model:metrics.model||null,
   prompt_version:PATCH,completed_turns:metrics.completed||0,failed_turns:metrics.failed||0,
   model_generation_requests:metrics.calls||0,model_generation_requests_scope:'completed_turns_only',
   failed_turn_generation_attempts:unknown?null:known,
   failed_turn_generation_attempts_known:known,failed_turn_attempt_count_unknown:unknown,
   request_counts_are_not_billed_usage:true,
   confirmed_proposals:metrics.applied||0,cancelled_or_stale:metrics.cancelled||0,last_error_code:metrics.error||null,
   last_failure:failure,last_failure_is_current_error:!!metrics.error,
   operation_adaptation:safeNormalization(metrics.lastAdaptation),
   proposal_active:!!activeProposal,
   latest_proposal:activeProposal?proposalDiagnostic(activeProposal,choices):safeProposalDiagnostic(metrics.lastProposal),
   tokens:clone(metrics.tokens||{}),token_counts_scope:'completed_turns_only',itinerary:plan,raw_conversation_included:false,
   coordinates_included:false,place_labels_included:false,credentials_included:false,
   training:false,provider_switch:false,source_catalogue_changed:false};
  if(safe.itinerary)safe.itinerary={...safe.itinerary,llm:safe.confirmed_proposals>0};
  return safe;
 }
 /* Reconcile only explicit editor changes with matching adopted parameters.
    The backend remains responsible for enforcing every hard requirement. */
 function reconcileManualPreferences(preferences,before,after){
  const result=clone(preferences);
  if(!before||!after)return result;
  const globalFields={budget_minutes:d=>d.time?.budget_minutes,
   departure_at:d=>d.time?.departure_at,finish_by:d=>d.time?.finish_by,
   transport_mode:d=>d.mode,travel_mode:d=>d.mode,finish_policy:d=>d.finish_policy,
   max_walk_minutes:d=>d.max_walk_minutes};
  const labels={budget_minutes:'total time budget',departure_at:'departure time',finish_by:'finish time',
   transport_mode:'transport mode',travel_mode:'transport mode',finish_policy:'finish policy',
   max_walk_minutes:'per-leg walking limit',stay_minutes:'stay duration',takeaway:'takeaway profile'};
  const same=(name,a,b)=>{
   if(a==null||b==null)return a==null&&b==null;
   if(name==='departure_at'||name==='finish_by'){
    const x=Date.parse(a),y=Date.parse(b);return Number.isFinite(x)&&Number.isFinite(y)&&x===y;
   }
   return a===b;
  };
  result.active_constraints=(result.active_constraints||[]).flatMap(row=>{
   let oldValue,newValue,managed=false;
   const ordinal=/^visit:([1-9][0-9]*)$/.exec(row.scope);
   const oldVisit=ordinal?before.visits?.[Number(ordinal[1])-1]
    :row.scope?.startsWith('visit_id:')?before.visits?.find(v=>v.visit_id===row.scope.slice(9)):null;
   if(oldVisit){
    const newVisit=after.visits?.find(v=>v.visit_id===oldVisit.visit_id);
    if(!newVisit)return []; // Removing a stop also removes requirements scoped to it.
    if(ordinal)row.scope='visit_id:'+oldVisit.visit_id;
    if(row.name==='stay_minutes'){
     oldValue=oldVisit.stay_minutes;newValue=newVisit.stay_minutes;managed=true;
    }else if(row.name==='takeaway'){
     oldValue=oldVisit.stay_profile==='takeaway';newValue=newVisit.stay_profile==='takeaway';managed=true;
    }
   }else if(row.scope==='global'&&Object.hasOwn(globalFields,row.name)){
    oldValue=globalFields[row.name](before);newValue=globalFields[row.name](after);managed=true;
   }
   if(!managed||same(row.name,oldValue,newValue))return [row];
   if(newValue==null)return []; // The user explicitly cleared this editor parameter.
   const unit=['budget_minutes','max_walk_minutes','stay_minutes'].includes(row.name)?' min':'';
   return [{...row,value:newValue,status:'understood',source:'user_explicit',evidence_source:null,
    quote:'Updated in itinerary editor: '+labels[row.name]+' = '+String(newValue)+unit+'.'}];
  });
  return result;
 }
 /* In-page conversation log. Memory only: never written to storage, never sent
    anywhere, gone on reload, and not shared with teammates. Opening an older
    thread never restores the itinerary that produced it; resuming only appends
    messages, because a thread holds words, not a verified plan. */
 class ConversationLog{
  constructor(limit=20,messageLimit=200){
   this.limit=Math.max(1,limit);this.messageLimit=Math.max(1,messageLimit);
   this.threads=[];this.activeId=null;this.viewingId=null;this.seq=0;this.start();
  }
  start(){
   this.threads=this.threads.filter(t=>t.messages.length);   // never keep blank threads
   const thread={id:'c'+(++this.seq),title:null,messages:[]};
   this.threads.unshift(thread);
   while(this.threads.length>this.limit)this.threads.pop();
   this.activeId=thread.id;this.viewingId=thread.id;return thread;
  }
  get(id){return this.threads.find(t=>t.id===id)||null;}
  active(){return this.get(this.activeId)||this.start();}
  viewing(){return this.get(this.viewingId)||this.active();}
  isViewingActive(){return this.viewingId===this.activeId;}
  add(role,text){
   const t=this.active();
   t.messages.push({role,text:String(text)});
   while(t.messages.length>this.messageLimit)t.messages.shift();
   if(t.title===null&&role==='user')t.title=String(text).replace(/\s+/g,' ').trim().slice(0,40);
   return t;
  }
  view(id){const t=this.get(id);if(t)this.viewingId=id;return t;}
  /* Opening an older thread RESUMES it: the user keeps talking in that thread
     rather than only reading it. New lines append where they left off. */
  activate(id){const t=this.get(id);if(!t)return null;this.activeId=id;this.viewingId=id;return t;}
  resume(){this.viewingId=this.activeId;return this.active();}
  list(){return this.threads.map(t=>({id:t.id,title:t.title||' (not sent)',
   count:t.messages.length,active:t.id===this.activeId,viewing:t.id===this.viewingId}));}
  clear(){this.threads=[];this.seq=0;return this.start();}
 }
 const exports={PATCH,Gate,diagnose,safeErrorDetail,safeValidation,safeTimeUpdate,errorSummary,recordFailure,safeRetrieval,proposalDiagnostic,adoptionBlocked,readinessText,ConversationLog,reconcileManualPreferences};root.PlayMapChatState=exports;
 if(typeof module!=='undefined'&&module.exports)module.exports=exports;
})(typeof window==='undefined'?globalThis:window);
