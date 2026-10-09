
    // Configuración de Mapas (Esri Dark Canvas como basemap oscuro por defecto)
    const CARTO_API_KEY = "";

    // Estado de la aplicación (Sin proyecto por defecto al inicio a menos que venga en la URL)
    let currentCityFile = null;
    let cityData = { city: {}, macroeconomics: {}, pois: [], places: [] };
    let autoSaveTimer = null;
    let currentStep = 1;
    let mapBbox = null, mapPoi = null, mapDemand = null;
    let bboxRectangle = null;
    let bboxPoiGroup = null;
    let isBboxLocked = false;
    let zoomPreviewRectangle = null;
    let zoomPreviewGroup = null;
    let poiMarkersGroup = null;
    let placesMarkersGroup = null;
    let nativeOsmMarkersGroup = null;
    let isNativeOsmPreviewOn = false;
    let selectedPlaceIndices = new Set();
    let placeFilterText = "";
    let placeFilterType = "all";
    let placeFilterCategory = "all";
    let zoneFilterMode = "high_conflict";
    let scannedPlacesCatalog = null;
    let pendingHomogenizeResult = null;
    let demandLayers = { residents: null, jobs: null, pois: null };
    let editingPoiIndex = -1;
    let sseSource = null;

    let bboxHandlesGroup = null;
    let bboxCornerMarkers = { NW: null, NE: null, SE: null, SW: null };
    let bboxMainGroup = null;
    let isolatedZonesGroup = null;
    let isolatedHandlesGroup = null;
    let isolatedZonesDemandGroup = null;
    let selectedIsolatedZoneIndex = -1;
    let isDrawingIsolated = false;
    let drawStartLatLng = null;
    let isolatedDrawPhase = 0; // 0: inactivo, 1: esquina 1 fijada esperando esquina 2
    let isolatedIsDragging = false;
    let isolatedCornerMarkers = { NW: null, NE: null, SE: null, SW: null };

    // Núcleo Urbano (Urban Core AOI LOD)
    let urbanCoreGroup = null;
    let urbanCoreHandlesGroup = null;
    let urbanCoreLayer = null;
    let isDrawingUrbanCore = false;
    let isEditingUrbanCore = false;
    let urbanCoreDrawPoints = [];
    let urbanCoreDrawMarkers = [];
    let urbanCoreTempShape = null;
    let urbanCoreVertexMarkers = [];
    let urbanCoreMidpointMarkers = [];
    let urbanCoreReachPreviewCircle = null;
    let urbanCoreReachTimer = null;

    // Zonas de Exclusión y Borrador Local
    let exclusionZonesGroup = null;
    let exclusionZonesBboxGroup = null;
    let exclusionZonesDemandGroup = null;
    let exclusionDrawStep = 4;
    let selectedExclusionIndex = -1;
    let isDrawingExclusion = false;
    let exclusionDrawMode = 'polygon';
    let exclusionDrawPoints = [];
    let exclusionTempShape = null;
    let exclusionDrawMarkers = [];
    let exclusionBoxStart = null;
    let currentExclusionDraft = null;
    window._pendingDraftData = null;
    window._pendingDraftPath = null;

    async function flushPendingAutoSave() {
      if (conapoCommitPromise) await conapoCommitPromise;
      await citySaveChain;
      if (autoSaveTimer) {
        clearTimeout(autoSaveTimer);
        autoSaveTimer = null;
        if (currentCityFile) {
          await saveCurrentCity(true);
        }
      }
    }

    // Guardado de emergencia al cerrar la pestaña o recargar (F5)
    window.addEventListener('beforeunload', () => {
      // Release the long-lived HTTP/1.1 slot before a reload starts new requests.
      if (sseSource) { sseSource.close(); sseSource = null; }
      if (autoSaveTimer) {
        clearTimeout(autoSaveTimer);
        autoSaveTimer = null;
        if (currentCityFile) {
          syncStateFromInputs();
          try {
            const payload = JSON.stringify({ file: currentCityFile, ...cityData });
            if (navigator.sendBeacon) {
              navigator.sendBeacon('/api/city/save', new Blob([payload], { type: 'application/json' }));
            } else {
              fetch('/api/city/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: payload,
                keepalive: true
              });
            }
          } catch (err) {}
        }
      }
    });

    window.addEventListener('pagehide', () => {
      if (sseSource) { sseSource.close(); sseSource = null; }
    });
    window.addEventListener('pageshow', event => {
      if (event.persisted && !sseSource) initSSE();
    });

    let startupPromise = null;
    let mapsInitialized = false;
    let sidebarInitialized = false;
    let cityLoadVersion = 0;
    let cityLoadController = null;

    async function fetchStartupJson(url, label, timeout = 10000, signal = null) {
      const deadline = AbortSignal.timeout(timeout);
      try {
        const response = await fetch(url, { signal: signal ? AbortSignal.any([signal, deadline]) : deadline });
        if (!response.ok) throw new Error(`${label}: HTTP ${response.status}`);
        const result = await response.json();
        if (result.error || result.status === 'error') throw new Error(result.error || result.message || label);
        return result;
      } catch (error) {
        if (deadline.aborted) throw new Error(`${label}: tiempo agotado. Reintenta.`);
        throw error;
      }
    }

    async function initializeWizard() {
      if (startupPromise) return startupPromise;
      startupPromise = (async () => {
        wizardLifecycle.lock('Cargando proyectos…');
        wizardLifecycle.setRetry(initializeWizard);
        try {
          if (typeof lucide === 'undefined') {
            window.lucide = { createIcons() {} };
            wizardLifecycle.warn('icons', 'Iconos no disponibles. Recarga para reintentar.');
          }
          lucide.createIcons();
          if (!mapsInitialized) {
            try { initMaps(); mapsInitialized = true; }
            catch (error) {
              wizardLifecycle.warn('maps', 'Mapas no disponibles. La configuración sigue accesible; recarga para reintentar.');
              // A partially initialized map must never be initialized twice.
              mapsInitialized = Boolean(mapBbox);
              console.warn('Inicialización de mapas:', error);
            }
          }
          if (!sidebarInitialized) { initSidebarResizer(); sidebarInitialized = true; }
          initSSE();
          checkSystemHealth();
          await fetchCitiesList();
          const cityParam = currentCityFile || new URLSearchParams(window.location.search).get('city');
          if (cityParam) {
            currentCityFile = cityParam;
            await loadCityData(cityParam);
          } else {
            await openProjectsModal();
            wizardLifecycle.ready(null);
          }
        } catch (error) {
          wizardLifecycle.fail(`No se pudo iniciar el Wizard: ${error.message}`);
          console.error('Inicio del Wizard:', error);
        }
      })();
      try { await startupPromise; }
      finally { startupPromise = null; }
    }

    window.addEventListener('DOMContentLoaded', initializeWizard);

    // -------------------------------------------------------------------------
    // GESTIÓN DE PROYECTOS (CREAR, ABRIR, ELIMINAR)
    // -------------------------------------------------------------------------
    async function openProjectsModal() {
      const modal = document.getElementById('modalProjects');
      if (!modal) return;
      modal.classList.remove('hidden');
      const closeBtn = document.getElementById('btnCloseModalProjects');
      if (closeBtn) {
        closeBtn.style.display = currentCityFile ? 'block' : 'none';
      }
      await renderProjectsList();
      lucide.createIcons();
    }

    function closeProjectsModal() {
      if (!currentCityFile) {
        showToast("Por favor selecciona o crea un proyecto para continuar", "warning");
        return;
      }
      const modal = document.getElementById('modalProjects');
      if (modal) modal.classList.add('hidden');
    }

    function openNewProjectModal() {
      openProjectsModal();
      const input = document.getElementById('newProj_name');
      if (input) {
        input.focus();
        input.scrollIntoView({ behavior: 'smooth' });
      }
    }

    async function renderProjectsList() {
      const cont = document.getElementById('projectsListContainer');
      if (!cont) return;
      cont.innerHTML = '<div class="text-center py-6 text-stone-500 font-mono text-xs">Cargando proyectos...</div>';

      try {
        const json = await fetchStartupJson('/api/projects', 'Proyectos');
        const cities = json.cities || [];

        if (cities.length === 0) {
          cont.innerHTML = `
            <div class="text-center py-8 bg-stone-50 border border-stone-200 rounded-xl text-stone-500 text-xs">
              <i data-lucide="folder-x" class="w-8 h-8 text-stone-400 mx-auto mb-2"></i>
              <span>No hay proyectos creados aún. ¡Crea el primero usando el formulario superior!</span>
            </div>
          `;
          lucide.createIcons();
          return;
        }

        cont.innerHTML = cities.map(c => {
          const isCurrent = c.path === currentCityFile;
          const poiCount = c.poi_count || 0;
          const bboxStr = c.bbox ? `[${c.bbox.map(n => typeof n === 'number' ? n.toFixed(2) : n).join(', ')}]` : 'Sin BBOX';
          const dataDirStr = c.data_dir || `data/${c.code ? c.code.toLowerCase() : ''}`;

          return `
            <div class="p-3.5 rounded-xl border transition flex flex-col sm:flex-row sm:items-center justify-between gap-3 ${
              isCurrent
                ? 'bg-amber-50/80 border-amber-400 shadow-xs'
                : 'bg-white hover:bg-stone-50 border-stone-200 shadow-xs'
            }">
              <div class="overflow-hidden space-y-1">
                <div class="flex items-center space-x-2">
                  <span class="font-bold text-stone-900 text-sm truncate">${c.name}</span>
                  <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-stone-100 text-stone-800 border border-stone-300 uppercase">${c.code}</span>
                  ${isCurrent ? '<span class="text-[10px] bg-amber-200 text-amber-900 px-2 py-0.5 rounded font-bold">Activo</span>' : ''}
                </div>
                <div class="flex flex-wrap items-center gap-x-3 text-[11px] text-stone-600 font-mono">
                  <span>📍 POIs: <strong>${poiCount}</strong></span>
                  <span>🗺️ BBOX: ${bboxStr}</span>
                  <span>📂 Carpeta: <code class="text-stone-800 font-bold bg-stone-100 px-1 rounded">${dataDirStr}</code></span>
                </div>
              </div>
              <div class="flex items-center space-x-2 shrink-0">
                <button onclick="selectAndLoadProject('${c.path}')" class="px-3 py-1.5 rounded-lg text-xs font-bold transition flex items-center space-x-1.5 ${
                  isCurrent
                    ? 'bg-amber-200 text-amber-900 cursor-default'
                    : 'bg-metro-orange hover:bg-orange-600 text-white shadow'
                }">
                  <i data-lucide="check" class="w-3.5 h-3.5"></i>
                  <span>${isCurrent ? 'Abierto' : 'Abrir'}</span>
                </button>
                <button onclick="confirmDeleteProject('${c.path}', '${c.name}')" class="p-1.5 rounded-lg text-rose-600 hover:text-rose-800 hover:bg-rose-50 transition border border-transparent hover:border-rose-200" title="Eliminar proyecto">
                  <i data-lucide="trash-2" class="w-4 h-4"></i>
                </button>
              </div>
            </div>
          `;
        }).join('');
        lucide.createIcons();
      } catch (e) {
        cont.innerHTML = `<div class="p-4 text-rose-600 text-xs">Error al cargar proyectos: ${e.message}</div>`;
        throw e;
      }
    }

    async function selectAndLoadProject(filePath) {
      await flushPendingAutoSave();
      currentCityFile = filePath;
      window.history.replaceState({}, '', `?city=${encodeURIComponent(filePath)}`);
      await fetchCitiesList();
      await loadCityData(filePath);
      const modal = document.getElementById('modalProjects');
      if (modal) modal.classList.add('hidden');
    }

    async function submitCreateNewProject() {
      const name = document.getElementById('newProj_name').value.trim();
      const code = document.getElementById('newProj_code').value.trim().toUpperCase();

      if (!name || !code) {
        showToast("Nombre y código son obligatorios", "error");
        return;
      }

      try {
        const res = await fetch('/api/project/new', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, code, creator: 'Creador' })
        });
        const json = await res.json();
        if (json.status === 'ok') {
          showToast(`Proyecto '${name}' creado exitosamente`, "success");
          document.getElementById('newProj_name').value = '';
          document.getElementById('newProj_code').value = '';
          await selectAndLoadProject(json.file || `cities/${json.filename}`);
        } else {
          showToast(json.error || "No se pudo crear el proyecto", "error");
        }
      } catch (e) {
        showToast(e.message, "error");
      }
    }

    async function confirmDeleteProject(filePath, cityName) {
      if (!confirm(`¿Eliminar el proyecto '${cityName}'?`)) return;

      try {
        const res = await fetch('/api/project/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file: filePath, delete_data_folder: false })
        });
        const json = await res.json();
        if (json.status === 'ok') {
          showToast(`Proyecto '${cityName}' eliminado`, "info");
          if (currentCityFile === filePath) {
            currentCityFile = null;
            window.history.replaceState({}, '', window.location.pathname);
          }
          await fetchCitiesList();
          await renderProjectsList();
        } else {
          showToast(json.error || "Error al eliminar proyecto", "error");
        }
      } catch (e) {
        showToast(e.message, "error");
      }
    }

    // -------------------------------------------------------------------------
    // GESTIÓN DE CARPETA DE DATOS DEL PROYECTO
    // -------------------------------------------------------------------------
    function promptChangeDataDir() {
      const currentDir = (cityData && cityData.data_dir) ? cityData.data_dir : (document.getElementById('activeDataFolderBadge').innerText || 'data/');
      const modal = document.getElementById('modalChangeDataDir');
      const input = document.getElementById('inputCustomDataDir');
      if (input) input.value = currentDir;
      if (modal) modal.classList.remove('hidden');
    }

    function closeChangeDataDirModal() {
      const modal = document.getElementById('modalChangeDataDir');
      if (modal) modal.classList.add('hidden');
    }

    async function submitChangeDataDir() {
      const input = document.getElementById('inputCustomDataDir');
      if (!input) return;
      const newDir = input.value.trim();
      if (!newDir) {
        showToast("Debes ingresar una ruta válida", "error");
        return;
      }

      try {
        const res = await fetch('/api/data/set-directory', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file: currentCityFile, data_dir: newDir })
        });
        const json = await res.json();
        if (json.status === 'ok') {
          cityData.data_dir = newDir;
          closeChangeDataDirModal();
          showToast(`Carpeta asignada: ${newDir}`, "success");
          await refreshDataStatus(true);
        } else {
          showToast(json.error || "Error al cambiar carpeta", "error");
        }
      } catch (e) {
        showToast(e.message, "error");
      }
    }

    async function openFileLocation(targetPath) {
      if (!targetPath) return;
      try {
        const res = await fetch('/api/data/open-location', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: targetPath })
        });
        const json = await res.json();
        if (json.status === 'ok') {
          showToast(`Abriendo ubicación: ${targetPath}`, "info");
        } else {
          showToast(json.error || "No se pudo abrir la ubicación", "error");
        }
      } catch (e) {
        showToast(e.message, "error");
      }
    }

    async function openActiveDataFolder() {
      const badge = document.getElementById('activeDataFolderBadge');
      const folderPath = badge ? badge.innerText.trim() : 'data/';
      await openFileLocation(folderPath);
    }

    async function unlinkDataFile(filename) {
      try {
        const res = await fetch('/api/data/unlink', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file: currentCityFile, filename })
        });
        const json = await res.json();
        if (json.status === 'ok') {
          await refreshDataStatus(false);
        } else {
          showToast(json.error || "No se pudo quitar el archivo", "error");
        }
      } catch (e) {
        showToast(e.message, "error");
      }
    }

    async function relinkDataFile(filename) {
      try {
        const res = await fetch('/api/data/relink', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file: currentCityFile, filename })
        });
        const json = await res.json();
        if (json.status === 'ok') {
          showToast(`Archivo '${filename}' restaurado exitosamente`, "success");
          await refreshDataStatus(false);
        } else {
          showToast(json.error || "No se pudo restaurar el archivo", "error");
        }
      } catch (e) {
        showToast(e.message, "error");
      }
    }

    // -------------------------------------------------------------------------
    // COLAPSO / EXPANSIÓN DE BARRAS LATERALES
    // -------------------------------------------------------------------------
    function toggleSidebar(step) {
      const sidebar = document.getElementById(`sidebar-${step}`);
      const btnExpand = document.getElementById(`btnExpand-${step}`);
      if (!sidebar) return;

      const isCollapsed = sidebar.classList.contains('hidden');
      if (isCollapsed) {
        sidebar.classList.remove('hidden');
        if (btnExpand) btnExpand.classList.add('hidden');
      } else {
        sidebar.classList.add('hidden');
        if (btnExpand) btnExpand.classList.remove('hidden');
      }

      // Reajustar tamaño de mapas
      setTimeout(() => {
        if (step === 1 && mapBbox) mapBbox.invalidateSize();
        if (step === 4 && mapPoi) mapPoi.invalidateSize();
        if (step === 6 && mapDemand) mapDemand.invalidateSize();
      }, 150);
      lucide.createIcons();
    }

    let isSidebarWide4 = false;
    let isPlacesCompactView = false;

    function togglePlacesCompactView() {
      const secControls = document.getElementById('placesSecondaryControls');
      const btn = document.getElementById('btnTogglePlacesCompact');
      if (!secControls) return;

      isPlacesCompactView = !isPlacesCompactView;
      if (isPlacesCompactView) {
        secControls.classList.add('hidden');
        if (btn) btn.classList.add('bg-stone-800', 'text-white', 'border-stone-800');
        showToast("Modo Compacto: Controles secundarios ocultos para maximizar la lista", "info", 1500);
      } else {
        secControls.classList.remove('hidden');
        if (btn) btn.classList.remove('bg-stone-800', 'text-white', 'border-stone-800');
      }
      localStorage.setItem('sb_places_compact_view', isPlacesCompactView ? '1' : '0');
    }

    function initSidebarResizer() {
      const resizer = document.getElementById('sidebarResizer-4');
      const sidebar = document.getElementById('sidebar-4');
      const icon = document.getElementById('iconExpandSidebar4');
      if (!resizer || !sidebar) return;

      // Restaurar ancho guardado
      const savedWidth = localStorage.getItem('sb_poi_sidebar_width');
      if (savedWidth) {
        const w = parseInt(savedWidth, 10);
        if (!isNaN(w) && w >= 360 && w <= window.innerWidth * 0.82) {
          sidebar.style.width = `${w}px`;
          sidebar.classList.remove('w-[430px]', 'w-[660px]', '2xl:w-[740px]');
          if (icon) icon.setAttribute('data-lucide', w > 520 ? 'chevrons-left' : 'chevrons-right');
        }
      }

      // Restaurar estado de vista compacta
      if (localStorage.getItem('sb_places_compact_view') === '1') {
        const secControls = document.getElementById('placesSecondaryControls');
        const btn = document.getElementById('btnTogglePlacesCompact');
        if (secControls) secControls.classList.add('hidden');
        if (btn) btn.classList.add('bg-stone-800', 'text-white', 'border-stone-800');
        isPlacesCompactView = true;
      }

      let isDragging = false;
      let startX = 0;
      let startWidth = 0;

      resizer.addEventListener('mousedown', (e) => {
        isDragging = true;
        startX = e.clientX;
        startWidth = sidebar.getBoundingClientRect().width;
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';

        const onMouseMove = (ev) => {
          if (!isDragging) return;
          const deltaX = ev.clientX - startX;
          let newWidth = Math.round(startWidth + deltaX);
          const minW = 360;
          const maxW = Math.round(window.innerWidth * 0.82);
          newWidth = Math.max(minW, Math.min(maxW, newWidth));

          sidebar.style.width = `${newWidth}px`;
          sidebar.classList.remove('w-[430px]', 'w-[660px]', '2xl:w-[740px]');
          if (mapPoi) mapPoi.invalidateSize();
          if (icon) icon.setAttribute('data-lucide', newWidth > 520 ? 'chevrons-left' : 'chevrons-right');
        };

        const onMouseUp = () => {
          if (isDragging) {
            isDragging = false;
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            window.removeEventListener('mousemove', onMouseMove);
            window.removeEventListener('mouseup', onMouseUp);

            const finalW = Math.round(sidebar.getBoundingClientRect().width);
            localStorage.setItem('sb_poi_sidebar_width', finalW);
            if (mapPoi) mapPoi.invalidateSize();
            if (window.lucide) lucide.createIcons();
          }
        };

        window.addEventListener('mousemove', onMouseMove);
        window.addEventListener('mouseup', onMouseUp);
      });

      // Doble clic sobre la barra para alternar rápidamente entre ancho estándar y amplio
      resizer.addEventListener('dblclick', () => {
        toggleSidebarWidth(4);
      });
    }

    function toggleSidebarWidth(step = 4) {
      const sidebar = document.getElementById(`sidebar-${step}`);
      const icon = document.getElementById(`iconExpandSidebar${step}`);
      if (!sidebar) return;

      const currentW = sidebar.getBoundingClientRect().width;
      let targetW;
      if (currentW > 520) {
        targetW = 430;
        if (icon) icon.setAttribute('data-lucide', 'chevrons-right');
        showToast("↔️ Panel lateral en ancho estándar (430px)", "info", 1500);
      } else {
        targetW = Math.min(720, Math.round(window.innerWidth * 0.65));
        if (icon) icon.setAttribute('data-lucide', 'chevrons-left');
        showToast(`↔️ Panel lateral en modo amplio (${targetW}px)`, "info", 1500);
      }

      sidebar.style.width = `${targetW}px`;
      sidebar.classList.remove('w-[430px]', 'w-[660px]', '2xl:w-[740px]');
      localStorage.setItem('sb_poi_sidebar_width', targetW);

      setTimeout(() => {
        if (step === 4 && mapPoi) mapPoi.invalidateSize();
        if (window.lucide) lucide.createIcons();
      }, 250);
    }

    // -------------------------------------------------------------------------
    // NAVEGACIÓN ENTRE PASOS
    // -------------------------------------------------------------------------
    function goToStep(step) {
      syncStateFromInputs();
      currentStep = step;
      document.querySelectorAll('.step-view').forEach((el, idx) => {
        el.classList.toggle('hidden', idx + 1 !== step);
      });
      document.querySelectorAll('.step-tab').forEach((el, idx) => {
        const isActive = idx + 1 === step;
        el.className = `step-tab flex items-center space-x-2 px-3.5 py-1.5 rounded-lg text-xs font-bold transition ${
          isActive
            ? 'text-white bg-metro-orange shadow-sm border border-transparent'
            : 'text-stone-700 hover:text-stone-950 bg-stone-100 hover:bg-stone-200 border border-stone-300/80'
        }`;
        const badge = el.querySelector('span:first-child');
        if (badge) {
          badge.className = `w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-mono font-bold ${
            isActive ? 'bg-white text-metro-orange' : 'bg-stone-300 text-stone-700'
          }`;
        }
      });

      if (step !== 1) {
        if (isEditingUrbanCore) stopEditingUrbanCore(false);
        if (isDrawingUrbanCore) cancelDrawingUrbanCore();
        if (urbanCoreReachPreviewCircle && mapBbox) {
          mapBbox.removeLayer(urbanCoreReachPreviewCircle);
          urbanCoreReachPreviewCircle = null;
        }
      }

      // Recalcular tamaño de mapas al cambiar de pestaña
      setTimeout(() => {
        if (step === 1 && mapBbox) mapBbox.invalidateSize();
        if (step === 4 && mapPoi) {
          mapPoi.invalidateSize();
          renderPoiList();
          renderPoiMarkersOnMap();
          renderBboxOnPoiMap();
          loadDemandDensityForPoi();
          if (cityData && cityData.city && cityData.city.bbox && Array.isArray(cityData.city.bbox) && cityData.city.bbox.length === 4) {
            const b = cityData.city.bbox;
            const minLat = Math.min(parseFloat(b[1]), parseFloat(b[3]));
            const minLon = Math.min(parseFloat(b[0]), parseFloat(b[2]));
            const maxLat = Math.max(parseFloat(b[1]), parseFloat(b[3]));
            const maxLon = Math.max(parseFloat(b[0]), parseFloat(b[2]));
            if (!isNaN(minLat) && !isNaN(minLon) && !isNaN(maxLat) && !isNaN(maxLon)) {
              mapPoi.fitBounds([[minLat, minLon], [maxLat, maxLon]], { padding: [30, 30] });
            }
          }
        }
        if (step === 6 && mapDemand) {
          mapDemand.invalidateSize();
          loadDemandDataPreview();
        }
      }, 100);

      if (step === 2) refreshDataStatus();
      if (step === 3 && !window.conapoAvailableYears) loadConapoYears();
    }

    // -------------------------------------------------------------------------
    // MAPAS LEAFLET Y CAPAS
    // -------------------------------------------------------------------------
    function createBaseLayers() {
      const lightEsri = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
        attribution: '&copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors',
        maxNativeZoom: 16,
        maxZoom: 20
      });
      const darkEsri = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
        attribution: '&copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors',
        maxNativeZoom: 16,
        maxZoom: 20
      });
      const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors',
        maxNativeZoom: 19,
        maxZoom: 20
      });
      const satEsri = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
        attribution: '&copy; Esri World Imagery',
        maxNativeZoom: 19,
        maxZoom: 20
      });

      return {
        baseMapDefault: lightEsri,
        layerControl: {
          "Esri Light Gray": lightEsri,
          "Esri Dark Canvas": darkEsri,
          "OpenStreetMap": osm,
          "Satélite HD": satEsri
        }
      };
    }

    function initMaps() {
      // Configuración de zoom ágil, rápido y reactivo
      const mapOptions = {
        maxZoom: 20,
        zoomSnap: 0.5,             // Pasos limpios y precisos
        zoomDelta: 1,              // Avance rápido con botones
        wheelPxPerZoomLevel: 50,   // Rápido y directo al girar la rueda del ratón
        wheelDebounceTime: 10,     // Respuesta instantánea sin retrasos
        preferCanvas: true         // Motor HTML5 2D Canvas de alto rendimiento
      };

      // 1. Mapa BBOX (Renderizado SVG nativo para permitir puntero transparente durante trazados)
      const b1 = createBaseLayers();
      mapBbox = L.map('mapBbox', { ...mapOptions, preferCanvas: false, layers: [b1.baseMapDefault] }).setView([21.16, -86.85], 11);
      bboxMainGroup = L.featureGroup().addTo(mapBbox);
      bboxHandlesGroup = L.layerGroup().addTo(mapBbox);
      urbanCoreGroup = L.featureGroup().addTo(mapBbox);
      urbanCoreHandlesGroup = L.featureGroup().addTo(mapBbox);
      isolatedZonesGroup = L.featureGroup().addTo(mapBbox);
      isolatedHandlesGroup = L.layerGroup().addTo(mapBbox);
      exclusionZonesBboxGroup = L.featureGroup().addTo(mapBbox);
      zoomPreviewGroup = L.layerGroup().addTo(mapBbox);

      const bboxOverlays = {
        "📦 BBOX Metropolitano": bboxMainGroup,
        "🏛️ Núcleo Urbano (LOD)": urbanCoreGroup,
        "🏝️ Zonas Aisladas (Islas)": isolatedZonesGroup,
        "🚫 Zonas de Exclusión": exclusionZonesBboxGroup,
        "🖥️ Encuadre Inicial (16:9)": zoomPreviewGroup
      };
      L.control.layers(b1.layerControl, bboxOverlays, { position: 'topright' }).addTo(mapBbox);
      setupIsolatedDrawingEvents();
      setupUrbanCoreDrawingEvents();
      setupExclusionDrawingEvents();

      // 2. Mapa POI Studio
      const b2 = createBaseLayers();
      mapPoi = L.map('mapPoi', { ...mapOptions, layers: [b2.baseMapDefault] }).setView([21.16, -86.85], 11);
      poiMarkersGroup = L.layerGroup().addTo(mapPoi);
      placesMarkersGroup = L.layerGroup().addTo(mapPoi);
      nativeOsmMarkersGroup = L.layerGroup().addTo(mapPoi);
      bboxPoiGroup = L.layerGroup().addTo(mapPoi);
      affluenceZonesGroup = L.featureGroup().addTo(mapPoi);
      exclusionZonesGroup = L.featureGroup().addTo(mapPoi);
      const poiOverlays = {
        "📍 POIs y Marcadores": poiMarkersGroup,
        "🏷️ Toponimia y Colonias": placesMarkersGroup,
        "🗺️ Vista Previa OSM Nativo": nativeOsmMarkersGroup,
        "📐 Delimitación BBOX": bboxPoiGroup,
        "✨ Zonas de Afluencia": affluenceZonesGroup,
        "🚫 Zonas de Exclusión": exclusionZonesGroup
      };
      L.control.layers(b2.layerControl, poiOverlays, { position: 'topright' }).addTo(mapPoi);
      mapPoi.on('mousemove', (e) => { lastMapMouseLatLng = e.latlng; });
      mapPoi.on('mouseout', () => { lastMapMouseLatLng = null; });
      mapPoi.on('zoomend', () => {
        if (cityData && cityData.places && cityData.places.length > 0) {
          renderPlacesMarkersOnMap();
        }
      });
      let mapPoiPlacesMoveTimer = null;
      mapPoi.on('moveend', () => {
        if (cityData && cityData.places && cityData.places.length > 80) {
          clearTimeout(mapPoiPlacesMoveTimer);
          mapPoiPlacesMoveTimer = setTimeout(() => {
            renderPlacesMarkersOnMap();
          }, 100);
        }
      });

      setupBoxSelectionForPoi();
      setupAffluenceDrawingEvents();
      setupExclusionDrawingEvents();

      // 3. Mapa Demand Viewer
      const b3 = createBaseLayers();
      mapDemand = L.map('mapDemand', { ...mapOptions, layers: [b3.baseMapDefault] }).setView([21.16, -86.85], 11);
      demandLayers.residents = L.layerGroup().addTo(mapDemand);
      demandLayers.jobs = L.layerGroup().addTo(mapDemand);
      demandLayers.pois = L.layerGroup().addTo(mapDemand);
      isolatedZonesDemandGroup = L.layerGroup().addTo(mapDemand);
      affluenceZonesDemandGroup = L.layerGroup().addTo(mapDemand);
      exclusionZonesDemandGroup = L.layerGroup().addTo(mapDemand);

      const demandOverlays = {
        "👥 Población (Azul)": demandLayers.residents,
        "💼 Empleo (Rojo)": demandLayers.jobs,
        "⭐ POIs Especiales (Naranja)": demandLayers.pois,
        "🏝️ Zonas Aisladas (Cian)": isolatedZonesDemandGroup,
        "✨ Zonas de Afluencia (Ámbar)": affluenceZonesDemandGroup,
        "🚫 Zonas de Exclusión (Rojo)": exclusionZonesDemandGroup
      };
      L.control.layers(b3.layerControl, demandOverlays, { position: 'topright' }).addTo(mapDemand);
    }

    function createCornerIcon(key) {
      return L.divIcon({
        className: 'bbox-corner-handle',
        html: `
          <div style="
            display: flex;
            align-items: center;
            justify-content: center;
            width: 22px;
            height: 22px;
            background: #E8E1D3;
            border: 2.5px solid #D95F18;
            border-radius: 50%;
            box-shadow: 0 0 10px rgba(217,95,24,0.7);
            color: #231F1C;
            font-size: 9px;
            font-weight: 800;
            cursor: grab;
          ">
            <div style="width: 6px; height: 6px; background: #FFFFFF; border-radius: 50%;"></div>
          </div>
        `,
        iconSize: [22, 22],
        iconAnchor: [11, 11]
      });
    }

    function updateBboxLayer(bbox, fit = true) {
      if (!mapBbox || !bbox || bbox.length !== 4) return;
      if (bboxRectangle) {
        if (bboxMainGroup) bboxMainGroup.removeLayer(bboxRectangle);
        else mapBbox.removeLayer(bboxRectangle);
      }
      if (bboxHandlesGroup) bboxHandlesGroup.clearLayers();

      let minLon = Math.min(parseFloat(bbox[0]), parseFloat(bbox[2]));
      let minLat = Math.min(parseFloat(bbox[1]), parseFloat(bbox[3]));
      let maxLon = Math.max(parseFloat(bbox[0]), parseFloat(bbox[2]));
      let maxLat = Math.max(parseFloat(bbox[1]), parseFloat(bbox[3]));
      const MIN_SPAN = 0.01;
      if (maxLon - minLon < MIN_SPAN) maxLon = minLon + MIN_SPAN;
      if (maxLat - minLat < MIN_SPAN) maxLat = minLat + MIN_SPAN;
      const bounds = [[minLat, minLon], [maxLat, maxLon]];

      bboxRectangle = L.rectangle(bounds, {
        color: '#F37021',
        weight: 2.5,
        fillColor: '#F37021',
        fillOpacity: 0.16,
        dashArray: '6, 6',
        className: 'draggable-bbox-rect'
      }).addTo(bboxMainGroup || mapBbox);

      if (isBboxLocked) {
        bboxRectangle.bindTooltip("<strong>Área de Delimitación BBOX (Bloqueada 🔒)</strong><br><span class='text-[11px]'>Desmarca 'Bloquear BBOX' para mover o redimensionar</span>", {
          sticky: true,
          direction: 'top',
          className: 'poi-custom-tooltip'
        });
        if (bboxRectangle.getElement()) {
          bboxRectangle.getElement().style.cursor = 'default';
        }
      } else {
        bboxRectangle.bindTooltip("<strong>Área de Delimitación BBOX</strong><br><span class='text-[11px]'>📍 Arrastra para mover el cuadro &bull; Esquinas para redimensionar</span>", {
          sticky: true,
          direction: 'top',
          className: 'poi-custom-tooltip'
        });
      }

      // Permitir mover el cuadro completo arrastrándolo
      let isDraggingBox = false;
      let dragStartLatLng = null;
      let startB0 = 0, startB1 = 0, startB2 = 0, startB3 = 0;

      function onBoxDragStart(e) {
        if (isDrawingUrbanCore || isDrawingIsolated || isDrawingExclusion) {
          return;
        }
        if (e.originalEvent) {
          L.DomEvent.stopPropagation(e);
          L.DomEvent.preventDefault(e);
        }
        isDraggingBox = true;
        dragStartLatLng = e.latlng;
        startB0 = parseFloat(document.getElementById('cfg_bbox_0').value) || minLon;
        startB1 = parseFloat(document.getElementById('cfg_bbox_1').value) || minLat;
        startB2 = parseFloat(document.getElementById('cfg_bbox_2').value) || maxLon;
        startB3 = parseFloat(document.getElementById('cfg_bbox_3').value) || maxLat;

        mapBbox.dragging.disable();
        mapBbox.getContainer().style.cursor = 'grabbing';
        if (bboxRectangle.getElement()) {
          bboxRectangle.getElement().style.cursor = 'grabbing';
        }

        mapBbox.on('mousemove touchmove', onBoxDragMove);
        mapBbox.on('mouseup touchend', onBoxDragEnd);
        L.DomEvent.on(document, 'mouseup touchend', onBoxDragEnd);
      }

      function onBoxDragMove(e) {
        if (!isDraggingBox || !dragStartLatLng || !e.latlng) return;

        const deltaLng = e.latlng.lng - dragStartLatLng.lng;
        const deltaLat = e.latlng.lat - dragStartLatLng.lat;

        const newB0 = parseFloat((startB0 + deltaLng).toFixed(4));
        const newB1 = parseFloat((startB1 + deltaLat).toFixed(4));
        const newB2 = parseFloat((startB2 + deltaLng).toFixed(4));
        const newB3 = parseFloat((startB3 + deltaLat).toFixed(4));

        document.getElementById('cfg_bbox_0').value = newB0.toFixed(4);
        document.getElementById('cfg_bbox_1').value = newB1.toFixed(4);
        document.getElementById('cfg_bbox_2').value = newB2.toFixed(4);
        document.getElementById('cfg_bbox_3').value = newB3.toFixed(4);

        cityData.city.bbox = [newB0, newB1, newB2, newB3];
        bboxRectangle.setBounds([[newB1, newB0], [newB3, newB2]]);

        if (bboxCornerMarkers.NW) bboxCornerMarkers.NW.setLatLng([newB3, newB0]);
        if (bboxCornerMarkers.NE) bboxCornerMarkers.NE.setLatLng([newB3, newB2]);
        if (bboxCornerMarkers.SE) bboxCornerMarkers.SE.setLatLng([newB1, newB2]);
        if (bboxCornerMarkers.SW) bboxCornerMarkers.SW.setLatLng([newB1, newB0]);
        updateZoomPreviewLayer();
        updateBboxDimensionsDisplay([newB0, newB1, newB2, newB3]);
      }

      function onBoxDragEnd() {
        if (!isDraggingBox) return;
        isDraggingBox = false;
        dragStartLatLng = null;

        mapBbox.off('mousemove touchmove', onBoxDragMove);
        mapBbox.off('mouseup touchend', onBoxDragEnd);
        L.DomEvent.off(document, 'mouseup touchend', onBoxDragEnd);

        mapBbox.dragging.enable();
        mapBbox.getContainer().style.cursor = '';
        if (bboxRectangle.getElement()) {
          bboxRectangle.getElement().style.cursor = 'move';
        }

        triggerAutoSave();
      }

      if (!isBboxLocked) {
        bboxRectangle.on('mousedown touchstart', onBoxDragStart);
      }

      if (fit) {
        mapBbox.fitBounds(bounds, { padding: [40, 40] });
      }

      // Crear las 4 guías visuales en las esquinas si no está bloqueado
      if (!isBboxLocked) {
        const corners = {
          NW: { latLng: [maxLat, minLon], label: '⇱ Noroeste (Arrastrar para ajustar)' },
          NE: { latLng: [maxLat, maxLon], label: '⇲ Noreste (Arrastrar para ajustar)' },
          SE: { latLng: [minLat, maxLon], label: '⇲ Sureste (Arrastrar para ajustar)' },
          SW: { latLng: [minLat, minLon], label: '⇱ Suroeste (Arrastrar para ajustar)' }
        };

        Object.entries(corners).forEach(([key, cfg]) => {
          const marker = L.marker(cfg.latLng, {
            icon: createCornerIcon(key),
            draggable: true,
            zIndexOffset: 1000
          }).addTo(bboxHandlesGroup);

          marker.bindTooltip(`<strong>${cfg.label}</strong>`, { direction: 'top', offset: [0, -10] });

          marker.on('drag', (e) => {
            const latLng = e.target.getLatLng();
            let b0 = parseFloat(document.getElementById('cfg_bbox_0').value);
            let b1 = parseFloat(document.getElementById('cfg_bbox_1').value);
            let b2 = parseFloat(document.getElementById('cfg_bbox_2').value);
            let b3 = parseFloat(document.getElementById('cfg_bbox_3').value);
            if (isNaN(b0)) b0 = minLon;
            if (isNaN(b1)) b1 = minLat;
            if (isNaN(b2)) b2 = maxLon;
            if (isNaN(b3)) b3 = maxLat;

            // Clamping para garantizar que las esquinas nunca se crucen ni inviertan coordenadas
            const MIN_SPAN = 0.01; // ~1km mínimo de seguridad
            if (key === 'NW') {
              b0 = Math.min(latLng.lng, b2 - MIN_SPAN);
              b3 = Math.max(latLng.lat, b1 + MIN_SPAN);
              marker.setLatLng([b3, b0]);
            } else if (key === 'NE') {
              b2 = Math.max(latLng.lng, b0 + MIN_SPAN);
              b3 = Math.max(latLng.lat, b1 + MIN_SPAN);
              marker.setLatLng([b3, b2]);
            } else if (key === 'SE') {
              b2 = Math.max(latLng.lng, b0 + MIN_SPAN);
              b1 = Math.min(latLng.lat, b3 - MIN_SPAN);
              marker.setLatLng([b1, b2]);
            } else if (key === 'SW') {
              b0 = Math.min(latLng.lng, b2 - MIN_SPAN);
              b1 = Math.min(latLng.lat, b3 - MIN_SPAN);
              marker.setLatLng([b1, b0]);
            }

            document.getElementById('cfg_bbox_0').value = b0.toFixed(4);
            document.getElementById('cfg_bbox_1').value = b1.toFixed(4);
            document.getElementById('cfg_bbox_2').value = b2.toFixed(4);
            document.getElementById('cfg_bbox_3').value = b3.toFixed(4);

            const presetSel = document.getElementById('cfg_bbox_preset');
            if (presetSel && presetSel.value !== 'custom') {
              presetSel.value = 'custom';
            }

            cityData.city.bbox = [b0, b1, b2, b3];
            bboxRectangle.setBounds([[b1, b0], [b3, b2]]);

            if (bboxCornerMarkers.NW && key !== 'NW') bboxCornerMarkers.NW.setLatLng([b3, b0]);
            if (bboxCornerMarkers.NE && key !== 'NE') bboxCornerMarkers.NE.setLatLng([b3, b2]);
            if (bboxCornerMarkers.SE && key !== 'SE') bboxCornerMarkers.SE.setLatLng([b1, b2]);
            if (bboxCornerMarkers.SW && key !== 'SW') bboxCornerMarkers.SW.setLatLng([b1, b0]);
            updateZoomPreviewLayer();
            updateBboxDimensionsDisplay([b0, b1, b2, b3]);
          });

          marker.on('dragend', () => {
            triggerAutoSave();
          });

          bboxCornerMarkers[key] = marker;
        });
      }
      updateZoomPreviewLayer();
      updateBboxDimensionsDisplay([minLon, minLat, maxLon, maxLat]);
    }


    function getBboxCenter() {
      const b0 = parseFloat(document.getElementById('cfg_bbox_0')?.value);
      const b1 = parseFloat(document.getElementById('cfg_bbox_1')?.value);
      const b2 = parseFloat(document.getElementById('cfg_bbox_2')?.value);
      const b3 = parseFloat(document.getElementById('cfg_bbox_3')?.value);
      if (!isNaN(b0) && !isNaN(b1) && !isNaN(b2) && !isNaN(b3)) {
        return [(b1 + b3) / 2, (b0 + b2) / 2];
      }
      if (cityData && cityData.city && cityData.city.bbox && Array.isArray(cityData.city.bbox) && cityData.city.bbox.length === 4) {
        const b = cityData.city.bbox;
        return [(b[1] + b[3]) / 2, (b[0] + b[2]) / 2];
      }
      return [21.16, -86.85];
    }

    function getCameraCenter() {
      const latEl = document.getElementById('cfg_initial_lat');
      const lonEl = document.getElementById('cfg_initial_lon');
      if (latEl && lonEl && latEl.value.trim() !== '' && lonEl.value.trim() !== '') {
        const lat = parseFloat(latEl.value);
        const lon = parseFloat(lonEl.value);
        if (!isNaN(lat) && !isNaN(lon)) {
          return [lat, lon];
        }
      }
      if (cityData && cityData.city && Array.isArray(cityData.city.initial_center) && cityData.city.initial_center.length === 2) {
        const lon = parseFloat(cityData.city.initial_center[0]);
        const lat = parseFloat(cityData.city.initial_center[1]);
        if (!isNaN(lat) && !isNaN(lon)) {
          return [lat, lon];
        }
      }
      return getBboxCenter();
    }

    let cameraCenterMarker = null;

    function updateZoomPreviewLayer() {
      if (!mapBbox || !zoomPreviewGroup) return;
      zoomPreviewGroup.clearLayers();
      cameraCenterMarker = null;

      const showPreview = document.getElementById('cfg_show_zoom_preview')?.checked ?? true;
      if (!showPreview) return;

      const center = getCameraCenter();
      const centerLatLng = L.latLng(center[0], center[1]);
      const zoomInput = document.getElementById('cfg_initial_zoom');
      const zoom = zoomInput ? (parseFloat(zoomInput.value) || 11.5) : 11.5;

      // Actualizar inputs si estaban vacios
      const latEl = document.getElementById('cfg_initial_lat');
      const lonEl = document.getElementById('cfg_initial_lon');
      if (latEl && !latEl.value) latEl.value = center[0].toFixed(5);
      if (lonEl && !lonEl.value) lonEl.value = center[1].toFixed(5);

      // Pantalla estandar 16:9 de referencia (1920 x 1080 px en el canvas del juego)
      const screenW = 1920;
      const screenH = 1080;

      // Proyectar coordenadas geograficas a pixeles mundiales Web Mercator al nivel de zoom configurado
      const centerPoint = mapBbox.project(centerLatLng, zoom);
      const swPoint = L.point(centerPoint.x - (screenW / 2), centerPoint.y + (screenH / 2));
      const nePoint = L.point(centerPoint.x + (screenW / 2), centerPoint.y - (screenH / 2));

      const swLatLng = mapBbox.unproject(swPoint, zoom);
      const neLatLng = mapBbox.unproject(nePoint, zoom);
      const cameraBounds = L.latLngBounds(swLatLng, neLatLng);

      zoomPreviewRectangle = L.rectangle(cameraBounds, {
        color: '#7C3AED',       // Violeta moderno de alto contraste
        weight: 2,
        dashArray: '5, 5',
        fillColor: '#8B5CF6',
        fillOpacity: 0.08,
        interactive: false
      }).addTo(zoomPreviewGroup);

      zoomPreviewRectangle.bindTooltip(
        `<strong>🖥️ Encuadre de Cámara Día 1 (16:9)</strong><br>` +
        `<span class="text-[11px]">Centro: [${center[1].toFixed(4)}, ${center[0].toFixed(4)}] &bull; Zoom: <b>${zoom}</b></span>`,
        { sticky: true, direction: 'top', className: 'poi-custom-tooltip' }
      );

      // Marcador de mira interactivo arrastrable
      const cameraIcon = L.divIcon({
        className: 'custom-camera-center-icon',
        html: `<div title="Arrastra para reubicar la cámara inicial del juego" style="width:30px;height:30px;background:rgba(124,58,237,0.92);border:2.5px solid #ffffff;border-radius:50%;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 10px rgba(0,0,0,0.4);cursor:grab;transition:transform 0.15s ease;">
                 <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                   <circle cx="12" cy="12" r="3"></circle>
                   <path d="M3 12h4m10 0h4M12 3v4m0 10v4"></path>
                 </svg>
               </div>`,
        iconSize: [30, 30],
        iconAnchor: [15, 15]
      });

      cameraCenterMarker = L.marker(centerLatLng, {
        icon: cameraIcon,
        draggable: true,
        zIndexOffset: 1000
      }).addTo(zoomPreviewGroup);

      cameraCenterMarker.bindTooltip("<b>🎯 Centro de Cámara Día 1</b><br><span class='text-[10px]'>Arrastra para encuadrar la ciudad</span>", {
        direction: 'top',
        offset: [0, -15]
      });

      cameraCenterMarker.on('drag', function(e) {
        const newPos = e.target.getLatLng();
        if (latEl) latEl.value = newPos.lat.toFixed(5);
        if (lonEl) lonEl.value = newPos.lng.toFixed(5);
        if (cityData && cityData.city) {
          cityData.city.initial_center = [parseFloat(newPos.lng.toFixed(5)), parseFloat(newPos.lat.toFixed(5))];
        }
        // Actualizar el rectangulo de camara en tiempo real
        const cp = mapBbox.project(newPos, zoom);
        const swP = L.point(cp.x - (screenW / 2), cp.y + (screenH / 2));
        const neP = L.point(cp.x + (screenW / 2), cp.y - (screenH / 2));
        const cb = L.latLngBounds(mapBbox.unproject(swP, zoom), mapBbox.unproject(neP, zoom));
        if (zoomPreviewRectangle) {
          zoomPreviewRectangle.setBounds(cb);
          zoomPreviewRectangle.setTooltipContent(
            `<strong>🖥️ Encuadre de Cámara Día 1 (16:9)</strong><br>` +
            `<span class="text-[11px]">Centro: [${newPos.lng.toFixed(4)}, ${newPos.lat.toFixed(4)}] &bull; Zoom: <b>${zoom}</b></span>`
          );
        }
      });

      cameraCenterMarker.on('dragend', function(e) {
        const newPos = e.target.getLatLng();
        triggerAutoSave();
        showToast(`Centro de cámara fijado: [${newPos.lng.toFixed(4)}, ${newPos.lat.toFixed(4)}]`, "info");
      });
    }

    function captureCurrentViewport() {
      if (!mapBbox) return;
      const center = mapBbox.getCenter();
      const rawZoom = mapBbox.getZoom();
      const currentZoom = parseFloat((Math.round(rawZoom * 2) / 2).toFixed(1));

      const zoomInput = document.getElementById('cfg_initial_zoom');
      if (zoomInput) zoomInput.value = currentZoom;

      const latEl = document.getElementById('cfg_initial_lat');
      const lonEl = document.getElementById('cfg_initial_lon');
      if (latEl) latEl.value = center.lat.toFixed(5);
      if (lonEl) lonEl.value = center.lng.toFixed(5);

      if (cityData && cityData.city) {
        cityData.city.initial_zoom = currentZoom;
        cityData.city.initial_center = [parseFloat(center.lng.toFixed(5)), parseFloat(center.lat.toFixed(5))];
      }

      updateZoomPreviewLayer();
      triggerAutoSave();
      showToast(`Vista de cámara capturada: [${center.lng.toFixed(4)}, ${center.lat.toFixed(4)}] (Zoom: ${currentZoom})`, "success");
    }

    function resetCameraToBboxCenter() {
      const center = getBboxCenter();
      const latEl = document.getElementById('cfg_initial_lat');
      const lonEl = document.getElementById('cfg_initial_lon');
      if (latEl) latEl.value = center[0].toFixed(5);
      if (lonEl) lonEl.value = center[1].toFixed(5);

      if (cityData && cityData.city) {
        cityData.city.initial_center = [parseFloat(center[1].toFixed(5)), parseFloat(center[0].toFixed(5))];
      }

      updateZoomPreviewLayer();
      triggerAutoSave();
      showToast("Cámara re-centrada al centroide del BBOX", "info");
    }

    function onCameraParamChange() {
      const zoomInput = document.getElementById('cfg_initial_zoom');
      const latEl = document.getElementById('cfg_initial_lat');
      const lonEl = document.getElementById('cfg_initial_lon');
      if (cityData && cityData.city) {
        if (zoomInput) cityData.city.initial_zoom = parseFloat(zoomInput.value) || 11.5;
        if (latEl && lonEl && latEl.value.trim() !== '' && lonEl.value.trim() !== '') {
          const lat = parseFloat(latEl.value);
          const lon = parseFloat(lonEl.value);
          if (!isNaN(lat) && !isNaN(lon)) {
            cityData.city.initial_center = [parseFloat(lon.toFixed(5)), parseFloat(lat.toFixed(5))];
          }
        }
      }
      updateZoomPreviewLayer();
      triggerAutoSave();
    }

    function previewGameCamera() {
      if (!mapBbox) return;
      const center = getCameraCenter();
      const zoomInput = document.getElementById('cfg_initial_zoom');
      const zoom = zoomInput ? (parseFloat(zoomInput.value) || 11.5) : 11.5;
      mapBbox.flyTo(center, zoom, { duration: 1.0 });
      showToast(`Simulando cámara inicial en [${center[1].toFixed(4)}, ${center[0].toFixed(4)}] (Zoom: ${zoom})`, "info");
    }

    function captureCurrentZoom() {
      if (!mapBbox) return;
      const rawZoom = mapBbox.getZoom();
      const currentZoom = parseFloat((Math.round(rawZoom * 2) / 2).toFixed(1));
      const zoomInput = document.getElementById('cfg_initial_zoom');
      if (zoomInput) {
        zoomInput.value = currentZoom;
        if (cityData && cityData.city) cityData.city.initial_zoom = currentZoom;
        updateZoomPreviewLayer();
        triggerAutoSave();
        showToast(`Zoom inicial adoptado: ${currentZoom}`, "success");
      }
    }

    function syncUrbanParksFromStep5(checked) {
      const el = document.getElementById('cfg_urban_parks_only');
      if (el) {
        el.checked = checked;
        triggerAutoSave();
      }
    }

    function fitMapToBbox() {
      if (bboxRectangle && mapBbox) {
        mapBbox.fitBounds(bboxRectangle.getBounds(), { padding: [40, 40] });
      }
    }

    const BBOX_PRESETS = {
      compact: { widthKm: 115, heightKm: 115, label: "Compacta" },
      standard: { widthKm: 145, heightKm: 145, label: "Estándar Oficial" },
      megacity: { widthKm: 180, heightKm: 180, label: "Megaciudad" },
      corridor_v: { widthKm: 120, heightKm: 280, label: "Corredor N-S" },
      corridor_h: { widthKm: 280, heightKm: 120, label: "Corredor E-W" }
    };

    function calculateBboxDimensions(bbox) {
      if (!Array.isArray(bbox) || bbox.length !== 4) return null;
      const minLon = Math.min(parseFloat(bbox[0]), parseFloat(bbox[2]));
      const minLat = Math.min(parseFloat(bbox[1]), parseFloat(bbox[3]));
      const maxLon = Math.max(parseFloat(bbox[0]), parseFloat(bbox[2]));
      const maxLat = Math.max(parseFloat(bbox[1]), parseFloat(bbox[3]));
      if (isNaN(minLon) || isNaN(minLat) || isNaN(maxLon) || isNaN(maxLat)) return null;

      const midLat = (minLat + maxLat) / 2.0;
      const rad = Math.PI / 180.0;
      const heightKm = (maxLat - minLat) * 111.32;
      const widthKm = (maxLon - minLon) * 111.32 * Math.cos(midLat * rad);
      const areaSqKm = widthKm * heightKm;

      return {
        widthKm: Math.max(0.1, widthKm),
        heightKm: Math.max(0.1, heightKm),
        areaSqKm: Math.max(0.01, areaSqKm),
        center: [(minLon + maxLon) / 2.0, midLat]
      };
    }

    function updateBboxDimensionsDisplay(bbox) {
      const b = bbox || (cityData && cityData.city ? cityData.city.bbox : null);
      const dims = calculateBboxDimensions(b);
      const whEl = document.getElementById('bbox_dim_w_h');
      const areaEl = document.getElementById('bbox_dim_area');
      if (!dims || !whEl || !areaEl) return;

      whEl.innerText = `↔ ${dims.widthKm.toFixed(1)} km × ↕ ${dims.heightKm.toFixed(1)} km`;
      areaEl.innerText = `~${Math.round(dims.areaSqKm).toLocaleString()} km²`;
    }

    function onBboxPresetChanged(presetKey) {
      if (!presetKey || presetKey === 'custom') return;
      if (isBboxLocked) {
        showToast("🔒 Desbloquea la BBOX primero para aplicar un preset", "warning");
        const sel = document.getElementById('cfg_bbox_preset');
        if (sel) sel.value = 'custom';
        return;
      }
      applyBboxPreset(presetKey);
    }

    function applyBboxPreset(presetKey) {
      const preset = BBOX_PRESETS[presetKey];
      if (!preset) return;

      const currentBbox = (cityData && cityData.city && cityData.city.bbox) ? cityData.city.bbox : [-87.05, 21.0, -86.72, 21.31];
      const dims = calculateBboxDimensions(currentBbox);
      const centerLon = dims ? dims.center[0] : ((parseFloat(document.getElementById('cfg_bbox_0')?.value) + parseFloat(document.getElementById('cfg_bbox_2')?.value)) / 2 || -86.88);
      const centerLat = dims ? dims.center[1] : ((parseFloat(document.getElementById('cfg_bbox_1')?.value) + parseFloat(document.getElementById('cfg_bbox_3')?.value)) / 2 || 21.15);

      const rad = Math.PI / 180.0;
      const deltaLat = preset.heightKm / 111.32;
      const deltaLon = preset.widthKm / (111.32 * Math.cos(centerLat * rad));

      const newMinLon = parseFloat((centerLon - deltaLon / 2.0).toFixed(4));
      const newMaxLon = parseFloat((centerLon + deltaLon / 2.0).toFixed(4));
      const newMinLat = parseFloat((centerLat - deltaLat / 2.0).toFixed(4));
      const newMaxLat = parseFloat((centerLat + deltaLat / 2.0).toFixed(4));

      document.getElementById('cfg_bbox_0').value = newMinLon.toFixed(4);
      document.getElementById('cfg_bbox_1').value = newMinLat.toFixed(4);
      document.getElementById('cfg_bbox_2').value = newMaxLon.toFixed(4);
      document.getElementById('cfg_bbox_3').value = newMaxLat.toFixed(4);

      cityData.city.bbox = [newMinLon, newMinLat, newMaxLon, newMaxLat];
      updateBboxLayer(cityData.city.bbox, true);
      updateBboxDimensionsDisplay(cityData.city.bbox);
      showToast(`Preset aplicado: ${preset.label} (${preset.widthKm} × ${preset.heightKm} km)`, "success");
      triggerAutoSave();
    }

    function updateBboxFromInputs() {
      let b0 = parseFloat(document.getElementById('cfg_bbox_0').value);
      let b1 = parseFloat(document.getElementById('cfg_bbox_1').value);
      let b2 = parseFloat(document.getElementById('cfg_bbox_2').value);
      let b3 = parseFloat(document.getElementById('cfg_bbox_3').value);
      if (isNaN(b0) || isNaN(b1) || isNaN(b2) || isNaN(b3)) return;

      const MIN_SPAN = 0.01;
      let minLon = Math.min(b0, b2);
      let maxLon = Math.max(b0, b2);
      let minLat = Math.min(b1, b3);
      let maxLat = Math.max(b1, b3);
      if (maxLon - minLon < MIN_SPAN) maxLon = parseFloat((minLon + MIN_SPAN).toFixed(4));
      if (maxLat - minLat < MIN_SPAN) maxLat = parseFloat((minLat + MIN_SPAN).toFixed(4));

      document.getElementById('cfg_bbox_0').value = minLon.toFixed(4);
      document.getElementById('cfg_bbox_1').value = minLat.toFixed(4);
      document.getElementById('cfg_bbox_2').value = maxLon.toFixed(4);
      document.getElementById('cfg_bbox_3').value = maxLat.toFixed(4);

      const presetSel = document.getElementById('cfg_bbox_preset');
      if (presetSel && presetSel.value !== 'custom') {
        presetSel.value = 'custom';
      }

      cityData.city.bbox = [minLon, minLat, maxLon, maxLat];
      updateBboxLayer(cityData.city.bbox, false);
      updateBboxDimensionsDisplay(cityData.city.bbox);
      triggerAutoSave();
    }

    function toggleBboxLock(locked, showToastMsg = true) {
      isBboxLocked = Boolean(locked);
      if (cityData && cityData.city) {
        cityData.city.bbox_locked = isBboxLocked;
      }
      const icon = document.getElementById('bboxLockIcon');
      const label = document.getElementById('bboxLockLabel');
      const chk = document.getElementById('cfg_bbox_locked');
      if (chk) chk.checked = isBboxLocked;
      if (icon) {
        icon.setAttribute('data-lucide', isBboxLocked ? 'lock' : 'unlock');
        icon.className = `w-3 h-3 ${isBboxLocked ? 'text-metro-orange' : 'text-stone-400'}`;
      }
      if (label) {
        label.innerText = isBboxLocked ? "BBOX Bloqueada" : "Bloquear BBOX";
        label.className = isBboxLocked ? "text-metro-orange font-bold" : "text-stone-700 font-bold";
      }
      ['cfg_bbox_0', 'cfg_bbox_1', 'cfg_bbox_2', 'cfg_bbox_3'].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
          el.disabled = isBboxLocked;
          if (isBboxLocked) el.classList.add('bg-stone-100', 'text-stone-500', 'cursor-not-allowed');
          else el.classList.remove('bg-stone-100', 'text-stone-500', 'cursor-not-allowed');
        }
      });
      const presetSel = document.getElementById('cfg_bbox_preset');
      if (presetSel) {
        presetSel.disabled = isBboxLocked;
        if (isBboxLocked) presetSel.classList.add('bg-stone-100', 'text-stone-500', 'cursor-not-allowed');
        else presetSel.classList.remove('bg-stone-100', 'text-stone-500', 'cursor-not-allowed');
      }
      lucide.createIcons();
      updateBboxLayer(cityData?.city?.bbox, false);
      if (showToastMsg) {
        showToast(isBboxLocked ? "🔒 BBOX bloqueada contra movimientos accidentales" : "🔓 BBOX desbloqueada para edición", "info");
        triggerAutoSave();
      }
    }


    function renderBboxOnPoiMap() {
      if (!mapPoi || !bboxPoiGroup || !cityData || !cityData.city || !cityData.city.bbox) return;
      const bbox = cityData.city.bbox;
      if (!Array.isArray(bbox) || bbox.length !== 4) return;

      bboxPoiGroup.clearLayers();

      const minLon = Math.min(parseFloat(bbox[0]), parseFloat(bbox[2]));
      const minLat = Math.min(parseFloat(bbox[1]), parseFloat(bbox[3]));
      const maxLon = Math.max(parseFloat(bbox[0]), parseFloat(bbox[2]));
      const maxLat = Math.max(parseFloat(bbox[1]), parseFloat(bbox[3]));
      if (isNaN(minLon) || isNaN(minLat) || isNaN(maxLon) || isNaN(maxLat)) return;
      const bounds = [[minLat, minLon], [maxLat, maxLon]];

      // 1. Polígono de máscara exterior con agujero para destacar exclusivamente la zona BBOX
      const worldHole = [
        [[-90, -180], [-90, 180], [90, 180], [90, -180], [-90, -180]],
        [[minLat, minLon], [maxLat, minLon], [maxLat, maxLon], [minLat, maxLon], [minLat, minLon]]
      ];
      L.polygon(worldHole, {
        stroke: false,
        fillColor: '#000000',
        fillOpacity: 0.35,
        interactive: false
      }).addTo(bboxPoiGroup);

      // 2. Línea delimitadora de la BBOX oficial
      L.rectangle(bounds, {
        color: '#F37021',
        weight: 2.5,
        dashArray: '6, 6',
        fill: false,
        interactive: false
      }).addTo(bboxPoiGroup);

      // 3. Etiqueta informativa en la esquina NW
      const nwMarker = L.marker([maxLat, minLon], {
        icon: L.divIcon({
          className: 'bbox-poi-tag',
          html: `<div class="bg-stone-900/90 text-amber-400 font-mono text-[9px] font-bold px-1.5 py-0.5 rounded border border-metro-orange/60 shadow flex items-center space-x-1"><i data-lucide="scan" class="w-2.5 h-2.5 inline"></i><span>BBOX Oficial</span></div>`,
          iconSize: [95, 20],
          iconAnchor: [0, 22]
        }),
        interactive: false
      });
      nwMarker.addTo(bboxPoiGroup);
      lucide.createIcons();
    }

    // =========================================================================
    // GESTIÓN Y TRAZADO INTERACTIVO DE ZONAS AISLADAS (ISLAS / CUENCAS)
    // =========================================================================
    function createIsolatedCornerIcon(key) {
      return L.divIcon({
        className: 'isolated-corner-handle',
        html: `
          <div style="
            display: flex;
            align-items: center;
            justify-content: center;
            width: 20px;
            height: 20px;
            background: #E8E1D3;
            border: 2px solid #06B6D4;
            border-radius: 50%;
            box-shadow: 0 0 8px rgba(6,182,212,0.8);
            color: #083344;
            font-size: 8px;
            font-weight: 800;
            cursor: pointer;
          ">
            <div style="width: 5px; height: 5px; background: #06B6D4; border-radius: 50%;"></div>
          </div>
        `,
        iconSize: [20, 20],
        iconAnchor: [10, 10]
      });
    }

    // -------------------------------------------------------------------------
    // CONTROL CENTRALIZADO DE INTERACTIVIDAD DE CAPAS EN MAPBBOX DURANTE TRAZADOS
    // -------------------------------------------------------------------------
    function setMapBboxDrawingInteractivity(isDrawing) {
      if (!mapBbox) return;
      const container = mapBbox.getContainer();
      if (isDrawing) {
        container.classList.add('leaflet-drawing-active');
      } else {
        container.classList.remove('leaflet-drawing-active');
      }

      // Desactivar o reactivar la interactividad de cada elemento vectorial / marcador en el DOM SVG
      if (bboxRectangle && bboxRectangle.getElement()) {
        bboxRectangle.getElement().style.pointerEvents = isDrawing ? 'none' : '';
      }
      if (urbanCoreLayer && urbanCoreLayer.getElement()) {
        urbanCoreLayer.getElement().style.pointerEvents = isDrawing ? 'none' : '';
      }
      if (isolatedZonesGroup) {
        isolatedZonesGroup.eachLayer(l => {
          if (l.getElement()) l.getElement().style.pointerEvents = isDrawing ? 'none' : '';
        });
      }
      if (isolatedHandlesGroup) {
        isolatedHandlesGroup.eachLayer(l => {
          if (l.getElement()) l.getElement().style.pointerEvents = isDrawing ? 'none' : '';
        });
      }
      if (bboxHandlesGroup) {
        bboxHandlesGroup.eachLayer(l => {
          if (l.getElement()) l.getElement().style.pointerEvents = isDrawing ? 'none' : '';
        });
      }
      if (exclusionZonesBboxGroup) {
        exclusionZonesBboxGroup.eachLayer(l => {
          if (l.getElement()) l.getElement().style.pointerEvents = isDrawing ? 'none' : '';
        });
      }
      if (zoomPreviewGroup) {
        zoomPreviewGroup.eachLayer(l => {
          if (l.getElement()) l.getElement().style.pointerEvents = isDrawing ? 'none' : '';
        });
      }
    }

    function setupIsolatedDrawingEvents() {
      if (!mapBbox) return;

      mapBbox.on('mousedown', (e) => {
        if (!isDrawingIsolated) return;
        isolatedIsDragging = false;
        drawStartLatLng = e.latlng;

        if (drawTempRect) mapBbox.removeLayer(drawTempRect);
        drawTempRect = L.rectangle([drawStartLatLng, drawStartLatLng], {
          color: '#06B6D4',
          weight: 2,
          dashArray: '5, 5',
          fillColor: '#06B6D4',
          fillOpacity: 0.25,
          interactive: false,
          className: 'drawing-temp-shape'
        }).addTo(mapBbox);

        window.addEventListener('mousemove', onIsolatedWindowMouseMove);
        window.addEventListener('mouseup', onIsolatedWindowMouseUp);
      });

      function onIsolatedWindowMouseMove(e) {
        if (!isDrawingIsolated || !drawStartLatLng || !drawTempRect) return;
        const pt = mapBbox.mouseEventToLatLng(e);
        if (!pt) return;
        const distPx = mapBbox.latLngToLayerPoint(drawStartLatLng).distanceTo(mapBbox.latLngToLayerPoint(pt));
        if (distPx > 8) {
          isolatedIsDragging = true;
        }
        drawTempRect.setBounds([drawStartLatLng, pt]);
      }

      function onIsolatedWindowMouseUp(e) {
        window.removeEventListener('mousemove', onIsolatedWindowMouseMove);
        window.removeEventListener('mouseup', onIsolatedWindowMouseUp);

        if (!isDrawingIsolated || !drawStartLatLng) return;
        const endLatLng = mapBbox.mouseEventToLatLng(e);
        if (!endLatLng) return;

        const distPx = mapBbox.latLngToLayerPoint(drawStartLatLng).distanceTo(mapBbox.latLngToLayerPoint(endLatLng));

        if (isolatedIsDragging && distPx > 12) {
          // Completar trazado por arrastre continuo
          finishIsolatedZoneDrawing(drawStartLatLng, endLatLng);
        } else {
          // Trazado de 2 clics: si no teníamos esquina 1, fijarla; si ya estaba, completar
          if (isolatedDrawPhase === 0) {
            isolatedDrawPhase = 1;
            const bannerText = document.getElementById('drawingIsolatedBannerText');
            if (bannerText) bannerText.innerText = "📍 Esquina 1 fijada. Haz clic en la esquina opuesta para completar.";
            showToast("Esquina 1 fijada. Mueve el cursor y haz clic para la esquina opuesta.", "info");
          } else {
            finishIsolatedZoneDrawing(drawStartLatLng, endLatLng);
          }
        }
      }

      mapBbox.on('mousemove', (e) => {
        if (!isDrawingIsolated) return;
        if (isolatedDrawPhase === 1 && drawStartLatLng && drawTempRect && !isolatedIsDragging) {
          drawTempRect.setBounds([drawStartLatLng, e.latlng]);
        }
      });

      mapBbox.on('click', (e) => {
        if (!isDrawingIsolated) return;
        if (isolatedDrawPhase === 1 && drawStartLatLng) {
          const distPx = mapBbox.latLngToLayerPoint(drawStartLatLng).distanceTo(mapBbox.latLngToLayerPoint(e.latlng));
          if (distPx > 8) {
            finishIsolatedZoneDrawing(drawStartLatLng, e.latlng);
          }
        }
      });

      window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isDrawingIsolated) {
          cancelDrawingIsolatedZone();
        }
      });
    }

    function finishIsolatedZoneDrawing(startLatLng, endLatLng) {
      const minLon = Math.min(startLatLng.lng, endLatLng.lng);
      const minLat = Math.min(startLatLng.lat, endLatLng.lat);
      const maxLon = Math.max(startLatLng.lng, endLatLng.lng);
      const maxLat = Math.max(startLatLng.lat, endLatLng.lat);

      if (drawTempRect && mapBbox) {
        mapBbox.removeLayer(drawTempRect);
        drawTempRect = null;
      }
      cancelDrawingIsolatedZone();

      // Evitar áreas microscópicas accidentales
      if (maxLon - minLon < 0.001 || maxLat - minLat < 0.001) {
        showToast("Área trazada muy pequeña o inválida", "warning");
        return;
      }

      openIsolatedZoneModal(-1, [
        parseFloat(minLon.toFixed(4)),
        parseFloat(minLat.toFixed(4)),
        parseFloat(maxLon.toFixed(4)),
        parseFloat(maxLat.toFixed(4))
      ]);
    }

    function startDrawingIsolatedZone() {
      if (!currentCityFile) {
        showToast("Selecciona o crea un proyecto primero", "warning");
        return;
      }
      isDrawingIsolated = true;
      isolatedDrawPhase = 0;
      isolatedIsDragging = false;
      drawStartLatLng = null;

      if (drawTempRect && mapBbox) {
        mapBbox.removeLayer(drawTempRect);
        drawTempRect = null;
      }

      if (mapBbox) {
        mapBbox.dragging.disable();
        mapBbox.getContainer().style.cursor = 'crosshair';
        setMapBboxDrawingInteractivity(true);
      }
      const banner = document.getElementById('drawingIsolatedBanner');
      if (banner) {
        const bannerText = document.getElementById('drawingIsolatedBannerText');
        if (bannerText) bannerText.innerText = "Arrastra un cuadro o haz 2 clics para encuadrar la isla";
        banner.classList.remove('hidden');
      }
      lucide.createIcons();
      showToast("Modo trazado activo: Arrastra un cuadro o haz 2 clics para definir las esquinas", "info");
    }

    function cancelDrawingIsolatedZone() {
      isDrawingIsolated = false;
      isolatedDrawPhase = 0;
      isolatedIsDragging = false;
      drawStartLatLng = null;
      if (drawTempRect && mapBbox) {
        mapBbox.removeLayer(drawTempRect);
        drawTempRect = null;
      }
      if (mapBbox) {
        mapBbox.dragging.enable();
        mapBbox.getContainer().style.cursor = '';
        setMapBboxDrawingInteractivity(false);
      }
      const banner = document.getElementById('drawingIsolatedBanner');
      if (banner) banner.classList.add('hidden');
    }

    function openIsolatedZoneModal(index = -1, bboxCoords = null) {
      document.getElementById('iso_edit_index').value = index;
      const titleEl = document.getElementById('modalIsolatedTitle');

      if (!cityData.isolated_zones) cityData.isolated_zones = [];

      if (index >= 0 && index < cityData.isolated_zones.length) {
        const z = cityData.isolated_zones[index];
        if (titleEl) titleEl.innerHTML = `<i data-lucide="shield-alert" class="w-5 h-5 text-cyan-600"></i><span>Editar Zona Aislada</span>`;
        document.getElementById('iso_name').value = z.name || "";
        document.getElementById('iso_id').value = z.id || "";
        const b = z.bbox || [-86.76, 21.20, -86.68, 21.28];
        document.getElementById('iso_bbox_0').value = b[0];
        document.getElementById('iso_bbox_1').value = b[1];
        document.getElementById('iso_bbox_2').value = b[2];
        document.getElementById('iso_bbox_3').value = b[3];
      } else {
        if (titleEl) titleEl.innerHTML = `<i data-lucide="shield-alert" class="w-5 h-5 text-cyan-600"></i><span>Nueva Zona Aislada (Isla)</span>`;
        const nextIdx = cityData.isolated_zones.length + 1;
        document.getElementById('iso_name').value = `Isla ${nextIdx}`;
        document.getElementById('iso_id').value = `isla_${nextIdx}`;
        let b = bboxCoords;
        if (!b || b.length !== 4) {
          // Centrar inteligentemente en el mapa actual o en el centro del BBOX principal
          let centerLon = -86.85, centerLat = 21.16;
          if (mapBbox) {
            const c = mapBbox.getCenter();
            centerLon = c.lng;
            centerLat = c.lat;
          } else if (cityData?.city?.bbox) {
            centerLon = (cityData.city.bbox[0] + cityData.city.bbox[2]) / 2;
            centerLat = (cityData.city.bbox[1] + cityData.city.bbox[3]) / 2;
          }
          b = [
            parseFloat((centerLon - 0.025).toFixed(4)),
            parseFloat((centerLat - 0.025).toFixed(4)),
            parseFloat((centerLon + 0.025).toFixed(4)),
            parseFloat((centerLat + 0.025).toFixed(4))
          ];
        }
        document.getElementById('iso_bbox_0').value = b[0];
        document.getElementById('iso_bbox_1').value = b[1];
        document.getElementById('iso_bbox_2').value = b[2];
        document.getElementById('iso_bbox_3').value = b[3];
      }

      document.getElementById('modalIsolatedZone').classList.remove('hidden');
      lucide.createIcons();
      setTimeout(() => document.getElementById('iso_name').focus(), 50);
    }

    function closeIsolatedZoneModal() {
      document.getElementById('modalIsolatedZone').classList.add('hidden');
    }

    function onIsoNameInput() {
      const editIdx = parseInt(document.getElementById('iso_edit_index').value, 10);
      if (editIdx === -1) {
        const nameVal = document.getElementById('iso_name').value;
        const slug = nameVal.trim().toLowerCase()
          .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
          .replace(/[^a-z0-9_]+/g, "_")
          .replace(/^_+|_+$/g, "");
        if (slug) document.getElementById('iso_id').value = slug;
      }
    }

    function submitIsolatedZoneModal() {
      const editIdx = parseInt(document.getElementById('iso_edit_index').value, 10);
      const name = document.getElementById('iso_name').value.trim();
      let id = document.getElementById('iso_id').value.trim().toLowerCase()
        .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
        .replace(/[^a-z0-9_]+/g, "_");

      if (!name) {
        showToast("El nombre descriptivo es obligatorio", "warning");
        return;
      }
      if (!id) id = "zona_" + Date.now();

      const b0 = parseFloat(document.getElementById('iso_bbox_0').value);
      const b1 = parseFloat(document.getElementById('iso_bbox_1').value);
      const b2 = parseFloat(document.getElementById('iso_bbox_2').value);
      const b3 = parseFloat(document.getElementById('iso_bbox_3').value);

      if (isNaN(b0) || isNaN(b1) || isNaN(b2) || isNaN(b3)) {
        showToast("Las 4 coordenadas BBOX deben ser numéricas", "error");
        return;
      }
      if (b0 >= b2 || b1 >= b3) {
        showToast("Coordenadas BBOX inválidas: Min Lon/Lat debe ser menor a Max Lon/Lat", "error");
        return;
      }

      const normalizedBbox = [
        parseFloat(Math.min(b0, b2).toFixed(4)),
        parseFloat(Math.min(b1, b3).toFixed(4)),
        parseFloat(Math.max(b0, b2).toFixed(4)),
        parseFloat(Math.max(b1, b3).toFixed(4))
      ];

      if (!cityData.isolated_zones) cityData.isolated_zones = [];

      if (editIdx >= 0 && editIdx < cityData.isolated_zones.length) {
        cityData.isolated_zones[editIdx] = { id, name, bbox: normalizedBbox };
        selectedIsolatedZoneIndex = editIdx;
      } else {
        cityData.isolated_zones.push({ id, name, bbox: normalizedBbox });
        selectedIsolatedZoneIndex = cityData.isolated_zones.length - 1;
      }

      closeIsolatedZoneModal();
      renderIsolatedZones();
      triggerAutoSave();
      showToast(`Zona aislada "${name}" guardada con éxito`, "success");
    }

    function deleteIsolatedZone(index) {
      if (!cityData.isolated_zones || index < 0 || index >= cityData.isolated_zones.length) return;
      const zone = cityData.isolated_zones[index];
      if (!confirm(`¿Eliminar la zona aislada "${zone.name || zone.id}"?`)) return;

      cityData.isolated_zones.splice(index, 1);
      if (selectedIsolatedZoneIndex === index) {
        selectedIsolatedZoneIndex = -1;
      } else if (selectedIsolatedZoneIndex > index) {
        selectedIsolatedZoneIndex--;
      }
      renderIsolatedZones();
      triggerAutoSave();
      showToast("Zona aislada eliminada", "info");
    }

    function fitMapToIsolatedZone(index) {
      if (!cityData.isolated_zones || index < 0 || index >= cityData.isolated_zones.length) return;
      const z = cityData.isolated_zones[index];
      const b = z.bbox;
      if (mapBbox && b && b.length === 4) {
        mapBbox.fitBounds([[b[1], b[0]], [b[3], b[2]]], { padding: [50, 50] });
      }
      selectIsolatedZone(index);
    }

    function selectIsolatedZone(index) {
      selectedIsolatedZoneIndex = index;
      renderIsolatedZonesList();
      renderIsolatedZonesOnMap();
    }

    function renderIsolatedZonesList() {
      const container = document.getElementById('isolatedZonesListContainer');
      const badge = document.getElementById('isolatedZonesBadge');
      const zones = cityData.isolated_zones || [];

      if (badge) {
        badge.innerText = `${zones.length} ${zones.length === 1 ? 'zona' : 'zonas'}`;
      }

      if (!container) return;
      container.innerHTML = '';

      if (zones.length === 0) {
        container.innerHTML = `
          <div class="text-center py-3 text-stone-500 text-[11px] italic bg-metro-dark/40 rounded-lg border border-metro-border/50">
            Sin zonas aisladas configuradas.<br>Usa "+ Trazar Zona Aislada" para definir islas.
          </div>
        `;
        return;
      }

      zones.forEach((z, idx) => {
        const isSelected = (idx === selectedIsolatedZoneIndex);
        const card = document.createElement('div');
        card.className = `p-2.5 rounded-lg border transition text-xs flex flex-col space-y-1.5 cursor-pointer ${
          isSelected
            ? 'bg-cyan-950/40 border-cyan-500/70 shadow-md shadow-cyan-950/50'
            : 'bg-metro-panel border-metro-border hover:border-cyan-500/40'
        }`;

        const b = z.bbox || [0, 0, 0, 0];
        const bboxFmt = `[${b[0].toFixed(2)}, ${b[1].toFixed(2)}, ${b[2].toFixed(2)}, ${b[3].toFixed(2)}]`;

        card.innerHTML = `
          <div class="flex items-center justify-between">
            <div class="flex items-center space-x-1.5 overflow-hidden">
              <span class="text-sm">🏝️</span>
              <span class="font-bold text-white truncate">${escapeHtml(z.name || z.id)}</span>
              <span class="text-[10px] font-mono text-cyan-300/80 bg-cyan-950/60 px-1.5 py-0.5 rounded border border-cyan-500/30 shrink-0">${escapeHtml(z.id)}</span>
            </div>
            <div class="flex items-center space-x-1 shrink-0">
              <button title="Enfocar en mapa" onclick="event.stopPropagation(); fitMapToIsolatedZone(${idx})" class="p-1 text-gray-400 hover:text-cyan-400 rounded hover:bg-metro-card transition">
                <i data-lucide="crosshair" class="w-3.5 h-3.5"></i>
              </button>
              <button title="Editar nombre / coordenadas" onclick="event.stopPropagation(); openIsolatedZoneModal(${idx})" class="p-1 text-gray-400 hover:text-white rounded hover:bg-metro-card transition">
                <i data-lucide="edit-2" class="w-3.5 h-3.5"></i>
              </button>
              <button title="Eliminar zona" onclick="event.stopPropagation(); deleteIsolatedZone(${idx})" class="p-1 text-gray-400 hover:text-rose-400 rounded hover:bg-metro-card transition">
                <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
              </button>
            </div>
          </div>
          <div class="flex justify-between items-center text-[10px] text-gray-400 font-mono">
            <span>BBOX: ${bboxFmt}</span>
            ${isSelected ? '<span class="text-cyan-400 font-bold">Activa</span>' : ''}
          </div>
        `;

        card.onclick = () => selectIsolatedZone(idx);
        container.appendChild(card);
      });

      lucide.createIcons();
    }

    function renderIsolatedZonesOnMap() {
      if (!mapBbox || !isolatedZonesGroup) return;
      isolatedZonesGroup.clearLayers();
      if (isolatedHandlesGroup) isolatedHandlesGroup.clearLayers();

      const zones = cityData.isolated_zones || [];

      zones.forEach((z, idx) => {
        const b = z.bbox;
        if (!b || b.length !== 4) return;
        const bounds = [[b[1], b[0]], [b[3], b[2]]];
        const isSelected = (idx === selectedIsolatedZoneIndex);

        const rect = L.rectangle(bounds, {
          color: '#06B6D4',
          weight: isSelected ? 3 : 2,
          fillColor: '#06B6D4',
          fillOpacity: isSelected ? 0.28 : 0.16,
          dashArray: isSelected ? '4, 4' : '6, 4',
          className: 'draggable-isolated-rect'
        }).addTo(isolatedZonesGroup);

        rect.bindTooltip(`
          <div class="p-1 text-xs">
            <strong class="text-cyan-400 flex items-center space-x-1">
              <span>🏝️ ${escapeHtml(z.name || z.id)}</span>
            </strong>
            <span class="text-[10px] text-gray-300 font-mono">ID: ${escapeHtml(z.id)}</span><br>
            <span class="text-[10px] text-cyan-200">${isSelected ? '📍 Esquinas editables activas' : 'Haz clic para seleccionar'}</span>
          </div>
        `, { sticky: true, className: 'poi-custom-tooltip' });

        rect.on('click', (e) => {
          if (isDrawingIsolated || isDrawingUrbanCore || isDrawingExclusion) return;
          L.DomEvent.stopPropagation(e);
          selectIsolatedZone(idx);
        });

        // Permitir mover la caja completa de la zona aislada arrastrándola
        if (isSelected) {
          let isDraggingIsoBox = false;
          let isoDragStartLatLng = null;
          let isoStartB0 = 0, isoStartB1 = 0, isoStartB2 = 0, isoStartB3 = 0;

          function onIsoBoxDragStart(e) {
            if (isDrawingUrbanCore || isDrawingIsolated || isDrawingExclusion) return;
            if (e.originalEvent) {
              L.DomEvent.stopPropagation(e);
              L.DomEvent.preventDefault(e);
            }
            isDraggingIsoBox = true;
            isoDragStartLatLng = e.latlng;
            isoStartB0 = z.bbox[0];
            isoStartB1 = z.bbox[1];
            isoStartB2 = z.bbox[2];
            isoStartB3 = z.bbox[3];

            mapBbox.dragging.disable();
            mapBbox.getContainer().style.cursor = 'grabbing';
            if (rect.getElement()) rect.getElement().style.cursor = 'grabbing';

            mapBbox.on('mousemove touchmove', onIsoBoxDragMove);
            mapBbox.on('mouseup touchend', onIsoBoxDragEnd);
            L.DomEvent.on(document, 'mouseup touchend', onIsoBoxDragEnd);
          }

          function onIsoBoxDragMove(e) {
            if (!isDraggingIsoBox || !isoDragStartLatLng || !e.latlng) return;
            const deltaLng = e.latlng.lng - isoDragStartLatLng.lng;
            const deltaLat = e.latlng.lat - isoDragStartLatLng.lat;

            const newB0 = parseFloat((isoStartB0 + deltaLng).toFixed(4));
            const newB1 = parseFloat((isoStartB1 + deltaLat).toFixed(4));
            const newB2 = parseFloat((isoStartB2 + deltaLng).toFixed(4));
            const newB3 = parseFloat((isoStartB3 + deltaLat).toFixed(4));

            z.bbox = [newB0, newB1, newB2, newB3];
            rect.setBounds([[newB1, newB0], [newB3, newB2]]);

            if (isolatedCornerMarkers.NW) isolatedCornerMarkers.NW.setLatLng([newB3, newB0]);
            if (isolatedCornerMarkers.NE) isolatedCornerMarkers.NE.setLatLng([newB3, newB2]);
            if (isolatedCornerMarkers.SE) isolatedCornerMarkers.SE.setLatLng([newB1, newB2]);
            if (isolatedCornerMarkers.SW) isolatedCornerMarkers.SW.setLatLng([newB1, newB0]);

            renderIsolatedZonesList();
          }

          function onIsoBoxDragEnd() {
            if (!isDraggingIsoBox) return;
            isDraggingIsoBox = false;
            isoDragStartLatLng = null;

            mapBbox.off('mousemove touchmove', onIsoBoxDragMove);
            mapBbox.off('mouseup touchend', onIsoBoxDragEnd);
            L.DomEvent.off(document, 'mouseup touchend', onIsoBoxDragEnd);

            mapBbox.dragging.enable();
            mapBbox.getContainer().style.cursor = '';
            if (rect.getElement()) rect.getElement().style.cursor = 'move';
            triggerAutoSave();
          }

          rect.on('mousedown touchstart', onIsoBoxDragStart);
        }

        // Tiradores de esquinas si está seleccionada
        if (isSelected && isolatedHandlesGroup) {
          isolatedCornerMarkers = { NW: null, NE: null, SE: null, SW: null };
          const cornerConfigs = [
            { key: 'NW', pos: [b[3], b[0]], label: 'NO' },
            { key: 'NE', pos: [b[3], b[2]], label: 'NE' },
            { key: 'SE', pos: [b[1], b[2]], label: 'SE' },
            { key: 'SW', pos: [b[1], b[0]], label: 'SO' }
          ];

          cornerConfigs.forEach(cfg => {
            const marker = L.marker(cfg.pos, {
              icon: createIsolatedCornerIcon(cfg.label),
              draggable: true,
              zIndexOffset: 1500
            }).addTo(isolatedHandlesGroup);
            isolatedCornerMarkers[cfg.key] = marker;

            marker.bindTooltip(`<strong>${cfg.label} - ${escapeHtml(z.name)}</strong>`, { direction: 'top', offset: [0, -10] });

            marker.on('drag', (e) => {
              const latLng = e.target.getLatLng();
              let b0 = z.bbox[0];
              let b1 = z.bbox[1];
              let b2 = z.bbox[2];
              let b3 = z.bbox[3];

              const MIN_SPAN = 0.005;
              if (cfg.key === 'NW') {
                b0 = Math.min(latLng.lng, b2 - MIN_SPAN);
                b3 = Math.max(latLng.lat, b1 + MIN_SPAN);
                marker.setLatLng([b3, b0]);
                if (isolatedCornerMarkers.NE) isolatedCornerMarkers.NE.setLatLng([b3, b2]);
                if (isolatedCornerMarkers.SW) isolatedCornerMarkers.SW.setLatLng([b1, b0]);
              } else if (cfg.key === 'NE') {
                b2 = Math.max(latLng.lng, b0 + MIN_SPAN);
                b3 = Math.max(latLng.lat, b1 + MIN_SPAN);
                marker.setLatLng([b3, b2]);
                if (isolatedCornerMarkers.NW) isolatedCornerMarkers.NW.setLatLng([b3, b0]);
                if (isolatedCornerMarkers.SE) isolatedCornerMarkers.SE.setLatLng([b1, b2]);
              } else if (cfg.key === 'SE') {
                b2 = Math.max(latLng.lng, b0 + MIN_SPAN);
                b1 = Math.min(latLng.lat, b3 - MIN_SPAN);
                marker.setLatLng([b1, b2]);
                if (isolatedCornerMarkers.SW) isolatedCornerMarkers.SW.setLatLng([b1, b0]);
                if (isolatedCornerMarkers.NE) isolatedCornerMarkers.NE.setLatLng([b3, b2]);
              } else if (cfg.key === 'SW') {
                b0 = Math.min(latLng.lng, b2 - MIN_SPAN);
                b1 = Math.min(latLng.lat, b3 - MIN_SPAN);
                marker.setLatLng([b1, b0]);
                if (isolatedCornerMarkers.NW) isolatedCornerMarkers.NW.setLatLng([b3, b0]);
                if (isolatedCornerMarkers.SE) isolatedCornerMarkers.SE.setLatLng([b1, b2]);
              }

              z.bbox = [
                parseFloat(b0.toFixed(4)),
                parseFloat(b1.toFixed(4)),
                parseFloat(b2.toFixed(4)),
                parseFloat(b3.toFixed(4))
              ];
              rect.setBounds([[z.bbox[1], z.bbox[0]], [z.bbox[3], z.bbox[2]]]);
              renderIsolatedZonesList();
            });

            marker.on('dragend', () => {
              triggerAutoSave();
            });
          });
        }
      });
    }

    function renderIsolatedZonesOnDemandMap() {
      if (!mapDemand || !isolatedZonesDemandGroup) return;
      isolatedZonesDemandGroup.clearLayers();

      const zones = cityData.isolated_zones || [];
      zones.forEach(z => {
        const b = z.bbox;
        if (!b || b.length !== 4) return;
        const bounds = [[b[1], b[0]], [b[3], b[2]]];
        L.rectangle(bounds, {
          color: '#06B6D4',
          weight: 2,
          fillColor: '#06B6D4',
          fillOpacity: 0.15,
          dashArray: '6, 4'
        }).bindTooltip(`<strong>🏝️ ${escapeHtml(z.name || z.id)}</strong><br><span class="text-[10px] text-stone-500">Zona Aislada</span>`, { sticky: true }).addTo(isolatedZonesDemandGroup);
      });
    }

    function renderIsolatedZones() {
      renderIsolatedZonesList();
      renderIsolatedZonesOnMap();
      renderIsolatedZonesOnDemandMap();
    }

    // -------------------------------------------------------------------------
    // SUBSISTEMA: NÚCLEO URBANO (URBAN CORE AOI LOD)
    // -------------------------------------------------------------------------
    function setupUrbanCoreDrawingEvents() {
      if (!mapBbox) return;

      mapBbox.on('click', (e) => {
        if (!isDrawingUrbanCore) return;
        addUrbanCoreVertex(e.latlng);
      });

      mapBbox.on('dblclick', (e) => {
        if (!isDrawingUrbanCore) return;
        L.DomEvent.stop(e);
        finishUrbanCoreDrawing();
      });

      window.addEventListener('keydown', (e) => {
        if (isDrawingUrbanCore) {
          if (e.key === 'Escape') cancelDrawingUrbanCore();
          if (e.key === 'Enter') finishUrbanCoreDrawing();
        }
        if (isEditingUrbanCore) {
          if (e.key === 'Escape' || e.key === 'Enter') stopEditingUrbanCore();
        }
      });
    }

    function startDrawingUrbanCore() {
      if (!currentCityFile) {
        showToast("Selecciona o crea un proyecto primero", "warning");
        return;
      }
      if (isEditingUrbanCore) stopEditingUrbanCore(false);
      isDrawingUrbanCore = true;
      urbanCoreDrawPoints = [];
      urbanCoreDrawMarkers = [];
      if (urbanCoreTempShape && mapBbox) {
        mapBbox.removeLayer(urbanCoreTempShape);
        urbanCoreTempShape = null;
      }
      if (mapBbox) {
        mapBbox.doubleClickZoom.disable();
        mapBbox.getContainer().style.cursor = 'crosshair';
        setMapBboxDrawingInteractivity(true);
      }
      const banner = document.getElementById('drawingUrbanCoreBanner');
      if (banner) banner.classList.remove('hidden');
      lucide.createIcons();
      showToast("Modo trazado núcleo activo: Haz clics en el mapa para rodear la mancha urbana (doble clic o Enter para cerrar)", "info");
    }

    function cancelDrawingUrbanCore() {
      isDrawingUrbanCore = false;
      urbanCoreDrawPoints = [];
      if (urbanCoreTempShape && mapBbox) {
        mapBbox.removeLayer(urbanCoreTempShape);
        urbanCoreTempShape = null;
      }
      if (urbanCoreDrawMarkers && mapBbox) {
        urbanCoreDrawMarkers.forEach(m => mapBbox.removeLayer(m));
        urbanCoreDrawMarkers = [];
      }
      if (mapBbox) {
        mapBbox.doubleClickZoom.enable();
        mapBbox.getContainer().style.cursor = '';
        setMapBboxDrawingInteractivity(false);
      }
      const banner = document.getElementById('drawingUrbanCoreBanner');
      if (banner) banner.classList.add('hidden');
    }

    function addUrbanCoreVertex(latlng) {
      if (urbanCoreDrawPoints.length >= 3) {
        const first = urbanCoreDrawPoints[0];
        const dist = mapBbox.distance(first, latlng);
        if (dist < 35) {
          finishUrbanCoreDrawing();
          return;
        }
      }

      const isFirst = (urbanCoreDrawPoints.length === 0);
      urbanCoreDrawPoints.push(latlng);

      const m = L.circleMarker(latlng, {
        radius: isFirst ? 7 : 5,
        color: '#D97706',
        fillColor: isFirst ? '#D97706' : '#FFFFFF',
        fillOpacity: 1,
        weight: 2,
        interactive: isFirst,
        className: isFirst ? 'drawing-handle-marker' : ''
      }).addTo(mapBbox);
      urbanCoreDrawMarkers.push(m);

      if (isFirst) {
        m.bindTooltip("Clic para cerrar polígono", { direction: 'top', className: 'poi-custom-tooltip' });
        m.on('click', (e) => {
          if (urbanCoreDrawPoints.length >= 3) {
            L.DomEvent.stopPropagation(e);
            finishUrbanCoreDrawing();
          }
        });
      }

      if (!urbanCoreTempShape) {
        urbanCoreTempShape = L.polygon([urbanCoreDrawPoints], {
          color: '#D97706',
          weight: 2.5,
          dashArray: '6, 6',
          fillColor: '#F59E0B',
          fillOpacity: 0.25,
          interactive: false,
          className: 'drawing-temp-shape'
        }).addTo(mapBbox);
      } else {
        urbanCoreTempShape.setLatLngs([urbanCoreDrawPoints]);
      }
    }

    function finishUrbanCoreDrawing() {
      if (urbanCoreDrawPoints.length < 3) {
        showToast("El polígono requiere al menos 3 vértices", "warning");
        return;
      }

      const rawCoords = urbanCoreDrawPoints.map(p => [
        parseFloat(p.lng.toFixed(5)),
        parseFloat(p.lat.toFixed(5))
      ]);
      if (rawCoords[0][0] !== rawCoords[rawCoords.length - 1][0] || rawCoords[0][1] !== rawCoords[rawCoords.length - 1][1]) {
        rawCoords.push([...rawCoords[0]]);
      }

      cancelDrawingUrbanCore();
      setUrbanCorePolygon(rawCoords);
      showToast(`Polígono núcleo definido (${rawCoords.length - 1} vértices). Guardando...`, "success");
      saveCurrentCity(true);
    }

    function setUrbanCorePolygon(coords) {
      if (!cityData) cityData = {};
      if (!cityData.city) cityData.city = {};
      cityData.city.urban_core_polygon = coords;
      renderUrbanCorePolygon();
    }

    function clearUrbanCorePolygon() {
      if (isEditingUrbanCore) stopEditingUrbanCore(false);
      if (!cityData || !cityData.city) return;
      cityData.city.urban_core_polygon = null;
      renderUrbanCorePolygon();
      showToast("Polígono núcleo eliminado. Modo detalle 100% en todo el BBOX restaurado.", "info");
      saveCurrentCity(true);
    }

    function renderUrbanCorePolygon() {
      if (urbanCoreLayer && mapBbox) {
        mapBbox.removeLayer(urbanCoreLayer);
        urbanCoreLayer = null;
      }
      if (urbanCoreGroup) {
        urbanCoreGroup.clearLayers();
      }

      const badge = document.getElementById('urbanCoreBadge');
      const clearBtn = document.getElementById('btnClearUrbanCore');
      const editBtn = document.getElementById('btnEditUrbanCoreVertices');
      const poly = cityData?.city?.urban_core_polygon;

      if (poly && Array.isArray(poly) && poly.length >= 3) {
        const latlngs = poly.map(p => [p[1], p[0]]);
        urbanCoreLayer = L.polygon(latlngs, {
          color: '#D97706',
          weight: 2.5,
          dashArray: '5, 5',
          fillColor: '#F59E0B',
          fillOpacity: 0.2
        }).bindTooltip(`<strong>🏛️ Núcleo Urbano (LOD / Demanda)</strong><br><span class="text-[10px] text-stone-500">Demanda activa, edificios 3D y calles finas</span>`, { sticky: true });

        if (urbanCoreGroup) {
          urbanCoreGroup.addLayer(urbanCoreLayer);
        } else if (mapBbox) {
          urbanCoreLayer.addTo(mapBbox);
        }
        if (badge) {
          badge.innerText = `${poly.length - 1} vértices`;
          badge.className = "text-[10px] bg-amber-100 border border-amber-300 text-amber-800 px-2 py-0.5 rounded-full font-bold";
        }
        if (clearBtn) clearBtn.classList.remove('hidden');
        if (editBtn) editBtn.classList.remove('hidden');

        if (isEditingUrbanCore) {
          renderUrbanCoreEditHandles();
        } else if (urbanCoreHandlesGroup) {
          urbanCoreHandlesGroup.clearLayers();
        }
      } else {
        if (badge) {
          badge.innerText = "100% BBOX";
          badge.className = "text-[10px] bg-stone-200 border border-stone-300 text-stone-700 px-2 py-0.5 rounded-full font-bold";
        }
        if (clearBtn) clearBtn.classList.add('hidden');
        if (editBtn) editBtn.classList.add('hidden');
        if (isEditingUrbanCore) stopEditingUrbanCore(false);
        if (urbanCoreHandlesGroup) urbanCoreHandlesGroup.clearLayers();
      }
      lucide.createIcons();
    }

    // -------------------------------------------------------------------------
    // EDICIÓN INTERACTIVA DE VÉRTICES DEL NÚCLEO URBANO
    // -------------------------------------------------------------------------
    function toggleEditUrbanCoreVertices() {
      if (isEditingUrbanCore) {
        stopEditingUrbanCore();
      } else {
        startEditingUrbanCore();
      }
    }

    function startEditingUrbanCore() {
      const poly = cityData?.city?.urban_core_polygon;
      if (!poly || !Array.isArray(poly) || poly.length < 3) {
        showToast("Primero traza o autodetecta un contorno de núcleo urbano", "warning");
        return;
      }
      if (isDrawingUrbanCore) cancelDrawingUrbanCore();
      isEditingUrbanCore = true;

      const banner = document.getElementById('editingUrbanCoreBanner');
      if (banner) banner.classList.remove('hidden');

      const btn = document.getElementById('btnEditUrbanCoreVertices');
      const btnText = document.getElementById('btnEditUrbanCoreVerticesText');
      if (btn) {
        btn.className = "bg-amber-600 hover:bg-amber-500 border border-amber-700 text-white text-xs font-bold py-1.5 px-2 rounded-lg flex items-center justify-center space-x-1 transition shadow-2xs";
      }
      if (btnText) btnText.innerText = "✓ Listo";

      renderUrbanCoreEditHandles();
      showToast("Modo edición activo: Arrastra vértices • Clic en '+' para insertar • Clic derecho para eliminar", "info");
      lucide.createIcons();
    }

    function stopEditingUrbanCore(notify = true) {
      if (!isEditingUrbanCore) return;
      isEditingUrbanCore = false;

      const banner = document.getElementById('editingUrbanCoreBanner');
      if (banner) banner.classList.add('hidden');

      const btn = document.getElementById('btnEditUrbanCoreVertices');
      const btnText = document.getElementById('btnEditUrbanCoreVerticesText');
      if (btn) {
        btn.className = "bg-stone-100 hover:bg-stone-200 border border-stone-300 text-stone-800 text-xs font-bold py-1.5 px-2 rounded-lg flex items-center justify-center space-x-1 transition shadow-2xs";
      }
      if (btnText) btnText.innerText = "Editar Vértices";

      if (urbanCoreHandlesGroup) urbanCoreHandlesGroup.clearLayers();
      urbanCoreVertexMarkers = [];
      urbanCoreMidpointMarkers = [];

      if (notify) showToast("Vértices del núcleo urbano guardados.", "success");
      lucide.createIcons();
      saveCurrentCity(true);
    }

    function createUrbanCoreVertexIcon() {
      return L.divIcon({
        className: 'urban-core-vertex-handle',
        html: `
          <div style="
            width: 14px;
            height: 14px;
            background: #FFFFFF;
            border: 2.5px solid #D97706;
            border-radius: 50%;
            box-shadow: 0 0 6px rgba(217,119,6,0.85);
            cursor: grab;
          "></div>
        `,
        iconSize: [14, 14],
        iconAnchor: [7, 7]
      });
    }

    function createUrbanCoreMidpointIcon() {
      return L.divIcon({
        className: 'urban-core-midpoint-handle',
        html: `
          <div style="
            width: 12px;
            height: 12px;
            background: #FEF3C7;
            border: 1.5px dashed #D97706;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 9px;
            font-weight: 800;
            color: #B45309;
            box-shadow: 0 0 4px rgba(217,119,6,0.5);
            cursor: pointer;
          ">+</div>
        `,
        iconSize: [12, 12],
        iconAnchor: [6, 6]
      });
    }

    function renderUrbanCoreEditHandles() {
      if (!urbanCoreHandlesGroup || !mapBbox) return;
      urbanCoreHandlesGroup.clearLayers();
      urbanCoreVertexMarkers = [];
      urbanCoreMidpointMarkers = [];

      const poly = cityData?.city?.urban_core_polygon;
      if (!poly || !Array.isArray(poly) || poly.length < 3) return;

      // Extraer vértices únicos (excluyendo punto de cierre idéntico)
      const isClosed = (poly[0][0] === poly[poly.length - 1][0] && poly[0][1] === poly[poly.length - 1][1]);
      const uniqueCoords = isClosed ? poly.slice(0, -1) : poly.slice();
      const n = uniqueCoords.length;

      // 1. Tiradores primarios arrastrables para cada vértice
      uniqueCoords.forEach((coord, idx) => {
        const latlng = [coord[1], coord[0]];
        const marker = L.marker(latlng, {
          icon: createUrbanCoreVertexIcon(),
          draggable: true,
          zIndexOffset: 2500
        }).addTo(urbanCoreHandlesGroup);

        marker.bindTooltip(`<strong>Vértice ${idx + 1}</strong><br><span class="text-[9px] text-stone-400">Arrastra para mover • Clic derecho para eliminar</span>`, { direction: 'top', offset: [0, -8] });

        marker.on('drag', (e) => {
          const newPos = e.target.getLatLng();
          uniqueCoords[idx] = [
            parseFloat(newPos.lng.toFixed(5)),
            parseFloat(newPos.lat.toFixed(5))
          ];
          const updatedClosed = [...uniqueCoords, [...uniqueCoords[0]]];
          if (urbanCoreLayer) {
            urbanCoreLayer.setLatLngs(updatedClosed.map(c => [c[1], c[0]]));
          }
          updateUrbanCoreMidpointPositions(uniqueCoords);
        });

        marker.on('dragend', () => {
          const updatedClosed = [...uniqueCoords, [...uniqueCoords[0]]];
          cityData.city.urban_core_polygon = updatedClosed;
          const badge = document.getElementById('urbanCoreBadge');
          if (badge) badge.innerText = `${updatedClosed.length - 1} vértices`;
          renderUrbanCoreEditHandles();
          saveCurrentCity(true);
        });

        marker.on('contextmenu', (e) => {
          L.DomEvent.preventDefault(e);
          L.DomEvent.stopPropagation(e);
          if (uniqueCoords.length <= 3) {
            showToast("Un polígono debe tener al menos 3 vértices", "warning");
            return;
          }
          uniqueCoords.splice(idx, 1);
          const updatedClosed = [...uniqueCoords, [...uniqueCoords[0]]];
          cityData.city.urban_core_polygon = updatedClosed;
          if (urbanCoreLayer) {
            urbanCoreLayer.setLatLngs(updatedClosed.map(c => [c[1], c[0]]));
          }
          const badge = document.getElementById('urbanCoreBadge');
          if (badge) badge.innerText = `${updatedClosed.length - 1} vértices`;
          renderUrbanCoreEditHandles();
          showToast("Vértice eliminado", "info");
          saveCurrentCity(true);
        });

        urbanCoreVertexMarkers.push(marker);
      });

      // 2. Tiradores fantasma en puntos medios para insertar nuevos vértices
      for (let i = 0; i < n; i++) {
        const nextIdx = (i + 1) % n;
        const c1 = uniqueCoords[i];
        const c2 = uniqueCoords[nextIdx];
        const midLatLng = [(c1[1] + c2[1]) / 2, (c1[0] + c2[0]) / 2];

        const midMarker = L.marker(midLatLng, {
          icon: createUrbanCoreMidpointIcon(),
          zIndexOffset: 2400
        }).addTo(urbanCoreHandlesGroup);

        midMarker.bindTooltip(`<strong>+ Insertar Vértice</strong><br><span class="text-[9px] text-stone-400">Clic para añadir punto aquí</span>`, { direction: 'top', offset: [0, -7] });

        const insertIndex = i + 1;
        midMarker.on('click', (e) => {
          L.DomEvent.stopPropagation(e);
          const newCoord = [
            parseFloat(midLatLng[1].toFixed(5)),
            parseFloat(midLatLng[0].toFixed(5))
          ];
          uniqueCoords.splice(insertIndex, 0, newCoord);
          const updatedClosed = [...uniqueCoords, [...uniqueCoords[0]]];
          cityData.city.urban_core_polygon = updatedClosed;
          if (urbanCoreLayer) {
            urbanCoreLayer.setLatLngs(updatedClosed.map(c => [c[1], c[0]]));
          }
          const badge = document.getElementById('urbanCoreBadge');
          if (badge) badge.innerText = `${updatedClosed.length - 1} vértices`;
          renderUrbanCoreEditHandles();
          showToast(`Vértice ${insertIndex + 1} insertado`, "success");
          saveCurrentCity(true);
        });

        urbanCoreMidpointMarkers.push(midMarker);
      }
    }

    function updateUrbanCoreMidpointPositions(coords) {
      const n = coords.length;
      if (urbanCoreMidpointMarkers.length !== n) return;
      for (let i = 0; i < n; i++) {
        const nextIdx = (i + 1) % n;
        const c1 = coords[i];
        const c2 = coords[nextIdx];
        urbanCoreMidpointMarkers[i].setLatLng([(c1[1] + c2[1]) / 2, (c1[0] + c2[0]) / 2]);
      }
    }

    // -------------------------------------------------------------------------
    // AJUSTADOR DE ALCANCE Y GUÍA CARTOGRÁFICA DE AUTO CENSO
    // -------------------------------------------------------------------------
    function onUrbanCoreReachInput(val) {
      const km = parseInt(val, 10);
      const badge = document.getElementById('urbanCoreReachBadge');
      if (badge) {
        let desc = "Estándar";
        if (km <= 10) desc = "Compacto";
        else if (km <= 18) desc = "Estándar";
        else if (km <= 30) desc = "Metropolitano";
        else desc = "Regional";
        badge.innerText = `${km} km (${desc})`;
      }
      highlightActiveReachPreset(km);
      showUrbanCoreReachGuide(km);
    }

    function onUrbanCoreReachChange(val) {
      const km = parseInt(val, 10);
      showUrbanCoreReachGuide(km);
    }

    function setUrbanCoreReachPreset(km) {
      const input = document.getElementById('cfg_urban_core_reach');
      if (input) {
        input.value = km;
        onUrbanCoreReachInput(km);
      }
    }

    function highlightActiveReachPreset(km) {
      document.querySelectorAll('.reach-preset-btn').forEach(btn => {
        const bKm = parseInt(btn.innerText, 10);
        if (bKm === km) {
          btn.className = "reach-preset-btn text-[10px] py-0.5 px-1 bg-amber-100 text-amber-900 border border-amber-300 rounded font-bold transition text-center";
        } else {
          btn.className = "reach-preset-btn text-[10px] py-0.5 px-1 bg-stone-100 hover:bg-amber-100 hover:text-amber-900 border border-stone-200 rounded text-stone-600 font-medium transition text-center";
        }
      });
    }

    function getCityCenterLatLng() {
      const initCenter = cityData?.city?.initial_center;
      if (Array.isArray(initCenter) && initCenter.length === 2 && !isNaN(initCenter[0]) && !isNaN(initCenter[1])) {
        return [initCenter[1], initCenter[0]];
      }
      const poly = cityData?.city?.urban_core_polygon;
      if (poly && poly.length >= 3) {
        const avgLon = poly.reduce((acc, p) => acc + p[0], 0) / poly.length;
        const avgLat = poly.reduce((acc, p) => acc + p[1], 0) / poly.length;
        return [avgLat, avgLon];
      }
      const bbox = cityData?.city?.bbox;
      if (bbox && bbox.length === 4) {
        return [(bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2];
      }
      if (mapBbox) {
        const c = mapBbox.getCenter();
        return [c.lat, c.lng];
      }
      return [20.97, -89.62];
    }

    function showUrbanCoreReachGuide(km) {
      if (!mapBbox) return;
      const centerLatLng = getCityCenterLatLng();

      if (!urbanCoreReachPreviewCircle) {
        urbanCoreReachPreviewCircle = L.circle(centerLatLng, {
          radius: km * 1000,
          color: '#D97706',
          weight: 2,
          dashArray: '6, 6',
          fillColor: '#F59E0B',
          fillOpacity: 0.10,
          interactive: false
        }).addTo(mapBbox);
      } else {
        urbanCoreReachPreviewCircle.setLatLng(centerLatLng);
        urbanCoreReachPreviewCircle.setRadius(km * 1000);
      }

      if (urbanCoreReachTimer) clearTimeout(urbanCoreReachTimer);
      urbanCoreReachTimer = setTimeout(() => {
        if (urbanCoreReachPreviewCircle && mapBbox) {
          mapBbox.removeLayer(urbanCoreReachPreviewCircle);
          urbanCoreReachPreviewCircle = null;
        }
      }, 4000);
    }

    let autoUrbanCoreController = null;

    function setAutoUrbanCoreState(state, message = '') {
      const running = state === 'running';
      const button = document.getElementById('btnAutoUrbanCore');
      if (button) {
        button.disabled = running;
        button.setAttribute('aria-busy', String(running));
      }
      const label = document.getElementById('btnAutoUrbanCoreText');
      if (label) label.textContent = running ? 'Calculando…' : 'Auto Censo';
      const status = document.getElementById('autoUrbanCoreStatus');
      if (status) {
        status.hidden = !message;
        status.dataset.state = state;
        status.textContent = message;
      }
    }

    function cancelAutoUrbanCore() {
      if (autoUrbanCoreController) autoUrbanCoreController.abort();
      autoUrbanCoreController = null;
      setAutoUrbanCoreState('idle');
    }

    async function autoDetectUrbanCore() {
      if (!currentCityFile) {
        showToast("Selecciona o crea un proyecto primero", "warning");
        return;
      }
      if (autoUrbanCoreController) return;
      const file = currentCityFile;
      const version = cityLoadVersion;
      const controller = new AbortController();
      autoUrbanCoreController = controller;
      const isCurrent = () => currentCityFile === file && cityLoadVersion === version && autoUrbanCoreController === controller;
      const reachInput = document.getElementById('cfg_urban_core_reach');
      const reachKm = reachInput ? reachInput.value : '15';
      const started = Date.now();
      let phase = 'Guardando configuración';
      const updateProgress = () => {
        if (isCurrent()) setAutoUrbanCoreState('running', `${phase} · ${Math.floor((Date.now() - started) / 1000)} s. Alcance: ${reachKm} km. La primera consulta puede tardar varios minutos.`);
      };
      updateProgress();
      const progressTimer = setInterval(updateProgress, 1000);
      try {
        const savedConfig = await saveCurrentCity(true);
        if (!isCurrent()) return;
        if (!savedConfig) throw new Error('No se pudo guardar la configuración. Revisa Guardar YAML y vuelve a intentarlo.');
        const previousPolygon = JSON.stringify(cityData.city.urban_core_polygon);
        phase = 'Leyendo censo y calculando contorno';
        updateProgress();
        const data = await fetchStartupJson(`/api/auto-urban-polygon?file=${encodeURIComponent(file)}&reach_km=${encodeURIComponent(reachKm)}`, 'Auto Censo', 600000, controller.signal);
        if (!isCurrent()) return;
        if (data.status !== 'ok' || !Array.isArray(data.polygon) || data.polygon.length < 4 ||
            !data.polygon.every(p => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite))) {
          throw new Error(data.message || 'No se recibió un contorno válido. Se conservó el contorno anterior.');
        }
        if (JSON.stringify(cityData.city.urban_core_polygon) !== previousPolygon) {
          throw new Error('El contorno cambió durante el cálculo. Se conservó tu edición; vuelve a ejecutar Auto Censo.');
        }
        if (urbanCoreReachPreviewCircle && mapBbox) {
          mapBbox.removeLayer(urbanCoreReachPreviewCircle);
          urbanCoreReachPreviewCircle = null;
        }
        setUrbanCorePolygon(data.polygon);
        if (urbanCoreLayer && mapBbox) mapBbox.fitBounds(urbanCoreLayer.getBounds(), {padding: [28, 28]});
        phase = 'Contorno calculado; guardando YAML';
        updateProgress();
        const savedPolygon = await saveCurrentCity(true);
        if (!isCurrent()) return;
        if (!savedPolygon) throw new Error('Contorno calculado, pero no se pudo guardar. Quedó como borrador; pulsa Guardar YAML.');
        const message = `Contorno guardado: ${data.points_count} puntos, ${data.polygon.length - 1} vértices, alcance ${data.reach_km} km · ${Math.round((Date.now() - started) / 1000)} s.`;
        setAutoUrbanCoreState('success', message);
        showToast(message, 'success');
      } catch (err) {
        if (isCurrent()) {
          setAutoUrbanCoreState('error', err.message);
          showToast(err.message, 'error');
        }
      } finally {
        clearInterval(progressTimer);
        if (autoUrbanCoreController === controller) {
          autoUrbanCoreController = null;
          const button = document.getElementById('btnAutoUrbanCore');
          if (button) { button.disabled = false; button.setAttribute('aria-busy', 'false'); }
          const label = document.getElementById('btnAutoUrbanCoreText');
          if (label) label.textContent = 'Auto Censo';
        }
      }
    }

    // -------------------------------------------------------------------------
    // SUBSISTEMA: ZONAS DE ALTA AFLUENCIA (AFFLUENCE ZONES)
    // -------------------------------------------------------------------------
    let isDrawingAffluence = false;
    let affluenceDrawMode = 'polygon';
    let affluenceDrawPoints = [];
    let affluenceTempShape = null;
    let affluenceDrawMarkers = [];
    let affluenceBoxStart = null;
    let selectedAffluenceIndex = -1;
    let currentAffluenceDraft = null;
    let affluenceTelemetryTimer = null;

    const AFFLUENCE_ARCHETYPES = {
      tourism: { name: "Corredor Turístico", multiplier: 2.2, reach_bonus: 0.50, color: "#F59E0B" },
      cbd: { name: "Distrito Financiero", multiplier: 2.5, reach_bonus: 0.40, color: "#8B5CF6" },
      industrial: { name: "Parque Industrial", multiplier: 1.8, reach_bonus: 0.30, color: "#06B6D4" },
      commercial: { name: "Corredor Comercial", multiplier: 1.4, reach_bonus: 0.15, color: "#10B981" },
      custom: { name: "Personalizado", multiplier: 2.0, reach_bonus: 0.30, color: "#F59E0B" }
    };

    function setupAffluenceDrawingEvents() {
      if (!mapPoi) return;

      mapPoi.on('click', (e) => {
        if (!isDrawingAffluence || affluenceDrawMode !== 'polygon') return;
        addAffluencePolygonVertex(e.latlng);
      });

      mapPoi.on('dblclick', (e) => {
        if (!isDrawingAffluence || affluenceDrawMode !== 'polygon') return;
        L.DomEvent.stop(e);
        finishAffluencePolygonDrawing();
      });

      mapPoi.on('mousedown', (e) => {
        if (!isDrawingAffluence || affluenceDrawMode !== 'bbox') return;
        affluenceBoxStart = e.latlng;
        if (affluenceTempShape) mapPoi.removeLayer(affluenceTempShape);
        affluenceTempShape = L.rectangle([affluenceBoxStart, affluenceBoxStart], {
          color: '#F59E0B',
          weight: 2,
          dashArray: '5, 5',
          fillColor: '#F59E0B',
          fillOpacity: 0.25,
          interactive: false,
          className: 'drawing-temp-shape'
        }).addTo(mapPoi);
      });

      mapPoi.on('mousemove', (e) => {
        if (!isDrawingAffluence) return;
        if (affluenceDrawMode === 'bbox' && affluenceBoxStart && affluenceTempShape) {
          affluenceTempShape.setBounds([affluenceBoxStart, e.latlng]);
        } else if (affluenceDrawMode === 'polygon' && affluenceDrawPoints.length > 0 && affluenceTempShape) {
          const currentPts = [...affluenceDrawPoints, e.latlng];
          affluenceTempShape.setLatLngs([currentPts]);
        }
      });

      mapPoi.on('mouseup', (e) => {
        if (!isDrawingAffluence || affluenceDrawMode !== 'bbox' || !affluenceBoxStart) return;
        const endLatLng = e.latlng;
        const minLon = Math.min(affluenceBoxStart.lng, endLatLng.lng);
        const minLat = Math.min(affluenceBoxStart.lat, endLatLng.lat);
        const maxLon = Math.max(affluenceBoxStart.lng, endLatLng.lng);
        const maxLat = Math.max(affluenceBoxStart.lat, endLatLng.lat);

        cancelDrawingAffluenceZone();

        if (maxLon - minLon < 0.001 || maxLat - minLat < 0.001) {
          showToast("Área trazada muy pequeña o inválida", "warning");
          return;
        }

        const bbox = [
          parseFloat(minLon.toFixed(5)),
          parseFloat(minLat.toFixed(5)),
          parseFloat(maxLon.toFixed(5)),
          parseFloat(maxLat.toFixed(5))
        ];
        const coordinates = [
          [bbox[0], bbox[1]],
          [bbox[2], bbox[1]],
          [bbox[2], bbox[3]],
          [bbox[0], bbox[3]],
          [bbox[0], bbox[1]]
        ];

        openAffluenceZoneModal(-1, {
          type: 'bbox',
          bbox: bbox,
          coordinates: coordinates,
          name: 'Nueva Zona de Afluencia'
        });
      });

      window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isDrawingAffluence) {
          cancelDrawingAffluenceZone();
        } else if (e.key === 'Enter' && isDrawingAffluence && affluenceDrawMode === 'polygon') {
          finishAffluencePolygonDrawing();
        }
      });
    }

    function startDrawingAffluenceZone(mode = 'polygon') {
      if (!currentCityFile) {
        showToast("Selecciona o crea un proyecto primero", "warning");
        return;
      }
      isDrawingAffluence = true;
      affluenceDrawMode = mode;
      affluenceDrawPoints = [];
      affluenceDrawMarkers = [];
      affluenceBoxStart = null;

      if (mapPoi) {
        if (mode === 'bbox') mapPoi.dragging.disable();
        mapPoi.getContainer().style.cursor = 'crosshair';
        mapPoi.getContainer().classList.add('leaflet-drawing-active');
      }

      const banner = document.getElementById('drawingZoneBanner');
      const instructions = document.getElementById('drawingZoneInstructions');
      if (banner) banner.classList.remove('hidden');
      if (instructions) {
        if (mode === 'polygon') {
          instructions.innerText = "Haz clics para trazar los vértices del polígono. Clic en el primer punto o Enter para cerrar.";
        } else {
          instructions.innerText = "Haz clic y arrastra en el mapa para delimitar el rectángulo de afluencia.";
        }
      }
      lucide.createIcons();
      showToast(`Modo trazado activo: ${mode === 'polygon' ? 'Polígono libre' : 'Rectángulo'}`, "info");
    }

    function cancelDrawingAffluenceZone() {
      isDrawingAffluence = false;
      affluenceBoxStart = null;
      affluenceDrawPoints = [];

      if (affluenceTempShape && mapPoi) {
        mapPoi.removeLayer(affluenceTempShape);
        affluenceTempShape = null;
      }
      if (affluenceDrawMarkers && mapPoi) {
        affluenceDrawMarkers.forEach(m => mapPoi.removeLayer(m));
        affluenceDrawMarkers = [];
      }

      if (mapPoi) {
        mapPoi.dragging.enable();
        mapPoi.getContainer().style.cursor = '';
        mapPoi.getContainer().classList.remove('leaflet-drawing-active');
      }

      const banner = document.getElementById('drawingZoneBanner');
      if (banner) banner.classList.add('hidden');
    }

    function addAffluencePolygonVertex(latlng) {
      if (affluenceDrawPoints.length >= 3) {
        const first = affluenceDrawPoints[0];
        const dist = mapPoi.distance(first, latlng);
        if (dist < 35) {
          finishAffluencePolygonDrawing();
          return;
        }
      }

      const isFirst = (affluenceDrawPoints.length === 0);
      affluenceDrawPoints.push(latlng);

      const m = L.circleMarker(latlng, {
        radius: isFirst ? 7 : 5,
        color: '#F59E0B',
        fillColor: isFirst ? '#F59E0B' : '#FFFFFF',
        fillOpacity: 1,
        weight: 2,
        interactive: isFirst,
        className: isFirst ? 'drawing-handle-marker' : ''
      }).addTo(mapPoi);
      affluenceDrawMarkers.push(m);

      if (isFirst) {
        m.bindTooltip("Clic para cerrar zona de afluencia", { direction: 'top', className: 'poi-custom-tooltip' });
        m.on('click', (e) => {
          if (affluenceDrawPoints.length >= 3) {
            L.DomEvent.stopPropagation(e);
            finishAffluencePolygonDrawing();
          }
        });
      }

      if (!affluenceTempShape) {
        affluenceTempShape = L.polygon([affluenceDrawPoints], {
          color: '#F59E0B',
          weight: 2,
          dashArray: '5, 5',
          fillColor: '#F59E0B',
          fillOpacity: 0.2,
          interactive: false,
          className: 'drawing-temp-shape'
        }).addTo(mapPoi);
      } else {
        affluenceTempShape.setLatLngs([affluenceDrawPoints]);
      }
    }

    function finishAffluencePolygonDrawing() {
      if (affluenceDrawPoints.length < 3) {
        showToast("Un polígono requiere al menos 3 vértices", "warning");
        return;
      }

      const rawCoords = affluenceDrawPoints.map(p => [
        parseFloat(p.lng.toFixed(5)),
        parseFloat(p.lat.toFixed(5))
      ]);
      if (rawCoords[0][0] !== rawCoords[rawCoords.length - 1][0] || rawCoords[0][1] !== rawCoords[rawCoords.length - 1][1]) {
        rawCoords.push([rawCoords[0][0], rawCoords[0][1]]);
      }

      cancelDrawingAffluenceZone();

      openAffluenceZoneModal(-1, {
        type: 'polygon',
        coordinates: rawCoords,
        name: 'Nuevo Corredor de Afluencia'
      });
    }

    function openAffluenceZoneModal(index = -1, draftData = null) {
      selectedAffluenceIndex = index;
      document.getElementById('az_edit_index').value = index;

      cityData.affluence_zones = cityData.affluence_zones || [];
      const z = (index >= 0 && cityData.affluence_zones[index]) ? { ...cityData.affluence_zones[index] } : (draftData || {});

      currentAffluenceDraft = {
        id: z.id || `zone_${Date.now().toString().slice(-4)}`,
        name: z.name || "Nueva Zona de Afluencia",
        type: z.type || "polygon",
        archetype: z.archetype || "tourism",
        multiplier: parseFloat(z.multiplier || 2.2),
        reach_bonus: parseFloat(z.reach_bonus !== undefined ? z.reach_bonus : 0.50),
        target_mode: z.target_mode || "MULTIPLIER",
        target_jobs: z.target_jobs || null,
        color: z.color || "#F59E0B",
        enabled: z.enabled !== undefined ? z.enabled : true,
        coordinates: z.coordinates || [],
        bbox: z.bbox || null
      };

      document.getElementById('az_name').value = currentAffluenceDraft.name;
      document.getElementById('az_id').value = currentAffluenceDraft.id;
      document.getElementById('az_archetype').value = currentAffluenceDraft.archetype;
      document.getElementById('az_multiplier').value = currentAffluenceDraft.multiplier;
      document.getElementById('az_mult_display').innerText = `${currentAffluenceDraft.multiplier.toFixed(1)}x`;
      document.getElementById('az_reach_bonus').value = currentAffluenceDraft.reach_bonus;
      document.getElementById('az_reach_display').innerText = `+${Math.round(currentAffluenceDraft.reach_bonus * 100)}%`;
      document.getElementById('az_color').value = currentAffluenceDraft.color;
      document.getElementById('az_color_hex').innerText = currentAffluenceDraft.color;
      document.getElementById('az_target_mode').value = currentAffluenceDraft.target_mode;
      document.getElementById('az_target_jobs').value = currentAffluenceDraft.target_jobs || '';

      const targetGroup = document.getElementById('az_target_jobs_group');
      if (targetGroup) {
        if (currentAffluenceDraft.target_mode === 'TARGET_CAPACITY') targetGroup.classList.remove('hidden');
        else targetGroup.classList.add('hidden');
      }

      document.getElementById('modalAffluenceTitle').querySelector('span').innerText = index >= 0 ? "Editar Zona de Afluencia" : "Nueva Zona de Afluencia";
      document.getElementById('modalAffluenceZone').classList.remove('hidden');
      lucide.createIcons();

      requestAffluenceZoneTelemetry();
    }

    function closeAffluenceZoneModal() {
      document.getElementById('modalAffluenceZone').classList.add('hidden');
      currentAffluenceDraft = null;
    }

    function onAzNameInput() {
      const name = document.getElementById('az_name').value;
      if (currentAffluenceDraft && selectedAffluenceIndex < 0) {
        const slug = name.trim().toLowerCase().replace(/[^a-z0-9]/g, '_').replace(/_+/g, '_').slice(0, 20);
        document.getElementById('az_id').value = slug ? `zone_${slug}` : `zone_${Date.now().toString().slice(-4)}`;
      }
    }

    function onAzArchetypeChanged(archetype) {
      if (AFFLUENCE_ARCHETYPES[archetype] && archetype !== 'custom') {
        const arch = AFFLUENCE_ARCHETYPES[archetype];
        document.getElementById('az_multiplier').value = arch.multiplier;
        document.getElementById('az_mult_display').innerText = `${arch.multiplier.toFixed(1)}x`;
        document.getElementById('az_reach_bonus').value = arch.reach_bonus;
        document.getElementById('az_reach_display').innerText = `+${Math.round(arch.reach_bonus * 100)}%`;
        document.getElementById('az_color').value = arch.color;
        document.getElementById('az_color_hex').innerText = arch.color;
      }
      requestAffluenceZoneTelemetry();
    }

    function onAzMultiplierInput(val) {
      const m = parseFloat(val);
      document.getElementById('az_mult_display').innerText = `${m.toFixed(1)}x`;
      document.getElementById('az_archetype').value = 'custom';
      requestAffluenceZoneTelemetry();
    }

    function onAzReachInput(val) {
      const r = parseFloat(val);
      document.getElementById('az_reach_display').innerText = `+${Math.round(r * 100)}%`;
      document.getElementById('az_archetype').value = 'custom';
    }

    function onAzColorInput(val) {
      document.getElementById('az_color_hex').innerText = val;
    }

    function onAzTargetModeChanged(mode) {
      const targetGroup = document.getElementById('az_target_jobs_group');
      if (targetGroup) {
        if (mode === 'TARGET_CAPACITY') targetGroup.classList.remove('hidden');
        else targetGroup.classList.add('hidden');
      }
    }

    function requestAffluenceZoneTelemetry() {
      if (affluenceTelemetryTimer) clearTimeout(affluenceTelemetryTimer);
      affluenceTelemetryTimer = setTimeout(async () => {
        if (!currentAffluenceDraft || !currentCityFile) return;

        const mult = parseFloat(document.getElementById('az_multiplier').value) || 1.0;
        const statusEl = document.getElementById('az_telemetry_status');
        if (statusEl) statusEl.innerText = "Consultando...";

        const payload = {
          file: currentCityFile,
          zone: {
            ...currentAffluenceDraft,
            id: document.getElementById('az_id').value || currentAffluenceDraft.id,
            multiplier: mult
          }
        };

        try {
          const res = await fetch('/api/affluence-zones/inspect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });
          const json = await res.json();
          if (json.status === 'ok') {
            document.getElementById('az_est_count').innerText = Number(json.establishment_count || 0).toLocaleString();
            document.getElementById('az_base_jobs').innerText = Number(json.base_jobs || 0).toLocaleString();
            document.getElementById('az_boosted_jobs').innerText = Number(json.boosted_jobs || 0).toLocaleString();
            if (statusEl) statusEl.innerText = "Actualizado";

            const poisNote = document.getElementById('az_pois_note');
            if (poisNote) {
              if (json.pois_inside && json.pois_inside.length > 0) {
                poisNote.classList.remove('hidden');
                document.getElementById('az_pois_text').innerText = `${json.pois_inside.length} POI(s) especial(es) en la zona (${json.pois_inside.map(p => p.id).join(', ')}) conservan sus empleos manuales intactos.`;
              } else {
                poisNote.classList.add('hidden');
              }
            }

            const overlapAlert = document.getElementById('az_overlap_alert');
            if (overlapAlert) {
              if (json.overlapping && json.overlapping.length > 0) {
                overlapAlert.classList.remove('hidden');
                document.getElementById('az_overlap_text').innerText = `Solapamiento detectado con '${json.overlapping.map(o => o.name).join(', ')}': los nodos compartidos adoptarán la regla MAX Priority.`;
              } else {
                overlapAlert.classList.add('hidden');
              }
            }
          }
        } catch (e) {
          if (statusEl) statusEl.innerText = "Error";
        }
      }, 250);
    }

    function submitAffluenceZoneModal() {
      const name = document.getElementById('az_name').value.trim();
      const id = document.getElementById('az_id').value.trim();
      if (!name || !id) {
        showToast("Nombre e ID son obligatorios", "warning");
        return;
      }

      cityData.affluence_zones = cityData.affluence_zones || [];
      const mult = parseFloat(document.getElementById('az_multiplier').value) || 2.0;
      const reach = parseFloat(document.getElementById('az_reach_bonus').value) || 0.30;
      const color = document.getElementById('az_color').value;
      const archetype = document.getElementById('az_archetype').value;
      const targetMode = document.getElementById('az_target_mode').value;
      const targetJobs = parseInt(document.getElementById('az_target_jobs').value) || null;

      const zoneObj = {
        ...currentAffluenceDraft,
        name: name,
        id: id,
        archetype: archetype,
        multiplier: mult,
        reach_bonus: reach,
        color: color,
        target_mode: targetMode,
        target_jobs: targetMode === 'TARGET_CAPACITY' ? targetJobs : null,
        enabled: currentAffluenceDraft.enabled !== undefined ? currentAffluenceDraft.enabled : true
      };

      if (selectedAffluenceIndex >= 0) {
        cityData.affluence_zones[selectedAffluenceIndex] = zoneObj;
        showToast("Zona de afluencia actualizada", "success");
      } else {
        cityData.affluence_zones.push(zoneObj);
        showToast("Nueva zona de afluencia agregada", "success");
      }

      closeAffluenceZoneModal();
      renderAffluenceZones();
      triggerAutoSave();
    }

    function deleteAffluenceZone(index) {
      if (!cityData.affluence_zones || index < 0 || index >= cityData.affluence_zones.length) return;
      const zName = cityData.affluence_zones[index].name || "Zona";
      cityData.affluence_zones.splice(index, 1);
      renderAffluenceZones();
      triggerAutoSave();
      showToast(`Zona '${zName}' eliminada`, "info");
    }

    function toggleAffluenceZone(index) {
      if (!cityData.affluence_zones || index < 0 || index >= cityData.affluence_zones.length) return;
      cityData.affluence_zones[index].enabled = !cityData.affluence_zones[index].enabled;
      renderAffluenceZones();
      triggerAutoSave();
    }

    function centerAffluenceZoneOnMap(index) {
      if (!cityData.affluence_zones || index < 0 || index >= cityData.affluence_zones.length) return;
      const z = cityData.affluence_zones[index];
      if (!mapPoi) return;

      if (z.coordinates && z.coordinates.length >= 3) {
        const bounds = L.latLngBounds(z.coordinates.map(c => [c[1], c[0]]));
        mapPoi.fitBounds(bounds, { padding: [50, 50] });
      } else if (z.bbox && z.bbox.length === 4) {
        mapPoi.fitBounds([[z.bbox[1], z.bbox[0]], [z.bbox[3], z.bbox[2]]], { padding: [50, 50] });
      }
    }

    function renderAffluenceZones() {
      renderAffluenceZonesList();
      renderAffluenceZonesOnMap();
      renderAffluenceZonesOnDemandMap();
      updateZoneBalanceMeter();
    }

    function renderAffluenceZonesList() {
      const container = document.getElementById('affluenceZoneListContainer');
      const badge = document.getElementById('affluenceZoneBadge');
      const countLabel = document.getElementById('affluenceZoneCountLabel');
      const zones = cityData.affluence_zones || [];

      if (badge) badge.innerText = zones.length;
      if (countLabel) countLabel.innerText = `${zones.length} ${zones.length === 1 ? 'zona configurada' : 'zonas configuradas'}`;
      if (!container) return;

      if (zones.length === 0) {
        container.innerHTML = `
          <div class="text-center py-6 text-stone-400 bg-stone-50 border border-dashed border-stone-300 rounded-xl space-y-2">
            <i data-lucide="sparkles" class="w-6 h-6 mx-auto text-stone-300"></i>
            <p class="text-xs font-medium">No hay zonas de afluencia definidas.</p>
            <p class="text-[10px] text-stone-400">Traza un polígono o rectángulo para potenciar distritos como centros de negocio o zonas turísticas.</p>
          </div>
        `;
        lucide.createIcons();
        return;
      }

      container.innerHTML = zones.map((z, idx) => {
        const isEnabled = z.enabled !== false;
        const color = z.color || '#F59E0B';
        const mult = parseFloat(z.multiplier || 2.0).toFixed(1);
        const reach = Math.round(parseFloat(z.reach_bonus || 0) * 100);
        const archName = AFFLUENCE_ARCHETYPES[z.archetype]?.name || 'Personalizado';

        return `
          <div class="p-3 bg-white border ${isEnabled ? 'border-stone-200 shadow-xs' : 'border-dashed border-stone-300 opacity-60'} rounded-xl transition hover:border-amber-400 space-y-2">
            <div class="flex items-center justify-between">
              <div class="flex items-center space-x-2 truncate">
                <span class="w-3 h-3 rounded-full shrink-0" style="background-color: ${color}"></span>
                <strong class="text-xs text-stone-900 font-bold truncate">${escapeHtml(z.name || z.id)}</strong>
              </div>
              <div class="flex items-center space-x-1 shrink-0">
                <button onclick="toggleAffluenceZone(${idx})" title="${isEnabled ? 'Desactivar zona' : 'Activar zona'}" class="p-1 text-stone-400 hover:text-amber-600 rounded">
                  <i data-lucide="${isEnabled ? 'eye' : 'eye-off'}" class="w-3.5 h-3.5"></i>
                </button>
                <button onclick="centerAffluenceZoneOnMap(${idx})" title="Centrar en el mapa" class="p-1 text-stone-400 hover:text-stone-700 rounded">
                  <i data-lucide="crosshair" class="w-3.5 h-3.5"></i>
                </button>
                <button onclick="openAffluenceZoneModal(${idx})" title="Editar zona" class="p-1 text-stone-400 hover:text-amber-600 rounded">
                  <i data-lucide="edit-2" class="w-3.5 h-3.5"></i>
                </button>
                <button onclick="deleteAffluenceZone(${idx})" title="Eliminar zona" class="p-1 text-stone-400 hover:text-rose-600 rounded">
                  <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
                </button>
              </div>
            </div>
            <div class="flex flex-wrap items-center gap-1.5 text-[10px] font-mono">
              <span class="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 font-bold border border-amber-200">
                ${mult}x Empleo
              </span>
              <span class="px-1.5 py-0.5 rounded bg-blue-50 text-blue-700 font-bold border border-blue-200">
                +${reach}% Alcance
              </span>
              <span class="px-1.5 py-0.5 rounded bg-stone-100 text-stone-600 border border-stone-200">
                ${archName}
              </span>
            </div>
          </div>
        `;
      }).join('');
      lucide.createIcons();
    }

    function renderAffluenceZonesOnMap() {
      if (!affluenceZonesGroup) return;
      affluenceZonesGroup.clearLayers();
      const zones = cityData.affluence_zones || [];

      zones.forEach((z, idx) => {
        if (!z.enabled && z.enabled !== undefined) return;
        const color = z.color || '#F59E0B';
        let layer = null;

        if (z.coordinates && z.coordinates.length >= 3) {
          const latlngs = z.coordinates.map(c => [c[1], c[0]]);
          layer = L.polygon(latlngs, {
            color: color,
            weight: 2.5,
            fillColor: color,
            fillOpacity: 0.20,
            dashArray: '6, 4'
          });
        } else if (z.bbox && z.bbox.length === 4) {
          const bounds = [[z.bbox[1], z.bbox[0]], [z.bbox[3], z.bbox[2]]];
          layer = L.rectangle(bounds, {
            color: color,
            weight: 2.5,
            fillColor: color,
            fillOpacity: 0.20,
            dashArray: '6, 4'
          });
        }

        if (layer) {
          const mult = parseFloat(z.multiplier || 2.0).toFixed(1);
          const reach = Math.round(parseFloat(z.reach_bonus || 0) * 100);
          layer.bindTooltip(`
            <div class="p-1 space-y-0.5">
              <strong style="color: ${color}">★ ${escapeHtml(z.name || z.id)}</strong><br>
              <span class="text-[10px] text-stone-500 font-mono">Atracción: ${mult}x | Alcance: +${reach}%</span>
            </div>
          `, { sticky: true, className: 'poi-custom-tooltip' });

          layer.on('click', () => { openAffluenceZoneModal(idx); });
          layer.addTo(affluenceZonesGroup);
        }
      });
    }

    function renderAffluenceZonesOnDemandMap() {
      if (!affluenceZonesDemandGroup) return;
      affluenceZonesDemandGroup.clearLayers();
      const zones = cityData.affluence_zones || [];

      zones.forEach(z => {
        if (!z.enabled && z.enabled !== undefined) return;
        const color = z.color || '#F59E0B';
        let layer = null;

        if (z.coordinates && z.coordinates.length >= 3) {
          const latlngs = z.coordinates.map(c => [c[1], c[0]]);
          layer = L.polygon(latlngs, {
            color: color,
            weight: 2,
            fillColor: color,
            fillOpacity: 0.15,
            dashArray: '4, 4'
          });
        } else if (z.bbox && z.bbox.length === 4) {
          const bounds = [[z.bbox[1], z.bbox[0]], [z.bbox[3], z.bbox[2]]];
          layer = L.rectangle(bounds, {
            color: color,
            weight: 2,
            fillColor: color,
            fillOpacity: 0.15,
            dashArray: '4, 4'
          });
        }

        if (layer) {
          layer.bindTooltip(`<strong>★ ${escapeHtml(z.name || z.id)}</strong><br><span class="text-[10px] text-stone-500">Zona de Afluencia</span>`, { sticky: true });
          layer.addTo(affluenceZonesDemandGroup);
        }
      });
    }

    function updateZoneBalanceMeter() {
      const label = document.getElementById('zoneBalanceLabel');
      const bar = document.getElementById('zoneBalanceBar');
      const tip = document.getElementById('zoneBalanceTip');
      const zones = cityData.affluence_zones || [];
      const activeZones = zones.filter(z => z.enabled !== false);

      if (!label || !bar) return;

      if (activeZones.length === 0) {
        label.innerText = "0% en Zonas";
        label.className = "font-bold text-stone-500 font-mono text-[10px]";
        bar.style.width = "0%";
        bar.className = "bg-stone-300 h-1.5 rounded-full transition-all duration-300";
        if (tip) tip.innerText = "Las zonas aumentan atracción redistribuyendo la PEA de forma balanceada.";
        return;
      }

      let avgMult = activeZones.reduce((acc, z) => acc + (parseFloat(z.multiplier) || 1.0), 0) / activeZones.length;
      let estimatedShare = Math.min(85, Math.round(activeZones.length * 15 * (avgMult / 2.0)));

      label.innerText = `${estimatedShare}% en Zonas`;
      bar.style.width = `${estimatedShare}%`;

      if (estimatedShare <= 45) {
        label.className = "font-bold text-emerald-600 font-mono text-[10px]";
        bar.className = "bg-emerald-500 h-1.5 rounded-full transition-all duration-300";
        if (tip) tip.innerText = "Distribución equilibrada: los centros locales conservan masa barrial.";
      } else if (estimatedShare <= 70) {
        label.className = "font-bold text-amber-600 font-mono text-[10px]";
        bar.className = "bg-amber-500 h-1.5 rounded-full transition-all duration-300";
        if (tip) tip.innerText = "Alta concentración: gran flujo de pasajeros viajará hacia los corredores.";
      } else {
        label.className = "font-bold text-rose-600 font-mono text-[10px]";
        bar.className = "bg-rose-500 h-1.5 rounded-full transition-all duration-300";
        if (tip) tip.innerText = "Zonificación muy densa: el empleo periférico tendrá baja afluencia.";
      }
    }

    // -------------------------------------------------------------------------
    // ZONAS DE EXCLUSIÓN (EXCLUSION ZONES - CERO DEMANDA, MAPA INTACTO)
    // -------------------------------------------------------------------------
    const EXCLUSION_REASONS = {
      water_body: { name: "Cuerpo de Agua / Laguna", icon: "waves" },
      ecological_reserve: { name: "Reserva Natural / Manglar", icon: "trees" },
      unpopulated_island: { name: "Isla Excluida", icon: "palmtree" },
      military_restricted: { name: "Zona Restringida / Militar", icon: "shield-alert" },
      industrial_uninhabited: { name: "Área Inhabitada / Cantera", icon: "factory" },
      custom: { name: "Personalizado / Otro", icon: "slash" }
    };

    function getExclusionTargetMap() {
      return (exclusionDrawStep === 1 && mapBbox) ? mapBbox : mapPoi;
    }

    function setupExclusionDrawingEvents() {
      const maps = [mapBbox, mapPoi].filter(Boolean);
      maps.forEach(m => {
        m.on('click', (e) => {
          if (!isDrawingExclusion || exclusionDrawMode !== 'polygon') return;
          if (m !== getExclusionTargetMap()) return;
          addExclusionPolygonVertex(e.latlng);
        });

        m.on('dblclick', (e) => {
          if (!isDrawingExclusion || exclusionDrawMode !== 'polygon') return;
          if (m !== getExclusionTargetMap()) return;
          L.DomEvent.stop(e);
          finishExclusionPolygonDrawing();
        });

        m.on('mousedown', (e) => {
          if (!isDrawingExclusion || exclusionDrawMode !== 'bbox') return;
          if (m !== getExclusionTargetMap()) return;
          exclusionBoxStart = e.latlng;
          const targetMap = getExclusionTargetMap();
          if (exclusionTempShape && targetMap) targetMap.removeLayer(exclusionTempShape);
          exclusionTempShape = L.rectangle([exclusionBoxStart, exclusionBoxStart], {
            color: '#EF4444',
            weight: 2,
            dashArray: '6, 6',
            fillColor: '#EF4444',
            fillOpacity: 0.25,
            interactive: false,
            className: 'drawing-temp-shape'
          }).addTo(targetMap);
        });

        m.on('mousemove', (e) => {
          if (!isDrawingExclusion) return;
          if (m !== getExclusionTargetMap()) return;
          if (exclusionDrawMode === 'bbox' && exclusionBoxStart && exclusionTempShape) {
            exclusionTempShape.setBounds([exclusionBoxStart, e.latlng]);
          } else if (exclusionDrawMode === 'polygon' && exclusionDrawPoints.length > 0 && exclusionTempShape) {
            const currentPts = [...exclusionDrawPoints, e.latlng];
            exclusionTempShape.setLatLngs([currentPts]);
          }
        });

        m.on('mouseup', (e) => {
          if (!isDrawingExclusion || exclusionDrawMode !== 'bbox' || !exclusionBoxStart) return;
          if (m !== getExclusionTargetMap()) return;
          handleExclusionBoxMouseUp(e.latlng);
        });
      });

      window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isDrawingExclusion) {
          cancelDrawingExclusionZone();
        } else if (e.key === 'Enter' && isDrawingExclusion && exclusionDrawMode === 'polygon') {
          finishExclusionPolygonDrawing();
        }
      });
    }

    function handleExclusionBoxMouseUp(endLatLng) {
      if (!exclusionBoxStart) return;
      const minLon = Math.min(exclusionBoxStart.lng, endLatLng.lng);
      const minLat = Math.min(exclusionBoxStart.lat, endLatLng.lat);
      const maxLon = Math.max(exclusionBoxStart.lng, endLatLng.lng);
      const maxLat = Math.max(exclusionBoxStart.lat, endLatLng.lat);

      cancelDrawingExclusionZone();

      if (maxLon - minLon < 0.001 || maxLat - minLat < 0.001) {
        showToast("Área de exclusión muy pequeña o inválida", "warning");
        return;
      }

      const bbox = [
        parseFloat(minLon.toFixed(5)),
        parseFloat(minLat.toFixed(5)),
        parseFloat(maxLon.toFixed(5)),
        parseFloat(maxLat.toFixed(5))
      ];
      const coordinates = [
        [bbox[0], bbox[1]],
        [bbox[2], bbox[1]],
        [bbox[2], bbox[3]],
        [bbox[0], bbox[3]],
        [bbox[0], bbox[1]]
      ];

      openExclusionZoneModal(-1, {
        type: 'bbox',
        bbox: bbox,
        coordinates: coordinates,
        name: 'Nueva Zona de Exclusión',
        reason: 'water_body'
      });
    }

    function startDrawingExclusionZone(mode = 'polygon', step = 4) {
      if (!currentCityFile) {
        showToast("Selecciona o crea un proyecto primero", "warning");
        return;
      }
      isDrawingExclusion = true;
      exclusionDrawMode = mode;
      exclusionDrawStep = step;
      exclusionDrawPoints = [];
      exclusionDrawMarkers = [];
      exclusionBoxStart = null;

      const targetMap = getExclusionTargetMap();
      if (targetMap) {
        if (mode === 'bbox') targetMap.dragging.disable();
        targetMap.getContainer().style.cursor = 'crosshair';
        targetMap.getContainer().classList.add('leaflet-drawing-active');
        if (targetMap === mapBbox) setMapBboxDrawingInteractivity(true);
      }

      if (step === 1) {
        const b = document.getElementById('drawingExclusionStep1Banner');
        const txt = document.getElementById('drawingExclusionStep1BannerText');
        if (b) b.classList.remove('hidden');
        if (txt) txt.innerText = mode === 'polygon'
          ? "Haz clics en el mapa para trazar el polígono de exclusión (doble clic o Enter para cerrar)"
          : "Haz clic y arrastra sobre el mapa para trazar el cuadro de exclusión";
      } else {
        const b = document.getElementById('drawingExclusionBanner');
        const txt = document.getElementById('drawingExclusionInstructions');
        if (b) b.classList.remove('hidden');
        if (txt) txt.innerText = mode === 'polygon'
          ? "Haz clics para trazar los vértices de exclusión (doble clic o Enter para cerrar)."
          : "Haz clic y arrastra en el mapa para delimitar el cuadro de exclusión.";
      }

      lucide.createIcons();
      showToast(`Modo exclusión activo: ${mode === 'polygon' ? 'Polígono libre' : 'Cuadro'}`, "info");
    }

    function cancelDrawingExclusionZone() {
      isDrawingExclusion = false;
      exclusionBoxStart = null;
      exclusionDrawPoints = [];

      const targetMap = getExclusionTargetMap();
      if (exclusionTempShape && targetMap) {
        targetMap.removeLayer(exclusionTempShape);
        exclusionTempShape = null;
      }
      if (exclusionDrawMarkers && targetMap) {
        exclusionDrawMarkers.forEach(m => targetMap.removeLayer(m));
        exclusionDrawMarkers = [];
      }

      [mapBbox, mapPoi].filter(Boolean).forEach(m => {
        m.dragging.enable();
        m.getContainer().style.cursor = '';
        m.getContainer().classList.remove('leaflet-drawing-active');
      });
      setMapBboxDrawingInteractivity(false);

      const b1 = document.getElementById('drawingExclusionStep1Banner');
      if (b1) b1.classList.add('hidden');
      const b4 = document.getElementById('drawingExclusionBanner');
      if (b4) b4.classList.add('hidden');
    }

    function addExclusionPolygonVertex(latlng) {
      const targetMap = getExclusionTargetMap();
      if (!targetMap) return;

      if (exclusionDrawPoints.length >= 3) {
        const first = exclusionDrawPoints[0];
        const dist = targetMap.distance(first, latlng);
        if (dist < 35) {
          finishExclusionPolygonDrawing();
          return;
        }
      }

      const isFirst = (exclusionDrawPoints.length === 0);
      exclusionDrawPoints.push(latlng);

      const m = L.circleMarker(latlng, {
        radius: isFirst ? 7 : 5,
        color: '#EF4444',
        fillColor: isFirst ? '#EF4444' : '#FFFFFF',
        fillOpacity: 1,
        weight: 2,
        interactive: isFirst,
        className: isFirst ? 'drawing-handle-marker' : ''
      }).addTo(targetMap);
      exclusionDrawMarkers.push(m);

      if (isFirst) {
        m.bindTooltip("Clic para cerrar exclusión", { direction: 'top', className: 'poi-custom-tooltip' });
        m.on('click', (e) => {
          if (exclusionDrawPoints.length >= 3) {
            L.DomEvent.stopPropagation(e);
            finishExclusionPolygonDrawing();
          }
        });
      }

      if (!exclusionTempShape) {
        exclusionTempShape = L.polygon([exclusionDrawPoints], {
          color: '#EF4444',
          weight: 2,
          dashArray: '6, 6',
          fillColor: '#EF4444',
          fillOpacity: 0.2,
          interactive: false,
          className: 'drawing-temp-shape'
        }).addTo(targetMap);
      } else {
        exclusionTempShape.setLatLngs([exclusionDrawPoints]);
      }
    }

    function finishExclusionPolygonDrawing() {
      if (exclusionDrawPoints.length < 3) {
        showToast("Un polígono requiere al menos 3 vértices", "warning");
        return;
      }

      const rawCoords = exclusionDrawPoints.map(p => [
        parseFloat(p.lng.toFixed(5)),
        parseFloat(p.lat.toFixed(5))
      ]);
      if (rawCoords[0][0] !== rawCoords[rawCoords.length - 1][0] || rawCoords[0][1] !== rawCoords[rawCoords.length - 1][1]) {
        rawCoords.push([...rawCoords[0]]);
      }

      const lons = rawCoords.map(c => c[0]);
      const lats = rawCoords.map(c => c[1]);
      const bbox = [
        parseFloat(Math.min(...lons).toFixed(5)),
        parseFloat(Math.min(...lats).toFixed(5)),
        parseFloat(Math.max(...lons).toFixed(5)),
        parseFloat(Math.max(...lats).toFixed(5))
      ];

      cancelDrawingExclusionZone();

      openExclusionZoneModal(-1, {
        type: 'polygon',
        coordinates: rawCoords,
        bbox: bbox,
        name: 'Nueva Zona de Exclusión',
        reason: 'ecological_reserve'
      });
    }

    function openExclusionZoneModal(index = -1, draftData = null) {
      selectedExclusionIndex = index;
      document.getElementById('ez_edit_index').value = index;

      cityData.exclusion_zones = cityData.exclusion_zones || [];
      const z = (index >= 0 && cityData.exclusion_zones[index]) ? { ...cityData.exclusion_zones[index] } : (draftData || {});

      currentExclusionDraft = {
        id: z.id || `excl_${Date.now().toString().slice(-4)}`,
        name: z.name || "Nueva Zona de Exclusión",
        type: z.type || "polygon",
        reason: z.reason || "water_body",
        color: z.color || "#EF4444",
        enabled: z.enabled !== undefined ? z.enabled : true,
        coordinates: z.coordinates || [],
        bbox: z.bbox || null
      };

      document.getElementById('ez_name').value = currentExclusionDraft.name;
      document.getElementById('ez_id').value = currentExclusionDraft.id;
      document.getElementById('ez_reason').value = currentExclusionDraft.reason;
      document.getElementById('ez_color').value = currentExclusionDraft.color;
      document.getElementById('ez_color_hex').innerText = currentExclusionDraft.color;
      document.getElementById('ez_enabled').checked = currentExclusionDraft.enabled;

      const geomInfo = document.getElementById('ez_geometry_info');
      if (geomInfo) {
        if (currentExclusionDraft.type === 'polygon' && currentExclusionDraft.coordinates) {
          geomInfo.innerText = `Polígono (${currentExclusionDraft.coordinates.length} vértices). BBOX: [${currentExclusionDraft.bbox ? currentExclusionDraft.bbox.join(', ') : ''}]`;
        } else if (currentExclusionDraft.bbox) {
          geomInfo.innerText = `Rectángulo BBOX: [${currentExclusionDraft.bbox.join(', ')}]`;
        } else {
          geomInfo.innerText = "Geometría pendiente";
        }
      }

      document.getElementById('modalExclusionTitle').querySelector('span').innerText = index >= 0 ? "Editar Zona de Exclusión" : "Nueva Zona de Exclusión";
      document.getElementById('modalExclusionZone').classList.remove('hidden');
      lucide.createIcons();
    }

    function closeExclusionZoneModal() {
      document.getElementById('modalExclusionZone').classList.add('hidden');
      currentExclusionDraft = null;
    }

    function onEzNameInput() {
      const name = document.getElementById('ez_name').value;
      if (currentExclusionDraft && selectedExclusionIndex < 0) {
        const slug = name.trim().toLowerCase().replace(/[^a-z0-9]/g, '_').replace(/_+/g, '_').slice(0, 20);
        document.getElementById('ez_id').value = slug ? `excl_${slug}` : `excl_${Date.now().toString().slice(-4)}`;
      }
    }

    function submitExclusionZoneModal() {
      if (!currentExclusionDraft) return;

      const name = document.getElementById('ez_name').value.trim() || "Zona de Exclusión";
      const id = document.getElementById('ez_id').value.trim() || `excl_${Date.now().toString().slice(-4)}`;
      const reason = document.getElementById('ez_reason').value;
      const color = document.getElementById('ez_color').value;
      const enabled = document.getElementById('ez_enabled').checked;

      cityData.exclusion_zones = cityData.exclusion_zones || [];

      const zoneObj = {
        ...currentExclusionDraft,
        name: name,
        id: id,
        reason: reason,
        color: color,
        enabled: enabled
      };

      if (selectedExclusionIndex >= 0) {
        cityData.exclusion_zones[selectedExclusionIndex] = zoneObj;
        showToast("Zona de exclusión actualizada", "success");
      } else {
        cityData.exclusion_zones.push(zoneObj);
        showToast("Nueva zona de exclusión agregada", "success");
      }

      closeExclusionZoneModal();
      renderExclusionZones();
      triggerAutoSave();
    }

    function deleteExclusionZone(index) {
      if (!cityData.exclusion_zones || index < 0 || index >= cityData.exclusion_zones.length) return;
      const zName = cityData.exclusion_zones[index].name || "Zona";
      cityData.exclusion_zones.splice(index, 1);
      renderExclusionZones();
      triggerAutoSave();
      showToast(`Zona de exclusión '${zName}' eliminada`, "info");
    }

    function toggleExclusionZone(index) {
      if (!cityData.exclusion_zones || index < 0 || index >= cityData.exclusion_zones.length) return;
      cityData.exclusion_zones[index].enabled = !cityData.exclusion_zones[index].enabled;
      renderExclusionZones();
      triggerAutoSave();
    }

    function centerExclusionZoneOnMap(index) {
      if (!cityData.exclusion_zones || index < 0 || index >= cityData.exclusion_zones.length) return;
      const z = cityData.exclusion_zones[index];
      const targetMap = (currentStep === 1 && mapBbox) ? mapBbox : ((currentStep === 5 && mapDemand) ? mapDemand : mapPoi);
      if (!targetMap) return;

      if (z.coordinates && z.coordinates.length >= 3) {
        const bounds = L.latLngBounds(z.coordinates.map(c => [c[1], c[0]]));
        targetMap.fitBounds(bounds, { padding: [50, 50] });
      } else if (z.bbox && z.bbox.length === 4) {
        targetMap.fitBounds([[z.bbox[1], z.bbox[0]], [z.bbox[3], z.bbox[2]]], { padding: [50, 50] });
      }
    }

    function renderExclusionZones() {
      renderExclusionZonesList();
      renderExclusionZonesOnMap();
      renderExclusionZonesOnDemandMap();
    }

    function renderExclusionZonesList() {
      const container = document.getElementById('exclusionZoneListContainer');
      const badge = document.getElementById('exclusionZoneBadge');
      const countLabel = document.getElementById('exclusionZoneCountLabel');
      const step1Container = document.getElementById('step1ExclusionListContainer');
      const step1Badge = document.getElementById('step1ExclusionBadge');
      const zones = cityData.exclusion_zones || [];

      if (badge) badge.innerText = zones.length;
      if (countLabel) countLabel.innerText = `${zones.length} ${zones.length === 1 ? 'exclusión configurada' : 'exclusiones configuradas'}`;
      if (step1Badge) step1Badge.innerText = `${zones.length} ${zones.length === 1 ? 'zona' : 'zonas'}`;

      // 1. Renderizado en Paso 1 (Sidebar compacto)
      if (step1Container) {
        if (zones.length === 0) {
          step1Container.innerHTML = `
            <div class="text-center py-2.5 text-stone-400 text-[11px] border border-dashed border-stone-200 rounded-lg">
              Sin exclusiones definidas
            </div>
          `;
        } else {
          step1Container.innerHTML = zones.map((z, idx) => {
            const isEnabled = z.enabled !== false;
            const color = z.color || '#EF4444';
            return `
              <div class="p-2 bg-white border ${isEnabled ? 'border-stone-200' : 'border-dashed border-stone-200 opacity-60'} rounded-lg flex items-center justify-between text-xs hover:border-rose-300 transition">
                <div class="flex items-center space-x-2 truncate">
                  <span class="w-2.5 h-2.5 rounded-full shrink-0" style="background-color: ${color}"></span>
                  <span class="font-bold text-stone-800 text-[11px] truncate">${escapeHtml(z.name || z.id)}</span>
                </div>
                <div class="flex items-center space-x-1 shrink-0">
                  <button onclick="toggleExclusionZone(${idx})" title="${isEnabled ? 'Desactivar' : 'Activar'}" class="p-1 rounded text-stone-400 hover:text-rose-600">
                    <i data-lucide="${isEnabled ? 'eye' : 'eye-off'}" class="w-3.5 h-3.5"></i>
                  </button>
                  <button onclick="centerExclusionZoneOnMap(${idx})" title="Centrar en mapa" class="p-1 rounded text-stone-400 hover:text-stone-700">
                    <i data-lucide="crosshair" class="w-3.5 h-3.5"></i>
                  </button>
                  <button onclick="openExclusionZoneModal(${idx})" title="Editar" class="p-1 rounded text-stone-400 hover:text-stone-700">
                    <i data-lucide="edit-2" class="w-3.5 h-3.5"></i>
                  </button>
                  <button onclick="deleteExclusionZone(${idx})" title="Eliminar" class="p-1 rounded text-stone-400 hover:text-rose-600">
                    <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
                  </button>
                </div>
              </div>
            `;
          }).join('');
        }
      }

      // 2. Renderizado en Paso 4 (Pestaña Exclusiones de POI Studio)
      if (container) {
        if (zones.length === 0) {
          container.innerHTML = `
            <div class="text-center py-8 text-stone-400 border border-dashed border-stone-300 rounded-xl space-y-2">
              <i data-lucide="shield-alert" class="w-8 h-8 mx-auto text-rose-300"></i>
              <p class="text-xs font-medium text-stone-600">No hay zonas de exclusión trazadas.</p>
              <p class="text-[10px] text-stone-400 max-w-xs mx-auto">
                Usa los botones de trazado para delimitar áreas protegidas, cuerpos de agua o islas donde no deseas que se proyecte población ni empleo.
              </p>
            </div>
          `;
        } else {
          container.innerHTML = zones.map((z, idx) => {
            const isEnabled = z.enabled !== false;
            const color = z.color || '#EF4444';
            const reasonInfo = EXCLUSION_REASONS[z.reason] || { name: z.reason || "Sin Demanda", icon: "shield-alert" };

            return `
              <div class="p-3 bg-white border ${isEnabled ? 'border-stone-200' : 'border-stone-200 opacity-60 bg-stone-50'} rounded-xl shadow-xs hover:border-rose-300 transition space-y-2">
                <div class="flex items-start justify-between">
                  <div class="flex items-start space-x-2">
                    <span class="w-3 h-3 rounded-full mt-0.5 shrink-0" style="background-color: ${color}"></span>
                    <div>
                      <h4 class="text-xs font-bold text-stone-900 leading-tight">${escapeHtml(z.name || z.id)}</h4>
                      <span class="text-[10px] font-mono text-stone-500">${escapeHtml(z.id)}</span>
                    </div>
                  </div>
                  <div class="flex items-center space-x-1 shrink-0">
                    <button onclick="toggleExclusionZone(${idx})" title="${isEnabled ? 'Desactivar exclusión' : 'Activar exclusión'}" class="p-1 rounded hover:bg-stone-100 ${isEnabled ? 'text-rose-600' : 'text-stone-400'}">
                      <i data-lucide="${isEnabled ? 'eye' : 'eye-off'}" class="w-3.5 h-3.5"></i>
                    </button>
                    <button onclick="centerExclusionZoneOnMap(${idx})" title="Centrar en mapa" class="p-1 rounded hover:bg-stone-100 text-stone-600">
                      <i data-lucide="crosshair" class="w-3.5 h-3.5"></i>
                    </button>
                    <button onclick="openExclusionZoneModal(${idx})" title="Editar" class="p-1 rounded hover:bg-stone-100 text-stone-600">
                      <i data-lucide="edit-2" class="w-3.5 h-3.5"></i>
                    </button>
                    <button onclick="deleteExclusionZone(${idx})" title="Eliminar" class="p-1 rounded hover:bg-stone-100 text-stone-400 hover:text-rose-600">
                      <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
                    </button>
                  </div>
                </div>

                <div class="flex items-center justify-between text-[11px] pt-1 border-t border-stone-100">
                  <span class="text-[10px] px-1.5 py-0.5 rounded bg-rose-50 text-rose-800 border border-rose-200 font-semibold flex items-center space-x-1">
                    <i data-lucide="shield-alert" class="w-3 h-3"></i>
                    <span>${escapeHtml(reasonInfo.name)}</span>
                  </span>
                  <span class="text-[10px] font-mono text-stone-400 uppercase">${z.type === 'bbox' ? 'Cuadro BBOX' : 'Polígono'}</span>
                </div>
              </div>
            `;
          }).join('');
        }
      }

      lucide.createIcons();
    }

    function renderExclusionZonesOnMap() {
      const targetGroups = [exclusionZonesGroup, exclusionZonesBboxGroup].filter(Boolean);
      targetGroups.forEach(g => g.clearLayers());
      const zones = cityData.exclusion_zones || [];

      zones.forEach(z => {
        if (!z.enabled && z.enabled !== undefined) return;
        const color = z.color || '#EF4444';

        targetGroups.forEach(group => {
          let layer = null;
          if (z.coordinates && z.coordinates.length >= 3) {
            const latlngs = z.coordinates.map(c => [c[1], c[0]]);
            layer = L.polygon(latlngs, {
              color: color,
              weight: 2,
              fillColor: color,
              fillOpacity: 0.25,
              dashArray: '6, 6'
            });
          } else if (z.bbox && z.bbox.length === 4) {
            const bounds = [[z.bbox[1], z.bbox[0]], [z.bbox[3], z.bbox[2]]];
            layer = L.rectangle(bounds, {
              color: color,
              weight: 2,
              fillColor: color,
              fillOpacity: 0.25,
              dashArray: '6, 6'
            });
          }

          if (layer) {
            layer.bindTooltip(`<strong>🚫 ${escapeHtml(z.name || z.id)}</strong><br><span class="text-[10px] text-rose-600 font-semibold">Exclusión: Cero Demanda</span>`, { sticky: true });
            layer.addTo(group);
          }
        });
      });
    }

    function renderExclusionZonesOnDemandMap() {
      if (!exclusionZonesDemandGroup) return;
      exclusionZonesDemandGroup.clearLayers();
      const zones = cityData.exclusion_zones || [];

      zones.forEach(z => {
        if (!z.enabled && z.enabled !== undefined) return;
        const color = z.color || '#EF4444';
        let layer = null;

        if (z.coordinates && z.coordinates.length >= 3) {
          const latlngs = z.coordinates.map(c => [c[1], c[0]]);
          layer = L.polygon(latlngs, {
            color: color,
            weight: 2,
            fillColor: color,
            fillOpacity: 0.20,
            dashArray: '6, 6'
          });
        } else if (z.bbox && z.bbox.length === 4) {
          const bounds = [[z.bbox[1], z.bbox[0]], [z.bbox[3], z.bbox[2]]];
          layer = L.rectangle(bounds, {
            color: color,
            weight: 2,
            fillColor: color,
            fillOpacity: 0.20,
            dashArray: '6, 6'
          });
        }

        if (layer) {
          layer.bindTooltip(`<strong>🚫 ${escapeHtml(z.name || z.id)}</strong><br><span class="text-[10px] text-stone-500">Zona de Exclusión</span>`, { sticky: true });
          layer.addTo(exclusionZonesDemandGroup);
        }
      });
    }

    // -------------------------------------------------------------------------
    // API CITIES Y CARGA DE DATOS
    // -------------------------------------------------------------------------
    async function fetchCitiesList() {
        const json = await fetchStartupJson('/api/cities', 'Lista de ciudades');
        const select = document.getElementById('citySelect');
        // The project modal replaced the old city select in the current layout.
        if (!select) return json;
        select.innerHTML = '';
        (json.cities || []).forEach(c => {
          const opt = document.createElement('option');
          opt.value = c.path;
          opt.textContent = `${c.name} (${c.code})`;
          if (c.path === currentCityFile) opt.selected = true;
          select.appendChild(opt);
        });
        return json;
    }

    async function onCitySelected(filePath) {
      if (conapoCommitPromise) await conapoCommitPromise;
      await flushPendingAutoSave();
      currentCityFile = filePath;
      await loadCityData(filePath);
    }

    function resetProjectSession() {
      activeZoneClustersData = null;
      activeZoneClustersContext = null;
      zoneCalculationVersion++;
      cancelConapoRequest();
      cancelAutoUrbanCore();
      cancelToponymyScan();
      densityPoints = [];
      densityLoadedCityFile = null;
      if (mapPoi) {
        if (densityLayers.jobs) {
          mapPoi.removeLayer(densityLayers.jobs);
          densityLayers.jobs = null;
        }
        if (densityLayers.pop) {
          mapPoi.removeLayer(densityLayers.pop);
          densityLayers.pop = null;
        }
        if (editorPreviewLayer) {
          editorPreviewLayer.clearLayers();
        }
        if (boxSelectionLayer) {
          mapPoi.removeLayer(boxSelectionLayer);
          boxSelectionLayer = null;
        }
        if (bboxPoiGroup) {
          bboxPoiGroup.clearLayers();
        }
        if (placesMarkersGroup) {
          placesMarkersGroup.clearLayers();
        }
        if (nativeOsmMarkersGroup) {
          nativeOsmMarkersGroup.clearLayers();
        }
        isNativeOsmPreviewOn = false;
        selectedPlaceIndices.clear();
      }
      if (isEditingUrbanCore) stopEditingUrbanCore(false);
      if (isDrawingUrbanCore) cancelDrawingUrbanCore();
      if (urbanCoreHandlesGroup) urbanCoreHandlesGroup.clearLayers();
      if (urbanCoreReachPreviewCircle && mapBbox) {
        mapBbox.removeLayer(urbanCoreReachPreviewCircle);
        urbanCoreReachPreviewCircle = null;
      }
      if (urbanCoreGroup) urbanCoreGroup.clearLayers();
      if (urbanCoreLayer && mapBbox) {
        mapBbox.removeLayer(urbanCoreLayer);
        urbanCoreLayer = null;
      }
      isBboxLocked = false;
      const lockChk = document.getElementById('cfg_bbox_locked');
      if (lockChk) lockChk.checked = false;
      const lockIcon = document.getElementById('bboxLockIcon');
      if (lockIcon) {
        lockIcon.setAttribute('data-lucide', 'unlock');
        lockIcon.className = 'w-3 h-3 text-stone-400';
      }
      const lockLabel = document.getElementById('bboxLockLabel');
      if (lockLabel) {
        lockLabel.innerText = "Bloquear BBOX";
        lockLabel.className = "text-stone-700 font-bold";
      }
      ['cfg_bbox_0', 'cfg_bbox_1', 'cfg_bbox_2', 'cfg_bbox_3'].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
          el.disabled = false;
          el.classList.remove('bg-stone-100', 'text-stone-500', 'cursor-not-allowed');
        }
      });
      const presetSel = document.getElementById('cfg_bbox_preset');
      if (presetSel) {
        presetSel.value = 'custom';
        presetSel.disabled = false;
        presetSel.classList.remove('bg-stone-100', 'text-stone-500', 'cursor-not-allowed');
      }

      document.getElementById('btnToggleDensityJobs')?.classList.remove('bg-rose-500/30', 'border-rose-500');
      document.getElementById('btnToggleDensityPop')?.classList.remove('bg-blue-500/30', 'border-blue-500');
      document.getElementById('btnToggleBoxSelect')?.classList.remove('bg-amber-600/30', 'border-amber-600');
      isBoxSelectActive = false;

      if (demandLayers && demandLayers.residents) demandLayers.residents.clearLayers();
      if (demandLayers && demandLayers.jobs) demandLayers.jobs.clearLayers();
      if (demandLayers && demandLayers.pois) demandLayers.pois.clearLayers();
      if (isolatedZonesGroup) isolatedZonesGroup.clearLayers();
      if (isolatedHandlesGroup) isolatedHandlesGroup.clearLayers();
      if (isolatedZonesDemandGroup) isolatedZonesDemandGroup.clearLayers();
      if (exclusionZonesGroup) exclusionZonesGroup.clearLayers();
      if (exclusionZonesBboxGroup) exclusionZonesBboxGroup.clearLayers();
      if (exclusionZonesDemandGroup) exclusionZonesDemandGroup.clearLayers();
      if (zoomPreviewGroup) zoomPreviewGroup.clearLayers();
      const showPreviewChk = document.getElementById('cfg_show_zoom_preview');
      if (showPreviewChk) showPreviewChk.checked = true;
      selectedIsolatedZoneIndex = -1;
      selectedExclusionIndex = -1;
      isDrawingIsolated = false;
      isDrawingExclusion = false;
      cancelDrawingExclusionZone();
      hideDraftBanner();
      const metricPea = document.getElementById('metricTotalPea');
      const metricPts = document.getElementById('metricDemandPoints');
      if (metricPea) metricPea.innerText = "0";
      if (metricPts) metricPts.innerText = "0";

      window.conapoMetadata = null;
      window.conapoYear = null;
      window.conapoAvailableYears = null;
      updateConapoYearSelect([], null);
      setConapoStatus('Consulta los años disponibles del archivo CONAPO.');

      activeEditingPoiIndex = -1;
      currentPoiFilter = 'ALL';
      const searchInput = document.getElementById('poiSearchInput');
      if (searchInput) searchInput.value = '';
      document.querySelectorAll('.poi-filter-chip').forEach(el => {
        el.className = "poi-filter-chip px-2 py-0.5 rounded bg-metro-panel text-metro-muted hover:text-metro-text transition";
      });
      const activeFilterBtn = document.getElementById('poiFilter-ALL');
      if (activeFilterBtn) activeFilterBtn.className = "poi-filter-chip px-2 py-0.5 rounded bg-metro-orange text-white transition";
    }

    async function loadCityData(filePath) {
      if (conapoCommitPromise) await conapoCommitPromise;
      const version = ++cityLoadVersion;
      if (cityLoadController) cityLoadController.abort();
      const controller = new AbortController();
      cityLoadController = controller;
      currentCityFile = filePath;
      wizardLifecycle.lock('Cargando configuración del proyecto…');
      wizardLifecycle.setRetry(() => loadCityData(filePath));
      resetProjectSession();
      try {
        const data = await fetchStartupJson(`/api/city?file=${encodeURIComponent(filePath)}`, 'Ciudad', 10000, controller.signal);
        if (version !== cityLoadVersion || filePath !== currentCityFile) return false;
        if (!data.city || typeof data.city !== 'object' || Array.isArray(data.city)) throw new Error('Configuración de ciudad inválida');
        cityData = data;

        // Verificar si existe un borrador de emergencia en localStorage (sin sobreescribir silenciosamente)
        try {
          const draftStr = localStorage.getItem(`sb_draft_${filePath}`);
          if (draftStr) {
            const draft = JSON.parse(draftStr);
            if (draft && draft.data) {
              const draftAgeMinutes = Math.max(0, Math.round((Date.now() - (draft.savedAt || Date.now())) / 60000));
              const banner = document.getElementById('draftRestoreBanner');
              const ageText = document.getElementById('draftAgeText');
              if (banner) {
                if (ageText) ageText.innerText = draftAgeMinutes < 2 ? "hace un momento" : `hace ${draftAgeMinutes} min`;
                banner.classList.remove('hidden');
                window._pendingDraftData = draft.data;
                window._pendingDraftPath = filePath;
              }
            }
          }
        } catch (err) {
          console.warn("No se pudo verificar localStorage:", err);
        }

        populateFormFields();
        const headerLabel = document.getElementById('headerProjectLabel');
        if (headerLabel && cityData.city) {
          headerLabel.textContent = `${cityData.city.name || 'Proyecto'} (${cityData.city.code || ''})`;
        }
        wizardLifecycle.ready(filePath);
        wizardLifecycle.clearWarning('sources');
        refreshDataStatus();
        showToast("Configuración lista", "success");
        return true;
      } catch (e) {
        if (version !== cityLoadVersion || filePath !== currentCityFile) return false;
        wizardLifecycle.fail(`No se pudo cargar el proyecto: ${e.message}`);
        showToast(e.message, "error");
        return false;
      }
    }

    function restorePendingDraft() {
      if (window._pendingDraftData) {
        cityData = window._pendingDraftData;
        populateFormFields();
        hideDraftBanner();
        showToast("⚡ Borrador local restaurado con éxito", "success");
      }
    }

    function discardPendingDraft() {
      if (window._pendingDraftPath) {
        try {
          localStorage.removeItem(`sb_draft_${window._pendingDraftPath}`);
        } catch (e) {}
      }
      hideDraftBanner();
      showToast("Borrador local descartado. Se mantiene la versión del disco.", "info");
    }

    function hideDraftBanner() {
      const banner = document.getElementById('draftRestoreBanner');
      if (banner) banner.classList.add('hidden');
      window._pendingDraftData = null;
      window._pendingDraftPath = null;
    }

    function syncActivePoiFromEditor(allowNew = false) {
      if (!cityData) cityData = { city: {}, macroeconomics: {}, pois: [], places: [], isolated_zones: [], exclusion_zones: [] };
      if (!cityData.pois) cityData.pois = [];

      const baseInput = document.getElementById('editPoiBaseName');
      if (!baseInput) return false;
      const baseName = baseInput.value.trim();
      if (!baseName) return false;

      const pCat = document.getElementById('editPoiCategory')?.value || 'AIR';
      const meta = (typeof TAXONOMY_META !== 'undefined' && TAXONOMY_META[pCat]) ? TAXONOMY_META[pCat] : (typeof TAXONOMY_META !== 'undefined' && TAXONOMY_META.AIR ? TAXONOMY_META.AIR : { prefix: 'AIR_' });
      const pId = meta.prefix ? `${meta.prefix}${baseName}` : baseName;
      const esName = (document.getElementById('editPoiNameEs')?.value || '').trim();
      const enName = (document.getElementById('editPoiNameEn')?.value || '').trim();
      const pMode = document.getElementById('editPoiMode')?.value || 'MAX';
      const rawJobs = parseInt(document.getElementById('editPoiJobs')?.value, 10);
      const pJobs = isNaN(rawJobs) ? 0 : Math.max(0, rawJobs);
      const rawRad = parseInt(document.getElementById('editPoiRadiusNum')?.value || document.getElementById('editPoiRadius')?.value, 10);
      const pRad = isNaN(rawRad) ? 100 : Math.max(50, rawRad);
      const lon = parseFloat(document.getElementById('editPoiLon')?.value);
      const lat = parseFloat(document.getElementById('editPoiLat')?.value);

      if (isNaN(lon) || isNaN(lat)) return false;

      let nameVal = esName || baseName;
      if (enName) {
        nameVal = { es: esName || baseName, en: enName };
      }

      const poiObj = {
        id: pId,
        name: nameVal,
        type: pCat.toLowerCase(),
        mode: pMode,
        jobs: pJobs,
        radius_m: pRad,
        loc: [lon, lat]
      };

      if (activeEditingPoiIndex >= 0 && activeEditingPoiIndex < cityData.pois.length) {
        const isDup = cityData.pois.some((p, i) => i !== activeEditingPoiIndex && p.id === pId);
        if (!isDup) {
          cityData.pois[activeEditingPoiIndex] = poiObj;
          return true;
        }
      } else if (allowNew && activeEditingPoiIndex === -1) {
        const isDup = cityData.pois.some(p => p.id === pId);
        if (!isDup) {
          cityData.pois.push(poiObj);
          activeEditingPoiIndex = cityData.pois.length - 1;
          return true;
        }
      }
      return false;
    }

    function updateDemandMethodsSummary(revealCompatibility = false) {
      const placement = document.getElementById('cfg_residential_placement')?.value || 'official_blocks';
      const residential = document.getElementById('cfg_residential_employment')?.value || 'census_employed';
      const workplace = document.getElementById('cfg_workplace_employment')?.value || 'auto';
      const labels = {
        placement: {official_blocks:'Manzanas oficiales INEGI', legacy:'Ubicación residencial anterior'},
        residential: {census_employed:'Ocupados del Censo', legacy:'Tasa de participación anterior'},
        workplace: {auto:'Empleo automático CE / DENUE', legacy:'Empleo con ajuste TIL1 anterior',
          ce_bounded:'DENUE acotado, selección manual', historical_transfer:'Transferencia CE manual'}
      };
      const summary = document.getElementById('demandMethodsSummary');
      if (document.getElementById('cfg_demographic_reference')?.value === 'eic2025') labels.residential.census_employed = 'EIC 2025: población, ocupados y viajeros';
      if (summary) summary.textContent = [labels.placement[placement] || placement,
        labels.residential[residential] || residential, labels.workplace[workplace] || workplace].join(' · ');
      const usesCompatibility = placement !== 'official_blocks' || residential !== 'census_employed' || workplace !== 'auto';
      const notice = document.getElementById('demandCompatibilityNotice');
      if (notice) {
        notice.hidden = !usesCompatibility;
        notice.textContent = usesCompatibility ? 'Este proyecto conserva métodos anteriores o una selección manual. Puedes revisarlos en Compatibilidad; cargarlo no cambia su configuración.' : '';
      }
      const panel = document.getElementById('demandCompatibility');
      if (panel && revealCompatibility) panel.open = usesCompatibility;
    }

    let historicalTransferInspection = null;

    function renderHistoricalTransferGroups(report) {
      const container = document.getElementById('historicalTransferGroups');
      if (!container) return;
      container.replaceChildren();
      const groups = new Map();
      for (const row of report.transfer_preflight?.controls || []) {
        const key = `${row.municipality}/${row.activity_code}`;
        if (!groups.has(key)) groups.set(key, {row, available:0, fallback:0});
        const item = groups.get(key);
        if (row.status === 'TRANSFERRED_HISTORICAL_MEAN') item.available++;
        else item.fallback++;
      }
      const selected = cityData.macroeconomics?.historical_workplace_transfer?.groups || [];
      const evidenceInput = document.getElementById('cfg_ce_unit_evidence');
      if (evidenceInput && !evidenceInput.value.trim()) evidenceInput.value = 'INEGI CE2024 Metodología, tabla 3, páginas PDF 20–21: establecimientos en manufactura, comercio y servicios seleccionados. https://www.inegi.org.mx/contenidos/programas/ce/2024/doc/889463925644.pdf';
      for (const [key, item] of groups) {
        const label = document.createElement('label');
        label.style.display = 'block';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox'; checkbox.name = 'historicalTransferGroup'; checkbox.value = key;
        checkbox.disabled = item.available === 0;
        checkbox.checked = selected.some(g => g.municipality === item.row.municipality && JSON.stringify(g.scian_prefixes) === JSON.stringify(item.row.scian_prefixes));
        label.append(checkbox, document.createTextNode(` ${key}: ${item.available} tamaños transferibles, ${item.fallback} sin transferencia`));
        container.append(label);
      }
    }

    function activateHistoricalTransfer() {
      const label = document.getElementById('historicalBenchmarkStatus');
      try {
        const inspected = historicalTransferInspection;
        const benchmark = cityData.macroeconomics?.historical_workplace_benchmark;
        if (!inspected || inspected.file !== currentCityFile || inspected.contract !== JSON.stringify(benchmark)) throw new Error('Inspecciona las fuentes de esta ciudad primero.');
        if (Number(document.getElementById('cfg_ce_reference_year').value) !== benchmark.reference_year || document.getElementById('cfg_ce_sources').value.split(/\r?\n/).map(p=>p.trim()).filter(Boolean).join('\n') !== benchmark.ce_sources.join('\n')) throw new Error('El año o las fuentes cambiaron; inspecciona de nuevo.');
        const evidence = document.getElementById('cfg_ce_unit_evidence').value.trim();
        if (!evidence) throw new Error('Indica la evidencia de unidad de observación para los sectores elegidos.');
        const keys = new Set(Array.from(document.querySelectorAll('input[name="historicalTransferGroup"]:checked')).filter(el=>!el.disabled).map(el=>el.value));
        const groups = inspected.report.groups.filter(g=>keys.has(`${g.municipality}/${g.activity_code}`)).map(g=>({municipality:g.municipality,scian_prefixes:g.scian_prefixes,reporting_unit:'establishment',reporting_unit_evidence:evidence}));
        if (!groups.length) throw new Error('Selecciona al menos un grupo transferible.');
        cityData.macroeconomics.historical_workplace_transfer = {...benchmark, role:'historical_transfer',enabled:true,formula_version:'municipal_sector_size_mean_v1',strength:Number(document.getElementById('cfg_ce_transfer_strength').value),groups,
          transfer_assumption:'Promedio histórico CE por municipio, sector y tamaño aplicado a ubicaciones DENUE actuales; propiedad, cobertura y edición condicionales. Sin ajuste a totales CE.'};
        cityData.macroeconomics.workplace_employment = 'historical_transfer';
        document.getElementById('cfg_workplace_employment').value = 'historical_transfer';
        updateDemandMethodsSummary();
        updateWorkplaceSourceStatus();
        densityLoadedCityFile = null;
        label.textContent = `Transferencia histórica ${benchmark.reference_year} activada para ${groups.length} grupos. Revisa la vista previa antes de compilar.`;
        triggerAutoSave();
      } catch (error) { label.textContent = error.message; }
    }

    async function reinspectHistoricalSources() {
      if (cityData.macroeconomics?.historical_workplace_benchmark) delete cityData.macroeconomics.historical_workplace_benchmark.source_sha256;
      delete cityData.macroeconomics?.historical_workplace_transfer;
      if (cityData.macroeconomics?.workplace_employment === 'historical_transfer') {
        cityData.macroeconomics.workplace_employment = 'auto';
        document.getElementById('cfg_workplace_employment').value = 'auto';
      }
      historicalTransferInspection = null;
      updateDemandMethodsSummary();
      densityLoadedCityFile = null;
      updateWorkplaceSourceStatus();
      triggerAutoSave();
      await inspectWorkplaceBenchmark();
    }

    async function inspectWorkplaceBenchmark() {
      const label = document.getElementById('historicalBenchmarkStatus');
      const file = currentCityFile;
      const year = Number(document.getElementById('cfg_ce_reference_year').value);
      const sources = document.getElementById('cfg_ce_sources').value.split(/\r?\n/).map(p => p.trim()).filter(Boolean);
      const existing = cityData.macroeconomics?.historical_workplace_benchmark;
      const contract = {
        ...(existing?.role === 'historical_benchmark' ? existing : {}),
        schema_version: 1, role: 'historical_benchmark', enabled: false,
        reference_year: year, ce_sources: sources,
        denue_edition: existing?.role === 'historical_benchmark' ? existing.denue_edition : {
          label: 'unknown', reference_year: null, evidence: 'Edición local no acreditada; fecha_alta no identifica la edición.'
        },
        scope_rule_version: 'saic_private_paraestatal_v1',
        coverage_evidence: 'Inspección por clases de actividad; propiedad institucional y cobertura completa no verificadas.',
        transfer_assumption: 'Referencia histórica para comparar; no se transfieren pesos ni se afirma empleo actual.',
        groups: existing?.role === 'historical_benchmark' ? existing.groups || [] : []
      };
      const requestState = JSON.stringify(cityData);
      label.textContent = 'Inspeccionando fuentes…';
      try {
        if (!Number.isInteger(year) || !sources.length) throw new Error('Selecciona el año y al menos un archivo CE.');
        const response = await fetch('/api/workplace/inspect', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({file, contract})});
        const result = await response.json();
        if (file !== currentCityFile || requestState !== JSON.stringify(cityData) || year !== Number(document.getElementById('cfg_ce_reference_year').value) || sources.join('\n') !== document.getElementById('cfg_ce_sources').value.split(/\r?\n/).map(p=>p.trim()).filter(Boolean).join('\n')) return;
        if (!response.ok) throw new Error(result.error || result.message || 'No se pudo inspeccionar la referencia CE.');
        const r = result.report;
        if (r.source_binding === 'mismatch') throw new Error('Las fuentes cambiaron desde la inspección vinculada. Revisa la referencia antes de continuar.');
        cityData.macroeconomics ||= {};
        cityData.macroeconomics.historical_workplace_benchmark = result.contract;
        historicalTransferInspection = {file, contract:JSON.stringify(result.contract), report:r};
        if (typeof renderHistoricalTransferGroups === 'function') renderHistoricalTransferGroups(r);
        label.textContent = `${r.reference_year}: ${r.publication_counts.published} controles publicados, ${r.publication_counts.suppressed} reservados. ${r.scope_counts.excluded} establecimientos fuera del alcance CE y ${r.scope_counts.review} por revisar. Edición DENUE: ${r.denue_edition.label || 'desconocida'}. Referencia inactiva; pesos sin cambios.`;
        document.getElementById('historicalBenchmarkGroups').textContent = r.groups.map(g => `${g.municipality} · ${g.activity_code}: ${g.status === 'UNAVAILABLE' ? 'sin control utilizable' : 'referencia histórica condicional'}; ${g.remaining_records} ubicaciones, ${g.review_records} por revisar${g.hard_fit_within_bounds === false ? '; total fuera de los intervalos DENUE' : ''}`).join('\n');
        triggerAutoSave();
      } catch (error) {
        if (file === currentCityFile) label.textContent = error.message;
      }
    }

    function workplaceSourceStatus(report) {
      const mode = report?.mode || cityData.macroeconomics?.workplace_employment || 'auto';
      if (mode === 'auto') return 'Selección AUTOMÁTICA: inspección de fuentes pendiente. Se mostrará si hay transferencia CE o si falta detalle municipal por sector y tamaño.';
      if (mode === 'legacy') return 'Método anterior seleccionado: correcciones de empleo desactivadas.';
      if (mode === 'historical_fine_transfer') {
        const coverage = report.coverage.bbox;
        return `CE histórico ${report.reference_year}: ${(100 * coverage.attraction_share).toFixed(2)}% del peso laboral dentro del BBOX con transferencia al nivel SCIAN compatible más fino; ${coverage.establishments - coverage.transferred_establishments} establecimientos conservan estimaciones DENUE. Antes del recorte urbano y POIs. Motivos de respaldo, universo completo: ${JSON.stringify(report.fallback_reasons || {})}. No son empleos actuales observados.`;
      }
      if (mode === 'historical_transfer') {
        if (!report || report.transferred_establishments == null) return 'Transferencia histórica CE ACTIVADA por las fuentes seleccionadas. La compilación verificará las fuentes y mostrará cuántos establecimientos conservan estimaciones DENUE.';
        const reasons = {};
        for (const row of report.controls || []) {
          if (row.status !== 'TRANSFERRED_HISTORICAL_MEAN') reasons[row.status] = (reasons[row.status] || 0) + 1;
        }
        return `CE histórico ${report.reference_year}: ${report.transferred_establishments} establecimientos transferidos; ATENCIÓN: ${report.fallback_establishments} conservan estimaciones DENUE. Totales antes del BBOX. Sectores fuera del alcance, registros excluidos/en revisión y celdas CE no utilizables conservan DENUE. Grupos por causa: ${JSON.stringify(reasons)}. Edición DENUE: ${report.denue_edition?.label || 'desconocida'}. No son empleos actuales observados.`;
      }
      const fitted = (report?.controls || []).filter(row => row.status === 'FITTED_DECLARED_COMPARABLE').length;
      return `ATENCIÓN: transferencia histórica CE DESACTIVADA. Se usan estimaciones DENUE acotadas. ${report?.automatic_selection?.reason || ''} ${report ? `${fitted} controles CE comparables ajustados. Motivos: ${(report.control_gate_reasons || []).join('; ') || 'Consultar informe de controles'}.` : 'No hay transferencia histórica configurada para este mapa.'}`;
    }

    function updateWorkplaceSourceStatus(report) {
      const message = workplaceSourceStatus(report);
      for (const id of ['workplaceCoverage', 'buildWorkplaceSourceStatus']) {
        const label = document.getElementById(id);
        if (label) label.textContent = message;
      }
    }

    async function refreshWorkplaceSourceStatus(strict = false) {
      const requestedFile = currentCityFile;
      const mode = cityData.macroeconomics?.workplace_employment || 'auto';
      try {
        const response = await fetch('/api/workplace/status', {method:'POST',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({file:requestedFile})});
        const report = await response.json();
        if (!response.ok) throw new Error(report.error || 'No se pudo inspeccionar CE');
        if (requestedFile !== currentCityFile || mode !== (cityData.macroeconomics?.workplace_employment || 'auto') || (report.requested_mode && report.requested_mode !== mode)) {
          if (strict) throw new Error('El proyecto o método cambió durante la inspección CE.');
          return;
        }
        updateWorkplaceSourceStatus(report);
        return report;
      } catch (error) {
        if (requestedFile === currentCityFile) {
          for (const id of ['workplaceCoverage','buildWorkplaceSourceStatus']) {
            const label = document.getElementById(id);
            if (label) label.textContent = `ATENCIÓN: fuentes CE sin verificar: ${error.message}`;
          }
        }
        if (strict) throw error;
      }
    }

    async function previewDemandV2() {
      const file = currentCityFile;
      const label = document.getElementById('demandV2Summary');
      syncStateFromInputs();
      const state = JSON.stringify(cityData);
      label.textContent = 'Evaluando candidato…';
      try {
        if (!await saveCurrentCity(true)) throw new Error('No se pudo guardar la configuración para comparar.');
        if (file !== currentCityFile || state !== JSON.stringify(cityData)) return;
        const previewUrl = `/api/demand-v2-preview?file=${encodeURIComponent(file)}&stage=allocation&async=1`;
        let response = await fetch(previewUrl);
        let result = await response.json();
        while (response.ok && result.status === 'running') {
          if (file !== currentCityFile || state !== JSON.stringify(cityData)) return;
          label.textContent = 'Evaluando fuentes y demanda del candidato…';
          await new Promise(resolve => setTimeout(resolve, 2000));
          if (file !== currentCityFile || state !== JSON.stringify(cityData)) return;
          response = await fetch(`${previewUrl}&job=${encodeURIComponent(result.job_id)}`);
          result = await response.json();
        }
        if (file !== currentCityFile || state !== JSON.stringify(cityData)) return;
        if (!response.ok) throw new Error(result.error || result.message || 'No se pudo evaluar el candidato.');
        if (result.status === 'error') throw new Error(result.error || 'No se pudo evaluar el candidato.');
        const territory = result.report.territory;
        const allocation = result.report.allocation;
        window.demandV2CohortMetadata = {file,state,commuters:territory.integer_commuters,report:allocation.cohorts};
        updateCohortTelemetry();
        const score = value => Number.isFinite(value?.conditional_kl) ? value.conditional_kl.toFixed(4) : 'sin observaciones comparables';
        const quotas = allocation.poi_quotas || [];
        const shortfall = quotas.reduce((sum, quota) => sum + (quota.shortfall || 0), 0);
        const coverage = result.report.workplaces?.coverage?.bbox;
        const employment = coverage ? `\nEmpleo dentro de la caja, antes de recortes y POIs: ${(100 * coverage.attraction_share).toFixed(2)}% del peso con transferencia histórica CE; el resto conserva estimaciones DENUE.` : '';
        label.textContent = `Candidato ${result.report.target_year ?? cityData.demand?.target_year ?? 2025} · ${result.points.length} puntos · ${territory.integer_commuters.toLocaleString()} viajeros · ${allocation.cohorts?.actual_count ?? 0} cohortes\nBeta: ${allocation.beta} (${allocation.beta_basis})\nError de proporciones municipales (menor es mejor): continuo ${score(allocation.validation)} · enteros ${score(allocation.validation_integer)}\nPOIs: ${quotas.length} cuotas después de captura DENUE; faltan ${shortfall.toLocaleString()} viajeros.${employment}\nMotor activo: ${cityData.demand?.engine || 'actual'}.`;
      } catch (error) {
        if (file === currentCityFile) label.textContent = `Candidato pendiente: ${error.message}`;
      }
    }

    function selectedDemandEngine() {
      const toggle = document.getElementById('cfg_demand_engine');
      return toggle ? (toggle.checked ? 'v2' : 'legacy') : (cityData.demand?.engine || 'legacy');
    }

    function updateDemandEngineControls(sources) {
      const active = selectedDemandEngine() === 'v2';
      for (const id of ['cfg_residential_placement', 'cfg_residential_employment', 'cfg_workplace_employment', 'cfg_demographic_reference', 'conapoYearSelect', 'btnAutoConapo', 'btnConapoYears', 'btnReplaceConapo']) {
        const control = document.getElementById(id);
        if (control) control.disabled = active;
      }
      const status = document.getElementById('demandEngineStatus');
      if (status) status.textContent = active
        ? 'Activado: nuevo motor · EIC 2025 · manzanas oficiales · empleo automático. Las fuentes se comprueban en Fuentes.'
        : 'Desactivado: motor legado.';
      if (status && active && sources) {
        const labels = {denue:'DENUE', cpv:'Censo 2020', marco:'Marco Geoestadístico', eic:'EIC 2025'};
        const missing = Object.keys(labels).filter(key => sources[key]?.status !== 'ok');
        status.textContent += missing.length
          ? ` Faltan fuentes utilizables: ${missing.map(key => labels[key]).join(', ')}. Ve a Fuentes y usa Preparar descargas.`
          : ' Fuentes necesarias vinculadas; los datos se validarán al compilar.';
      }
    }

    function syncDemandEngineFields() {
      const macro = cityData.macroeconomics || {};
      for (const [id, value] of Object.entries({cfg_residential_placement: cityData.city?.residential_placement || 'official_blocks',
        cfg_residential_employment: macro.residential_employment || 'census_employed',
        cfg_workplace_employment: macro.workplace_employment || 'auto',
        cfg_demographic_reference: macro.demographic_reference?.mode || 'projected',
        cfg_eic_indicators: macro.demographic_reference?.indicators || '',
        cfg_eic_persons: (macro.demographic_reference?.persons || []).join('\n')})) {
        const control = document.getElementById(id);
        if (control) control.value = value;
      }
      const year = macro.projection_year || macro.target_year;
      const yearSelect = document.getElementById('conapoYearSelect');
      if (year && yearSelect) {
        if (![...yearSelect.options].some(option => String(option.value) === String(year))) {
          const option = document.createElement('option'); option.value = year; option.textContent = year;
          yearSelect.appendChild(option);
        }
        yearSelect.value = year;
      }
      updateDemandEngineControls();
    }

    async function onDemandEngineChange() {
      if (!currentCityFile || !wizardLifecycle.canSave(currentCityFile)) {
        document.getElementById('cfg_demand_engine').checked = cityData.demand?.engine === 'v2';
        return;
      }
      const active = selectedDemandEngine() === 'v2';
      cancelConapoRequest();
      const demand = cityData.demand ||= {};
      const macro = cityData.macroeconomics ||= {};
      const fields = ['residential_employment', 'workplace_employment', 'demographic_reference', 'projection_year', 'target_year'];
      if (active) {
        if (!demand.wizard_legacy_settings) demand.wizard_legacy_settings = JSON.parse(JSON.stringify({
          residential_placement: cityData.city.residential_placement,
          macro: Object.fromEntries(fields.filter(key => key in macro).map(key => [key, macro[key]])),
          eic_sources: Object.fromEntries(['eic_indicators', 'eic_persons'].filter(key => key in (demand.sources || {})).map(key => [key, demand.sources[key]])),
        }));
        demand.engine = 'v2'; demand.target_year = 2025; demand.boundary_policy = 'closed';
        const sources = demand.sources || {};
        const previous = macro.demographic_reference;
        macro.demographic_reference = {mode:'eic2025',
          previous_projection_year: previous?.previous_projection_year || macro.projection_year || macro.target_year || 2026,
          indicators: previous?.indicators || sources.eic_indicators?.[0] || '',
          persons: previous?.persons?.length ? [...previous.persons] : [...(sources.eic_persons || [])]};
        if (!macro.demographic_reference.indicators || !macro.demographic_reference.persons.length) macro.demographic_reference.pending_download = true;
        if (demand.sources) { delete demand.sources.eic_indicators; delete demand.sources.eic_persons; }
        cityData.city.residential_placement = 'official_blocks';
        macro.residential_employment = 'census_employed'; macro.workplace_employment = 'auto';
        macro.projection_year = 2025; delete macro.target_year;
      } else {
        const previous = demand.wizard_legacy_settings;
        if (previous) {
          if (previous.residential_placement === undefined) delete cityData.city.residential_placement;
          else cityData.city.residential_placement = previous.residential_placement;
          for (const key of fields) {
            if (key in previous.macro) macro[key] = JSON.parse(JSON.stringify(previous.macro[key]));
            else delete macro[key];
          }
          if (previous.eic_sources && Object.keys(previous.eic_sources).length) Object.assign(demand.sources ||= {}, previous.eic_sources);
          delete demand.wizard_legacy_settings;
        }
        demand.engine = 'legacy';
      }
      syncDemandEngineFields();
      updateCohortTelemetry();
      triggerAutoSave();
      const file = currentCityFile;
      if (await saveCurrentCity(true)) {
        if (file === currentCityFile && selectedDemandEngine() === (active ? 'v2' : 'legacy')) refreshDataStatus();
      } else if (file === currentCityFile) {
        document.getElementById('demandEngineStatus').textContent = 'No se pudo guardar el motor. Reintenta el guardado antes de compilar.';
      }
    }

    function syncStateFromInputs() {
      if (!cityData) cityData = { city: {}, macroeconomics: {}, pois: [], places: [], isolated_zones: [], exclusion_zones: [] };
      if (!cityData.city) cityData.city = {};
      if (!cityData.macroeconomics) cityData.macroeconomics = {};
      if (!cityData.isolated_zones) cityData.isolated_zones = [];
      if (!cityData.exclusion_zones) cityData.exclusion_zones = [];
      const demandEngine = selectedDemandEngine();
      if (demandEngine === 'v2') cityData.demand = {...(cityData.demand || {}), engine:'v2', target_year:2025, boundary_policy:'closed'};
      else if (demandEngine === 'legacy' && cityData.demand) cityData.demand.engine = 'legacy';
      if (demandEngine === 'v2') {
        document.getElementById('cfg_residential_placement').value = 'official_blocks';
        document.getElementById('cfg_residential_employment').value = 'census_employed';
        document.getElementById('cfg_workplace_employment').value = 'auto';
        document.getElementById('cfg_demographic_reference').value = 'eic2025';
        const sources = cityData.demand.sources || {};
        if (!document.getElementById('cfg_eic_indicators').value && sources.eic_indicators?.length === 1) document.getElementById('cfg_eic_indicators').value = sources.eic_indicators[0];
        if (!document.getElementById('cfg_eic_persons').value && sources.eic_persons?.length) document.getElementById('cfg_eic_persons').value = sources.eic_persons.join('\n');
        delete sources.eic_indicators; delete sources.eic_persons;
      }
      syncCohortCount();

      const nameEl = document.getElementById('cfg_city_name');
      if (nameEl) cityData.city.name = nameEl.value;

      cityData.city.residential_placement = document.getElementById('cfg_residential_placement').value;
      cityData.macroeconomics = cityData.macroeconomics || {};
      cityData.macroeconomics.residential_employment = document.getElementById('cfg_residential_employment').value;
      const demographicMode = document.getElementById('cfg_demographic_reference')?.value;
      if (demographicMode === 'eic2025') {
        const previous = cityData.macroeconomics.demographic_reference;
        cityData.macroeconomics.demographic_reference = {
          mode: 'eic2025',
          previous_projection_year: previous?.previous_projection_year || cityData.macroeconomics.projection_year || cityData.macroeconomics.target_year || 2026,
          indicators: document.getElementById('cfg_eic_indicators').value.trim(),
          persons: document.getElementById('cfg_eic_persons').value.split(/\r?\n/).map(p => p.trim()).filter(Boolean),
        };
        if (!cityData.macroeconomics.demographic_reference.indicators || !cityData.macroeconomics.demographic_reference.persons.length) {
          cityData.macroeconomics.demographic_reference.pending_download = true;
        }
        cityData.macroeconomics.projection_year = 2025;
        delete cityData.macroeconomics.target_year;
        cityData.macroeconomics.residential_employment = 'census_employed';
        document.getElementById('cfg_residential_employment').value = 'census_employed';
      } else if (demographicMode === 'projected' && cityData.macroeconomics.demographic_reference) {
        const previousYear = cityData.macroeconomics.demographic_reference.previous_projection_year;
        delete cityData.macroeconomics.demographic_reference;
        if (previousYear) cityData.macroeconomics.projection_year = previousYear;
      }
      const requestedWorkplaceMode = document.getElementById('cfg_workplace_employment')?.value || 'auto';
      const workplaceLabel = document.getElementById('workplaceCoverage');
      if (requestedWorkplaceMode === 'historical_transfer' && !cityData.macroeconomics.historical_workplace_transfer) {
        document.getElementById('cfg_workplace_employment').value = cityData.macroeconomics.workplace_employment || 'auto';
        if (workplaceLabel) workplaceLabel.textContent = 'Abre la referencia histórica, inspecciona las fuentes y aplica los grupos seleccionados primero.';
      } else {
        cityData.macroeconomics.workplace_employment = requestedWorkplaceMode;
        updateWorkplaceSourceStatus();
      }
      updateDemandMethodsSummary();
      document.getElementById('employmentCoverage').textContent = '';
      const sourceYear = document.getElementById('cfg_conapo_source_year').value;
      if (sourceYear) cityData.macroeconomics.conapo_source_year = Number(sourceYear);
      else delete cityData.macroeconomics.conapo_source_year;
      densityLoadedCityFile = null;
      const codeEl = document.getElementById('cfg_city_code');
      if (codeEl) cityData.city.code = codeEl.value.toUpperCase();

      const creatorEl = document.getElementById('cfg_city_creator');
      if (creatorEl) cityData.city.creator = creatorEl.value;

      const descEl = document.getElementById('cfg_city_desc');
      if (descEl) cityData.city.description = descEl.value;

      const gridEl = document.getElementById('cfg_grid_size');
      if (gridEl) cityData.city.grid_size = parseFloat(gridEl.value);

      const zoomEl = document.getElementById('cfg_initial_zoom');
      if (zoomEl) cityData.city.initial_zoom = parseFloat(zoomEl.value);

      const lonEl = document.getElementById('cfg_initial_lon');
      const latEl = document.getElementById('cfg_initial_lat');
      if (lonEl && latEl && lonEl.value.trim() !== '' && latEl.value.trim() !== '') {
        const lon = parseFloat(lonEl.value);
        const lat = parseFloat(latEl.value);
        if (!isNaN(lon) && !isNaN(lat)) {
          cityData.city.initial_center = [parseFloat(lon.toFixed(5)), parseFloat(lat.toFixed(5))];
        }
      }

      const oceanEl = document.getElementById('cfg_include_ocean');
      if (oceanEl) cityData.city.include_ocean = oceanEl.checked;

      const parksEl = document.getElementById('cfg_urban_parks_only');
      if (parksEl) cityData.city.urban_parks_only = parksEl.checked;

      const restrictDemandEl = document.getElementById('cfg_restrict_demand_to_urban_core');
      if (restrictDemandEl) cityData.city.restrict_demand_to_urban_core = restrictDemandEl.checked;

      const lodRoadsEl = document.getElementById('cfg_lod_peripheral_roads');
      if (lodRoadsEl) cityData.city.lod_peripheral_roads = lodRoadsEl.value;

      const omitPedEl = document.getElementById('cfg_omit_pedestrian_paths');
      if (omitPedEl) cityData.city.include_pedestrian_paths = !omitPedEl.checked;

      const lodLabelsEl = document.getElementById('cfg_lod_peripheral_labels');
      if (lodLabelsEl) cityData.city.lod_peripheral_labels = lodLabelsEl.value;

      const lodBldgsEl = document.getElementById('cfg_lod_peripheral_buildings');
      if (lodBldgsEl) cityData.city.lod_peripheral_buildings = lodBldgsEl.value;


      const minResidentsEl = document.getElementById('cfg_min_residents');
      if (minResidentsEl) cityData.city.min_residents = parseInt(minResidentsEl.value, 10) || 10;

      const minJobsEl = document.getElementById('cfg_min_jobs');
      if (minJobsEl) cityData.city.min_jobs = parseInt(minJobsEl.value, 10) || 3;

      const bldgFilterEl = document.getElementById('cfg_building_filter_size');
      if (bldgFilterEl) cityData.city.building_filter_size = parseFloat(bldgFilterEl.value) || 15.0;

      const bldgSimpEl = document.getElementById('cfg_building_simplification');
      if (bldgSimpEl) cityData.city.building_simplification = parseFloat(bldgSimpEl.value) || 0.2;

      const b0 = parseFloat(document.getElementById('cfg_bbox_0').value) || 0;
      const b1 = parseFloat(document.getElementById('cfg_bbox_1').value) || 0;
      const b2 = parseFloat(document.getElementById('cfg_bbox_2').value) || 0;
      const b3 = parseFloat(document.getElementById('cfg_bbox_3').value) || 0;
      cityData.city.bbox = [
        parseFloat(Math.min(b0, b2).toFixed(4)),
        parseFloat(Math.min(b1, b3).toFixed(4)),
        parseFloat(Math.max(b0, b2).toFixed(4)),
        parseFloat(Math.max(b1, b3).toFixed(4))
      ];

      const bboxLockedEl = document.getElementById('cfg_bbox_locked');
      if (bboxLockedEl) cityData.city.bbox_locked = bboxLockedEl.checked;
      else if (cityData.city.bbox_locked === undefined) cityData.city.bbox_locked = isBboxLocked;

      const peaEl = document.getElementById('cfg_tasa_pea');

      if (peaEl) cityData.macroeconomics.tasa_pea = parseFloat(peaEl.value);

      const tilEl = document.getElementById('cfg_til_1');
      if (tilEl) cityData.macroeconomics.til_1_state = parseFloat(tilEl.value);

      const betaEl = document.getElementById('cfg_gravity_beta');
      if (betaEl) cityData.macroeconomics.gravity_beta = parseFloat(betaEl.value);

      const distEl = document.getElementById('cfg_max_distance_km');
      if (distEl) cityData.macroeconomics.max_distance_km = parseFloat(distEl.value);

      const minPopEl = document.getElementById('cfg_min_pop_size');
      if (minPopEl) cityData.macroeconomics.min_pop_size = parseInt(minPopEl.value, 10) || 25;

      const targetPopEl = document.getElementById('cfg_target_pop_size');
      if (targetPopEl) cityData.macroeconomics.target_pop_size = parseInt(targetPopEl.value, 10) || 150;

      const popEl = document.getElementById('cfg_max_pop_size');
      if (popEl) cityData.macroeconomics.max_pop_size = parseInt(popEl.value, 10) || 200;

      if (!cityData.macroeconomics.cohort_mode) {
        const minP = cityData.macroeconomics.min_pop_size;
        const maxP = cityData.macroeconomics.max_pop_size;
        cityData.macroeconomics.cohort_mode = (minP === maxP) ? 'rigid' : 'adaptive';
      }

      const sampleThreshEl = document.getElementById('cfg_sample_threshold');
      if (sampleThreshEl) cityData.macroeconomics.sample_threshold = parseInt(sampleThreshEl.value, 10) || 500;

      const furnessIterEl = document.getElementById('cfg_furness_iterations');
      if (furnessIterEl) cityData.macroeconomics.furness_iterations = parseInt(furnessIterEl.value, 10) || 15;

      const furnessTolEl = document.getElementById('cfg_furness_tol');
      if (furnessTolEl) cityData.macroeconomics.furness_tol = parseFloat(furnessTolEl.value) || 0.02;

      const seedEl = document.getElementById('cfg_seed');
      if (seedEl && seedEl.value.trim() !== '') {
        const parsedSeed = parseInt(seedEl.value, 10);
        cityData.city.seed = isNaN(parsedSeed) ? 42 : Math.max(0, parsedSeed);
      }

      if (!cityData.macroeconomics.modal_experiment) {
        cityData.macroeconomics.modal_experiment = {};
      }
      const modalExpEn = document.getElementById('cfg_modal_experiment_enabled');
      if (modalExpEn) cityData.macroeconomics.modal_experiment.enabled = modalExpEn.checked;

      const modalPresetEl = document.getElementById('cfg_modal_experiment_preset');
      if (modalPresetEl) cityData.macroeconomics.modal_experiment.preset = modalPresetEl.value;

      const modalSpeedEl = document.getElementById('cfg_modal_traffic_speed');
      if (modalSpeedEl) cityData.macroeconomics.modal_experiment.traffic_speed_kmh = parseFloat(modalSpeedEl.value);

      const modalMotorEl = document.getElementById('cfg_modal_motorization_rate');
      if (modalMotorEl) cityData.macroeconomics.modal_experiment.motorization_rate = parseFloat(modalMotorEl.value);

      // Sincronizar POI activo SOLO si el editor de POIs está visible y hay un índice válido en edición
      const isEditorTabOpen = document.getElementById('poiTab-editor') && !document.getElementById('poiTab-editor').classList.contains('hidden');
      if (isEditorTabOpen && activeEditingPoiIndex >= 0) {
        syncActivePoiFromEditor(false);
      }
    }

    function triggerAutoSave() {
      if (typeof wizardLifecycle !== 'undefined' && !wizardLifecycle.canSave(currentCityFile)) return;
      syncStateFromInputs();

      // Guardar snapshot instantáneo en localStorage (para apagones repentinos)
      try {
        localStorage.setItem(`sb_draft_${currentCityFile}`, JSON.stringify({
          data: cityData,
          savedAt: Date.now()
        }));
      } catch (err) {}

      // UI de guardando
      const statusText = document.getElementById('saveStatusText');
      const statusIcon = document.getElementById('saveStatusIcon');
      if (statusText) statusText.innerText = "Guardando...";
      if (statusIcon) statusIcon.className = "w-3.5 h-3.5 text-metro-orange animate-spin";

      // Debounce para guardar físicamente en disco (.yaml)
      if (autoSaveTimer) clearTimeout(autoSaveTimer);
      autoSaveTimer = setTimeout(async () => {
        await saveCurrentCity(true);
      }, 1000);
    }

    function populateFormFields() {
      const c = cityData.city || {};
      const m = cityData.macroeconomics || {};

      // Paso 1
      document.getElementById('cfg_city_name').value = c.name || "";
      document.getElementById('cfg_residential_placement').value = c.residential_placement || 'official_blocks';
      if (document.getElementById('cfg_demand_engine')) document.getElementById('cfg_demand_engine').checked = cityData.demand?.engine === 'v2';
      updateDemandEngineControls();
      document.getElementById('cfg_residential_employment').value = m.residential_employment || 'census_employed';
      const demographic = m.demographic_reference;
      if (document.getElementById('cfg_demographic_reference')) document.getElementById('cfg_demographic_reference').value = demographic?.mode || 'projected';
      if (document.getElementById('cfg_eic_indicators')) document.getElementById('cfg_eic_indicators').value = demographic?.indicators || '';
      if (document.getElementById('cfg_eic_persons')) document.getElementById('cfg_eic_persons').value = (demographic?.persons || []).join('\n');
      const workplaceSelect = document.getElementById('cfg_workplace_employment');
      if (workplaceSelect) workplaceSelect.value = m.workplace_employment || 'auto';
      const historical = m.historical_workplace_benchmark || null;
      const ceYear = document.getElementById('cfg_ce_reference_year');
      const ceSources = document.getElementById('cfg_ce_sources');
      if (ceYear) ceYear.value = historical?.reference_year || 2023;
      if (ceSources) ceSources.value = (historical?.ce_sources || []).join('\n');
      const historicalStatus = document.getElementById('historicalBenchmarkStatus');
      if (historicalStatus) historicalStatus.textContent = m.historical_workplace_transfer && m.workplace_employment === 'historical_transfer' ? 'Referencia histórica vinculada; transferencia ACTIVADA. Inspecciona para actualizar el diagnóstico.' : historical ? 'Referencia histórica guardada, inactiva. Inspecciona para actualizar el diagnóstico.' : '';
      const historicalGroups = document.getElementById('historicalBenchmarkGroups');
      if (historicalGroups) historicalGroups.textContent = '';
      historicalTransferInspection = null;
      const transferGroupsContainer = document.getElementById('historicalTransferGroups');
      if (transferGroupsContainer) transferGroupsContainer.textContent = '';
      const transfer = m.historical_workplace_transfer;
      const strengthInput = document.getElementById('cfg_ce_transfer_strength');
      if (strengthInput) strengthInput.value = String(transfer?.strength ?? 1);
      const evidenceInput = document.getElementById('cfg_ce_unit_evidence');
      if (evidenceInput) evidenceInput.value = transfer?.groups?.[0]?.reporting_unit_evidence || '';
      updateDemandMethodsSummary(true);

      const workplaceLabel = document.getElementById('workplaceCoverage');
      updateWorkplaceSourceStatus();
      document.getElementById('employmentCoverage').textContent = '';
      document.getElementById('cfg_city_code').value = c.code || "";
      document.getElementById('cfg_city_creator').value = c.creator || "";
      document.getElementById('cfg_city_desc').value = c.description || "";
      document.getElementById('cfg_grid_size').value = c.grid_size || 0.0025;
      document.getElementById('cfg_initial_zoom').value = c.initial_zoom || 11.5;
      const lonEl = document.getElementById('cfg_initial_lon');
      const latEl = document.getElementById('cfg_initial_lat');
      if (lonEl && latEl) {
        if (c.initial_center && Array.isArray(c.initial_center) && c.initial_center.length === 2) {
          lonEl.value = parseFloat(c.initial_center[0]).toFixed(5);
          latEl.value = parseFloat(c.initial_center[1]).toFixed(5);
        } else {
          lonEl.value = "";
          latEl.value = "";
        }
      }
      document.getElementById('cfg_include_ocean').checked = Boolean(c.include_ocean);

      const parksEl = document.getElementById('cfg_urban_parks_only');
      if (parksEl) parksEl.checked = Boolean(c.urban_parks_only);
      const parksStep5El = document.getElementById('chkUrbanParksStep5');
      if (parksStep5El) parksStep5El.checked = Boolean(c.urban_parks_only);

      const restrictDemandEl = document.getElementById('cfg_restrict_demand_to_urban_core');
      if (restrictDemandEl) restrictDemandEl.checked = c.restrict_demand_to_urban_core !== undefined ? Boolean(c.restrict_demand_to_urban_core) : true;

      const lodRoadsEl = document.getElementById('cfg_lod_peripheral_roads');
      if (lodRoadsEl) lodRoadsEl.value = c.lod_peripheral_roads || "standard";

      const omitPedEl = document.getElementById('cfg_omit_pedestrian_paths');
      if (omitPedEl) omitPedEl.checked = c.include_pedestrian_paths !== undefined ? !Boolean(c.include_pedestrian_paths) : false;

      const lodLabelsEl = document.getElementById('cfg_lod_peripheral_labels');
      if (lodLabelsEl) lodLabelsEl.value = c.lod_peripheral_labels || "none";

      const lodBldgsEl = document.getElementById('cfg_lod_peripheral_buildings');
      if (lodBldgsEl) lodBldgsEl.value = c.lod_peripheral_buildings || "none";


      const mrEl = document.getElementById('cfg_min_residents');
      if (mrEl) mrEl.value = c.min_residents !== undefined ? c.min_residents : 10;

      const mjEl = document.getElementById('cfg_min_jobs');
      if (mjEl) mjEl.value = c.min_jobs !== undefined ? c.min_jobs : 3;

      const bfsEl = document.getElementById('cfg_building_filter_size');
      if (bfsEl) bfsEl.value = c.building_filter_size !== undefined ? c.building_filter_size : 15.0;

      const bsimEl = document.getElementById('cfg_building_simplification');
      if (bsimEl) bsimEl.value = c.building_simplification !== undefined ? c.building_simplification : 0.2;

      const rawBbox = c.bbox || [-87.05, 21.0, -86.72, 21.31];
      const normBbox = [
        parseFloat(Math.min(rawBbox[0], rawBbox[2]).toFixed(4)),
        parseFloat(Math.min(rawBbox[1], rawBbox[3]).toFixed(4)),
        parseFloat(Math.max(rawBbox[0], rawBbox[2]).toFixed(4)),
        parseFloat(Math.max(rawBbox[1], rawBbox[3]).toFixed(4))
      ];
      cityData.city.bbox = normBbox;
      document.getElementById('cfg_bbox_0').value = normBbox[0];
      document.getElementById('cfg_bbox_1').value = normBbox[1];
      document.getElementById('cfg_bbox_2').value = normBbox[2];
      document.getElementById('cfg_bbox_3').value = normBbox[3];
      updateBboxLayer(normBbox);
      updateBboxDimensionsDisplay(normBbox);

      const locked = Boolean(c.bbox_locked);
      toggleBboxLock(locked, false);
      const presetSel = document.getElementById('cfg_bbox_preset');
      if (presetSel) presetSel.value = 'custom';

      // Paso 3

      const pea = m.tasa_pea || 0.62;
      document.getElementById('cfg_tasa_pea').value = pea;
      document.getElementById('val_tasa_pea').innerText = (pea * 100).toFixed(2) + "%";

      const til1 = m.til_1_state || 0.45;
      document.getElementById('cfg_til_1').value = til1;
      document.getElementById('val_til_1').innerText = (til1 * 100).toFixed(2) + "%";

      const beta = m.gravity_beta || 0.12;
      document.getElementById('cfg_gravity_beta').value = beta;
      document.getElementById('val_gravity_beta').innerText = beta.toFixed(3);

      document.getElementById('cfg_max_distance_km').value = m.max_distance_km || 50.0;
      document.getElementById('cfg_min_pop_size').value = m.min_pop_size || 25;
      document.getElementById('cfg_target_pop_size').value = m.target_pop_size || 150;
      document.getElementById('cfg_max_pop_size').value = m.max_pop_size || 200;
      if (document.getElementById('cfg_cohort_count')) document.getElementById('cfg_cohort_count').value = cityData.demand?.cohort_count ?? '';
      updateCohortUIFromData();

      const stEl = document.getElementById('cfg_sample_threshold');
      if (stEl) stEl.value = m.sample_threshold !== undefined ? m.sample_threshold : 500;

      const fiEl = document.getElementById('cfg_furness_iterations');
      if (fiEl) fiEl.value = m.furness_iterations !== undefined ? m.furness_iterations : 15;

      const ftEl = document.getElementById('cfg_furness_tol');
      if (ftEl) ftEl.value = m.furness_tol !== undefined ? m.furness_tol : 0.02;

      const seedVal = (c.seed !== undefined && c.seed !== null) ? c.seed : ((m.seed !== undefined && m.seed !== null) ? m.seed : 42);
      const seedEl = document.getElementById('cfg_seed');
      if (seedEl) seedEl.value = seedVal;

      // Laboratorio Modal (Paso 3)
      const modExp = m.modal_experiment || {};
      const modEn = Boolean(modExp.enabled);
      const modEnEl = document.getElementById('cfg_modal_experiment_enabled');
      if (modEnEl) modEnEl.checked = modEn;

      const modPreset = modExp.preset || (modEn ? "custom" : "canonical");
      const modPresetEl = document.getElementById('cfg_modal_experiment_preset');
      if (modPresetEl) modPresetEl.value = modPreset;

      const modSpeed = modExp.traffic_speed_kmh !== undefined ? parseFloat(modExp.traffic_speed_kmh) : 22.0;
      const modSpeedEl = document.getElementById('cfg_modal_traffic_speed');
      if (modSpeedEl) {
        modSpeedEl.value = modSpeed;
        const valSpeedEl = document.getElementById('val_modal_traffic_speed');
        if (valSpeedEl) valSpeedEl.innerText = `${Math.round(modSpeed)} km/h`;
      }

      const modMotor = modExp.motorization_rate !== undefined ? parseFloat(modExp.motorization_rate) : 0.40;
      const modMotorEl = document.getElementById('cfg_modal_motorization_rate');
      if (modMotorEl) {
        modMotorEl.value = modMotor;
        const valMotorEl = document.getElementById('val_modal_motorization_rate');
        if (valMotorEl) valMotorEl.innerText = `${Math.round(modMotor * 100)}%`;
      }

      toggleModalExperimentControls(modEn);
      updateModalSimulatorPreview();
      renderUrbanCorePolygon();

      document.getElementById('cfg_conapo_source_year').value = m.conapo_source_year || '';
      const projYr = m.projection_year || m.target_year;
      const selYr = document.getElementById('conapoYearSelect');
      if (projYr && selYr) {
        let exists = false;
        for (let i = 0; i < selYr.options.length; i++) {
          if (parseInt(selYr.options[i].value) === parseInt(projYr)) {
            exists = true;
            break;
          }
        }
        if (!exists) {
          const opt = document.createElement('option');
          opt.value = projYr;
          opt.innerText = projYr;
          selYr.appendChild(opt);
        }
        selYr.value = projYr;
      }

      // Adjuntar listeners de auto-guardado a los campos de formulario
      const inputIds = [
        'cfg_residential_placement', 'cfg_residential_employment', 'cfg_demographic_reference', 'cfg_eic_indicators', 'cfg_eic_persons', 'cfg_workplace_employment', 'cfg_conapo_source_year', 'cfg_city_name', 'cfg_city_code', 'cfg_city_creator', 'cfg_city_desc',
        'cfg_grid_size', 'cfg_initial_zoom', 'cfg_initial_lon', 'cfg_initial_lat', 'cfg_include_ocean', 'cfg_urban_parks_only',
        'cfg_min_residents', 'cfg_min_jobs',
        'cfg_building_filter_size', 'cfg_building_simplification',
        'cfg_bbox_0', 'cfg_bbox_1', 'cfg_bbox_2', 'cfg_bbox_3',
        'cfg_tasa_pea', 'cfg_til_1', 'cfg_gravity_beta',
        'cfg_max_distance_km', 'cfg_min_pop_size', 'cfg_target_pop_size', 'cfg_max_pop_size', 'cfg_cohort_count',
        'cfg_fixed_pop_size', 'cfg_fixed_pop_range', 'cfg_seed',
        'cfg_sample_threshold', 'cfg_furness_iterations', 'cfg_furness_tol',
        'cfg_modal_experiment_enabled', 'cfg_modal_experiment_preset',
        'cfg_modal_traffic_speed', 'cfg_modal_motorization_rate',
        'cfg_restrict_demand_to_urban_core', 'cfg_lod_peripheral_roads',
        'cfg_omit_pedestrian_paths', 'cfg_lod_peripheral_labels', 'cfg_lod_peripheral_buildings'
      ];
      inputIds.forEach(id => {
        const el = document.getElementById(id);
        if (el && !el.dataset.autosaveBound) {
          el.addEventListener('input', triggerAutoSave);
          el.addEventListener('change', triggerAutoSave);
          el.dataset.autosaveBound = "true";
        }
      });

      // Listeners reactivos para Zoom Preview
      const showPreviewEl = document.getElementById('cfg_show_zoom_preview');
      if (showPreviewEl && !showPreviewEl.dataset.bound) {
        showPreviewEl.addEventListener('change', () => {
          updateZoomPreviewLayer();
        });
        showPreviewEl.dataset.bound = "true";
      }

      const zoomInputEl = document.getElementById('cfg_initial_zoom');
      if (zoomInputEl && !zoomInputEl.dataset.zoomPreviewBound) {
        zoomInputEl.addEventListener('input', () => {
          updateZoomPreviewLayer();
        });
        zoomInputEl.dataset.zoomPreviewBound = "true";
      }

      updateZoomPreviewLayer();

      renderGrowthFactorsTable();
      renderPoiList();
      renderPoiMarkersOnMap();
      renderPlacesList();
      renderPlacesMarkersOnMap();
      if (!cityData.isolated_zones) {
        cityData.isolated_zones = (cityData.city && Array.isArray(cityData.city.isolated_zones)) ? cityData.city.isolated_zones : [];
      }
      renderIsolatedZones();
      if (!cityData.affluence_zones) {
        cityData.affluence_zones = [];
      }
      renderAffluenceZones();
      if (!cityData.exclusion_zones) {
        cityData.exclusion_zones = [];
      }
      renderExclusionZones();
    }

    // -------------------------------------------------------------------------
    // GUARDADO GLOBAL EN DISCO
    // -------------------------------------------------------------------------
    async function saveCurrentCity(isSilent = false) {
      if (!currentCityFile) return false;
      if (typeof wizardLifecycle !== 'undefined' && !wizardLifecycle.canSave(currentCityFile)) return false;
      if (autoSaveTimer) {
        clearTimeout(autoSaveTimer);
        autoSaveTimer = null;
      }
      try {
        syncStateFromInputs();
        const file = currentCityFile;
        const body = JSON.stringify({ file, ...cityData });
        const pending = citySaveChain.then(async () => {
          const res = await fetch('/api/city/save', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body,
            signal: AbortSignal.timeout(30000)
          });
          const json = await res.json();
          if (!res.ok || json.status !== 'ok') throw new Error(json.error || json.message || 'Error al guardar');
          return json;
        });
        citySaveChain = pending.catch(() => {});
        const json = await pending;
        if (file !== currentCityFile) return json.status === 'ok';
        if (json.status === 'ok') {
          if (body === JSON.stringify({file, ...cityData}) && json.demographic_reference && cityData.demand?.engine === 'v2') {
            cityData.macroeconomics.demographic_reference = json.demographic_reference;
            syncDemandEngineFields();
          }
          // Limpiar borrador temporal al guardar con éxito en disco
          try {
            localStorage.removeItem(`sb_draft_${currentCityFile}`);
          } catch (e) {}
          hideDraftBanner();

          renderPoiList();
          renderPoiMarkersOnMap();

          const statusText = document.getElementById('saveStatusText');
          const statusIcon = document.getElementById('saveStatusIcon');
          if (statusText) statusText.innerText = "Guardado";
          if (statusIcon) {
            statusIcon.className = "w-3.5 h-3.5 text-emerald-400";
            statusIcon.setAttribute('data-lucide', 'check');
          }
          lucide.createIcons();

          if (!isSilent) {
            showToast("¡Configuración guardada en disco exitosamente!", "success");
          }
          return true;
        } else {
          throw new Error(json.error || "Error al guardar");
        }
      } catch (e) {
        const statusText = document.getElementById('saveStatusText');
        const statusIcon = document.getElementById('saveStatusIcon');
        if (statusText) statusText.innerText = "Borrador local";
        if (statusIcon) {
          statusIcon.className = "w-3.5 h-3.5 text-amber-400";
          statusIcon.setAttribute('data-lucide', 'alert-triangle');
        }
        lucide.createIcons();
        if (!isSilent) {
          showToast(e.message, "error");
        }
        return false;
      }
    }

    function openDataSourcesModal(sourceKey = '') {
      const modal = document.getElementById('modalDataSources');
      modal.classList.remove('hidden');
      const context = document.getElementById('dataSourceGuideContext');
      if (context) context.textContent = `${cityData.city?.name || 'Proyecto'} · Carpeta: ${cityData.data_dir || document.getElementById('activeDataFolderBadge')?.textContent || 'data/'}. Selecciona las entidades y periodos correspondientes.`;
      modal.querySelectorAll('.source-guide-block').forEach(block => {
        block.open = sourceKey ? block.id === `guide-${sourceKey}` : ['guide-denue', 'guide-cpv'].includes(block.id);
      });
      if (sourceKey) document.getElementById(`guide-${sourceKey}`)?.scrollIntoView({block: 'nearest'});
    }
    function closeDataSourcesModal() { document.getElementById('modalDataSources').classList.add('hidden'); }

    let sourceDownloadPlan = null;
    let sourceDownloadBusy = false;
    function updateSourceDownloadEnoeOptions() {
      const options = document.getElementById('sourceDownloadEnoeOptions');
      if (options) options.hidden = ![...document.querySelectorAll('#sourceDownloadChoices input:checked')].some(input => input.value === 'enoe');
    }
    async function sourceDownloadRequest(url, body, timeout = 120000) {
      const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body), signal: AbortSignal.timeout(timeout)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || result.message || 'Error al preparar descargas');
      return result;
    }
    async function prepareSourceDownloads() {
      if (sourceDownloadBusy || !currentCityFile) return;
      const file = currentCityFile;
      sourceDownloadBusy = true;
      sourceDownloadPlan = null;
      document.getElementById('sourceDownloadPlan').classList.add('hidden');
      const message = document.getElementById('sourceDownloadProgress');
      message.textContent = 'Consultando geografía oficial INEGI…';
      document.getElementById('sourceDownloadPrepare').disabled = true;
      try {
        if (!await saveCurrentCity(true) || file !== currentCityFile) return;
        let preparation = await sourceDownloadRequest('/api/sources/plan', {file}, 30000);
        while (preparation.status === 'running') {
          await new Promise(resolve => setTimeout(resolve, 1500));
          const response = await fetch(`/api/sources/plan-job?id=${encodeURIComponent(preparation.id)}`, {signal: AbortSignal.timeout(15000)});
          preparation = await response.json();
          if (!response.ok) throw new Error(preparation.error || preparation.message || 'No se pudo consultar la preparación');
        }
        if (preparation.status === 'error') throw new Error(preparation.message);
        const plan = preparation.plan || preparation;
        if (file !== currentCityFile) return;
        sourceDownloadPlan = {...plan, requestedFile: file};
        document.getElementById('sourceDownloadGeography').textContent =
          `${plan.geography.states.map(s => `${s.code} ${s.name}`).join(', ')}. ` +
          `${plan.geography.municipalities.length} municipios. Carpeta: ${plan.folder}`;
        const choices = document.getElementById('sourceDownloadChoices');
        choices.replaceChildren();
        for (const source of plan.sources) {
          const label = document.createElement('label');
          label.className = 'block';
          const input = document.createElement('input');
          input.type = 'checkbox'; input.value = source.kind;
          input.checked = source.conflicts.length === 0 && source.recommended !== false;
          input.disabled = source.conflicts.length > 0;
          if (source.kind === 'enoe') input.addEventListener('change', updateSourceDownloadEnoeOptions);
          label.append(input, document.createTextNode(' ' + source.label +
            (source.conflicts.length ? (source.kind === 'eic' ? ' · archivos existentes: conserva sus rutas; no se sobrescriben archivos manuales' : ' · fuentes existentes: conserva su selección o exclúyelas y prepara de nuevo') : '')));
          if (source.conflicts.length) label.title = source.conflicts.join('\n');
          choices.append(label);
        }
        updateSourceDownloadEnoeOptions();
        const state = document.getElementById('sourceDownloadEnoeState');
        state.replaceChildren(new Option('Elige una entidad', ''));
        for (const s of plan.geography.states) state.add(new Option(`${s.code} ${s.name}`, s.code));
        if (plan.geography.states.length === 1) state.value = plan.geography.states[0].code;
        document.getElementById('sourceDownloadPlan').classList.remove('hidden');
        message.textContent = 'Revisa las fuentes y el periodo. OSM descarga el extracto nacional (~600 MB); se comparte entre proyectos.';
      } catch (error) {
        if (file === currentCityFile) message.textContent = error.message + ' · La guía manual sigue disponible en «¿Dónde descargar?».';
      } finally {
        sourceDownloadBusy = false;
        document.getElementById('sourceDownloadPrepare').disabled = false;
      }
    }
    async function startSourceDownloads() {
      if (sourceDownloadBusy || !sourceDownloadPlan || sourceDownloadPlan.requestedFile !== currentCityFile) return;
      const file = currentCityFile;
      const eicReferenceAtStart = JSON.stringify(cityData.macroeconomics?.demographic_reference);
      const eicBboxAtStart = JSON.stringify(cityData.city?.bbox);
      const demandAtStart = JSON.stringify({engine: cityData.demand?.engine || 'legacy', year: cityData.demand?.target_year ?? 2025});
      const kinds = [...document.querySelectorAll('#sourceDownloadChoices input:checked')].map(i => i.value);
      const state = document.getElementById('sourceDownloadEnoeState').value;
      const message = document.getElementById('sourceDownloadProgress');
      if (!kinds.length || (kinds.includes('enoe') && !state)) {
        message.textContent = 'Selecciona fuentes y, para ENOE, una entidad de referencia.';
        return;
      }
      sourceDownloadBusy = true;
      document.getElementById('sourceDownloadStart').disabled = true;
      document.getElementById('sourceDownloadPrepare').disabled = true;
      try {
        let job = await sourceDownloadRequest('/api/sources/start', {plan_id: sourceDownloadPlan.id, kinds,
          enoe_state: state, enoe_year: Number(document.getElementById('sourceDownloadEnoeYear').value),
          enoe_quarter: Number(document.getElementById('sourceDownloadEnoeQuarter').value),
          refresh: document.getElementById('sourceDownloadRefresh').checked}, 30000);
        while (true) {
          if (file === currentCityFile) message.textContent = job.message + '\n' + job.results.map(r =>
            `${r.kind}: ${r.status === 'ok' ? `${r.files.length} archivos preparados` : r.message}` +
            (r.warnings?.length ? '\n' + r.warnings.join('\n') : '')).join('\n');
          if (job.status !== 'running') break;
          await new Promise(resolve => setTimeout(resolve, 1500));
          const response = await fetch(`/api/sources/job?id=${encodeURIComponent(job.id)}`, {signal: AbortSignal.timeout(15000)});
          job = await response.json();
          if (!response.ok) throw new Error(job.error || job.message || 'No se pudo consultar la descarga');
        }
        sourceDownloadPlan = null;
        if (file === currentCityFile) {
          const eicResult = job.results.find(r => r.kind === 'eic' && r.status === 'ok' && r.demographic_reference);
          const demandNow = {engine: cityData.demand?.engine || 'legacy', year: cityData.demand?.target_year ?? 2025};
          const requiresEic = cityData.macroeconomics?.demographic_reference?.mode === 'eic2025' || (demandNow.engine === 'v2' && Number(demandNow.year) === 2025);
          if (eicResult && requiresEic) {
            if (JSON.stringify(cityData.macroeconomics?.demographic_reference) !== eicReferenceAtStart || JSON.stringify(cityData.city?.bbox) !== eicBboxAtStart || JSON.stringify(demandNow) !== demandAtStart) {
              message.textContent += '\nEIC descargada; la referencia cambió durante la descarga. Conserva tu selección y revisa las rutas.';
            } else {
              cityData.macroeconomics ||= {};
              if (cityData.macroeconomics.demographic_reference?.mode !== 'eic2025') {
                cityData.macroeconomics.demographic_reference = {previous_projection_year: cityData.macroeconomics.projection_year || cityData.macroeconomics.target_year || 2026};
              }
              cityData.macroeconomics.demographic_reference = {...cityData.macroeconomics.demographic_reference, ...eicResult.demographic_reference};
              delete cityData.macroeconomics.demographic_reference.pending_download;
              cityData.macroeconomics.projection_year = 2025;
              delete cityData.macroeconomics.target_year;
              cityData.macroeconomics.residential_employment = 'census_employed';
              document.getElementById('cfg_demographic_reference').value = 'eic2025';
              document.getElementById('cfg_residential_employment').value = 'census_employed';
              document.getElementById('cfg_eic_indicators').value = eicResult.demographic_reference.indicators;
              document.getElementById('cfg_eic_persons').value = eicResult.demographic_reference.persons.join('\n');
              if (!await saveCurrentCity()) message.textContent += '\nArchivos EIC descargados; guarda el proyecto para vincular sus rutas.';
            }
          }
          document.getElementById('sourceDownloadPlan').classList.add('hidden');
          await refreshDataStatus(false);
        }
      } catch (error) {
        if (file === currentCityFile) message.textContent = error.message + ' · Revisa «Detectar» y la guía manual. Si se perdió la conexión, la descarga puede seguir en el servidor.';
      } finally {
        sourceDownloadBusy = false;
        document.getElementById('sourceDownloadStart').disabled = false;
        document.getElementById('sourceDownloadPrepare').disabled = false;
      }
    }

    async function refreshDataStatus(isManual = false) {
      if (!currentCityFile) return;
      if (sourceDownloadPlan && sourceDownloadPlan.requestedFile !== currentCityFile) {
        sourceDownloadPlan = null;
        document.getElementById('sourceDownloadPlan').classList.add('hidden');
        document.getElementById('sourceDownloadProgress').textContent = sourceDownloadBusy ? 'Hay una descarga en curso para otro proyecto.' : '';
      }
      const file = currentCityFile;
      const code = cityData.city ? (cityData.city.code || "") : "";
      const name = cityData.city ? (cityData.city.name || "") : "";
      const state = JSON.stringify(cityData);
      try {
        const status = await fetchStartupJson(`/api/data-status?city=${encodeURIComponent(code)}&name=${encodeURIComponent(name)}&file=${encodeURIComponent(file)}`, 'Fuentes de datos', 30000);
        if (file !== currentCityFile || state !== JSON.stringify(cityData)) return;
        wizardLifecycle.clearWarning('sources');
        updateDemandEngineControls(status);
        refreshWorkplaceSourceStatus();

        const folderBadge = document.getElementById('activeDataFolderBadge');
        if (folderBadge) {
          folderBadge.innerText = status.active_dir ? `${status.active_dir}/` : 'data/';
        }

        const container = document.getElementById('dataStatusList');
        if (!container) return;
        container.innerHTML = '';

        const sources = [
          { key: 'denue', label: 'DENUE', icon: 'briefcase', role: 'Establecimientos, actividad y estrato de empleo', requirement: 'Necesaria' },
          { key: 'cpv', label: 'Censo CPV 2020', icon: 'users', role: 'Población y ocupación por manzana urbana', requirement: 'Necesaria' },
          { key: 'marco', label: 'Marco Geoestadístico 2020', icon: 'map', role: 'Polígonos de manzanas y AGEB para ubicación oficial', requirement: 'Geometría oficial' },
          { key: 'eic', label: 'Encuesta Intercensal 2025', icon: 'users', role: 'Controles municipales y traslado al trabajo; rutas explícitas de indicadores y personas', requirement: status.eic?.required ? 'Necesaria · referencia 2025' : 'Opcional · activar en Macroeconomía' },
          { key: 'ce2024', label: 'Censos Económicos · SAIC', icon: 'file-text', role: 'Referencia municipal por sector y tamaño', requirement: 'Complementaria' },
          { key: 'conapo', label: 'CONAPO municipal', icon: 'trending-up', role: status.eic?.active ? 'No se aplica con EIC; se conserva para proyectos con proyecciones' : 'Población municipal para el año objetivo', requirement: status.eic?.active ? 'Opcional · sin uso con EIC' : 'Según proyección' },
          { key: 'enoe', label: 'ENOE', icon: 'activity', role: 'Participación laboral e informalidad por entidad', requirement: 'Complementaria' },
          { key: 'osm', label: 'OpenStreetMap', icon: 'map-pin', role: 'Cartografía y red vial', requirement: 'Compilación cartográfica' }
        ];

        sources.forEach(s => {
          const item = status[s.key] || { status: 'missing', files: [] };
          const files = item.files || [];
          const archives = item.archives || [];
          const isArchive = item.status === 'archive';
          const stateLabel = item.status === 'inactive' ? 'Sin activar' : item.status === 'invalid' ? 'Revisar fuente' : item.status === 'ok' ? `${files.length} encontrado${files.length === 1 ? '' : 's'}` : isArchive ? 'Extraer ZIP' : 'No encontrado';
          const detail = files.length ? `
            <details class="source-file-details">
              <summary>Ver ${files.length} archivo${files.length === 1 ? '' : 's'} y su selección</summary>
              ${files.map((f, index) => `
                <div class="source-file-row">
                  <div class="source-file-info">
                    <span class="source-file-name" title="${escapeHtml(f.path)}">${escapeHtml(f.filename)}</span>
                    <span class="source-file-meta">${f.size_mb} MB · ${s.key === 'eic' ? 'Ruta explícita' : (f.shared ? 'Compartido en data/' : 'Proyecto')}${item.selection === 'first' ? (f.selected ? ' · Primero seleccionado' : ' · No seleccionado') : ''}</span>
                  </div>
                  <div class="source-file-actions">
                    <button data-open-file="${index}" title="Abrir ubicación del archivo" aria-label="Abrir ubicación"><i data-lucide="folder-open" class="w-4 h-4"></i></button>
                    ${['marco', 'eic'].includes(s.key) ? '' : `<button data-exclude-file="${index}" title="Excluir del proyecto; conservar en disco">Excluir</button>`}
                  </div>
                </div>`).join('')}
            </details>` : '';
          const archiveNote = archives.length ? `<p class="source-preparation-note">${files.length ? 'ZIP conservados; no se procesan:' : 'Descomprime y coloca los datos extraídos en la carpeta del proyecto:'} ${archives.map(f => escapeHtml(f.filename)).join(', ')}</p>` : '';
          const selectionNote = files.length > 1 && item.selection === 'first' && item.active !== false ? '<p class="source-selection-note">Solo se selecciona el primer archivo. Excluye los demás si no corresponden al periodo o cobertura que necesitas.</p>' : '';
          const eicNote = s.key === 'eic' ? `<p class="source-selection-note">${escapeHtml(item.message || (item.active ? (item.missing_paths?.length ? 'Faltan archivos: ' + item.missing_paths.join(', ') : 'Se comprueba estructura de CSV. Los controles municipales se concilian al descargar y compilar; cambia las rutas en Macroeconomía.') : 'Activa EIC en Macroeconomía y prepara la descarga de las entidades del mapa.'))}</p>` : '';
          const missingNote = !files.length && !isArchive ? ({
            marco: item.placement === 'legacy' ? 'El proyecto conserva ubicación legacy. Consulta la guía para cambiar sus capas.' : 'Sin capas oficiales se usan fuentes de respaldo; revisa la cobertura de ubicación en la vista previa.',
            ce2024: 'Sin detalle utilizable, el método automático conserva estimaciones DENUE.',
            enoe: 'Revisa las tasas configuradas y su procedencia en Macroeconomía.',
            conapo: status.eic?.active ? 'No necesitas descargar CONAPO para compilar con EIC 2025.' : 'Revisa el factor de crecimiento configurado antes de continuar.',
            osm: 'Para un mapa nuevo necesitas PBF; para solo demanda, cartografía previa compatible.'
          }[s.key] || 'Añade los datos extraídos de las entidades del proyecto.') : '';
          const card = document.createElement('div');
          card.className = `source-data-card${isArchive ? ' source-needs-extraction' : ''}`;
          card.innerHTML = `
            <div class="source-card-heading">
              <div class="source-card-title"><i data-lucide="${s.icon}" class="w-4 h-4"></i><strong>${s.label}</strong></div>
              <span class="source-state${isArchive ? ' source-state-warning' : ''}">${stateLabel}</span>
            </div>
            <p class="source-card-purpose">${s.role}</p>
            <div class="source-card-tools"><span class="source-guide-tag">${s.requirement}</span><button data-source-help>Cómo conseguirlo ↗</button></div>
            ${detail}${selectionNote}${archiveNote}${eicNote}
            ${missingNote ? `<p class="source-selection-note">${missingNote}</p>` : ''}`;
          card.querySelector('[data-source-help]').addEventListener('click', () => openDataSourcesModal(s.key));
          card.querySelectorAll('[data-open-file]').forEach(button => {
            const f = files[Number(button.dataset.openFile)];
            button.addEventListener('click', () => openFileLocation(f.abs_path || f.path));
          });
          card.querySelectorAll('[data-exclude-file]').forEach(button => {
            button.addEventListener('click', () => unlinkDataFile(files[Number(button.dataset.excludeFile)].filename));
          });
          container.appendChild(card);
        });

        // Renderizar exclusiones si existen
        const exCont = document.getElementById('dataExclusionsContainer');
        const exList = document.getElementById('dataExclusionsList');
        if (exCont && exList) {
          const exclusions = status.exclusions || [];
          if (exclusions.length > 0) {
            exCont.classList.remove('hidden');
            exList.innerHTML = exclusions.map(ex => `
              <div class="flex items-center justify-between bg-stone-50 border border-stone-200 px-3 py-2 rounded-lg text-xs">
                <div class="flex items-center space-x-2 truncate mr-2">
                  <span class="w-2 h-2 rounded-full bg-stone-400 shrink-0"></span>
                  <span class="truncate text-stone-600 font-mono line-through font-semibold">${ex}</span>
                </div>
                <button onclick="relinkDataFile('${ex}')" class="px-2 py-1 bg-white hover:bg-stone-100 border border-stone-300 text-emerald-700 hover:text-emerald-800 rounded font-semibold text-[11px] flex items-center space-x-1 transition shadow-2xs shrink-0" title="Volver a incluir este archivo en la compilación">
                  <i data-lucide="undo-2" class="w-3.5 h-3.5"></i>
                  <span>Restaurar</span>
                </button>
              </div>
            `).join('');
          } else {
            exCont.classList.add('hidden');
            exList.innerHTML = '';
          }
        }

        lucide.createIcons();
        if (isManual) {
          showToast("Fuentes de datos actualizadas", "success");
        }
      } catch (e) {
        console.error("Error al obtener estado de datos:", e);
        const message = e.message.startsWith('Fuentes de datos:') ? e.message : `Fuentes de datos: ${e.message}`;
        if (file === currentCityFile) wizardLifecycle.warn('sources', message, () => refreshDataStatus(true));
      }
    }

    // Drag & Drop
    function handleDragOver(e) {
      e.preventDefault();
      document.getElementById('dropzone').classList.add('border-metro-pink', 'bg-metro-pink/10');
    }
    function handleDragLeave(e) {
      e.preventDefault();
      document.getElementById('dropzone').classList.remove('border-metro-pink', 'bg-metro-pink/10');
    }
    function handleDrop(e) {
      e.preventDefault();
      document.getElementById('dropzone').classList.remove('border-metro-pink', 'bg-metro-pink/10');
      if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        uploadFiles(e.dataTransfer.files);
      }
    }
    function handleFileSelect(e) {
      if (e.target.files && e.target.files.length > 0) {
        uploadFiles(e.target.files);
      }
    }

    async function uploadFiles(files) {
      if (!files || files.length === 0) return;
      const progressCont = document.getElementById('uploadProgressContainer');
      const progressBar = document.getElementById('uploadProgressBar');
      const progressPercent = document.getElementById('uploadPercent');
      const fileNameLabel = document.getElementById('uploadFileName');
      const uploadedList = document.getElementById('uploadedFilesList');

      progressCont.classList.remove('hidden');

      const code = cityData.city ? (cityData.city.code || "") : "";
      const uploadUrl = `/api/upload?file=${encodeURIComponent(currentCityFile)}&city=${encodeURIComponent(code)}`;

      const totalFiles = files.length;
      let successCount = 0;

      for (let i = 0; i < totalFiles; i++) {
        const file = files[i];
        fileNameLabel.innerText = `Subiendo (${i + 1}/${totalFiles}): ${file.name}...`;
        progressBar.style.width = "0%";
        progressPercent.innerText = "0%";

        try {
          await new Promise((resolve) => {
            const formData = new FormData();
            formData.append("file", file);
            const xhr = new XMLHttpRequest();
            xhr.open("POST", uploadUrl, true);

            xhr.upload.onprogress = (e) => {
              if (e.lengthComputable) {
                const p = Math.round((e.loaded / e.total) * 100);
                progressBar.style.width = p + "%";
                progressPercent.innerText = p + "%";
              }
            };

            xhr.onload = () => {
              if (xhr.status === 200) {
                const resp = JSON.parse(xhr.responseText || '{}');
                const targetFolder = resp.target_dir ? `${resp.target_dir}/` : 'data/';
                showToast(`Archivo '${file.name}' guardado en ${targetFolder}`, "success");
                const isArchive = /\.zip$/i.test(file.name);
                if (isArchive) showToast('ZIP guardado: descomprímelo en la carpeta del proyecto y pulsa Detectar.', 'warning');
                const li = document.createElement('li');
                li.className = "text-emerald-700 flex items-center space-x-1 font-medium";
                li.textContent = `${file.name} (${(file.size / (1024 * 1024)).toFixed(2)} MB) → ${targetFolder}${isArchive ? ' · Pendiente de extracción' : ''}`;
                if (uploadedList.querySelector('li.italic')) uploadedList.innerHTML = '';
                uploadedList.appendChild(li);
                successCount++;
              } else {
                showToast(`Error al subir ${file.name}`, "error");
              }
              resolve();
            };

            xhr.onerror = () => {
              showToast(`Error de red al subir ${file.name}`, "error");
              resolve();
            };

            xhr.send(formData);
          });
        } catch (err) {
          console.error(`Error en subida de ${file.name}:`, err);
        }
      }

      if (successCount > 0) {
        await refreshDataStatus(false);
      }
      fileNameLabel.innerText = `Carga finalizada (${successCount}/${totalFiles} archivos)`;
      progressBar.style.width = "100%";
      progressPercent.innerText = "100%";
      setTimeout(() => progressCont.classList.add('hidden'), 2500);
    }

    // -------------------------------------------------------------------------
    // PASO 3: PARÁMETROS MACROECONÓMICOS (ENOE / GRAVITATORIO)
    // -------------------------------------------------------------------------
    async function resetMacroeconomicParameters() {
      const btn = document.getElementById('btnResetMacro');
      if (btn) btn.disabled = true;
      showToast("Consultando parámetros oficiales ENOE / INEGI...", "info");
      try {
        const res = await fetch(`/api/macro/detect?file=${encodeURIComponent(currentCityFile)}`);
        const data = await res.json();
        if (data.status === 'ok' && data.parameters) {
          const p = data.parameters;
          if (!cityData.macroeconomics) cityData.macroeconomics = {};

          cityData.macroeconomics.tasa_pea = p.tasa_pea;
          cityData.macroeconomics.til_1_state = p.til_1_state;
          cityData.macroeconomics.gravity_beta = p.gravity_beta;
          cityData.macroeconomics.max_distance_km = p.max_distance_km;
          cityData.macroeconomics.max_pop_size = p.max_pop_size;

          document.getElementById('cfg_tasa_pea').value = p.tasa_pea;
          document.getElementById('val_tasa_pea').innerText = (p.tasa_pea * 100).toFixed(2) + "%";

          document.getElementById('cfg_til_1').value = p.til_1_state;
          document.getElementById('val_til_1').innerText = (p.til_1_state * 100).toFixed(2) + "%";

          document.getElementById('cfg_gravity_beta').value = p.gravity_beta;
          document.getElementById('val_gravity_beta').innerText = p.gravity_beta.toFixed(3);

          document.getElementById('cfg_max_distance_km').value = p.max_distance_km;
          document.getElementById('cfg_max_pop_size').value = p.max_pop_size;

          if (p.seed !== undefined) {
            if (!cityData.city) cityData.city = {};
            cityData.city.seed = p.seed;
            const seedInput = document.getElementById('cfg_seed');
            if (seedInput) seedInput.value = p.seed;
          }

          const ind = document.getElementById('macroSourceIndicator');
          const txt = document.getElementById('macroSourceText');
          if (ind && txt && data.source) {
            ind.classList.remove('hidden');
            txt.innerText = data.source;
          }

          if (data.beta_recommendation) {
            updateBetaRecommendationUI(data.beta_recommendation);
          }

          triggerAutoSave();
          showToast(`Parámetros macroeconómicos: ${data.source}`, data.method === 'enoe_file' ? "success" : "info");
        } else {
          showToast("No se pudieron obtener parámetros oficiales", "error");
        }
      } catch (e) {
        showToast(`Error al consultar parámetros: ${e.message}`, "error");
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    function setBetaArchetype(arch, betaVal) {
      const betaInput = document.getElementById('cfg_gravity_beta');
      const betaValEl = document.getElementById('val_gravity_beta');
      if (betaInput) betaInput.value = betaVal;
      if (betaValEl) betaValEl.innerText = parseFloat(betaVal).toFixed(3);
      if (!cityData.macroeconomics) cityData.macroeconomics = {};
      cityData.macroeconomics.gravity_beta = parseFloat(betaVal);
      onGravityBetaChanged(betaVal, arch);
      triggerAutoSave();
    }

    function onGravityBetaChanged(val, forceArch = null) {
      const betaValEl = document.getElementById('val_gravity_beta');
      if (betaValEl) betaValEl.innerText = parseFloat(val).toFixed(3);
      if (!cityData.macroeconomics) cityData.macroeconomics = {};
      cityData.macroeconomics.gravity_beta = parseFloat(val);

      const medianEl = document.getElementById('beta_expected_median');
      const ratEl = document.getElementById('beta_rationale_text');
      if (medianEl && ratEl) {
        if (val >= 0.14) {
          medianEl.innerText = "Mediana esperada: ~6 – 9 km (Perfil Compacto).";
          ratEl.innerText = "Fricción alta: concentra los viajes en el núcleo urbano y zonas contiguas.";
        } else if (val <= 0.095) {
          medianEl.innerText = "Mediana esperada: ~18 – 25 km (Perfil Megaciudad).";
          ratEl.innerText = "Fricción baja: favorece flujos intermunicipales de largo alcance.";
        } else {
          medianEl.innerText = "Mediana esperada: ~10 – 15 km (Perfil Intermedio).";
          ratEl.innerText = "Fricción equilibrada: balance entre desplazamientos locales y suburbanos.";
        }
      }
    }

    function updateBetaRecommendationUI(rec) {
      if (!rec) return;
      const medianEl = document.getElementById('beta_expected_median');
      const ratEl = document.getElementById('beta_rationale_text');
      if (medianEl && rec.expected_median_km) {
        medianEl.innerText = `Mediana esperada: ~${rec.expected_median_km} (${rec.label || rec.archetype}).`;
      }
      if (ratEl && rec.rationale) {
        ratEl.innerText = rec.rationale;
      }
    }

    // =========================================================================
    // SUITE DE CALIBRACIÓN DE COHORTES (POPS) Y MODO RÍGIDO CANÓNICO
    // =========================================================================
    function setCohortPreset(presetName) {
      if (!cityData.macroeconomics) cityData.macroeconomics = {};
      cityData.macroeconomics.cohort_preset = presetName;

      if (presetName === 'canonical_200') {
        setCohortMode('rigid', false);
        setCohortFixedValue(200, false);
      } else if (presetName === 'metropoli_150') {
        setCohortMode('adaptive', false);
        setCohortValues(25, 150, 200, false);
      } else if (presetName === 'mediana_75') {
        setCohortMode('adaptive', false);
        setCohortValues(20, 75, 120, false);
      } else if (presetName === 'alta_fidelidad_35') {
        setCohortMode('adaptive', false);
        setCohortValues(10, 35, 60, false);
      }

      highlightActiveCohortPreset(presetName);
      updateCohortTelemetry();
      triggerAutoSave();
    }

    function highlightActiveCohortPreset(presetName) {
      const presets = ['canonical_200', 'metropoli_150', 'mediana_75', 'alta_fidelidad_35'];
      presets.forEach(p => {
        const btn = document.getElementById(`btn_preset_${p}`);
        if (!btn) return;
        if (p === presetName) {
          btn.classList.add('border-blue-500', 'ring-2', 'ring-blue-200', 'bg-blue-50/50');
          btn.classList.remove('border-stone-200', 'bg-white');
        } else {
          btn.classList.remove('border-blue-500', 'ring-2', 'ring-blue-200', 'bg-blue-50/50');
          btn.classList.add('border-stone-200', 'bg-white');
        }
      });
    }

    function onCohortModeRadioChanged(mode) {
      setCohortMode(mode, true);
    }

    function setCohortMode(mode, triggerSave = true) {
      if (!cityData.macroeconomics) cityData.macroeconomics = {};
      cityData.macroeconomics.cohort_mode = mode;
      if (mode === 'adaptive' && cityData.demand?.fixed_cohort_size) delete cityData.demand.fixed_cohort_size;

      const isRigid = (mode === 'rigid');
      const rigidBox = document.getElementById('cohort_rigid_controls');
      const adaptiveBox = document.getElementById('cohort_adaptive_controls');
      const badgeMode = document.getElementById('cohort_badge_mode');
      const btnAdapt = document.getElementById('btn_mode_adaptive');
      const btnRigid = document.getElementById('btn_mode_rigid');

      if (rigidBox && adaptiveBox) {
        if (isRigid) {
          rigidBox.classList.remove('hidden');
          adaptiveBox.classList.add('hidden');
        } else {
          rigidBox.classList.add('hidden');
          adaptiveBox.classList.remove('hidden');
        }
      }

      if (badgeMode) {
        badgeMode.innerText = isRigid ? '🎯 Rígido Canónico' : '⚖️ Adaptativo';
        badgeMode.className = isRigid
          ? 'text-[10px] text-blue-800 font-mono bg-blue-50 px-2 py-0.5 rounded border border-blue-200 font-bold'
          : 'text-[10px] text-stone-600 font-mono bg-white px-2 py-0.5 rounded border border-stone-200';
      }

      if (btnAdapt && btnRigid) {
        if (isRigid) {
          btnRigid.className = 'px-2 py-0.5 rounded-md font-bold text-blue-800 bg-white shadow-xs transition-colors select-none';
          btnAdapt.className = 'px-2 py-0.5 rounded-md font-medium text-stone-500 hover:text-stone-800 transition-colors select-none';
        } else {
          btnAdapt.className = 'px-2 py-0.5 rounded-md font-bold text-stone-800 bg-white shadow-xs transition-colors select-none';
          btnRigid.className = 'px-2 py-0.5 rounded-md font-medium text-stone-500 hover:text-stone-800 transition-colors select-none';
        }
      }

      if (isRigid) {
        const curFixed = parseInt(document.getElementById('cfg_fixed_pop_size')?.value, 10) || 200;
        setCohortFixedValue(curFixed, false);
      }

      updateCohortTelemetry();
      if (triggerSave) triggerAutoSave();
    }

    function setCohortFixedValue(val, triggerSave = true) {
      val = parseInt(val, 10) || 200;
      const rangeEl = document.getElementById('cfg_fixed_pop_range');
      const inputEl = document.getElementById('cfg_fixed_pop_size');
      const valEl = document.getElementById('val_fixed_pop_size');
      if (rangeEl) rangeEl.value = val;
      if (inputEl) inputEl.value = val;
      if (valEl) valEl.innerText = `${val} pax`;

      setCohortValues(val, val, val, triggerSave);
    }

    function onFixedPopRangeChanged(val) {
      const numVal = parseInt(val, 10) || 200;
      const inputEl = document.getElementById('cfg_fixed_pop_size');
      const valEl = document.getElementById('val_fixed_pop_size');
      if (inputEl) inputEl.value = numVal;
      if (valEl) valEl.innerText = `${numVal} pax`;
      setCohortValues(numVal, numVal, numVal, true);
    }

    function onFixedPopInputChanged(val) {
      const numVal = parseInt(val, 10) || 200;
      const rangeEl = document.getElementById('cfg_fixed_pop_range');
      const valEl = document.getElementById('val_fixed_pop_size');
      if (rangeEl) rangeEl.value = numVal;
      if (valEl) valEl.innerText = `${numVal} pax`;
      setCohortValues(numVal, numVal, numVal, true);
    }

    function setCohortValues(minVal, targetVal, maxVal, triggerSave = true) {
      minVal = parseInt(minVal, 10) || 25;
      targetVal = parseInt(targetVal, 10) || 150;
      maxVal = parseInt(maxVal, 10) || 200;

      const minEl = document.getElementById('cfg_min_pop_size');
      const targetEl = document.getElementById('cfg_target_pop_size');
      const maxEl = document.getElementById('cfg_max_pop_size');

      if (minEl) minEl.value = minVal;
      if (targetEl) targetEl.value = targetVal;
      if (maxEl) maxEl.value = maxVal;

      if (!cityData.macroeconomics) cityData.macroeconomics = {};
      cityData.macroeconomics.min_pop_size = minVal;
      cityData.macroeconomics.target_pop_size = targetVal;
      cityData.macroeconomics.max_pop_size = maxVal;

      if (cityData.demand?.fixed_cohort_size) {
        if (minVal === targetVal && targetVal === maxVal) cityData.demand.fixed_cohort_size = targetVal;
        else delete cityData.demand.fixed_cohort_size;
      }
      syncCohortCount();
      updateCohortTelemetry();
      if (triggerSave) triggerAutoSave();
    }

    function onCohortParamChanged() {
      syncCohortCount();
      const minVal = parseInt(document.getElementById('cfg_min_pop_size')?.value, 10) || 25;
      const targetVal = parseInt(document.getElementById('cfg_target_pop_size')?.value, 10) || 150;
      const maxVal = parseInt(document.getElementById('cfg_max_pop_size')?.value, 10) || 200;

      if (!cityData.macroeconomics) cityData.macroeconomics = {};
      cityData.macroeconomics.min_pop_size = minVal;
      cityData.macroeconomics.target_pop_size = targetVal;
      cityData.macroeconomics.max_pop_size = maxVal;

      // Detectar preset
      if (minVal === 200 && targetVal === 200 && maxVal === 200) {
        cityData.macroeconomics.cohort_preset = 'canonical_200';
        highlightActiveCohortPreset('canonical_200');
      } else if (minVal === 25 && targetVal === 150 && maxVal === 200) {
        cityData.macroeconomics.cohort_preset = 'metropoli_150';
        highlightActiveCohortPreset('metropoli_150');
      } else if (minVal === 20 && targetVal === 75 && maxVal === 120) {
        cityData.macroeconomics.cohort_preset = 'mediana_75';
        highlightActiveCohortPreset('mediana_75');
      } else if (minVal === 10 && targetVal === 35 && maxVal === 60) {
        cityData.macroeconomics.cohort_preset = 'alta_fidelidad_35';
        highlightActiveCohortPreset('alta_fidelidad_35');
      } else {
        cityData.macroeconomics.cohort_preset = 'custom';
        highlightActiveCohortPreset('custom');
      }

      updateCohortTelemetry();
      triggerAutoSave();
    }

    function updateCohortTelemetry() {
      const m = cityData.macroeconomics || {};
      const targetVal = parseInt(document.getElementById('cfg_target_pop_size')?.value || m.target_pop_size, 10) || 150;
      const minVal = parseInt(document.getElementById('cfg_min_pop_size')?.value || m.min_pop_size, 10) || 25;
      const maxVal = parseInt(document.getElementById('cfg_max_pop_size')?.value || m.max_pop_size, 10) || 200;
      const isRigid = (minVal === maxVal && minVal === targetVal) || (m.cohort_mode === 'rigid');

      const engine = selectedDemandEngine();
      const countInput = document.getElementById('cfg_cohort_count');
      if (countInput) countInput.disabled = engine !== 'v2' || isRigid;
      if (engine === 'v2') {
        const requested = countInput?.value ? Number(countInput.value) : null;
        const metadata = window.demandV2CohortMetadata?.file === currentCityFile ? window.demandV2CohortMetadata : null;
        const options = metadata?.report?.options;
        const matching = options && metadata.state === JSON.stringify(cityData) && options.target_pop_size === targetVal && options.max_pop_size === maxVal && options.min_pop_size === minVal && options.cohort_count === requested;
        const count = matching ? metadata.report.actual_count : requested ?? (metadata ? (isRigid ? Math.ceil(metadata.commuters / targetVal) : Math.round(metadata.commuters / targetVal)) : null);
        const estimated = document.getElementById('cohort_estimated_pops');
        if (estimated) estimated.innerText = count === null ? 'Evalúa el candidato' : isRigid && !matching ? `Al menos ${count.toLocaleString()} cohortes; evalúa para incluir los restos locales` : `${matching ? '' : requested ? '' : '~'}${count.toLocaleString()} cohortes ${matching ? 'calculadas' : requested ? 'solicitadas' : 'estimadas'}`;
        const badge = document.getElementById('cohort_fps_badge');
        if (badge) { badge.innerText = 'Rendimiento pendiente de medir'; badge.className = 'text-[10px] text-stone-500'; }
        const recommendation = document.getElementById('cohort_recommendation_text');
        if (recommendation) recommendation.innerText = isRigid ? `Tamaño objetivo: ${targetVal} personas. Agrupamiento máximo de 500 m dentro del mismo municipio, zona aislada y componente vial. Conserva restos locales adicionales, el reparto municipal y las cuotas de POIs. Evalúa para obtener el conteo real.` : matching ? `Total calculado: ${metadata.report.actual_count.toLocaleString()}. Mínimo factible: ${metadata.report.minimum_feasible.toLocaleString()} con este máximo. Todos los viajeros se conservan.` : 'Evalúa el candidato para comprobar el total y los límites por origen. No se modifican destinos durante el empaquetado.';
        const dynamics = document.getElementById('cohort_dynamics_text');
        if (dynamics) dynamics.innerText = isRigid ? `Hasta ${targetVal} personas por cohorte, con restos locales` : requested ? `Total exacto solicitado: ${requested}` : `Objetivo: ${targetVal} personas por cohorte`;
        return;
      }

      // Calcular PEA de referencia
      let estPEA = 600000;
      if (window.conapoMetadata && Object.keys(window.conapoMetadata).length > 0) {
        const totalPobConapo = Object.values(window.conapoMetadata).reduce((acc, cur) => acc + (cur.pob_mit_mun || 0), 0);
        if (totalPobConapo > 0) {
          const tasaPEA = parseFloat(document.getElementById('cfg_tasa_pea')?.value || m.tasa_pea || 0.62);
          estPEA = Math.round(totalPobConapo * tasaPEA);
        }
      }

      const estPops = Math.max(1, Math.round(estPEA / targetVal));
      const estPopsEl = document.getElementById('cohort_estimated_pops');
      if (estPopsEl) estPopsEl.innerText = `~${estPops.toLocaleString()} pops`;

      const fpsBadge = document.getElementById('cohort_fps_badge');
      const recText = document.getElementById('cohort_recommendation_text');
      const dynText = document.getElementById('cohort_dynamics_text');

      if (fpsBadge && recText) {
        if (estPops < 20000) {
          fpsBadge.className = 'px-2 py-0.5 text-[10px] font-bold rounded-full bg-green-100 text-green-800 border border-green-200';
          fpsBadge.innerText = '🟢 60 FPS Sólido';
          recText.innerText = `Carga ligera (~${estPops.toLocaleString()} pops). Simulación ultra-fluida a 60 FPS en cualquier navegador o laptop.`;
        } else if (estPops <= 32000) {
          fpsBadge.className = 'px-2 py-0.5 text-[10px] font-bold rounded-full bg-amber-100 text-amber-800 border border-amber-200';
          fpsBadge.innerText = '🟡 Fluido (60-50 FPS)';
          recText.innerText = `Carga moderada (~${estPops.toLocaleString()} pops). Fluido en PCs de escritorio. Mantén drivingPath desactivado.`;
        } else {
          fpsBadge.className = 'px-2 py-0.5 text-[10px] font-bold rounded-full bg-red-100 text-red-800 border border-red-200';
          fpsBadge.innerText = '🔴 Alta Carga (>32k pops)';
          recText.innerText = `Alerta: ~${estPops.toLocaleString()} pops puede provocar caídas de frames en WebGL. Considera elevar target_pop_size a 150–200.`;
        }
      }

      if (dynText) {
        if (isRigid) {
          dynText.innerText = `Pulsos canónicos (${targetVal} pax/vagón)`;
          dynText.title = 'Abordaje en bloques exactos idéntico a mapas oficiales de Colin Miller.';
        } else {
          dynText.innerText = `Flujo continuo (${minVal}–${maxVal} pax)`;
          dynText.title = 'Abordaje gradual orgánico; evita andenes desiertos en periferias.';
        }
      }
    }

    function syncCohortCount() {
      const input = document.getElementById('cfg_cohort_count');
      const m = cityData.macroeconomics || {};
      if (cityData.demand?.engine === 'v2' && (cityData.demand.fixed_cohort_size || Number.isInteger(m.target_pop_size) && m.target_pop_size > 0 && m.min_pop_size === m.target_pop_size && m.target_pop_size === m.max_pop_size)) {
        if (input) input.value = '';
        cityData.demand.cohort_count = null;
      }
      if (input && (cityData.demand || input.value !== '')) {
        cityData.demand = {...(cityData.demand || {}),cohort_count:input.value === '' ? null : Number(input.value)};
      }
    }

    function updateCohortUIFromData() {
      const m = (cityData && cityData.macroeconomics) || {};
      const fixed = cityData.demand?.fixed_cohort_size;
      const minVal = fixed ?? (m.min_pop_size !== undefined ? m.min_pop_size : 25);
      const targetVal = fixed ?? (m.target_pop_size !== undefined ? m.target_pop_size : 150);
      const maxVal = fixed ?? (m.max_pop_size !== undefined ? m.max_pop_size : 200);

      let mode = fixed ? 'rigid' : m.cohort_mode;
      if (!mode) {
        mode = (minVal === maxVal && minVal === targetVal) ? 'rigid' : 'adaptive';
      }

      const minEl = document.getElementById('cfg_min_pop_size');
      const targetEl = document.getElementById('cfg_target_pop_size');
      const maxEl = document.getElementById('cfg_max_pop_size');
      const fixInput = document.getElementById('cfg_fixed_pop_size');
      const fixRange = document.getElementById('cfg_fixed_pop_range');
      const fixVal = document.getElementById('val_fixed_pop_size');

      if (minEl) minEl.value = minVal;
      if (targetEl) targetEl.value = targetVal;
      if (maxEl) maxEl.value = maxVal;
      if (fixInput) fixInput.value = maxVal;
      if (fixRange) fixRange.value = maxVal;
      if (fixVal) fixVal.innerText = `${maxVal} pax`;

      setCohortMode(mode, false);

      let preset = m.cohort_preset;
      if (!preset) {
        if (minVal === 200 && targetVal === 200 && maxVal === 200) preset = 'canonical_200';
        else if (minVal === 25 && targetVal === 150 && maxVal === 200) preset = 'metropoli_150';
        else if (minVal === 20 && targetVal === 75 && maxVal === 120) preset = 'mediana_75';
        else if (minVal === 10 && targetVal === 35 && maxVal === 60) preset = 'alta_fidelidad_35';
        else preset = 'custom';
      }
      highlightActiveCohortPreset(preset);
      updateCohortTelemetry();
    }

    function randomizeSeed() {
      const newSeed = Math.floor(Math.random() * 900000000) + 100000000;
      const el = document.getElementById('cfg_seed');
      if (el) {
        el.value = newSeed;
        if (!cityData.city) cityData.city = {};
        cityData.city.seed = newSeed;
        triggerAutoSave();
        showToast(`🎲 Nueva semilla aleatoria generada: ${newSeed}`, "info");
      }
    }

    function resetSeedDefault() {
      const el = document.getElementById('cfg_seed');
      if (el) {
        el.value = 42;
        if (!cityData.city) cityData.city = {};
        cityData.city.seed = 42;
        triggerAutoSave();
        showToast("Semilla restablecida al estándar canónico (42)", "info");
      }
    }

    // -------------------------------------------------------------------------
    // PASO 3: LABORATORIO EXPERIMENTAL DE COMPETITIVIDAD MODAL (AUTO VS. METRO)
    // -------------------------------------------------------------------------
    const MODAL_PRESETS_JS = {
      canonical: { speed: 40, motor: 1.0 },
      moderate_traffic: { speed: 28, motor: 0.55 },
      cdmx_peak: { speed: 18, motor: 0.35 },
      captive_transit: { speed: 24, motor: 0.20 },
      custom: null
    };

    function toggleModalExperimentControls(show) {
      const ctrls = document.getElementById('modalExperimentControls');
      if (ctrls) {
        if (show) {
          ctrls.classList.remove('hidden');
        } else {
          ctrls.classList.add('hidden');
        }
      }
    }

    function onModalExperimentToggle(checked) {
      toggleModalExperimentControls(checked);
      if (!cityData.macroeconomics) cityData.macroeconomics = {};
      if (!cityData.macroeconomics.modal_experiment) cityData.macroeconomics.modal_experiment = {};
      cityData.macroeconomics.modal_experiment.enabled = checked;
      updateModalSimulatorPreview();
      triggerAutoSave();
    }

    function onModalPresetChange(presetKey) {
      const p = MODAL_PRESETS_JS[presetKey];
      if (p) {
        document.getElementById('cfg_modal_traffic_speed').value = p.speed;
        document.getElementById('val_modal_traffic_speed').innerText = `${p.speed} km/h`;

        document.getElementById('cfg_modal_motorization_rate').value = p.motor;
        document.getElementById('val_modal_motorization_rate').innerText = `${Math.round(p.motor * 100)}%`;
      }
      updateModalSimulatorPreview();
      triggerAutoSave();
    }

    function onModalSliderInput() {
      const speedVal = parseFloat(document.getElementById('cfg_modal_traffic_speed').value) || 22;
      const motorVal = parseFloat(document.getElementById('cfg_modal_motorization_rate').value) || 0.4;

      document.getElementById('val_modal_traffic_speed').innerText = `${Math.round(speedVal)} km/h`;
      document.getElementById('val_modal_motorization_rate').innerText = `${Math.round(motorVal * 100)}%`;

      const presetSelect = document.getElementById('cfg_modal_experiment_preset');
      if (presetSelect) presetSelect.value = "custom";

      updateModalSimulatorPreview();
      triggerAutoSave();
    }

    function updateModalSimulatorPreview() {
      const enabled = document.getElementById('cfg_modal_experiment_enabled')?.checked || false;
      const speed = parseFloat(document.getElementById('cfg_modal_traffic_speed')?.value) || 22;
      const motor = parseFloat(document.getElementById('cfg_modal_motorization_rate')?.value) || 0.4;

      const metroMin = 18.0;
      let effectiveMin = 15.0;

      if (enabled) {
        const d_m = 10000.0;
        const speed_ms = (speed / 3.6);
        const t_auto_s = Math.max(45.0, d_m / speed_ms);
        const t_transit_s = t_auto_s * 2.0 + 300.0;
        const t_eff_s = motor * t_auto_s + (1.0 - motor) * t_transit_s;
        effectiveMin = Math.round(t_eff_s / 60.0);
      }

      const effLabel = document.getElementById('sim_effective_time_label');
      if (effLabel) {
        effLabel.innerText = `${effectiveMin} min`;
      }

      const badge = document.getElementById('sim_metro_advantage_badge');
      if (badge) {
        if (!enabled) {
          badge.innerText = "Línea Base Oficial";
          badge.className = "px-1.5 py-0.5 bg-stone-700/50 text-stone-300 border border-stone-600 rounded text-[10px] font-mono";
        } else {
          const diffPct = Math.round(((effectiveMin - metroMin) / metroMin) * 100);
          if (diffPct > 0) {
            badge.innerText = `+${diffPct}% ventaja metro`;
            badge.className = "px-1.5 py-0.5 bg-emerald-500/20 text-emerald-300 border border-emerald-400/40 rounded text-[10px] font-mono font-bold";
          } else {
            badge.innerText = `${diffPct}% ventaja metro`;
            badge.className = "px-1.5 py-0.5 bg-amber-500/20 text-amber-300 border border-amber-400/40 rounded text-[10px] font-mono";
          }
        }
      }
    }

    // -------------------------------------------------------------------------
    // PASO 3: TABLA DE PROYECCIONES CONAPO
    // -------------------------------------------------------------------------
    let conapoRequestVersion = 0;
    let conapoController = null;
    let conapoCommitPromise = null;
    let citySaveChain = Promise.resolve();

    function setConapoStatus(message) {
      const label = document.getElementById('conapoSubtitleLabel');
      if (label) label.textContent = message;
    }

    function setConapoBusy(busy) {
      ['btnAutoConapo', 'conapoYearSelect', 'btnConapoYears', 'btnReplaceConapo'].forEach(id => {
        const element = document.getElementById(id);
        if (element) element.disabled = busy || cityData.demand?.engine === 'v2';
      });
    }

    function cancelConapoRequest() {
      conapoRequestVersion++;
      if (conapoController) conapoController.abort();
      conapoController = null;
      setConapoBusy(false);
    }

    async function fetchConapoJson(url, signal) {
      const response = await fetch(url, { signal });
      const data = await response.json();
      if (!response.ok || data.status !== 'ok') throw new Error(data.message || data.error || 'No se pudo consultar CONAPO.');
      return data;
    }

    async function loadConapoYears() {
      if (!currentCityFile || conapoCommitPromise) return;
      if (cityData.demand?.engine === 'v2') {
        setConapoStatus('Nuevo motor: referencia EIC 2025. CONAPO no se aplica.');
        setConapoBusy(false);
        return;
      }
      cancelConapoRequest();
      const version = conapoRequestVersion;
      const file = currentCityFile;
      const controller = conapoController = new AbortController();
      const timer = setTimeout(() => controller.abort(), 30000);
      const sourceYear = document.getElementById('cfg_conapo_source_year')?.value;
      setConapoBusy(true);
      setConapoStatus('Consultando años del archivo CONAPO…');
      try {
        const data = await fetchConapoJson(`/api/conapo/years?file=${encodeURIComponent(file)}${sourceYear === undefined ? '' : `&source_year=${encodeURIComponent(sourceYear)}`}`, controller.signal);
        if (version !== conapoRequestVersion || file !== currentCityFile) return;
        if (document.getElementById('cfg_conapo_source_year')?.value !== sourceYear) throw new Error('El año confirmado cambió durante la consulta. Pulsa Consultar años otra vez.');
        window.conapoAvailableYears = data.available_years;
        const savedYear = cityData.macroeconomics?.projection_year || cityData.macroeconomics?.target_year || data.requested_year;
        updateConapoYearSelect(data.available_years, savedYear);
        setConapoStatus(`${data.conapo_file}: ${data.available_years.length} años disponibles. Selecciona el año y pulsa Sincronizar.`);
      } catch (error) {
        if (version !== conapoRequestVersion || file !== currentCityFile) return;
        updateConapoYearSelect([], null);
        window.conapoAvailableYears = null;
        const message = error.name === 'AbortError' ? 'La consulta de años agotó el tiempo. Pulsa Consultar años para reintentar.' : error.message;
        setConapoStatus(message);
        showToast(message, 'error');
      } finally {
        clearTimeout(timer);
        if (version === conapoRequestVersion) { conapoController = null; setConapoBusy(false); }
      }
    }

    function updateConapoYearSelect(availableYears, selectedYear) {
      const select = document.getElementById('conapoYearSelect');
      if (!select) return;
      if (!availableYears || availableYears.length === 0) {
        select.innerHTML = '<option value="">Consultar años…</option>';
        return;
      }
      select.innerHTML = '';
      const years = [...new Set(availableYears.map(Number))].sort((a, b) => a - b);
      const requested = Number(selectedYear);
      if (selectedYear && !years.includes(requested)) years.push(requested);
      years.sort((a, b) => a - b).forEach(y => {
        const opt = document.createElement('option');
        opt.value = y;
        opt.innerText = availableYears.map(Number).includes(y) ? String(y) : `${y} (año guardado; sin datos exactos)`;
        if (parseInt(y) === parseInt(selectedYear)) opt.selected = true;
        select.appendChild(opt);
      });
      select.value = String(selectedYear || years[0]);
    }

    async function onConapoYearSelectChange(newYear) {
      if (!newYear) return;
      if (conapoCommitPromise) return;
      cancelConapoRequest();
      setConapoStatus(`Año seleccionado: ${newYear}. Pulsa Sincronizar para calcular y guardar; los factores anteriores aún no se han actualizado.`);
    }

    async function autoCalculateConapo(selectedYear = null, replaceSaved = false) {
      if (!currentCityFile || conapoCommitPromise) return;
      if (cityData.demand?.engine === 'v2') {
        setConapoStatus('Nuevo motor: referencia EIC 2025. CONAPO no se aplica.');
        return;
      }
      const select = document.getElementById('conapoYearSelect');
      const reqYear = Number(selectedYear || select?.value);
      if (!Number.isInteger(reqYear) || reqYear < 1900 || reqYear > 2100) {
        setConapoStatus('Consulta los años disponibles antes de sincronizar.');
        await loadConapoYears();
        return;
      }
      if (replaceSaved && !window.confirm(`Se reemplazarán los factores guardados de los municipios calculados por CONAPO para ${reqYear}. ¿Continuar?`)) return;
      cancelConapoRequest();
      const version = conapoRequestVersion;
      const file = currentCityFile;
      const project = cityData;
      const controller = conapoController = new AbortController();
      // Each page has its own deadline; large regions are never truncated.
      let timer = null;
      let previousMacro = null;
      let ownCommit = null;
      let commitControls = [];
      setConapoBusy(true);
      setConapoStatus(`Calculando CONAPO para ${reqYear}…`);
      try {
        if (await saveCurrentCity(true) !== true) throw new Error('No se pudo guardar la configuración antes de consultar CONAPO.');
        if (version !== conapoRequestVersion || file !== currentCityFile || project !== cityData) return;
        const fingerprint = JSON.stringify(project);
        const base = `/api/conapo/calculate?file=${encodeURIComponent(file)}&year=${reqYear}&mode=automatic`;
        let cursor = 0, data = null, token = null;
        const rows = [];
        do {
          clearTimeout(timer);
          timer = setTimeout(() => controller.abort(), 90000);
          const page = await fetchConapoJson(`${base}&cursor=${cursor}${token ? `&result_token=${encodeURIComponent(token)}` : ''}`, controller.signal);
          if (version !== conapoRequestVersion || file !== currentCityFile || project !== cityData) return;
          if (!Array.isArray(page.factors) || !Number.isInteger(page.projection_year)) throw new Error('Respuesta CONAPO inválida.');
          if (token && page.result_token !== token) throw new Error('Los resultados CONAPO cambiaron durante la consulta.');
          if (!data) { data = page; token = page.result_token; }
          rows.push(...page.factors);
          const next = page.next_cursor;
          if (next != null && (!Number.isInteger(next) || next <= cursor || next !== rows.length)) throw new Error('Paginación CONAPO inválida.');
          cursor = next;
        } while (cursor != null);
        clearTimeout(timer);
        if (!rows.length) throw new Error('No hay municipios CONAPO dentro del área de esta ciudad.');
        if (rows.length !== data.total_factor_count || new Set(rows.map(row => row.cve_mun)).size !== rows.length ||
            rows.some(row => !/^\d{5}$/.test(row.cve_mun) || !Number.isFinite(row.factor) || row.factor <= 0)) throw new Error('Los factores CONAPO están incompletos o son inválidos.');
        syncStateFromInputs();
        if (JSON.stringify(project) !== fingerprint) throw new Error('La configuración cambió durante el cálculo. Vuelve a sincronizar.');
        previousMacro = JSON.parse(JSON.stringify(project.macroeconomics || {}));
        const nextMacro = JSON.parse(JSON.stringify(previousMacro));
        nextMacro.growth_factors = nextMacro.growth_factors || {};
        nextMacro.growth_factor_sources = nextMacro.growth_factor_sources || {};
        let updated = 0, preserved = 0;
        rows.forEach(row => {
          const code = row.cve_mun;
          const saved = nextMacro.growth_factors[code];
          const source = nextMacro.growth_factor_sources[code];
          const automatic = source?.kind === 'conapo' && Number(source.factor) === Number(saved);
          if (saved !== undefined && !automatic && !replaceSaved) { preserved++; return; }
          nextMacro.growth_factors[code] = row.factor;
          nextMacro.growth_factor_sources[code] = { ...row, kind: 'conapo', ano: data.projection_year, requested_year: reqYear, source_file: data.conapo_file };
          updated++;
        });
        // Automatic factors outside the new region must not retain an old year.
        const included = new Set(rows.map(row => row.cve_mun));
        Object.entries(nextMacro.growth_factor_sources).forEach(([code, source]) => {
          if (!included.has(code) && source.kind === 'conapo' && Number(source.factor) === Number(nextMacro.growth_factors[code])) {
            delete nextMacro.growth_factors[code]; delete nextMacro.growth_factor_sources[code];
          }
        });
        nextMacro.projection_year = reqYear;
        commitControls = [...document.querySelectorAll('input, select, button, textarea')].map(element => [element, element.disabled]);
        commitControls.forEach(([element]) => { element.disabled = true; });
        project.macroeconomics = nextMacro;
        conapoCommitPromise = ownCommit = saveCurrentCity(true);
        if (await conapoCommitPromise !== true) {
          // A lost POST response may still have committed the atomic YAML write.
          // Confirm the persisted projection before reporting a failed commit.
          let confirmed = false;
          try {
            const response = await fetch(`/api/city?file=${encodeURIComponent(file)}`, {signal: AbortSignal.timeout(15000)});
            const reloaded = await response.json();
            const projection = macro => JSON.stringify({year: macro.projection_year, factors: macro.growth_factors, sources: macro.growth_factor_sources});
            confirmed = response.ok && reloaded.macroeconomics && projection(reloaded.macroeconomics) === projection(nextMacro);
          } catch (_) {}
          if (!confirmed) {
            project.macroeconomics = previousMacro;
            throw new Error('No se pudo confirmar el guardado; se restauró la vista anterior. Recarga la ciudad para comprobar el archivo antes de reintentar.');
          }
        }
        previousMacro = null;
        if (file !== currentCityFile || project !== cityData) return;
        window.conapoMetadata = Object.fromEntries(rows.map(row => [row.cve_mun, row]));
        window.conapoYear = data.projection_year;
        window.conapoAvailableYears = data.available_years;
        updateConapoYearSelect(data.available_years, reqYear);
        renderGrowthFactorsTable();
        const message = `Año solicitado: ${reqYear}; usado: ${data.projection_year}. ${updated} factores actualizados; ${preserved} manuales o guardados conservados.`;
        setConapoStatus(message);
        showToast(message, 'success');
      } catch (e) {
        if (previousMacro) project.macroeconomics = previousMacro;
        if (version !== conapoRequestVersion || file !== currentCityFile || project !== cityData) return;
        const message = e.name === 'AbortError' ? 'La consulta CONAPO agotó el tiempo. Vuelve a pulsar Sincronizar.' : e.message;
        setConapoStatus(message);
        renderGrowthFactorsTable();
        showToast(message, 'error');
      } finally {
        clearTimeout(timer);
        if (conapoCommitPromise === ownCommit) conapoCommitPromise = null;
        commitControls.forEach(([element, disabled]) => { element.disabled = disabled; });
        if (version === conapoRequestVersion) { conapoController = null; setConapoBusy(false); }
      }
    }

    function renderGrowthFactorsTable() {
      const tbody = document.getElementById('growthFactorsTableBody');
      tbody.innerHTML = '';
      const factors = (cityData.macroeconomics && cityData.macroeconomics.growth_factors) || {};
      const entries = Object.entries(factors);

      if (entries.length === 0) {
        const emptyTr = document.createElement('tr');
        emptyTr.innerHTML = `
          <td colspan="7" class="p-6 text-center text-stone-500">
            <span>No hay factores de proyección asignados.</span>
            <button onclick="autoCalculateConapo()" class="text-emerald-600 hover:text-emerald-700 font-bold underline ml-1.5">Sincronizar automáticamente con CONAPO</button>
          </td>
        `;
        tbody.appendChild(emptyTr);
        lucide.createIcons();
        return;
      }

      entries.forEach(([code, val]) => {
        const source = cityData.macroeconomics?.growth_factor_sources?.[code];
        const automatic = source?.kind === 'conapo' && Number(source.factor) === Number(val);
        const meta = automatic ? source : (source?.kind === 'manual' ? source : {});
        const munName = meta.name || window.conapoMetadata?.[code]?.name || `Municipio ${code}`;
        const anoStr = meta.ano || 'Sin año confirmado';
        const pob2020Str = meta.pob_2020 ? Number(meta.pob_2020).toLocaleString() : "-";
        const pobProjStr = meta.pob_conapo ? Number(meta.pob_conapo).toLocaleString() : "-";
        const factorBasis = !automatic ? 'Manual / guardado: se conserva al sincronizar' :
          meta.denominator === 'CPV municipal total 2020' ? 'Base censal municipal 2020' :
          meta.denominator === 'CONAPO population 2020 fallback' ? 'Respaldo: CONAPO 2020' :
          meta.denominator ? 'Factor predeterminado: falta base municipal' : '';

        const tr = document.createElement('tr');
        tr.className = "hover:bg-metro-panel/50 transition";
        tr.innerHTML = `
          <td class="p-2.5 font-bold text-stone-900 font-mono">${code}</td>
          <td class="p-2.5 text-stone-800 font-sans">${munName}<span class="block text-[10px] text-stone-500">${factorBasis}</span></td>
          <td class="p-2.5 text-stone-600 font-mono text-center font-bold">${anoStr}</td>
          <td class="p-2.5 text-stone-600 font-mono">${pob2020Str}</td>
          <td class="p-2.5 text-emerald-700 font-mono font-bold">${pobProjStr}</td>
          <td class="p-2.5">
            <input type="number" step="0.01" value="${val}" onchange="updateGrowthFactor('${code}', this.value)" class="bg-white border border-stone-300 rounded px-2 py-1 text-stone-900 w-20 font-mono text-center">
          </td>
          <td class="p-2.5 text-right">
            <button onclick="deleteGrowthFactor('${code}')" class="text-rose-600 hover:text-rose-700 p-1 rounded hover:bg-rose-50 transition" title="Eliminar factor">
              <i data-lucide="trash-2" class="w-4 h-4"></i>
            </button>
          </td>
        `;
        tbody.appendChild(tr);
      });
      lucide.createIcons();
    }

    function openAddGrowthFactorModal() {
      document.getElementById('gf_code').value = '';
      document.getElementById('gf_name').value = '';
      document.getElementById('gf_factor').value = '1.05';
      document.getElementById('gf_val_display').innerText = '1.05x';
      const defAno = window.conapoYear || (window.conapoMetadata && window.conapoMetadata.projection_year) || 2026;
      document.getElementById('gf_ano').value = defAno;
      document.getElementById('modalAddGrowthFactor').classList.remove('hidden');
      setTimeout(() => document.getElementById('gf_code').focus(), 50);
      lucide.createIcons();
    }

    function closeAddGrowthFactorModal() {
      const modal = document.getElementById('modalAddGrowthFactor');
      if (modal) modal.classList.add('hidden');
    }

    function saveCustomGrowthFactor() {
      const code = document.getElementById('gf_code').value.trim();
      const name = document.getElementById('gf_name').value.trim();
      const factorVal = parseFloat(document.getElementById('gf_factor').value);
      const anoVal = parseInt(document.getElementById('gf_ano').value) || 2026;

      if (!code || code.length < 4 || isNaN(factorVal) || factorVal <= 0) {
        showToast("Por favor ingresa una clave municipal válida de 4 o 5 dígitos y un factor positivo.", "warning");
        return;
      }

      if (!cityData.macroeconomics) cityData.macroeconomics = {};
      if (!cityData.macroeconomics.growth_factors) cityData.macroeconomics.growth_factors = {};
      cityData.macroeconomics.growth_factors[code] = factorVal;
      cityData.macroeconomics.growth_factor_sources = cityData.macroeconomics.growth_factor_sources || {};
      cityData.macroeconomics.growth_factor_sources[code] = {kind: 'manual', factor: factorVal, ano: anoVal, name: name || `Municipio ${code}`};

      if (!window.conapoMetadata) window.conapoMetadata = {};
      window.conapoMetadata[code] = {
        name: name || `Municipio ${code}`,
        ano: anoVal,
        pob_2020: null,
        pob_conapo: null
      };

      renderGrowthFactorsTable();
      triggerAutoSave();
      closeAddGrowthFactorModal();
      showToast(`Factor para ${name || code} agregado exitosamente`, "success");
    }

    function addGrowthFactorRow() {
      openAddGrowthFactorModal();
    }
    function updateGrowthFactor(code, val) {
      const factor = Number(val);
      if (!Number.isFinite(factor) || factor <= 0) {
        showToast('El factor debe ser un número positivo.', 'error');
        renderGrowthFactorsTable();
        return;
      }
      const prior = cityData.macroeconomics.growth_factor_sources?.[code] || {};
      cityData.macroeconomics.growth_factors[code] = factor;
      cityData.macroeconomics.growth_factor_sources = cityData.macroeconomics.growth_factor_sources || {};
      cityData.macroeconomics.growth_factor_sources[code] = {kind: 'manual', factor, ano: prior.ano || null, name: prior.name || null};
      renderGrowthFactorsTable();
      triggerAutoSave();
    }
    function deleteGrowthFactor(code) {
      delete cityData.macroeconomics.growth_factors[code];
      if (cityData.macroeconomics.growth_factor_sources) delete cityData.macroeconomics.growth_factor_sources[code];
      renderGrowthFactorsTable();
      triggerAutoSave();
    }

    // -------------------------------------------------------------------------
    // PASO 4: POI STUDIO INTEGRADO (CALIBRADOR DE RADIOS, ABSORCIÓN Y COLONIAS)
    // -------------------------------------------------------------------------
    let currentPoiFilter = 'ALL';
    let activeEditingPoiIndex = -1;
    let isPickingLocation = false;
    let densityPoints = [];
    let densityLayers = { jobs: null, pop: null };
    let isBoxSelectActive = false;
    let boxStartLatLng = null;
    let boxSelectionLayer = null;
    let isMouseDownForBox = false;
    let currentAreaStats = null;
    let editorPreviewLayer = null;
    let lastMapMouseLatLng = null;

    const TAXONOMY_META = {
      AIR: { prefix: 'AIR_', label: 'Aeropuerto', defaultRadius: 100, defaultJobs: 0, color: '#C80068', icon: 'plane', behavior: '24/7 (dampening 0.5)' },
      UNI: { prefix: 'UNI_', label: 'Universidad', defaultRadius: 100, defaultJobs: 0, color: '#007A54', icon: 'graduation-cap', behavior: 'Estudiantil (dampening 0.3)' }
    };

    let copiedPoiBuffer = null;

    function decomposePoiId(fullId) {
      if (!fullId) return { taxonomy: 'AIR', baseName: '' };
      for (let k of ['AIR', 'UNI']) {
        if (fullId.startsWith(k + '_')) {
          return { taxonomy: k, baseName: fullId.substring((k + '_').length) };
        }
      }
      return { taxonomy: 'AIR', baseName: fullId };
    }

    function generateUniquePoiId(baseId) {
      if (!cityData || !cityData.pois) return baseId + '_2';
      const existingIds = new Set(cityData.pois.map(p => p.id));
      let candidate = baseId + '_copia';
      if (!existingIds.has(candidate)) return candidate;

      let counter = 2;
      while (existingIds.has(`${baseId}_copia_${counter}`) || existingIds.has(`${baseId}_${counter}`)) {
        counter++;
      }
      return `${baseId}_copia_${counter}`;
    }

    function duplicatePoiByIndex(idx) {
      if (!cityData || !cityData.pois || !cityData.pois[idx]) return;
      const src = cityData.pois[idx];
      const cloned = JSON.parse(JSON.stringify(src));

      cloned.id = generateUniquePoiId(src.id);
      if (typeof cloned.name === 'string') {
        cloned.name = `${cloned.name} (Copia)`;
      } else if (cloned.name && typeof cloned.name === 'object') {
        if (cloned.name.es) cloned.name.es = `${cloned.name.es} (Copia)`;
        if (cloned.name.en) cloned.name.en = `${cloned.name.en} (Copy)`;
      }

      // Si el mapa está disponible, verificar si la posición original está dentro de la vista actual
      if (mapPoi) {
        const bounds = mapPoi.getBounds();
        const srcLatLng = L.latLng(src.loc[1], src.loc[0]);
        if (bounds.contains(srcLatLng)) {
          // El POI original está a la vista: clonar a un lado (+250m)
          cloned.loc = [parseFloat((src.loc[0] + 0.0025).toFixed(5)), parseFloat((src.loc[1] + 0.0025).toFixed(5))];
        } else {
          // El usuario se desplazó a otra zona: clonar en el centro de la pantalla actual
          const center = mapPoi.getCenter();
          cloned.loc = [parseFloat(center.lng.toFixed(5)), parseFloat(center.lat.toFixed(5))];
        }
      } else if (cloned.loc && cloned.loc.length === 2) {
        cloned.loc[0] = parseFloat((cloned.loc[0] + 0.0025).toFixed(5));
        cloned.loc[1] = parseFloat((cloned.loc[1] + 0.0025).toFixed(5));
      }

      cityData.pois.push(cloned);
      const newIdx = cityData.pois.length - 1;
      renderPoiList();
      renderPoiMarkersOnMap();
      openPoiEditor(newIdx);
      // No llamar a focusPoiOnMap para evitar saltos o sacudidas de cámara
      if (autoSaveTimer) {
        clearTimeout(autoSaveTimer);
        autoSaveTimer = null;
      }
      saveCurrentCity(true);
      showToast(`📋 POI duplicado como '${cloned.id}'`, "success");
    }

    async function copyPoiByIndex(idx) {
      if (!cityData || !cityData.pois || !cityData.pois[idx]) return;
      const poi = cityData.pois[idx];
      copiedPoiBuffer = JSON.parse(JSON.stringify(poi));
      try {
        await navigator.clipboard.writeText(JSON.stringify(poi, null, 2));
      } catch (err) {}
      showToast(`📋 POI '${poi.id}' copiado al portapapeles`, "info");
    }

    async function pastePoi() {
      let poiToPaste = copiedPoiBuffer;
      if (!poiToPaste) {
        try {
          const clipText = await navigator.clipboard.readText();
          if (clipText) {
            const parsed = JSON.parse(clipText);
            if (parsed && parsed.id && parsed.loc) {
              poiToPaste = parsed;
            }
          }
        } catch (err) {}
      }

      if (!poiToPaste) {
        showToast("No hay ningún POI copiado para pegar", "warning");
        return;
      }

      const cloned = JSON.parse(JSON.stringify(poiToPaste));
      cloned.id = generateUniquePoiId(cloned.id);

      // Posicionamiento inteligente:
      // 1. Si el cursor del ratón está sobre el mapa, pegar exactamente bajo el ratón
      // 2. Si no, pegar en el centro geométrico de la pantalla actual
      if (lastMapMouseLatLng) {
        cloned.loc = [parseFloat(lastMapMouseLatLng.lng.toFixed(5)), parseFloat(lastMapMouseLatLng.lat.toFixed(5))];
      } else if (mapPoi) {
        const center = mapPoi.getCenter();
        cloned.loc = [parseFloat(center.lng.toFixed(5)), parseFloat(center.lat.toFixed(5))];
      } else if (cloned.loc && cloned.loc.length === 2) {
        cloned.loc[0] = parseFloat((cloned.loc[0] + 0.003).toFixed(5));
        cloned.loc[1] = parseFloat((cloned.loc[1] + 0.003).toFixed(5));
      }

      if (!cityData.pois) cityData.pois = [];
      cityData.pois.push(cloned);
      const newIdx = cityData.pois.length - 1;
      renderPoiList();
      renderPoiMarkersOnMap();
      openPoiEditor(newIdx);
      // Cero sacudidas de cámara: el POI ya aparece en tu campo visual
      if (autoSaveTimer) {
        clearTimeout(autoSaveTimer);
        autoSaveTimer = null;
      }
      saveCurrentCity(true);
      showToast(`📋 POI pegado como '${cloned.id}'`, "success");
    }

    function detectPoiTaxonomy(id) {
      if (!id) return 'AIR';
      for (let k of ['AIR', 'UNI']) {
        if (id.startsWith(k + '_')) return k;
      }
      return 'AIR';
    }

    function switchPoiTab(tabName) {
      if (tabName === 'absorption') tabName = 'editor';
      if (tabName !== 'editor') {
        activeEditingPoiIndex = -1;
        if (editorPreviewLayer) editorPreviewLayer.clearLayers();
      }
      const tabs = ['list', 'editor', 'places', 'zones', 'exclusions'];
      tabs.forEach(t => {
        const btn = document.getElementById(`poiTabBtn-${t}`);
        const content = document.getElementById(`poiTab-${t}`);
        if (t === tabName) {
          if (btn) btn.className = "flex-1 py-2 px-1 text-center flex items-center justify-center space-x-1 border-b-2 border-metro-orange text-metro-orange bg-white transition font-bold";
          if (content) content.classList.remove('hidden');
        } else {
          if (btn) btn.className = "flex-1 py-2 px-1 text-center flex items-center justify-center space-x-1 border-b-2 border-transparent text-stone-600 hover:text-stone-900 transition font-bold";
          if (content) content.classList.add('hidden');
        }
      });
      if (tabName === 'places') {
        renderPlacesList();
        renderPlacesMarkersOnMap();
      }
      if (tabName === 'editor') updatePoiPreviewFromForm();
      if (tabName === 'zones') renderAffluenceZones();
      if (tabName === 'exclusions') renderExclusionZones();
      lucide.createIcons();
    }

    function setPoiTaxonomyFilter(tax) {
      currentPoiFilter = tax;
      document.querySelectorAll('.poi-filter-chip').forEach(el => {
        el.className = "poi-filter-chip px-2 py-0.5 rounded bg-metro-panel text-metro-muted hover:text-metro-text transition";
      });
      const activeBtn = document.getElementById(`poiFilter-${tax}`);
      if (activeBtn) activeBtn.className = "poi-filter-chip px-2 py-0.5 rounded bg-metro-orange text-white transition";
      renderPoiList();
    }

    function filterPoiList() {
      renderPoiList();
    }

    function calculatePoiLiveStats(poi) {
      const jobsManual = parseInt(poi.jobs) || 0;
      const radius = (poi.radius_m !== undefined && poi.radius_m !== null) ? parseInt(poi.radius_m) : 100;
      const mode = (poi.mode || 'MAX').toUpperCase();
      const lon = poi.loc ? poi.loc[0] : null;
      const lat = poi.loc ? poi.loc[1] : null;

      let absorbedJobs = 0;
      let absorbedPop = 0;
      let absorbedNodesCount = 0;

      if (densityPoints && densityPoints.length > 0 && lon !== null && lat !== null) {
        densityPoints.forEach(p => {
          const d = getDistanceMeters(lon, lat, p.location[0], p.location[1]);
          if (d <= radius) {
            absorbedJobs += (p.jobs || 0);
            absorbedPop += (p.residents || 0);
            absorbedNodesCount++;
          }
        });
      }

      let finalJobs = jobsManual;
      let formulaText = "";
      if (mode === "MAX") {
        finalJobs = Math.max(jobsManual, absorbedJobs);
        if (absorbedJobs >= jobsManual && jobsManual > 0) {
          formulaText = `Piso DENUE (${absorbedJobs.toLocaleString()}) supera manual (${jobsManual.toLocaleString()})`;
        } else {
          formulaText = `Piso manual: ${jobsManual.toLocaleString()} (absorbe ${absorbedJobs.toLocaleString()} DENUE)`;
        }
      } else if (mode === "BOOST" || mode === "ADDITIVE") {
        finalJobs = jobsManual + absorbedJobs;
        formulaText = `Manual: ${jobsManual.toLocaleString()} + DENUE: ${absorbedJobs.toLocaleString()}`;
      } else if (mode === "REPLACE") {
        finalJobs = jobsManual;
        formulaText = `Fijo manual: ${jobsManual.toLocaleString()} (sustituye ${absorbedJobs.toLocaleString()} DENUE)`;
      } else {
        finalJobs = Math.max(jobsManual, absorbedJobs);
        formulaText = `Piso: ${jobsManual.toLocaleString()}`;
      }

      return {
        manual: jobsManual,
        absorbed: absorbedJobs,
        absorbedPop: absorbedPop,
        absorbedNodes: absorbedNodesCount,
        finalJobs: finalJobs,
        mode: mode,
        formulaText: formulaText
      };
    }

    function renderPoiList() {
      const pois = cityData.pois || [];
      const badge = document.getElementById('poiCountBadge');
      const badgeTab = document.getElementById('poiCountBadgeTab');
      if (badge) badge.innerText = `${pois.length} POIs configurados`;
      if (badgeTab) badgeTab.innerText = pois.length;
      const cont = document.getElementById('poiListContainer');
      if (!cont) return;
      cont.innerHTML = '';

      const search = (document.getElementById('poiSearchInput')?.value || '').toLowerCase().trim();

      const filtered = pois.map((p, idx) => ({ ...p, originalIndex: idx })).filter(p => {
        const tax = detectPoiTaxonomy(p.id);
        if (currentPoiFilter !== 'ALL' && tax !== currentPoiFilter) return false;
        if (search) {
          const nameStr = (typeof p.name === 'object' ? (p.name.es || p.name.en || '') : (p.name || '')).toLowerCase();
          const idStr = (p.id || '').toLowerCase();
          return idStr.includes(search) || nameStr.includes(search);
        }
        return true;
      });

      if (filtered.length === 0) {
        cont.innerHTML = `
          <div class="p-6 text-center text-metro-muted text-xs bg-metro-panel/50 rounded-lg border border-metro-border">
            <span>No se encontraron POIs con los filtros seleccionados.</span>
          </div>
        `;
        return;
      }

      filtered.forEach(poi => {
        const tax = detectPoiTaxonomy(poi.id);
        const meta = TAXONOMY_META[tax] || TAXONOMY_META.AIR;
        const nameStr = typeof poi.name === 'object' ? (poi.name.es || poi.name.en || poi.id) : (poi.name || poi.id);
        const isCurrent = activeEditingPoiIndex === poi.originalIndex;
        const stats = calculatePoiLiveStats(poi);
        const pRad = (poi.radius_m !== undefined && poi.radius_m !== null) ? poi.radius_m : 100;

        const card = document.createElement('div');
        card.className = `p-3 rounded-lg border text-xs transition shadow-sm flex flex-col space-y-2 cursor-pointer ${
          isCurrent ? 'bg-metro-orange/15 border-metro-orange' : 'bg-metro-panel hover:border-metro-orange/60 border-metro-border'
        }`;
        card.onclick = (e) => {
          if (!e.target.closest('button')) {
            openPoiEditor(poi.originalIndex);
            focusPoiOnMap(poi.originalIndex);
          }
        };

        card.innerHTML = `
          <div class="flex justify-between items-start">
            <div class="flex items-center space-x-1.5 truncate mr-2">
              <span class="w-2 h-2 rounded-full shrink-0" style="background-color: ${meta.color}"></span>
              <span class="font-bold text-metro-text truncate font-sans text-xs" title="${nameStr}">${nameStr}</span>
            </div>
            <span class="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold shrink-0 bg-metro-card border border-metro-border text-metro-text">
              ${poi.mode || 'MAX'}
            </span>
          </div>
          <div class="flex justify-between text-[11px] font-mono text-metro-muted">
            <span>ID: <strong class="text-metro-text">${poi.id}</strong></span>
            <span>Radio: <strong class="text-metro-text">${pRad}m</strong></span>
            <span>Final: <strong class="text-metro-green font-bold">${stats.finalJobs.toLocaleString()}</strong></span>
          </div>
          <div class="text-[10px] font-mono text-metro-muted/80 truncate border-t border-metro-border/30 pt-1" title="${stats.formulaText}">
            ${stats.formulaText}
          </div>
          <div class="flex justify-end space-x-1 pt-1 border-t border-metro-border/50">
            <button onclick="copyPoiByIndex(${poi.originalIndex})" class="p-1 rounded text-metro-muted hover:text-metro-orange hover:bg-metro-card transition" title="Copiar al portapapeles (Ctrl+C)">
              <i data-lucide="copy" class="w-3.5 h-3.5"></i>
            </button>
            <button onclick="duplicatePoiByIndex(${poi.originalIndex})" class="p-1 rounded text-metro-muted hover:text-metro-green hover:bg-metro-card transition" title="Duplicar POI">
              <i data-lucide="copy-plus" class="w-3.5 h-3.5"></i>
            </button>
            <button onclick="focusPoiOnMap(${poi.originalIndex})" class="p-1 rounded text-metro-muted hover:text-metro-orange hover:bg-metro-card transition" title="Centrar en mapa">
              <i data-lucide="crosshair" class="w-3.5 h-3.5"></i>
            </button>
            <button onclick="openPoiEditor(${poi.originalIndex})" class="p-1 rounded text-metro-muted hover:text-metro-text hover:bg-metro-card transition" title="Editar en Sidebar">
              <i data-lucide="edit-3" class="w-3.5 h-3.5"></i>
            </button>
            <button onclick="deletePoiByIndex(${poi.originalIndex})" class="p-1 rounded text-metro-muted hover:text-rose-600 hover:bg-metro-card transition" title="Eliminar POI">
              <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
            </button>
          </div>
        `;
        cont.appendChild(card);
      });
      lucide.createIcons();
    }

    function openPoiEditorForNew(coords = null) {
      activeEditingPoiIndex = -1;
      document.getElementById('editorTitleLabel').innerText = "Nuevo POI Especial";
      document.getElementById('editPoiCategory').value = "AIR";
      document.getElementById('editPoiBaseName').value = "";
      document.getElementById('editPoiId').value = "";
      document.getElementById('editPoiNameEs').value = "";
      document.getElementById('editPoiNameEn').value = "";
      document.getElementById('editPoiJobs').value = 0;
      setPoiRadiusValue(100);
      document.getElementById('editPoiMode').value = "MAX";
      document.getElementById('btnDeleteEditorPoi').classList.add('hidden');

      const pt = coords || (mapPoi ? mapPoi.getCenter() : { lng: -86.85, lat: 21.16 });
      const lng = (typeof pt.lng !== 'undefined' ? pt.lng : (pt[0] || 0));
      const lat = (typeof pt.lat !== 'undefined' ? pt.lat : (pt[1] || 0));
      document.getElementById('editPoiLon').value = Number(lng).toFixed(5);
      document.getElementById('editPoiLat').value = Number(lat).toFixed(5);

      onPoiTaxonomyChanged('AIR');
      switchPoiTab('editor');
      updatePoiPreviewFromForm();
    }

    function openPoiEditor(idx) {
      activeEditingPoiIndex = idx;
      const poi = cityData.pois[idx];
      const decomposed = decomposePoiId(poi.id || '');
      const meta = TAXONOMY_META[decomposed.taxonomy] || TAXONOMY_META.AIR;

      document.getElementById('editorTitleLabel').innerText = `Editar: ${poi.id}`;
      document.getElementById('editPoiCategory').value = decomposed.taxonomy;

      const badge = document.getElementById('poiPrefixBadge');
      if (badge) {
        if (meta.prefix) {
          badge.classList.remove('hidden');
          badge.innerText = meta.prefix;
        } else {
          badge.classList.add('hidden');
        }
      }

      const behaviorTag = document.getElementById('poiGameBehaviorTag');
      if (behaviorTag) behaviorTag.innerText = meta.behavior || '';

      document.getElementById('editPoiBaseName').value = decomposed.baseName;
      document.getElementById('editPoiId').value = poi.id || "";
      document.getElementById('editPoiNameEs').value = typeof poi.name === 'object' ? (poi.name.es || "") : (poi.name || "");
      document.getElementById('editPoiNameEn').value = typeof poi.name === 'object' ? (poi.name.en || "") : "";
      document.getElementById('editPoiJobs').value = (poi.jobs !== undefined && poi.jobs !== null) ? poi.jobs : 0;
      setPoiRadiusValue((poi.radius_m !== undefined && poi.radius_m !== null) ? poi.radius_m : 100);
      document.getElementById('editPoiMode').value = poi.mode || "MAX";
      document.getElementById('editPoiLon').value = poi.loc ? poi.loc[0].toFixed(5) : 0;
      document.getElementById('editPoiLat').value = poi.loc ? poi.loc[1].toFixed(5) : 0;
      document.getElementById('btnDeleteEditorPoi').classList.remove('hidden');

      onPoiBaseNameChanged();
      switchPoiTab('editor');
      updatePoiPreviewFromForm();
      renderPoiList();
    }

    function onPoiTaxonomyChanged(val) {
      const meta = TAXONOMY_META[val] || TAXONOMY_META.AIR;
      const badge = document.getElementById('poiPrefixBadge');
      if (badge) {
        if (meta.prefix) {
          badge.classList.remove('hidden');
          badge.innerText = meta.prefix;
        } else {
          badge.classList.add('hidden');
        }
      }

      const behaviorTag = document.getElementById('poiGameBehaviorTag');
      if (behaviorTag) behaviorTag.innerText = meta.behavior || '';

      const baseInput = document.getElementById('editPoiBaseName');
      if (baseInput) {
        baseInput.placeholder = val === 'AIR' ? 'Nombre de la ciudad o terminal' : (val === 'UNI' ? 'Nombre de la universidad' : 'Nombre del lugar');
      }

      if (meta.defaultRadius !== undefined) {
        setPoiRadiusValue(meta.defaultRadius);
      }
      if (meta.defaultJobs !== undefined) {
        document.getElementById('editPoiJobs').value = meta.defaultJobs;
      }

      onPoiBaseNameChanged();
    }

    function onPoiBaseNameChanged() {
      const cat = document.getElementById('editPoiCategory').value;
      const meta = TAXONOMY_META[cat] || TAXONOMY_META.CUSTOM;
      const baseInput = document.getElementById('editPoiBaseName');
      let baseVal = baseInput ? baseInput.value : '';

      // Si el usuario pegó el ID completo con prefijo (ej. AIR_Nombre), extraer solo la base
      if (meta.prefix && baseVal.startsWith(meta.prefix)) {
        baseVal = baseVal.substring(meta.prefix.length);
        if (baseInput) baseInput.value = baseVal;
      }

      const cleanBase = baseVal.trim();
      const fullId = cleanBase ? (meta.prefix ? `${meta.prefix}${cleanBase}` : cleanBase) : '';
      const idInput = document.getElementById('editPoiId');
      if (idInput) idInput.value = fullId;

      const previewEl = document.getElementById('poiIdFullPreview');
      if (previewEl) previewEl.innerText = fullId ? `ID: ${fullId}` : '';

      // Banner de nombre en el juego
      const gameNameEl = document.getElementById('poiGameDisplayName');
      const redundancyWarn = document.getElementById('poiRedundancyWarning');
      const cleanDisplay = cleanBase || 'Sin nombre';

      if (cat === 'AIR') {
        const hasAirportWord = /aeropuerto|airport/i.test(baseVal);
        if (redundancyWarn) {
          redundancyWarn.classList.toggle('hidden', !hasAirportWord);
        }
        if (gameNameEl) {
          gameNameEl.innerText = `✈️ ${cleanDisplay} Terminal`;
        }
      } else {
        if (redundancyWarn) redundancyWarn.classList.add('hidden');
        const iconEmoji = {
          UNI: '🎓 ', SPO: '🏟️ ', TOU: '🏖️ ', MED: '🏥 ', TRA: '🚆 ', CUSTOM: '🏢 '
        }[cat] || '';
        if (gameNameEl) {
          gameNameEl.innerText = `${iconEmoji}${cleanDisplay}`;
        }
      }

      // Validación de unicidad de ID en tiempo real
      const dupWarningEl = document.getElementById('poiIdDuplicateWarning');
      const isDuplicate = fullId ? (cityData?.pois || []).some((p, i) => i !== activeEditingPoiIndex && p.id === fullId) : false;
      if (dupWarningEl) {
        dupWarningEl.classList.toggle('hidden', !isDuplicate);
      }

      updatePoiPreviewFromForm();
    }

    function setPoiRadiusValue(val, sourceInput) {
      let numVal = parseInt(val);
      if (isNaN(numVal) || numVal < 50) numVal = 50;
      if (numVal > 10000) numVal = 10000;

      const sliderEl = document.getElementById('editPoiRadius');
      const numEl = document.getElementById('editPoiRadiusNum');
      const displayEl = document.getElementById('editPoiRadiusDisplay');

      if (sliderEl && sourceInput !== 'slider') {
        sliderEl.value = Math.min(4000, Math.max(100, numVal));
      }
      if (numEl && sourceInput !== 'number') {
        numEl.value = numVal;
      }
      if (displayEl) {
        displayEl.innerText = `${numVal} m`;
      }

      updatePoiPreviewFromForm();
    }

    function onPoiRadiusSlider(val) {
      setPoiRadiusValue(val, 'slider');
    }

    // Compatibilidad retroactiva para cualquier evento enlazado
    function onPoiRadiusSliderChanged(val) {
      setPoiRadiusValue(val, 'slider');
    }

    function onPoiRadiusNumberInput(val) {
      setPoiRadiusValue(val, 'number');
    }

    function updatePoiPreviewFromForm() {
      const lon = parseFloat(document.getElementById('editPoiLon').value);
      const lat = parseFloat(document.getElementById('editPoiLat').value);
      const radius = parseInt(document.getElementById('editPoiRadiusNum')?.value || document.getElementById('editPoiRadius').value) || 750;
      const jobsManual = parseInt(document.getElementById('editPoiJobs').value) || 0;
      const mode = document.getElementById('editPoiMode').value;
      const cat = document.getElementById('editPoiCategory').value;
      const meta = TAXONOMY_META[cat] || TAXONOMY_META.CUSTOM;

      if (isNaN(lon) || isNaN(lat) || !mapPoi) return;

      if (!editorPreviewLayer) {
        editorPreviewLayer = L.layerGroup().addTo(mapPoi);
      }
      editorPreviewLayer.clearLayers();

      const latLng = [lat, lon];
      L.circle(latLng, {
        radius: radius,
        color: meta.color,
        weight: 2,
        fillColor: meta.color,
        fillOpacity: 0.2
      }).addTo(editorPreviewLayer);

      L.circleMarker(latLng, {
        radius: 8,
        color: '#ffffff',
        weight: 2,
        fillColor: meta.color,
        fillOpacity: 1
      }).addTo(editorPreviewLayer);

      // Métricas de Absorción en Vivo contra densityPoints
      let absorbedJobs = 0;
      let absorbedPop = 0;
      let absorbedNodesCount = 0;

      if (densityPoints && densityPoints.length > 0) {
        densityPoints.forEach(p => {
          const d = getDistanceMeters(lon, lat, p.location[0], p.location[1]);
          if (d <= radius) {
            absorbedJobs += (p.jobs || 0);
            absorbedPop += (p.residents || 0);
            absorbedNodesCount++;
          }
        });
      }

      let finalJobs = jobsManual;
      let diagnosis = "";

      if (!densityPoints || densityPoints.length === 0) {
        diagnosis = "⚠️ Sin datos DENUE precargados en memoria. Si aún no descargas datos en el Paso 2, la absorción real se calculará al compilar con los datos de data/.";
      } else if (mode === "MAX") {
        finalJobs = Math.max(jobsManual, absorbedJobs);
        if (absorbedJobs >= jobsManual && jobsManual > 0) {
          diagnosis = `Piso DENUE alcanzado: los ${absorbedJobs.toLocaleString()} empleos locales superan la cuota manual de ${jobsManual.toLocaleString()}.`;
        } else {
          diagnosis = `Cuota manual fijada (${jobsManual.toLocaleString()} empleos): absorbe y consolida ${absorbedJobs.toLocaleString()} locales del DENUE e inyecta ${(jobsManual - absorbedJobs).toLocaleString()} exógenos.`;
        }
      } else if (mode === "BOOST") {
        finalJobs = jobsManual + absorbedJobs;
        diagnosis = `Suma exógena: +${absorbedJobs.toLocaleString()} empleos DENUE sumados sobre la cuota base de ${jobsManual.toLocaleString()} (Total: ${finalJobs.toLocaleString()}).`;
      } else if (mode === "REPLACE") {
        finalJobs = jobsManual;
        diagnosis = `Sobrescritura forzada: cuota fija de ${jobsManual.toLocaleString()} empleos (sustituye y retira ${absorbedJobs.toLocaleString()} empleos DENUE locales).`;
      }

      const elAbsJobs = document.getElementById('statAbsorbedJobs');
      const elAbsPop = document.getElementById('statAbsorbedPop');
      const elAbsNodes = document.getElementById('statAbsorbedNodes');
      const elFinalJobs = document.getElementById('statFinalJobs');
      const elDiagnosis = document.getElementById('statDiagnosisText');
      const modeBadge = document.getElementById('editorStatModeBadge');

      if (modeBadge) modeBadge.innerText = mode;
      if (elAbsJobs) elAbsJobs.innerText = absorbedJobs.toLocaleString();
      if (elAbsPop) elAbsPop.innerText = absorbedPop.toLocaleString();
      if (elAbsNodes) elAbsNodes.innerText = absorbedNodesCount.toLocaleString();
      if (elFinalJobs) elFinalJobs.innerText = finalJobs.toLocaleString();
      if (elDiagnosis) elDiagnosis.innerText = diagnosis || "Ajusta las coordenadas y radio para ver la absorción en vivo.";

      // Calcular y renderizar POIs afectados o solapados
      const affectedListEl = document.getElementById('editorAffectedPoisList');
      const countEl = document.getElementById('editorAffectedPoisCount');
      if (affectedListEl) {
        affectedListEl.innerHTML = '';
        let overlappingCount = 0;
        const pois = cityData.pois || [];
        const affected = [];

        pois.forEach((otherPoi, oIdx) => {
          if (oIdx === activeEditingPoiIndex) return;
          if (!otherPoi.loc || otherPoi.loc.length !== 2) return;
          const dist = getDistanceMeters(lon, lat, otherPoi.loc[0], otherPoi.loc[1]);
          const otherRad = (otherPoi.radius_m !== undefined && otherPoi.radius_m !== null) ? otherPoi.radius_m : 100;
          const overlap = dist < (radius + otherRad);
          const isNear = dist <= (radius + otherRad + 600);

          if (isNear) {
            affected.push({
              poi: otherPoi,
              dist: Math.round(dist),
              overlap: overlap,
              otherRad: otherRad
            });
            if (overlap) overlappingCount++;
          }
        });

        affected.sort((a, b) => a.dist - b.dist);

        if (countEl) {
          if (overlappingCount > 0) {
            countEl.innerText = `${overlappingCount} solapado(s)`;
            countEl.className = "font-mono font-bold text-amber-800 text-[10px]";
          } else if (affected.length > 0) {
            countEl.innerText = `${affected.length} próximo(s)`;
            countEl.className = "font-mono font-medium text-stone-600 text-[10px]";
          } else {
            countEl.innerText = "0 en radio";
            countEl.className = "font-mono text-stone-500 text-[10px]";
          }
        }

        if (affected.length === 0) {
          affectedListEl.innerHTML = `
            <div class="text-[11px] text-emerald-800 bg-emerald-50 border border-emerald-200 px-2.5 py-1.5 rounded-lg flex items-center space-x-1.5">
              <span>&check; Sin interferencia con otros POIs especiales.</span>
            </div>
          `;
        } else {
          affected.forEach(item => {
            const oPoi = item.poi;
            const nameStr = typeof oPoi.name === 'object' ? (oPoi.name.es || oPoi.id) : (oPoi.name || oPoi.id);
            const row = document.createElement('div');
            row.className = `p-2 rounded-lg border text-[11px] flex items-center justify-between transition ${
              item.overlap ? 'bg-amber-50 border-amber-300 text-stone-900' : 'bg-white border-stone-200 text-stone-700'
            }`;
            row.innerHTML = `
              <div class="truncate mr-2">
                <span class="font-bold block truncate text-stone-900">${nameStr}</span>
                <span class="font-mono text-[10px] text-stone-500">${oPoi.id} &bull; radio ${item.otherRad}m</span>
              </div>
              <div class="text-right shrink-0">
                <span class="font-mono font-bold block ${item.overlap ? 'text-amber-800' : 'text-stone-800'}">${item.dist} m</span>
                <span class="text-[9px] font-bold uppercase ${item.overlap ? 'text-amber-800' : 'text-stone-500'}">
                  ${item.overlap ? '⚠️ Solapado' : 'Próximo'}
                </span>
              </div>
            `;
            affectedListEl.appendChild(row);
          });
        }
      }

      // Si estamos editando un POI existente, sincronizarlo en memoria y auto-guardar
      if (activeEditingPoiIndex >= 0 && cityData && cityData.pois && cityData.pois[activeEditingPoiIndex]) {
        const synced = syncActivePoiFromEditor(false);
        if (synced) {
          triggerAutoSave();
        }
      }
    }

    function getDistanceMeters(lon1, lat1, lon2, lat2) {
      const R = 6371000;
      const phi1 = lat1 * Math.PI / 180;
      const phi2 = lat2 * Math.PI / 180;
      const dphi = (lat2 - lat1) * Math.PI / 180;
      const dlam = (lon2 - lon1) * Math.PI / 180;
      const a = Math.sin(dphi / 2) * Math.sin(dphi / 2) +
                Math.cos(phi1) * Math.cos(phi2) *
                Math.sin(dlam / 2) * Math.sin(dlam / 2);
      return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    function savePoiFromEditor() {
      const pCat = document.getElementById('editPoiCategory').value;
      const meta = TAXONOMY_META[pCat] || TAXONOMY_META.AIR;
      const baseName = (document.getElementById('editPoiBaseName')?.value || '').trim();

      if (!baseName) {
        showToast("El nombre del POI no puede estar vacío", "error");
        document.getElementById('editPoiBaseName')?.focus();
        return;
      }

      const pId = meta.prefix ? `${meta.prefix}${baseName}` : baseName;
      document.getElementById('editPoiId').value = pId;

      const isDuplicate = (cityData?.pois || []).some((p, i) => i !== activeEditingPoiIndex && p.id === pId);
      if (isDuplicate) {
        showToast(`Error: El ID "${pId}" ya pertenece a otro POI. Cada POI debe tener un ID único.`, "error");
        document.getElementById('editPoiBaseName')?.focus();
        return;
      }

      const esName = document.getElementById('editPoiNameEs').value.trim();
      const enName = document.getElementById('editPoiNameEn').value.trim();
      const pMode = document.getElementById('editPoiMode').value;
      const rawJobs = parseInt(document.getElementById('editPoiJobs').value, 10);
      const pJobs = isNaN(rawJobs) ? 0 : Math.max(0, rawJobs);
      const rawRad = parseInt(document.getElementById('editPoiRadiusNum')?.value || document.getElementById('editPoiRadius')?.value, 10);
      const pRad = isNaN(rawRad) ? 100 : Math.max(50, rawRad);
      const lon = parseFloat(document.getElementById('editPoiLon').value);
      const lat = parseFloat(document.getElementById('editPoiLat').value);

      if (isNaN(lon) || isNaN(lat)) {
        showToast("Coordenadas inválidas", "error");
        return;
      }

      let nameVal = esName || baseName;
      if (enName) {
        nameVal = { es: esName || baseName, en: enName };
      }

      const poiObj = {
        id: pId,
        name: nameVal,
        type: pCat.toLowerCase(),
        mode: pMode,
        jobs: pJobs,
        radius_m: pRad,
        loc: [lon, lat]
      };

      if (!cityData.pois) cityData.pois = [];
      if (activeEditingPoiIndex >= 0) {
        cityData.pois[activeEditingPoiIndex] = poiObj;
      } else {
        cityData.pois.push(poiObj);
        activeEditingPoiIndex = cityData.pois.length - 1;
      }

      if (editorPreviewLayer) editorPreviewLayer.clearLayers();
      renderPoiList();
      renderPoiMarkersOnMap();

      if (autoSaveTimer) {
        clearTimeout(autoSaveTimer);
        autoSaveTimer = null;
      }
      saveCurrentCity(true);
      showToast(`POI '${pId}' guardado correctamente`, "success");
      activeEditingPoiIndex = -1;
      switchPoiTab('list');
    }

    function deleteCurrentEditorPoi() {
      if (activeEditingPoiIndex >= 0) {
        deletePoiByIndex(activeEditingPoiIndex);
      }
    }

    let lastDeletedPoiInfo = null;
    let activeUndoToast = null;

    function undoDeletePoi() {
      if (!lastDeletedPoiInfo || !lastDeletedPoiInfo.poi) {
        showToast("No hay ningún POI para restaurar", "info");
        return;
      }

      const item = lastDeletedPoiInfo;
      lastDeletedPoiInfo = null;

      if (!cityData) cityData = { city: {}, macroeconomics: {}, pois: [] };
      if (!cityData.pois) cityData.pois = [];

      const insertIdx = Math.min(item.index, cityData.pois.length);
      cityData.pois.splice(insertIdx, 0, item.poi);

      if (activeUndoToast) {
        activeUndoToast.remove();
        activeUndoToast = null;
      }

      renderPoiList();
      renderPoiMarkersOnMap();
      openPoiEditor(insertIdx);
      focusPoiOnMap(insertIdx);

      if (autoSaveTimer) {
        clearTimeout(autoSaveTimer);
        autoSaveTimer = null;
      }
      saveCurrentCity(true);
      showToast(`↺ POI '${item.poi.id}' restaurado`, "success");
    }

    function deletePoiByIndex(idx) {
      const poi = cityData.pois[idx];
      if (!poi) return;

      // Guardar copia para Deshacer
      lastDeletedPoiInfo = {
        poi: JSON.parse(JSON.stringify(poi)),
        index: idx,
        timestamp: Date.now()
      };

      switchPoiTab('list');
      activeEditingPoiIndex = -1;
      cityData.pois.splice(idx, 1);
      if (editorPreviewLayer) editorPreviewLayer.clearLayers();
      renderPoiList();
      renderPoiMarkersOnMap();

      if (autoSaveTimer) {
        clearTimeout(autoSaveTimer);
        autoSaveTimer = null;
      }
      saveCurrentCity(true);

      if (activeUndoToast) {
        activeUndoToast.remove();
      }

      activeUndoToast = showToast(`🗑️ POI '${poi.id}' eliminado`, "info", 7000, {
        label: "↺ Deshacer (Ctrl+Z)",
        callback: () => undoDeletePoi()
      });
    }

    function focusPoiOnMap(idx) {
      const poi = cityData.pois[idx];
      if (!poi || !poi.loc || !mapPoi) return;
      mapPoi.flyTo([poi.loc[1], poi.loc[0]], 14, { duration: 0.8 });
    }

    // -------------------------------------------------------------------------
    // ATAJOS DE TECLADO PARA POIS (CTRL+C / CTRL+V / SUPR / CTRL+Z)
    // -------------------------------------------------------------------------
    function isEditableTextInput(el) {
      if (!el) return false;
      const tag = el.tagName ? el.tagName.toLowerCase() : '';
      if (tag === 'textarea') return true;
      if (tag === 'input') {
        const type = (el.type || 'text').toLowerCase();
        return ['text', 'search', 'number', 'password', 'email', 'url', 'tel'].includes(type);
      }
      return el.isContentEditable === true;
    }

    document.addEventListener('keydown', (e) => {
      const isCtrlOrMeta = e.ctrlKey || e.metaKey;
      const key = e.key.toLowerCase();

      // Atajos con modificador Ctrl / Cmd
      if (isCtrlOrMeta) {
        if (key === 's') {
          e.preventDefault();
          saveCurrentCity(false);
          return;
        } else if (key === 'c') {
          if (isEditableTextInput(document.activeElement)) {
            const input = document.activeElement;
            if (input.selectionStart !== input.selectionEnd) {
              return; // Copia normal de texto seleccionado
            }
          }
          if (activeEditingPoiIndex >= 0 && cityData && cityData.pois && cityData.pois[activeEditingPoiIndex]) {
            copyPoiByIndex(activeEditingPoiIndex);
          }
        } else if (key === 'v') {
          if (isEditableTextInput(document.activeElement)) {
            return; // Pegado normal dentro de inputs
          }
          if (currentStep === 4 || activeEditingPoiIndex >= 0) {
            e.preventDefault();
            pastePoi();
          }
        } else if (key === 'z') {
          if (isEditableTextInput(document.activeElement)) {
            return; // Deshacer nativo en campos de texto
          }
          if (lastDeletedPoiInfo) {
            e.preventDefault();
            undoDeletePoi();
          }
        }
        return;
      }

      // Tecla Escape para cerrar modales o cancelar modos de dibujo / selección
      if (key === 'escape') {
        if (typeof isDrawingIsolated !== 'undefined' && isDrawingIsolated) {
          cancelDrawingIsolatedZone();
          return;
        }
        if (typeof isDrawingAffluence !== 'undefined' && isDrawingAffluence) {
          cancelDrawingAffluenceZone();
          return;
        }
        if (typeof isPickingLocation !== 'undefined' && isPickingLocation) {
          isPickingLocation = false;
          if (mapPoi) mapPoi.getContainer().style.cursor = '';
          showToast("Selección de ubicación cancelada", "info");
          return;
        }
        const modalIds = ['modalProjects', 'modalDataSources', 'modalChangeDataDir', 'modalIsolatedZone', 'modalAffluenceZone', 'modalAddGrowthFactor'];
        for (const mid of modalIds) {
          const m = document.getElementById(mid);
          if (m && !m.classList.contains('hidden')) {
            if (mid === 'modalProjects') {
              if (currentCityFile) closeProjectsModal();
            } else {
              m.classList.add('hidden');
            }
            return;
          }
        }
      }

      // Teclas directas: Supr / Delete para eliminar POI seleccionado
      if (key === 'delete' || key === 'supr') {
        if (isEditableTextInput(document.activeElement)) {
          return; // Borrado normal de caracteres en inputs
        }
        if (currentStep === 4 || activeEditingPoiIndex >= 0) {
          if (activeEditingPoiIndex >= 0 && cityData && cityData.pois && cityData.pois[activeEditingPoiIndex]) {
            e.preventDefault();
            deletePoiByIndex(activeEditingPoiIndex);
          }
        }
      }
    });

    function startPickingPoiLocation() {
      isPickingLocation = true;
      mapPoi.getContainer().style.cursor = 'crosshair';
      showToast("Haz clic en cualquier punto del mapa para posicionar el POI", "info");
    }

    function createPoiIcon(cat) {
      const meta = TAXONOMY_META[cat] || TAXONOMY_META.AIR;
      const iconEmoji = meta.icon === 'plane' ? '✈️' : (meta.icon === 'graduation-cap' ? '🎓' : '📍');
      return L.divIcon({
        className: 'custom-poi-marker',
        html: `<div class="poi-marker-icon" style="background-color: ${meta.color}; display:flex; align-items:center; justify-content:center; border-radius:50%; width:28px; height:28px; color:white; font-size:14px; box-shadow:0 2px 6px rgba(0,0,0,0.4);">${iconEmoji}</div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14]
      });
    }

    function renderPoiMarkersOnMap() {
      if (!mapPoi || !poiMarkersGroup) return;
      poiMarkersGroup.clearLayers();
      const pois = cityData.pois || [];

      pois.forEach((poi, idx) => {
        if (!poi.loc || poi.loc.length !== 2) return;
        const latLng = [poi.loc[1], poi.loc[0]];
        const tax = detectPoiTaxonomy(poi.id);
        const meta = TAXONOMY_META[tax] || TAXONOMY_META.AIR;
        const pRad = (poi.radius_m !== undefined && poi.radius_m !== null) ? poi.radius_m : 100;

        const circle = L.circle(latLng, {
          radius: pRad,
          color: meta.color,
          weight: 1.5,
          fillColor: meta.color,
          fillOpacity: 0.16
        }).addTo(poiMarkersGroup);

        const marker = L.marker(latLng, {
          icon: createPoiIcon(tax),
          draggable: true
        }).addTo(poiMarkersGroup);

        const nameStr = typeof poi.name === 'object' ? (poi.name.es || poi.id) : (poi.name || poi.id);
        const stats = calculatePoiLiveStats(poi);

        let formulaBadgeHtml = '';
        if (stats.mode === 'BOOST') {
          formulaBadgeHtml = `
            <div class="text-[10px] font-mono text-stone-600 pt-0.5 bg-stone-50 rounded px-1.5 py-0.5 border border-stone-200">
              Manual: <strong>${stats.manual.toLocaleString()}</strong> + DENUE: <strong class="text-blue-600">+${stats.absorbed.toLocaleString()}</strong>
            </div>
          `;
        } else if (stats.mode === 'MAX') {
          formulaBadgeHtml = `
            <div class="text-[10px] font-mono text-stone-600 pt-0.5 bg-stone-50 rounded px-1.5 py-0.5 border border-stone-200">
              Piso DENUE: <strong>${stats.absorbed.toLocaleString()}</strong> | Base: <strong>${stats.manual.toLocaleString()}</strong>
            </div>
          `;
        } else if (stats.mode === 'REPLACE') {
          formulaBadgeHtml = `
            <div class="text-[10px] font-mono text-stone-600 pt-0.5 bg-stone-50 rounded px-1.5 py-0.5 border border-stone-200">
              Fijo manual (sustituye <strong>${stats.absorbed.toLocaleString()}</strong> DENUE)
            </div>
          `;
        }

        const toastHtml = `
          <div class="p-2.5 bg-white text-stone-900 rounded-lg shadow-xl text-xs space-y-1 min-w-[210px]">
            <div class="flex items-center justify-between space-x-2">
              <span class="font-bold text-stone-900 truncate">${nameStr}</span>
              <span class="px-1.5 py-0.2 rounded text-[10px] font-mono font-bold" style="background: ${meta.color}22; color: ${meta.color}; border: 1px solid ${meta.color}66">${poi.mode || 'MAX'}</span>
            </div>
            <div class="text-[11px] font-mono text-stone-600">ID: <strong class="text-stone-900">${poi.id}</strong></div>
            <div class="flex justify-between text-[11px] font-mono text-stone-700 pt-1 border-t border-stone-200">
              <span>Radio: <strong class="text-stone-900">${pRad}m</strong></span>
              <span>Final: <strong class="text-emerald-700 font-bold">${stats.finalJobs.toLocaleString()}</strong></span>
            </div>
            ${formulaBadgeHtml}
            <div class="text-[10px] text-stone-500 pt-1 border-t border-stone-200 flex items-center justify-between">
              <span>📍 Arrastra para reubicar</span>
              <span>🖱️ Clic para editar</span>
            </div>
          </div>
        `;

        marker.bindTooltip(toastHtml, {
          direction: 'top',
          offset: [0, -16],
          className: 'poi-custom-tooltip'
        });

        marker.on('drag', (e) => {
          const newPos = e.target.getLatLng();
          circle.setLatLng(newPos);
          poi.loc = [parseFloat(newPos.lng.toFixed(5)), parseFloat(newPos.lat.toFixed(5))];
          if (activeEditingPoiIndex === idx) {
            const elLon = document.getElementById('editPoiLon');
            const elLat = document.getElementById('editPoiLat');
            if (elLon) elLon.value = poi.loc[0];
            if (elLat) elLat.value = poi.loc[1];
            updatePoiPreviewFromForm();
          }
        });

        marker.on('dragend', () => {
          triggerAutoSave();
        });

        marker.on('click', (e) => {
          L.DomEvent.stopPropagation(e);
          openPoiEditor(idx);
          focusPoiOnMap(idx);
        });
      });
    }

    // =========================================================================
    // TOPONIMIA, COLONIAS Y HOMOGENEIZADOR URBANO (PLACES STUDIO PRO - ULTRA OPTIMIZADO)
    // =========================================================================

    // SVGs estáticos en línea para evitar recargas y reflows masivos de lucide.createIcons()
    const SVG_PLACE_CROSSHAIR = '<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="22" y1="12" x2="18" y2="12"/><line x1="6" y1="12" x2="2" y2="12"/><line x1="12" y1="6" x2="12" y2="2"/><line x1="12" y1="22" x2="12" y2="18"/></svg>';
    const SVG_PLACE_TRASH = '<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>';
    const SVG_PLACE_PIN = '<svg class="w-2.5 h-2.5 text-sky-600 inline" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg>';
    const SVG_PLACE_PIN_MD = '<svg class="w-3 h-3 text-sky-600 inline" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg>';

    let placesHistorySnapshot = null;
    let forceShowPlaceLabels = false;
    let inGameSimulationMode = false;
    let placeDisplayMode = 'pill'; // 'pill', 'ingame', 'halo', 'dots'
    let placeFontScale = 1.0; // 0.75 a 1.80
    let placeMarkerMap = new Map(); // idx -> L.marker
    let tsSearchQuery = '';
    let tsScaleFilterVal = 'all';
    let placesRenderLimit = 80;
    const PLACES_CHUNK_SIZE = 60;

    function isMicroPlace(p) {
      if (!p) return false;
      if (p.is_micro) return true;
      if (p.category === 'PRIVADA_CERRADA') return true;
      const nm = (p.name || '').trim().toLowerCase();
      return /\b(cerrada|cda\.?|privada|priv\.?|retorno|ret\.?|callej[oó]n|andador|and\.?|pasaje|pasillo)\b/i.test(nm);
    }

    function getPlaceScale(type) {
      const t = String(type || '').trim().toLowerCase();
      if (t === 'city' || t === 'town' || t === 'borough' || t === 'large') return 'large';
      if (t === 'neighbourhood' || t === 'neighborhood' || t === 'village' || t === 'hamlet' || t === 'isolated_dwelling' || t === 'small') return 'small';
      return 'medium';
    }

    function getPlaceTaxonomyColor(name, type) {
      const n = (name || '').toLowerCase();
      if (/supermanzana|s\.?m\.?|smz|regi[oó]n|\br-\d+/i.test(n)) return '#0284c7'; // Azul cielo (Supermanzanas)
      if (/fracc|fraccionamiento|residencial|privada/i.test(n)) return '#d97706'; // Ámbar (Fraccionamientos)
      if (/barrio|pueblo|ejido|u\.?h\.?|unidad\s+habitacional/i.test(n)) return '#7c3aed'; // Púrpura (Barrios/UH)
      return '#059669'; // Esmeralda (Colonias estándar)
    }

    function createPlaceIcon(name, type, isSelected = false, isCompactDot = false) {
      const scale = getPlaceScale(type);
      const safeName = escapeHtml(name || 'Colonia');
      const safeNameUpper = safeName.toUpperCase();
      const selClass = isSelected ? 'selected' : '';
      const scaleEmoji = scale === 'large' ? '👑' : (scale === 'small' ? '🏘️' : '🏙️');
      const color = getPlaceTaxonomyColor(name, type);

      // Si el modo forzado es puntos compactos, o el LOD requiere punto compacto
      if (placeDisplayMode === 'dots' || isCompactDot) {
        return L.divIcon({
          className: 'place-dot-wrapper',
          html: `<div class="place-marker-dot ${selClass}" style="transform: translate(-50%, -50%) scale(${placeFontScale});">
                   <span class="place-dot-core" style="background-color: ${color};"></span>
                 </div>`,
          iconSize: [14, 14],
          iconAnchor: [7, 7]
        });
      }

      // Modo In-Game Subway Builder
      if (placeDisplayMode === 'ingame') {
        const scaleClass = scale === 'large' ? 'ingame-large' : (scale === 'medium' ? 'ingame-medium' : 'ingame-small');
        const baseSize = scale === 'large' ? 17 : (scale === 'medium' ? 13 : 10.5);
        const fontSize = (baseSize * placeFontScale).toFixed(1);
        return L.divIcon({
          className: 'place-ingame-wrapper',
          html: `<div class="place-marker-ingame ${scaleClass} ${selClass}" style="font-size: ${fontSize}px !important;">${safeNameUpper}</div>`,
          iconSize: null,
          iconAnchor: [0, 0]
        });
      }

      // Modo Halo Cartográfico Minimalista
      if (placeDisplayMode === 'halo') {
        const scaleClass = `scale-${scale}`;
        const baseSize = scale === 'large' ? 16 : (scale === 'medium' ? 12.5 : 10);
        const fontSize = (baseSize * placeFontScale).toFixed(1);
        return L.divIcon({
          className: 'place-halo-wrapper',
          html: `<div class="place-marker-halo ${scaleClass} ${selClass}" style="font-size: ${fontSize}px !important;">
                   <span style="font-size: ${Math.round(fontSize * 0.9)}px;">${scaleEmoji}</span>
                   <span>${safeNameUpper}</span>
                 </div>`,
          iconSize: null,
          iconAnchor: [0, 0]
        });
      }

      // Modo Píldora Editorial (Predeterminado - Alto Contraste y Sin Recortes Rígidos)
      const scaleClass = `scale-${scale}`;
      const baseFontSize = scale === 'large' ? 13.5 : (scale === 'medium' ? 11.5 : 10);
      const fontSize = (baseFontSize * placeFontScale).toFixed(1);
      return L.divIcon({
        className: 'place-marker-wrapper',
        html: `<div class="place-marker-pill ${scaleClass} ${selClass}" style="border-left: 3.5px solid ${color} !important; font-size: ${fontSize}px !important;">
                 <span style="font-size: ${Math.round(fontSize * 1.05)}px;">${scaleEmoji}</span>
                 <span class="place-marker-text">${safeName}</span>
               </div>`,
        iconSize: null,
        iconAnchor: [12, 10]
      });
    }

    function setPlaceDisplayMode(mode) {
      placeDisplayMode = mode;
      inGameSimulationMode = (mode === 'ingame');
      ['pill', 'ingame', 'halo', 'dots'].forEach(m => {
        const btn = document.getElementById(`btnDisplayMode-${m}`);
        if (btn) {
          if (m === mode) {
            btn.className = "place-mode-chip py-1 px-1 rounded-lg text-center transition bg-sky-800 text-white shadow-xs font-bold cursor-pointer ring-1 ring-sky-300";
          } else {
            btn.className = "place-mode-chip py-1 px-1 rounded-lg text-center transition bg-stone-200 text-stone-700 hover:bg-stone-300 font-bold cursor-pointer";
          }
        }
      });
      renderPlacesMarkersOnMap();
      renderPlacesList();
      const modeNames = { pill: '🏷️ Píldoras Editoriales', ingame: '🎮 In-Game Subway Builder', halo: '🎯 Halo Cartográfico Minimalista', dots: '🔘 Puntos de Densidad' };
      showToast(`Modo de mapa: ${modeNames[mode] || mode}`, "info", 1800);
    }

    function changePlaceFontScale(delta) {
      placeFontScale = Math.max(0.75, Math.min(1.80, Math.round((placeFontScale + delta) * 100) / 100));
      const badge = document.getElementById('placeFontScaleBadge');
      if (badge) badge.innerText = `${Math.round(placeFontScale * 100)}%`;
      renderPlacesMarkersOnMap();
      showToast(`Tamaño de rótulo en mapa: ${Math.round(placeFontScale * 100)}%`, "info", 1500);
    }

    function fitMapToPlaces() {
      const places = cityData.places || [];
      if (places.length === 0 || !mapPoi) {
        showToast("No hay colonias registradas para encuadrar", "warning");
        return;
      }
      const latLngs = places.filter(p => p.loc && p.loc.length === 2).map(p => [p.loc[1], p.loc[0]]);
      if (latLngs.length > 0) {
        mapPoi.fitBounds(latLngs, { padding: [50, 50], maxZoom: 16 });
        showToast(`🗺️ Mapa encuadrado a ${latLngs.length} asentamientos`, "info", 2000);
      }
    }

    function hoverHighlightPlaceMarker(idx, isHover) {
      const marker = placeMarkerMap.get(idx);
      if (!marker) return;
      const el = marker.getElement();
      if (!el) return;
      const child = el.querySelector('.place-marker-pill, .place-marker-halo, .place-marker-ingame, .place-marker-dot');
      if (child) {
        if (isHover) {
          child.classList.add('highlighted');
          marker.setZIndexOffset(10000);
        } else {
          child.classList.remove('highlighted');
          marker.setZIndexOffset(0);
        }
      }
    }

    function toggleInGameSimulation() {
      if (placeDisplayMode === 'ingame') {
        setPlaceDisplayMode('pill');
      } else {
        setPlaceDisplayMode('ingame');
      }
    }

    function togglePlaceLabels() {
      forceShowPlaceLabels = !forceShowPlaceLabels;
      if (forceShowPlaceLabels) {
        showToast("🏷️ Rótulos forzados visibles en todos los niveles de zoom", "info");
      } else {
        showToast("✨ Modo adaptativo inteligente activo (según zoom y jerarquía)", "info");
      }
      renderPlacesMarkersOnMap();
    }

    function undoPlacesLastAction() {
      if (!placesHistorySnapshot) return;
      cityData.places = JSON.parse(JSON.stringify(placesHistorySnapshot));
      cityData.deleted_places = (cityData.deleted_places || []).filter(deleted =>
        !cityData.places.some(p => typeof deleted === 'string' ?
          toponymyNameKey(deleted) === toponymyNameKey(p.name) : sameToponymyPlace(deleted, p)));
      placesHistorySnapshot = null;
      updateUndoButtonUI();
      selectedPlaceIndices.clear();
      renderPlacesList();
      renderPlacesMarkersOnMap();
      if (document.getElementById('modalToponymyStudio') && !document.getElementById('modalToponymyStudio').classList.contains('hidden')) {
        renderToponymyStudioTable();
      }
      triggerAutoSave();
      showToast("↩️ Se restauró la toponimia al estado anterior", "info");
    }

    function updateUndoButtonUI() {
      const btn = document.getElementById('btnUndoPlaces');
      if (btn) {
        if (placesHistorySnapshot && Array.isArray(placesHistorySnapshot)) {
          btn.classList.remove('hidden');
        } else {
          btn.classList.add('hidden');
        }
      }
    }

    function renderPlacesMarkersOnMap() {
      if (!mapPoi || !placesMarkersGroup) return;
      placesMarkersGroup.clearLayers();
      placeMarkerMap.clear();
      const places = cityData.places || [];
      if (places.length === 0) return;

      const currentZoom = mapPoi.getZoom ? mapPoi.getZoom() : 11;
      const bounds = (places.length > 100 && mapPoi.getBounds) ? mapPoi.getBounds().pad(0.35) : null;

      places.forEach((pl, idx) => {
        if (!pl.loc || pl.loc.length !== 2) return;
        const latLng = [pl.loc[1], pl.loc[0]];

        // Viewport culling para optimización extrema de FPS en mapas metropolitanos
        if (bounds && !bounds.contains(latLng) && !selectedPlaceIndices.has(idx)) {
          return;
        }

        const scale = getPlaceScale(pl.type);

        let useCompactDot = false;
        if (placeDisplayMode === 'dots') {
          useCompactDot = true;
        } else if (placeDisplayMode === 'ingame') {
          if (currentZoom < 12 && scale !== 'large') return;
          if (currentZoom < 14 && scale === 'small') return;
        } else if (!forceShowPlaceLabels) {
          if (currentZoom < 12.0) {
            useCompactDot = (scale !== 'large');
          } else if (currentZoom < 13.5) {
            useCompactDot = (scale === 'small');
          } else {
            useCompactDot = false;
          }
        }

        const isSel = selectedPlaceIndices.has(idx);

        const marker = L.marker(latLng, {
          icon: createPlaceIcon(pl.name, pl.type, isSel, useCompactDot),
          draggable: !useCompactDot
        }).addTo(placesMarkersGroup);

        placeMarkerMap.set(idx, marker);

        const color = getPlaceTaxonomyColor(pl.name, pl.type);
        const scaleLabel = scale === 'large' ? '👑 Grande (city)' : (scale === 'small' ? '🏘️ Chico (neighbourhood)' : '🏙️ Mediano (suburb)');
        const tooltipHtml = `
          <div class="p-2 bg-white text-stone-900 rounded-lg shadow-xl text-xs space-y-1 min-w-[180px] border border-stone-200 font-sans">
            <div class="flex items-center justify-between space-x-2">
              <strong class="text-stone-900 truncate flex items-center space-x-1">
                <span class="w-2 h-2 rounded-full inline-block shrink-0" style="background-color: ${color};"></span>
                <span>${escapeHtml(pl.name || 'Sin Nombre')}</span>
              </strong>
              <span class="px-1.5 py-0.5 rounded text-[9px] font-mono bg-stone-100 text-stone-700 border border-stone-300 font-bold">${scaleLabel}</span>
            </div>
            <div class="text-[10px] font-mono text-stone-500">Coord: [${pl.loc[0].toFixed(5)}, ${pl.loc[1].toFixed(5)}]</div>
            ${pl.source ? `<div class="text-[10px] text-stone-500">Origen: <strong>${escapeHtml(pl.source)}</strong></div>` : ''}
            <div class="text-[10px] text-stone-400 pt-1 border-t border-stone-100 flex items-center justify-between">
              <span>📍 Arrastra para ajustar</span>
              <span>🔍 Zoom: ${currentZoom.toFixed(1)}</span>
            </div>
          </div>
        `;
        marker.bindTooltip(tooltipHtml, {
          direction: 'top',
          offset: useCompactDot ? [0, -6] : [0, -10],
          className: 'poi-custom-tooltip'
        });

        // Asociar popup interactivo para edición y borrado directo desde el mapa
        marker.bindPopup(createPlacePopupHtml(pl, idx), {
          maxWidth: 340,
          minWidth: 270,
          className: 'place-edit-popup',
          offset: useCompactDot ? [0, -6] : [0, -10]
        });

        marker.on('popupopen', () => {
          setTimeout(() => {
            const inp = document.getElementById(`popupPlaceNameInput_${idx}`);
            if (inp) {
              inp.focus();
              inp.select();
            }
            if (window.lucide) lucide.createIcons();
          }, 60);
        });

        if (!useCompactDot) {
          marker.on('drag', (e) => {
            const newPos = e.target.getLatLng();
            pl.loc = [parseFloat(newPos.lng.toFixed(5)), parseFloat(newPos.lat.toFixed(5))];
            const coordEl = document.getElementById(`placeCoordBadge_${idx}`);
            if (coordEl) {
              coordEl.innerText = `${pl.loc[0].toFixed(3)}, ${pl.loc[1].toFixed(3)}`;
            }
          });

          marker.on('dragend', () => {
            triggerAutoSave();
          });
        }

        marker.on('click', (e) => {
          L.DomEvent.stopPropagation(e);
          marker.openPopup();
          highlightPlaceRowInList(idx);
          if (useCompactDot) {
            mapPoi.flyTo(latLng, Math.max(14, currentZoom), { duration: 0.5 });
          }
        });
      });
    }

    function createPlacePopupHtml(pl, idx) {
      const scale = getPlaceScale(pl.type);
      const scaleEmoji = scale === 'large' ? '👑' : (scale === 'small' ? '🏘️' : '🏙️');
      const scaleLabel = scale === 'large' ? '👑 Ciudad (Grande)' : (scale === 'small' ? '🏘️ Colonia (Chico)' : '🏙️ Distrito (Mediano)');
      const safeName = escapeHtml(pl.name || '');
      const loc = pl.loc || [0, 0];
      const color = getPlaceTaxonomyColor(pl.name, pl.type);

      return `
        <div class="p-3 bg-white text-stone-900 rounded-xl shadow-2xl text-xs space-y-2.5 min-w-[270px] max-w-[320px] font-sans border border-stone-200" onclick="event.stopPropagation();">
          <!-- Encabezado con Icono y Coordenadas -->
          <div class="flex items-center justify-between border-b border-stone-100 pb-1.5">
            <div class="flex items-center space-x-1.5">
              <span class="w-2.5 h-2.5 rounded-full inline-block shrink-0" style="background-color: ${color};"></span>
              <span class="font-bold text-stone-800 text-[11px]">${scaleLabel}</span>
            </div>
            <span class="text-[9px] font-mono text-stone-400 font-bold">[${loc[0].toFixed(3)}, ${loc[1].toFixed(3)}]</span>
          </div>

          <!-- Input de Edición de Nombre con Guardado al Presionar Enter o Botón -->
          <div class="space-y-1">
            <label class="text-[10px] font-bold text-stone-600 block">Editar Nombre en Mapa:</label>
            <div class="flex items-center space-x-1">
              <input type="text" id="popupPlaceNameInput_${idx}" value="${safeName}"
                     class="flex-1 px-2.5 py-1.5 text-xs font-bold bg-stone-50 border border-stone-300 rounded-lg focus:outline-none focus:border-metro-orange focus:bg-white text-stone-900 shadow-2xs"
                     placeholder="Nombre de la colonia..."
                     onkeydown="if(event.key==='Enter') savePlacePopupName(${idx})">
              <button type="button" onclick="savePlacePopupName(${idx})"
                      class="px-2.5 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white font-bold rounded-lg transition shadow-2xs text-xs flex items-center space-x-1 cursor-pointer shrink-0" title="Guardar cambios de nombre">
                <i data-lucide="check" class="w-3.5 h-3.5"></i>
                <span>Listo</span>
              </button>
            </div>
          </div>

          <!-- Selector Rápido de Jerarquía -->
          <div class="space-y-1">
            <label class="text-[10px] font-bold text-stone-600 block">Jerarquía de Rótulo:</label>
            <div class="grid grid-cols-3 gap-1 text-[10px] font-bold">
              <button type="button" onclick="setPlaceScaleFromMap(${idx}, 'city')"
                      class="py-1 px-1 rounded-lg border text-center transition cursor-pointer ${scale === 'large' ? 'bg-amber-100 border-amber-400 text-amber-900 font-black ring-1 ring-amber-300 shadow-2xs' : 'bg-stone-50 border-stone-200 text-stone-600 hover:bg-stone-100'}">
                👑 Grande
              </button>
              <button type="button" onclick="setPlaceScaleFromMap(${idx}, 'suburb')"
                      class="py-1 px-1 rounded-lg border text-center transition cursor-pointer ${scale === 'medium' ? 'bg-sky-100 border-sky-400 text-sky-900 font-black ring-1 ring-sky-300 shadow-2xs' : 'bg-stone-50 border-stone-200 text-stone-600 hover:bg-stone-100'}">
                🏙️ Mediano
              </button>
              <button type="button" onclick="setPlaceScaleFromMap(${idx}, 'neighbourhood')"
                      class="py-1 px-1 rounded-lg border text-center transition cursor-pointer ${scale === 'small' ? 'bg-emerald-100 border-emerald-400 text-emerald-900 font-black ring-1 ring-emerald-300 shadow-2xs' : 'bg-stone-50 border-stone-200 text-stone-600 hover:bg-stone-100'}">
                🏘️ Chico
              </button>
            </div>
          </div>

          <!-- Botones de Acción: Eliminar y Localizar en Panel Lateral -->
          <div class="flex items-center justify-between pt-2 border-t border-stone-100 text-[11px]">
            <button type="button" onclick="deletePlaceFromMap(${idx})"
                    class="px-2.5 py-1 bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 rounded-lg font-bold flex items-center space-x-1 transition cursor-pointer"
                    title="Eliminar colonia desde el mapa">
              <i data-lucide="trash-2" class="w-3 h-3 text-rose-600"></i>
              <span>Borrar Colonia</span>
            </button>
            <button type="button" onclick="highlightPlaceRowInList(${idx}); if(mapPoi) mapPoi.closePopup();"
                    class="text-[10px] text-indigo-600 hover:text-indigo-800 hover:underline font-bold flex items-center space-x-0.5 cursor-pointer"
                    title="Enfocar en la lista del panel lateral">
              <span>Ver en lista</span>
              <span>&rarr;</span>
            </button>
          </div>
        </div>
      `;
    }

    function savePlacePopupName(idx) {
      const inp = document.getElementById(`popupPlaceNameInput_${idx}`);
      if (!inp || !cityData.places || !cityData.places[idx]) return;
      const newName = inp.value.trim();
      if (!newName) {
        showToast("El nombre de la colonia no puede estar vacío", "warning");
        return;
      }
      placesHistorySnapshot = JSON.parse(JSON.stringify(cityData.places));
      updateUndoButtonUI();
      if (!cityData.places[idx].original_name) cityData.places[idx].original_name = cityData.places[idx].name;
      cityData.places[idx].name = newName;

      // Sincronizar input en la tarjeta de la lista lateral si está visible
      const listInp = document.querySelector(`#placeRow_${idx} input[type="text"]`);
      if (listInp) listInp.value = newName;

      if (mapPoi) mapPoi.closePopup();
      renderPlacesMarkersOnMap();
      triggerAutoSave();
      showToast(`✓ Nombre actualizado: "${newName}"`, "success", 2000);
    }

    function setPlaceScaleFromMap(idx, newType) {
      if (!cityData.places || !cityData.places[idx]) return;
      placesHistorySnapshot = JSON.parse(JSON.stringify(cityData.places));
      updateUndoButtonUI();
      cityData.places[idx].type = newType;
      if (mapPoi) mapPoi.closePopup();
      renderPlacesList();
      renderPlacesMarkersOnMap();
      triggerAutoSave();
      const scaleLabel = newType === 'city' ? '👑 Grande' : (newType === 'suburb' ? '🏙️ Mediano' : '🏘️ Chico');
      showToast(`✓ Jerarquía cambiada a ${scaleLabel}`, "success", 2000);
    }

    function deletePlaceFromMap(idx) {
      if (!cityData.places || !cityData.places[idx]) return;
      const plName = cityData.places[idx].name || 'esta colonia';
      if (!confirm(`¿Deseas eliminar la colonia "${plName}" directamente desde el mapa?\n\nPuedes revertir este cambio inmediatamente con el botón Deshacer (↩️).`)) {
        return;
      }
      if (mapPoi) mapPoi.closePopup();
      deletePlaceByIndex(idx);
      showToast(`🗑️ Colonia "${plName}" eliminada desde el mapa`, "info", 3000);
    }

    function flyToPlaceOnMap(idx) {
      if (!cityData.places || !cityData.places[idx]) return;
      const pl = cityData.places[idx];
      if (!pl.loc || pl.loc.length !== 2 || !mapPoi) return;
      mapPoi.flyTo([pl.loc[1], pl.loc[0]], 15, { duration: 0.8 });
      highlightPlaceRowInList(idx);
      setTimeout(() => {
        if (!placeMarkerMap.has(idx)) {
          renderPlacesMarkersOnMap();
        }
        hoverHighlightPlaceMarker(idx, true);
        setTimeout(() => hoverHighlightPlaceMarker(idx, false), 1500);
      }, 400);
    }

    function highlightPlaceRowInList(idx) {
      const row = document.getElementById(`placeRow_${idx}`);
      if (row) {
        row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        row.classList.add('ring-2', 'ring-metro-orange', 'bg-orange-50');
        setTimeout(() => {
          row.classList.remove('ring-2', 'ring-metro-orange', 'bg-orange-50');
        }, 1800);
      }
    }

    function checkPlacesDuplicatesCount() {
      const places = cityData.places || [];
      const badge = document.getElementById('badgeDupCount');
      if (!badge) return;
      const seen = new Set();
      let dupCount = 0;
      places.forEach(p => {
        const n = (p.name || '').trim().toLowerCase();
        if (!n) return;
        if (seen.has(n)) dupCount++;
        else seen.add(n);
      });
      if (dupCount > 0) {
        badge.innerText = dupCount;
        badge.classList.remove('hidden');
      } else {
        badge.classList.add('hidden');
      }
    }

    function loadMorePlaces() {
      placesRenderLimit += PLACES_CHUNK_SIZE;
      renderPlacesList();
    }

    function loadAllPlaces() {
      placesRenderLimit = 999999;
      renderPlacesList();
    }

    function purgeMicroPlaces() {
      const places = cityData.places || [];
      if (places.length === 0) {
        showToast("No hay asentamientos registrados para purgar", "warning");
        return;
      }

      const microIndices = [];
      places.forEach((p, idx) => {
        if (isMicroPlace(p)) {
          microIndices.push(idx);
        }
      });

      if (microIndices.length === 0) {
        showToast("✨ No se detectaron cerradas, privadas ni micro-calles para purgar", "info");
        return;
      }

      const count = microIndices.length;
      if (!confirm(`¿Deseas purgar ${count} cerradas, privadas y micro-calles residuales?\n\nEsta acción eliminará callejones y privadas menores de OSM/DENUE para despejar el mapa.\nPuedes revertir la acción inmediatamente con el botón Deshacer (↩️).`)) {
        return;
      }

      placesHistorySnapshot = JSON.parse(JSON.stringify(places));
      updateUndoButtonUI();

      const microSet = new Set(microIndices);
      cityData.deleted_places = [...(cityData.deleted_places || []), ...places.filter((_, idx) => microSet.has(idx))];
      cityData.places = places.filter((_, idx) => !microSet.has(idx));
      selectedPlaceIndices.clear();

      renderPlacesList();
      renderPlacesMarkersOnMap();
      if (document.getElementById('modalToponymyStudio') && !document.getElementById('modalToponymyStudio').classList.contains('hidden')) {
        renderToponymyStudioTable();
      }
      triggerAutoSave();
      showToast(`🧹 Se purgaron ${count} cerradas y micro-calles con éxito. Puedes deshacer con ↩️`, "success", 3000);
    }

    function setPlaceCategoryFilter(cat) {
      placeFilterCategory = cat;
      const categories = ['all', 'city', 'suburb', 'neighbourhood'];
      categories.forEach(c => {
        const btn = document.getElementById(`btnPlaceCat-${c}`);
        if (btn) {
          if (c === cat) {
            btn.className = "py-1 px-1 rounded-lg text-center transition bg-white text-stone-900 shadow-2xs cursor-pointer";
          } else {
            btn.className = "py-1 px-1 rounded-lg text-center transition text-stone-600 hover:text-stone-900 cursor-pointer";
          }
        }
      });
      renderPlacesList();
    }

    async function previewNativeOsmPlacesOnMap() {
      if (!currentCityFile) {
        showToast("Selecciona un proyecto de ciudad primero", "warning");
        return;
      }
      const btnTxt = document.getElementById('txtPreviewOsmMode');
      if (isNativeOsmPreviewOn) {
        if (nativeOsmMarkersGroup) nativeOsmMarkersGroup.clearLayers();
        isNativeOsmPreviewOn = false;
        if (btnTxt) btnTxt.innerText = "Previsualizar etiquetas OSM";
        showToast("Se ocultó la previsualización de etiquetas nativas de OSM", "info");
        return;
      }

      if (btnTxt) btnTxt.innerText = "Cargando OSM...";
      try {
        const resp = await fetch(`/api/toponymy/osm-preview?file=${encodeURIComponent(currentCityFile)}`);
        const data = await resp.json();
        if (data.status === "ok") {
          if (!nativeOsmMarkersGroup && mapPoi) {
            nativeOsmMarkersGroup = L.layerGroup().addTo(mapPoi);
          }
          if (nativeOsmMarkersGroup) nativeOsmMarkersGroup.clearLayers();

          const osmPlaces = data.places || [];
          if (osmPlaces.length === 0) {
            showToast("No se encontraron etiquetas de asentamientos en el archivo OSM PBF", "warning");
            if (btnTxt) btnTxt.innerText = "Previsualizar etiquetas OSM";
            return;
          }

          osmPlaces.forEach(p => {
            if (!p.loc || p.loc.length !== 2) return;
            const latLng = [p.loc[1], p.loc[0]];
            const isLarge = getPlaceScale(p.type) === 'large';
            const iconHtml = `
              <div class="px-2 py-0.5 rounded-md font-bold text-[10px] whitespace-nowrap shadow-md border flex items-center space-x-1 ${isLarge ? 'bg-amber-500 text-stone-950 border-amber-300 ring-2 ring-amber-400' : 'bg-indigo-600 text-white border-indigo-400'}">
                <span>${isLarge ? '👑' : '📍'}</span>
                <span>${escapeHtml(p.name)}</span>
              </div>
            `;
            const icon = L.divIcon({
              className: 'osm-native-label-preview',
              html: iconHtml,
              iconSize: null,
              iconAnchor: [30, 10]
            });
            const marker = L.marker(latLng, { icon: icon }).addTo(nativeOsmMarkersGroup);
            marker.bindTooltip(`
              <div class="p-1.5 text-xs text-stone-900 font-sans">
                <strong>${escapeHtml(p.name)}</strong> (OSM Nativo)
                <div class="text-[10px] text-stone-500">Tipo: ${p.type || 'suburb'} | Coord: [${p.loc[0].toFixed(3)}, ${p.loc[1].toFixed(3)}]</div>
              </div>
            `, { direction: 'top', className: 'poi-custom-tooltip' });
          });

          isNativeOsmPreviewOn = true;
          if (btnTxt) btnTxt.innerText = "Ocultar previsualización OSM";
          showToast(`🗺️ Mostrando ${osmPlaces.length} etiquetas nativas encontradas en OpenStreetMap PBF`, "success", 4000);
        } else {
          showToast(`Error al consultar etiquetas OSM: ${data.message}`, "error");
          if (btnTxt) btnTxt.innerText = "Previsualizar etiquetas OSM";
        }
      } catch (err) {
        showToast(`Fallo de conexión al cargar OSM: ${err.message}`, "error");
        if (btnTxt) btnTxt.innerText = "Previsualizar etiquetas OSM";
      }
    }

    function resetToNativeOsmMode() {
      const places = cityData.places || [];
      if (places.length === 0) return;
      if (!confirm(`¿Deseas vaciar la lista curada de asentamientos y toponimia?\n\nAl dejar la lista vacía, el compilador cartográfico de Subway Builder México utilizará las etiquetas nativas originales del archivo OSM PBF sin reemplazos.\n\nPuedes revertir esto inmediatamente con el botón Deshacer (↩️).`)) {
        return;
      }
      placesHistorySnapshot = JSON.parse(JSON.stringify(places));
      updateUndoButtonUI();
      cityData.places = [];
      selectedPlaceIndices.clear();
      renderPlacesList();
      renderPlacesMarkersOnMap();
      triggerAutoSave();
      showToast("Modo OSM Nativo activado. El juego utilizará las etiquetas estándar de OpenStreetMap.", "info");
    }

    function renderPlacesList() {
      const places = cityData.places || [];
      const cont = document.getElementById('placesListContainer');
      const badge = document.getElementById('placesCountBadge');
      const totalPill = document.getElementById('placesTotalPill');
      if (!cont) return;

      if (totalPill) totalPill.innerText = places.length;
      const delivery = document.getElementById('toponymyDeliveryMode');
      if (delivery) delivery.value = cityData.toponymy_mode || 'replace';
      const deliveryNote = document.getElementById('toponymyDeliveryNote');
      if (deliveryNote) deliveryNote.textContent = cityData.toponymy_mode === 'merge' ?
        'Conserva OSM y aplica tus nombres y descartes locales. La vista de esta lista muestra solo los nombres guardados.' :
        'Una lista incompleta sustituye las etiquetas OSM de cada capa con nombres guardados. Complementar OSM evita esa pérdida.';

      // Actualizar contadores por categoría jerárquica
      let catCityCount = 0;
      let catSuburbCount = 0;
      let catNeighbourhoodCount = 0;
      places.forEach(p => {
        const sc = getPlaceScale(p.type);
        if (sc === 'large') catCityCount++;
        else if (sc === 'small') catNeighbourhoodCount++;
        else catSuburbCount++;
      });
      const elCountAll = document.getElementById('countCat-all');
      const elCountCity = document.getElementById('countCat-city');
      const elCountSuburb = document.getElementById('countCat-suburb');
      const elCountNeighbourhood = document.getElementById('countCat-neighbourhood');
      if (elCountAll) elCountAll.innerText = places.length;
      if (elCountCity) elCountCity.innerText = catCityCount;
      if (elCountSuburb) elCountSuburb.innerText = catSuburbCount;
      if (elCountNeighbourhood) elCountNeighbourhood.innerText = catNeighbourhoodCount;

      // Banner de Estado de Modo Cartográfico (OSM Nativo vs Curado)
      const banner = document.getElementById('toponymyModeBanner');
      if (banner) {
        if (places.length === 0) {
          banner.innerHTML = `
            <div class="px-2.5 py-1 bg-gradient-to-r from-sky-50 to-indigo-50 border border-sky-200 rounded-lg flex items-center justify-between text-[11px] shadow-2xs">
              <div class="flex items-center space-x-1.5 min-w-0">
                <span class="w-2 h-2 rounded-full bg-sky-500 shrink-0"></span>
                <span class="font-bold text-sky-900 truncate">Modo: OSM Nativo</span>
                <span class="text-[10px] text-stone-500 hidden sm:inline">(etiquetas base)</span>
              </div>
              <button type="button" onclick="previewNativeOsmPlacesOnMap()" id="btnPreviewOsmMode" class="text-[10px] bg-white hover:bg-sky-100 text-sky-800 border border-sky-300 font-bold px-2 py-0.5 rounded transition flex items-center space-x-1 cursor-pointer shrink-0">
                <i data-lucide="eye" class="w-3 h-3 text-sky-600"></i>
                <span id="txtPreviewOsmMode">${isNativeOsmPreviewOn ? 'Ocultar' : 'Previsualizar'}</span>
              </button>
            </div>
          `;
        } else {
          banner.innerHTML = `
            <div class="px-2.5 py-1 bg-white border border-emerald-200 rounded-lg flex items-center justify-between text-[11px] shadow-2xs">
              <div class="flex items-center space-x-1.5 min-w-0">
                <span class="w-2 h-2 rounded-full bg-emerald-500 shrink-0"></span>
                <span class="font-bold text-emerald-900">Nombres guardados</span>
                <span class="text-[10px] font-mono text-stone-500">(${places.length})</span>
              </div>
              <button type="button" onclick="resetToNativeOsmMode()" class="text-[10px] text-stone-500 hover:text-rose-600 hover:underline transition flex items-center space-x-1 cursor-pointer shrink-0" title="Vaciar lista curada y usar etiquetas estándar de OSM">
                <i data-lucide="rotate-ccw" class="w-2.5 h-2.5"></i>
                <span>Volver a OSM</span>
              </button>
              <button type="button" onclick="previewNativeOsmPlacesOnMap()" class="text-[10px] text-sky-700 cursor-pointer" title="Alternar etiquetas OSM para comparar cobertura">Comparar OSM</button>
            </div>
          `;
        }
      }

      cont.innerHTML = '';

      const textQuery = toponymyNameKey(placeFilterText || '');
      const typeQuery = placeFilterType || 'all';

      const filtered = places.map((pl, idx) => ({ ...pl, originalIndex: idx })).filter(item => {
        const matchesText = !textQuery || toponymyNameKey(
          [item.name, ...(item.aliases || []), item.municipality || '', item.locality || ''].join(' ')).includes(textQuery);
        const scale = getPlaceScale(item.type);

        let matchesCategory = true;
        if (placeFilterCategory === 'city') {
          matchesCategory = (scale === 'large');
        } else if (placeFilterCategory === 'suburb') {
          matchesCategory = (scale === 'medium');
        } else if (placeFilterCategory === 'neighbourhood') {
          matchesCategory = (scale === 'small');
        }

        let matchesType = false;
        if (typeQuery === 'all') {
          matchesType = true;
        } else if (typeQuery === 'micro') {
          matchesType = isMicroPlace(item);
        } else if (typeQuery === 'denue') {
          matchesType = (item.source || '').toUpperCase().includes('DENUE');
        } else if (typeQuery === 'manual') {
          matchesType = item.source === 'YAML_CURATED';
        } else if (typeQuery === 'unknown') {
          matchesType = !item.source || item.source === 'UNKNOWN';
        } else if (typeQuery === 'osm') {
          matchesType = (item.source || '').toUpperCase().includes('OSM');
        } else {
          matchesType = scale === typeQuery || item.type === typeQuery;
        }
        return matchesText && matchesType && matchesCategory;
      });

      if (badge) {
        if (filtered.length !== places.length) {
          badge.innerText = `${filtered.length} de ${places.length} colonias`;
        } else {
          badge.innerText = `${places.length} colonias`;
        }
      }

      updateSelectedPlacesUI();
      checkPlacesDuplicatesCount();

      if (places.length === 0) {
        cont.innerHTML = `
          <div class="p-6 text-center text-metro-muted text-xs bg-metro-panel/50 rounded-xl border border-metro-border space-y-2.5">
            <i data-lucide="map-pin-off" class="w-8 h-8 mx-auto text-stone-400"></i>
            <span class="block font-bold text-stone-800 text-sm">No hay toponimia curada asignada.</span>
            <p class="text-[11px] text-stone-500 max-w-xs mx-auto">Escanea automáticamente asentamientos desde OpenStreetMap y microdatos DENUE del INEGI para rotular el mapa.</p>
            <div class="flex flex-col sm:flex-row justify-center items-center gap-2 pt-2">
              <button type="button" onclick="quickScanAndPopulateToponymy(this)" id="btnEmptyStateScan" class="w-full sm:w-auto px-4 py-2 bg-sky-700 hover:bg-sky-600 text-white font-bold rounded-lg shadow transition flex items-center justify-center space-x-1.5 text-xs cursor-pointer">
                <i data-lucide="scan" class="w-4 h-4"></i>
                <span>Escanear y revisar nombres</span>
              </button>
              <div class="flex items-center gap-1.5">
                <button type="button" onclick="openScanToponymyDialog()" class="px-2.5 py-2 bg-stone-100 hover:bg-stone-200 text-stone-700 font-semibold rounded-lg border border-stone-200 text-[11px] transition cursor-pointer" title="Opciones avanzadas de escaneo">
                  <i data-lucide="sliders-horizontal" class="w-3.5 h-3.5 inline"></i>
                </button>
                <button type="button" onclick="addNewPlaceRow()" class="px-3 py-2 bg-metro-orange hover:bg-orange-600 text-white font-bold rounded-lg shadow-xs flex items-center space-x-1 text-[11px] transition cursor-pointer">
                  <i data-lucide="plus" class="w-3.5 h-3.5"></i>
                  <span>Manual</span>
                </button>
              </div>
            </div>
          </div>
        `;
        if (window.lucide) lucide.createIcons();
        return;
      }

      if (filtered.length === 0) {
        cont.innerHTML = `
          <div class="p-6 text-center text-stone-500 text-xs bg-stone-50 rounded-xl border border-stone-200 space-y-1">
            <i data-lucide="search-x" class="w-6 h-6 text-stone-400 mx-auto"></i>
            <span class="font-bold text-stone-700">Ningún asentamiento coincide con "${escapeHtml(textQuery)}".</span>
            <button type="button" onclick="filterPlacesList(''); document.getElementById('placesSearchInput').value='';" class="text-sky-600 hover:underline text-[11px] font-bold block mx-auto pt-1 cursor-pointer">Limpiar búsqueda</button>
          </div>
        `;
        if (window.lucide) lucide.createIcons();
        return;
      }

      const visibleItems = filtered.slice(0, placesRenderLimit);
      const frag = document.createDocumentFragment();

      visibleItems.forEach(item => {
        const idx = item.originalIndex;
        const isChecked = selectedPlaceIndices.has(idx);
        const scale = getPlaceScale(item.type);
        const isMicro = isMicroPlace(item);
        const scaleEmoji = isMicro ? '🚪' : (scale === 'large' ? '👑' : (scale === 'small' ? '🏘️' : '🏙️'));
        const loc = item.loc || [0, 0];
        const safeName = escapeHtml(item.name || '');

        const row = document.createElement('div');
        row.id = `placeRow_${idx}`;
        row.className = `p-2.5 bg-white border rounded-xl text-xs space-y-1.5 shadow-2xs transition-all ${
          isChecked ? 'bg-amber-50/80 border-amber-400 ring-1 ring-amber-300' : 'border-stone-200 hover:border-stone-300 hover:shadow-xs'
        }`;

        row.onmouseenter = () => hoverHighlightPlaceMarker(idx, true);
        row.onmouseleave = () => hoverHighlightPlaceMarker(idx, false);

        row.innerHTML = `
          <!-- Fila 1: Checkbox, Icono, Input de Nombre a TODO lo ancho (100% libre!), Acciones -->
          <div class="flex items-center space-x-2">
            <input type="checkbox" ${isChecked ? 'checked' : ''} onclick="handlePlaceCheckboxClick(event, ${idx})" class="rounded border-stone-300 text-metro-orange focus:ring-0 shrink-0 cursor-pointer w-3.5 h-3.5" title="Seleccionar para edición en lote">
            <span class="text-base shrink-0 cursor-pointer select-none" onclick="flyToPlaceOnMap(${idx})" title="Volar a ${safeName} en el mapa">${scaleEmoji}</span>

            <div class="flex-1 min-w-0">
              <input type="text" value="${safeName}" onchange="updatePlaceName(${idx}, this.value)" placeholder="Nombre del asentamiento..." title="${safeName}" class="w-full font-bold text-stone-800 text-xs bg-stone-50 hover:bg-white focus:bg-white px-2.5 py-1.5 rounded-lg border border-stone-200 focus:border-metro-orange focus:ring-1 focus:ring-metro-orange focus:outline-none transition shadow-2xs">
            </div>

            <div class="flex items-center space-x-0.5 shrink-0">
              <button type="button" onclick="flyToPlaceOnMap(${idx})" class="p-1.5 text-stone-400 hover:text-sky-600 hover:bg-sky-50 rounded-lg transition cursor-pointer" title="Centrar y enfocar en el mapa">
                ${SVG_PLACE_CROSSHAIR}
              </button>
              <button type="button" onclick="deletePlaceByIndex(${idx})" class="p-1.5 text-stone-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition cursor-pointer" title="Eliminar colonia">
                ${SVG_PLACE_TRASH}
              </button>
            </div>
          </div>

          <!-- Fila 2: Chips Rápidos de Escala (1-clic), Coordenadas interactivas y metadatos -->
          <div class="flex items-center justify-between text-[11px] pt-1 border-t border-stone-100">
            <!-- Selector de Escala Segmentado Instantáneo -->
            <div class="flex items-center space-x-1 bg-stone-100/90 p-0.5 rounded-lg border border-stone-200/80">
              <button type="button" onclick="updatePlaceScale(${idx}, 'large')" class="px-2 py-0.5 rounded text-[10px] font-bold transition cursor-pointer ${scale === 'large' ? 'bg-white text-amber-900 shadow-xs ring-1 ring-amber-300 font-black' : 'text-stone-500 hover:text-stone-800'}" title="👑 Grande (18px) - Cabecera / Ciudad">👑 Grande</button>
              <button type="button" onclick="updatePlaceScale(${idx}, 'medium')" class="px-2 py-0.5 rounded text-[10px] font-bold transition cursor-pointer ${scale === 'medium' ? 'bg-white text-sky-900 shadow-xs ring-1 ring-sky-300 font-black' : 'text-stone-500 hover:text-stone-800'}" title="🏙️ Mediano (13px) - Distrito / Suburbio">🏙️ Mediano</button>
              <button type="button" onclick="updatePlaceScale(${idx}, 'small')" class="px-2 py-0.5 rounded text-[10px] font-bold transition cursor-pointer ${scale === 'small' ? 'bg-white text-emerald-900 shadow-xs ring-1 ring-emerald-300 font-black' : 'text-stone-500 hover:text-stone-800'}" title="🏘️ Chico (10px) - Fracc. / Colonia">🏘️ Chico</button>
            </div>

            <div class="flex items-center space-x-1.5">
              ${isMicro ? '<span class="px-1.5 py-0.5 bg-rose-50 text-rose-700 rounded text-[9px] font-bold border border-rose-200">Cerrada</span>' : ''}
              <span class="place-source-badge" title="${escapeHtml([placeSourceLabel(item),item.municipality,item.locality,item.hierarchy_note,...(item.aliases || [])].filter(Boolean).join(' · '))}">${escapeHtml(placeSourceLabel(item))}${item.hierarchy_inferred ? ' · revisar escala' : ''}</span>
              <button type="button" onclick="flyToPlaceOnMap(${idx})" class="font-mono text-[10px] text-stone-600 hover:text-sky-700 bg-stone-50 hover:bg-sky-50 border border-stone-200 px-1.5 py-0.5 rounded flex items-center space-x-1 transition cursor-pointer" title="Coordenadas (arrastrable en mapa)">
                ${SVG_PLACE_PIN}
                <span id="placeCoordBadge_${idx}">${loc[0].toFixed(3)}, ${loc[1].toFixed(3)}</span>
              </button>
            </div>
          </div>
        `;
        frag.appendChild(row);
      });
      cont.appendChild(frag);

      if (filtered.length > placesRenderLimit) {
        const loadMoreDiv = document.createElement('div');
        loadMoreDiv.className = "p-2.5 text-center bg-stone-100/80 border border-stone-200 rounded-xl space-y-1";
        const remaining = filtered.length - placesRenderLimit;
        loadMoreDiv.innerHTML = `
          <div class="text-[11px] text-stone-600 font-semibold">Mostrando <strong>${visibleItems.length}</strong> de <strong>${filtered.length}</strong> colonias</div>
          <div class="flex justify-center items-center gap-2 pt-1">
            <button type="button" onclick="loadMorePlaces()" class="px-3 py-1 bg-stone-200 hover:bg-stone-300 text-stone-800 text-xs font-bold rounded-lg transition cursor-pointer">
              Cargar ${Math.min(remaining, PLACES_CHUNK_SIZE)} más...
            </button>
            <button type="button" onclick="loadAllPlaces()" class="text-xs text-sky-700 hover:underline font-bold cursor-pointer">
              Mostrar todas (${filtered.length})
            </button>
          </div>
        `;
        cont.appendChild(loadMoreDiv);
      }
    }

    function updatePlaceScale(idx, scale) {
      if (!cityData.places || !cityData.places[idx]) return;
      placesHistorySnapshot = JSON.parse(JSON.stringify(cityData.places));
      updateUndoButtonUI();
      if (scale === 'large') cityData.places[idx].type = 'city';
      else if (scale === 'small') cityData.places[idx].type = 'neighbourhood';
      else cityData.places[idx].type = 'suburb';
      renderPlacesMarkersOnMap();
      renderPlacesList();
      if (document.getElementById('modalToponymyStudio') && !document.getElementById('modalToponymyStudio').classList.contains('hidden')) {
        renderToponymyStudioTable();
      }
      triggerAutoSave();
    }

    function batchSetPlaceScale(scale) {
      if (!cityData.places || selectedPlaceIndices.size === 0) return;
      placesHistorySnapshot = JSON.parse(JSON.stringify(cityData.places));
      updateUndoButtonUI();
      const targetType = scale === 'large' ? 'city' : (scale === 'small' ? 'neighbourhood' : 'suburb');
      selectedPlaceIndices.forEach(idx => {
        if (cityData.places[idx]) {
          cityData.places[idx].type = targetType;
        }
      });
      renderPlacesMarkersOnMap();
      renderPlacesList();
      if (document.getElementById('modalToponymyStudio') && !document.getElementById('modalToponymyStudio').classList.contains('hidden')) {
        renderToponymyStudioTable();
      }
      triggerAutoSave();
      showToast(`Se cambió la escala a ${scale === 'large' ? 'Grande' : (scale === 'small' ? 'Chico' : 'Mediano')} para ${selectedPlaceIndices.size} colonias`, 'info');
    }

    // -------------------------------------------------------------------------
    // TOPONIMY STUDIO - MATRIZ MAESTRA Y CURADURÍA MASIVA (MODAL)
    // -------------------------------------------------------------------------
    function openToponymyStudioModal() {
      const modal = document.getElementById('modalToponymyStudio');
      if (!modal) return;
      modal.classList.remove('hidden');
      renderToponymyStudioTable();
      if (window.lucide) lucide.createIcons();
    }

    function closeToponymyStudioModal() {
      const modal = document.getElementById('modalToponymyStudio');
      if (modal) modal.classList.add('hidden');
      renderPlacesList();
      renderPlacesMarkersOnMap();
    }

    function filterToponymyStudioTable(query) {
      tsSearchQuery = (query || '').trim().toLowerCase();
      renderToponymyStudioTable();
    }

    function filterToponymyStudioByScale(scale) {
      tsScaleFilterVal = scale || 'all';
      renderToponymyStudioTable();
    }

    function renderToponymyStudioTable() {
      const tbody = document.getElementById('tsTableBody');
      const places = cityData.places || [];
      if (!tbody) return;

      // Actualizar Métricas
      let cLarge = 0, cMedium = 0, cSmall = 0, cMicro = 0;
      places.forEach(p => {
        if (isMicroPlace(p)) cMicro++;
        const sc = getPlaceScale(p.type);
        if (sc === 'large') cLarge++;
        else if (sc === 'small') cSmall++;
        else cMedium++;
      });
      const mTot = document.getElementById('tsMetricTotal');
      const mLar = document.getElementById('tsMetricLarge');
      const mMed = document.getElementById('tsMetricMedium');
      const mSma = document.getElementById('tsMetricSmall');
      const mMic = document.getElementById('tsMetricMicro');
      if (mTot) mTot.innerText = places.length;
      if (mLar) mLar.innerText = cLarge;
      if (mMed) mMed.innerText = cMedium;
      if (mSma) mSma.innerText = cSmall;
      if (mMic) mMic.innerText = cMicro;

      tbody.innerHTML = '';

      const filtered = places.map((pl, idx) => ({ ...pl, originalIndex: idx })).filter(item => {
        const matchesText = !tsSearchQuery || (item.name && item.name.toLowerCase().includes(tsSearchQuery));
        const scale = getPlaceScale(item.type);
        let matchesType = false;
        if (tsScaleFilterVal === 'all') {
          matchesType = true;
        } else if (tsScaleFilterVal === 'micro') {
          matchesType = isMicroPlace(item);
        } else if (tsScaleFilterVal === 'denue') {
          matchesType = (item.source || '').toUpperCase().includes('DENUE');
        } else if (tsScaleFilterVal === 'osm') {
          matchesType = (item.source || '').toUpperCase().includes('OSM');
        } else {
          matchesType = scale === tsScaleFilterVal || item.type === tsScaleFilterVal;
        }
        return matchesText && matchesType;
      });

      if (filtered.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="6" class="p-8 text-center text-stone-500 font-medium">
              No se encontraron asentamientos que coincidan con los filtros.
            </td>
          </tr>
        `;
        return;
      }

      const frag = document.createDocumentFragment();
      filtered.forEach(item => {
        const idx = item.originalIndex;
        const isChecked = selectedPlaceIndices.has(idx);
        const scale = getPlaceScale(item.type);
        const isMicro = isMicroPlace(item);
        const loc = item.loc || [0, 0];
        const safeName = escapeHtml(item.name || '');

        const tr = document.createElement('tr');
        tr.className = `hover:bg-stone-50/80 transition ${isChecked ? 'bg-amber-50/60' : ''}`;
        tr.innerHTML = `
          <td class="p-2.5 text-center">
            <input type="checkbox" ${isChecked ? 'checked' : ''} onclick="handlePlaceCheckboxClick(event, ${idx}); renderToponymyStudioTable();" class="rounded border-stone-300 text-metro-orange focus:ring-0 cursor-pointer">
          </td>
          <td class="p-2.5">
            <div class="inline-flex items-center space-x-1 bg-stone-100 p-0.5 rounded-lg border border-stone-200">
              <button type="button" onclick="updatePlaceScale(${idx}, 'large')" class="px-2 py-0.5 rounded text-[10px] font-bold transition cursor-pointer ${scale === 'large' ? 'bg-white text-amber-900 shadow-xs ring-1 ring-amber-300 font-black' : 'text-stone-500 hover:text-stone-800'}" title="👑 Grande (18px)">👑 Grande</button>
              <button type="button" onclick="updatePlaceScale(${idx}, 'medium')" class="px-2 py-0.5 rounded text-[10px] font-bold transition cursor-pointer ${scale === 'medium' ? 'bg-white text-sky-900 shadow-xs ring-1 ring-sky-300 font-black' : 'text-stone-500 hover:text-stone-800'}" title="🏙️ Mediano (13px)">🏙️ Mediano</button>
              <button type="button" onclick="updatePlaceScale(${idx}, 'small')" class="px-2 py-0.5 rounded text-[10px] font-bold transition cursor-pointer ${scale === 'small' ? 'bg-white text-emerald-900 shadow-xs ring-1 ring-emerald-300 font-black' : 'text-stone-500 hover:text-stone-800'}" title="🏘️ Chico (10px)">🏘️ Chico</button>
            </div>
          </td>
          <td class="p-2.5">
            <div class="flex items-center space-x-2">
              <input type="text" value="${safeName}" onchange="updatePlaceName(${idx}, this.value)" class="flex-1 font-bold text-stone-900 text-xs bg-stone-50 hover:bg-white focus:bg-white px-2.5 py-1.5 rounded-lg border border-stone-200 focus:border-metro-orange focus:ring-1 focus:ring-metro-orange focus:outline-none transition shadow-2xs">
              ${isMicro ? '<span class="px-1.5 py-0.5 bg-rose-100 text-rose-800 rounded text-[9px] font-bold border border-rose-300 shrink-0">Cerrada</span>' : ''}
            </div>
          </td>
          <td class="p-2.5 font-mono text-[11px] text-stone-600">
            <button type="button" onclick="closeToponymyStudioModal(); flyToPlaceOnMap(${idx});" class="hover:text-sky-700 bg-stone-100 hover:bg-sky-50 border border-stone-200 px-2 py-1 rounded flex items-center space-x-1 transition cursor-pointer">
              ${SVG_PLACE_PIN_MD}
              <span>[${loc[0].toFixed(5)}, ${loc[1].toFixed(5)}]</span>
            </button>
          </td>
          <td class="p-2.5 text-stone-500 text-[11px]">
            <span class="px-2 py-0.5 bg-stone-100 rounded text-[10px] font-mono border border-stone-200">${escapeHtml(item.source || 'Manual')}</span>
          </td>
          <td class="p-2.5 text-right space-x-1">
            <button type="button" onclick="closeToponymyStudioModal(); flyToPlaceOnMap(${idx});" class="p-1.5 text-stone-400 hover:text-sky-600 hover:bg-sky-50 rounded-lg transition cursor-pointer" title="Centrar en el mapa">
              ${SVG_PLACE_CROSSHAIR}
            </button>
            <button type="button" onclick="deletePlaceByIndex(${idx}); renderToponymyStudioTable();" class="p-1.5 text-stone-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition cursor-pointer" title="Eliminar colonia">
              ${SVG_PLACE_TRASH}
            </button>
          </td>
        `;
        frag.appendChild(tr);
      });
      tbody.appendChild(frag);
    }

    function batchCapitalizeToponymyNames() {
      const places = cityData.places || [];
      if (places.length === 0) return;

      placesHistorySnapshot = JSON.parse(JSON.stringify(places));
      updateUndoButtonUI();

      let count = 0;
      const targetIndices = selectedPlaceIndices.size > 0 ? Array.from(selectedPlaceIndices) : places.map((_, i) => i);

      targetIndices.forEach(idx => {
        if (!places[idx] || !places[idx].name) return;
        const orig = places[idx].name;
        const words = orig.split(/\s+/);
        const cap = words.map((w, wIdx) => {
          const lower = w.toLowerCase();
          if (wIdx > 0 && ['de', 'del', 'la', 'las', 'el', 'los', 'y', 'e', 'en'].includes(lower)) {
            return lower;
          }
          if (/^(sm|cun|i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii|km)$/i.test(w)) {
            return w.toUpperCase();
          }
          return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
        }).join(' ');

        if (cap !== orig) {
          places[idx].name = cap;
          count++;
        }
      });

      renderToponymyStudioTable();
      renderPlacesList();
      renderPlacesMarkersOnMap();
      triggerAutoSave();
      showToast(`✨ Se normalizó la capitalización (Title Case) de ${count} colonias`, "success");
    }

    function batchStripCommonPrefixes() {
      const places = cityData.places || [];
      if (places.length === 0) return;

      placesHistorySnapshot = JSON.parse(JSON.stringify(places));
      updateUndoButtonUI();

      let count = 0;
      const targetIndices = selectedPlaceIndices.size > 0 ? Array.from(selectedPlaceIndices) : places.map((_, i) => i);

      targetIndices.forEach(idx => {
        if (!places[idx] || !places[idx].name) return;
        const orig = places[idx].name;
        const cleaned = orig.replace(/^(colonia|fraccionamiento|fracc\.?|barrio|pueblo|ejido|u\.?h\.?|unidad\s+habitacional)\s+/i, '').trim();
        if (cleaned && cleaned !== orig) {
          places[idx].name = cleaned;
          count++;
        }
      });

      renderToponymyStudioTable();
      renderPlacesList();
      renderPlacesMarkersOnMap();
      triggerAutoSave();
      showToast(`✨ Se depuraron los prefijos de ${count} colonias`, "success");
    }

    function addNewPlaceRowFromModal() {
      addNewPlaceRow();
      renderToponymyStudioTable();
    }

    // Acciones de Selección y Manipulación en Lista
    let lastSelectedPlaceIndex = null;

    function handlePlaceCheckboxClick(event, idx) {
      const isChecked = event.target.checked;

      if (event.shiftKey && lastSelectedPlaceIndex !== null && lastSelectedPlaceIndex !== idx) {
        const places = cityData.places || [];
        const textQuery = (placeFilterText || '').trim().toLowerCase();
        const typeQuery = placeFilterType || 'all';

        const filtered = places.map((pl, i) => ({ ...pl, originalIndex: i })).filter(item => {
          const matchesText = !textQuery || (item.name && item.name.toLowerCase().includes(textQuery));
          const matchesType = typeQuery === 'all' || item.type === typeQuery;
          return matchesText && matchesType;
        });

        const pos1 = filtered.findIndex(item => item.originalIndex === lastSelectedPlaceIndex);
        const pos2 = filtered.findIndex(item => item.originalIndex === idx);

        if (pos1 !== -1 && pos2 !== -1) {
          const start = Math.min(pos1, pos2);
          const end = Math.max(pos1, pos2);
          for (let i = start; i <= end; i++) {
            const itemIdx = filtered[i].originalIndex;
            if (isChecked) {
              selectedPlaceIndices.add(itemIdx);
            } else {
              selectedPlaceIndices.delete(itemIdx);
            }
          }
          lastSelectedPlaceIndex = idx;
          updateSelectedPlacesUI();
          renderPlacesList();
          renderPlacesMarkersOnMap();
          return;
        }
      }

      togglePlaceSelection(idx, isChecked);
      lastSelectedPlaceIndex = idx;
    }

    function togglePlaceSelection(idx, isChecked) {
      if (isChecked) {
        selectedPlaceIndices.add(idx);
      } else {
        selectedPlaceIndices.delete(idx);
      }
      updateSelectedPlacesUI();
      renderPlacesMarkersOnMap();
    }

    function selectFilteredPlacesOnly() {
      const places = cityData.places || [];
      const textQuery = (placeFilterText || '').trim().toLowerCase();
      const typeQuery = placeFilterType || 'all';
      let count = 0;
      places.forEach((item, idx) => {
        const matchesText = !textQuery || (item.name && item.name.toLowerCase().includes(textQuery));
        const matchesType = typeQuery === 'all' || item.type === typeQuery;
        if (matchesText && matchesType) {
          selectedPlaceIndices.add(idx);
          count++;
        }
      });
      updateSelectedPlacesUI();
      renderPlacesList();
      renderPlacesMarkersOnMap();
      showToast(`Se seleccionaron ${count} colonias visibles`, "info");
    }

    function toggleSelectAllPlaces(checked) {
      const places = cityData.places || [];
      if (checked) {
        places.forEach((_, idx) => selectedPlaceIndices.add(idx));
      } else {
        selectedPlaceIndices.clear();
      }
      renderPlacesList();
      renderPlacesMarkersOnMap();
    }

    function updateSelectedPlacesUI() {
      const btnDel = document.getElementById('btnDeleteSelectedPlaces');
      const countSpan = document.getElementById('selectedPlacesCount');
      const chkAll = document.getElementById('chkSelectAllPlaces');
      const batchScales = document.getElementById('batchScaleControls');
      const places = cityData.places || [];

      if (btnDel && countSpan) {
        const count = selectedPlaceIndices.size;
        countSpan.innerText = count;
        if (count > 0) {
          btnDel.classList.remove('hidden');
        } else {
          btnDel.classList.add('hidden');
        }
      }
      if (batchScales) {
        if (selectedPlaceIndices.size > 0) {
          batchScales.classList.remove('hidden');
          batchScales.classList.add('flex');
        } else {
          batchScales.classList.add('hidden');
          batchScales.classList.remove('flex');
        }
      }
      if (chkAll) {
        chkAll.checked = places.length > 0 && selectedPlaceIndices.size === places.length;
      }
    }

    function deleteSelectedPlaces() {
      if (selectedPlaceIndices.size === 0 || !cityData.places) return;
      const count = selectedPlaceIndices.size;
      if (!confirm(`¿Eliminar ${count} colonias seleccionadas?`)) return;

      placesHistorySnapshot = JSON.parse(JSON.stringify(cityData.places));
      updateUndoButtonUI();

      if (!cityData.deleted_places) cityData.deleted_places = [];
      cityData.places.forEach((p, idx) => {
        if (selectedPlaceIndices.has(idx) && p.name) {
          cityData.deleted_places.push(JSON.parse(JSON.stringify(p)));
        }
      });

      cityData.places = cityData.places.filter((_, idx) => !selectedPlaceIndices.has(idx));
      selectedPlaceIndices.clear();
      lastSelectedPlaceIndex = null;
      renderPlacesList();
      renderPlacesMarkersOnMap();
      triggerAutoSave();
      showToast(`Se eliminaron ${count} colonias`, "info");
    }

    function filterPlacesList(query) {
      placeFilterText = query;
      renderPlacesList();
    }

    function filterPlacesByType(type) {
      placeFilterType = type;
      renderPlacesList();
    }

    function updatePlaceName(idx, val) {
      if (cityData.places && cityData.places[idx]) {
        if (!String(val).trim()) { renderPlacesList(); showToast('El nombre no puede estar vacío.', 'warning'); return; }
        if (!cityData.places[idx].original_name) cityData.places[idx].original_name = cityData.places[idx].name;
        cityData.places[idx].name = val;
        renderPlacesMarkersOnMap();
        triggerAutoSave();
      }
    }

    function updatePlaceType(idx, val) {
      if (cityData.places && cityData.places[idx]) {
        cityData.places[idx].type = val;
        renderPlacesMarkersOnMap();
        triggerAutoSave();
      }
    }

    function deletePlaceByIndex(idx) {
      if (cityData.places && cityData.places[idx]) {
        placesHistorySnapshot = JSON.parse(JSON.stringify(cityData.places));
        updateUndoButtonUI();
        const delName = (cityData.places[idx].name || '').trim();
        if (delName) {
          if (!cityData.deleted_places) cityData.deleted_places = [];
          cityData.deleted_places.push(JSON.parse(JSON.stringify(cityData.places[idx])));
        }
        cityData.places.splice(idx, 1);
        selectedPlaceIndices.delete(idx);
        renderPlacesList();
        renderPlacesMarkersOnMap();
        triggerAutoSave();
      }
    }

    function addNewPlaceRow() {
      if (!cityData.places) cityData.places = [];
      const center = mapPoi ? mapPoi.getCenter() : { lng: -86.85, lat: 21.16 };
      cityData.places.push({
        id: 'place_' + crypto.randomUUID(),
        name: "Nueva Colonia",
        loc: [parseFloat(center.lng.toFixed(5)), parseFloat(center.lat.toFixed(5))],
        type: "suburb",
        source: 'YAML_CURATED'
      });
      renderPlacesList();
      renderPlacesMarkersOnMap();
      triggerAutoSave();
      flyToPlaceOnMap(cityData.places.length - 1);
    }

    // -------------------------------------------------------------------------
    // FLUJO DE ESCANEO RÁPIDO 1-CLIC Y DIÁLOGO AVANZADO (OSM + DENUE)
    // -------------------------------------------------------------------------

    let toponymyScanController = null;
    let scanReviewRows = [], scanReviewSelected = new Set(), scanReviewLimit = 60;
    let scanImportSaving = false;
    let scannedPlacesContext = null;

    function setToponymyScanStatus(message, state = 'running') {
      for (const id of ['toponymyScanStatus', 'scanSourceSummary']) {
        const el = document.getElementById(id);
        if (el) { el.textContent = message; el.hidden = !message; el.dataset.state = state; }
      }
    }

    function cancelToponymyScan() {
      if (toponymyScanController) toponymyScanController.abort();
      toponymyScanController = null;
      scannedPlacesCatalog = null;
      scannedPlacesContext = null;
      scanReviewRows = [];
      scanReviewSelected.clear();
      setToponymyScanStatus('');
      for (const id of ['btnScanPlaces', 'btnEmptyStateScan', 'btnExecuteScan']) {
        const button = document.getElementById(id);
        if (button) { button.disabled = false; button.setAttribute('aria-busy', 'false'); }
      }
      const confirm = document.getElementById('btnConfirmImportScan');
      if (confirm) confirm.disabled = true;
      document.getElementById('scanLoadingIndicator')?.classList.add('hidden');
    }

    function toponymyNameKey(name) {
      let s = String(name || '').normalize('NFC').toLowerCase().replaceAll('ñ', '\u0001');
      s = s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').replaceAll('\u0001', 'ñ');
      s = s.replace(/^(?:super\s*manzana|s\.?\s*m\.?)(?=\s|\d|$)\s*/, 'supermanzana ');
      s = s.replace(/^(?:region|reg\.?)(?=\s|\d|$)\s*/, 'region ');
      s = s.replace(/^(?:colonia|col\.?|fraccionamiento|fracc\.?)\s+/, '');
      if (/^\d+[a-z]?$/.test(s.trim())) s = 'supermanzana ' + s;
      return s.replace(/[^\p{L}\p{N}]/gu, '');
    }

    function sameToponymyPlace(a, b) {
      if (['cve_ent', 'cve_mun', 'cve_loc'].some(k => a[k] && b[k] && String(a[k]) !== String(b[k]))) return false;
      if (a.id && b.id && a.id === b.id) return true;
      const keys = p => [p.name,p.original_name].filter(Boolean).map(toponymyNameKey);
      if (!keys(a).some(key=>keys(b).includes(key))) return false;
      if (!a.loc || !b.loc || ![...a.loc, ...b.loc].every(Number.isFinite)) return false;
      const rad = x => x * Math.PI / 180;
      const h = Math.sin(rad(b.loc[1] - a.loc[1]) / 2) ** 2 +
        Math.cos(rad(a.loc[1])) * Math.cos(rad(b.loc[1])) * Math.sin(rad(b.loc[0] - a.loc[0]) / 2) ** 2;
      const distance = 12742000 * Math.asin(Math.min(1, Math.sqrt(h)));
      const radius = ['city', 'town'].includes(a.type) && ['city', 'town'].includes(b.type) ? 7500 : 500;
      return distance <= radius;
    }

    function mergeScannedToponymy(existing, scanned) {
      const places = JSON.parse(JSON.stringify(existing || []));
      const buckets = new Map(), identities = new Map();
      for (const p of places) {
        const key = toponymyNameKey(p.name);
        if (!buckets.has(key)) buckets.set(key, []);
        buckets.get(key).push(p);
        if (p.original_name) {
          const originalKey = toponymyNameKey(p.original_name);
          if (!buckets.has(originalKey)) buckets.set(originalKey, []);
          buckets.get(originalKey).push(p);
        }
        if (p.id) identities.set(p.id, p);
      }
      for (const item of scanned) {
        const deleted = (cityData.deleted_places || []).some(p =>
          typeof p === 'string' ? toponymyNameKey(p) === toponymyNameKey(item.name) : sameToponymyPlace(p, item));
        if (deleted) continue;
        const key = toponymyNameKey(item.name);
        const choices = [...(buckets.get(key) || [])];
        if (item.id && identities.has(item.id)) choices.unshift(identities.get(item.id));
        const old = choices.find(p => sameToponymyPlace(p, item));
        if (!old) {
          const fresh = JSON.parse(JSON.stringify(item));
          places.push(fresh);
          if (!buckets.has(key)) buckets.set(key, []);
          buckets.get(key).push(fresh);
          if (fresh.id) identities.set(fresh.id, fresh);
          continue;
        }
        for (const key of Object.keys(item)) {
          if (!['name', 'loc', 'type', 'source'].includes(key) && old[key] === undefined && item[key] !== undefined) {
            old[key] = JSON.parse(JSON.stringify(item[key]));
          }
        }
        old.establishments = Math.max(old.establishments || 0, item.establishments || 0);
        old.source_files = [...new Set([...(old.source_files || []), ...(item.source_files || [])])].sort();
        if (!old.source && item.source) old.source = item.source;
        if (old.source === 'UNKNOWN') {
          const matched = item.matched_source || (item.source !== 'UNKNOWN' ? item.source : null);
          if (matched) old.matched_source = matched;
        }
      }
      return places;
    }

    function toponymyScanSummary(cat) {
      const files = (cat.sources || []).map(s => {
        const name = (s.path || '').split(/[\\/]/).pop() || s.kind;
        return name + ': ' + (s.status === 'ok' ? (s.cached ? 'caché válida' : 'consultado') : (s.message || s.status));
      }).join(' · ');
      const municipalities = (cat.coverage || []).map(m => m.municipality || m.cve_mun).filter(Boolean);
      return (cat.partial ? 'Resultado parcial. ' : 'Fuentes consultadas. ') + files +
        (municipalities.length ? '. Municipios: ' + municipalities.join(', ') : '') +
        '. ' + cat.total + ' nombres; ' + (cat.preserved || 0) + ' existentes conservados.';
    }

    function toponymyInputsSignature() {
      return JSON.stringify([cityData.city?.bbox, cityData.data_dir, cityData.data_exclusions || []]);
    }

    function ensureToponymyPlaceIds() {
      for (const place of cityData.places || []) {
        if (!place.id) place.id = 'place_' + crypto.randomUUID();
      }
    }

    async function runToponymyScan(minCount, consume) {
      if (!currentCityFile || toponymyScanController) return;
      const file = currentCityFile, version = cityLoadVersion, controller = new AbortController();
      toponymyScanController = controller;
      scannedPlacesCatalog = null;
      scannedPlacesContext = null;
      const current = () => currentCityFile === file && cityLoadVersion === version && toponymyScanController === controller;
      const started = Date.now();
      let phase = 'Guardando configuración';
      const progress = () => { if (current()) setToponymyScanStatus(phase + ' · ' + Math.floor((Date.now() - started) / 1000) + ' s. La primera extracción OSM puede tardar varios minutos.'); };
      for (const id of ['btnScanPlaces', 'btnEmptyStateScan', 'btnExecuteScan', 'btnConfirmImportScan']) {
        const button = document.getElementById(id);
        if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
      }
      document.getElementById('scanLoadingIndicator')?.classList.remove('hidden');
      document.getElementById('scanResultsSection')?.classList.add('hidden');
      progress();
      const timer = setInterval(progress, 1000);
      try {
        ensureToponymyPlaceIds();
        const saved = await saveCurrentCity(true);
        if (!current()) return;
        if (!saved) throw new Error('No se pudo guardar la configuración antes del escaneo.');
        const snapshot = JSON.stringify(cityData.places || []);
        const inputsSnapshot = toponymyInputsSignature();
        phase = 'Consultando OpenStreetMap y todas las fuentes DENUE';
        progress();
        const data = await fetchStartupJson('/api/toponymy/scan?file=' + encodeURIComponent(file) +
          '&min_count=' + encodeURIComponent(minCount), 'Toponimia', 600000, controller.signal);
        if (!current()) return;
        const cat = data.catalog;
        if (toponymyInputsSignature() !== inputsSnapshot) {
          throw new Error('El área o las fuentes cambiaron durante el escaneo. Ejecuta Escanear nuevamente.');
        }
        if (!cat || !Array.isArray(cat.places) || !cat.places.every(p =>
          typeof p.name === 'string' && p.name.trim() && Array.isArray(p.loc) && p.loc.length === 2 && p.loc.every(Number.isFinite))) {
          throw new Error('El escaneo no devolvió nombres y coordenadas válidos.');
        }
        clearInterval(timer);
        setToponymyScanStatus(toponymyScanSummary(cat), cat.partial ? 'warning' : 'success');
        await consume(cat, current, {file, version, snapshot, inputsSnapshot});
      } catch (error) {
        if (current()) { setToponymyScanStatus(error.message, 'error'); showToast(error.message, 'error'); }
      } finally {
        clearInterval(timer);
        if (toponymyScanController === controller) {
          toponymyScanController = null;
          for (const id of ['btnScanPlaces', 'btnEmptyStateScan', 'btnExecuteScan']) {
            const button = document.getElementById(id);
            if (button) { button.disabled = false; button.setAttribute('aria-busy', 'false'); }
          }
          document.getElementById('scanLoadingIndicator')?.classList.add('hidden');
        }
      }
    }

    async function quickScanAndPopulateToponymy(btnElement = null) {
      // Both entry points now use the same review; scanning never imports silently.
      openScanToponymyDialog();
    }

    function openScanToponymyDialog() {
      const modal = document.getElementById('modalScanToponymy');
      if (modal) modal.classList.remove('hidden');
      document.getElementById('scanMinCount')?.focus?.();
      if (scannedPlacesCatalog && scannedPlacesContext &&
          scannedPlacesContext.file === currentCityFile && scannedPlacesContext.version === cityLoadVersion &&
          scannedPlacesContext.inputsSnapshot === toponymyInputsSignature()) {
        renderScanResultsUI(scannedPlacesCatalog);
      } else {
        executeCityToponymyScan();
      }
      lucide.createIcons();
    }

    function closeScanToponymyDialog() {
      if (toponymyScanController) cancelToponymyScan();
      const modal = document.getElementById('modalScanToponymy');
      if (modal) modal.classList.add('hidden');
    }

    async function executeCityToponymyScan() {
      const minCount = document.getElementById('scanMinCount')?.value || 8;
      await runToponymyScan(minCount, async (cat, current, context) => {
        scannedPlacesCatalog = cat;
        scannedPlacesContext = context;
        prepareScanReview(cat);
        renderScanResultsUI(cat);
      });
    }

    function placeSourceLabel(place) {
      const source = String(place.source || 'UNKNOWN');
      const labels = {UNKNOWN:'Origen no registrado', YAML_CURATED:'Añadido manualmente',
        OSM_NODE:'OSM · punto', OSM_POLYGON:'OSM · polígono',
        INEGI_DENUE:'DENUE · asentamiento', INEGI_DENUE_LOCALIDAD:'DENUE · localidad sugerida'};
      return labels[source] || source;
    }

    function prepareScanReview(cat) {
      const byId = new Map(), byName = new Map();
      for (const p of cityData.places || []) {
        if (p.id) byId.set(p.id, p);
        const key = toponymyNameKey(p.name);
        if (!byName.has(key)) byName.set(key, []);
        byName.get(key).push(p);
        if (p.original_name) {
          const originalKey = toponymyNameKey(p.original_name);
          if (!byName.has(originalKey)) byName.set(originalKey, []);
          byName.get(originalKey).push(p);
        }
      }
      const deleted = cityData.deleted_places || [];
      scanReviewRows = cat.places.map((p, index) => {
        const choices = [...(byName.get(toponymyNameKey(p.name)) || [])];
        if (p.id && byId.has(p.id)) choices.push(byId.get(p.id));
        const old = choices.find(a => sameToponymyPlace(a, p));
        const discarded = deleted.some(a => typeof a === 'string' ?
          toponymyNameKey(a) === toponymyNameKey(p.name) : sameToponymyPlace(a, p));
        return {p, index, old, status:discarded ? 'discarded' : old ? 'kept' : 'new'};
      });
      scanReviewSelected = new Set(scanReviewRows.filter(r => r.status !== 'discarded').map(r => r.index));
      scanReviewLimit = 60;
      for (const id of ['scanReviewSearch', 'scanReviewSource', 'scanReviewMunicipality']) {
        const el = document.getElementById(id);
        if (el) el.value = '';
      }
      const replace = document.getElementById('scanConfirmReplace');
      if (replace) replace.checked = false;
      const municipalities = [...new Set(cat.places.map(p => p.municipality).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'es'));
      const municipality = document.getElementById('scanReviewMunicipality');
      if (municipality) municipality.innerHTML = '<option value="">Todos los municipios</option>' +
        municipalities.map(n => '<option value="' + escapeHtml(n) + '">' + escapeHtml(n) + '</option>').join('');
      const sources = [...new Set(cat.places.map(p => p.source || 'UNKNOWN'))].sort();
      const source = document.getElementById('scanReviewSource');
      if (source) source.innerHTML = '<option value="">Todas las fuentes</option>' +
        sources.map(n => '<option value="' + escapeHtml(n) + '">' + escapeHtml(placeSourceLabel({source:n})) + '</option>').join('');
    }

    function scanReviewFiltered() {
      const query = toponymyNameKey(document.getElementById('scanReviewSearch')?.value || '');
      const source = document.getElementById('scanReviewSource')?.value || '';
      const municipality = document.getElementById('scanReviewMunicipality')?.value || '';
      const status = document.getElementById('scanReviewStatus')?.value || 'new';
      return scanReviewRows.filter(r => (!query || toponymyNameKey(
        [r.p.name, ...(r.p.aliases || []), r.p.municipality || '', r.p.locality || ''].join(' ')).includes(query)) &&
        (!source || (r.p.source || 'UNKNOWN') === source) &&
        (!municipality || r.p.municipality === municipality) &&
        (status === 'all' || r.status === status));
    }

    function scanImportPreview() {
      const mode = document.querySelector('input[name="scanImportMode"]:checked')?.value || 'merge';
      const excludeMicro = document.getElementById('scanExcludeMicro')?.checked || false;
      const smallInferred = document.getElementById('scanInferSmall')?.checked || false;
      const scanned = scanReviewRows.filter(r => scanReviewSelected.has(r.index) &&
        r.status !== 'discarded' && (!excludeMicro || !isMicroPlace(r.p))).map(r =>
          smallInferred && r.p.hierarchy_inferred && r.status === 'new' ?
          {...r.p, type:'village', category:'LOCALIDAD'} : r.p);
      const before = cityData.places || [];
      // Merge also preserves manual edits when strict replacement is selected.
      const merged = mergeScannedToponymy(before, scanned);
      const selectedIds = new Set(scanned.map(p=>p.id).filter(Boolean)), selectedNames = new Map();
      for (const p of scanned) {
        const key = toponymyNameKey(p.name);
        if (!selectedNames.has(key)) selectedNames.set(key, []);
        selectedNames.get(key).push(p);
      }
      const isSelected = p => selectedIds.has(p.id) ||
        (selectedNames.get(toponymyNameKey(p.name)) || []).some(s=>sameToponymyPlace(p,s));
      const places = mode === 'replace' ? merged.filter(isSelected) : merged;
      const ids = new Set(before.map(p=>p.id).filter(Boolean));
      const added = places.filter(p => p.id ? !ids.has(p.id) : !before.some(a=>sameToponymyPlace(a,p))).length;
      const retainedIds = new Set(places.map(p=>p.id).filter(Boolean));
      const removed = mode === 'replace' ? before.filter(p=>p.id ? !retainedIds.has(p.id) :
        !places.some(a=>sameToponymyPlace(a,p))) : [];
      return {places, added, removed, selected:scanned.length};
    }

    function toggleScanReviewCandidate(index, checked) {
      if (scanImportSaving) return;
      if (checked) scanReviewSelected.add(index); else scanReviewSelected.delete(index);
      renderScanReview();
    }

    function selectScanReviewFiltered(checked) {
      if (scanImportSaving) return;
      for (const row of scanReviewFiltered()) {
        if (row.status === 'discarded') continue;
        if (checked) scanReviewSelected.add(row.index); else scanReviewSelected.delete(row.index);
      }
      renderScanReview();
    }

    function loadMoreScanReview() {
      scanReviewLimit += 60;
      renderScanReview();
    }

    function showScanCandidateOnMap(index) {
      const row = scanReviewRows[index];
      if (!row || !mapPoi) return;
      document.getElementById('modalScanToponymy')?.classList.add('hidden');
      mapPoi.flyTo([row.p.loc[1], row.p.loc[0]], 15);
      showToast(row.p.name + ' · ' + placeSourceLabel(row.p) + '. Pulsa Escanear para volver a la revisión.', 'info');
    }

    function renderScanReview() {
      if (!scannedPlacesCatalog) return;
      const rows = scanReviewFiltered(), preview = scanImportPreview();
      const count = document.getElementById('scanCandidateCount');
      if (count) count.textContent = rows.length + ' coincidencias · mostrando ' + Math.min(rows.length,scanReviewLimit);
      const summary = document.getElementById('scanImportDelta');
      if (summary) summary.textContent = preview.added + ' nuevos · ' +
        ((cityData.places || []).length - preview.removed.length) + ' existentes conservados · ' +
        preview.removed.length + ' eliminaciones · ' + preview.selected + ' candidatos seleccionados';
      const warning = document.getElementById('scanReplaceWarning');
      if (warning) {
        warning.hidden = !preview.removed.length;
        const names = document.getElementById('scanReplaceNames');
        if (names) names.textContent = preview.removed.slice(0,10).map(p=>p.name).join(', ') +
          (preview.removed.length > 10 ? '…' : '');
      }
      const confirm = document.getElementById('btnConfirmImportScan');
      if (confirm) {
        confirm.disabled = scanImportSaving || preview.selected === 0 ||
          (preview.removed.length > 0 && !document.getElementById('scanConfirmReplace')?.checked);
        confirm.setAttribute('aria-busy', String(scanImportSaving));
      }
      const list = document.getElementById('scanCandidatesList');
      if (list) {
        list.innerHTML = rows.slice(0,scanReviewLimit).map(({p,index,old,status}) =>
          '<div class="scan-review-row"><label><input type="checkbox" aria-label="Incorporar ' + escapeHtml(p.name) +
          '" ' + (scanReviewSelected.has(index) ? 'checked ' : '') + (status === 'discarded' || scanImportSaving ? 'disabled ' : '') +
          'onchange="toggleScanReviewCandidate(' + index + ',this.checked)"><span><strong>' + escapeHtml(p.name) +
          '</strong><small>' + escapeHtml([placeSourceLabel(p),p.municipality || p.cve_mun,
          status === 'new' ? 'Nuevo' : status === 'kept' ? 'Conservado: ' + old.name : 'Descartado previamente',
          p.hierarchy_inferred ? 'Escala inferida, revisar' : '',
          p.establishments ? p.establishments + ' establecimientos; no es población' : '',
          isMicroPlace(p) ? 'Micro-asentamiento' : ''].filter(Boolean).join(' · ')) +
          '</small></span></label><button type="button" onclick="showScanCandidateOnMap(' + index +
          ')" title="Ver ubicación">Mapa</button></div>').join('') ||
          '<p class="scan-review-empty">Sin coincidencias. Cambia los filtros.</p>';
        if (rows.length > scanReviewLimit) list.innerHTML +=
          '<button type="button" class="scan-review-more" onclick="loadMoreScanReview()">Mostrar 60 más</button>';
      }
      lucide.createIcons();
    }

    function setToponymyDeliveryMode(mode) {
      if (!['merge','replace'].includes(mode)) return;
      cityData.toponymy_mode = mode;
      renderPlacesList();
      triggerAutoSave();
    }

    function renderScanResultsUI(cat) {
      const resultsSec = document.getElementById('scanResultsSection');
      if (!resultsSec) return;
      resultsSec.classList.remove('hidden');
      document.getElementById('scanTotalCount').innerText = cat.total || 0;
      const diag = cat.diagnosis?.categories || {};
      document.getElementById('scanSmCount').innerText = (diag.SUPERMANZANA || 0) + (diag.REGION || 0);
      document.getElementById('scanFraccCount').innerText = diag.FRACCIONAMIENTO || 0;
      document.getElementById('scanColCount').innerText = (diag.COLONIA || 0) + (diag.GENERIC || 0);
      document.getElementById('scanMicroCount').innerText = (cat.places || []).filter(isMicroPlace).length;
      renderScanReview();
    }

    async function importScannedPlaces() {
      if (!scannedPlacesCatalog || !scannedPlacesContext || scanImportSaving) return;
      const {file, version, snapshot, inputsSnapshot} = scannedPlacesContext;
      if (file !== currentCityFile || version !== cityLoadVersion) return;
      if (toponymyInputsSignature() !== inputsSnapshot) {
        setToponymyScanStatus('El área o las fuentes cambiaron. Escanea nuevamente antes de importar.', 'warning');
        return;
      }
      const mode = document.querySelector('input[name="scanImportMode"]:checked')?.value || 'merge';
      if (mode === 'replace' && JSON.stringify(cityData.places || []) !== snapshot) {
        setToponymyScanStatus('La lista cambió durante la revisión. Escanea nuevamente antes de reemplazarla.', 'warning');
        return;
      }
      const preview = scanImportPreview();
      if (preview.removed.length && !document.getElementById('scanConfirmReplace')?.checked) {
        setToponymyScanStatus('Revisa las eliminaciones y marca la confirmación de reemplazo.', 'warning');
        return;
      }
      const before = cityData.places || [], cat = scannedPlacesCatalog;
      scanImportSaving = true;
      const confirm = document.getElementById('btnConfirmImportScan');
      if (confirm) confirm.disabled = true;
      try {
        placesHistorySnapshot = JSON.parse(JSON.stringify(before));
        updateUndoButtonUI();
        cityData.places = preview.places;
        // Persist explicit removals so a later scan/OSM merge respects this review.
        cityData.deleted_places = [...(cityData.deleted_places || []), ...preview.removed];
        selectedPlaceIndices.clear();
        renderPlacesList();
        renderPlacesMarkersOnMap();
        setToponymyScanStatus(toponymyScanSummary(cat) + ' Guardando nombres…', 'running');
        const saved = await saveCurrentCity(true);
        if (file !== currentCityFile || version !== cityLoadVersion) return;
        if (!saved) throw new Error('La importación quedó como borrador. Pulsa Guardar YAML para reintentar.');
        scannedPlacesCatalog = null;
        scannedPlacesContext = null;
        closeScanToponymyDialog();
        setToponymyScanStatus(toponymyScanSummary(cat) + ' ' + preview.added + ' nombres nuevos guardados.', cat.partial ? 'warning' : 'success');
        showToast(preview.added + ' nombres nuevos guardados; ' + preview.removed.length + ' retirados.', cat.partial ? 'warning' : 'success');
      } catch (error) {
        if (file === currentCityFile && version === cityLoadVersion) {
          setToponymyScanStatus(error.message, 'error');
          showToast(error.message, 'error');
        }
      } finally {
        scanImportSaving = false;
        if (scannedPlacesCatalog && file === currentCityFile && version === cityLoadVersion) renderScanReview();
      }
    }

    // -------------------------------------------------------------------------
    // HOMOGENEIZADOR EN MASA (BATCH HOMOGENIZER MODAL)
    // -------------------------------------------------------------------------
    function openHomogenizerModal() {
      const modal = document.getElementById('modalHomogenizer');
      if (modal) modal.classList.remove('hidden');
      runHomogenizerDiagnosis();
      resetHomogenizerPreview();
      lucide.createIcons();
    }

    function closeHomogenizerModal() {
      const modal = document.getElementById('modalHomogenizer');
      if (modal) modal.classList.add('hidden');
      pendingHomogenizeResult = null;
    }

    function resetHomogenizerPreview() {
      pendingHomogenizeResult = null;
      document.getElementById('homoDiffCountBadge').innerText = "0 cambios";
      document.getElementById('btnApplyHomogenize').disabled = true;
      document.getElementById('homoActionStatus').innerText = "Selecciona una acción para previsualizar.";
      const tb = document.getElementById('homoDiffTableBody');
      if (tb) {
        tb.innerHTML = `<tr><td colspan="3" class="p-4 text-center text-stone-400 font-sans">Selecciona una acción arriba para previsualizar cambios.</td></tr>`;
      }
    }

    async function runHomogenizerDiagnosis() {
      const places = cityData.places || [];
      document.getElementById('homoTotalBadge').innerText = places.length;
      const badgesCont = document.getElementById('homoDiagnosisBadges');
      if (!badgesCont) return;

      badgesCont.innerHTML = '<span class="text-stone-400">Analizando...</span>';

      try {
        const res = await fetch('/api/toponymy/homogenize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ places: places, options: {} })
        });
        const data = await res.json();
        if (data.status === 'ok' && data.result && data.result.diagnosis) {
          const cats = data.result.diagnosis.categories || {};
          badgesCont.innerHTML = `
            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-sky-100 text-sky-800 border border-sky-300">SM / Regiones: <strong>${(cats.SUPERMANZANA || 0) + (cats.REGION || 0)}</strong></span>
            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-100 text-amber-800 border border-amber-300">Números Sueltos: <strong>${cats.BARE_NUMERIC || 0}</strong></span>
            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-100 text-rose-800 border border-rose-300">Prefijo 'Colonia': <strong>${cats.COLONIA || 0}</strong></span>
            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-purple-100 text-purple-800 border border-purple-300">Fraccionamientos: <strong>${cats.FRACCIONAMIENTO || 0}</strong></span>
            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-stone-200 text-stone-800">Otros / Genéricos: <strong>${cats.GENERIC || 0}</strong></span>
          `;
        }
      } catch (err) {
        badgesCont.innerHTML = `<span class="text-rose-500">Error en diagnóstico: ${err.message}</span>`;
      }
    }

    async function previewHomogenizeAction(actionType) {
      const places = cityData.places || [];
      if (places.length === 0) {
        showToast("No hay colonias en la lista para homogeneizar", "info");
        return;
      }

      const options = {};
      if (actionType === 'unify_sm') {
        const fmt = document.getElementById('homoSmFormat')?.value || "Supermanzana {num}";
        options.unify_supermanzanas = fmt;
      } else if (actionType === 'strip_prefixes') {
        options.strip_prefixes = {
          strip_colonia: document.getElementById('homoStripColonia')?.checked ?? true,
          fracc_mode: document.getElementById('homoFraccMode')?.value || "Fracc.",
          strip_residencial: document.getElementById('homoStripResidencial')?.checked ?? false
        };
      } else if (actionType === 'smart_case') {
        options.smart_casing = true;
      } else if (actionType === 'regex') {
        const pat = document.getElementById('homoRegexPattern')?.value;
        const rep = document.getElementById('homoRegexReplacement')?.value || "";
        if (!pat) {
          showToast("Ingresa un patrón regex para buscar", "error");
          return;
        }
        options.regex_rule = { pattern: pat, replacement: rep, ignore_case: true };
      }

      document.getElementById('homoActionStatus').innerText = "Calculando vista previa...";

      try {
        const res = await fetch('/api/toponymy/homogenize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ places: places, options: options })
        });
        const data = await res.json();
        if (data.status === 'ok' && data.result) {
          pendingHomogenizeResult = data.result;
          renderHomogenizerDiffUI(data.result);
        } else {
          showToast(`Error al procesar: ${data.message || 'Error desconocido'}`, "error");
        }
      } catch (err) {
        showToast(`Fallo de conexión: ${err.message}`, "error");
      }
    }

    function renderHomogenizerDiffUI(result) {
      const diffs = result.diffs || [];
      const tb = document.getElementById('homoDiffTableBody');
      const badge = document.getElementById('homoDiffCountBadge');
      const btnApply = document.getElementById('btnApplyHomogenize');
      const status = document.getElementById('homoActionStatus');

      badge.innerText = `${diffs.length} cambios`;
      btnApply.disabled = (diffs.length === 0);
      status.innerText = diffs.length > 0 ? `Se encontraron ${diffs.length} modificaciones listas para aplicar.` : "No se requieren modificaciones con los parámetros actuales.";

      if (!tb) return;
      tb.innerHTML = '';

      if (diffs.length === 0) {
        tb.innerHTML = `<tr><td colspan="3" class="p-4 text-center text-emerald-600 font-sans font-bold">¡Excelente! Los nombres ya cumplen con esta regla.</td></tr>`;
        return;
      }

      diffs.slice(0, 100).forEach((d, i) => {
        const tr = document.createElement('tr');
        tr.className = "hover:bg-orange-50/50 transition";
        tr.innerHTML = `
          <td class="p-1.5 text-stone-400 text-[10px]">${i + 1}</td>
          <td class="p-1.5 text-rose-700 line-through">${d.old}</td>
          <td class="p-1.5 text-emerald-700 font-bold">${d.new}</td>
        `;
        tb.appendChild(tr);
      });
      if (diffs.length > 100) {
        const moreRow = document.createElement('tr');
        moreRow.innerHTML = `<td colspan="3" class="p-2 text-center text-stone-400 font-sans font-bold">... y ${diffs.length - 100} cambios adicionales</td>`;
        tb.appendChild(moreRow);
      }
    }

    function applyPendingHomogenization() {
      if (!pendingHomogenizeResult || !pendingHomogenizeResult.places) return;
      const count = pendingHomogenizeResult.diffs ? pendingHomogenizeResult.diffs.length : 0;
      if (cityData.places && cityData.places.length > 0) {
        placesHistorySnapshot = JSON.parse(JSON.stringify(cityData.places));
        updateUndoButtonUI();
      }
      cityData.places = pendingHomogenizeResult.places;
      renderPlacesList();
      renderPlacesMarkersOnMap();
      triggerAutoSave();
      closeHomogenizerModal();
      showToast(`¡Homogeneización aplicada con éxito! (${count} nombres actualizados)`, "success");
    }

    async function deduplicateNearbyPlaces() {
      const places = cityData.places || [];
      if (places.length < 2) {
        showToast("Se requieren al menos 2 colonias para deduplicar", "info");
        return;
      }

      if (!confirm("¿Deduplicar automáticamente asentamientos idénticos o equivalentes a menos de 500m de distancia?")) {
        return;
      }

      placesHistorySnapshot = JSON.parse(JSON.stringify(cityData.places));
      updateUndoButtonUI();

      showToast("Buscando duplicados espaciales cercanos...", "info");
      try {
        const res = await fetch('/api/toponymy/deduplicate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ places: places, distance_m: 500.0 })
        });
        const data = await res.json();
        if (data.status === 'ok') {
          const removed = data.removed_count || 0;
          cityData.places = data.kept;
          selectedPlaceIndices.clear();
          renderPlacesList();
          renderPlacesMarkersOnMap();
          triggerAutoSave();
          if (removed > 0) {
            showToast(`Deduplicación exitosa: se eliminaron ${removed} colonias duplicadas`, "success");
          } else {
            showToast("No se detectaron colonias duplicadas cercanas", "info");
          }
        } else {
          showToast(`Error: ${data.message}`, "error");
        }
      } catch (err) {
        showToast(`Fallo de conexión al deduplicar: ${err.message}`, "error");
      }
    }

    // =========================================================================
    // ZONIFICACIÓN ESPACIAL Y PODA POR RADIO (TOPONYMY CLUSTER RESOLVER)
    // =========================================================================
    let isPlacesBoxSelectActive = false;
    let activeZoneClustersData = null;
    let activeZoneClustersContext = null, zoneCalculationVersion = 0;
    function zoneScoreExplanation(candidate) {
      const names = {source:'Procedencia', scale:'Escala', commerce:'Comercio', taxonomy:'Taxonomía', length:'Longitud'};
      return 'Puntuación ' + candidate.score + ': ' + Object.entries(candidate.score_components || {})
        .map(([key,value])=>(names[key] || key) + ' ' + value).join(' · ') +
        '. Es una sugerencia de rotulación; no mide población.';
    }
    let mapZonePreview = null;
    let zonePreviewLayerGroup = null;
    let zoneClusterRadiusTimer = null;
    let zoneFilterText = "";
    let zoneViewMode = 'deck'; // 'deck' | 'grid' | 'list'
    let currentDeckIndex = 0;
    let deckStreakCount = 0;
    let activeDeckZoneId = null;
    let isZoningKeydownActive = false;

    function attachZoningKeyboardListener() {
      if (isZoningKeydownActive) return;
      window.addEventListener('keydown', handleZoningModalKeydown);
      isZoningKeydownActive = true;
    }

    function detachZoningKeyboardListener() {
      if (!isZoningKeydownActive) return;
      window.removeEventListener('keydown', handleZoningModalKeydown);
      isZoningKeydownActive = false;
    }

    function handleZoningModalKeydown(e) {
      const modal = document.getElementById('modalZoneThinning');
      if (!modal || modal.classList.contains('hidden')) return;
      if (zoneViewMode !== 'deck') return;

      const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : '';
      if (tag === 'input' || tag === 'select' || tag === 'textarea') return;

      if (e.key === 'ArrowRight' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        deckAcceptAndNext();
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        deckPrevZone();
      } else if (e.key === 'ArrowDown' || e.key === 's' || e.key === 'S') {
        e.preventDefault();
        deckNextZone();
      } else if (e.key === 'e' || e.key === 'E') {
        e.preventDefault();
        deckToggleException();
      }
    }

    function toggleBoxSelectPlacesMode() {
      isPlacesBoxSelectActive = !isPlacesBoxSelectActive;
      const btn = document.getElementById('btnBoxSelectPlaces');
      if (btn) {
        if (isPlacesBoxSelectActive) {
          btn.className = "px-2 py-1 bg-indigo-600 hover:bg-indigo-700 text-white text-[10px] font-bold rounded border border-indigo-700 flex items-center space-x-1 transition shadow-inner";
          if (mapPoi && mapPoi.getContainer()) mapPoi.getContainer().style.cursor = 'crosshair';
          showToast("Modo Caja Activo: Arrastra un cuadro sobre el mapa para seleccionar colonias", "info");
        } else {
          btn.className = "px-2 py-1 bg-stone-100 hover:bg-stone-200 text-stone-700 text-[10px] font-bold rounded border border-stone-300 flex items-center space-x-1 transition";
          if (mapPoi && mapPoi.getContainer()) mapPoi.getContainer().style.cursor = '';
        }
      }
    }

    function openZoneThinningModal() {
      const places = cityData.places || [];
      if (places.length < 2) {
        showToast("Se requieren al menos 2 colonias para zonificar y podar", "info");
        return;
      }

      const modal = document.getElementById('modalZoneThinning');
      if (modal) {
        modal.classList.remove('hidden');
        attachZoningKeyboardListener();
        initZonePreviewMapIfNeeded();
        currentDeckIndex = 0;
        deckStreakCount = 0;
        setZoneViewMode('deck');
        setTimeout(() => {
          if (mapZonePreview) {
            mapZonePreview.invalidateSize();
            if (mapPoi) {
              mapZonePreview.setView(mapPoi.getCenter(), Math.max(11, mapPoi.getZoom() - 1));
            }
          }
        }, 150);
        recalculateZoneClusters();
      }
    }

    function closeZoneThinningModal() {
      const modal = document.getElementById('modalZoneThinning');
      if (modal) modal.classList.add('hidden');
      detachZoningKeyboardListener();
      if (zonePreviewLayerGroup) zonePreviewLayerGroup.clearLayers();
    }

    function initZonePreviewMapIfNeeded() {
      if (mapZonePreview) return;
      const el = document.getElementById('mapZonePreview');
      if (!el) return;

      mapZonePreview = L.map('mapZonePreview', {
        zoomControl: true,
        attributionControl: false,
        wheelPxPerZoomLevel: 60,
        wheelDebounceTime: 10,
        zoomSnap: 0.5
      }).setView([21.16, -86.85], 11);

      L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
        maxZoom: 19,
        maxNativeZoom: 16
      }).addTo(mapZonePreview);

      zonePreviewLayerGroup = L.layerGroup().addTo(mapZonePreview);
    }

    function setZoneViewMode(mode) {
      zoneViewMode = mode;
      const btnDeck = document.getElementById('btnZoneViewDeck');
      const btnGrid = document.getElementById('btnZoneViewGrid');
      const btnList = document.getElementById('btnZoneViewList');

      const deckCont = document.getElementById('zoneDeckContainer');
      const gridCont = document.getElementById('zoneGridContainer');
      const listCont = document.getElementById('zoneListContainer');

      const activeClass = "px-2.5 py-1 rounded-md bg-white text-indigo-700 font-bold shadow-xs flex items-center space-x-1 transition cursor-pointer";
      const inactiveClass = "px-2.5 py-1 rounded-md text-stone-600 hover:text-stone-900 flex items-center space-x-1 transition cursor-pointer";

      if (btnDeck) btnDeck.className = mode === 'deck' ? activeClass : inactiveClass;
      if (btnGrid) btnGrid.className = mode === 'grid' ? activeClass : inactiveClass;
      if (btnList) btnList.className = mode === 'list' ? activeClass : inactiveClass;

      if (deckCont) deckCont.classList.toggle('hidden', mode !== 'deck');
      if (gridCont) gridCont.classList.toggle('hidden', mode !== 'grid');
      if (listCont) listCont.classList.toggle('hidden', mode !== 'list');

      renderZoneClustersUI();
      if (mode === 'deck') {
        const filtered = getFilteredZoneList();
        if (filtered[currentDeckIndex]) {
          focusZoneOnPreviewMap(filtered[currentDeckIndex].zone_id);
        }
      }
    }

    function onZoneRadiusSliderInput(val) {
      const intVal = parseInt(val);
      const km = (intVal / 1000.0).toFixed(1);
      const lbl = document.getElementById('zoneRadiusVal');
      if (lbl) lbl.innerText = `${intVal.toLocaleString()} m (${km} km)`;

      if (zoneClusterRadiusTimer) clearTimeout(zoneClusterRadiusTimer);
      zoneClusterRadiusTimer = setTimeout(() => {
        recalculateZoneClusters();
      }, 350);
    }

    function setZoneRadiusPreset(val) {
      const slider = document.getElementById('zoneRadiusSlider');
      if (slider) {
        slider.value = val;
        onZoneRadiusSliderInput(val);
      }
    }

    function onToggleExcludeMicroZones(isChecked) {
      recalculateZoneClusters();
    }

    function onZoneFilterModeChange(mode) {
      zoneFilterMode = mode;
      currentDeckIndex = 0;
      updateZoneMetricsUI(activeZoneClustersData || {});
      renderZoneClustersUI();
      renderZoneClustersOnMap();
    }

    function filterZoneCards(query) {
      zoneFilterText = (query || '').trim().toLowerCase();
      currentDeckIndex = 0;
      updateZoneMetricsUI(activeZoneClustersData || {});
      renderZoneClustersUI();
      renderZoneClustersOnMap();
    }

    function toggleZoneMicroList(zoneId) {
      const el = document.getElementById(`zoneMicro_${zoneId}`);
      if (el) {
        el.classList.toggle('hidden');
      }
    }

    function getFilteredZoneList() {
      if (!activeZoneClustersData || !activeZoneClustersData.zones) return [];
      const zones = activeZoneClustersData.zones;
      const query = (zoneFilterText || '').trim().toLowerCase();
      return zones.filter(z => {
        if (zoneFilterMode === 'high_conflict' && !z.is_high_conflict) return false;
        if (zoneFilterMode === 'conflicts' && !z.is_conflict) return false;
        if (!query) return true;
        if (z.title && z.title.toLowerCase().includes(query)) return true;
        return (z.candidates || []).some(c => c.name && c.name.toLowerCase().includes(query));
      });
    }

    function focusZoneInList(zoneId) {
      if (zoneViewMode === 'deck') {
        jumpToZoneInDeck(zoneId);
        return;
      }
      const card = document.getElementById(`zoneCard_${zoneId}`);
      if (card) {
        card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        card.classList.add('ring-2', 'ring-indigo-500', 'bg-indigo-50/50');
        setTimeout(() => {
          card.classList.remove('ring-2', 'ring-indigo-500', 'bg-indigo-50/50');
        }, 1500);
      }
    }

    function autoResolveTrivialZones() {
      if (!activeZoneClustersData || !activeZoneClustersData.zones) return;
      let count = 0;
      activeZoneClustersData.zones.forEach(z => {
        if (z.is_trivial || (!z.is_high_conflict && z.is_conflict)) {
          z.selected_indices = [z.recommended_index];
          z.selected_index = z.recommended_index;
          z.is_exception = false;
          count++;
        }
      });
      updateZoneMetricsUI(activeZoneClustersData);
      renderZoneClustersUI();
      renderZoneClustersOnMap();
      showToast(`⚡ Se aprobaron ${count} zonas triviales con su ganador sugerido`, "success");
    }

    function fitMapZonePreviewToBounds() {
      if (!mapZonePreview || !activeZoneClustersData || !activeZoneClustersData.zones) return;
      const latLngs = [];
      activeZoneClustersData.zones.forEach(z => {
        if (z.center && z.center.length === 2) {
          latLngs.push([z.center[1], z.center[0]]);
        }
      });
      if (latLngs.length > 0) {
        mapZonePreview.fitBounds(L.latLngBounds(latLngs), { padding: [25, 25] });
      }
      setTimeout(() => { if (mapZonePreview) mapZonePreview.invalidateSize(); }, 100);
    }

    async function recalculateZoneClusters() {
      const places = cityData.places || [];
      const slider = document.getElementById('zoneRadiusSlider');
      const heuristicSelect = document.getElementById('zoneRankHeuristic');
      const chkMicro = document.getElementById('chkExcludeMicroZones');
      const status = document.getElementById('zoneThinningStatus');

      const radius = slider ? parseFloat(slider.value) : 1000.0;
      const heuristic = heuristicSelect ? heuristicSelect.value : 'balanced';
      const excludeMicro = chkMicro ? chkMicro.checked : true;

      if (status) status.innerText = "Calculando zonas de proximidad y puntuaciones...";
      const ownVersion = ++zoneCalculationVersion;
      const context = {file:currentCityFile, version:cityLoadVersion, snapshot:JSON.stringify(places)};
      activeZoneClustersContext = null;

      try {
        const res = await fetch('/api/toponymy/cluster-zones', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            places: places,
            radius_m: radius,
            rank_heuristic: heuristic,
            exclude_micro: excludeMicro,
            city_file: currentCityFile
          })
        });
        const data = await res.json();
        if (ownVersion !== zoneCalculationVersion || context.file !== currentCityFile ||
            context.version !== cityLoadVersion || context.snapshot !== JSON.stringify(cityData.places || [])) return;
        if (data.status === 'ok') {
          activeZoneClustersData = data;
          activeZoneClustersContext = context;
          currentDeckIndex = 0;
          updateZoneMetricsUI(data);
          renderZoneClustersUI();
          renderZoneClustersOnMap();
          if (status) {
            status.innerText = `Zonificación: ${data.high_conflict_count || 0} zonas de alto impacto y ${data.trivial_count || 0} triviales de ${data.anchors_count} totales.`;
          }
        } else {
          showToast(`Error al agrupar zonas: ${data.message}`, "error");
          if (status) status.innerText = "Error al procesar zonificación.";
        }
      } catch (err) {
        showToast(`Fallo de conexión: ${err.message}`, "error");
        if (status) status.innerText = "Fallo de conexión con el servidor.";
      }
    }

    function updateZoneMetricsUI(data) {
      const elTotal = document.getElementById('zoneTotalPlacesBadge');
      const elClusters = document.getElementById('zoneTotalClustersBadge');
      const elHighConflict = document.getElementById('zoneHighConflictBadge');
      const elTrivial = document.getElementById('zoneTrivialBadge');
      const elIsolated = document.getElementById('zoneIsolatedClustersBadge');
      const elPruned = document.getElementById('zonePrunedBadge');

      const zones = data.zones || [];
      if (elTotal) elTotal.innerText = data.total_places || 0;
      if (elClusters) elClusters.innerText = data.anchors_count || 0;

      const highCount = data.high_conflict_count !== undefined ? data.high_conflict_count : zones.filter(z => z.is_high_conflict).length;
      const trivialCount = data.trivial_count !== undefined ? data.trivial_count : zones.filter(z => z.is_trivial).length;
      const isolatedCount = data.isolated_zones_count !== undefined ? data.isolated_zones_count : zones.filter(z => !z.is_conflict).length;

      if (elHighConflict) elHighConflict.innerText = highCount;
      if (elTrivial) elTrivial.innerText = trivialCount;
      if (elIsolated) elIsolated.innerText = isolatedCount;

      let keptCount = 0;
      let totalInZones = 0;
      zones.forEach(z => {
        const cands = z.candidates || [];
        totalInZones += cands.length;
        if (z.is_exception) {
          keptCount += cands.length;
        } else {
          const sel = (z.selected_indices && z.selected_indices.length > 0) ? z.selected_indices.length : (z.selected_index !== undefined ? 1 : 0);
          keptCount += sel;
        }
      });
      const prunedCount = Math.max(0, totalInZones - keptCount);
      if (elPruned) elPruned.innerText = `${prunedCount} colonias`;

      // Barra de progreso y gamificación
      const filtered = getFilteredZoneList();
      const totalFiltered = filtered.length;
      const progText = document.getElementById('zoneGamifiedProgressText');
      const progBar = document.getElementById('zoneGamifiedProgressBar');
      const progPct = document.getElementById('zoneGamifiedPercentBadge');
      const streakBadge = document.getElementById('zoneStreakBadge');

      const curPos = totalFiltered > 0 ? Math.min(currentDeckIndex + 1, totalFiltered) : 0;
      const pct = totalFiltered > 0 ? Math.round((curPos / totalFiltered) * 100) : 100;

      if (progText) progText.innerText = `${curPos} / ${totalFiltered}`;
      if (progBar) progBar.style.width = `${pct}%`;
      if (progPct) progPct.innerText = `${pct}%`;

      if (streakBadge) {
        if (deckStreakCount >= 3) {
          streakBadge.classList.remove('hidden');
          streakBadge.innerText = `🔥 ${deckStreakCount} seguidas`;
        } else {
          streakBadge.classList.add('hidden');
        }
      }
    }

    function onToggleZoneCandidate(zoneId, originalIndex, isChecked) {
      if (!activeZoneClustersData) return;
      const zone = (activeZoneClustersData.zones || []).find(z => z.zone_id === zoneId);
      if (!zone) return;

      if (!Array.isArray(zone.selected_indices)) {
        zone.selected_indices = zone.selected_index !== undefined ? [zone.selected_index] : [];
      }

      if (isChecked) {
        if (!zone.selected_indices.includes(originalIndex)) {
          zone.selected_indices.push(originalIndex);
        }
        zone.selected_index = originalIndex;
      } else {
        zone.selected_indices = zone.selected_indices.filter(idx => idx !== originalIndex);
        if (zone.selected_index === originalIndex) {
          zone.selected_index = zone.selected_indices.length > 0 ? zone.selected_indices[0] : null;
        }
      }

      updateZoneMetricsUI(activeZoneClustersData);
      renderZoneClustersUI();
      renderZoneClustersOnMap();
    }

    function onToggleZoneException(zoneId, isChecked) {
      if (!activeZoneClustersData) return;
      const zone = (activeZoneClustersData.zones || []).find(z => z.zone_id === zoneId);
      if (zone) {
        zone.is_exception = isChecked;
        updateZoneMetricsUI(activeZoneClustersData);
        renderZoneClustersUI();
        renderZoneClustersOnMap();
      }
    }

    function resetAllZonesToSuggested() {
      if (!activeZoneClustersData) return;
      (activeZoneClustersData.zones || []).forEach(z => {
        z.selected_indices = [z.recommended_index];
        z.selected_index = z.recommended_index;
        z.is_exception = false;
      });
      deckStreakCount = 0;
      updateZoneMetricsUI(activeZoneClustersData);
      renderZoneClustersUI();
      renderZoneClustersOnMap();
      showToast("Se restablecieron todas las zonas a la sugerencia óptima", "info");
    }

    // =========================================================================
    // ACCIONES GAMIFICADAS DE LA BARAJA (DECK CONTROLS)
    // =========================================================================
    function deckAcceptAndNext() {
      const filtered = getFilteredZoneList();
      if (filtered.length === 0) return;
      const z = filtered[currentDeckIndex];
      if (z) {
        if (!z.is_exception && (!z.selected_indices || z.selected_indices.length === 0)) {
          z.selected_indices = [z.recommended_index];
          z.selected_index = z.recommended_index;
        }
        deckStreakCount++;
      }
      if (currentDeckIndex < filtered.length - 1) {
        currentDeckIndex++;
      } else {
        showToast("🎉 ¡Has completado la revisión de todas las zonas de este filtro!", "success");
      }
      updateZoneMetricsUI(activeZoneClustersData);
      renderZoneClustersUI();
      renderZoneClustersOnMap();
    }

    function deckToggleException() {
      const filtered = getFilteredZoneList();
      if (filtered.length === 0) return;
      const z = filtered[currentDeckIndex];
      if (z) {
        z.is_exception = !z.is_exception;
        deckStreakCount = 0;
      }
      if (currentDeckIndex < filtered.length - 1) {
        currentDeckIndex++;
      }
      updateZoneMetricsUI(activeZoneClustersData);
      renderZoneClustersUI();
      renderZoneClustersOnMap();
    }

    function deckPrevZone() {
      if (currentDeckIndex > 0) {
        currentDeckIndex--;
        deckStreakCount = 0;
        updateZoneMetricsUI(activeZoneClustersData);
        renderZoneClustersUI();
        renderZoneClustersOnMap();
      }
    }

    function deckNextZone() {
      const filtered = getFilteredZoneList();
      if (currentDeckIndex < filtered.length - 1) {
        currentDeckIndex++;
        deckStreakCount = 0;
        updateZoneMetricsUI(activeZoneClustersData);
        renderZoneClustersUI();
        renderZoneClustersOnMap();
      }
    }

    function deckSetChampion(zoneId, candOriginalIndex) {
      if (!activeZoneClustersData) return;
      const zone = (activeZoneClustersData.zones || []).find(z => z.zone_id === zoneId);
      if (!zone) return;
      zone.recommended_index = candOriginalIndex;
      zone.selected_indices = [candOriginalIndex];
      zone.selected_index = candOriginalIndex;
      zone.is_exception = false;
      updateZoneMetricsUI(activeZoneClustersData);
      renderZoneClustersUI();
      renderZoneClustersOnMap();
    }

    function jumpToZoneInDeck(zoneId) {
      setZoneViewMode('deck');
      const filtered = getFilteredZoneList();
      const idx = filtered.findIndex(z => z.zone_id === zoneId);
      if (idx !== -1) {
        currentDeckIndex = idx;
      } else {
        zoneFilterMode = 'all';
        const select = document.getElementById('zoneFilterModeSelect');
        if (select) select.value = 'all';
        const allFiltered = getFilteredZoneList();
        const allIdx = allFiltered.findIndex(z => z.zone_id === zoneId);
        if (allIdx !== -1) currentDeckIndex = allIdx;
      }
      updateZoneMetricsUI(activeZoneClustersData);
      renderZoneClustersUI();
      renderZoneClustersOnMap();
    }

    function quickAcceptZone(zoneId) {
      if (!activeZoneClustersData) return;
      const zone = (activeZoneClustersData.zones || []).find(z => z.zone_id === zoneId);
      if (zone) {
        zone.selected_indices = [zone.recommended_index];
        zone.selected_index = zone.recommended_index;
        zone.is_exception = false;
        deckStreakCount++;
        updateZoneMetricsUI(activeZoneClustersData);
        renderZoneClustersUI();
        renderZoneClustersOnMap();
        showToast(`✅ Zona ${zone.zone_number} aprobada con su sugerencia`, "info");
      }
    }

    // =========================================================================
    // RENDERIZADO PRINCIPAL DE INTERFACES SEGÚN MODO
    // =========================================================================
    function renderZoneClustersUI() {
      if (!activeZoneClustersData) return;
      if (zoneViewMode === 'deck') {
        renderZoneDeckUI();
      } else if (zoneViewMode === 'grid') {
        renderZoneGridUI();
      } else {
        renderZoneListUI();
      }
    }

    // --- 1. RENDERIZADO BARAJA GAMIFICADA (CARD DECK / DUELO DE ZONAS) ---
    function renderZoneDeckUI() {
      const cont = document.getElementById('zoneDeckContainer');
      if (!cont || !activeZoneClustersData) return;

      const filtered = getFilteredZoneList();
      if (filtered.length === 0) {
        cont.innerHTML = `
          <div class="flex-1 flex flex-col items-center justify-center p-8 bg-white border border-stone-200 rounded-xl text-center space-y-3 shadow-2xs">
            <div class="w-14 h-14 rounded-full bg-emerald-100 flex items-center justify-center text-2xl">🎉</div>
            <h4 class="text-base font-bold text-stone-900">¡No hay conflictos pendientes con este filtro!</h4>
            <p class="text-xs text-stone-500 max-w-md">Todas las zonas bajo el filtro seleccionado han sido resueltas o no contienen conflictos espaciales.</p>
            <button type="button" onclick="onZoneFilterModeChange('all')" class="px-3 py-1.5 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 text-xs font-bold rounded-lg border border-indigo-200 transition">
              Ver todas las zonas
            </button>
          </div>
        `;
        return;
      }

      currentDeckIndex = Math.max(0, Math.min(currentDeckIndex, filtered.length - 1));
      const z = filtered[currentDeckIndex];
      activeDeckZoneId = z.zone_id;

      const isExcept = z.is_exception === true;
      const candidates = z.candidates || [];
      const selectedIndices = new Set(z.selected_indices || (z.selected_index !== undefined ? [z.selected_index] : []));
      const keptCount = isExcept ? candidates.length : selectedIndices.size;

      // Separar micro-calles
      const regularCands = [];
      const microCands = [];
      candidates.forEach(c => {
        if (c.is_micro) microCands.push(c);
        else regularCands.push(c);
      });

      const recCand = candidates.find(c => c.original_index === z.recommended_index) || candidates[0];
      const otherCands = regularCands.filter(c => c.original_index !== recCand.original_index);

      const isRecSelected = selectedIndices.has(recCand.original_index);
      const recScaleIcon = recCand.scale === 'large' ? '👑' : (recCand.scale === 'small' ? '🏘️' : '🏙️');

      const conflictBadge = z.is_high_conflict
        ? '<span class="px-2 py-0.5 bg-rose-100 text-rose-800 border border-rose-200 rounded-full text-[10px] font-bold shrink-0">⚡ Alto Impacto</span>'
        : (z.is_conflict ? '<span class="px-2 py-0.5 bg-amber-100 text-amber-800 border border-amber-200 rounded-full text-[10px] font-bold shrink-0">⚠️ Conflicto</span>' : '<span class="px-2 py-0.5 bg-emerald-100 text-emerald-800 border border-emerald-200 rounded-full text-[10px] font-bold shrink-0">✅ Despejada</span>');

      // Tarjetas de rivales en cuadrícula
      let rivalCardsHtml = '';
      if (otherCands.length > 0) {
        rivalCardsHtml = otherCands.map(c => {
          const isSelected = selectedIndices.has(c.original_index);
          const scaleIcon = c.scale === 'large' ? '👑' : (c.scale === 'small' ? '🏘️' : '🏙️');
          const borderClass = isSelected
            ? 'border-indigo-400 bg-indigo-50/90 shadow-2xs ring-1 ring-indigo-300'
            : 'border-stone-200 bg-white hover:border-stone-300 opacity-80 hover:opacity-100';

          return `
            <div class="p-2 rounded-lg border transition ${borderClass} flex flex-col justify-between space-y-1.5">
              <div class="flex items-start justify-between gap-1">
                <label class="flex items-center space-x-1.5 cursor-pointer min-w-0 flex-1">
                  <input type="checkbox" ${isSelected ? 'checked' : ''} onchange="onToggleZoneCandidate('${z.zone_id}', ${c.original_index}, this.checked)" class="rounded text-indigo-600 focus:ring-0 shrink-0 cursor-pointer">
                  <span title="${escapeHtml(zoneScoreExplanation(c))}" class="text-xs font-semibold text-stone-900 truncate ${!isSelected ? 'line-through text-stone-400' : ''}">${scaleIcon} ${escapeHtml(c.name)}</span>
                </label>
                <button type="button" onclick="deckSetChampion('${z.zone_id}', ${c.original_index})" class="px-1.5 py-0.5 bg-stone-100 hover:bg-emerald-50 text-stone-500 hover:text-emerald-700 hover:border-emerald-300 border border-stone-200 rounded text-[9px] font-bold transition shrink-0" title="Hacer a este el ganador único de la zona">
                  👑 Ganador
                </button>
              </div>
              <div class="flex items-center justify-between text-[10px] text-stone-500 font-mono pt-1 border-t border-stone-100">
                <span class="bg-stone-50 px-1 py-0.2 rounded border border-stone-200">${c.establishments > 0 ? `🏢 ${c.establishments}` : '🏢 0'} DENUE</span>
                <span class="text-stone-400">📍 a ${Math.round(c.distance_m)}m</span>
              </div>
            </div>
          `;
        }).join('');
      } else {
        rivalCardsHtml = `
          <div class="col-span-2 py-3 text-center text-stone-400 text-xs bg-white rounded-lg border border-dashed border-stone-200">
            No hay otras colonias principales compitiendo en esta zona.
          </div>
        `;
      }

      // Micro-calles plegables
      let microSectionHtml = '';
      if (microCands.length > 0) {
        const microBadges = microCands.map(c => {
          const isSelected = selectedIndices.has(c.original_index);
          return `
            <label class="inline-flex items-center space-x-1 px-2 py-0.5 bg-white border ${isSelected ? 'border-indigo-400 text-indigo-800 font-bold' : 'border-stone-200 text-stone-500 line-through'} rounded text-[10px] cursor-pointer hover:bg-stone-50">
              <input type="checkbox" ${isSelected ? 'checked' : ''} onchange="onToggleZoneCandidate('${z.zone_id}', ${c.original_index}, this.checked)" class="rounded text-indigo-600 focus:ring-0 w-3 h-3">
              <span class="truncate max-w-[120px]">${escapeHtml(c.name)}</span>
            </label>
          `;
        }).join('');

        microSectionHtml = `
          <div class="bg-stone-100/80 border border-dashed border-stone-300 rounded-xl p-2.5 space-y-1.5">
            <div class="flex items-center justify-between cursor-pointer" onclick="toggleZoneMicroList('${z.zone_id}')">
              <div class="flex items-center space-x-1.5 text-xs text-stone-600 font-semibold">
                <i data-lucide="chevrons-down" class="w-3.5 h-3.5 text-stone-400"></i>
                <span>📦 ${microCands.length} micro-calles (cerradas / privadas) podadas</span>
              </div>
              <span class="text-[10px] text-indigo-600 font-bold">Ver / Recuperar</span>
            </div>
            <div id="zoneMicro_${z.zone_id}" class="hidden flex flex-wrap gap-1 pt-1.5 border-t border-stone-200">
              ${microBadges}
            </div>
          </div>
        `;
      }

      cont.innerHTML = `
        <div class="flex-1 flex flex-col justify-between bg-white border border-stone-200 rounded-xl p-4 shadow-sm space-y-3 overflow-y-auto">

          <!-- Encabezado de la Tarjeta de Duelo -->
          <div class="flex items-center justify-between border-b border-stone-100 pb-2.5 shrink-0">
            <div class="flex items-center space-x-2 min-w-0">
              <span class="font-extrabold text-stone-900 text-sm truncate">Zona ${z.zone_number}: ${escapeHtml(z.title)}</span>
              ${conflictBadge}
              <span class="px-2 py-0.5 bg-stone-100 border border-stone-200 rounded-full text-[10px] font-mono text-stone-600 font-bold shrink-0">
                Radio: ${z.radius_m || 1000}m
              </span>
            </div>
            <!-- Navegación 1 a 1 -->
            <div class="flex items-center space-x-1 shrink-0">
              <button type="button" onclick="deckPrevZone()" class="p-1 text-stone-500 hover:text-stone-900 hover:bg-stone-100 rounded transition cursor-pointer" title="Zona anterior (Flecha Izquierda)">
                <i data-lucide="chevron-left" class="w-4 h-4"></i>
              </button>
              <span class="text-[11px] font-mono text-stone-500 px-1 font-bold">${currentDeckIndex + 1} / ${filtered.length}</span>
              <button type="button" onclick="deckNextZone()" class="p-1 text-stone-500 hover:text-stone-900 hover:bg-stone-100 rounded transition cursor-pointer" title="Zona siguiente (Flecha Abajo o S)">
                <i data-lucide="chevron-right" class="w-4 h-4"></i>
              </button>
            </div>
          </div>

          <!-- 👑 HERO CARD: CAMPEÓN SUGERIDO -->
          <div class="bg-gradient-to-r from-emerald-50 via-teal-50/50 to-emerald-50/30 border-2 ${isRecSelected ? 'border-emerald-500 shadow-xs' : 'border-stone-300 opacity-60'} rounded-xl p-3 space-y-2 transition shrink-0">
            <div class="flex items-center justify-between">
              <div class="flex items-center space-x-1.5 text-emerald-800 text-xs font-extrabold">
                <span class="text-sm">👑</span>
                <span>CANDIDATO SUGERIDO (criterio seleccionado)</span>
              </div>
              <div class="flex items-center space-x-1">
                ${isRecSelected ? '<span class="px-2 py-0.5 bg-emerald-600 text-white rounded-md text-[10px] font-bold shadow-2xs flex items-center space-x-1"><i data-lucide="check" class="w-3 h-3"></i><span>Campeón Activo</span></span>' : `<button onclick="deckSetChampion('${z.zone_id}', ${recCand.original_index})" class="px-2 py-0.5 bg-white hover:bg-emerald-100 text-emerald-800 border border-emerald-300 rounded-md text-[10px] font-bold transition">Reelegir Campeón</button>`}
              </div>
            </div>

            <div class="flex items-center justify-between">
              <div class="flex items-center space-x-2">
                <span class="text-lg">${recScaleIcon}</span>
                <span class="text-base font-extrabold text-stone-900 tracking-tight">${escapeHtml(recCand.name)}</span>
              </div>
              <div class="flex items-center space-x-2 text-[11px] font-mono">
                <span class="bg-white/90 px-2 py-0.5 rounded-lg border border-emerald-200 text-emerald-900 font-bold shadow-2xs">
                  🏢 ${recCand.establishments || 0} comercios DENUE
                </span>
                <span class="bg-white/90 px-2 py-0.5 rounded-lg border border-emerald-200 text-emerald-900 font-bold shadow-2xs">
                  📍 Ancla Central
                </span>
              </div>
            </div>
          </div>

          <!-- ⚔️ GRID DE CONTENDIENTES RIVALES -->
          <div class="space-y-1.5 flex-1 min-h-[120px]">
            <div class="flex items-center justify-between text-xs text-stone-600 font-bold">
              <span>⚔️ Contendientes Rivales (${otherCands.length}):</span>
              <span class="text-[10px] text-stone-400 font-normal">Marca casillas para conservar o haz clic en 👑 para cambiar ganador</span>
            </div>
            <div class="grid grid-cols-2 gap-2 max-h-[160px] overflow-y-auto pr-1">
              ${rivalCardsHtml}
            </div>
          </div>

          <!-- Micro-calles plegables -->
          ${microSectionHtml}

          <!-- Banner de Excepción -->
          <div class="flex items-center justify-between bg-stone-50 border border-stone-200 rounded-lg px-3 py-1.5 text-xs shrink-0">
            <label class="flex items-center space-x-2 cursor-pointer select-none text-stone-700 font-semibold hover:text-indigo-700">
              <input type="checkbox" ${isExcept ? 'checked' : ''} onchange="onToggleZoneException('${z.zone_id}', this.checked)" class="rounded border-stone-300 text-indigo-600 focus:ring-0">
              <span>Excepción: Conservar todas las colonias de esta zona</span>
            </label>
            <span class="text-[10px] font-mono ${isExcept ? 'text-indigo-700 font-bold' : 'text-rose-600'}">
              ${isExcept ? '0 colonias podadas' : `Se podarán ${Math.max(0, candidates.length - keptCount)} colonias`}
            </span>
          </div>

          <!-- CONTROLES ESTILO VIDEOJUEGO (BOTTOM ACTIONS) -->
          <div class="pt-2 border-t border-stone-100 flex items-center justify-between gap-2 shrink-0">
            <button type="button" onclick="deckPrevZone()" class="px-3 py-2 bg-stone-100 hover:bg-stone-200 text-stone-700 text-xs font-bold rounded-xl transition flex items-center space-x-1 cursor-pointer" title="Atajo: Flecha Izquierda">
              <i data-lucide="arrow-left" class="w-3.5 h-3.5"></i>
              <span>Anterior</span>
            </button>

            <button type="button" onclick="deckToggleException()" class="px-3.5 py-2 ${isExcept ? 'bg-indigo-600 text-white' : 'bg-indigo-50 hover:bg-indigo-100 text-indigo-800 border border-indigo-200'} text-xs font-bold rounded-xl transition flex items-center space-x-1.5 cursor-pointer" title="Atajo: Tecla E">
              <i data-lucide="shield" class="w-3.5 h-3.5"></i>
              <span>${isExcept ? 'Excepción Activa' : 'Conservar Todas [E]'}</span>
            </button>

            <button type="button" onclick="deckAcceptAndNext()" class="flex-1 py-2.5 bg-emerald-600 hover:bg-emerald-700 active:bg-emerald-800 text-white text-xs font-extrabold rounded-xl transition shadow-sm flex items-center justify-center space-x-2 cursor-pointer" title="Atajo: Enter, Espacio o Flecha Derecha">
              <i data-lucide="check-check" class="w-4 h-4"></i>
              <span>Aceptar y Siguiente ➔</span>
            </button>
          </div>

          <!-- Mini Guía de Atajos de Teclado -->
          <div class="text-[10px] text-stone-400 text-center font-mono shrink-0">
            Atajos: <kbd class="px-1 py-0.2 bg-stone-100 rounded border border-stone-300 text-stone-600 font-bold">Enter</kbd> o <kbd class="px-1 py-0.2 bg-stone-100 rounded border border-stone-300 text-stone-600 font-bold">→</kbd> Aceptar  •  <kbd class="px-1 py-0.2 bg-stone-100 rounded border border-stone-300 text-stone-600 font-bold">E</kbd> Excepción  •  <kbd class="px-1 py-0.2 bg-stone-100 rounded border border-stone-300 text-stone-600 font-bold">←</kbd> Anterior
          </div>

        </div>
      `;

      if (window.lucide) lucide.createIcons();
      if (z.center && z.center.length === 2) {
        focusZoneOnPreviewMap(z.zone_id);
      }
    }

    // --- 2. RENDERIZADO TABLERO DE CARDS (GRID KANBAN) ---
    function renderZoneGridUI() {
      const grid = document.getElementById('zoneGridCards');
      if (!grid || !activeZoneClustersData) return;

      const filtered = getFilteredZoneList();
      grid.innerHTML = '';

      if (filtered.length === 0) {
        grid.innerHTML = `
          <div class="col-span-2 p-8 text-center text-stone-500 text-xs bg-white rounded-xl border border-stone-200">
            No se encontraron zonas que coincidan con los filtros.
          </div>
        `;
        return;
      }

      filtered.forEach(z => {
        const isExcept = z.is_exception === true;
        const candidates = z.candidates || [];
        const selectedIndices = new Set(z.selected_indices || (z.selected_index !== undefined ? [z.selected_index] : []));
        const keptCount = isExcept ? candidates.length : selectedIndices.size;
        const prunedCount = Math.max(0, candidates.length - keptCount);

        const recCand = candidates.find(c => c.original_index === z.recommended_index) || candidates[0];

        const card = document.createElement('div');
        card.id = `gridZone_${z.zone_id}`;
        card.className = `p-3 rounded-xl border transition text-xs space-y-2.5 bg-white shadow-2xs hover:shadow-sm ${z.is_high_conflict ? 'border-rose-200' : 'border-stone-200'}`;
        card.innerHTML = `
          <div class="flex items-start justify-between gap-1">
            <div class="min-w-0">
              <span class="font-bold text-stone-900 truncate block">Zona ${z.zone_number}: ${escapeHtml(z.title)}</span>
              <span class="text-[10px] text-stone-400 font-mono">${candidates.length} colonias compitiendo</span>
            </div>
            ${z.is_high_conflict ? '<span class="px-1.5 py-0.2 bg-rose-100 text-rose-800 rounded text-[9px] font-bold shrink-0">⚡ Alto</span>' : ''}
          </div>

          <div class="bg-emerald-50/80 border border-emerald-200 rounded-lg p-2 space-y-1">
            <div class="text-[10px] text-emerald-800 font-bold flex items-center space-x-1">
              <span>👑</span>
              <span class="truncate">${escapeHtml(recCand.name)}</span>
            </div>
            <div class="text-[9px] text-emerald-700 font-mono">
              🏢 ${recCand.establishments || 0} DENUE
            </div>
          </div>

          <div class="flex items-center justify-between text-[10px] text-stone-500 font-mono">
            <span>Conservando: <strong class="text-stone-800">${keptCount}</strong></span>
            <span class="text-rose-600 font-bold">Podadas: ${prunedCount}</span>
          </div>

          <div class="flex items-center space-x-1.5 pt-1 border-t border-stone-100">
            <button type="button" onclick="jumpToZoneInDeck('${z.zone_id}')" class="flex-1 py-1 bg-stone-100 hover:bg-indigo-50 text-stone-700 hover:text-indigo-800 border border-stone-200 rounded-lg text-[10px] font-bold transition flex items-center justify-center space-x-1 cursor-pointer">
              <i data-lucide="swords" class="w-3 h-3"></i>
              <span>Resolver en Duelo</span>
            </button>
            <button type="button" onclick="quickAcceptZone('${z.zone_id}')" class="p-1 bg-emerald-50 hover:bg-emerald-100 text-emerald-800 border border-emerald-200 rounded-lg text-[10px] font-bold transition cursor-pointer" title="Aprobar de inmediato">
              <i data-lucide="check" class="w-3.5 h-3.5"></i>
            </button>
          </div>
        `;
        grid.appendChild(card);
      });

      if (window.lucide) lucide.createIcons();
    }

    // --- 3. RENDERIZADO LISTA DETALLADA (LEGACY AUDITORÍA) ---
    function renderZoneListUI() {
      const cont = document.getElementById('zoneClustersListContainer');
      if (!cont || !activeZoneClustersData) return;

      const filteredZones = getFilteredZoneList();
      cont.innerHTML = '';

      if (filteredZones.length === 0) {
        cont.innerHTML = `
          <div class="p-6 text-center text-stone-500 text-xs bg-white rounded-xl border border-stone-200">
            No se encontraron zonas que coincidan con la búsqueda.
          </div>
        `;
        return;
      }

      filteredZones.forEach((z) => {
        const card = document.createElement('div');
        card.id = `zoneCard_${z.zone_id}`;
        const isExcept = z.is_exception === true;
        const candidates = z.candidates || [];
        const selectedIndices = new Set(z.selected_indices || (z.selected_index !== undefined ? [z.selected_index] : []));
        const keptCount = isExcept ? candidates.length : selectedIndices.size;

        const regularCands = [];
        const microCands = [];
        candidates.forEach(c => {
          if (c.is_micro) microCands.push(c);
          else regularCands.push(c);
        });

        const statusColor = isExcept ? 'border-indigo-300 bg-indigo-50/30' : (z.is_high_conflict ? 'border-rose-200 bg-white shadow-2xs' : (z.is_conflict ? 'border-amber-200 bg-white' : 'border-stone-200 bg-stone-50/60'));

        const renderCandidateItem = (c) => {
          const isSelected = selectedIndices.has(c.original_index);
          const isRec = c.recommended === true;
          const isExtra = isSelected && !isRec;
          const estText = c.establishments > 0 ? `🏢 ${c.establishments} DENUE` : '🏢 Sin DENUE';
          const distText = c.distance_m > 0 ? `📍 a ${Math.round(c.distance_m)}m` : '📍 Anchor';
          const scaleIcon = c.scale === 'large' ? '👑' : (c.scale === 'small' ? '🏘️' : '🏙️');

          let itemClass = 'bg-stone-50/60 border-stone-200 hover:bg-stone-100 text-stone-600 opacity-60';
          if (isSelected) {
            itemClass = isRec
              ? 'bg-emerald-50/90 border-emerald-400 text-stone-900 font-semibold shadow-2xs ring-1 ring-emerald-200'
              : 'bg-indigo-50/90 border-indigo-400 text-stone-900 font-semibold shadow-2xs ring-1 ring-indigo-200';
          }

          return `
            <label class="flex items-center justify-between p-1.5 rounded-lg border cursor-pointer transition ${itemClass}">
              <div class="flex items-center space-x-2 min-w-0 flex-1">
                <input type="checkbox" ${isSelected ? 'checked' : ''} onchange="onToggleZoneCandidate('${z.zone_id}', ${c.original_index}, this.checked)" class="rounded text-emerald-600 focus:ring-0 shrink-0 cursor-pointer">
                <div class="truncate flex items-center space-x-1.5 text-xs">
                  <span>${scaleIcon}</span>
                  <span title="${escapeHtml(zoneScoreExplanation(c))}" class="truncate ${!isSelected ? 'line-through text-stone-400' : ''}">${escapeHtml(c.name || 'Sin nombre')}</span>
                  ${isRec ? '<span class="px-1.5 py-0.2 bg-emerald-600 text-white rounded text-[9px] font-bold shrink-0">★ Sugerido</span>' : ''}
                  ${isExtra ? '<span class="px-1.5 py-0.2 bg-indigo-600 text-white rounded text-[9px] font-bold shrink-0">+ Conservada</span>' : ''}
                </div>
              </div>
              <div class="flex items-center space-x-1.5 shrink-0 text-[10px] text-stone-500 font-mono ml-2">
                <span class="px-1 py-0.2 bg-white/80 rounded border border-stone-200">${estText}</span>
                <span class="text-stone-400">${distText}</span>
              </div>
            </label>
          `;
        };

        let regularHtml = regularCands.map(renderCandidateItem).join('');

        let microHtml = '';
        if (microCands.length > 0) {
          const microItemsHtml = microCands.map(renderCandidateItem).join('');
          microHtml = `
            <div class="pt-1">
              <button type="button" onclick="toggleZoneMicroList('${z.zone_id}')" class="w-full py-1 px-2 text-[10px] text-stone-500 hover:text-stone-800 bg-stone-100 hover:bg-stone-200/80 rounded border border-dashed border-stone-300 flex items-center justify-between transition cursor-pointer">
                <span class="flex items-center space-x-1">
                  <i data-lucide="chevrons-down" class="w-3 h-3 text-stone-400"></i>
                  <span>${microCands.length} micro-calles (cerradas / privadas)</span>
                </span>
                <span class="text-stone-400 text-[9px] font-mono">Click para ver</span>
              </button>
              <div id="zoneMicro_${z.zone_id}" class="hidden space-y-1 pt-1 pl-2 border-l-2 border-stone-200">
                ${microItemsHtml}
              </div>
            </div>
          `;
        }

        const conflictBadge = z.is_high_conflict
          ? '<span class="px-1.5 py-0.2 bg-rose-100 text-rose-800 border border-rose-200 rounded text-[9px] font-bold">⚡ Alto Impacto</span>'
          : (z.is_conflict ? '<span class="px-1.5 py-0.2 bg-amber-100 text-amber-800 border border-amber-200 rounded text-[9px] font-bold">⚠️ Conflicto</span>' : '');

        card.className = `p-3 rounded-xl border transition text-xs space-y-2 ${statusColor}`;
        card.innerHTML = `
          <div class="flex items-center justify-between border-b border-stone-100 pb-1.5">
            <div class="flex items-center space-x-2 min-w-0">
              <span class="w-2 h-2 rounded-full ${isExcept ? 'bg-indigo-500' : (z.is_high_conflict ? 'bg-rose-500' : (z.is_conflict ? 'bg-amber-500' : 'bg-emerald-500'))} shrink-0"></span>
              <span class="font-bold text-stone-900 truncate">Zona ${z.zone_number}: ${escapeHtml(z.title)}</span>
              ${conflictBadge}
              <span class="px-2 py-0.5 rounded-full text-[10px] font-mono font-bold ${keptCount > 1 ? 'bg-indigo-100 text-indigo-800 border border-indigo-200' : 'bg-stone-100 text-stone-600 border border-stone-200'} shrink-0">
                Conservando ${keptCount} de ${candidates.length}
              </span>
            </div>
            <button onclick="focusZoneOnPreviewMap('${z.zone_id}')" class="text-indigo-600 hover:text-indigo-800 font-bold text-[11px] flex items-center space-x-0.5 shrink-0 cursor-pointer" title="Centrar zona en el mapa">
              <i data-lucide="map-pin" class="w-3 h-3"></i>
              <span>Ver</span>
            </button>
          </div>

          <div class="space-y-1">
            ${regularHtml}
            ${microHtml}
          </div>

          <div class="flex items-center justify-between pt-1 border-t border-stone-100 text-[11px]">
            <label class="flex items-center space-x-1.5 cursor-pointer select-none text-stone-600 font-medium hover:text-indigo-700">
              <input type="checkbox" ${isExcept ? 'checked' : ''} onchange="onToggleZoneException('${z.zone_id}', this.checked)" class="rounded border-stone-300 text-indigo-600 focus:ring-0">
              <span>Excepción: Mantener todas las de esta zona</span>
            </label>
            ${isExcept ? '<span class="text-indigo-600 font-bold text-[10px]">No se podará nada</span>' : `<span class="text-rose-600 text-[10px] font-medium">Se podarán ${Math.max(0, candidates.length - keptCount)}</span>`}
          </div>
        `;
        cont.appendChild(card);
      });
      if (window.lucide) lucide.createIcons();
    }

    function renderZoneClustersOnMap() {
      if (!mapZonePreview || !zonePreviewLayerGroup || !activeZoneClustersData) return;
      zonePreviewLayerGroup.clearLayers();

      const zones = activeZoneClustersData.zones || [];
      const status = document.getElementById('zoneMapStatus');
      const radius = activeZoneClustersData.radius_m || 1000;
      if (status) status.innerText = `${zones.length} zonas · Radio ${radius} m`;

      zones.forEach(z => {
        if (!z.center || z.center.length !== 2) return;
        const latLng = [z.center[1], z.center[0]];
        const isExcept = z.is_exception === true;
        const isHighConflict = z.is_high_conflict === true;
        const isConflict = z.is_conflict === true;
        const isActiveZone = (activeDeckZoneId === z.zone_id);

        let circleColor = isExcept ? '#6366f1' : (isHighConflict ? '#f43f5e' : (isConflict ? '#d97706' : '#10b981'));
        if (isActiveZone) circleColor = '#4f46e5';

        const circle = L.circle(latLng, {
          radius: z.radius_m || radius,
          color: circleColor,
          weight: isActiveZone ? 3.5 : (isHighConflict ? 2 : (isConflict ? 1.5 : 1)),
          dashArray: isActiveZone ? null : '3, 4',
          fillColor: circleColor,
          fillOpacity: isActiveZone ? 0.16 : 0.06
        }).addTo(zonePreviewLayerGroup);

        circle.on('click', () => {
          if (zoneViewMode === 'deck') {
            jumpToZoneInDeck(z.zone_id);
          } else {
            focusZoneInList(z.zone_id);
          }
        });

        const selectedSet = new Set(z.selected_indices || (z.selected_index !== undefined ? [z.selected_index] : []));

        (z.candidates || []).forEach(c => {
          if (!c.loc || c.loc.length !== 2) return;
          const cLatLng = [c.loc[1], c.loc[0]];
          const isSelected = selectedSet.has(c.original_index);
          const isRec = (c.original_index === z.recommended_index);

          let markerColor = '#f43f5e';
          if (isExcept) {
            markerColor = '#6366f1';
          } else if (isSelected) {
            markerColor = isRec ? '#10b981' : '#3b82f6';
          }

          const markerRadius = isActiveZone
            ? (isSelected ? 8 : 5)
            : (isSelected ? 6 : (isExcept ? 5 : 3.5));

          const cm = L.circleMarker(cLatLng, {
            radius: markerRadius,
            fillColor: markerColor,
            color: isActiveZone && isRec ? '#fef08a' : '#ffffff',
            weight: isActiveZone ? 2.5 : 1.5,
            opacity: 1,
            fillOpacity: isSelected || isExcept ? 0.95 : 0.35
          }).addTo(zonePreviewLayerGroup);

          const statusText = isExcept ? 'Excepción' : (isSelected ? (isRec ? '★ Ganador Sugerido' : '+ Conservada') : 'A descartar');

          cm.bindTooltip(`
            <div class="p-1.5 text-stone-900 text-xs font-sans">
              <strong class="${isSelected ? 'text-emerald-700' : (isExcept ? 'text-indigo-700' : 'text-stone-500 line-through')}">${escapeHtml(c.name)}</strong>
              <div class="text-[10px] text-stone-500">🏢 ${c.establishments || 0} DENUE | <span class="font-bold">${statusText}</span></div>
              <div class="text-[9px] text-stone-400">Click para alternar selección</div>
            </div>
          `, { direction: 'top', className: 'poi-custom-tooltip' });

          cm.on('click', () => {
            onToggleZoneCandidate(z.zone_id, c.original_index, !isSelected);
            if (zoneViewMode === 'deck') {
              jumpToZoneInDeck(z.zone_id);
            } else {
              focusZoneInList(z.zone_id);
            }
          });
        });
      });
    }

    function focusZoneOnPreviewMap(zoneId) {
      if (!mapZonePreview || !activeZoneClustersData) return;
      const zone = (activeZoneClustersData.zones || []).find(z => z.zone_id === zoneId);
      if (zone && zone.center && zone.center.length === 2) {
        mapZonePreview.flyTo([zone.center[1], zone.center[0]], 14, { duration: 0.5 });
      }
    }

    async function applyZoneThinningSelections() {
      if (!activeZoneClustersData || !activeZoneClustersData.zones) return;
      const places = cityData.places || [];
      const context = activeZoneClustersContext;
      if (!context || context.file !== currentCityFile || context.version !== cityLoadVersion ||
          context.snapshot !== JSON.stringify(places)) {
        showToast('La lista o el proyecto cambió. Recalcula la poda antes de aplicar.', 'warning');
        return;
      }
      const zones = activeZoneClustersData.zones;

      const btn = document.getElementById('btnApplyZoneThinning');
      if (btn) btn.disabled = true;

      showToast("Aplicando poda espacial y guardando cambios...", "info");

      try {
        const payloadZones = zones.map(z => ({
          zone_id: z.zone_id,
          selected_indices: z.selected_indices || (z.selected_index !== undefined ? [z.selected_index] : []),
          selected_index: z.selected_index,
          is_exception: z.is_exception === true,
          candidates: z.candidates
        }));

        const res = await fetch('/api/toponymy/apply-thinning', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ places: places, zones: payloadZones })
        });
        const data = await res.json();
        if (context.file !== currentCityFile || context.version !== cityLoadVersion ||
            context.snapshot !== JSON.stringify(cityData.places || [])) return;
        if (data.status === 'ok') {
          if (places.length > 0) {
            placesHistorySnapshot = JSON.parse(JSON.stringify(places));
            updateUndoButtonUI();
          }

          const kept = new Set((data.places || []).map(p=>p.id));
          const removed = places.filter(p=>p.id ? !kept.has(p.id) : !(data.places || []).some(a=>sameToponymyPlace(a,p)));
          cityData.deleted_places = [...(cityData.deleted_places || []), ...removed];
          cityData.places = data.places || [];
          selectedPlaceIndices.clear();
          lastSelectedPlaceIndex = null;
          renderPlacesList();
          renderPlacesMarkersOnMap();
          const saved = await saveCurrentCity(true);
          if (context.file !== currentCityFile || context.version !== cityLoadVersion) return;
          if (!saved) throw new Error('La poda quedó como borrador. Pulsa Guardar YAML para reintentar.');
          activeZoneClustersContext = null;
          closeZoneThinningModal();
          showToast(`¡Poda exitosa! Se conservaron ${data.total_kept} colonias y se podaron ${data.total_pruned}`, "success");
        } else {
          showToast(`Error al aplicar poda: ${data.message}`, "error");
        }
      } catch (err) {
        showToast(`Fallo de conexión: ${err.message}`, "error");
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    // -------------------------------------------------------------------------
    // RESOLUCIÓN INTELIGENTE DE DUPLICADOS DE TOPONIMIA (EXACTOS Y DIFUSOS)
    // -------------------------------------------------------------------------
    let currentDuplicateGroups = [];

    async function openDuplicateResolverModal() {
      const modal = document.getElementById('modalDuplicateResolver');
      if (!modal) return;
      modal.classList.remove('hidden');
      await scanForToponymyDuplicates();
    }

    function closeDuplicateResolverModal() {
      const modal = document.getElementById('modalDuplicateResolver');
      if (modal) modal.classList.add('hidden');
    }

    async function scanForToponymyDuplicates() {
      const statusEl = document.getElementById('dupResolutionStatus');
      const container = document.getElementById('dupGroupsContainer');
      const badge = document.getElementById('dupTotalBadge');
      const distSelect = document.getElementById('dupDistanceThreshold');
      const distM = distSelect ? parseFloat(distSelect.value) || 3500.0 : 3500.0;

      const places = cityData.places || [];
      if (places.length === 0) {
        if (container) {
          container.innerHTML = `<div class="p-8 text-center text-stone-500 bg-stone-50 rounded-xl border border-stone-200">No hay toponimia cargada en este proyecto.</div>`;
        }
        if (badge) badge.innerText = "0 duplicados";
        return;
      }

      if (statusEl) statusEl.innerText = 'Escaneando duplicados en la toponimia...';
      if (container) {
        container.innerHTML = `
          <div class="p-8 text-center text-stone-500 bg-stone-50 rounded-xl border border-stone-200 space-y-2">
            <i data-lucide="loader-2" class="w-7 h-7 animate-spin mx-auto text-metro-orange"></i>
            <div class="font-bold text-xs text-stone-700">Analizando nombres idénticos y proximidad geográfica (< ${distM} m)...</div>
          </div>
        `;
        if (window.lucide) lucide.createIcons();
      }

      try {
        const resp = await fetch('/api/toponymy/find-duplicates', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            places: places,
            distance_m: distM
          })
        });
        const data = await resp.json();
        currentDuplicateGroups = data.groups || [];

        if (badge) {
          badge.innerText = `${data.total_redundant || 0} duplicados en ${currentDuplicateGroups.length} grupos`;
        }

        const topBadge = document.getElementById('badgeDupCount');
        if (topBadge) {
          if (data.total_redundant > 0) {
            topBadge.innerText = data.total_redundant;
            topBadge.classList.remove('hidden');
          } else {
            topBadge.classList.add('hidden');
          }
        }

        renderDuplicateGroupsUI();
      } catch (err) {
        if (statusEl) statusEl.innerText = `Error al escanear: ${err.message}`;
        showToast(`Error al escanear duplicados: ${err.message}`, "error");
      }
    }

    function renderDuplicateGroupsUI() {
      const container = document.getElementById('dupGroupsContainer');
      const statusEl = document.getElementById('dupResolutionStatus');
      if (!container) return;

      container.innerHTML = '';
      if (!currentDuplicateGroups || currentDuplicateGroups.length === 0) {
        container.innerHTML = `
          <div class="p-8 text-center text-stone-500 bg-stone-50 rounded-xl border border-stone-200 space-y-2">
            <i data-lucide="check-circle" class="w-10 h-10 text-emerald-500 mx-auto"></i>
            <div class="font-bold text-sm text-stone-800">¡Toponimia limpia sin duplicados!</div>
            <p class="text-xs text-stone-500 max-w-sm mx-auto">Todos los nombres y asentamientos son únicos y no tienen conflictos espaciales.</p>
          </div>
        `;
        if (statusEl) statusEl.innerText = 'No se encontraron duplicados que requieran resolución.';
        if (window.lucide) lucide.createIcons();
        return;
      }

      let totalRedundant = 0;

      currentDuplicateGroups.forEach(grp => {
        totalRedundant += (grp.candidates.length - 1);
        const card = document.createElement('div');
        card.className = "p-3 bg-white border border-stone-200 rounded-xl space-y-2.5 shadow-xs";

        const isExact = grp.match_type === 'EXACT';
        const typeBadge = isExact
          ? `<span class="px-2 py-0.5 bg-rose-100 text-rose-800 font-bold text-[10px] rounded border border-rose-200">Nombre Idéntico</span>`
          : `<span class="px-2 py-0.5 bg-amber-100 text-amber-800 font-bold text-[10px] rounded border border-amber-200">Variante por Proximidad</span>`;

        let candidatesHtml = '';
        grp.candidates.forEach(cand => {
          const isRec = cand.is_recommended;
          const isChecked = isRec;
          const scaleIcon = cand.scale === 'large' ? '👑' : (cand.scale === 'small' ? '🏘️' : '🏙️');
          const scaleText = cand.scale === 'large' ? 'Grande (18px)' : (cand.scale === 'small' ? 'Chico (10px)' : 'Mediano (13px)');

          candidatesHtml += `
            <label class="flex items-start space-x-2.5 p-2 rounded-lg border transition cursor-pointer ${isRec ? 'bg-emerald-50/70 border-emerald-300 ring-1 ring-emerald-200' : 'bg-stone-50/50 border-stone-200 hover:bg-stone-100/60'}">
              <input type="radio" name="dup_choice_${grp.group_id}" value="${cand.original_index}" ${isChecked ? 'checked' : ''} class="mt-1 text-metro-orange focus:ring-0 cursor-pointer">
              <div class="flex-1 min-w-0 text-xs">
                <div class="flex items-center justify-between">
                  <span class="font-bold text-stone-900 truncate flex items-center space-x-1.5">
                    <span>${cand.name}</span>
                    ${isRec ? '<span class="px-1.5 py-0.2 bg-emerald-600 text-white rounded text-[9px] font-bold shadow-xs">⭐ Ganador Recomendado</span>' : ''}
                  </span>
                  <span class="text-[10px] font-mono text-stone-500">${cand.distance_m > 0 ? `a ${cand.distance_m}m` : 'referencia'}</span>
                </div>
                <div class="flex items-center space-x-3 text-[10px] text-stone-500 mt-1">
                  <span class="flex items-center space-x-0.5">
                    <span>${scaleIcon}</span>
                    <span class="font-semibold text-stone-700">${scaleText}</span>
                  </span>
                  <span>Origen: <strong>${cand.source}</strong></span>
                  <span class="font-mono">[${cand.loc[0].toFixed(3)}, ${cand.loc[1].toFixed(3)}]</span>
                  ${cand.establishments ? `<span class="text-sky-700 font-semibold">DENUE: ${cand.establishments}</span>` : ''}
                </div>
              </div>
            </label>
          `;
        });

        candidatesHtml += `
          <label class="flex items-center space-x-2 p-1.5 rounded text-[11px] text-stone-500 hover:text-stone-800 cursor-pointer select-none">
            <input type="radio" name="dup_choice_${grp.group_id}" value="keep_all" class="text-stone-400 focus:ring-0 cursor-pointer">
            <span>Conservar ambos en este grupo (no eliminar ninguno)</span>
          </label>
        `;

        card.innerHTML = `
          <div class="flex justify-between items-center border-b border-stone-100 pb-2">
            <div class="flex items-center space-x-2">
              <span class="font-bold text-sm text-stone-900">${grp.title}</span>
              ${typeBadge}
              <span class="text-[10px] text-stone-400">(${grp.candidates.length} elementos en conflicto)</span>
            </div>
          </div>
          <div class="space-y-1.5">
            ${candidatesHtml}
          </div>
        `;
        container.appendChild(card);
      });

      if (statusEl) {
        statusEl.innerText = `${currentDuplicateGroups.length} grupos detectados. Se eliminarán ${totalRedundant} duplicados manteniendo los ganadores marcados.`;
      }
      if (window.lucide) lucide.createIcons();
    }

    function markAllRecommendedWinners() {
      if (!currentDuplicateGroups) return;
      currentDuplicateGroups.forEach(grp => {
        const recInput = document.querySelector(`input[name="dup_choice_${grp.group_id}"][value="${grp.recommended_index}"]`);
        if (recInput) recInput.checked = true;
      });
      showToast("⭐ Se restableció la selección a los ganadores recomendados", "info");
    }

    async function applyDuplicateResolutions() {
      if (!currentDuplicateGroups || currentDuplicateGroups.length === 0) {
        closeDuplicateResolverModal();
        return;
      }

      const resolutions = [];
      currentDuplicateGroups.forEach(grp => {
        const selectedRadio = document.querySelector(`input[name="dup_choice_${grp.group_id}"]:checked`);
        if (selectedRadio) {
          const val = selectedRadio.value;
          if (val === "keep_all") return;
          const selIdx = parseInt(val, 10);
          resolutions.push({
            group_id: grp.group_id,
            selected_index: selIdx,
            candidates: grp.candidates
          });
        }
      });

      if (resolutions.length === 0) {
        showToast("No se seleccionó ningún cambio de duplicados", "info");
        closeDuplicateResolverModal();
        return;
      }

      const btn = document.getElementById('btnApplyDuplicates');
      if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i data-lucide="loader-2" class="w-4 h-4 animate-spin inline"></i><span>Aplicando resolución...</span>`;
      }

      try {
        const resp = await fetch('/api/toponymy/resolve-duplicates', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            places: cityData.places,
            resolutions: resolutions
          })
        });
        const result = await resp.json();
        if (result.status === "ok") {
          placesHistorySnapshot = JSON.parse(JSON.stringify(cityData.places));
          updateUndoButtonUI();
          cityData.places = result.places;
          selectedPlaceIndices.clear();
          renderPlacesList();
          renderPlacesMarkersOnMap();
          triggerAutoSave();
          closeDuplicateResolverModal();
          showToast(`✨ Se resolvieron los duplicados: ${result.total_pruned} elementos redundantes eliminados`, "success");
        } else {
          showToast(`Error: ${result.message || 'No se pudo resolver duplicados'}`, "error");
        }
      } catch (err) {
        showToast(`Fallo de conexión: ${err.message}`, "error");
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = `<i data-lucide="trash-2" class="w-4 h-4 inline"></i><span>Aplicar y Eliminar Redundantes</span>`;
        }
      }
    }

    // Demanda y Box Selection
    let densityLoadedCityFile = null;
    let densityRequestId = 0;

    async function loadDemandDensityForPoi(force = false) {
      const requestId = ++densityRequestId;
      const requestedFile = currentCityFile;
      let requestedState;
      try {
        if (conapoCommitPromise) await conapoCommitPromise;
        if (requestId !== densityRequestId || requestedFile !== currentCityFile) return;
        if (await saveCurrentCity(true) !== true) throw new Error('Guarda la configuración antes de consultar la demanda.');
        if (requestId !== densityRequestId || requestedFile !== currentCityFile) return;
        requestedState = JSON.stringify(cityData);
        densityLoadedCityFile = currentCityFile;
        const res = await fetch(`/api/density?file=${encodeURIComponent(currentCityFile)}`);
        const data = await res.json();
        if (requestId !== densityRequestId || requestedFile !== currentCityFile || requestedState !== JSON.stringify(cityData)) return;
        if (!res.ok) throw new Error(data.error || data.message || 'No se pudo cargar la referencia de demanda');
        if (cityData.macroeconomics?.residential_employment === 'census_employed' && data.diagnostics?.employment_mode !== 'census_employed') throw new Error('La referencia de demanda no coincide con el método de ocupados seleccionado.');
        if (cityData.macroeconomics?.demographic_reference?.mode === 'eic2025' && data.diagnostics?.residential_employment?.demographic_reference?.mode !== 'eic2025') throw new Error('La vista previa no corresponde a la referencia EIC 2025 seleccionada.');
        if (['auto','ce_bounded','historical_transfer'].includes(cityData.macroeconomics?.workplace_employment) && data.diagnostics?.workplace_mode !== cityData.macroeconomics.workplace_employment) throw new Error('La referencia de empleo no coincide con el método seleccionado.');
        const workplace = data.diagnostics?.workplace_employment;
        const workplaceLabel = document.getElementById('workplaceCoverage');
        updateWorkplaceSourceStatus(workplace);
        const coverage = data.diagnostics?.residential_placement;
        const coverageLabel = document.getElementById('residentialCoverage');
        if (coverageLabel && coverage) {
          const counts = Object.entries(coverage.sources_retained || {}).map(([source, values]) => `${source}: ${values.blocks} manzanas`).join('; ');
          coverageLabel.textContent = `${counts}. Sin ubicación: ${coverage.unlocated?.blocks || 0}. Antes del ajuste vial.`;
        }
        const employment = data.diagnostics?.residential_employment;
        const employmentLabel = document.getElementById('employmentCoverage');
        if (employmentLabel) {
          const retained = employment?.placement?.retained;
          const estimated = Object.entries(retained?.by_source || {}).filter(([source]) => source !== 'published_block').reduce((sum, [, values]) => sum + values.blocks, 0);
          employmentLabel.textContent = employment ? `${retained?.blocks || 0} manzanas: ${estimated} con ocupados estimados. Ocupados proyectados tras ubicación y BBOX, antes de filtros de demanda: ${Math.round(retained?.projected_employed || 0).toLocaleString('es-MX')}.` : '';
          if (employment?.demographic_reference?.mode === 'eic2025') {
            employmentLabel.textContent = `Referencia EIC 2025. Ocupados residentes: ${Math.round(retained?.projected_employed || 0).toLocaleString('es-MX')}. Viajeros laborales: ${Math.round(retained?.projected_commuters || 0).toLocaleString('es-MX')}. Antes de filtros de demanda; distribución espacial CPV 2020.`;
          }
        }
        const rawPoints = data.points || [];
        const declaredPoiIds = new Set((cityData?.pois || []).map(p => p.id));
        densityPoints = rawPoints.filter(p => !p.is_special && !declaredPoiIds.has(p.id));
        updatePoiPreviewFromForm();
        renderPoiMarkersOnMap();
        renderPoiList();
      } catch (e) {
        if (requestId !== densityRequestId || requestedFile !== currentCityFile) return;
        console.warn("No se pudo precargar densidad para POI Studio:", e);
        densityPoints = [];
        const coverageLabel = document.getElementById('residentialCoverage');
        if (coverageLabel) coverageLabel.textContent = e.message;
        const employmentLabel = document.getElementById('employmentCoverage');
        if (employmentLabel) employmentLabel.textContent = '';
        const workplaceLabel = document.getElementById('workplaceCoverage');
        updateWorkplaceSourceStatus();
        if (workplaceLabel) workplaceLabel.textContent += ` Vista sin validar: ${e.message}`;
        showToast(e.message, 'error');
      }
    }

    function toggleDensityLayer(type) {
      if (!mapPoi) return;
      loadDemandDensityForPoi().then(() => {
        if (type === 'jobs') {
          if (densityLayers.jobs) {
            mapPoi.removeLayer(densityLayers.jobs);
            densityLayers.jobs = null;
            document.getElementById('btnToggleDensityJobs')?.classList.remove('bg-rose-500/30', 'border-rose-500');
          } else {
            densityLayers.jobs = L.layerGroup();
            const poiDensityCanvas = L.canvas({ padding: 0.5 });
            densityPoints.filter(p => p.jobs > 0).forEach(p => {
              const r = Math.min(18, Math.max(3.5, Math.sqrt(p.jobs) * 0.45));
              const circle = L.circleMarker([p.location[1], p.location[0]], {
                renderer: poiDensityCanvas,
                radius: r,
                color: '#e11d48',
                weight: 1.5,
                fillColor: '#f43f5e',
                fillOpacity: 0.45
              });

              circle.bindTooltip(`
                <div class="p-2 text-xs font-sans">
                  <div class="font-bold flex items-center space-x-1.5 text-rose-700">
                    <span class="w-2.5 h-2.5 rounded-full bg-rose-500 inline-block shrink-0"></span>
                    <span>Nodo de Empleo (DENUE)</span>
                  </div>
                  <div class="mt-1 text-stone-900 font-mono font-bold text-sm">
                    ${(p.jobs || 0).toLocaleString()} <span class="text-xs font-normal text-stone-600">empleos estimados</span>
                  </div>
                  <div class="text-[10px] text-stone-500 mt-0.5">
                    Establecimientos comerciales y económicos
                  </div>
                  <div class="mt-1.5 text-[10px] text-metro-orange font-semibold flex items-center space-x-1 border-t border-stone-200/80 pt-1">
                    <span>📍 Clic para colocar POI aquí</span>
                  </div>
                </div>
              `, {
                direction: 'top',
                sticky: true,
                className: 'poi-custom-tooltip'
              });

              circle.on('click', (e) => {
                L.DomEvent.stopPropagation(e);
                if (isBoxSelectActive) return;
                const editorTab = document.getElementById('poiTab-editor');
                const isEditorOpen = editorTab && !editorTab.classList.contains('hidden');
                if (isEditorOpen) {
                  document.getElementById('editPoiLon').value = p.location[0].toFixed(5);
                  document.getElementById('editPoiLat').value = p.location[1].toFixed(5);
                  updatePoiPreviewFromForm();
                } else {
                  openPoiEditorForNew({ lng: p.location[0], lat: p.location[1] });
                }
              });

              circle.addTo(densityLayers.jobs);
            });
            densityLayers.jobs.addTo(mapPoi);
            document.getElementById('btnToggleDensityJobs')?.classList.add('bg-rose-500/30', 'border-rose-500');
          }
        } else if (type === 'pop') {
          if (densityLayers.pop) {
            mapPoi.removeLayer(densityLayers.pop);
            densityLayers.pop = null;
            document.getElementById('btnToggleDensityPop')?.classList.remove('bg-blue-500/30', 'border-blue-500');
          } else {
            densityLayers.pop = L.layerGroup();
            densityPoints.filter(p => p.residents > 0).forEach(p => {
              const r = Math.min(18, Math.max(3.5, Math.sqrt(p.residents) * 0.45));
              const circle = L.circleMarker([p.location[1], p.location[0]], {
                radius: r,
                color: '#0284c7',
                weight: 1.5,
                fillColor: '#38bdf8',
                fillOpacity: 0.45
              });

              circle.bindTooltip(`
                <div class="p-2 text-xs font-sans">
                  <div class="font-bold flex items-center space-x-1.5 text-blue-700">
                    <span class="w-2.5 h-2.5 rounded-full bg-blue-500 inline-block shrink-0"></span>
                    <span>Nodo Residencial (Censo CPV)</span>
                  </div>
                  <div class="mt-1 text-stone-900 font-mono font-bold text-sm">
                    ${(p.residents || 0).toLocaleString()} <span class="text-xs font-normal text-stone-600">habitantes / PEA</span>
                  </div>
                  <div class="text-[10px] text-stone-500 mt-0.5">
                    Población censal por manzana urbana
                  </div>
                  <div class="mt-1.5 text-[10px] text-metro-orange font-semibold flex items-center space-x-1 border-t border-stone-200/80 pt-1">
                    <span>📍 Clic para colocar POI aquí</span>
                  </div>
                </div>
              `, {
                direction: 'top',
                sticky: true,
                className: 'poi-custom-tooltip'
              });

              circle.on('click', (e) => {
                L.DomEvent.stopPropagation(e);
                if (isBoxSelectActive) return;
                const editorTab = document.getElementById('poiTab-editor');
                const isEditorOpen = editorTab && !editorTab.classList.contains('hidden');
                if (isEditorOpen) {
                  document.getElementById('editPoiLon').value = p.location[0].toFixed(5);
                  document.getElementById('editPoiLat').value = p.location[1].toFixed(5);
                  updatePoiPreviewFromForm();
                } else {
                  openPoiEditorForNew({ lng: p.location[0], lat: p.location[1] });
                }
              });

              circle.addTo(densityLayers.pop);
            });
            densityLayers.pop.addTo(mapPoi);
            document.getElementById('btnToggleDensityPop')?.classList.add('bg-blue-500/30', 'border-blue-500');
          }
        }
      });
    }

    function toggleBoxSelectMode() {
      isBoxSelectActive = !isBoxSelectActive;
      const btn = document.getElementById('btnToggleBoxSelect');
      if (btn) {
        btn.classList.toggle('bg-amber-600/30', isBoxSelectActive);
        btn.classList.toggle('border-amber-600', isBoxSelectActive);
      }

      if (isBoxSelectActive) {
        mapPoi.dragging.disable();
        mapPoi.getContainer().style.cursor = 'crosshair';
        showToast("Arrastra sobre el mapa para medir empleos y población en el área", "info");
      } else {
        mapPoi.dragging.enable();
        mapPoi.getContainer().style.cursor = '';
      }
    }

    let isPolyMeasureActive = false;
    let polyMeasurePoints = [];
    let polyMeasurePreviewLayer = null;
    let polySelectionLayer = null;
    let measureHighlightGroup = null;

    function getDistMetersWizard(lat1, lon1, lat2, lon2) {
      const dLat = (lat2 - lat1) * 111139.0;
      const dLon = (lon2 - lon1) * 111139.0 * Math.cos(((lat1 + lat2) / 2.0) * Math.PI / 180.0);
      return Math.sqrt(dLat * dLat + dLon * dLon);
    }

    function isPointInZonePolygon(point, vs) {
      const x = point[0], y = point[1];
      let inside = false;
      for (let i = 0, j = vs.length - 1; i < vs.length; j = i++) {
        const xi = vs[i][0], yi = vs[i][1];
        const xj = vs[j][0], yj = vs[j][1];
        const intersect = ((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi);
        if (intersect) inside = !inside;
      }
      return inside;
    }

    function getPolygonAreaKm2Wizard(coords) {
      if (!coords || coords.length < 3) return 0;
      let avgLat = 0;
      for (const c of coords) avgLat += c[1];
      avgLat /= coords.length;
      const cosLat = Math.cos(avgLat * Math.PI / 180);

      let area = 0;
      for (let i = 0, j = coords.length - 1; i < coords.length; j = i++) {
        const xi = coords[i][0] * 111.32 * cosLat;
        const yi = coords[i][1] * 110.57;
        const xj = coords[j][0] * 111.32 * cosLat;
        const yj = coords[j][1] * 110.57;
        area += (xj * yi) - (xi * yj);
      }
      return Math.max(0.01, Math.abs(area / 2.0));
    }

    function toggleBoxSelectMode() {
      if (isPolyMeasureActive) cancelPolyMeasureMode();
      isBoxSelectActive = !isBoxSelectActive;
      const btn = document.getElementById('btnToggleBoxSelect');
      if (btn) {
        btn.classList.toggle('bg-amber-600/30', isBoxSelectActive);
        btn.classList.toggle('border-amber-600', isBoxSelectActive);
      }

      if (isBoxSelectActive) {
        mapPoi.dragging.disable();
        mapPoi.getContainer().style.cursor = 'crosshair';
        showToast("Arrastra sobre el mapa para medir empleos y población en el área", "info");
      } else {
        mapPoi.dragging.enable();
        mapPoi.getContainer().style.cursor = '';
      }
    }

    function togglePolyMeasureMode() {
      if (isBoxSelectActive) toggleBoxSelectMode();
      if (isPolyMeasureActive) {
        cancelPolyMeasureMode();
      } else {
        startPolyMeasureMode();
      }
    }

    function startPolyMeasureMode() {
      isPolyMeasureActive = true;
      polyMeasurePoints = [];
      const btn = document.getElementById('btnTogglePolyMeasure');
      if (btn) {
        btn.classList.add('bg-amber-600/30', 'border-amber-600');
      }
      document.getElementById('polyMeasureBanner')?.classList.remove('hidden');
      updatePolyMeasureButton();
      mapPoi.getContainer().style.cursor = 'crosshair';
      showToast("Modo Lazo / Polígono activo: haz clics para marcar los vértices de la zona", "info");
    }

    function cancelPolyMeasureMode() {
      isPolyMeasureActive = false;
      polyMeasurePoints = [];
      if (polyMeasurePreviewLayer && mapPoi) {
        mapPoi.removeLayer(polyMeasurePreviewLayer);
        polyMeasurePreviewLayer = null;
      }
      const btn = document.getElementById('btnTogglePolyMeasure');
      if (btn) {
        btn.classList.remove('bg-amber-600/30', 'border-amber-600');
      }
      document.getElementById('polyMeasureBanner')?.classList.add('hidden');
      mapPoi.getContainer().style.cursor = '';
    }

    function updatePolyMeasureButton() {
      const btn = document.getElementById('btnFinishPolyMeasure');
      if (btn) {
        btn.innerText = `Finalizar (${polyMeasurePoints.length} pts)`;
      }
    }

    function finishPolyMeasure() {
      if (polyMeasurePoints.length < 3) {
        showToast("Debes marcar al menos 3 puntos para delimitar un polígono", "warning");
        return;
      }

      if (polySelectionLayer && mapPoi) {
        mapPoi.removeLayer(polySelectionLayer);
        polySelectionLayer = null;
      }
      if (boxSelectionLayer && mapPoi) {
        mapPoi.removeLayer(boxSelectionLayer);
        boxSelectionLayer = null;
      }

      polySelectionLayer = L.polygon(polyMeasurePoints, {
        color: '#d97706',
        weight: 2,
        dashArray: '4, 4',
        fillColor: '#f59e0b',
        fillOpacity: 0.18
      }).addTo(mapPoi);

      const coords = polyMeasurePoints.map(p => [parseFloat(p.lng.toFixed(5)), parseFloat(p.lat.toFixed(5))]);
      cancelPolyMeasureMode();

      runAreaSimulationInWizard('poly', coords);
    }

    function setupBoxSelectionForPoi() {
      if (!mapPoi) return;

      mapPoi.on('mousedown', (e) => {
        const isPlacesTabOpen = document.getElementById('poiTab-places') && !document.getElementById('poiTab-places').classList.contains('hidden');
        if (isBoxSelectActive || isPlacesBoxSelectActive || (isPlacesTabOpen && e.originalEvent.shiftKey) || e.originalEvent.shiftKey) {
          isMouseDownForBox = true;
          boxStartLatLng = e.latlng;
          mapPoi.dragging.disable();
          if (boxSelectionLayer) {
            mapPoi.removeLayer(boxSelectionLayer);
            boxSelectionLayer = null;
          }
          if (polySelectionLayer) {
            mapPoi.removeLayer(polySelectionLayer);
            polySelectionLayer = null;
          }
        }
      });

      mapPoi.on('mousemove', (e) => {
        if (isMouseDownForBox && boxStartLatLng) {
          const bounds = L.latLngBounds(boxStartLatLng, e.latlng);
          const isPlacesTabOpen = document.getElementById('poiTab-places') && !document.getElementById('poiTab-places').classList.contains('hidden');
          const isPlacesMode = isPlacesBoxSelectActive || (isPlacesTabOpen && e.originalEvent.shiftKey);

          if (!boxSelectionLayer) {
            boxSelectionLayer = L.rectangle(bounds, {
              color: isPlacesMode ? '#6366f1' : '#d97706',
              weight: 2,
              dashArray: '4, 4',
              fillColor: isPlacesMode ? '#818cf8' : '#f59e0b',
              fillOpacity: isPlacesMode ? 0.25 : 0.2
            }).addTo(mapPoi);
          } else {
            boxSelectionLayer.setBounds(bounds);
          }
          if (!isPlacesMode) {
            runAreaSimulationInWizard('box', bounds);
          }
        }
      });

      mapPoi.on('mouseup', (e) => {
        if (isMouseDownForBox) {
          isMouseDownForBox = false;
          const isPlacesTabOpen = document.getElementById('poiTab-places') && !document.getElementById('poiTab-places').classList.contains('hidden');
          const isPlacesMode = isPlacesBoxSelectActive || (isPlacesTabOpen && e.originalEvent.shiftKey);

          if (!isBoxSelectActive && !isPlacesBoxSelectActive && !e.originalEvent.shiftKey) {
            mapPoi.dragging.enable();
          }

          if (isPlacesMode && boxStartLatLng) {
            const bounds = L.latLngBounds(boxStartLatLng, e.latlng);
            let newlySelected = 0;
            const places = cityData.places || [];

            places.forEach((pl, idx) => {
              if (pl.loc && pl.loc.length >= 2) {
                const latLng = L.latLng(pl.loc[1], pl.loc[0]);
                if (bounds.contains(latLng)) {
                  selectedPlaceIndices.add(idx);
                  newlySelected++;
                }
              }
            });

            renderPlacesList();
            renderPlacesMarkersOnMap();

            if (newlySelected > 0) {
              showToast(`Se seleccionaron ${newlySelected} colonias dentro del cuadro`, "info");
            } else {
              showToast("No se encontraron colonias dentro del cuadro", "info");
            }

            setTimeout(() => {
              if (boxSelectionLayer && mapPoi) {
                mapPoi.removeLayer(boxSelectionLayer);
                boxSelectionLayer = null;
              }
            }, 400);

            if (isPlacesBoxSelectActive) {
              toggleBoxSelectPlacesMode();
            }
          }
        }
      });

      mapPoi.on('click', (e) => {
        if (isPolyMeasureActive) {
          polyMeasurePoints.push(e.latlng);
          updatePolyMeasureButton();
          if (!polyMeasurePreviewLayer) {
            polyMeasurePreviewLayer = L.polyline(polyMeasurePoints, {
              color: '#d97706',
              weight: 2,
              dashArray: '3, 3'
            }).addTo(mapPoi);
          } else {
            polyMeasurePreviewLayer.setLatLngs(polyMeasurePoints);
          }
          return;
        }

        if (isBoxSelectActive || isDrawingAffluence || isDrawingExclusion) return;

        isPickingLocation = false;
        mapPoi.getContainer().style.cursor = '';

        const editorTab = document.getElementById('poiTab-editor');
        const isEditorOpen = editorTab && !editorTab.classList.contains('hidden');

        if (isEditorOpen) {
          document.getElementById('editPoiLon').value = e.latlng.lng.toFixed(5);
          document.getElementById('editPoiLat').value = e.latlng.lat.toFixed(5);
          updatePoiPreviewFromForm();
        } else {
          openPoiEditorForNew(e.latlng);
        }
      });

      mapPoi.on('dblclick', (e) => {
        if (isPolyMeasureActive && polyMeasurePoints.length >= 3) {
          L.DomEvent.stopPropagation(e);
          finishPolyMeasure();
        }
      });
    }

    function runAreaSimulationInWizard(type, selectionObj) {
      if (!mapPoi || !densityPoints || densityPoints.length === 0) return;

      if (!measureHighlightGroup) {
        measureHighlightGroup = L.layerGroup().addTo(mapPoi);
      }
      measureHighlightGroup.clearLayers();

      const isInside = (lon, lat) => {
        if (type === 'box') {
          return selectionObj.contains([lat, lon]);
        } else if (type === 'poly') {
          return isPointInZonePolygon([lon, lat], selectionObj);
        }
        return false;
      };

      // 1. Preparar POIs con radios y estados dentro/fuera
      const poisList = (cityData && Array.isArray(cityData.pois)) ? cityData.pois : [];
      const resolvedPois = poisList.map(p => {
        const loc = p.loc || p.location || [0, 0];
        const lon = parseFloat(loc[0]);
        const lat = parseFloat(loc[1]);
        return {
          id: p.id || 'POI',
          lon: lon,
          lat: lat,
          radius_m: parseFloat(p.radius_m || 1000),
          mode: (p.mode || 'MAX').toUpperCase(),
          declaredJobs: parseInt(p.jobs || 0, 10),
          absorbedJobs: 0,
          finalJobs: 0,
          isInsideSelection: isInside(lon, lat)
        };
      });

      // Zonas de Afluencia
      const zones = (cityData && Array.isArray(cityData.affluence_zones))
        ? cityData.affluence_zones.filter(z => z.enabled !== false && z.coordinates && z.coordinates.length >= 3)
        : [];

      // 2. Resolver absorción y afluencia para cada nodo
      densityPoints.forEach(pt => {
        const ptLon = pt.location[0];
        const ptLat = pt.location[1];

        // Absorción estricta al POI más cercano (argmin)
        let matchedPoi = null;
        let minDist = Infinity;
        for (const poi of resolvedPois) {
          const d = getDistMetersWizard(ptLat, ptLon, poi.lat, poi.lon);
          if (d <= poi.radius_m && d < minDist) {
            minDist = d;
            matchedPoi = poi;
          }
        }

        pt._absorbedBy = matchedPoi;
        if (matchedPoi) {
          matchedPoi.absorbedJobs += (pt.jobs || 0);
        } else {
          let maxMult = 1.0;
          let matchedZone = null;
          for (const zone of zones) {
            if (isPointInZonePolygon([ptLon, ptLat], zone.coordinates)) {
              const mult = parseFloat(zone.multiplier || 1.0);
              if (mult > maxMult) {
                maxMult = mult;
                matchedZone = zone;
              }
            }
          }
          pt._affluenceMult = maxMult;
          pt._matchedZone = matchedZone;
          pt._finalJobs = Math.round((pt.jobs || 0) * maxMult);
        }
      });

      // Resolver POIs finales
      resolvedPois.forEach(poi => {
        const manual = poi.declaredJobs;
        const absorbed = Math.round(poi.absorbedJobs);
        if (poi.mode === "MAX") {
          poi.finalJobs = Math.max(manual, absorbed);
        } else if (poi.mode === "BOOST" || poi.mode === "ADDITIVE") {
          poi.finalJobs = manual + absorbed;
        } else if (poi.mode === "REPLACE") {
          poi.finalJobs = manual;
        } else {
          poi.finalJobs = Math.max(manual, absorbed);
        }
      });

      // 3. Acumular métricas dentro del área seleccionada
      let origCalibratedJobs = 0;
      let origRawJobs = 0;
      let origPop = 0;
      let organicJobsInside = 0;
      let affluenceBoostJobs = 0;
      let leakedJobs = 0;
      let nodesCount = 0;
      const activeZonesInside = new Map();

      densityPoints.forEach(pt => {
        const ptLon = pt.location[0];
        const ptLat = pt.location[1];
        if (!isInside(ptLon, ptLat)) return;

        nodesCount++;
        const j = (pt.jobs || 0);
        const rawJ = (pt.raw_jobs !== undefined ? pt.raw_jobs : j);
        const r = (pt.residents || 0);
        origCalibratedJobs += j;
        origRawJobs += rawJ;
        origPop += r;

        if (pt._absorbedBy) {
          if (!pt._absorbedBy.isInsideSelection) {
            leakedJobs += j;
          }
          // Nodo absorbido: gris suave con contorno discontinuo
          const cm = L.circleMarker([ptLat, ptLon], {
            radius: 4,
            color: '#64748b',
            weight: 1,
            dashArray: '2, 2',
            fillColor: '#475569',
            fillOpacity: 0.6
          });
          cm.bindTooltip(`<b>${pt.id}</b><br>⚪ <i>Absorbido por:</i> <strong>${pt._absorbedBy.id}</strong><br>Base: ${j.toLocaleString()} empleos`, { direction: 'top' });
          measureHighlightGroup.addLayer(cm);
        } else {
          organicJobsInside += j;
          const finalJ = pt._finalJobs || j;
          if (finalJ > j) {
            affluenceBoostJobs += (finalJ - j);
          }
          if (pt._matchedZone) {
            activeZonesInside.set(pt._matchedZone.id, {
              name: pt._matchedZone.name || pt._matchedZone.id,
              mult: pt._affluenceMult
            });
          }

          const isBoosted = (pt._affluenceMult || 1.0) > 1.0;
          const cm = L.circleMarker([ptLat, ptLon], {
            radius: isBoosted ? 6 : 5,
            color: '#fff',
            weight: isBoosted ? 1.5 : 1,
            fillColor: isBoosted ? '#10b981' : '#ef4444',
            fillOpacity: isBoosted ? 0.85 : 0.75
          });
          const desc = isBoosted
            ? `🟢 <i>Impulsado por:</i> <strong>${pt._matchedZone?.name || pt._matchedZone?.id}</strong> (${pt._affluenceMult}x)<br>Final: ${finalJ.toLocaleString()} (Base: ${j.toLocaleString()})`
            : `🔴 <i>Orgánico Directo:</i> ${j.toLocaleString()} empleos`;
          cm.bindTooltip(`<b>${pt.id}</b><br>${desc}`, { direction: 'top' });
          measureHighlightGroup.addLayer(cm);
        }
      });

      // POIs con centro adentro de la selección
      const poisInside = resolvedPois.filter(p => p.isInsideSelection);
      let totalPoiJobsInside = 0;
      poisInside.forEach(p => { totalPoiJobsInside += p.finalJobs; });

      const totalGameJobs = (organicJobsInside + affluenceBoostJobs) + totalPoiJobsInside;
      const delta = totalGameJobs - origCalibratedJobs;
      const deltaPct = origCalibratedJobs > 0 ? ((delta / origCalibratedJobs) * 100) : 0;

      // Calcular Superficie km²
      let areaKm2 = 0;
      let centerLatLng = { lat: 0, lng: 0 };
      if (type === 'box') {
        const sw = selectionObj.getSouthWest();
        const ne = selectionObj.getNorthEast();
        const widthKm = (ne.lng - sw.lng) * 111.32 * Math.cos((sw.lat + ne.lat) / 2 * Math.PI / 180);
        const heightKm = (ne.lat - sw.lat) * 110.57;
        areaKm2 = Math.max(0.01, Math.abs(widthKm * heightKm));
        centerLatLng = selectionObj.getCenter();
      } else if (type === 'poly') {
        areaKm2 = getPolygonAreaKm2Wizard(selectionObj);
        let sLon = 0, sLat = 0;
        selectionObj.forEach(c => { sLon += c[0]; sLat += c[1]; });
        centerLatLng = { lat: sLat / selectionObj.length, lng: sLon / selectionObj.length };
      }

      currentAreaStats = {
        type: type,
        center: centerLatLng,
        areaKm2: areaKm2,
        jobs: origCalibratedJobs,
        rawJobs: origRawJobs,
        gameJobs: totalGameJobs,
        pop: origPop,
        nodes: nodesCount
      };

      // Actualizar DOM del HUD
      document.getElementById('measJobs').innerText = origCalibratedJobs.toLocaleString();
      const rawEl = document.getElementById('measRawJobs');
      if (rawEl) rawEl.innerText = origRawJobs.toLocaleString();
      const gameEl = document.getElementById('measGameJobs');
      if (gameEl) gameEl.innerText = totalGameJobs.toLocaleString();
      const deltaEl = document.getElementById('measDeltaBadge');
      if (deltaEl) {
        const sign = delta > 0 ? '+' : '';
        deltaEl.innerText = `${sign}${delta.toLocaleString()} (${sign}${deltaPct.toFixed(1)}%)`;
        deltaEl.className = 'font-bold ' + (delta > 0 ? 'text-emerald-700' : (delta < 0 ? 'text-rose-600' : 'text-stone-500'));
      }

      document.getElementById('measOrganicJobs').innerText = organicJobsInside.toLocaleString();
      document.getElementById('measAffluenceBoost').innerText = `+${affluenceBoostJobs.toLocaleString()}`;
      document.getElementById('measLeakedJobs').innerText = `-${leakedJobs.toLocaleString()}`;
      document.getElementById('measPoisInside').innerText = `${poisInside.length} (${totalPoiJobsInside.toLocaleString()} empl.)`;

      const zonesContainer = document.getElementById('measZonesContainer');
      const zonesList = document.getElementById('measZonesList');
      if (zonesContainer && zonesList) {
        if (activeZonesInside.size > 0) {
          zonesContainer.classList.remove('hidden');
          zonesList.innerHTML = Array.from(activeZonesInside.values()).map(z => {
            return `<span class="px-1.5 py-0.5 bg-amber-200/90 border border-amber-400/50 rounded font-semibold text-amber-900">${z.name} (${z.mult}x)</span>`;
          }).join('');
        } else {
          zonesContainer.classList.add('hidden');
          zonesList.innerHTML = '';
        }
      }

      document.getElementById('measPop').innerText = origPop.toLocaleString();
      document.getElementById('measNodes').innerText = nodesCount.toLocaleString();
      document.getElementById('measArea').innerText = `${areaKm2.toFixed(2)} km²`;
      document.getElementById('areaMeasureHud').classList.remove('hidden');
      if (window.lucide) lucide.createIcons();
    }

    function calculateBoxStats(bounds) {
      runAreaSimulationInWizard('box', bounds);
    }

    function clearAreaSelection() {
      if (boxSelectionLayer && mapPoi) {
        mapPoi.removeLayer(boxSelectionLayer);
        boxSelectionLayer = null;
      }
      if (polySelectionLayer && mapPoi) {
        mapPoi.removeLayer(polySelectionLayer);
        polySelectionLayer = null;
      }
      if (measureHighlightGroup) {
        measureHighlightGroup.clearLayers();
      }
      document.getElementById('areaMeasureHud').classList.add('hidden');
      if (isBoxSelectActive) toggleBoxSelectMode();
      if (isPolyMeasureActive) cancelPolyMeasureMode();
    }

    function createPoiFromBoxSelection() {
      if (!currentAreaStats) return;
      const center = currentAreaStats.center;
      const suggestedRadius = Math.min(3000, Math.max(500, Math.round(Math.sqrt((currentAreaStats.areaKm2 * 1000000) / Math.PI))));
      const suggestedJobs = Math.max(3000, currentAreaStats.jobs);

      openPoiEditorForNew();
      document.getElementById('editPoiCategory').value = "CUSTOM";
      document.getElementById('editPoiBaseName').value = "Cluster Comercial";
      document.getElementById('editPoiId').value = "Cluster Comercial";
      document.getElementById('editPoiNameEs').value = "Cluster Comercial";
      document.getElementById('editPoiLon').value = center.lng.toFixed(5);
      document.getElementById('editPoiLat').value = center.lat.toFixed(5);
      setPoiRadiusValue(suggestedRadius);
      document.getElementById('editPoiJobs').value = suggestedJobs;
      onPoiTaxonomyChanged('CUSTOM');

      clearAreaSelection();
      updatePoiPreviewFromForm();
      showToast("POI inicializado con la masa económica del área seleccionada", "success");
    }

    // -------------------------------------------------------------------------
    // PASO 5: COMPILACIÓN Y TELEMETRÍA EN VIVO (SSE)
    // -------------------------------------------------------------------------
    function initSSE() {
      if (sseSource) sseSource.close();
      sseSource = new EventSource('/api/build/stream');

      sseSource.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.file && msg.file !== currentCityFile) return;
          appendTerminalLog(msg);

          if (msg.progress !== undefined) {
            document.getElementById('buildProgressBar').style.width = msg.progress + "%";
            document.getElementById('buildPercentLabel').innerText = msg.progress + "%";
          }
          if (msg.step_name) {
            document.getElementById('buildStepLabel').innerText = `Fase: ${msg.step_name}`;
          }

          const btn = document.getElementById('btnStartBuild');
          const btnText = document.getElementById('textStartBuild');
          const btnIcon = document.getElementById('iconStartBuild');
          const btnResults = document.getElementById('btnGoToResults');
          const statusBadge = document.getElementById('buildStatusBadge');
          const statusText = document.getElementById('buildStatusText');

          // 1. Detección de Error
          if (msg.step_name === "Error en Compilación" || (msg.line && (msg.line.includes('❌') || msg.line.includes('ERROR CRÍTICO')))) {
            if (btn) {
              btn.disabled = false;
              btn.classList.remove('opacity-50', 'cursor-not-allowed', 'bg-metro-orange');
              btn.classList.add('bg-rose-600', 'hover:bg-rose-700');
            }
            if (btnText) btnText.innerText = "Reintentar Compilación";
            if (btnIcon) btnIcon.setAttribute('data-lucide', 'rotate-cw');
            if (btnResults) btnResults.classList.add('hidden');

            document.getElementById('buildProgressBar').className = "h-full bg-rose-600 w-full transition-all duration-300";
            document.getElementById('buildStepLabel').innerHTML = `<span class="text-rose-400 font-bold">❌ Error en compilación. Revisa los detalles en rojo en la terminal.</span>`;

            if (statusBadge) statusBadge.className = "flex items-center space-x-2 px-3 py-1.5 rounded-lg text-xs font-mono bg-rose-950/60 text-rose-300 border border-rose-800";
            if (statusText) statusText.innerText = "Error en Pipeline";

            showToast("Hubo un error en la compilación. Revisa la terminal.", "error");
            lucide.createIcons();
          }

          if (msg.step_name === "Solo demanda") {
            if (btn) btn.disabled = false;
            if (btnText) btnText.innerText = "Recompilar";
            if (btnResults) btnResults.classList.remove('hidden');
            const download = document.getElementById('btnDownloadZipStep5');
            if (download) download.classList.add('hidden');
            if (statusText) statusText.innerText = "Demanda lista; falta cartografía para ZIP";
            showToast("Demanda generada. Completa la cartografía para descargar el ZIP.", "info");
          }

          // 2. Detección de Éxito / Finalizado
          if (msg.step_name === "Finalizado" || (msg.progress === 100 && msg.line && msg.line.includes('✨'))) {
            if (btn) {
              btn.disabled = false;
              btn.classList.remove('opacity-50', 'cursor-not-allowed', 'bg-rose-600');
              btn.classList.add('bg-metro-orange', 'hover:bg-orange-600');
            }
            if (btnText) btnText.innerText = "Recompilar";
            if (btnIcon) btnIcon.setAttribute('data-lucide', 'play');
            if (btnResults) btnResults.classList.remove('hidden');

            const btnDl = document.getElementById('btnDownloadZipStep5');
            if (btnDl) {
              btnDl.href = `/api/download?file=${encodeURIComponent(currentCityFile)}`;
              btnDl.classList.remove('hidden');
            }

            document.getElementById('buildProgressBar').className = "h-full bg-emerald-500 w-full transition-all duration-300";
            document.getElementById('buildStepLabel').innerHTML = `<span class="text-emerald-400 font-bold">✨ ¡Compilación y Empaquetado Completados!</span>`;

            if (statusBadge) statusBadge.className = "flex items-center space-x-2 px-3 py-1.5 rounded-lg text-xs font-mono bg-emerald-100 text-emerald-900 border border-emerald-300";
            if (statusText) statusText.innerText = "Listo";

            showToast("¡Compilación finalizada! Ya puedes descargar tu archivo .zip.", "success");
            lucide.createIcons();
          }
        } catch (err) {
          console.error("Error al procesar mensaje SSE:", err);
        }
      };
    }

    let systemHealthTimer = null;
    let systemHealthPolls = 0;
    async function checkSystemHealth() {
      if (systemHealthTimer) { clearTimeout(systemHealthTimer); systemHealthTimer = null; }
      try {
        const data = await fetchStartupJson('/api/system-check', 'Estado del sistema', 15000);
        wizardLifecycle.clearWarning('system');
        const badge = document.getElementById('wslStatusBadge');
        if (!badge) return;

        if (data.status === 'checking') {
          badge.textContent = 'Comprobando herramientas WSL…';
          if (++systemHealthPolls <= 15) systemHealthTimer = setTimeout(checkSystemHealth, 1000);
          else wizardLifecycle.warn('system', 'No se pudo comprobar WSL.', () => {systemHealthPolls = 0; checkSystemHealth();});
          return;
        }
        systemHealthPolls = 0;

        if (data.wsl_ready) {
          badge.className = "text-[11px] px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 flex items-center space-x-1.5";
          badge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span> <span>WSL 2 + MapGen Listo</span>`;
        } else {
          badge.className = "text-[11px] px-2.5 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/30 flex items-center space-x-1.5";
          badge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-amber-500"></span> <span>WSL No Detectado (Modo Demanda)</span>`;
        }
      } catch (e) { wizardLifecycle.warn('system', e.message, checkSystemHealth); }
    }

    async function startBuild() {
      if (!wizardLifecycle.canSave(currentCityFile)) {
        showToast('Carga un proyecto válido antes de compilar.', 'error');
        return;
      }
      const skipMap = document.getElementById('chkSkipMap').checked;
      const btn = document.getElementById('btnStartBuild');
      const btnResults = document.getElementById('btnGoToResults');
      const btnDl = document.getElementById('btnDownloadZipStep5');
      btn.disabled = true;
      btn.classList.add('opacity-50', 'cursor-not-allowed');
      if (btnResults) btnResults.classList.add('hidden');
      if (btnDl) btnDl.classList.add('hidden');

      document.getElementById('buildProgressBar').className = "h-full bg-gradient-to-r from-metro-orange via-metro-green to-metro-pink w-0 transition-all duration-300";
      document.getElementById('buildStepLabel').innerText = "Fase: Iniciando Pipeline...";
      document.getElementById('buildPercentLabel').innerText = "0%";
      document.getElementById('buildStatusText').innerText = "Compilando...";
      document.getElementById('buildStatusBadge').className = "flex items-center space-x-2 px-3 py-1.5 rounded-lg text-xs font-mono bg-metro-card text-metro-text border border-metro-border shadow-sm";

      showToast("Iniciando pipeline de compilación...", "info");

      try {
        const file = currentCityFile;
        if (await saveCurrentCity(true) === false) throw new Error('Guarda la configuración antes de compilar.');
        const state = JSON.stringify(cityData);
        if (file !== currentCityFile) throw new Error('El proyecto cambió; vuelve a compilar.');
        if (cityData.demand?.engine === 'v2') {
          const sources = await fetchStartupJson(`/api/data-status?file=${encodeURIComponent(file)}`, 'Fuentes del nuevo motor', 30000);
          if (file !== currentCityFile || state !== JSON.stringify(cityData)) throw new Error('La configuración cambió; vuelve a compilar.');
          updateDemandEngineControls(sources);
          const names = {denue:'DENUE', cpv:'Censo 2020', marco:'Marco Geoestadístico', eic:'EIC 2025'};
          const missing = Object.keys(names).filter(key => sources[key]?.status !== 'ok');
          if (missing.length) throw new Error(`Faltan fuentes del nuevo motor: ${missing.map(key => names[key]).join(', ')}. Ve a Fuentes y usa Preparar descargas.`);
        }
        const sourceReport = await refreshWorkplaceSourceStatus(true);
        if (file !== currentCityFile || state !== JSON.stringify(cityData)) throw new Error('La configuración cambió; vuelve a compilar.');
        appendTerminalLog({timestamp: new Date().toLocaleTimeString(), line: workplaceSourceStatus(sourceReport)});
        const res = await fetch('/api/build/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file, skip_map: skipMap })
        });
        const json = await res.json();
        if (!res.ok) throw new Error(json.error || "No se pudo iniciar");
      } catch (e) {
        showToast(e.message, "error");
        btn.disabled = false;
        document.getElementById('buildStatusText').innerText = 'No iniciado: ' + e.message;
        btn.classList.remove('opacity-50', 'cursor-not-allowed');
      }
    }

    function appendTerminalLog(logObj) {
      const term = document.getElementById('terminalOutput');
      const p = document.createElement('p');
      p.className = "leading-relaxed";

      const line = logObj.line || "";
      const isError = line.includes('❌') || line.includes('ERROR') || line.includes('Traceback') || line.includes('Exception') || line.includes('FileNotFoundError');
      const isSuccess = line.includes('✨') || line.includes('exitosamente') || line.includes('Completado');
      const isStep = line.includes('🚀') || line.includes('📊') || line.includes('===');

      let colorClass = "text-gray-300";
      if (isError) colorClass = "text-rose-400 font-bold bg-rose-950/40 px-1.5 py-0.5 rounded block my-0.5 border border-rose-900/50";
      else if (isSuccess) colorClass = "text-emerald-300 font-bold bg-emerald-950/40 px-1.5 py-0.5 rounded block my-0.5 border border-emerald-900/50";
      else if (isStep) colorClass = "text-metro-orange font-bold";

      p.innerHTML = `<span class="text-gray-600 font-mono text-[11px]">[${logObj.timestamp}]</span> <span class="${colorClass}">${escapeHtml(line)}</span>`;
      term.appendChild(p);
      term.scrollTop = term.scrollHeight;
    }

    function clearTerminal() {
      document.getElementById('terminalOutput').innerHTML = '';
    }

    // -------------------------------------------------------------------------
    // PASO 6: VISOR DE DEMANDA Y EMPAQUETADO
    // -------------------------------------------------------------------------
    let isDemandCompiled = false;

    function dismissDemandEmptyState() {
      const el = document.getElementById('demandEmptyState');
      if (el) el.classList.add('hidden');
    }

    async function loadDemandDataPreview() {
      try {
        const res = await fetch(`/api/demand-preview?file=${encodeURIComponent(currentCityFile)}`);
        const data = await res.json();
        const points = data.points || [];
        const isNotCompiled = !data || data.metadata?.status === 'not_compiled' || points.length === 0;
        const packageAvailable = data.metadata?.package_available === true;
        const marginNotice = document.getElementById('metricODMargins');
        if (marginNotice) {
          const exactMargins = data.metadata?.od_allocation?.mode === 'balanced_integer_v1';
          marginNotice.classList.toggle('hidden', !exactMargins);
          marginNotice.textContent = exactMargins
            ? 'Márgenes enteros del modelo conservados. Destinos estimados; residuos pequeños conservados en su par O/D.'
            : '';
        }
        isDemandCompiled = !isNotCompiled;

        const emptyStateEl = document.getElementById('demandEmptyState');
        const btnDownload = document.getElementById('btnDownloadZip');
        if (isNotCompiled) {
          if (emptyStateEl) emptyStateEl.classList.remove('hidden');
          if (btnDownload) {
            btnDownload.classList.add('opacity-50', 'cursor-not-allowed');
            btnDownload.setAttribute('title', 'Debes compilar el proyecto en el Paso 5 antes de descargar');
          }
        } else {
          if (emptyStateEl) emptyStateEl.classList.add('hidden');
          if (btnDownload) {
            btnDownload.classList.remove('opacity-50', 'cursor-not-allowed');
            btnDownload.removeAttribute('title');
          }
        }
        if (btnDownload) {
          btnDownload.disabled = !packageAvailable;
          if (!packageAvailable) {
            btnDownload.classList.add('opacity-50', 'cursor-not-allowed');
            btnDownload.setAttribute('title', 'Genera un ZIP validado con cartografía para esta configuración.');
          }
        }

        document.getElementById('metricDemandPoints').innerText = points.length.toLocaleString();

        let totalResidents = 0;
        let totalJobs = 0;

        demandLayers.residents.clearLayers();
        demandLayers.jobs.clearLayers();
        demandLayers.pois.clearLayers();

        const demandCanvas = L.canvas({ padding: 0.5 });

        points.forEach(pt => {
          const latLng = [pt.location[1], pt.location[0]];
          totalResidents += (pt.residents || 0);
          totalJobs += (pt.jobs || 0);

          if (pt.residents > 0) {
            const resCircle = L.circleMarker(latLng, {
              renderer: demandCanvas,
              radius: Math.min(8, Math.max(3, Math.sqrt(pt.residents) * 0.8)),
              color: '#0284c7',
              fillColor: '#38bdf8',
              fillOpacity: 0.6,
              weight: 1
            });
            resCircle.bindTooltip(`
              <div class="p-2 text-xs font-sans">
                <div class="font-bold flex items-center space-x-1.5 text-blue-700">
                  <span class="w-2 h-2 rounded-full bg-blue-500 inline-block shrink-0"></span>
                  <span>Demanda Residencial (Origen)</span>
                </div>
                <div class="mt-1 text-stone-900 font-mono font-bold text-sm">
                  ${(pt.residents || 0).toLocaleString()} <span class="text-xs font-normal text-stone-600">habitantes (PEA)</span>
                </div>
                <div class="text-[10px] text-stone-500 mt-0.5">Población económicamente activa proyectada</div>
              </div>
            `, { direction: 'top', sticky: true, className: 'poi-custom-tooltip' });
            resCircle.addTo(demandLayers.residents);
          }
          if (pt.jobs > 0) {
            const jobCircle = L.circleMarker(latLng, {
              renderer: demandCanvas,
              radius: Math.min(8, Math.max(3, Math.sqrt(pt.jobs) * 0.8)),
              color: '#e11d48',
              fillColor: '#f43f5e',
              fillOpacity: 0.6,
              weight: 1
            });
            jobCircle.bindTooltip(`
              <div class="p-2 text-xs font-sans">
                <div class="font-bold flex items-center space-x-1.5 text-rose-700">
                  <span class="w-2 h-2 rounded-full bg-rose-500 inline-block shrink-0"></span>
                  <span>Atracción Laboral (Destino)</span>
                </div>
                <div class="mt-1 text-stone-900 font-mono font-bold text-sm">
                  ${(pt.jobs || 0).toLocaleString()} <span class="text-xs font-normal text-stone-600">empleos</span>
                </div>
                <div class="text-[10px] text-stone-500 mt-0.5">Establecimientos y actividad económica DENUE / CE</div>
              </div>
            `, { direction: 'top', sticky: true, className: 'poi-custom-tooltip' });
            jobCircle.addTo(demandLayers.jobs);
          }
        });

        document.getElementById('metricTotalPea').innerText = totalResidents.toLocaleString();

        // Renderizar Auditoría de Distancias de Viaje (Trip Length Distribution - TLD)
        const dDist = data.distance_distribution;
        const panelTld = document.getElementById('panelDistanceDistribution');
        if (dDist && dDist.total_commuters > 0) {
          if (panelTld) panelTld.classList.remove('hidden');
          const medEl = document.getElementById('metricTldMedian');
          const meanEl = document.getElementById('metricTldMean');
          const p25El = document.getElementById('metricTldP25');
          const p75El = document.getElementById('metricTldP75');
          const p95El = document.getElementById('metricTldP95');
          const badgeEl = document.getElementById('badgeTldProfile');
          const bContainer = document.getElementById('tldBracketsContainer');

          if (medEl) medEl.innerText = `${dDist.median_km} km`;
          if (meanEl) meanEl.innerText = `${dDist.mean_km} km`;
          if (p25El) p25El.innerText = `${dDist.p25_km} km`;
          if (p75El) p75El.innerText = `${dDist.p75_km} km`;
          if (p95El) p95El.innerText = `${dDist.p95_km} km`;
          if (badgeEl) {
            badgeEl.innerText = (dDist.profile || 'intermedia').toUpperCase();
            if (dDist.profile === 'compacta') {
              badgeEl.className = "text-[9px] px-1.5 py-0.5 bg-blue-900/60 border border-blue-500/40 text-blue-200 rounded font-semibold";
            } else if (dDist.profile === 'megaciudad') {
              badgeEl.className = "text-[9px] px-1.5 py-0.5 bg-purple-900/60 border border-purple-500/40 text-purple-200 rounded font-semibold";
            } else {
              badgeEl.className = "text-[9px] px-1.5 py-0.5 bg-cyan-900/60 border border-cyan-500/40 text-cyan-200 rounded font-semibold";
            }
          }

          if (bContainer && Array.isArray(dDist.brackets)) {
            bContainer.innerHTML = dDist.brackets.map(b => `
              <div class="space-y-0.5">
                <div class="flex justify-between items-center text-[10px]">
                  <span class="text-gray-300">${b.label} <span class="text-gray-500 text-[9px]">(${b.category})</span></span>
                  <span class="font-mono text-cyan-300 font-bold">${b.percentage.toFixed(1)}% <span class="text-gray-500 text-[9px]">(${b.commuters.toLocaleString()})</span></span>
                </div>
                <div class="w-full bg-stone-800 rounded-full h-1.5 overflow-hidden">
                  <div class="bg-gradient-to-r from-cyan-500 to-metro-pink h-1.5 rounded-full" style="width: ${Math.min(100, Math.max(0, b.percentage))}%"></div>
                </div>
              </div>
            `).join('');
          }
        } else {
          if (panelTld) panelTld.classList.add('hidden');
        }

        renderIsolatedZonesOnDemandMap();
        renderAffluenceZonesOnDemandMap();

        if (points.length > 0 && mapDemand) {
          const b = cityData.city.bbox;
          if (b && b.length === 4) mapDemand.fitBounds([[b[1], b[0]], [b[3], b[2]]]);
        }
        lucide.createIcons();
      } catch (e) {
        console.error("Error al cargar demanda final:", e);
      }
    }

    function toggleDemandLayers() {
      const chkRes = document.getElementById('chkLayerResidents')?.checked;
      const chkJobs = document.getElementById('chkLayerJobs')?.checked;
      const chkPois = document.getElementById('chkLayerPois')?.checked;
      const chkIso = document.getElementById('chkLayerIsolated')?.checked;

      if (chkRes) mapDemand.addLayer(demandLayers.residents); else mapDemand.removeLayer(demandLayers.residents);
      if (chkJobs) mapDemand.addLayer(demandLayers.jobs); else mapDemand.removeLayer(demandLayers.jobs);
      if (chkPois) mapDemand.addLayer(demandLayers.pois); else mapDemand.removeLayer(demandLayers.pois);
      if (chkIso) {
        if (isolatedZonesDemandGroup) mapDemand.addLayer(isolatedZonesDemandGroup);
      } else {
        if (isolatedZonesDemandGroup) mapDemand.removeLayer(isolatedZonesDemandGroup);
      }
    }

    function downloadCityPackage() {
      const btn = document.getElementById('btnDownloadZip');
      if (btn && btn.classList.contains('cursor-not-allowed')) {
        showToast("El paquete aún no ha sido compilado. Dirígete al Paso 5 para iniciar la compilación.", "warning");
        return;
      }
      window.location.href = `/api/download?file=${encodeURIComponent(currentCityFile)}`;
    }

    // -------------------------------------------------------------------------
    // UTILIDADES GENERALES
    // -------------------------------------------------------------------------
    function showToast(msg, type = "info", duration = 3500, action = null) {
      const toast = document.createElement('div');
      const colors = {
        success: 'bg-emerald-600 text-white border-emerald-500',
        error: 'bg-rose-600 text-white border-rose-500',
        warning: 'bg-amber-600 text-white border-amber-500',
        info: 'bg-stone-900 text-white border-stone-700'
      };
      toast.className = `wizard-toast fixed bottom-6 right-6 ${colors[type] || colors.info} border px-4 py-2.5 rounded-lg text-xs font-semibold shadow-2xl z-50 transition transform duration-300 flex items-center space-x-3`;
      toast.innerHTML = `<span>${msg}</span>`;

      let timer = null;
      if (action) {
        const btn = document.createElement('button');
        btn.className = "ml-2 px-2.5 py-1 rounded bg-white text-stone-900 hover:bg-stone-200 font-bold text-[11px] shadow transition shrink-0 cursor-pointer flex items-center space-x-1";
        btn.innerHTML = action.label;
        btn.onclick = () => {
          clearTimeout(timer);
          toast.remove();
          if (typeof action.callback === 'function') action.callback();
        };
        toast.appendChild(btn);
      }

      document.body.appendChild(toast);
      timer = setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
      }, duration);
      return toast;
    }

    function escapeHtml(str) {
      if (!str) return '';
      return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
