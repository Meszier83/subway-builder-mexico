"""Verify the generated private pilot without installing or launching the game."""
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/cur-economic-pilot'


def main():
    results = {}
    commands = {
        'diagnostic_tests': [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_cur_economic*.py'],
        'controller_and_bootstrap': ['node', 'tests/cur_economic_pilot.cjs'],
        'installed_worker_raptor': ['node', 'tests/cur_economic_worker_integration.cjs'],
        'packaged_javascript_syntax': ['node', '--check', str(OUT / 'index.js')]}
    for name, command in commands.items():
        run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
        results[name] = {'status': 'PASS' if run.returncode == 0 else 'FAIL', 'command': command,
                         'stdout': run.stdout, 'stderr': run.stderr}
        if name == 'installed_worker_raptor' and run.returncode == 0:
            results[name]['evidence'] = json.loads(run.stdout)
        print(name, results[name]['status'], flush=True)
    with zipfile.ZipFile(OUT / 'cur-economic-pilot-0.1.0.zip') as archive:
        assert archive.testzip() is None
        assert archive.read('cur-economic-profile/index.js') == (OUT / 'index.js').read_bytes()
        assert json.loads(archive.read('cur-economic-profile/profile.json')) == json.loads((OUT / 'profile.json').read_text())
        manifest = json.loads(archive.read('cur-economic-profile/manifest.json'))
        assert manifest['main'] == 'index.js'
        assert len(set(archive.namelist())) == len(archive.namelist())
    results['package_integrity'] = {'status': 'PASS'}
    results['live_game'] = {'status': 'PENDING', 'cases': ['visible UI', 'CUR-other-CUR', 'save/reload',
        'first-commute and in-flight handoff', 'full-day financial playability']}
    (OUT / 'validation.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf8')
    if any(r['status'] == 'FAIL' for r in results.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
