const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');

const harnessFile = path.join(__dirname, 'test_calculation_bridge.cjs');
const realRequire = createRequire(harnessFile);
const harness = {require: name => name === 'node:test' ? (() => {}) : realRequire(name),
  __dirname, module: {exports: {}}, exports: {}, console, AbortController, clearTimeout, setTimeout};
vm.createContext(harness);
vm.runInContext(fs.readFileSync(harnessFile, 'utf8') +
  '\nmodule.exports={createCalculator,planFor};', harness, {filename: harnessFile});
const {createCalculator, planFor} = harness.module.exports;
const copy = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
async function waitFor(predicate) {
  for (let attempt = 0; attempt < 60; attempt++) {if (predicate()) return; await tick();}
  assert.fail('Expected asynchronous request was not reached');
}
const suggestion = label => ({suggestions: [{label, route_duration_min: 8, feasible: true,
  feasibility_check: {checks: {opening_hours: true, time_budget: true, walking_constraint: true}}}]});

function createChat() {
  const app = createCalculator();
  let prepared, planCount = 0;
  function walkingPlan(payload, count, minutes = 15) {
    const plan = planFor(payload);
    plan.verification_ref = count.toString(16).padStart(32, '0');
    plan.visits[0].category = 'nature';
    plan.legs[0].duration_s = minutes * 60;
    plan.totals.travel_s = minutes * 60;
    plan.totals.reference_duration_s = minutes * 60 + plan.totals.stay_s;
    plan.totals.duration_range_s = [plan.totals.reference_duration_s, plan.totals.reference_duration_s];
    plan.budget_check.slack_s = payload.time.budget_minutes * 60 - plan.totals.reference_duration_s;
    plan.timeline[0].start_offset_s = minutes * 60;
    plan.timeline[0].end_offset_s = plan.totals.reference_duration_s;
    plan.walking_constraint_check = minutes > 10
      ? {status: 'violation', violations: [{leg_id: 'leg-1', duration_minutes: minutes, exceeded_by_minutes: minutes - 10}]}
      : {status: 'ok', violations: []};
    return plan;
  }
  let planResponse = walkingPlan, alternativeResponse = () => suggestion('CURRENT PARK');
  app.respondWith((payload, url) => {
    if (url === '/api/stage2c/status') return {configured: true, model: 'offline-fixture'};
    if (url === '/api/stage2c/chat') {
      const budget = payload.message.includes('90') ? 90 : 60;
      const draft = copy(payload.draft);
      draft.time = {...draft.time, mode: 'budget', budget_minutes: budget};
      draft.max_walk_minutes = 10;
      const preferences = {preferred: [], excluded: [], notes: [], active_constraints: [
        {name: 'budget_minutes', value: budget, scope: 'global', kind: 'constraint', hardness: 'hard',
          status: 'understood', source: 'user_explicit', quote: payload.message, turn_index: 1}]};
      prepared = {draft, preferences, ready_to_calculate: true};
      return {base_revision: payload.revision, ticket: 'offline-ticket', model: 'offline-fixture',
        acknowledgement: 'Review this change.', questions: [], slots: [],
        interpretation: {actions: [{op: 'set_time', budget_minutes: budget}]},
        preview: {can_apply: true, changes: ['Change budget'], warnings: [], draft, preferences},
        usage: {generation_requests: 1, calls: []}};
    }
    if (url === '/api/stage2c/resolve') return prepared;
    if (url === '/api/stage2b/plan') return planResponse(payload, ++planCount);
    if (url === '/api/stage2b/alternatives') return alternativeResponse(payload);
    if (url === '/api/stage2c/verification') return {verification: {status: 'matched'}, preferences: {
      ...copy(payload.preferences), active_constraints: payload.preferences.active_constraints.map(row =>
        ({...row, status: 'satisfied', evidence_source: 'route'}))}};
    throw Error('Unexpected offline fixture URL: ' + url);
  });
  for (const filename of ['chat_state.js', 'chat.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../frontend/stage2c', filename), 'utf8'),
      app.context, {filename});
  }
  return {...app, walkingPlan,
    planWith: response => {planResponse = response;},
    alternativesWith: response => {alternativeResponse = response;},
    async propose(budget = 60) {
      app.element('chat-input').value = `Set the total budget to ${budget} minutes.`;
      await app.element('chat-form').onsubmit({preventDefault() {}});
      assert.equal(app.element('chat-apply').disabled, false);
    },
    adopt: () => app.element('chat-apply').onclick(),
    refresh: () => vm.runInContext('calculate(true)', app.context),
    cancelCalculation: () => vm.runInContext('cancelCalculation()', app.context)};
}

for (const state of ['started', 'completed', 'failed', 'cancelled']) {
  for (const outcome of ['success', 'error']) {
    test(`old alternatives ${outcome} is discarded after a same-revision recalculation ${state}`, async () => {
      const app = createChat(); let releaseAlternative, rejectAlternative;
      app.alternativesWith(() => new Promise((resolve, reject) => {
        releaseAlternative = resolve; rejectAlternative = reject;
      }));
      await app.propose(); const adoption = app.adopt();
      await waitFor(() => !!releaseAlternative);
      const revision = app.trip.revision, oldPlan = app.bridge.activePlan();
      let finishPlan;
      app.planWith((payload, count) => state === 'failed'
        ? {ok: false, body: {detail: {code: 'NO_ROUTE', message: 'Offline route failed'}}}
        : state === 'completed' ? app.walkingPlan(payload, count, 5)
        : new Promise(resolve => {finishPlan = () => resolve(app.walkingPlan(payload, count, 5));}));
      const recalculation = app.refresh();
      if (state === 'completed' || state === 'failed') await recalculation;
      if (state === 'cancelled') app.cancelCalculation();
      assert.equal(app.trip.revision, revision);
      if (state === 'completed') assert.notStrictEqual(app.bridge.activePlan(), oldPlan);
      const before = app.element('chat-log').textContent;
      if (outcome === 'success') releaseAlternative(suggestion('OBSOLETE PARK'));
      else rejectAlternative(Error('OBSOLETE ALTERNATIVE ERROR'));
      await adoption;
      assert.equal(app.element('chat-log').textContent, before);
      assert.doesNotMatch(app.element('chat-log').textContent, /OBSOLETE PARK|alternatives search did not finish/);
      if (finishPlan) {finishPlan(); await recalculation;}
    });
  }
}

test('current alternatives remain visible and their duration identifies the violated walk', async () => {
  const app = createChat(); await app.propose(); await app.adopt();
  assert.match(app.element('chat-log').textContent, /CURRENT PARK.*about 8 min for this walk/i);
  assert.match(app.element('chat-log').textContent, /Passes current feasibility checks/);
  assert.equal(app.trip.data.visits[0].point.label, 'Museum');
});

test('a new adopted plan keeps its own alternatives when the old request settles later', async () => {
  const app = createChat(); let releaseOld, calls = 0;
  app.alternativesWith(() => ++calls === 1
    ? new Promise(resolve => {releaseOld = resolve;}) : suggestion('LATEST PARK'));
  await app.propose(); const oldAdoption = app.adopt(); await waitFor(() => !!releaseOld);
  await app.propose(90); await app.adopt();
  assert.match(app.element('chat-log').textContent, /LATEST PARK/);
  const before = app.element('chat-log').textContent;
  releaseOld(suggestion('OBSOLETE PARK')); await oldAdoption;
  assert.equal(app.element('chat-log').textContent, before);
  assert.equal(app.trip.data.time.budget_minutes, 90);
});
