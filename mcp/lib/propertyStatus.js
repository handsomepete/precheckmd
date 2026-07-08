/**
 * Consolidates Home Assistant state for the brief's PROPERTY section.
 *
 * HOME-447: Weather and Garage sections each failed independently with raw
 * "Client network socket disconnected before secure TLS connection was
 * established" errors per entity. Fix: gate the whole PROPERTY section on
 * one HA health check and degrade to a single line instead of printing a
 * socket error per entity. Also fetch weather + cover entities from a
 * single /api/states call rather than one call per domain.
 */

'use strict';

const UNAVAILABLE_LINE = 'Property status unavailable (HA unreachable)';

/**
 * @param {(path: string) => Promise<any>} haGet - e.g. haGet('/api/states')
 */
async function checkHaHealth(haGet) {
  try {
    await haGet('/api/config');
    return { ok: true, error: null };
  } catch (err) {
    return { ok: false, error: err.message || String(err) };
  }
}

/**
 * @param {object[]} states - full /api/states payload
 */
function splitWeatherAndCoverEntities(states) {
  return {
    weather: (states || []).filter((s) => s.entity_id.startsWith('weather.')),
    covers: (states || []).filter((s) => s.entity_id.startsWith('cover.')),
  };
}

/**
 * Single entry point for the brief's PROPERTY section: one health check
 * gates weather + garage/cover reporting instead of each failing separately.
 *
 * @param {object} opts
 * @param {() => Promise<any>} opts.haHealthCheck - async () => { ok, error }
 * @param {() => Promise<object[]>} opts.getAllStates - async () => full /api/states array
 */
async function buildPropertyStatus({ haHealthCheck, getAllStates }) {
  const health = await haHealthCheck();
  if (!health.ok) {
    return {
      ok: false,
      line: UNAVAILABLE_LINE,
      error: health.error,
    };
  }

  try {
    const states = await getAllStates();
    const { weather, covers } = splitWeatherAndCoverEntities(states);
    return {
      ok: true,
      weather,
      covers,
    };
  } catch (err) {
    return {
      ok: false,
      line: UNAVAILABLE_LINE,
      error: err.message || String(err),
    };
  }
}

module.exports = { buildPropertyStatus, checkHaHealth, splitWeatherAndCoverEntities, UNAVAILABLE_LINE };
