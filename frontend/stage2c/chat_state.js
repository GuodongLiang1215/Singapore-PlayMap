/* Revision gate and value-free failure diagnostics. No raw model response export. */
(function(root){'use strict';
 const clone=x=>JSON.parse(JSON.stringify(x));
 const PATCH='2C1-F5';
 const KINDS=new Set(['SCHEMA_COMPLEXITY','SCHEMA_FIELD','SCHEMA_VALIDATION','GENERATION_PARAMETER','BILLING_OR_REGION','UNKNOWN']);
 const STATUSES=new Set(['INVALID_ARGUMENT','FAILED_PRECONDITION','UNAUTHENTICATED','PERMISSION_DENIED','NOT_FOUND','RESOURCE_EXHAUSTED','INTERNAL','UNAVAILABLE','DEADLINE_EXCEEDED','UNKNOWN']);
 const FIELDS=new Set(['generationConfig.responseJsonSchema','generationConfig.responseSchema','generationConfig.responseFormat','generationConfig.responseMimeType','generationConfig.maxOutputTokens','generationConfig.candidateCount','generationConfig.thinkingConfig','generationConfig.temperature','systemInstruction','contents','$defs','$ref','anyOf','oneOf','const','exclusiveMinimum','maxLength','minLength']);
 const PHASES=new Set(['preflight','interpretation','selection','interpretation_request','selection_request','interpretation_validation','action_validation','preference_merge','retrieval','selection_validation','proposal_assembly','resolve_validation','time_merge_validation']);
 const OPS=new Set(['set_origin','set_finish','add_visit','replace_visit','remove_visit','move_visit','set_stay','set_finish_policy','set_mode','set_time','clear_visits']);
 const PATHS=new Set(['op','quote','place','target_id','target_position','position','minutes','value','mode','budget_minutes','departure_at','finish_by','clear_departure','buffer_minutes','commands','order','transport_mode','finish_policy','time_mode',...OPS,'acknowledgement','preferred_add','preferred_remove','excluded_add','excluded_remove','notes_add','notes_remove','questions','actions','query','categories','keywords','picks','slot_id','candidate_key','reason','<unknown>','<root>']);
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
   wire_version:['2C1-F1','2C1-F2','2C1-F3','2C1-F4',PATCH].includes(r.wire_version)?r.wire_version:null,
   generation_attempts:integer(r.generation_attempts,0,2),
   completed_generation_responses:integer(r.completed_generation_responses,0,2),schema,
   validation:safeValidation(r.validation),normalization:safeNormalization(r.normalization),time_update:safeTimeUpdate(r.time_update),
   raw_provider_body_included:false,raw_request_included:false,raw_model_output_included:false};
 }
 function errorSummary(f){
  if(!f)return '';
  const phases={preflight:'发送前检查',interpretation:'需求解析请求',selection:'候选选择请求',interpretation_request:'需求解析请求',selection_request:'候选选择请求',interpretation_validation:'模型操作格式检查',action_validation:'修改对象及依据检查',preference_merge:'偏好合并',retrieval:'地点检索',selection_validation:'候选选择检查',proposal_assembly:'草案组装',resolve_validation:'草案确认',time_merge_validation:'时间条件合并检查'};
  const kinds={unknown_command_field:'此操作出现未定义参数，未忽略',mixed_envelope:'新旧操作格式混用',invalid_command_order:'操作顺序须为正整数',duplicate_command_order:'操作顺序重复',incomplete_command_order:'操作顺序不连续',missing:'缺少必填信息',unknown_field:'出现未定义字段',extra_forbidden:'出现不属于此操作的字段',irrelevant_field_value:'无关字段携带了实际值，未丢弃',invalid_operation:'操作类型不支持',invalid_target_type:'站点序号必须为整数',target_out_of_range:'站点序号超出现有清单',target_conflict:'序号与地点ID指向不同站点',int_type:'应为整数',int_parsing:'整数格式错误',literal_error:'取值不在允许范围',list_type:'应为列表',string_type:'应为文字',greater_than:'数值不满足下限',less_than_equal:'数值超过上限'};
  const parts=[];if(phases[f.phase])parts.push('失败阶段：'+phases[f.phase]);
  for(const x of f.validation?.issues?.slice(0,3)||[]){
   let path='';for(const part of x.path)path+=typeof part==='number'?'['+part+']':(path?'.':'')+part;
   parts.push('字段 '+(path||'<root>')+'：'+(kinds[x.type]||x.type));
  }
  const reasons={conflicting_values:'同一字段存在不同的时间要求',contradictory_fields:'时间字段之间互相矛盾',ambiguous_mode_switch:'需明确替换还是保留原时间约束',missing_budget:'缺少明确预算',missing_departure:'缺少出发时刻',missing_finish:'缺少结束时刻',invalid_time:'日期、时区或起止顺序不合法'};
  if(reasons[f.time_update?.failure_reason])parts.push(reasons[f.time_update.failure_reason]);
  return parts.join('；');
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
 function readinessText(proposal,choices){
  const d=proposalDiagnostic(proposal,choices);if(!d)return '';
  const labels={nature:'公园/自然',food:'餐饮',heritage:'历史地点',monument:'古迹',tourism:'景点'};
  const name=s=>'第'+s.slot_number+'项（'+(s.categories.map(c=>labels[c]).join('/')||'地点')+'）';
  const empty=d.slots.filter(s=>s.option_count===0);
  if(empty.length)return '暂不能采用：'+empty.map(name).join('、')+'没有可用候选。已找到的地点仍只是草案；不会删除未满足的项目来启用按钮。可继续说明具体地点，或明确调整该项要求。';
  const unresolved=d.slots.filter(s=>!s.selection_resolved);
  if(unresolved.length)return '还需确认：'+unresolved.map(name).join('、')+'的具体位置。选好后即可提交整份草案检查。';
  return d.slot_count?'各项候选已选定，尚未应用；采用后再计算路线与参考时长。':'条件修改草案尚未应用，请核对后采用。';
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
 /* In-page conversation log. Memory only: never written to storage, never sent
    anywhere, gone on reload, and not shared with teammates. Viewing an older
    thread is READ-ONLY and never restores or alters the itinerary that produced
    it, because the thread holds words, not a verified plan. */
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
  list(){return this.threads.map(t=>({id:t.id,title:t.title||'（尚未发送）',
   count:t.messages.length,active:t.id===this.activeId,viewing:t.id===this.viewingId}));}
  clear(){this.threads=[];this.seq=0;return this.start();}
 }
 const exports={PATCH,Gate,diagnose,safeErrorDetail,safeValidation,safeTimeUpdate,errorSummary,recordFailure,safeRetrieval,proposalDiagnostic,readinessText,ConversationLog};root.PlayMapChatState=exports;
 if(typeof module!=='undefined'&&module.exports)module.exports=exports;
})(typeof window==='undefined'?globalThis:window);
