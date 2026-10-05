/* Small early bootstrap: visible failures and no edits before a validated project. */
(() => {
  let readyFile = null;
  let retry = () => location.reload();
  let state = 'loading';
  let message = 'Cargando Wizard…';
  let retryVisible = false;
  const warnings = new Map();
  const warningRetries = new Map();
  function render() {
    if (!document.body) return;
    document.body.dataset.wizardState = state;
    const main = document.querySelector('main');
    if (main) main.inert = !readyFile;
    const save = document.getElementById('btnSaveGlobal');
    if (save) save.disabled = !readyFile;
    document.querySelectorAll('.step-tab').forEach(button => { button.disabled = !readyFile; });
    const banner = document.getElementById('wizardStartupBanner');
    if (banner) banner.hidden = state === 'ready' && warnings.size === 0;
    const text = document.getElementById('wizardStartupMessage');
    if (text) text.textContent = message + (warnings.size ? ' ' + [...warnings.values()].join(' ') : '');
    const button = document.getElementById('wizardStartupRetry');
    if (button) button.hidden = !retryVisible && warnings.size === 0;
    const saved = document.getElementById('saveStatusText');
    if (saved && !readyFile) saved.textContent = state === 'failed' ? 'Carga fallida' : 'Sin proyecto cargado';
  }
  const watchdog = setTimeout(() => {
    if (state === 'loading') lifecycle.fail('No se completó la carga. Reintenta; revisa la conexión local si vuelve a ocurrir.');
  }, 20000);
  const lifecycle = {
    get readyFile() { return readyFile; },
    canSave(file) { return Boolean(file && file === readyFile); },
    lock(text) { readyFile = null; state = 'loading'; message = text; retryVisible = false; render(); },
    ready(file) {
      clearTimeout(watchdog); readyFile = file; state = 'ready';
      message = file ? 'Proyecto cargado.' : 'Selecciona o crea un proyecto.';
      retryVisible = false; render();
      const saved = document.getElementById('saveStatusText');
      if (saved && file) saved.textContent = 'Guardado';
      const build = document.getElementById('buildStatusText');
      if (build) build.textContent = file ? 'Listo' : 'Seleccionar proyecto';
    },
    fail(text) { clearTimeout(watchdog); readyFile = null; state = 'failed'; message = text; retryVisible = true; render(); },
    warn(key, text, action = null) {
      warnings.set(key, text);
      warningRetries.set(key, action || (() => location.reload()));
      render();
    },
    clearWarning(key) { warnings.delete(key); warningRetries.delete(key); render(); },
    setRetry(action) { retry = action; },
    refresh: render,
  };
  window.wizardLifecycle = lifecycle;
  window.retryWizardStartup = () => readyFile && warnings.size ? [...warningRetries.values()][0]() : retry();
  window.addEventListener('error', event => {
    if (event.target?.tagName === 'SCRIPT') {
      const src = event.target.getAttribute('src') || '';
      if (src.includes('/app.js')) lifecycle.fail('No se pudo cargar la aplicación. Reintenta.');
      else lifecycle.warn('dependency:' + src, 'No se pudo cargar un recurso local.');
    }
  }, true);
  document.addEventListener('DOMContentLoaded', render);
})();
