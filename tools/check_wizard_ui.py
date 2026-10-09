"""Check inline JS syntax and changed CONAPO/SSE behavior with a small DOM harness."""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
html = (ROOT / 'tools/templates/wizard.html').read_text(encoding='utf-8')
scripts = '\n'.join(body for attrs, body in re.findall(r'<script([^>]*)>(.*?)</script>', html, re.S)
                    if 'src=' not in attrs)
scripts += '\n' + (ROOT / 'tools/static/wizard/app.js').read_text(encoding='utf-8')
with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / 'wizard.js'
    path.write_text(scripts, encoding='utf-8')
    subprocess.run(['node', '--check', str(path)], check=True)
    subprocess.run(['node', str(ROOT / 'tests/conapo_panel_ui.cjs')], check=True)
    subprocess.run(['node', '--check', str(ROOT / 'tools/static/wizard/bootstrap.js')], check=True)
    subprocess.run(['node', str(ROOT / 'tests/wizard_startup_ui.cjs')], check=True)
    subprocess.run(['node', str(ROOT / 'tests/auto_urban_core_ui.cjs')], check=True)
    subprocess.run(['node', str(ROOT / 'tests/toponymy_scan_ui.cjs')], check=True)
    subprocess.run(['node', str(ROOT / 'tests/source_download_ui.cjs')], check=True)
    subprocess.run(['node', str(ROOT / 'tests/eic_source_status_ui.cjs')], check=True)
    subprocess.run(['node', str(ROOT / 'tests/demand_v2_ui.cjs')], check=True)
    subprocess.run(['node', str(ROOT / 'tests/demand_engine_toggle_ui.cjs')], check=True)
    subprocess.run(['node', str(ROOT / 'tests/residential_employment_ui.cjs')], check=True)
    # Functions in this file use top-level four-space indentation.
    functions = []
    for name in ('initSSE',):
        match = re.search(r'    (?:async )?function ' + name + r'\(.*?\n    }', scripts, re.S)
        if not match:
            raise RuntimeError(f'Missing UI function {name}')
        functions.append(match.group())
    harness = '''
const assert = require('node:assert/strict');
const controls = new Map();
const element = id => {
  if (!controls.has(id)) controls.set(id, { value: id === 'conapoYearSelect' ? '2025' : '',
    disabled: false, style: {}, classList: { add(...names) { this.hidden = names.includes('hidden'); },
      remove(...names) { if (names.includes('hidden')) this.hidden = false; } },
    setAttribute() {} });
  return controls.get(id);
};
const document = { getElementById: element };
const window = {};
const cityData = { city: { code: 'TST' }, macroeconomics: { growth_factors: { '23005': 1.23 } } };
const currentCityFile = 'cities/test.yaml';
let saved = 0, requests = 0;
const saveCurrentCity = async () => { saved++; return true; };
const showToast = () => {};
const updateConapoYearSelect = () => {};
const renderGrowthFactorsTable = () => {};
const triggerAutoSave = () => {};
const lucide = { createIcons() {} };
const appendTerminalLog = () => {};
let sseSource = null;
class EventSource { close() {} }
const fetch = async () => { requests++; return { json: async () => ({ status: 'ok',
  projection_year: 2024, requested_year: 2025, available_years: [2024, 2026],
  factors: [{ cve_mun: '23005', factor: 1.23 }, { cve_mun: '23008', factor: 1.11 }] }) }; };
''' + '\n'.join(functions) + '''
(async () => {
  initSSE();
  sseSource.onmessage({ data: JSON.stringify({ progress: 100, step_name: 'Solo demanda' }) });
  assert.equal(element('btnDownloadZipStep5').classList.hidden, true);
  sseSource.onmessage({ data: JSON.stringify({ progress: 100, step_name: 'Finalizado' }) });
  assert.equal(element('btnDownloadZipStep5').href, '/api/download?file=cities%2Ftest.yaml');
  assert.equal(element('btnDownloadZipStep5').classList.hidden, false);
  console.log('Wizard inline syntax and SSE behavior: PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    path.write_text(harness, encoding='utf-8')
    subprocess.run(['node', str(path)], check=True)
