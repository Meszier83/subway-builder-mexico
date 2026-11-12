"""Inspect 1.7.2, test and package Sandbox Balance Support; never install it."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
GAME = Path(r'C:\Program Files (x86)\Steam\steamapps\common\Subway Builder\resources\app.asar')
SOURCE = ROOT / 'mods/sandbox-balance-support'
OUT = ROOT / 'dist/sandbox-balance-support'
API_MEMBER = 'dist/renderer/public/index-CuszHwSj.js'
DASHBOARD_MEMBER = 'dist/renderer/public/GameMain-zem-cOEv.js'


def inspect_game(path: Path) -> dict:
    """Read the installed archive without extracting or modifying game files."""
    with path.open('rb') as stream:
        header = struct.unpack('<4I', stream.read(16))
        tree = json.loads(stream.read(header[3]))

        def read(member: str) -> bytes:
            node = tree
            for part in member.split('/'):
                node = node['files'][part]
            stream.seek(8 + header[1] + int(node['offset']))
            return stream.read(node['size'])

        version = json.loads(read('package.json'))['version']
        if version != '1.7.2':
            raise ValueError(f'Expected game 1.7.2; found {version}')
        api_bytes, dashboard_bytes = read(API_MEMBER), read(DASHBOARD_MEMBER)
        api, dashboard = api_bytes.decode('utf8'), dashboard_bytes.decode('utf8')
    required_api = ['useMainStore as aJ,', 'EXPENSE_CATEGORIES as bR,',
                    '"saveCheckPending": !![]', '"setFinancialHistory":',
                    '"setRouteFinancials":', '"onGameEnd":',
                    '"modding-api-reload-request"']
    required_dashboard = ['entries.slice(-24)', 'timeFrame === "hourly" ? -25 : -49',
                          'sortedDailyData.slice(-14)', 'entries[0]?.timestamp || 0',
                          'const profit = totalRevenue - operationalExpenses;',
                          '(entry.expenseCategories?.gradeCrossingMaintenance || 0)',
                          '(entry.expenseCategories?.other || 0)']
    for token in required_api:
        if token not in api:
            raise ValueError(f'Game API contract changed: {token}')
    for token in required_dashboard:
        if token not in dashboard:
            raise ValueError(f'Dashboard contract changed: {token}')
    start = dashboard.index('function FinancialDashboard(')
    end = dashboard.index('  return /* @__PURE__ */ jsxRuntimeExports.jsx(Dialog,', start)
    # Evaluate the REAL game's calculations in tests, with its rendering omitted.
    oracle = dashboard[start:end] + '\nreturn {processedData, totals, last24HoursTotals};\n}'
    return {'version': version, 'api_member': API_MEMBER, 'dashboard_member': DASHBOARD_MEMBER,
            'api_sha256': hashlib.sha256(api_bytes).hexdigest(),
            'dashboard_sha256': hashlib.sha256(dashboard_bytes).hexdigest(),
            'oracle': oracle}


def bundle() -> bytes:
    return ('\n'.join((SOURCE / name).read_text(encoding='utf8').rstrip()
                      for name in ('controller.js', 'bootstrap.js')) + '\n').encode('utf8')


def check_sources() -> None:
    paths = list(SOURCE.iterdir()) + [Path(__file__), ROOT / 'tests/sandbox_balance_support.cjs',
                                    ROOT / 'tests/test_sandbox_balance_support.py']
    for path in paths:
        data = path.read_bytes()
        data.decode('utf8')
        if data.startswith(b'\xef\xbb\xbf') or b'\r' in data or not data.endswith(b'\n'):
            raise ValueError(f'Source must be UTF-8 without BOM and use LF: {path}')
        if any(line.rstrip() != line for line in data.splitlines()):
            raise ValueError(f'Trailing whitespace: {path}')


def run_tests(game: dict) -> dict:
    result = subprocess.run(['node', '--experimental-vm-modules',
                             str(ROOT / 'tests/sandbox_balance_support.cjs')],
                            cwd=ROOT, input=json.dumps({'oracle': game['oracle']}),
                            capture_output=True, text=True, encoding='utf8', timeout=60)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asar', type=Path, default=GAME)
    parser.add_argument('--output', type=Path, default=OUT)
    args = parser.parse_args()
    check_sources()
    game = inspect_game(args.asar)
    tests = run_tests(game)
    args.output.mkdir(parents=True, exist_ok=True)
    content = bundle()
    (args.output / 'index.js').write_bytes(content)
    subprocess.run(['node', '--check', str(args.output / 'index.js')], check=True, timeout=30)
    manifest = json.loads((SOURCE / 'manifest.json').read_text(encoding='utf8'))
    archive_path = args.output / f'sandbox-balance-support-{manifest["version"]}.zip'
    files = {'index.js': content, 'manifest.json': (SOURCE / 'manifest.json').read_bytes(),
             'README.md': (SOURCE / 'README.md').read_bytes()}
    # Fixed metadata gives the same ZIP for the same reviewed sources.
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo('sandbox-balance-support/' + name, date_time=(2026, 10, 26, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None or len(archive.namelist()) != len(files):
            raise RuntimeError('Package integrity check failed')
        for name, data in files.items():
            if archive.read('sandbox-balance-support/' + name) != data:
                raise RuntimeError(f'Packaged file differs: {name}')
    evidence = {key: value for key, value in game.items() if key != 'oracle'}
    evidence.update(tests=tests, package=str(archive_path),
                    package_sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                    package_integrity='PASS', javascript_syntax='PASS', source_integrity='PASS',
                    live_game={'status': 'PENDING', 'cases': ['visible Balance tab and line figures',
                              'native game save/reload', 'live mod-manager disable/reload']})
    (args.output / 'validation.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf8')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
