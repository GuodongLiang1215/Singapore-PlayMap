const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');
const {reconcileManualPreferences} = require('../frontend/stage2c/chat_state.js');
const copy = value => JSON.parse(JSON.stringify(value));
const draft = () => ({mode:'walk', max_walk_minutes:10, finish_policy:'last_stop',
  time:{mode:'budget', budget_minutes:60, departure_at:'2026-10-28T10:00:00+08:00', finish_by:null},
  visits:[{visit_id:'a',stay_minutes:30,stay_profile:'regular'},
          {visit_id:'b',stay_minutes:45,stay_profile:'regular'}]});
const row = (name, value, scope='global', extra={}) => ({name,value,scope,kind:'constraint',
  hardness:'hard',status:'satisfied',source:'user_explicit',evidence_source:'route',
  quote:'Original user request',turn_index:1,...extra});
const prefs = rows => ({preferred:['nature'],excluded:['shopping'],notes:['Keep the river view'],active_constraints:rows});

test('manual budget update changes only its requirement and expires its old proof', () => {
  const before=draft(), after=copy(before); after.time.budget_minutes=90;
  const input=prefs([row('budget_minutes',60),row('place_type','park','future_visits')]);
  const original=copy(input), output=reconcileManualPreferences(input,before,after);
  assert.deepEqual(input,original);
  assert.equal(output.active_constraints[0].value,90);
  assert.equal(output.active_constraints[0].hardness,'hard');
  assert.equal(output.active_constraints[0].status,'understood');
  assert.equal(output.active_constraints[0].source,'user_explicit');
  assert.equal(output.active_constraints[0].evidence_source,null);
  assert.match(output.active_constraints[0].quote,/editor/i);
  assert.deepEqual(output.active_constraints[1],original.active_constraints[1]);
  assert.deepEqual(output.excluded,original.excluded);
});

test('an unrelated edit never repairs or removes a conflicting budget silently', () => {
  const before=draft(),after=copy(before); after.visits[0].stay_minutes=50;
  const input=prefs([row('budget_minutes',120),row('wheelchair_access',true)]);
  assert.deepEqual(reconcileManualPreferences(input,before,after),input);
});

for (const mode of ['estimate','window']) test(`leaving budget mode for ${mode} removes only the cleared budget`, () => {
  const before=draft(),after=copy(before);
  after.time={...after.time,mode,budget_minutes:null,finish_by:mode==='window'?'2026-10-28T12:00:00+08:00':null};
  const output=reconcileManualPreferences(prefs([row('budget_minutes',60),row('max_walk_minutes',10)]),before,after);
  assert.deepEqual(output.active_constraints.map(x=>x.name),['max_walk_minutes']);
});

test('clearing an explicit deadline and departure removes their matching rows', () => {
  const before=draft(); before.time={mode:'window',budget_minutes:null,
    departure_at:'2026-10-28T10:00:00+08:00',finish_by:'2026-10-28T12:00:00+08:00'};
  const after=copy(before); after.time={mode:'estimate',budget_minutes:null,departure_at:null,finish_by:null};
  const output=reconcileManualPreferences(prefs([row('departure_at',before.time.departure_at),
    row('finish_by',before.time.finish_by),row('theme','heritage')]),before,after);
  assert.deepEqual(output.active_constraints.map(x=>x.name),['theme']);
});

test('equivalent time offsets do not change a dated requirement', () => {
  const before=draft(),after=copy(before); after.time.departure_at='2026-10-28T02:00:00Z';
  const input=prefs([row('departure_at',before.time.departure_at)]);
  assert.deepEqual(reconcileManualPreferences(input,before,after),input);
});

test('manual mode, finish policy and walking-limit changes retain hardness and scope', () => {
  const before=draft(),after=copy(before);
  after.mode='drive';after.finish_policy='return_to_start';after.max_walk_minutes=15;
  const input=prefs([row('transport_mode','walk'),row('finish_policy','last_stop'),
    row('max_walk_minutes',10,'global',{hardness:'soft'})]);
  const output=reconcileManualPreferences(input,before,after);
  assert.deepEqual(output.active_constraints.map(x=>x.value),['drive','return_to_start',15]);
  assert.equal(output.active_constraints[2].hardness,'soft');
  assert.ok(output.active_constraints.every(x=>x.scope==='global'));
});

test('duration requirements follow visit identity through reordering and edits', () => {
  const before=draft(),after=copy(before); after.visits.reverse();after.visits[1].stay_minutes=20;
  const output=reconcileManualPreferences(prefs([row('stay_minutes',30,'visit:1'),
    row('stay_minutes',45,'visit_id:b')]),before,after);
  assert.equal(output.active_constraints[0].scope,'visit_id:a');
  assert.equal(output.active_constraints[0].value,20);
  assert.equal(output.active_constraints[1].value,45);
});

test('reordering alone binds ordinal constraints to the same original stop', () => {
  const before=draft(),after=copy(before);after.visits.reverse();
  const output=reconcileManualPreferences(prefs([row('stay_minutes',30,'visit:1')]),before,after);
  assert.equal(output.active_constraints[0].scope,'visit_id:a');
  assert.equal(output.active_constraints[0].value,30);
});

test('using a suggested duration clears the old fixed-stay override only', () => {
  const before=draft(),after=copy(before);after.visits[0].stay_minutes=null;
  const output=reconcileManualPreferences(prefs([row('stay_minutes',30,'visit_id:a'),
    row('place_type','museum','visit_id:a')]),before,after);
  assert.deepEqual(output.active_constraints.map(x=>x.name),['place_type']);
});

test('removing a stop removes its requirements but keeps other stop and future requirements', () => {
  const before=draft(),after=copy(before);after.visits.shift();
  const output=reconcileManualPreferences(prefs([row('stay_minutes',30,'visit_id:a'),
    row('place_type','museum','visit:1'),row('stay_minutes',45,'visit_id:b'),
    row('place_type','park','future_visits')]),before,after);
  assert.deepEqual(output.active_constraints.map(x=>x.scope),['visit_id:b','future_visits']);
});

test('changing takeaway profile updates that stop only', () => {
  const before=draft();before.visits[0].stay_profile='takeaway';
  const after=copy(before);after.visits[0].stay_profile='regular';
  const output=reconcileManualPreferences(prefs([row('takeaway',true,'visit_id:a'),
    row('takeaway',true,'future_visits')]),before,after);
  assert.equal(output.active_constraints[0].value,false);
  assert.equal(output.active_constraints[1].value,true);
});

test('no preceding draft or no corresponding ledger entry invents no requirement', () => {
  const before=draft(),after=copy(before);after.time.budget_minutes=90;
  assert.deepEqual(reconcileManualPreferences(prefs([]),before,after),prefs([]));
  const input=prefs([row('budget_minutes',60)]);
  assert.deepEqual(reconcileManualPreferences(input,null,after),input);
});

function calculatorHarness() {
  const file=path.join(__dirname,'test_calculation_bridge.cjs'),realRequire=createRequire(file);
  const context={require:name=>name==='node:test'?(()=>{}):realRequire(name),
    __dirname, module:{exports:{}},exports:{},console,AbortController,clearTimeout,setTimeout};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(file,'utf8')+'\nmodule.exports={withRealChat};',context,{filename:file});
  return context.module.exports.withRealChat();
}

test('real editor and chat bridge send the new budget and undo restores the previous ledger', async () => {
  const app=calculatorHarness();await app.propose();await app.adopt();
  app.element('budget-minutes').value='90';vm.runInContext('timeChanged()',app.context);
  assert.match(app.element('chat-preferences').textContent,/Total time budget: 90 min/);
  app.element('chat-input').value='Change the first stop to 45 minutes.';
  await app.element('chat-form').onsubmit({preventDefault(){}});
  const sent=app.requests.filter(r=>r.url==='/api/stage2c/chat').at(-1).payload;
  assert.equal(sent.draft.time.budget_minutes,90);
  assert.equal(sent.preferences.active_constraints.find(r=>r.name==='budget_minutes').value,90);
  app.trip.undo();vm.runInContext('timeInputs()',app.context);
  assert.equal(app.trip.data.time.budget_minutes,60);
  assert.match(app.element('chat-preferences').textContent,/Total time budget: 60 min/);
  assert.doesNotMatch(app.element('chat-preferences').textContent,/Passed using reference data/);
  app.trip.undo();vm.runInContext('timeInputs()',app.context);
  assert.equal(app.trip.data.time.mode,'estimate');
  assert.doesNotMatch(app.element('chat-preferences').textContent,/Total time budget/);
});
