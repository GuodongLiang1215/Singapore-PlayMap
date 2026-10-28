const assert=require('node:assert/strict');
const test=require('node:test');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createRequire}=require('node:module');
const copy=value=>JSON.parse(JSON.stringify(value));

function harness(chat=false){
 const file=path.join(__dirname,'test_calculation_bridge.cjs'),realRequire=createRequire(file);
 const outer={require:name=>name==='node:test'?(()=>{}):realRequire(name),__dirname,
  module:{exports:{}},exports:{},console,AbortController,clearTimeout,setTimeout};
 vm.createContext(outer);
 vm.runInContext(fs.readFileSync(file,'utf8')+'\nmodule.exports={createCalculator,withRealChat,Element};',outer,{filename:file});
 const {createCalculator,withRealChat,Element}=outer.module.exports;
 Element.prototype.querySelector=function(selector){return this.querySelectorAll(selector)[0]||null;};
 const app=chat?withRealChat():createCalculator({mode:'budget',budget_minutes:60});
 const source=fs.readFileSync(path.join(__dirname,'../frontend/stage2b/app.js'),'utf8');
 Object.assign(app.context,{all:[],SOURCE_LABEL:{user_map:'Map point'},STAYS:{unknown:[15,30,60]},
  replacement:null,selected:null,CATEGORY:{},closeLocate(){}});
 Object.assign(app.context.mapView,{setPick(){},clearPlan(){},setDraft(){}});
 function load(start,end){
  const from=source.indexOf(start),to=source.indexOf(end,from+start.length);
  assert.ok(from>=0&&to>from,`Missing source section: ${start}`);
  vm.runInContext(source.slice(from,to),app.context,{filename:'frontend/stage2b/app.js'});
 }
 load('function edit(fn)','function beginReplacement(');
 load('function renderSelected(','async function selectFeature(');
 load('function stayPreview(','function clockInput(');
 load(" $('finish-policy').onchange=",' for(const id of');
 load(" $('undo').onclick="," $('calculate').onclick=");
 load(" $('clear-trip').onclick="," $('fit').onclick=");
 vm.runInContext('renderTrip()',app.context);
 return {...app,input:(index=0)=>app.element('visits').querySelectorAll('input')[index],
  profile:(index=0)=>app.element('visits').querySelectorAll('select')[index],
  timeChange:()=>vm.runInContext('timeChanged()',app.context),
  render:()=>vm.runInContext('renderTrip()',app.context)};
}

function unchanged(app,before,revision,history){
 assert.deepEqual(copy(app.trip.data),before);
 assert.equal(app.trip.revision,revision);
 assert.equal(app.trip.history.length,history);
}

test('invalid stay plus valid time edit blocks chat and preserves draft, ledger and entered text',async()=>{
 const app=harness(true);await app.propose();await app.adopt();
 const before=copy(app.trip.data),revision=app.trip.revision,history=app.trip.history.length;
 const requirements=app.element('chat-preferences').textContent;
 app.input().value='2000';app.input().onchange();
 app.element('budget-minutes').value='90';app.timeChange();
 unchanged(app,before,revision,history);
 assert.equal(app.input().value,'2000');assert.equal(app.element('budget-minutes').value,'90');
 assert.equal(app.element('chat-preferences').textContent,requirements);
 const chatCalls=app.requests.filter(r=>r.url==='/api/stage2c/chat').length;
 app.element('chat-input').value='Keep the current settings.';
 await app.element('chat-form').onsubmit({preventDefault(){}});
 assert.equal(app.requests.filter(r=>r.url==='/api/stage2c/chat').length,chatCalls);
 assert.throws(()=>app.bridge.snapshot(),/Stay duration/);
});

test('correcting the stay atomically commits pending stay and time, then undo restores the old ledger',async()=>{
 const app=harness(true);await app.propose();await app.adopt();
 const before=copy(app.trip.data),revision=app.trip.revision,history=app.trip.history.length;
 app.input().value='2000';app.input().onchange();
 app.element('budget-minutes').value='90';app.timeChange();
 app.input().value='45';app.input().onchange();
 assert.equal(app.trip.data.visits[0].stay_minutes,45);
 assert.equal(app.trip.data.time.budget_minutes,90);
 assert.equal(app.trip.revision,revision+1);assert.equal(app.trip.history.length,history+1);
 assert.match(app.element('chat-preferences').textContent,/Total time budget: 90 min/);
 const snapshot=app.bridge.snapshot();assert.equal(snapshot.draft.visits[0].stay_minutes,45);
 app.element('undo').onclick();assert.deepEqual(copy(app.trip.data),before);
 assert.match(app.element('chat-preferences').textContent,/Total time budget: 60 min/);
});

test('invalid time plus valid stay edit is equally atomic and recovers without discarding input',()=>{
 const app=harness(),before=copy(app.trip.data),revision=app.trip.revision,history=app.trip.history.length;
 app.element('budget-minutes').value='0';app.timeChange();
 app.input().value='45';app.input().onchange();
 unchanged(app,before,revision,history);
 assert.equal(app.input().value,'45');assert.equal(app.element('budget-minutes').value,'0');
 assert.throws(()=>app.bridge.snapshot(),/Total time budget/);
 app.element('budget-minutes').value='90';app.timeChange();
 assert.equal(app.trip.revision,revision+1);
 assert.equal(app.trip.data.visits[0].stay_minutes,45);assert.equal(app.trip.data.time.budget_minutes,90);
});

for(const action of ['snapshot','calculate'])test(`${action} validates every stay before changing any of them`,async()=>{
 const app=harness();app.trip.data.visits.push({...copy(app.trip.data.visits[0]),visit_id:'second'});app.render();
 const before=copy(app.trip.data),revision=app.trip.revision,history=app.trip.history.length;
 app.input(0).value='45';app.input(1).value='2000';
 if(action==='snapshot')assert.throws(()=>app.bridge.snapshot(),/Stay duration/);
 else await app.bridge.calculate();
 unchanged(app,before,revision,history);
 assert.equal(app.input(0).value,'45');assert.equal(app.input(1).value,'2000');
 assert.equal(app.requests.length,0);
 app.input(1).value='50';const result=app.bridge.snapshot();
 assert.deepEqual(copy(result.draft.visits.map(v=>v.stay_minutes)),[45,50]);
 assert.equal(app.trip.revision,revision+1);
});

test('multiple invalid stays must all be corrected before recovery',()=>{
 const app=harness();app.trip.data.visits.push({...copy(app.trip.data.visits[0]),visit_id:'second'});app.render();
 const before=copy(app.trip.data),revision=app.trip.revision,history=app.trip.history.length;
 app.input(0).value='2000';app.input(1).value='-1';app.input(0).onchange();
 app.input(0).value='45';app.input(0).onchange();
 unchanged(app,before,revision,history);assert.equal(app.input(1).value,'-1');
 app.input(1).value='50';app.input(1).onchange();
 assert.deepEqual(app.trip.data.visits.map(v=>v.stay_minutes),[45,50]);
 assert.equal(app.trip.revision,revision+1);
});

test('pending proposal adoption revalidates visible stays even before their change event',async()=>{
 const app=harness(true);await app.propose();
 const before=copy(app.trip.data),revision=app.trip.revision,history=app.trip.history.length;
 app.input().value='2000';await app.adopt();
 unchanged(app,before,revision,history);assert.equal(app.input().value,'2000');
 assert.doesNotMatch(app.element('chat-preferences').textContent,/Total time budget/);
 assert.match(app.element('chat-status').textContent,/Stay duration/);
});

test('pending proposal cannot overwrite valid but unsynchronized visible edits',async()=>{
 const app=harness(true);await app.propose();
 const before=copy(app.trip.data),revision=app.trip.revision,history=app.trip.history.length;
 app.input().value='45';await app.adopt();
 unchanged(app,before,revision,history);assert.equal(app.input().value,'45');
 assert.match(app.element('chat-status').textContent,/unsaved|edited/i);
});

for(const [id,value,stored]of[['mode','drive','walk'],['finish-policy','return_to_start','last_stop']])
 test(`blocked ${id} change restores its select without clearing invalid stays`,()=>{
  const app=harness(),before=copy(app.trip.data),revision=app.trip.revision,history=app.trip.history.length;
  app.input().value='2000';app.input().onchange();
  app.element(id).value=value;app.element(id).onchange();
  unchanged(app,before,revision,history);assert.equal(app.input().value,'2000');
  assert.equal(app.element(id).value,stored);
 });

for(const replace of [false,true])test(`blocked ${replace?'replacement':'addition'} retains invalid editor values`,()=>{
 const app=harness(),before=copy(app.trip.data),revision=app.trip.revision,history=app.trip.history.length;
 app.input().value='2000';app.input().onchange();
 app.context.selected={point:{latitude:1.31,longitude:103.87,label:'New point',source:'user_map',confirmed:true},meta:{}};
 app.context.replacement=replace?'museum':null;vm.runInContext('renderSelected()',app.context);
 const label=replace?'Replace selected item':'Add a stop';
 app.element('selected-place').querySelectorAll('button').find(b=>b.textContent===label).onclick();
 unchanged(app,before,revision,history);assert.equal(app.input().value,'2000');
});

test('explicit undo and clear remain available to recover an invalid editor',async()=>{
 const app=harness();app.input().value='45';app.input().onchange();
 app.input().value='2000';app.input().onchange();app.element('undo').onclick();
 assert.equal(app.trip.data.visits[0].stay_minutes,30);assert.equal(app.input().value,30);
 app.input().value='2000';app.input().onchange();await app.element('clear-trip').onclick();
 assert.equal(app.trip.data.visits.length,0);assert.equal(app.element('visits').querySelectorAll('input').length,0);
 assert.equal(app.bridge.snapshot().draft.origin,null);
});

for(const target of ['stay','departure'])test(`native invalid ${target} input is not treated as an intentional blank`,()=>{
 const app=harness(),before=copy(app.trip.data),revision=app.trip.revision,history=app.trip.history.length;
 const input=target==='stay'?app.input():app.element('departure');
 input.value='';input.validity={badInput:true};
 assert.throws(()=>app.bridge.snapshot(),/invalid|valid number/i);
 unchanged(app,before,revision,history);
 input.validity.badInput=false;const snapshot=app.bridge.snapshot();
 if(target==='stay')assert.equal(snapshot.draft.visits[0].stay_minutes,null);
 else assert.equal(snapshot.draft.time.departure_at,null);
});
