"""Exercise CONAPO UI functions against real HTTP with disposable data only."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import wizard


def main():
    with tempfile.TemporaryDirectory(prefix='.conapo-e2e-', dir=ROOT) as temporary:
        root = Path(temporary)
        data = root / 'data'
        data.mkdir()
        (data / 'conapo.csv').write_text(
            'CLAVE,ANO,POB_TOTAL\n23005,2020,100\n23005,2024,115\n23005,2026,150\n', encoding='utf-8')
        (data / 'cpv.csv').write_text(
            'ENTIDAD,MUN,LOC,AGEB,MZA,POBTOT\n23,5,0,0000,0,100\n', encoding='utf-8')
        city = root / 'test.yaml'
        wizard.save_full_city_data(str(city), dict(
            city=dict(code='TST', name='CONAPO E2E', bbox=[-87, 20, -86, 22]),
            data_dir=data.relative_to(ROOT).as_posix(),
            macroeconomics=dict(projection_year=2024, growth_factors={'23005': 1.1})))
        server = subprocess.Popen([sys.executable, '-u', '-B', str(ROOT / 'tools/wizard.py'),
                                   '--port', '0', '--no-browser'], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        try:
            base = None
            for line in server.stdout:
                if 'Servidor iniciado en:' in line:
                    base = line.split('Servidor iniciado en:', 1)[1].strip().rstrip('/')
                    break
            if not base:
                raise RuntimeError('Temporary Wizard failed to start: ' + server.stderr.read())
            result = subprocess.run(['node', str(ROOT / 'tests/conapo_panel_e2e.cjs'),
                                     city.relative_to(ROOT).as_posix(), base], cwd=ROOT, timeout=120)
            return result.returncode
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__ == '__main__':
    raise SystemExit(main())
