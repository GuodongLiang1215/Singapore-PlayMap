'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');

// Reuse the real app/bridge/chat fixture without registering its separate tests.
// Production calculate(), bridge.apply(), state and chat code run unchanged.
const fixtureFile = path.join(__dirname, 'test_calculation_bridge.cjs');
const fixtureRequire = createRequire(fixtureFile), fixtureModule = {exports: {}};
new Function('require', 'module', '__dirname', '__filename',
  fs.readFileSync(fixtureFile, 'utf8') + '\nmodule.exports={withRealChat,planFor,copy};')(
  name => name === 'node:test' ? () => {} : fixtureRequire(name),
  fixtureModule, __dirname, fixtureFile);
const {withRealChat, planFor, copy} = fixtureModule.exports;
const emptyPreferences = () => ({preferred: [], excluded: [], notes: [], active_constraints: []});
const point = (label, longitude) => ({label, latitude: 1.30, longitude, source: 'user_map', confirmed: true});
const budget = overrides => ({name: 'budget_minutes', value: 180, scope: 'global', kind: 'constraint',
  hardness: 'hard', status: 'understood', source: 'user_explicit', quote: '180 minutes', turn_index: 1, ...overrides});

function setup() {
  const app = withRealChat();
  app.trip.data = {origin: point('Public start', 103.845), visits: [
    {visit_id: 'park', point: point('Park', 103.847), stay_minutes: 30, stay_profile: 'regular'},
    {visit_id: 'cafe', point: point('Current cafe', 103.849), stay_minutes: 45, stay_profile: 'regular'}],
    finish_policy: 'return_to_start', finish: null, mode: 'walk', max_walk_minutes: null,
    time: {mode: 'budget', departure_at: '2026-10-31T14:00:00+08:00', finish_by: null,
      budget_minutes: 180, buffer_minutes: 0}};
  vm.runInContext('timeInputs()', app.context);
  let response, resolveDraft, verifiedPreferences;
  app.respondWith((payload, url) => {
    if (url === '/api/stage2c/status') return {configured: true, model: 'offline'};
    if (url === '/api/stage2c/chat') return {...response, base_revision: payload.revision};
    if (url === '/api/stage2c/resolve') return {draft: resolveDraft || response.preview.draft,
      preferences: response.preview.preferences, ready_to_calculate: true};
    if (url === '/api/stage2b/plan') return planFor(payload);
    if (url === '/api/stage2c/verification') return {verification: {status: 'matched'},
      preferences: verifiedPreferences || response.preview.preferences};
    throw Error('Unexpected offline endpoint: ' + url);
  });
  return {...app,
    async send({actions = [], slots = [], preview = {}, resolved = null, verified = null} = {}) {
      response = {ticket: 'offline-ticket', model: 'offline', acknowledgement: 'Review the proposal.',
        questions: [], slots, interpretation: {actions},
        preview: {draft: copy(app.trip.data), preferences: emptyPreferences(), changes: [], warnings: [],
          can_apply: true, adoption_blockers: [], ...preview}, usage: {generation_requests: 1, calls: []}};
      resolveDraft = resolved; verifiedPreferences = verified;
      app.element('chat-input').value = 'Change another cafe';
      await app.element('chat-form').onsubmit({preventDefault() {}});
    },
    async assertNoAdoption() {
      assert.equal(app.element('chat-apply').disabled, true);
      assert.match(app.element('chat-readiness').textContent, /No changes to adopt/);
      const revision = app.trip.revision, draft = copy(app.trip.data), n = app.requests.length;
      await app.adopt();
      assert.equal(app.trip.revision, revision); assert.deepEqual(copy(app.trip.data), draft);
      assert.equal(app.requests.slice(n).filter(r => /\/(resolve|plan)$/.test(r.url)).length, 0);
    }};
}

test('descriptive changes alone cannot turn an unchanged legacy proposal into an adoption', async () => {
  const app = setup();
  await app.send({preview: {changes: ['Update recorded requirements and preferences (pending confirmation)']}});
  await app.assertNoAdoption();
});

test('an unchanged typed action is not a material edit without new server flags', async () => {
  const app = setup();
  await app.send({actions: [{op: 'set_mode', value: 'walk'}], preview: {changes: ['Transport mode → walk']}});
  await app.assertNoAdoption();
});

test('equivalent timestamp formatting and model null defaults do not create an itinerary change', async () => {
  const app = setup(), draft = copy(app.trip.data);
  draft.time.departure_at = '2026-10-31T06:00:00Z';
  draft.visits[1].point.accuracy_m = null; draft.visits[1].point.entity_id = null;
  await app.send({actions: [{op: 'set_time', departure_at: draft.time.departure_at}],
    preview: {draft, changes: ['Time settings are unchanged.']}});
  await app.assertNoAdoption();
});

test('verification, turn and quote metadata cannot enable saving an unchanged requirement', async () => {
  const app = setup(), original = {...emptyPreferences(), active_constraints: [budget()]};
  const verified = {...emptyPreferences(), active_constraints: [budget({status: 'satisfied', evidence_source: 'route'})]};
  await app.send({preview: {preferences: original}, verified});
  await app.adopt();
  assert.match(app.element('chat-preferences').textContent, /Passed using reference data/);
  await app.send({preview: {preferences: {...emptyPreferences(), active_constraints: [budget({
    quote: 'Keep the same time budget', turn_index: 7, evidence_source: null})]}}});
  await app.assertNoAdoption();
});

test('new server no-change flags do not allow action or description text to override them', async () => {
  const app = setup();
  await app.send({actions: [{op: 'set_mode', value: 'walk'}], preview: {
    has_changes: false, semantic_changes: {itinerary: false, requirements: false},
    changes: ['Transport mode → walk'], adoption_blockers: ['no_changes']}});
  await app.assertNoAdoption();
});

test('a real preference-only edit still saves without changing places', async () => {
  const app = setup(), before = copy(app.trip.data);
  const preferences = {...emptyPreferences(), active_constraints: [{name: 'pace', value: 'relaxed',
    kind: 'preference', scope: 'global', hardness: 'soft', status: 'understood', source: 'user_explicit',
    quote: 'Make it relaxed', turn_index: 1}]};
  await app.send({preview: {preferences, has_changes: true,
    semantic_changes: {itinerary: false, requirements: true}}});
  assert.equal(app.element('chat-apply').disabled, false);
  await app.adopt();
  assert.deepEqual(copy(app.trip.data), before);
  assert.match(app.element('chat-preferences').textContent, /Relaxed/);
  assert.equal(app.requests.filter(r => r.url === '/api/stage2c/resolve').length, 1);
});

test('a real clearing-only draft remains adoptable while an omitted change preserves a legacy cap', async () => {
  const app = setup();
  app.trip.data.max_walk_minutes = 10;
  await app.send();
  await app.assertNoAdoption();
  const draft = {...copy(app.trip.data), max_walk_minutes: null};
  await app.send({preview: {draft, has_changes: true,
    semantic_changes: {itinerary: true, requirements: false}}});
  assert.equal(app.element('chat-apply').disabled, false);
  assert.equal(app.trip.data.max_walk_minutes, 10);
  await app.adopt();
  assert.equal(app.trip.data.max_walk_minutes, null);
});

function replacementSlot(current, selected = '') {
  return {slot_id: 's0', operation: 'replace_visit', target_visit_id: 'cafe', categories: ['food'],
    query: '', notices: [], selected_key: selected, options: [current, point('Different cafe', 103.853)].map((p,i) => ({
      candidate_key: 's0c'+i, label: p.label, point: p, address: 'Public fixture', basis: 'Offline fixture', distance_hint_m: 200}))};
}

test('selecting a different candidate can complete an initially unchanged replacement preview', async () => {
  const app = setup(), before = copy(app.trip.data), draft = copy(before);
  draft.visits[1].point = point('Different cafe', 103.853);
  await app.send({actions: [{op: 'replace_visit', target_id: 'cafe'}],
    slots: [replacementSlot(before.visits[1].point, 's0c0')], preview: {has_changes: false,
      semantic_changes: {itinerary: false, requirements: false}, adoption_blockers: ['no_changes']}, resolved: draft});
  assert.equal(app.element('chat-apply').disabled, true);
  const picker = app.element('chat-slots').querySelectorAll('select')[0];
  picker.value = 's0c1'; picker.onchange();
  assert.equal(app.element('chat-apply').disabled, false);
  await app.adopt();
  const resolve = app.requests.find(r => r.url === '/api/stage2c/resolve');
  assert.deepEqual(resolve.payload.choices, {s0: 's0c1'});
  const plan = app.requests.find(r => r.url === '/api/stage2b/plan');
  assert.equal(plan.payload.visits[1].point.longitude, 103.853);
  assert.deepEqual(copy(plan.payload.visits[0]), before.visits[0]);
  assert.equal(plan.payload.visits[1].visit_id, 'cafe'); assert.equal(plan.payload.visits[1].stay_minutes, 45);
  assert.deepEqual(copy(plan.payload.time), before.time);
  assert.equal(app.mapped.at(-1).visits[1].point.longitude, 103.853);
  assert.equal(app.completed().length, 1);
  assert.equal(app.requests.filter(r => r.url === '/api/stage2c/verification').length, 1);
});

test('a new selected origin remains adoptable when its initial preview has no material change', async () => {
  const app = setup(), selected = point('New origin', 103.851);
  const slot = {slot_id: 's0', operation: 'set_origin', categories: [], query: 'New origin', notices: [],
    selected_key: '', options: [{candidate_key: 's0c0', label: selected.label, point: selected,
      address: 'Public fixture', basis: 'Offline fixture', distance_hint_m: null}]};
  await app.send({actions: [{op: 'set_origin'}], slots: [slot], preview: {has_changes: false,
    semantic_changes: {itinerary: false, requirements: false}}, resolved: {...copy(app.trip.data), origin: selected}});
  assert.equal(app.element('chat-apply').disabled, true);
  const picker = app.element('chat-slots').querySelectorAll('select')[0];
  picker.value = 's0c0'; picker.onchange();
  assert.equal(app.element('chat-apply').disabled, false);
  await app.adopt();
  assert.equal(app.requests.find(r => r.url === '/api/stage2b/plan').payload.origin.longitude, 103.851);
});

test('selecting a different place never bypasses a real semantic blocker', async () => {
  const app = setup();
  await app.send({actions: [{op: 'replace_visit', target_id: 'cafe'}],
    slots: [replacementSlot(app.trip.data.visits[1].point, 's0c1')],
    preview: {adoption_blockers: ['no_changes', 'semantic_conflict'], has_changes: false,
      semantic_changes: {itinerary: false, requirements: false}}});
  assert.equal(app.element('chat-apply').disabled, true);
  await app.adopt();
  assert.equal(app.requests.filter(r => r.url === '/api/stage2c/resolve').length, 0);
});
