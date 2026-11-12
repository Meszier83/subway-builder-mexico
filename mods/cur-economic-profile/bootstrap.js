/* Entry wrapper. The build embeds the profile and controller above this file. */
(function () {
  'use strict';
  const api = window.SubwayBuilderAPI;
  const slot = '__meszier83CurEconomicPilot';
  const placement = 'settings-menu';
  const uiId = 'cur-economic-pilot-switch';
  const profile = /* EMBED_CUR_PROFILE */ null;
  const factory = globalThis.createCurEconomicsController;
  delete globalThis.createCurEconomicsController;
  window[slot]?.dispose();
  if (!api || !profile) { console.error('[CUR economics] Missing API or profile'); return; }
  // Capture storage context while the mod loader's synchronous context is live.
  const storage = api.storage?.scoped?.();
  const off = [];
  let controller = null, gone = false, pendingVersion = true;
  let versionError = '', saveError = '', version = '';
  let writeTail = Promise.resolve();
  const notify = (text, type) => api.ui.showNotification(text, type || 'info', 'CUR economic pilot');
  const subscribers = new Set();
  const emit = () => { for (const fn of subscribers) fn(); };
  function persist(value) {
    // Serial writes prevent quick ON/OFF clicks saving an obsolete preference.
    writeTail = writeTail.then(() => storage.set('preference', value)).catch(error => {
      saveError = 'Could not save the pilot preference'; console.error('[CUR economics]', error); emit();
    });
  }
  function dispose() {
    if (gone) return;
    gone = true; controller?.dispose();
    off.splice(0).forEach(fn => fn?.());
    api.ui.unregisterComponent(placement, uiId);
    window.removeEventListener('beforeunload', dispose);
    window.removeEventListener('modding-api-reload-request', dispose);
    subscribers.clear();
    if (window[slot]?.dispose === dispose) delete window[slot];
  }
  window[slot] = {dispose, getState: () => ({...controller?.getState(), version, versionError, saveError})};
  // Inspected 1.7.2 reload event: cleanup supplements supported lifecycle hooks.
  // Do not replace reloadMods or touch the game's stores.
  window.addEventListener('modding-api-reload-request', dispose);
  window.addEventListener('beforeunload', dispose);
  const React = api.utils.React;
  function Switch() {
    const [, redraw] = React.useState(0);
    React.useEffect(() => { const fn = () => redraw(n => n + 1); subscribers.add(fn); return () => subscribers.delete(fn); }, []);
    const state = controller?.getState();
    const disabled = pendingVersion || !!versionError || !state;
    const reason = pendingVersion ? 'Checking game version…' : versionError || saveError || state?.reason;
    return React.createElement('div', {className: 'space-y-1'},
      React.createElement('label', {className: 'flex items-center gap-2'},
        React.createElement('input', {type: 'checkbox', checked: state?.enabled || false, disabled,
          onChange: event => controller.setEnabled(event.target.checked), 'aria-label': 'CUR economic pilot'}),
        React.createElement('span', null, 'CUR economic pilot (experimental)')),
      React.createElement('p', {className: 'text-xs text-muted-foreground'},
        reason || (state.active ? 'On · Cancún earnings proxy · your fares stay in game currency' : 'Off · original economic rules')));
  }
  api.ui.registerComponent(placement, {id: uiId, component: Switch});
  (async () => {
    try {
      if (!storage || !window.electron?.getVersion) throw new Error('Desktop version/storage API unavailable');
      version = await window.electron.getVersion();
      if (!profile.supportedGameVersions.includes(version)) throw new Error(`Pilot requires game ${profile.supportedGameVersions.join(', ')}; found ${version}`);
      if (gone) return;
      controller = factory(api, profile, {persist, notify});
      off.push(controller.subscribe(emit));
      controller.start();
      const preference = await storage.get('preference', {enabled: false, profileVersion: profile.profileVersion});
      if (gone) return;
      if (!preference || typeof preference.enabled !== 'boolean' || preference.profileVersion !== profile.profileVersion) {
        controller.setEnabled(false);
        notify('CUR profile preference was updated. The economic pilot is off.');
      } else controller.setEnabled(preference.enabled, false);
    } catch (error) {
      controller?.dispose(); controller = null;
      versionError = String(error.message || error); console.error('[CUR economics]', error);
    } finally { pendingVersion = false; if (!gone) emit(); }
  })();
})();
