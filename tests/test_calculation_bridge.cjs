const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const T = require('../frontend/stage2b/state.js');

const root = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'frontend/stage2b/app.js'), 'utf8');
const bridge = fs.readFileSync(path.join(root, 'frontend/stage2c/bridge.js'), 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.value = ''; this.disabled = false;
    this.dataset = {}; this.attributes = {}; this.classes = new Set(); this.style = {};
    this.scrollHeight = 40; this.listeners = {};
    this.classList = {add: value => this.classes.add(value), remove: value => this.classes.delete(value),
      toggle: value => {if (this.classes.has(value)) {this.classes.delete(value); return false;}
        this.classes.add(value); return true;}};
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ''; this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  querySelectorAll(selector) {
    const matches = child => selector.startsWith('.')
      ? (child.className || '').split(' ').includes(selector.slice(1))
      : child.tagName === selector;
    return this.children.flatMap(child => [
      ...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)
    ]);
  }
}

function point(label, longitude) {
  return {label, latitude: 1.30, longitude, source: 'user_map', confirmed: true};
}

function planFor(payload) {
  const visits = copy(payload.visits).map(visit => ({
    ...visit, stay_basis: 'user_specified', stay_range_minutes: [30, 30],
    opening_status: 'closed_at_arrival', opening_hours_display: 'Daily 10:00–18:00',
    selected_entrance: {label: 'Museum Gate'},
    entrance_selection_basis: 'single_entrance_candidate'
  }));
  return {revision: payload.revision, mode: payload.mode, time: payload.time, visits,
    complete_plan_created: true, verification_ref: 'b'.repeat(32),
    totals: {travel_s: 300, stay_s: 1800, buffer_s: 0, reference_duration_s: 2100,
      duration_range_s: [2100, 2100], distance_m: 1000},
    budget_check: {status: 'all_estimates_fit', slack_s: 1500},
    route_calls: {provider_calls: 1, cache_hits: 0},
    walking_constraint_check: {status: 'not_requested', violations: []},
    opening_hours_checked: true, cautions: ['Fixture caution'],
    legs: [{kind: 'provider_route', distance_m: 1000, reused_in_memory: false,
      endpoint_offsets_m: {origin: 0, destination: 0}, endpoint_review_required: false,
      diagnostics: {large_detour_flag: false, geometry_summary_mismatch: false}}],
    timeline: [{kind: 'visit', visit_index: 0, title: visits[0].point.label,
      start_offset_s: 300, end_offset_s: 2100, duration_s: 1800}]
  };
}

function createCalculator(time = {}) {
  const elements = new Map(), events = [], requests = [], mapped = [], handlers = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const trip = new T.ItineraryState('a'.repeat(32));
  trip.data = {origin: point('Origin', 103.85),
    visits: [{visit_id: 'museum', point: point('Museum', 103.86),
      stay_minutes: 30, stay_profile: 'regular'}],
    finish_policy: 'last_stop', finish: null, mode: 'walk', max_walk_minutes: null,
    time: {mode: 'estimate', departure_at: '2026-10-28T19:00:00+08:00',
      finish_by: null, budget_minutes: null, buffer_minutes: 0, ...time}};
  trip.revision = 4;
  let response = payload => planFor(payload);
  const context = {T, PlayMapTrip: T, trip, $: element,
    MODE: {walk: 'Walk', cycle: 'Cycle', drive: 'Drive'},
    autoTimer: null, planController: null, formInvalid: false,
    AbortController, clearTimeout, setTimeout, confirm: () => true,
    document: {getElementById: element, createElement: tag => new Element(tag),
      querySelector: selector => selector === '.ov-thread' ? element('thread') : null},
    CustomEvent: class {constructor(type, init) {this.type = type; this.detail = init.detail;}},
    addEventListener: (name, listener) => {if (!handlers.has(name)) handlers.set(name, []);
      handlers.get(name).push(listener);},
    dispatchEvent: event => {events.push(event); for (const listener of handlers.get(event.type) || []) listener(event);},
    mapView: {viewRevision: 0, setPlan: plan => mapped.push(plan), markStale() {}, focusPoint() {}},
    cancelReplacement() {}, renderTrip() {}, invalidate() {},
    fetch: async (url, options = {}) => {
      const payload = options.body ? JSON.parse(options.body) : null;
      requests.push({url, payload, signal: options.signal});
      const result = await response(payload, url);
      return {ok: result.ok !== false, json: async () => result.body ?? result};
    }
  };
  context.window = context;
  vm.createContext(context);
  function load(start, end) {
    const from = source.indexOf(start), to = source.indexOf(end, from + start.length);
    assert.ok(from >= 0 && to > from, `Missing source section: ${start}`);
    vm.runInContext(source.slice(from, to), context, {filename: 'frontend/stage2b/app.js'});
  }
  // Execute production functions unchanged. Only DOM, map and HTTP are adapters.
  // In particular, calculate(), bridge.calculate(), readTime(), timeInputs(),
  // renderResult() and ItineraryState are not replaced; no completion event is injected.
  load('function node(', 'function tab(');
  if (source.includes('function clockInput(')) load('function clockInput(', 'function timeInputs(');
  load('function timeInputs(', 'function renderResult(');
  load('function renderResult(', 'function exportReport(');
  vm.runInContext(bridge, context, {filename: 'frontend/stage2c/bridge.js'});
  vm.runInContext('timeInputs()', context);
  return {trip, context, element, events, requests, mapped,
    bridge: context.PlayMapChatBridge,
    respondWith: value => {response = value;},
    completed: () => events.filter(event => event.type === 'playmap:plan-complete')};
}

test('adoption followed by calculation does not turn zero-second formatting into another edit', async () => {
  const app = createCalculator();
  const draft = copy(app.trip.data);
  draft.time.mode = 'budget'; draft.time.budget_minutes = 60;
  const adoptedRevision = app.bridge.apply(draft, 4);
  assert.equal(adoptedRevision, 5);
  assert.equal(app.element('departure').value, '2026-10-28T19:00');
  await app.bridge.calculate();
  assert.equal(app.trip.revision, adoptedRevision);
  assert.equal(app.requests[0].payload.revision, adoptedRevision);
  assert.equal(app.events.filter(event => event.type === 'playmap:edit').length, 1);
  assert.equal(app.bridge.activePlan().revision, adoptedRevision);
});

test('time inputs and the real bridge preserve nonzero seconds at both ends of a window', async () => {
  const app = createCalculator({mode: 'window', departure_at: '2026-10-28T19:00:37+08:00',
    finish_by: '2026-10-28T21:15:09+08:00'});
  assert.equal(app.element('departure').value, '2026-10-28T19:00:37');
  assert.equal(app.element('finish-by').value, '2026-10-28T21:15:09');
  const snapshot = app.bridge.snapshot();
  assert.equal(snapshot.revision, 4);
  await app.bridge.calculate();
  assert.equal(app.trip.revision, 4);
  assert.equal(app.requests[0].payload.time.departure_at, '2026-10-28T19:00:37+08:00');
  assert.equal(app.requests[0].payload.time.finish_by, '2026-10-28T21:15:09+08:00');
});

test('a successful current calculation emits its saved plan exactly once and retains C warnings', async () => {
  const app = createCalculator();
  await app.bridge.calculate();
  const completed = app.completed();
  assert.equal(completed.length, 1);
  assert.equal(completed[0].detail.revision, app.trip.revision);
  assert.strictEqual(completed[0].detail.plan, app.bridge.activePlan());
  assert.strictEqual(completed[0].detail.plan, app.mapped[0]);
  assert.equal(completed[0].detail.plan.verification_ref, 'b'.repeat(32));
  assert.match(app.element('plan-status').textContent, /Museum.*outside opening hours/);
  assert.match(app.element('timeline').textContent, /closed on arrival/);
  assert.match(app.element('timeline').textContent, /Museum Gate.*not field-verified/);
});

test('a failed plan request never emits a successful completion event', async () => {
  const app = createCalculator();
  app.respondWith(() => ({ok: false, body: {detail: {message: 'Fixture route failure', code: 'NO_ROUTE'}}}));
  await app.bridge.calculate();
  assert.equal(app.completed().length, 0);
  assert.equal(app.bridge.activePlan(), null);
  assert.equal(app.mapped.length, 0);
  assert.match(app.element('plan-status').textContent, /Fixture route failure/);
});

test('a result arriving after a real itinerary edit is not saved or announced', async () => {
  const app = createCalculator();
  let finish;
  app.respondWith(payload => new Promise(resolve => {finish = () => resolve(planFor(payload));}));
  const calculation = app.bridge.calculate();
  assert.equal(app.requests.length, 1);
  app.trip.setStay('museum', 45, 'regular');
  finish();
  await calculation;
  assert.equal(app.completed().length, 0);
  assert.equal(app.bridge.activePlan(), null);
  assert.equal(app.mapped.length, 0);
});

test('an incomplete result is not saved or announced', async () => {
  const app = createCalculator();
  app.respondWith(payload => ({...planFor(payload), complete_plan_created: false}));
  await app.bridge.calculate();
  assert.equal(app.completed().length, 0);
  assert.equal(app.bridge.activePlan(), null);
  assert.equal(app.mapped.length, 0);
});

test('a genuinely changed time still creates one edit before calculating', async () => {
  const app = createCalculator();
  app.element('departure').value = '2026-10-28T19:01';
  await app.bridge.calculate();
  assert.equal(app.trip.revision, 5);
  assert.equal(app.events.filter(event => event.type === 'playmap:edit').length, 1);
  assert.equal(app.requests[0].payload.time.departure_at, '2026-10-28T19:01+08:00');
  assert.equal(app.requests[0].payload.revision, 5);
});

test('cancelling an in-flight calculation does not announce its late response', async () => {
  const app = createCalculator();
  let finish;
  app.respondWith(payload => new Promise(resolve => {finish = () => resolve(planFor(payload));}));
  const calculation = app.bridge.calculate();
  vm.runInContext('cancelCalculation()', app.context);
  assert.equal(app.requests[0].signal.aborted, true);
  finish();
  await calculation;
  assert.equal(app.completed().length, 0);
  assert.equal(app.bridge.activePlan(), null);
});

function withRealChat(alternatives = null) {
  const app = createCalculator();
  const draft = copy(app.trip.data);
  draft.time.mode = 'budget'; draft.time.budget_minutes = 60;
  if (alternatives) draft.max_walk_minutes = 10;
  const preferences = {preferred: [], excluded: [], notes: [], active_constraints: [
    {name: 'budget_minutes', value: 60, scope: 'global', kind: 'constraint', hardness: 'hard',
      status: 'understood', source: 'user', quote: '一小时内', turn_index: 1}
  ]};
  const verificationFor = payload => ({verification: {status: 'matched'}, preferences: {
    ...copy(payload.preferences), active_constraints: payload.preferences.active_constraints.map(row => ({
      ...row, status: 'satisfied', evidence_source: 'route'
    }))
  }});
  let verification = verificationFor;
  app.respondWith((payload, url) => {
    if (url === '/api/stage2c/status') return {configured: true, model: 'fixture-model'};
    if (url === '/api/stage2b/plan') {
      const plan = planFor(payload);
      if (alternatives) {
        plan.visits[0].category = 'nature';
        plan.walking_constraint_check = {status: 'violation', violations: [
          {leg_id: 'leg-1', duration_minutes: 15, exceeded_by_minutes: 5}]};
      }
      return plan;
    }
    if (url === '/api/stage2b/alternatives' && alternatives) return alternatives;
    if (url === '/api/stage2c/chat') return {base_revision: payload.revision, ticket: 'fixture-ticket',
      model: 'fixture-model', acknowledgement: 'Review the budget change.', questions: [], slots: [],
      interpretation: {actions: [{op: 'set_time', budget_minutes: 60}]},
      preview: {can_apply: true, changes: ['Change the budget'], warnings: [], draft, preferences},
      usage: {generation_requests: 1, calls: []}};
    if (url === '/api/stage2c/resolve') return {draft, preferences, ready_to_calculate: true};
    if (url === '/api/stage2c/verification') return verification(payload);
    if (url === '/api/stage2c/forget') return {forgotten: true};
    throw Error('Unexpected fixture route: ' + url);
  });
  for (const filename of ['chat_state.js', 'chat.js']) {
    vm.runInContext(fs.readFileSync(path.join(root, 'frontend/stage2c', filename), 'utf8'),
      app.context, {filename: 'frontend/stage2c/' + filename});
  }
  return {...app, verificationFor, verifyWith: value => {verification = value;},
    verificationRequests: () => app.requests.filter(request => request.url === '/api/stage2c/verification'),
    async propose() {
      app.element('chat-input').value = '一小时内';
      await app.element('chat-form').onsubmit({preventDefault() {}});
      assert.equal(app.element('chat-apply').disabled, false);
    },
    adopt: () => app.element('chat-apply').onclick()};
}

test('the real chat adoption and completion listener share one verification request', async () => {
  const app = withRealChat();
  await app.propose();
  await app.adopt();
  assert.equal(app.trip.revision, 5);
  assert.equal(app.completed().length, 1);
  assert.equal(app.verificationRequests().length, 1);
  assert.equal(app.verificationRequests()[0].payload.revision, 5);
  assert.equal(app.verificationRequests()[0].payload.verification_ref, app.bridge.activePlan().verification_ref);
  assert.match(app.element('chat-preferences').textContent, /Passed using reference data.*Route check.*60 min/);
  assert.match(app.element('chat-log').textContent, /Reference route generated for 1 stop/);
});

test('a real edit during chat verification prevents its late evidence from being displayed', async () => {
  const app = withRealChat();
  let completeVerification;
  app.verifyWith(payload => new Promise(resolve => {
    completeVerification = () => resolve(app.verificationFor(payload));
  }));
  await app.propose();
  const adoption = app.adopt();
  for (let step = 0; step < 30 && !completeVerification; step++) await Promise.resolve();
  assert.equal(typeof completeVerification, 'function');
  assert.equal(app.verificationRequests().length, 1);
  app.trip.setStay('museum', 45, 'regular');
  completeVerification();
  await adoption;
  assert.equal(app.trip.revision, 6);
  assert.doesNotMatch(app.element('chat-preferences').textContent, /Passed using reference data|Route check/);
  assert.doesNotMatch(app.element('chat-log').textContent, /Reference route generated for 1 stop/);
});

test('clearing the chat during verification does not restore a late satisfied badge', async () => {
  const app = withRealChat();
  let completeVerification;
  app.verifyWith(payload => new Promise(resolve => {
    completeVerification = () => resolve(app.verificationFor(payload));
  }));
  await app.propose();
  const adoption = app.adopt();
  for (let step = 0; step < 30 && !completeVerification; step++) await Promise.resolve();
  assert.equal(typeof completeVerification, 'function');
  await app.element('chat-reset').onclick();
  completeVerification();
  await adoption;
  assert.equal(app.trip.revision, 5);
  assert.equal(app.verificationRequests().length, 1);
  assert.doesNotMatch(app.element('chat-preferences').textContent, /Passed using reference data|Route check/);
  assert.doesNotMatch(app.element('chat-log').textContent, /Reference route generated for 1 stop/);
});


test('walking alternatives label checked and unknown plans separately without applying either', async () => {
  const app = withRealChat({suggestions: [
    {label: 'Checked Park', route_duration_min: 8, feasible: true,
      feasibility_check: {checks: {opening_hours: true, time_budget: true, walking_constraint: true}}},
    {label: 'Unknown Cafe', route_duration_min: 5, feasible: null,
      feasibility_check: {checks: {opening_hours: null, time_budget: true, walking_constraint: true}}},
    {label: 'Incomplete Walk Check', route_duration_min: 4, feasible: null,
      feasibility_check: {checks: {opening_hours: true, time_budget: true, walking_constraint: null}}}
  ]});
  await app.propose();
  await app.adopt();
  const alternativeRequests = app.requests.filter(request => request.url === '/api/stage2b/alternatives');
  assert.equal(alternativeRequests.length, 1);
  assert.equal(alternativeRequests[0].payload.leg_id, 'leg-1');
  assert.equal(app.trip.revision, 5);
  assert.equal(app.trip.data.visits[0].point.label, 'Museum');
  assert.equal(app.trip.data.visits[0].stay_minutes, 30);
  assert.equal(app.completed().length, 1);
  const reply = app.element('chat-log').children.at(-1).textContent;
  assert.match(reply, /Checked Park.*Passes current feasibility checks/);
  assert.match(reply, /Unknown Cafe.*Opening hours need checking/);
  assert.match(reply, /Incomplete Walk Check.*Walking limit needs checking/);
  assert.doesNotMatch(reply, /These nearby places.*meet the walking limit/);
  assert.match(reply, /The itinerary has not changed/);
});

test('an empty alternatives result reports full-itinerary checks rather than only walking distance', async () => {
  const app = withRealChat({suggestions: []});
  await app.propose();
  await app.adopt();
  assert.match(app.element('chat-log').children.at(-1).textContent, /No nearby replacement is available after checking the full itinerary/);
  assert.equal(app.trip.revision, 5);
  assert.equal(app.trip.data.visits[0].point.label, 'Museum');
});


const waitForAsync = async predicate => {
  for (let attempt = 0; attempt < 80; attempt++) {
    if (predicate()) return;
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.fail('Expected asynchronous request was not reached');
};
const oldAlternative = {suggestions: [{label: 'OBSOLETE PARK', route_duration_min: 8, feasible: true,
  feasibility_check: {checks: {opening_hours: true, time_budget: true, walking_constraint: true}}}]};

for (const transition of ['edit', 'clear', 'new', 'switch_aba', 'session']) {
  for (const outcome of ['success', 'error']) {
    test(`late alternatives ${outcome} is discarded after ${transition}`, async () => {
      let release, reject;
      const app = withRealChat(new Promise((resolve, fail) => {release = resolve; reject = fail;}));
      await app.propose();
      if (transition === 'switch_aba') {
        app.element('chat-new').onclick();
        await app.propose();
        app.element('chat-history').children[1].onclick();
        await app.propose();
      }
      const adoption = app.adopt();
      await waitForAsync(() => app.requests.some(r => r.url === '/api/stage2b/alternatives'));
      if (transition === 'edit') app.trip.setStay('museum', 90, 'regular');
      if (transition === 'session') app.trip.sessionId = 'c'.repeat(32);
      if (transition === 'clear') await app.element('chat-reset').onclick();
      if (transition === 'new') app.element('chat-new').onclick();
      if (transition === 'switch_aba') {
        const active = app.element('chat-history').children.find(b => b.className.includes('selected'));
        app.element('chat-history').children.find(b => !b.className.includes('selected')).onclick();
        active.onclick();
      }
      const before = app.element('chat-log').textContent;
      if (outcome === 'success') release(oldAlternative); else reject(Error('OBSOLETE FAILURE'));
      await adoption;
      assert.equal(app.element('chat-log').textContent, before);
      assert.doesNotMatch(app.element('chat-log').textContent, /OBSOLETE PARK|alternatives search did not finish/);
      assert.equal(app.trip.data.visits[0].stay_minutes, transition === 'edit' ? 90 : 30);
    });
  }
}

test('switching A to B to A cancels a pending chat reply and clears pending adoption controls', async () => {
  const app = withRealChat();
  await app.propose();
  app.element('chat-new').onclick();
  let release;
  app.respondWith((payload, url) => url === '/api/stage2c/chat' ? new Promise(resolve => {release = resolve;}) : {});
  app.element('chat-input').value = 'THREAD B QUESTION';
  const sending = app.element('chat-form').onsubmit({preventDefault() {}});
  await waitForAsync(() => !!release);
  const threadB = app.element('chat-history').children[0];
  app.element('chat-history').children[1].onclick();
  threadB.onclick();
  const before = app.element('chat-log').textContent;
  release({base_revision: app.trip.revision, ticket: 'old', model: 'fixture', acknowledgement: 'OBSOLETE REPLY',
    questions: [], slots: [], interpretation: {actions: [{op: 'set_time', budget_minutes: 60}]},
    preview: {can_apply: true, changes: ['Change budget'], warnings: [], draft: copy(app.trip.data),
      preferences: {preferred: [], excluded: [], notes: [], active_constraints: []}},
    usage: {generation_requests: 1, calls: []}});
  await sending;
  assert.equal(app.element('chat-log').textContent, before);
  assert.equal(app.element('chat-proposal').hidden, true);
  assert.equal(app.element('chat-apply').disabled, true);
  assert.equal(app.element('chat-send').disabled, false);
  assert.match(app.element('chat-status').textContent, /Switched chat.*Pending/);
  assert.equal(app.trip.revision, 4);
});

test('switching history invalidates pending resolve errors as well as successful application', async () => {
  const app = withRealChat();
  await app.propose();
  app.element('chat-new').onclick();
  await app.propose();
  let fail;
  app.respondWith((payload, url) => url === '/api/stage2c/resolve' ? new Promise((_, reject) => {fail = reject;}) : {});
  const adoption = app.adopt();
  await waitForAsync(() => !!fail);
  app.element('chat-history').children[1].onclick();
  const status = app.element('chat-status').textContent;
  fail(Error('OBSOLETE RESOLVE ERROR'));
  await adoption;
  assert.equal(app.element('chat-status').textContent, status);
  assert.equal(app.element('chat-proposal').hidden, true);
  assert.equal(app.element('chat-apply').disabled, true);
  assert.equal(app.trip.revision, 4);
});


for (const transition of ['clear', 'new']) {
  test(`a calculation finishing after ${transition} cannot start old chat verification or append a reply`, async () => {
    const app = withRealChat();
    await app.propose();
    let release;
    const draft = copy(app.trip.data);
    draft.time = {...draft.time, mode: 'budget', budget_minutes: 60};
    const preferences = {preferred: [], excluded: [], notes: [], active_constraints: []};
    app.respondWith((payload, url) => {
      if (url === '/api/stage2c/resolve') return {draft, preferences, ready_to_calculate: true};
      if (url === '/api/stage2b/plan') return new Promise(resolve => {release = () => resolve(planFor(payload));});
      if (url === '/api/stage2c/forget') return {forgotten: true};
      throw Error('Unexpected late request: ' + url);
    });
    const adoption = app.adopt();
    await waitForAsync(() => !!release);
    if (transition === 'clear') await app.element('chat-reset').onclick();
    else app.element('chat-new').onclick();
    const before = app.element('chat-log').textContent;
    release(); await adoption;
    assert.equal(app.element('chat-log').textContent, before);
    assert.equal(app.verificationRequests().length, 0);
    assert.equal(app.trip.revision, 5);
    assert.ok(app.bridge.activePlan());
  });
}


test('a newer manual calculation at the same revision still verifies while an old chat request settles', async () => {
  const app = withRealChat();
  await app.propose();
  let releaseOld, planCount = 0;
  const draft = copy(app.trip.data);
  draft.time = {...draft.time, mode: 'budget', budget_minutes: 60};
  const preferences = {preferred: [], excluded: [], notes: [], active_constraints: []};
  app.respondWith((payload, url) => {
    if (url === '/api/stage2c/resolve') return {draft, preferences, ready_to_calculate: true};
    if (url === '/api/stage2b/plan') {
      planCount++;
      if (planCount === 1) return new Promise(resolve => {releaseOld = () => resolve(planFor(payload));});
      return {...planFor(payload), verification_ref: 'c'.repeat(32)};
    }
    if (url === '/api/stage2c/verification') return app.verificationFor(payload);
    throw Error('Unexpected fixture request: ' + url);
  });
  const adoption = app.adopt();
  await waitForAsync(() => !!releaseOld);
  app.element('chat-new').onclick();
  await app.bridge.calculate();
  await waitForAsync(() => app.verificationRequests().length === 1);
  releaseOld(); await adoption;
  assert.equal(app.trip.revision, 5);
  assert.equal(app.bridge.activePlan().verification_ref, 'c'.repeat(32));
  assert.equal(app.verificationRequests()[0].payload.verification_ref, 'c'.repeat(32));
  assert.equal(app.element('chat-log').textContent, '');
});
