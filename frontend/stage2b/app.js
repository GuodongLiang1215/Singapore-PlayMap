'use strict';
const $=id=>document.getElementById(id),T=PlayMapTrip,trip=new T.ItineraryState();
const CATEGORY={nature:'Nature and parks',tourism:'Attractions, shopping and entertainment',heritage:'Historic places',monument:'Monuments',food:'Food and drink'};
const MODE={walk:'Walk',cycle:'Cycle',drive:'Drive'};
// A device reading must never be described as a point the user identified.
const SOURCE_LABEL={catalogue_representative:'Source representative point',device_location:'Device location (with uncertainty)',
 onemap_search:'Confirmed candidate',user_map:'Confirmed candidate',user_coordinates:'Confirmed candidate'};
const STAYS={nature:[30,60,120],tourism:[30,60,90],heritage:[10,20,40],monument:[15,30,60],food:[20,40,60],unknown:[15,30,60]};
let all=[],profile=null,matched=[],listLimit=30,filterTimer=null,autoTimer=null,planController=null;
let selected=null,replacement=null,searchSequence=0,searchController=null,nextSearch=null,searchText='',detailSequence=0,formInvalid=false;
const mapView=new PlayMapTripMap({onFeature:f=>selectFeature(f),onMapPoint:p=>selectPlace(p,{})});
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function button(text,handler,cls='quiet'){const b=node('button',text,cls);b.type='button';b.onclick=handler;return b;}
function reportError(e){$('input-error').hidden=!e;$('input-error').textContent=e?e.message||String(e):'';}
async function api(url,body,signal){const opt={cache:'no-store',signal};if(body!==undefined){opt.method='POST';opt.headers={'Content-Type':'application/json','X-PlayMap-Client':url.startsWith('/api/stage2b/')?'stage2b':'stage2a'};opt.body=JSON.stringify(body);}const r=await fetch(url,opt);let d;try{d=await r.json();}catch{throw Error('The server did not return JSON. Check that the application is running.');}if(!r.ok){const x=d.detail,e=Error(typeof x==='string'?x:(x?.message||'Request failed')+(x?.code?' ['+x.code+']':''));e.code=x?.code;throw e;}return d;}
function tab(name){for(const x of ['local','search','map']){$(x==='local'?'local-pane':x+'-pane').hidden=x!==name;$('tab-'+x).classList.toggle('selected',x===name);}}
async function refreshStatus(){try{const r=await api('/api/stage2b/status');$('credential-status').textContent=r.credentials.configured?'Token configured · Validity depends on the provider response':'Token not configured. Check the OneMap credentials on the server.';$('catalogue-status').textContent=r.catalogue.ready?`${r.catalogue.entity_count} place groups across Singapore · ${r.catalogue.default_exploration_count} shown by default · Not a count of verified attractions`:'Catalogue unavailable. Search and map selection still work.';}catch(e){$('credential-status').textContent=e.message;}}
function invalidate(){
 clearTimeout(autoTimer);if(planController){planController.abort();planController=null;}$('cancel-calculation').disabled=true;
 $('export-report').disabled=true;$('export-status').textContent='';$('undo').disabled=!trip.history.length;
 mapView.setDraft(trip.data);
 if(trip.lastSuccess){mapView.markStale();$('result-body').classList.add('is-stale');$('plan-status').className='stale';$('plan-status').textContent='Stops or conditions changed. The faded route and timeline belong to the previous result.';$('plan-version').textContent='Recalculation needed · Revision '+trip.revision;}
 else {$('plan-status').className='';$('plan-status').textContent='Stops updated. Select Calculate trip.';$('plan-version').textContent='Revision '+trip.revision;}
 if(trip.lastSuccess&&$('auto-plan').checked&&!formInvalid){try{trip.validate();autoTimer=setTimeout(()=>calculate(false),550);}catch{}}
}
function edit(fn){try{syncEditor();fn();reportError(null);renderTrip();invalidate();}catch(e){
 // A blocked command must not look adopted, or erase another invalid field.
 $('mode').value=trip.data.mode;$('finish-policy').value=trip.data.finish_policy;
 if(formInvalid){trip.cancel();invalidate();}reportError(e);
}}
function beginReplacement(target){replacement=target;const label=target==='origin'?'start point':target==='finish'?'finish point':'stop '+(trip.data.visits.findIndex(v=>v.visit_id===target)+1)+'';$('replacement-notice').hidden=false;$('replacement-notice').replaceChildren(node('p','Choose a new '+label+'. Other conditions and stay durations are preserved.'),button('Cancel replacement',cancelReplacement));renderSelected();$('selected-place').scrollIntoView({block:'nearest'});}
function cancelReplacement(){replacement=null;$('replacement-notice').hidden=true;renderSelected();}
function selectPlace(point,meta){selected={point,meta};renderSelected();reportError(null);}
function renderSelected(){
 const box=$('selected-place');box.hidden=!selected;if(!selected)return;const {point:p,meta}=selected;
 // Dismissable: picking a place on the map should not pin a card open forever.
 const close=node('button','×','place-close');close.type='button';close.title='Close';
 close.onclick=()=>{selected=null;cancelReplacement();renderSelected();};
 box.replaceChildren(close,node('h3',p.label),node('p',meta.address||'No detailed address','small'),node('p',`${p.latitude.toFixed(6)}, ${p.longitude.toFixed(6)} · ${SOURCE_LABEL[p.source]||'Confirmed candidate'}, not a verified entrance.`,'place-source'));
 if(meta.hold)box.append(node('p','Former location or status awaiting review. Find the current location before adding it.','warning'));
 const actions=node('div',undefined,'place-actions');
 const use=target=>{if(meta.hold)return;try{
  syncEditor();
  if(p.source==='catalogue_representative'&&!window.confirm('Use this source representative coordinate? It may be inside a park or building and is not a verified entrance.'))return;
  if(target==='origin')trip.setOrigin(p);else if(target==='finish')trip.setFinish(p);else if(target==='add'){
   if(trip.data.visits.some(v=>v.point.latitude===p.latitude&&v.point.longitude===p.longitude)&&!window.confirm('This coordinate is already in the itinerary. Add another visit here?'))return;
   trip.addVisit(p);
  }else trip.replaceVisit(target,p);
  cancelReplacement();reportError(null);renderTrip();invalidate();mapView.setPick(false);
 }catch(e){reportError(e);}};
 if(!meta.hold){if(replacement)actions.append(button('Replace selected item',()=>use(replacement),'replace-action'));actions.append(button('Set as start',()=>use('origin')),button('Add a stop',()=>use('add')),button('Set as finish',()=>use('finish')));}
 const lookup=button('Find an access point',()=>{tab('search');$('search-query').value=meta.searchQuery||p.label;search(1);});actions.append(lookup,button('View on map',()=>mapView.focusPoint(p)));box.append(actions);
 if(meta.category)box.append(node('p','Category: '+(CATEGORY[meta.category]||meta.category),'small'));
 if(meta.detail){if(meta.detail.opening_hours_display)box.append(node('p','Opening hours: '+meta.detail.opening_hours_display+(meta.detail.last_admission?' (last admission '+meta.detail.last_admission+')':''),'small'));for(const review of meta.detail.legacy_reviews||[])box.append(node('p',review.note_zh,'small'));box.append(node('p',`Regular-hours evidence records: ${meta.detail.regular_hours_evidence?.length||0}. Only safely parsed hours can be checked against the schedule.`,'small'));}
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
 $('list-count').textContent=`${matched.length} matching groups. All matching points can be shown on the map.`;
 const box=$('catalogue-list');box.replaceChildren();for(const f of matched.slice(0,listLimit)){const p=f.properties,b=button(p.display_title,()=>selectFeature(f),'catalogue-item');b.append(node('small',p.categories.map(c=>CATEGORY[c]||c).join(' / ')+(p.default_exploration_visible?'':' · Needs review')));box.append(b);}
 if(!matched.length)box.append(node('p','No local catalogue match. Try OneMap search.','small'));
 $('catalogue-more').hidden=listLimit>=matched.length;mapView.setCatalogue(matched,$('show-pois').checked);
}
async function loadCatalogue(){try{profile=await api('/api/stage1c/status');let offset=0,expected=null,items=[];do{const d=await api('/api/stage1c/features?view=all&limit=1000&offset='+offset+'&build_id='+encodeURIComponent(profile.build_id));if(expected===null)expected=d.matched_total;if(expected!==d.matched_total||d.build_id!==profile.build_id)throw Error('Catalogue version changed. Refresh the page.');items.push(...d.features);if(d.next_offset!==null&&d.next_offset<=offset)throw Error('Catalogue pagination did not advance.');offset=d.next_offset;}while(offset!==null);if(items.length!==expected)throw Error('The Singapore-wide catalogue did not load completely.');all=items;renderCatalogue();}catch(e){$('list-count').textContent=e.message;}}
function resetSearch(){searchSequence++;if(searchController)searchController.abort();searchController=null;nextSearch=null;$('search-more').hidden=true;$('search-results').replaceChildren();}
async function search(page=1){
 const query=$('search-query').value.trim();if(!query)return reportError(Error('Enter a place, building or postal code.'));
 if(searchController)searchController.abort();const controller=searchController=new AbortController(),seq=++searchSequence;searchText=query;nextSearch=null;$('search-more').hidden=true;$('search-results').replaceChildren(node('p','Searching…','small'));
 try{const d=await api('/api/stage2a/search',{query,page},controller.signal);if(seq!==searchSequence||query!==$('search-query').value.trim())return;const box=$('search-results');box.replaceChildren(node('p',`Page ${d.page}/${d.total_pages||1} · Matches: ${d.provider_found}. Choose a result to use it.`,'small'));for(const p of d.results){const b=button(p.label,()=>{detailSequence++;selectPlace({latitude:p.latitude,longitude:p.longitude,label:p.label.slice(0,200),source:'onemap_search',confirmed:true},{address:p.address});},'result');b.append(node('small',p.address));box.append(b);}if(!d.results.length)box.append(node('p','No usable candidates on this page. Try another keyword or select a point manually.','small'));nextSearch=d.next_page;$('search-more').hidden=nextSearch===null;}
 catch(e){if(e.name!=='AbortError'&&seq===searchSequence)$('search-results').replaceChildren(node('p',e.message,'error'));}
}
function stayPreview(v){
 if(v.stay_minutes!==null)return `Custom stay: ${v.stay_minutes} min`;
 const f=all.find(x=>x.properties.entity_id===v.point.entity_id),category=f?.properties.category||'unknown';const sd=f?.properties.suggested_duration;if(sd){if(v.stay_profile==='takeaway'&&sd.takeaway)return `Takeaway: ${sd.takeaway} min; editable.`;const m={takeaway:sd.short,quick:sd.short,regular:sd.regular,extended:sd.long}[v.stay_profile];return `Suggested: ${m} min, range ${sd.short}–${sd.long}; editable.`;}const base=STAYS[category]||STAYS.unknown,k={takeaway:.5,quick:.5,regular:1,extended:1.5}[v.stay_profile];return `Initial assumption: ${Math.floor(base[1]*k)} min, range ${Math.floor(base[0]*k)}–${Math.floor(base[2]*k)}; editable, not measured.`;
}
function renderTrip(){
 const d=trip.data;$('origin-summary').textContent=d.origin?d.origin.label+' · '+(SOURCE_LABEL[d.origin.source]||'Confirmed point')+', not a verified entrance':'Not selected';$('visit-count').textContent=`Stops: ${d.visits.length} · Order stays as you set it`;
 const box=$('visits');box.replaceChildren();d.visits.forEach((v,i)=>{
  const card=node('div',undefined,'visit-card');card.dataset.visitId=v.visit_id;const head=node('div',undefined,'visit-head');head.append(node('span',String(i+1),'badge'),node('strong',v.point.label));card.append(head);
  const actions=node('div',undefined,'visit-actions'),up=button('Move up',()=>edit(()=>trip.moveVisit(v.visit_id,-1))),down=button('Move down',()=>edit(()=>trip.moveVisit(v.visit_id,1)));up.disabled=i===0;down.disabled=i===d.visits.length-1;actions.append(up,down,button('Replace',()=>beginReplacement(v.visit_id)),button('Remove',()=>edit(()=>{trip.removeVisit(v.visit_id);if(replacement===v.visit_id)cancelReplacement();})),button('Locate',()=>mapView.focusPoint(v.point)));card.append(actions);
  const fields=node('div',undefined,'visit-fields'),left=node('div'),right=node('div'),label=node('label','Stay (min; blank for suggestion)'),input=node('input');input.type='number';input.min='0';input.max='1440';input.step='1';input.value=v.stay_minutes??'';input.placeholder='Use suggested duration';input.setAttribute('aria-label','Stop '+(i+1)+' stay (min)');label.append(input);left.append(label);
  const sel=node('select');sel.setAttribute('aria-label','Stop '+(i+1)+' stay profile');for(const [key,text] of [['takeaway','Takeaway'],['quick','Quick'],['regular','Regular'],['extended','Extended']]){const o=node('option',text);o.value=key;sel.append(o);}sel.value=v.stay_profile;const label2=node('label','Suggested profile (when duration is blank)');label2.append(sel);right.append(label2);fields.append(left,right);card.append(fields,node('p',stayPreview(v),'visit-note'));
  const apply=()=>{try{syncEditor();renderTrip();invalidate();reportError(null);}catch(e){trip.cancel();invalidate();reportError(e);}};input.onchange=sel.onchange=apply;box.append(card);
 });
 $('finish-policy').value=d.finish_policy;$('finish-panel').hidden=d.finish_policy!=='custom';$('finish-summary').textContent=d.finish?d.finish.label:'No finish point selected';$('mode').value=d.mode;$('undo').disabled=!trip.history.length;
}
function clockInput(value){if(!value)return '';const local=value.slice(0,19);return local.endsWith(':00')?local.slice(0,16):local;}
function timeInputs(){const t=trip.data.time;$('time-mode').value=t.mode;$('budget-minutes').value=t.budget_minutes??'';$('departure').value=clockInput(t.departure_at);$('finish-by').value=clockInput(t.finish_by);$('buffer-minutes').value=t.buffer_minutes;timeVisibility();}
function timeVisibility(){const m=$('time-mode').value;$('budget-row').hidden=m!=='budget';$('deadline-row').hidden=m!=='window';}
function readTime(){
 const mode=$('time-mode').value,t={mode,departure_at:T.singaporeISO($('departure').value),finish_by:mode==='window'?T.singaporeISO($('finish-by').value):null,budget_minutes:mode==='budget'?T.integer($('budget-minutes').value,'Total time budget',{min:1,max:10080}):null,buffer_minutes:T.integer($('buffer-minutes').value,'Overall buffer',{min:0,max:1440})};
 if(mode==='window'&&(!t.departure_at||!t.finish_by||Date.parse(t.finish_by)<=Date.parse(t.departure_at)))throw Error('Enter complete start and finish dates and times. The finish must be later; choose the next day explicitly for an overnight trip.');return t;
}
function readEditor(){
 // Read every visible field before applying any of them. A valid edit in one
 // section cannot clear another section's error or commit only part of a form.
 try{
  const mode=$('time-mode').value,timeFields=['departure','buffer-minutes',...(mode==='budget'?['budget-minutes']:[]),...(mode==='window'?['finish-by']:[])];
  for(const id of timeFields)if($(id).validity?.badInput)throw Error('A time field is incomplete or invalid. Complete it before continuing.');
  const time=readTime(),stays=[];
  for(const card of $('visits').querySelectorAll('.visit-card')){
   const input=card.querySelector('input'),profile=card.querySelector('select').value;
   if(!trip.data.visits.some(v=>v.visit_id===card.dataset.visitId))throw Error('The stop list changed. Refresh the editor before continuing.');
   let minutes;
   try{if(input.validity?.badInput)throw Error('Stay duration must be a valid number.');minutes=T.integer(input.value,'Stay duration',{min:0,max:1440,nullable:true});input.classList.remove('input-error-border');}
   catch(e){input.classList.add('input-error-border');throw e;}
   if(!['takeaway','quick','regular','extended'].includes(profile))throw Error('Choose a valid stay profile.');
   stays.push({visit_id:card.dataset.visitId,minutes,profile});
  }
  formInvalid=false;return {time,stays};
 }catch(e){formInvalid=true;throw e;}
}
function editorChanged(editor){
 return !T.sameTime(editor.time,trip.data.time)||editor.stays.some(row=>{
  const visit=trip.data.visits.find(v=>v.visit_id===row.visit_id);
  return visit.stay_minutes!==row.minutes||visit.stay_profile!==row.profile;
 });
}
function syncEditor(){
 const editor=readEditor();if(!editorChanged(editor))return false;
 trip.change(d=>{
  if(!T.sameTime(editor.time,d.time))d.time=editor.time;
  for(const row of editor.stays){const visit=d.visits.find(v=>v.visit_id===row.visit_id);visit.stay_minutes=row.minutes;visit.stay_profile=row.profile;}
 });
 return true;
}
function timeChanged(){timeVisibility();try{if(syncEditor())renderTrip();reportError(null);invalidate();}catch(e){trip.cancel();invalidate();reportError(e);}}
function cancelCalculation(){clearTimeout(autoTimer);trip.cancel();if(planController)planController.abort();planController=null;$('cancel-calculation').disabled=true;if(trip.lastSuccess){mapView.markStale();$('result-body').classList.add('is-stale');}$('plan-status').textContent='Waiting cancelled. No further legs will be requested. An existing provider request may still be finishing.';$('plan-status').className='stale';$('export-report').disabled=true;}
async function calculate(force){
 clearTimeout(autoTimer);let ticket;
 try{if(syncEditor())renderTrip();ticket=trip.begin(force);reportError(null);}catch(e){formInvalid=true;reportError(e);return;}
 if(planController)planController.abort();const controller=planController=new AbortController(),viewRevision=mapView.viewRevision;
 $('cancel-calculation').disabled=false;$('export-report').disabled=true;
 if(trip.lastSuccess){mapView.markStale();$('result-body').classList.add('is-stale');}
 const n=trip.data.visits.length+(trip.data.finish_policy==='last_stop'?0:1);$('plan-status').className='';$('plan-status').textContent=`Calculating or reusing route legs in order: ${n}. You can keep editing; old requests will not overwrite new inputs.`;$('plan-version').textContent='Calculating · Revision '+ticket.revision;
 try{const r=await api('/api/stage2b/plan',ticket.payload,controller.signal);if(!trip.accepts(ticket,r))return;
  mapView.setPlan(r,mapView.viewRevision===viewRevision);if(!trip.save(ticket,r))return;renderResult(r);$('export-report').disabled=false;
  window.dispatchEvent(new CustomEvent('playmap:plan-complete',{detail:{plan:r,revision:trip.revision}}));
 }catch(e){if(e.name==='AbortError'||ticket.sequence!==trip.sequence)return;$('plan-status').textContent=e.message+(trip.lastSuccess?' The previous route and timeline are shown for comparison, not as this result.':' No complete itinerary was created.');$('plan-status').className='error';$('plan-version').textContent='Calculation incomplete';}
 finally{if(planController===controller){planController=null;$('cancel-calculation').disabled=true;}}
}
function renderResult(r){
 $('result-body').hidden=false;$('result-body').classList.remove('is-stale');const x=r.totals;
 $('travel-total').textContent=T.duration(x.travel_s);$('stay-total').textContent=T.duration(x.stay_s);$('buffer-total').textContent=T.duration(x.buffer_s);$('overall-total').textContent=T.duration(x.reference_duration_s);
 $('time-range').textContent='Reference duration: '+T.duration(x.reference_duration_s)+' · Stay-duration range: '+T.duration(x.duration_range_s[0])+'—'+T.duration(x.duration_range_s[1]);
 const labels={no_budget:'No time budget provided. This is an estimated duration for the itinerary, not an assumption about your free time.',all_estimates_exceed:'The current order exceeds the budget across the entire stay-duration range. No stops or durations were changed.',reference_exceeds:'The reference itinerary exceeds the budget. Shorter stays may fit; edit the durations to choose them.',reference_fits_upper_exceeds:'The reference itinerary fits the budget, but longer stays would exceed it.',all_estimates_fit:'The reference itinerary and stay-duration range fit within the current travel and stay estimates.'};
 const check=r.budget_check;$('budget-check').textContent=labels[check.status]+(check.slack_s===null?'':` Reference ${check.slack_s>=0?'time remaining':'overrun'}: ${T.minutes(Math.abs(check.slack_s))}.`);$('budget-check').className=check.status==='no_budget'||check.status==='all_estimates_fit'?'budget-ok':'budget-warning';
 $('journey-summary').textContent=`Stops: ${r.visits.length} · Legs: ${r.legs.length} · ${(x.distance_m/1000).toFixed(2)} km from provider routes · ${MODE[r.mode]} · New route requests: ${r.route_calls.provider_calls} / Cached legs: ${r.route_calls.cache_hits}.`;
 $('clock-note').textContent=(r.time.departure_at?'Times below use Singapore time (UTC+8).':'No departure time set. +HH:MM shows elapsed time from departure.')+' Departure time positions the timeline; it does not predict future traffic.'+(r.opening_hours_checked?' Safely parsed catalogue opening hours were checked. Complex schedules, closures and last admission still need review.':' No usable opening-hours check could be completed.');
 $('plan-version').textContent='Current plan revision '+r.revision;
 const flagged=r.legs.some(l=>l.endpoint_review_required||l.diagnostics.large_detour_flag||l.diagnostics.geometry_summary_mismatch);
 const walkingCheck=r.walking_constraint_check;
 const walkingViolated=walkingCheck?.status==='violation';
 const hoursProblems=r.visits.filter(v=>['closed_at_arrival','insufficient_time'].includes(v.opening_status));
 $('plan-status').className=(flagged||walkingViolated||hoursProblems.length)?'warning':'';

if(hoursProblems.length){
    const detail=hoursProblems.map(v=>{
      const h=v.opening_hours_display||'Opening hours not shown';
      return v.opening_status==='closed_at_arrival'
        ? `${v.point.label}: expected arrival is outside opening hours (${h})`
        : `${v.point.label}: open on arrival, but this stay extends past closing (${h})`;
    }).join('; ');
    $('plan-status').textContent='Opening-hours checks found a possible conflict. '+detail+'.';

}else if(walkingViolated){
    const violations=walkingCheck.violations;

    const detail=violations.map(v =>
        `${v.leg_id}: ${v.duration_minutes} min, ${v.exceeded_by_minutes} min over the limit`
    ).join('; ');

    $('plan-status').textContent=
        `Walking-limit violations: ${violations.length}. ${detail}.`;

}else if(flagged){

    $('plan-status').textContent=
        'All legs were calculated, with endpoint-offset or detour warnings. Durations remain conditional estimates.';

}else{

    $('plan-status').textContent=
        'Travel, stays and total time were calculated in your chosen order. Review opening hours and access separately.';
}
 const panel=$('timeline');panel.replaceChildren();for(const row of r.timeline){
  const el=node('div',undefined,'timeline-row '+row.kind);el.tabIndex=0;el.setAttribute('role','button');
  const time=node('div',undefined,'time-label');time.append(node('span',T.axisLabel(row)),node('span','↓ '+T.axisLabel(row,true)));const content=node('div',undefined,'timeline-content');
  content.append(node('span',row.kind==='travel'?'Travel · '+MODE[r.mode]:row.kind==='visit'?'Stop '+(row.visit_index+1):'Overall buffer','timeline-type'),node('strong',row.title),node('p',T.minutes(row.duration_s),'small'));
  if(row.kind==='travel'){const leg=r.legs[row.leg_index];content.append(node('p',leg.kind==='same_selected_coordinate'?'Same selected coordinate: no connector drawn; transfer time is 0.':`${(leg.distance_m/1000).toFixed(2)} km · ${leg.reused_in_memory?'Cached':'New request'} · Endpoint offsets ${leg.endpoint_offsets_m.origin.toFixed(0)} / ${leg.endpoint_offsets_m.destination.toFixed(0)} m`,'small'));if(leg.endpoint_review_required||leg.diagnostics.large_detour_flag)content.append(node('p','Check this leg for endpoint offsets or a large detour. The transport mode was not changed.','leg-warning'));}
  if(row.kind==='visit'){const v=r.visits[row.visit_index],h=v.opening_hours_display;
   const hoursText=v.opening_status==='feasible'?'Opening-hours check: this schedule fits':
    v.opening_status==='closed_at_arrival'?'Opening-hours check: closed on arrival':
    v.opening_status==='insufficient_time'?'Opening-hours check: stay extends past closing':
    h?'Opening hours need manual review':'No usable opening hours';
   content.append(node('p',(v.stay_basis==='user_specified'?'Custom stay':'Initial stay assumption')+` · Range ${v.stay_range_minutes.join('–')} min · ${hoursText}${h?' · '+h:''}`,'small'));
   if(v.selected_entrance){
    const why=v.entrance_selection_basis==='fastest_reachable_entrance_candidate'?'Selected using OneMap travel time from the preceding point':'Only one entrance candidate is available';
    content.append(node('p',`Suggested access point: ${v.selected_entrance.label} · ${why} · Candidate entrance, not field-verified. The same point is used on departure; travel through the park to another exit is not assumed.`,'small'));
   }else if(v.entrance_selection_basis==='representative_point_all_candidates_unreachable'){
    content.append(node('p','No usable route to the entrance candidates. Routing uses the place representative point instead; check the actual entrance.','leg-warning'));
   }
  }
  const focus=()=>{for(const e of panel.querySelectorAll('.is-focus'))e.classList.remove('is-focus');el.classList.add('is-focus');if(row.kind==='travel')mapView.focusLeg(row.leg_index);else if(row.kind==='visit')mapView.focusPoint(r.visits[row.visit_index].point);};el.onclick=focus;el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();focus();}};el.append(time,content);panel.append(el);
 }
 $('plan-warnings').replaceChildren(...r.cautions.map(t=>node('p',t,'small')));
}
function exportReport(){if(!trip.lastSuccess||trip.lastSuccess.revision!==trip.revision||formInvalid)return reportError(Error('Obtain a complete plan for the current inputs first.'));const report=T.diagnostic(trip.lastSuccess,trip.revision);const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),link=node('a');link.href=url;link.download='stage2b_itinerary_report.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);$('export-status').textContent='Downloaded locally. Excludes exact locations, place names, clock times and tokens. Nothing was uploaded.';}
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
  syncEditor();
  trip.setOrigin({latitude:r.latitude,longitude:r.longitude,label:'Current location (device reading)',
   source:'device_location',accuracy_m:r.accuracy_m,confirmed:true});
  reportError(null);renderTrip();invalidate();closeLocate(true);
 }catch(e){reportError(e);closeLocate(false);}
}
function renderLocateCard(reading){
 deviceReading=reading;
 const card=$('locate-card');card.hidden=false;card.replaceChildren();
 card.append(node('p','Location detected. '+PlayMapLocate.describe(reading),'small'));
 if(reading.coarse)card.append(node('p','Low accuracy: the blue circle shows the possible area. Using this as the start may affect time estimates; that uncertainty is not included in the calculations.','small warning'));
 const row=node('div',undefined,'place-actions');
 row.append(button('Start here',adoptDevice,'primary'),button('Choose manually',()=>closeLocate(false)));
 card.append(row,node('p','Device location is not a verified entrance and does not prove you are at that point.','small'));
}
async function locateDevice(){
 $('locate-status').textContent='Requesting browser location… A permission prompt may appear.';
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
 $('pick-map').onclick=()=>{if(!mapView.map)return reportError(Error('Map is not ready. Use search or enter coordinates.'));mapView.setPick(true);$('map').scrollIntoView({block:'nearest'});};$('cancel-pick').onclick=()=>mapView.setPick(false);document.addEventListener('keydown',e=>{if(e.key==='Escape')mapView.setPick(false);});
 $('coordinate-form').onsubmit=e=>{e.preventDefault();try{const latitude=Number($('manual-lat').value),longitude=Number($('manual-lon').value);if(!$('manual-lat').value||!$('manual-lon').value||!Number.isFinite(latitude)||!Number.isFinite(longitude)||latitude<.9||latitude>1.7||longitude<103.4||longitude>104.7)throw Error('Check the latitude and longitude are within Singapore.');selectPlace({latitude,longitude,label:'Entered coordinates',source:'user_coordinates',confirmed:true},{});reportError(null);}catch(e){reportError(e);}};
 $('catalogue-query').oninput=()=>{clearTimeout(filterTimer);filterTimer=setTimeout(()=>{listLimit=30;renderCatalogue();},150);};$('category').onchange=$('include-holds').onchange=()=>{listLimit=30;renderCatalogue();};$('show-pois').onchange=renderCatalogue;$('catalogue-more').onclick=()=>{listLimit+=30;renderCatalogue();};
 $('finish-policy').onchange=()=>edit(()=>trip.setFinishPolicy($('finish-policy').value));$('mode').onchange=()=>edit(()=>trip.setMode($('mode').value));
 for(const id of ['time-mode','budget-minutes','departure','finish-by','buffer-minutes'])$(id).onchange=timeChanged;
 $('replace-origin').onclick=()=>beginReplacement('origin');$('replace-finish').onclick=()=>beginReplacement('finish');
 $('undo').onclick=()=>{if(!trip.undo())return;formInvalid=false;cancelReplacement();timeInputs();renderTrip();invalidate();reportError(null);};
 $('calculate').onclick=()=>calculate(false);$('fresh-routes').onclick=()=>calculate(true);$('cancel-calculation').onclick=cancelCalculation;
 $('auto-plan').onchange=()=>{clearTimeout(autoTimer);if($('auto-plan').checked&&trip.lastSuccess&&trip.lastSuccess.revision!==trip.revision&&!formInvalid)autoTimer=setTimeout(()=>calculate(false),550);};
 $('clear-trip').onclick=async()=>{if(!window.confirm('Clear the itinerary on this page? Place data, tokens and source files will be retained.'))return;cancelCalculation();trip.clear();formInvalid=false;cancelReplacement();selected=null;renderSelected();timeInputs();renderTrip();mapView.clearPlan();mapView.setDraft(trip.data);$('result-body').hidden=true;$('plan-status').textContent='Itinerary cleared. Singapore-wide data is retained.';$('plan-status').className='';$('plan-version').textContent='Not calculated';$('export-report').disabled=true;try{await api('/api/stage2b/clear-memory',{session_id:trip.sessionId});}catch{}}
 $('fit').onclick=()=>mapView.fitNation();$('fit-route').onclick=()=>mapView.fitRoute();$('fit-stops').onclick=()=>mapView.fitDraft();$('refresh-status').onclick=refreshStatus;$('export-report').onclick=exportReport;
}
wire();renderTrip();timeInputs();refreshStatus();loadCatalogue();mapView.init();
