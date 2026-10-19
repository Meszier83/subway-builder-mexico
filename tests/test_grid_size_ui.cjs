const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../tools/static/wizard/app.js'), 'utf8');

function makeSelect() {
  const options = [
    { value: '0.0025', textContent: 'Estándar (~250m)', attributes: {} },
    { value: '0.0035', textContent: 'Amplia (~380m)', attributes: {} },
    { value: '0.0018', textContent: 'Fina (~180m)', attributes: {} }
  ];
  return {
    value: '0.0025',
    options,
    dataset: {},
    addEventListener() {},
    querySelector(sel) {
      if (sel === 'option[data-custom="true"]') {
        const found = options.find(o => o.attributes['data-custom'] === 'true');
        return found ? {
          remove() {
            const idx = options.indexOf(found);
            if (idx >= 0) options.splice(idx, 1);
          }
        } : null;
      }
      return null;
    },
    querySelectorAll(sel) {
      if (sel === 'option[data-custom="true"]') {
        const found = options.filter(o => o.attributes['data-custom'] === 'true');
        return found.map(f => ({
          remove() {
            const idx = options.indexOf(f);
            if (idx >= 0) options.splice(idx, 1);
          }
        }));
      }
      return [];
    },
    appendChild(opt) {
      options.push(opt);
    }
  };
}

const select = makeSelect();
const elements = new Map();
elements.set('cfg_grid_size', select);

function getElement(id) {
  if (!elements.has(id)) {
    let val = '';
    elements.set(id, {
      get value() { return val; },
      set value(v) { val = v === null || v === undefined ? '' : String(v); },
      textContent: '',
      innerText: '',
      dataset: {},
      options: [],
      classList: { contains() { return true; }, add() {}, remove() {} },
      addEventListener() {}
    });
  }
  return elements.get(id);
}

const context = {
  document: {
    getElementById: getElement,
    createElement(tag) {
      return {
        value: '',
        textContent: '',
        attributes: {},
        setAttribute(k, v) { this.attributes[k] = v; }
      };
    }
  },
  cityData: { city: {}, macroeconomics: {} },
  window: {},
  updateDemandEngineControls() {},
  updateDemandMethodsSummary() {},
  updateWorkplaceSourceStatus() {},
  updateBboxLayer() {},
  updateBboxDimensionsDisplay() {},
  toggleBboxLock() {},
  updateCohortUIFromData() {},
  updateCohortTelemetry() {},
  toggleModalExperimentControls() {},
  updateModalSimulatorPreview() {},
  renderUrbanCorePolygon() {},
  updateZoomPreviewLayer() {},
  renderGrowthFactorsTable() {},
  renderPoiList() {},
  renderPoiMarkersOnMap() {},
  renderPlacesList() {},
  renderPlacesMarkersOnMap() {},
  renderIsolatedZones() {},
  renderAffluenceZones() {},
  renderExclusionZones() {},
  updatePoiPreviewFromForm() {},
  triggerAutoSave() {},
  selectedDemandEngine() { return 'v2'; },
  syncCohortCount() {}
};

vm.createContext(context);
const matchPopulate = source.match(/    function populateFormFields\(\) \{[\s\S]*?\n    \}/);
assert.ok(matchPopulate, 'populateFormFields found');
vm.runInContext(matchPopulate[0], context);

const matchSync = source.match(/    function syncStateFromInputs\(\) \{[\s\S]*?\n    \}/);
assert.ok(matchSync, 'syncStateFromInputs found');
vm.runInContext(matchSync[0], context);

// Test 1: Standard 0.0025
context.cityData = { city: { grid_size: 0.0025 }, macroeconomics: {} };
context.populateFormFields();
assert.equal(select.value, '0.0025');
assert.equal(select.options.length, 3);

// Test 2: Custom 0.0020
context.cityData = { city: { grid_size: 0.002 }, macroeconomics: {} };
context.populateFormFields();
assert.equal(select.value, '0.002');
assert.equal(select.options.length, 4);
assert.equal(select.options[3].value, '0.002');
assert.match(select.options[3].textContent, /Personalizada \(0\.002\)/);

// Test 3: Sync back from select to cityData
context.syncStateFromInputs();
assert.equal(context.cityData.city.grid_size, 0.002);

// Test 4: Switch project to Standard 0.0035 (removes custom option)
context.cityData = { city: { grid_size: 0.0035 }, macroeconomics: {} };
context.populateFormFields();
assert.equal(select.value, '0.0035');
assert.equal(select.options.length, 3);

// Test 5: Scientific notation 2.5e-3 -> parses as 0.0025
context.cityData = { city: { grid_size: 2.5e-3 }, macroeconomics: {} };
context.populateFormFields();
assert.equal(select.value, '0.0025');
assert.equal(select.options.length, 3);

// Test 6: Custom 0.001
context.cityData = { city: { grid_size: 0.001 }, macroeconomics: {} };
context.populateFormFields();
assert.equal(select.value, '0.001');
assert.equal(select.options.length, 4);

// Test 7: Another custom 0.004 replaces previous custom
context.cityData = { city: { grid_size: 0.004 }, macroeconomics: {} };
context.populateFormFields();
assert.equal(select.value, '0.004');
assert.equal(select.options.length, 4);
assert.equal(select.options[3].value, '0.004');

// Test 8: renderGrowthFactorsTable does not throw when growthFactorsTableBody is absent from DOM (returns null)
const matchGrowthTable = source.match(/    function renderGrowthFactorsTable\(\) \{[\s\S]*?\n    \}/);
assert.ok(matchGrowthTable, 'renderGrowthFactorsTable found');
vm.runInContext(matchGrowthTable[0], context);
assert.doesNotThrow(() => {
  const origGet = context.document.getElementById;
  context.document.getElementById = (id) => id === 'growthFactorsTableBody' ? null : origGet(id);
  context.cityData = { city: { grid_size: 0.0025 }, macroeconomics: { growth_factors: { '31050': 1.15 } } };
  context.renderGrowthFactorsTable();
  context.document.getElementById = origGet;
});

console.log('Grid size UI edge cases: PASS');

