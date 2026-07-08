'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  buildPropertyStatus,
  checkHaHealth,
  splitWeatherAndCoverEntities,
  UNAVAILABLE_LINE,
} = require('../lib/propertyStatus');

test('HA reachable: returns weather and cover entities from one states call', async () => {
  const states = [
    { entity_id: 'weather.home', state: 'sunny' },
    { entity_id: 'cover.garage_door', state: 'closed' },
    { entity_id: 'light.kitchen', state: 'on' },
  ];
  let statesCalls = 0;

  const result = await buildPropertyStatus({
    haHealthCheck: async () => ({ ok: true, error: null }),
    getAllStates: async () => { statesCalls += 1; return states; },
  });

  assert.equal(result.ok, true);
  assert.equal(statesCalls, 1);
  assert.equal(result.weather.length, 1);
  assert.equal(result.covers.length, 1);
  assert.equal(result.weather[0].entity_id, 'weather.home');
  assert.equal(result.covers[0].entity_id, 'cover.garage_door');
});

test('HA unreachable: degrades to a single line, never calls getAllStates', async () => {
  let statesCalls = 0;

  const result = await buildPropertyStatus({
    haHealthCheck: async () => ({
      ok: false,
      error: 'Client network socket disconnected before secure TLS connection was established',
    }),
    getAllStates: async () => { statesCalls += 1; return []; },
  });

  assert.equal(result.ok, false);
  assert.equal(result.line, UNAVAILABLE_LINE);
  assert.match(result.error, /TLS/);
  assert.equal(statesCalls, 0);
});

test('HA health check passes but the states call itself fails: still degrades cleanly', async () => {
  const result = await buildPropertyStatus({
    haHealthCheck: async () => ({ ok: true, error: null }),
    getAllStates: async () => { throw new Error('socket hang up'); },
  });

  assert.equal(result.ok, false);
  assert.equal(result.line, UNAVAILABLE_LINE);
  assert.match(result.error, /socket hang up/);
});

test('checkHaHealth wraps a failing haGet into { ok: false, error }', async () => {
  const result = await checkHaHealth(async () => {
    throw new Error('Client network socket disconnected before secure TLS connection was established');
  });
  assert.equal(result.ok, false);
  assert.match(result.error, /TLS/);
});

test('checkHaHealth reports ok when haGet succeeds', async () => {
  const result = await checkHaHealth(async () => ({ version: '2026.7.0' }));
  assert.equal(result.ok, true);
  assert.equal(result.error, null);
});

test('splitWeatherAndCoverEntities filters by domain prefix only', () => {
  const states = [
    { entity_id: 'weather.home' },
    { entity_id: 'weatherstation.outdoor' }, // must not match 'weather.' prefix loosely
    { entity_id: 'cover.garage_door' },
    { entity_id: 'sensor.temp' },
  ];
  const { weather, covers } = splitWeatherAndCoverEntities(states);
  assert.equal(weather.length, 1);
  assert.equal(covers.length, 1);
});
