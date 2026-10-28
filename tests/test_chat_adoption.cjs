const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tripState = require('../frontend/stage2b/state.js');

const frontend = path.join(__dirname, '..', 'frontend', 'stage2c');
const copy = value => JSON.parse(JSON.stringify(value));
const emptyPreferences = () => ({preferred: [], excluded: [], notes: [], active_constraints: []});
const point = label => ({label, lat: 1.3, lon: 103.8, source: 'catalogue_representative'});

class Element {
  constructor(tagName = 'div') {
    this.tagName = tagName;
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this.hidden = false;
    this.disabled = false;
    this.value = '';
    this.scrollHeight = 40;
    this.listeners = {};
    this.classes = new Set();
    this.classList = {
      remove: name => this.classes.delete(name),
      toggle: name => {
        if (this.classes.has(name)) { this.classes.delete(name); return false; }
        this.classes.add(name); return true;
      }
    };
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ''; this.children = [...children]; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  querySelectorAll(tagName) {
    return this.children.flatMap(child => [
      ...(child.tagName === tagName ? [child] : []), ...child.querySelectorAll(tagName)
    ]);
  }
  click() { if (!this.disabled) return this.onclick?.(); }
}

function proposal(overrides = {}) {
  return {
    base_revision: 1, ticket: 'ticket-1', model: 'fake-model',
    acknowledgement: 'Cafe candidates found. Review your choice.', questions: [],
    interpretation: {actions: [{op: 'add_visit'}]},
    slots: [{
      slot_id: 'slot-1', target_visit_id: 'v-cafe', operation: 'add_visit', categories: ['food'], query: '',
      selected_key: 'cafe-a', notices: [],
      options: ['cafe-a', 'cafe-b'].map(key => ({
        candidate_key: key, label: key, point: point(key), basis: 'Catalogue category: cafe',
        address: 'Singapore', distance_hint_m: null
      }))
    }],
    preview: {can_apply: true, changes: ['Add a cafe stop'], warnings: [], preferences: emptyPreferences()},
    usage: {generation_requests: 1, calls: []}, ...overrides
  };
}

function createChat() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const thread = new Element();
  const handlers = new Map();
  const requests = [];
  const responses = {chat: [], resolve: [], verification: [], forget: []};
  let revision = 1;
  let draft = {origin: point('Bugis'), visits: [], max_walk_minutes: null};
  let applyFailure = null;
  let plan = {};
  const applied = [], focused = [];
  let calculations = 0;
  const context = {
    document: {
      getElementById: element,
      createElement: tag => new Element(tag),
      querySelector: selector => selector === '.ov-thread' ? thread : null
    },
    AbortController, setTimeout, clearTimeout,
    confirm: () => true,
    addEventListener: (name, listener) => handlers.set(name, listener),
    dispatchEvent: event => handlers.get(event.type)?.(event),
    fetch: async (url, options = {}) => {
      const route = url.split('/').pop();
      if (route === 'status') return {ok: true, json: async () => ({configured: true, model: 'fake-model'})};
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({route, body, signal: options.signal});
      const next = responses[route]?.shift();
      assert.notEqual(next, undefined, `Unexpected request: ${route}`);
      const result = await (typeof next === 'function' ? next(body) : next);
      return {ok: result.ok !== false, json: async () => copy(result.body ?? result)};
    },
    PlayMapTrip: {duration: seconds => `${seconds / 60} min`},
    PlayMapChatBridge: {
      snapshot: () => ({session_id: 'session-1', revision, draft: copy(draft)}),
      currentRevision: () => revision,
      currentSession: () => 'session-1',
      hasOrigin: () => !!draft.origin,
      proposeDeviceOrigin: async () => false,
      currentData: () => copy(draft),
      apply: (value, expected) => {
        assert.equal(expected, revision);
        if (applyFailure) throw applyFailure;
        applied.push(copy(value));
        draft = copy(value); revision++;
        context.dispatchEvent({type: 'playmap:edit', detail: {kind: 'change', revision}});
      },
      calculate: async () => {
        calculations++;
        const active = {revision, visits: draft.visits, totals: {reference_duration_s: 3600}, ...plan};
        context.dispatchEvent({type: 'playmap:plan-complete', detail: {plan: active, revision}});
      },
      activePlan: () => ({revision, visits: draft.visits, totals: {reference_duration_s: 3600}, ...plan}),
      diagnostic: () => ({}), focus: value => focused.push(copy(value))
    }
  };
  context.window = context;
  vm.createContext(context);
  for (const filename of ['chat_state.js', 'chat.js']) {
    vm.runInContext(fs.readFileSync(path.join(frontend, filename), 'utf8'), context, {filename});
  }
  return {
    element, requests, responses, applied, focused,
    get draft() { return copy(draft); },
    get calculations() { return calculations; },
    get revision() { return revision; },
    failApply: error => { applyFailure = error; },
    setPlan: value => { plan = value; },
    send: async (text, response = proposal({base_revision: revision})) => {
      responses.chat.push(response); element('chat-input').value = text;
      return element('chat-form').onsubmit({preventDefault() {}});
    },
    resolve: (preferences = emptyPreferences(), ready = false) => responses.resolve.push({
      draft: {...copy(draft), visits: [{visit_id: 'v-cafe', point: point('cafe-a')}]},
      preferences, ready_to_calculate: ready
    }),
    manualEdit: () => {
      draft.visits.push({visit_id: 'manual', point: point('Manual stop')}); revision++;
      context.dispatchEvent({type: 'playmap:edit', detail: {kind: 'change', revision}});
    }
  };
}

test('selected recommendations remain a visible proposal until the user confirms', async () => {
  const chat = createChat();
  await chat.send('找一家咖啡馆');
  assert.equal(chat.applied.length, 0);
  assert.equal(chat.requests.filter(request => request.route === 'resolve').length, 0);
  assert.equal(chat.element('chat-proposal').hidden, false);
  assert.equal(chat.element('chat-apply').disabled, false);
  const select = chat.element('chat-slots').querySelectorAll('select')[0];
  select.value = 'cafe-b'; select.onchange();
  assert.equal(chat.applied.length, 0);
});

test('one explicit confirmation applies the chosen candidate exactly once during repeated clicks', async () => {
  const chat = createChat();
  await chat.send('找一家咖啡馆');
  const select = chat.element('chat-slots').querySelectorAll('select')[0];
  select.value = 'cafe-b'; select.onchange();
  let finishResolve;
  chat.responses.resolve.push(new Promise(resolve => { finishResolve = resolve; }));
  const confirmation = chat.element('chat-apply').onclick();
  await chat.element('chat-apply').onclick();
  assert.equal(chat.requests.filter(request => request.route === 'resolve').length, 1);
  assert.equal(chat.applied.length, 0);
  finishResolve({draft: {...chat.draft, visits: [{visit_id: 'v-cafe', point: point('cafe-b')}]},
    preferences: emptyPreferences(), ready_to_calculate: true});
  await confirmation;
  assert.equal(chat.applied.length, 1);
  assert.equal(chat.draft.visits[0].point.label, 'cafe-b');
  assert.equal(chat.calculations, 1);
  assert.deepEqual(chat.requests.find(request => request.route === 'resolve').body.choices, {'slot-1': 'cafe-b'});
  await chat.element('chat-apply').onclick();
  assert.equal(chat.applied.length, 1);
});

test('known conflicts block confirmation even when every candidate is selected', async () => {
  const chat = createChat();
  const candidate = proposal();
  candidate.preview.can_apply = false;
  candidate.preview.warnings = ['An existing stop conflicts with the global exclusion. Confirm the scope.'];
  await chat.send('全局排除购物', candidate);
  assert.equal(chat.element('chat-apply').disabled, true);
  assert.match(chat.element('chat-readiness').textContent, /Cannot adopt yet/);
  await chat.element('chat-apply').onclick();
  assert.equal(chat.requests.filter(request => request.route === 'resolve').length, 0);
  assert.equal(chat.applied.length, 0);
});

test('cancelling a ready pending proposal preserves adopted state and clears its next-turn context', async () => {
  const chat = createChat();
  const adoptedPreferences = {...emptyPreferences(), preferred: ['nature']};
  const first = proposal(); first.preview.preferences = adoptedPreferences;
  await chat.send('喜欢自然', first);
  chat.resolve(adoptedPreferences);
  await chat.element('chat-apply').click();
  const originalDraft = chat.draft;
  await chat.send('再找一家咖啡馆');
  assert.equal(chat.element('chat-cancel').disabled, false);
  assert.match(chat.element('chat-cancel').textContent, /Cancel draft/);
  chat.element('chat-cancel').click();
  assert.equal(chat.element('chat-proposal').hidden, true);
  assert.deepEqual(chat.draft, originalDraft);
  await chat.send('重新找个公园');
  const latest = chat.requests.filter(request => request.route === 'chat').at(-1).body;
  assert.equal(latest.pending, null);
  assert.deepEqual(latest.preferences, adoptedPreferences);
  assert.equal(chat.applied.length, 1);
});

test('local selection can complete a pending slot when the server reports no other adoption blockers', async () => {
  const chat = createChat();
  const candidate = proposal();
  candidate.slots[0].selected_key = '';
  candidate.preview.can_apply = false;
  candidate.preview.unresolved_slots = ['slot-1'];
  candidate.preview.adoption_blockers = [];
  await chat.send('找个咖啡馆，先让我选', candidate);
  assert.equal(chat.element('chat-apply').disabled, true);
  const select = chat.element('chat-slots').querySelectorAll('select')[0];
  select.value = 'cafe-b'; select.onchange();
  assert.equal(chat.element('chat-apply').disabled, false);
  assert.match(chat.element('chat-readiness').textContent, /not applied/);
  chat.resolve();
  await chat.element('chat-apply').click();
  assert.equal(chat.applied.length, 1);
});

test('non-selection blockers stay blocked after candidate changes', async () => {
  const chat = createChat();
  const candidate = proposal();
  candidate.preview.adoption_blockers = ['semantic_conflict'];
  await chat.send('存在冲突的请求', candidate);
  const select = chat.element('chat-slots').querySelectorAll('select')[0];
  select.value = 'cafe-b'; select.onchange();
  assert.equal(chat.element('chat-apply').disabled, true);
  await chat.element('chat-apply').onclick();
  assert.equal(chat.requests.filter(request => request.route === 'resolve').length, 0);
});

test('expired tickets discard pending context without changing the adopted itinerary', async () => {
  const chat = createChat();
  await chat.send('找一家咖啡馆');
  const originalDraft = chat.draft;
  chat.responses.resolve.push({ok: false, body: {detail: {code: 'PROPOSAL_EXPIRED', message: 'Draft expired'}}});
  await chat.element('chat-apply').click();
  assert.equal(chat.applied.length, 0);
  assert.deepEqual(chat.draft, originalDraft);
  assert.equal(chat.element('chat-proposal').hidden, true);
  await chat.send('重新找一家');
  assert.equal(chat.requests.filter(request => request.route === 'chat').at(-1).body.pending, null);
});

test('adopted and proposed ledgers show requirement kind, scope, hardness and verification status separately', async () => {
  const chat = createChat();
  const rows = [
    {kind: 'constraint', name: 'place_type', value: 'cafe', scope: 'visit_id:v-cafe', hardness: 'hard', status: 'unresolved'},
    {kind: 'preference', name: 'drink', value: 'coffee', scope: 'visit_id:v-cafe', hardness: 'soft', status: 'understood'},
    {name: 'excluded_topic', value: 'shopping', scope: 'future_visits', hardness: 'hard', status: 'unsupported'},
    {kind: 'constraint', name: 'budget_minutes', value: 180, scope: 'global', hardness: 'hard', status: 'satisfied'}
  ].map(row => ({...row, source: 'user_explicit', quote: '测试要求', turn_index: 1}));
  const adoptedPreferences = {...emptyPreferences(), active_constraints: rows};
  const candidate = proposal();
  candidate.preview.preferences = adoptedPreferences;
  candidate.preview.draft = {...chat.draft, visits: [{visit_id: 'v-cafe', point: point('cafe-a')}]};
  await chat.send('请找咖啡馆，喜欢喝咖啡，以后别加购物，总共三小时', candidate);
  const pendingText = chat.element('chat-proposed-prefs').textContent;
  assert.match(pendingText, /Proposed requirements/);
  assert.match(pendingText, /Place type.*Cafe/);
  assert.match(pendingText, /Requirement.*Required.*cafe-a.*not verified/);
  assert.match(pendingText, /Preference.*Preferred.*cafe-a.*not verified/);
  assert.match(pendingText, /Future additions only/);
  assert.match(pendingText, /Cannot currently verify/);
  assert.match(pendingText, /Passed using reference data/);
  assert.doesNotMatch(chat.element('chat-preferences').textContent, /Cafe/);
  const select = chat.element('chat-slots').querySelectorAll('select')[0];
  select.value = 'cafe-b'; select.onchange();
  assert.match(chat.element('chat-proposed-prefs').textContent, /Stop cafe-b/);
  assert.doesNotMatch(chat.element('chat-proposed-prefs').textContent, /Stop cafe-a/);
  select.value = 'cafe-a'; select.onchange();
  chat.resolve(adoptedPreferences);
  await chat.element('chat-apply').click();
  const adoptedText = chat.element('chat-preferences').textContent;
  assert.match(adoptedText, /Adopted requirements/);
  assert.match(adoptedText, /Place type.*Cafe/);
  assert.match(adoptedText, /Preference.*Preferred/);
  const next = proposal({base_revision: chat.revision});
  next.preview.preferences.active_constraints = [{kind: 'preference', name: 'environment', value: 'indoor',
    scope: 'global', hardness: 'soft', status: 'understood', source: 'user_explicit', quote: '喜欢室内', turn_index: 2}];
  await chat.send('喜欢室内', next);
  assert.match(chat.element('chat-proposed-prefs').textContent, /Indoors/);
  assert.equal(chat.element('chat-preferences').textContent, adoptedText);
  chat.element('chat-cancel').click();
  assert.equal(chat.element('chat-preferences').textContent, adoptedText);
});

for (const failure of ['resolve', 'apply']) {
  test(`${failure} failure keeps the adopted draft and requirement memory unchanged`, async () => {
    const chat = createChat();
    const originalDraft = chat.draft;
    const proposedPreferences = {...emptyPreferences(), preferred: ['nature']};
    const candidate = proposal(); candidate.preview.preferences = proposedPreferences;
    await chat.send('喜欢自然，再加咖啡馆', candidate);
    if (failure === 'resolve') chat.responses.resolve.push({ok: false, body: {detail: {code: 'REJECTED', message: 'Cannot save yet'}}});
    else { chat.resolve(proposedPreferences); chat.failApply(new Error('Time inputs are incomplete')); }
    await chat.element('chat-apply').click();
    assert.deepEqual(chat.draft, originalDraft);
    assert.equal(chat.revision, 1);
    assert.equal(chat.applied.length, 0);
    assert.equal(chat.calculations, 0);
    assert.match(chat.element('chat-status').className, /error/);
    await chat.send('重新确认');
    assert.deepEqual(chat.requests.filter(request => request.route === 'chat').at(-1).body.preferences, emptyPreferences());
  });
}

test('manual itinerary changes invalidate an outstanding proposal response', async () => {
  const chat = createChat();
  let finishChat;
  const response = new Promise(resolve => { finishChat = resolve; });
  const sending = chat.send('找一家咖啡馆', response);
  chat.manualEdit();
  finishChat(proposal());
  await sending;
  assert.equal(chat.element('chat-proposal').hidden, true);
  assert.equal(chat.draft.visits[0].visit_id, 'manual');
  assert.equal(chat.applied.length, 0);
  await chat.send('按新行程继续');
  assert.equal(chat.requests.filter(request => request.route === 'chat').at(-1).body.pending, null);
});

test('cancelled confirmation ignores a late resolve response', async () => {
  const chat = createChat();
  await chat.send('找一家咖啡馆');
  const originalDraft = chat.draft;
  let finishResolve;
  chat.responses.resolve.push(new Promise(resolve => { finishResolve = resolve; }));
  const adopting = chat.element('chat-apply').click();
  chat.element('chat-cancel').click();
  finishResolve({draft: {...chat.draft, visits: [{visit_id: 'late', point: point('late cafe')}]},
    preferences: {...emptyPreferences(), preferred: ['food']}, ready_to_calculate: true});
  await adopting;
  assert.deepEqual(chat.draft, originalDraft);
  assert.equal(chat.applied.length, 0);
  assert.equal(chat.calculations, 0);
  assert.equal(chat.element('chat-proposal').hidden, true);
  await chat.send('重新开始');
  const request = chat.requests.filter(request => request.route === 'chat').at(-1).body;
  assert.equal(request.pending, null);
  assert.deepEqual(request.preferences, emptyPreferences());
});

test('an empty candidate slot cannot be bypassed by a partial selection', async () => {
  const chat = createChat();
  const candidate = proposal();
  candidate.slots.push({...copy(candidate.slots[0]), slot_id: 'slot-2', selected_key: '', options: []});
  candidate.preview.adoption_blockers = [];
  await chat.send('找咖啡馆和公园', candidate);
  assert.equal(chat.element('chat-apply').disabled, true);
  assert.match(chat.element('chat-readiness').textContent, /No candidates for Item 2/);
  await chat.element('chat-apply').onclick();
  assert.equal(chat.requests.filter(request => request.route === 'resolve').length, 0);
});

test('requirements without itinerary edits can be saved while unverified', async () => {
  const chat = createChat();
  const preferences = {...emptyPreferences(), active_constraints: [{kind: 'preference', name: 'pace', value: 'relaxed',
    scope: 'global', hardness: 'soft', status: 'understood', source: 'user_explicit', quote: '轻松点', turn_index: 1}]};
  const candidate = proposal({slots: [], interpretation: {actions: []},
    preview: {can_apply: true, adoption_blockers: [], changes: [], warnings: [], preferences}});
  await chat.send('轻松点', candidate);
  assert.equal(chat.element('chat-apply').disabled, false);
  assert.match(chat.element('chat-proposed-prefs').textContent, /not verified.*Relaxed/);
  chat.resolve(preferences);
  await chat.element('chat-apply').click();
  assert.equal(chat.applied.length, 1);
  assert.match(chat.element('chat-preferences').textContent, /not verified.*Relaxed/);
});

test('a no-change response leaves the confirmation button disabled', async () => {
  const chat = createChat();
  await chat.send('再说一遍', proposal({slots: [], interpretation: {actions: []},
    preview: {can_apply: true, adoption_blockers: [], changes: [], warnings: [], preferences: emptyPreferences()}}));
  assert.equal(chat.element('chat-apply').disabled, true);
  assert.match(chat.element('chat-readiness').textContent, /No changes to adopt/);
  await chat.element('chat-apply').onclick();
  assert.equal(chat.requests.filter(request => request.route === 'resolve').length, 0);
});

test('offset times display as Singapore time without inventing a date for a floating time', async () => {
  const chat = createChat();
  const candidate = proposal();
  candidate.preview.preferences.active_constraints = [
    {name: 'departure_at', value: '2026-10-27T10:00:00Z'},
    {name: 'finish_by', value: '21:00'}
  ].map(row => ({...row, kind: 'constraint', scope: 'global', hardness: 'hard', status: 'unresolved',
    source: 'user_explicit', quote: '测试时间', turn_index: 1}));
  await chat.send('测试时间', candidate);
  const lines = chat.element('chat-proposed-prefs').textContent.split('\n');
  assert.match(lines.find(line => line.includes('Departure time')), /27\/10\/2026.*18:00.*Singapore time/);
  assert.match(lines.find(line => line.includes('Finish time')), /21:00/);
  assert.doesNotMatch(lines.find(line => line.includes('Finish time')), /2026/);
});

test('route verification refreshes requirements without reapplying the draft or advancing its revision', async () => {
  const chat = createChat();
  const preferences = {...emptyPreferences(), active_constraints: [{kind: 'constraint', name: 'max_walk_minutes', value: 10,
    scope: 'global', hardness: 'hard', status: 'understood', source: 'user_explicit', quote: '每段最多十分钟', turn_index: 1}]};
  const verified = copy(preferences); verified.active_constraints[0].status = 'satisfied';
  verified.active_constraints[0].source = 'route';
  chat.setPlan({verification_ref: 'proof-1'});
  chat.responses.verification.push({preferences: verified,
    verification: {status: 'matched', reason: 'matched', checked_requirements: 1}, generation_requests: 0});
  await chat.send('每段最多十分钟');
  chat.resolve(preferences, true);
  await chat.element('chat-apply').click();
  const verification = chat.requests.find(request => request.route === 'verification');
  assert.ok(verification);
  assert.equal(verification.body.revision, 2);
  assert.equal(verification.body.session_id, 'session-1');
  assert.equal(verification.body.verification_ref, 'proof-1');
  assert.deepEqual(verification.body.draft, chat.draft);
  assert.deepEqual(verification.body.preferences, preferences);
  assert.equal(chat.requests.filter(request => request.route === 'verification').length, 1);
  assert.equal(chat.applied.length, 1);
  assert.equal(chat.revision, 2);
  assert.match(chat.element('chat-preferences').textContent, /Passed using reference data/);
  await chat.send('继续');
  const next = chat.requests.filter(request => request.route === 'chat').at(-1).body;
  assert.equal(next.verification_ref, 'proof-1');
  assert.deepEqual(next.preferences, verified);
});

test('time comparison treats equivalent offset timestamps as equal but preserves seconds', () => {
  const base = {mode: 'window', budget_minutes: null, buffer_minutes: 0,
    departure_at: '2026-10-27T09:00:00+08:00', finish_by: '2026-10-27T18:00:00+08:00'};
  assert.equal(tripState.sameTime(base, {...base, departure_at: '2026-10-27T01:00:00Z'}), true);
  assert.equal(tripState.sameTime(base, {...base, departure_at: '2026-10-27T09:00:01+08:00'}), false);
});

test('editing the adopted itinerary invalidates displayed proof and omits its reference on the next request', async () => {
  const chat = createChat();
  const verified = {...emptyPreferences(), active_constraints: [{kind: 'constraint', name: 'max_walk_minutes', value: 10,
    scope: 'global', hardness: 'hard', status: 'satisfied', source: 'route', quote: '每段最多十分钟', turn_index: 1}]};
  chat.setPlan({verification_ref: 'proof-1'});
  chat.responses.verification.push({preferences: verified,
    verification: {status: 'matched', reason: 'matched', checked_requirements: 1}, generation_requests: 0});
  await chat.send('每段最多十分钟');
  chat.resolve(emptyPreferences(), true);
  await chat.element('chat-apply').click();
  assert.match(chat.element('chat-preferences').textContent, /Passed using reference data/);
  chat.manualEdit();
  assert.doesNotMatch(chat.element('chat-preferences').textContent, /Passed using reference data/);
  assert.match(chat.element('chat-preferences').textContent, /not verified/);
  await chat.send('继续');
  const next = chat.requests.filter(request => request.route === 'chat').at(-1).body;
  assert.equal(next.verification_ref, undefined);
  assert.equal(next.preferences.active_constraints[0].status, 'understood');
});

test('clearing the conversation preserves the adopted draft and requirements but drops pending context', async () => {
  const chat = createChat();
  const preferences = {...emptyPreferences(), preferred: ['nature']};
  await chat.send('喜欢自然');
  chat.resolve(preferences);
  await chat.element('chat-apply').click();
  const originalDraft = chat.draft;
  await chat.send('还想喝咖啡');
  chat.responses.forget.push({forgotten: true});
  await chat.element('chat-reset').click();
  assert.deepEqual(chat.draft, originalDraft);
  assert.match(chat.element('chat-preferences').textContent, /Nature/);
  assert.equal(chat.element('chat-proposal').hidden, true);
  assert.equal(chat.element('chat-log').textContent, '');
  await chat.send('重新开始');
  const next = chat.requests.filter(request => request.route === 'chat').at(-1).body;
  assert.deepEqual(next.preferences, preferences);
  assert.equal(next.pending, null);
  assert.equal(chat.requests.filter(request => request.route === 'forget').length, 1);
});

test('clearing the conversation rejects an outstanding proof response even when the draft revision is unchanged', async () => {
  const chat = createChat();
  const preferences = {...emptyPreferences(), active_constraints: [{kind: 'constraint', name: 'max_walk_minutes', value: 10,
    scope: 'global', hardness: 'hard', status: 'understood', source: 'user_explicit', quote: '每段最多十分钟', turn_index: 1}]};
  const verified = copy(preferences); verified.active_constraints[0].status = 'satisfied';
  let finishVerification, signalVerification;
  const verificationStarted = new Promise(resolve => { signalVerification = resolve; });
  const verificationResponse = new Promise(resolve => { finishVerification = resolve; });
  chat.setPlan({verification_ref: 'proof-late'});
  chat.responses.verification.push(() => { signalVerification(); return verificationResponse; });
  await chat.send('每段最多十分钟');
  chat.resolve(preferences, true);
  const adopting = chat.element('chat-apply').click();
  await verificationStarted;
  chat.responses.forget.push({forgotten: true});
  await chat.element('chat-reset').click();
  finishVerification({preferences: verified,
    verification: {status: 'matched', reason: 'matched', checked_requirements: 1}, generation_requests: 0});
  await adopting;
  assert.doesNotMatch(chat.element('chat-preferences').textContent, /Passed using reference data/);
  assert.equal(chat.element('chat-log').textContent, '');
  await chat.send('继续');
  assert.equal(chat.requests.filter(request => request.route === 'chat').at(-1).body.verification_ref, undefined);
});

test('an explicit clearing-only proposal can remove a legacy walking limit after confirmation', async () => {
  const chat = createChat();
  await chat.send('每段步行最多十分钟');
  chat.responses.resolve.push({draft: {...chat.draft, max_walk_minutes: 10}, preferences: emptyPreferences(), ready_to_calculate: false});
  await chat.element('chat-apply').click();
  assert.equal(chat.draft.max_walk_minutes, 10);
  const candidate = proposal({base_revision: chat.revision, slots: [], interpretation: {actions: [], max_walk_minutes: null},
    preview: {can_apply: true, adoption_blockers: [], changes: ['Remove the per-leg walking limit'], warnings: [], preferences: emptyPreferences()}});
  await chat.send('取消步行时间上限', candidate);
  assert.equal(chat.element('chat-apply').disabled, false);
  assert.equal(chat.draft.max_walk_minutes, 10);
  chat.responses.resolve.push({draft: {...chat.draft, max_walk_minutes: null}, preferences: emptyPreferences(), ready_to_calculate: false});
  await chat.element('chat-apply').click();
  assert.equal(chat.draft.max_walk_minutes, null);
  assert.equal(chat.applied.length, 2);
});

test('a verification request failure leaves a saved reference route unverified and usable for later edits', async () => {
  const chat = createChat();
  const preferences = {...emptyPreferences(), active_constraints: [{kind: 'constraint', name: 'max_walk_minutes', value: 10,
    scope: 'global', hardness: 'hard', status: 'understood', source: 'user_explicit', quote: '每段最多十分钟', turn_index: 1}]};
  chat.setPlan({verification_ref: 'proof-unavailable'});
  chat.responses.verification.push({ok: false, body: {detail: {code: 'UNAVAILABLE', message: 'Verification service unavailable'}}});
  await chat.send('每段最多十分钟');
  chat.resolve(preferences, true);
  await chat.element('chat-apply').click();
  assert.equal(chat.applied.length, 1);
  assert.equal(chat.revision, 2);
  assert.equal(chat.calculations, 1);
  assert.match(chat.element('chat-preferences').textContent, /not verified/);
  assert.match(chat.element('chat-status').textContent, /requirement verification is incomplete/);
  await chat.send('继续修改');
  const next = chat.requests.filter(request => request.route === 'chat').at(-1).body;
  assert.equal(next.verification_ref, undefined);
  assert.deepEqual(next.preferences, preferences);
});
