const assert = require('node:assert/strict');
const test = require('node:test');
const {diagnostic} = require('../frontend/stage2b/state.js');

function plan(hoursChecked) {
  return {
    revision: 7, mode: 'walk', finish_policy: 'last_stop',
    time: {mode: 'budget', departure_at: '2026-10-28T09:00:00+08:00'},
    opening_hours_checked: hoursChecked,
    visits: [{point: {label: 'Private stop label', latitude: 1.3123456, longitude: 103.8765432},
      opening_hours_display: 'Private raw schedule', opening_status: 'open_at_arrival'}],
    totals: {distance_m: 500, travel_s: 300, stay_s: 1800, buffer_s: 0,
      reference_duration_s: 2100, duration_range_s: [1800, 2400]},
    budget_check: {status: 'all_estimates_fit'}, route_calls: {provider_calls: 1},
    legs: [{index: 0, kind: 'provider_route', mode: 'walk', distance_m: 500,
      duration_s: 300, endpoint_offsets_m: {origin: 1, destination: 2},
      diagnostics: {large_detour_flag: false}, reused_in_memory: false,
      geometry: {type: 'LineString', coordinates: [[103.8765432, 1.3123456]]}}]
  };
}

test('diagnostic records a completed catalogue-hours check from the actual plan', () => {
  const result = diagnostic(plan(true), 7);
  assert.equal(result.opening_hours_checked, true);
  assert.equal(result.current_plan, true);
  assert.equal(result.entrances_verified, false);
});

test('unknown, absent, and non-boolean hours flags do not imply a completed check', () => {
  for (const value of [false, null, undefined, 'true', 1]) {
    assert.equal(diagnostic(plan(value), 7).opening_hours_checked, false);
  }
});

test('a missing plan has no hours check and is not current', () => {
  const result = diagnostic(null, 7);
  assert.equal(result.opening_hours_checked, false);
  assert.equal(result.current_plan, false);
  assert.equal(result.plan_revision, null);
  assert.equal(result.visit_count, 0);
});

test('historical checked plan preserves its fact while the revision marks it stale', () => {
  const result = diagnostic(plan(true), 8);
  assert.equal(result.opening_hours_checked, true);
  assert.equal(result.current_plan, false);
  assert.equal(result.plan_revision, 7);
  assert.equal(result.current_revision, 8);
});

test('hours flag adds no place, schedule, coordinate, clock, or live-access data', () => {
  const original = plan(true);
  const before = JSON.stringify(original);
  const result = diagnostic(original, 7);
  const encoded = JSON.stringify(result);
  for (const privateValue of ['Private stop label', 'Private raw schedule', 'open_at_arrival',
    '1.3123456', '103.8765432', '2026-10-28T09:00:00+08:00']) {
    assert.equal(encoded.includes(privateValue), false, privateValue);
  }
  assert.equal(result.exact_coordinates_included, false);
  assert.equal(result.labels_included, false);
  assert.equal(result.clock_times_included, false);
  assert.equal(result.entrances_verified, false);
  assert.equal(Object.hasOwn(result, 'live_availability'), false);
  assert.equal(JSON.stringify(original), before);
});
