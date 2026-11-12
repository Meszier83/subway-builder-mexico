/* Shared controller used by the packaged mod and adversarial lifecycle tests. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.createCurEconomicsController = factory;
})(typeof globalThis === 'object' ? globalThis : this, function createController(api, profile, options) {
  'use strict';
  const config = options || {};
  const copy = value => JSON.parse(JSON.stringify(value));
  const rules = copy(profile.rules);
  const keys = Object.keys(rules);
  const listeners = new Set();
  const unsubscribers = [];
  let desired = false, active = false, disposed = false, ended = false;
  let prior = null, fault = '', restoring = false;
  function validateProfile() {
    const allowed = ['INCOME_MEAN', 'INCOME_STD_DEV', 'MINIMUM_INCOME', 'MAXIMUM_INCOME',
      'HOURS_WORKED_PER_YEAR', 'DRIVING_COST_PER_KM', 'PARKING_COST'];
    if (profile.schemaVersion !== 1 || !profile.profileVersion || !Number.isFinite(profile.mxnPerGameUnit)
        || profile.mxnPerGameUnit <= 0 || keys.length !== allowed.length
        || keys.some(k => !allowed.includes(k) || !Number.isFinite(rules[k]) || rules[k] < 0)
        || rules.HOURS_WORKED_PER_YEAR <= 0 || rules.INCOME_STD_DEV <= 0
        || rules.MINIMUM_INCOME >= rules.MAXIMUM_INCOME || !profile.identity?.modId
        || profile.identity.code !== 'CUR') throw new Error('Missing or invalid CUR economic profile');
  }
  validateProfile();
  function identity() {
    const code = api.utils.getCityCode();
    if (code !== profile.identity.code) return {ok: false, reason: 'Available in the CUR map'};
    const entries = api.utils.getCities().filter(c => c.code === code);
    if (entries.length !== 1) return {ok: false, reason: 'CUR identity is ambiguous or unavailable'};
    const city = entries[0];
    if (city.modId !== profile.identity.modId || city.name !== profile.identity.name)
      return {ok: false, reason: 'This CUR map does not match the pilot'};
    return {ok: true, reason: ''};
  }
  function state() {
    let match;
    try { match = identity(); } catch (_) { match = {ok: false, reason: 'Map identity unavailable'}; }
    return {enabled: desired, active, disposed, fault, eligible: !ended && match.ok,
      reason: fault || (ended ? 'Waiting for a city' : match.reason), profileVersion: profile.profileVersion,
      mxnPerGameUnit: profile.mxnPerGameUnit};
  }
  function publish() { for (const listener of listeners) listener(state()); }
  function notify(message, type) { config.notify?.(message, type || 'info'); }
  function restore() {
    if (!prior || restoring) return;
    restoring = true;
    try {
      const current = api.utils.getPathfindingRules();
      const restoreValues = {};
      // Relinquish any key another mod changed; restore only values still ours.
      for (const key of keys) if (current[key] === rules[key]) restoreValues[key] = prior[key];
      if (Object.keys(restoreValues).length) api.modifyPathfindingRules(restoreValues);
    } finally { prior = null; active = false; restoring = false; }
  }
  function persist() {
    config.persist?.({enabled: desired, profileVersion: profile.profileVersion});
  }
  function refresh() {
    if (disposed) return;
    try {
      const match = identity();
      if (active && keys.some(key => api.utils.getPathfindingRules()[key] !== rules[key])) {
        restore(); desired = false; fault = 'Another mod changed economic settings; pilot switched off';
        persist(); notify(fault, 'warning');
      }
      if (!desired || ended || !match.ok || fault) { if (active) restore(); publish(); return; }
      if (!active) {
        const current = api.utils.getPathfindingRules();
        if (keys.some(key => !Number.isFinite(current[key]))) throw new Error('Economic rules are unavailable');
        prior = Object.fromEntries(keys.map(key => [key, current[key]]));
        try {
          api.modifyPathfindingRules(copy(rules));
          if (keys.some(key => api.utils.getPathfindingRules()[key] !== rules[key]))
            throw new Error('Game did not accept the complete profile');
          active = true;
        } catch (error) { restore(); throw error; }
      }
      publish();
    } catch (error) {
      restore(); desired = false; fault = String(error.message || error); persist(); publish(); notify(fault, 'error');
    }
  }
  function setEnabled(value, persistChoice = true) {
    if (disposed) return;
    desired = !!value; fault = '';
    if (!desired) restore();
    refresh();
    if (persistChoice) persist();
    if (desired && active) notify('CUR economic pilot on. Current fares are preserved; future commute calculations use the profile.');
  }
  function onCityLoad() { ended = false; refresh(); }
  function onGameEnd() { ended = true; restore(); publish(); }
  function start() {
    if (disposed || unsubscribers.length) return;
    for (const hook of ['onCityLoad', 'onGameInit', 'onGameLoaded', 'onDemandChange'])
      unsubscribers.push(api.hooks[hook](onCityLoad));
    unsubscribers.push(api.hooks.onGameEnd(onGameEnd));
    for (const hook of ['onHourChange', 'onPauseChanged'])
      if (api.hooks[hook]) unsubscribers.push(api.hooks[hook](refresh));
    refresh();
  }
  function dispose() {
    if (disposed) return;
    restore(); disposed = true;
    for (const unsubscribe of unsubscribers.splice(0)) unsubscribe?.();
    publish(); listeners.clear();
  }
  return {start, setEnabled, refresh, dispose, getState: state,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); }};
});
