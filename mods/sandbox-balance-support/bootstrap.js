/* The packager embeds controller.js above this entry point. */
(function () {
  'use strict';
  const slot = '__meszier83SandboxBalanceSupport';
  const factory = globalThis.createSandboxBalanceSupport;
  delete globalThis.createSandboxBalanceSupport;
  window[slot]?.dispose();
  const api = window.SubwayBuilderAPI;
  const off = [];
  let controller = null, disposed = false, ended = false, error = '';
  function fail(message) {
    error = message;
    console.error('[Sandbox Balance Support]', message);
    api?.ui?.showNotification?.(message, 'error', 'Sandbox Balance Support');
  }
  function dispose() {
    if (disposed) return;
    disposed = true; controller?.dispose();
    off.splice(0).forEach(fn => fn());
    window.removeEventListener('beforeunload', dispose);
    window.removeEventListener('modding-api-reload-request', dispose);
    if (window[slot]?.dispose === dispose) delete window[slot];
  }
  function start() { ended = false; controller?.start(); }
  function stop() { ended = true; controller?.stop(); }
  function isActive() {
    const url = new URL(window.location.href);
    const route = url.hash ? url.hash.slice(1).split('?')[0] : url.pathname;
    return !ended && route === '/game' && !api.tutorial?.isActive?.();
  }
  window[slot] = {dispose, getState: () => ({...controller?.getState(), error})};
  window.addEventListener('beforeunload', dispose);
  window.addEventListener('modding-api-reload-request', dispose);
  (async () => {
    try {
      if (!api || !window.electron?.getVersion || typeof factory !== 'function') {
        throw new Error('Desktop game mod API unavailable; subsidy is inactive.');
      }
      // Register synchronously while the loader's mod ownership context is set.
      for (const [name, callback] of [['onGameInit', start], ['onGameLoaded', start],
        ['onCityLoad', stop], ['onGameEnd', stop]]) {
        if (typeof api.hooks?.[name] !== 'function') throw new Error('Required lifecycle hook unavailable.');
        off.push(api.hooks[name](callback));
      }
      const version = await window.electron.getVersion();
      if (disposed) return;
      if (version !== '1.7.2') throw new Error(`Requires Subway Builder 1.7.2; found ${version}. Subsidy is inactive.`);
      // Import the ALREADY LOADED entry module by its exact script URL. No game
      // files are patched, and no second store or renderer is created.
      const script = [...document.querySelectorAll('script[type="module"][src]')]
        .find(node => new URL(node.src).pathname.endsWith('/index-CuszHwSj.js'));
      if (!script) throw new Error('Unsupported 1.7.2 game bundle; subsidy is inactive.');
      const module = await import(script.src);
      if (disposed) return;
      const store = module.aJ;
      if (!module.bR || !['trainOperational', 'trackMaintenance', 'stationMaintenance',
        'gradeCrossingMaintenance', 'other'].every(key => key in module.bR) ||
          typeof store?.getState !== 'function' || !('saveCheckPending' in store.getState())) {
        throw new Error('Unsupported 1.7.2 financial store; subsidy is inactive.');
      }
      controller = factory(store, {isActive, onError: fail});
      if (!ended) controller.start();
    } catch (cause) {
      if (!disposed) { controller?.dispose(); fail(String(cause.message || cause)); }
    }
  })();
})();
