/* Serializable itinerary commands, usable by future language tools and present UI alike. */
(function(root){
 'use strict';
 const copy=x=>JSON.parse(JSON.stringify(x));
 const MAX_VISITS=20;
 function makeId(){
  const bytes=new Uint8Array(16);root.crypto.getRandomValues(bytes);
  return Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
 }
 function empty(){return {origin:null,visits:[],finish_policy:'last_stop',finish:null,mode:'walk',
  time:{mode:'estimate',departure_at:null,finish_by:null,budget_minutes:null,buffer_minutes:0}};}
 function validPoint(p){
  if(!p||p.confirmed!==true||!Number.isFinite(p.latitude)||!Number.isFinite(p.longitude)||p.latitude<.9||p.latitude>1.7||p.longitude<103.4||p.longitude>104.7)throw Error('请确认有效的新加坡通行点与经纬度顺序。');
  if(!p.label||typeof p.label!=='string')throw Error('地点需要名称或选点说明。');
  return copy(p);
 }
 function singaporeISO(value){
  if(!value)return null;
  if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value))throw Error('请输入完整的新加坡日期与时刻。');
  const iso=value+'+08:00';if(!Number.isFinite(Date.parse(iso)))throw Error('日期或时刻无效。');return iso;
 }
 function integer(value,label,{min=0,max=10080,nullable=false}={}){
  if(value===''||value===null||value===undefined){if(nullable)return null;throw Error(label+'不能为空。');}
  if(!/^\d+$/.test(String(value)))throw Error(label+'必须为整数。');
  const n=Number(value);if(!Number.isSafeInteger(n)||n<min||n>max)throw Error(label+'超出允许范围。');return n;
 }
 class ItineraryState{
  constructor(sessionId=makeId()){this.sessionId=sessionId;this.data=empty();this.revision=0;this.sequence=0;this.history=[];this.lastSuccess=null;}
  change(fn){const next=copy(this.data);fn(next);this.history.push(copy(this.data));if(this.history.length>20)this.history.shift();this.data=next;this.revision++;this.sequence++;}
  setOrigin(p){this.change(d=>{d.origin=validPoint(p);});}
  setFinish(p){this.change(d=>{d.finish=validPoint(p);d.finish_policy='custom';});}
  setFinishPolicy(policy){if(!['last_stop','return_to_start','custom'].includes(policy))throw Error('结束方式无效。');this.change(d=>{d.finish_policy=policy;});}
  addVisit(p,id=makeId()){
   if(this.data.visits.length>=MAX_VISITS)throw Error('当前一条请求最多20个停留点；全岛目录没有裁剪。');
   this.change(d=>{if(d.visits.some(v=>v.visit_id===id))throw Error('重复的停留ID。');d.visits.push({visit_id:id,point:validPoint(p),stay_minutes:null,stay_profile:'regular'});});return id;
  }
  replaceVisit(id,p){this.change(d=>{const v=d.visits.find(v=>v.visit_id===id);if(!v)throw Error('停留点已不存在。');v.point=validPoint(p);});}
  removeVisit(id){this.change(d=>{if(!d.visits.some(v=>v.visit_id===id))throw Error('停留点已不存在。');d.visits=d.visits.filter(v=>v.visit_id!==id);});}
  moveVisit(id,delta){this.change(d=>{const i=d.visits.findIndex(v=>v.visit_id===id),j=i+delta;if(i<0||j<0||j>=d.visits.length)throw Error('无法继续移动。');const [v]=d.visits.splice(i,1);d.visits.splice(j,0,v);});}
  setStay(id,minutes,profile){this.change(d=>{const v=d.visits.find(v=>v.visit_id===id);if(!v)throw Error('停留点已不存在。');if(minutes!==null&&(!Number.isInteger(minutes)||minutes<0||minutes>1440))throw Error('停留时间需要0—1440的整数或留空。');if(!['quick','regular','extended'].includes(profile))throw Error('玩法无效。');v.stay_minutes=minutes;v.stay_profile=profile;});}
  setMode(mode){if(!['walk','drive','cycle'].includes(mode))throw Error('未接入该交通方式。');this.change(d=>{d.mode=mode;});}
  setTime(t){this.change(d=>{d.time=copy(t);});}
  undo(){if(!this.history.length)return false;this.data=this.history.pop();this.revision++;this.sequence++;return true;}
  clear(){this.data=empty();this.revision++;this.sequence++;this.history=[];this.lastSuccess=null;}
  validate(){
   if(!this.data.origin)throw Error('先选择出发点。');if(!this.data.visits.length)throw Error('至少加入一个停留点。');
   if(this.data.finish_policy==='custom'&&!this.data.finish)throw Error('请明确选择最后结束的位置。');
   const t=this.data.time;
   if(t.mode==='budget'&&(!Number.isInteger(t.budget_minutes)||t.budget_minutes<=0))throw Error('请输入总时长预算。');
   if(t.mode==='window'&&(!t.departure_at||!t.finish_by||Date.parse(t.finish_by)<=Date.parse(t.departure_at)))throw Error('请填写起止日期与时间；跨午夜显式选择下一天。');
  }
  begin(force=false){this.validate();return {sequence:++this.sequence,revision:this.revision,payload:{session_id:this.sessionId,revision:this.revision,...copy(this.data),finish:this.data.finish_policy==='custom'?copy(this.data.finish):null,force_refresh:force}};}
  accepts(ticket,result){return ticket.sequence===this.sequence&&ticket.revision===this.revision&&result.revision===this.revision&&result.mode===this.data.mode&&result.complete_plan_created===true&&result.visits.map(v=>v.visit_id).join('|')===this.data.visits.map(v=>v.visit_id).join('|');}
  save(ticket,result){if(!this.accepts(ticket,result))return false;this.lastSuccess=result;return true;}
  cancel(){this.sequence++;}
 }
 function minutes(s){return (s/60).toFixed(1)+' 分钟';}
 function duration(s){const m=Math.round(s/60);return m<60?m+'分钟':Math.floor(m/60)+'小时'+(m%60?m%60+'分':'');}
 function axisLabel(row,end=false){
  const t=end?row.end_at:row.start_at,offset=end?row.end_offset_s:row.start_offset_s;
  if(t){const date=new Date(t);return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Singapore',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(date);}
  const m=Math.round(offset/60),days=Math.floor(m/1440),hh=Math.floor(m%1440/60),mm=m%60;
  return (days?`+${days}天 `:'+')+String(hh).padStart(2,'0')+':'+String(mm).padStart(2,'0');
 }
 function diagnostic(plan,currentRevision){
  return {stage:'2B',schema_version:1,exported_at:new Date().toISOString(),
   current_revision:currentRevision,plan_revision:plan?.revision??null,current_plan:!!plan&&plan.revision===currentRevision,
   mode:plan?.mode??null,visit_count:plan?.visits.length??0,leg_count:plan?.legs.length??0,
   finish_policy:plan?.finish_policy??null,time_mode:plan?.time.mode??null,
   totals:plan?{distance_m:plan.totals.distance_m,travel_s:plan.totals.travel_s,stay_s:plan.totals.stay_s,buffer_s:plan.totals.buffer_s,reference_duration_s:plan.totals.reference_duration_s,duration_range_s:plan.totals.duration_range_s}:null,
   budget_check:plan?.budget_check??null,route_calls:plan?.route_calls??null,
   legs:plan?plan.legs.map(l=>({index:l.index,kind:l.kind,mode:l.mode,distance_m:l.distance_m,duration_s:l.duration_s,endpoint_offsets_m:l.endpoint_offsets_m,diagnostics:l.diagnostics,reused_in_memory:l.reused_in_memory})):[],
   entrances_verified:false,opening_hours_checked:false,order_optimisation:false,llm:false,
   exact_coordinates_included:false,labels_included:false,clock_times_included:false,credentials_included:false,uploaded_automatically:false};
 }
 const out={ItineraryState,MAX_VISITS,makeId,singaporeISO,integer,minutes,duration,axisLabel,diagnostic};
 root.PlayMapTrip=out;if(typeof module!=='undefined'&&module.exports)module.exports=out;
})(typeof window==='undefined'?globalThis:window);
