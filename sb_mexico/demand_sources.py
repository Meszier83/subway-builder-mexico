"""Ordered source selection shared by builds and Wizard previews."""
from pathlib import Path
import glob

PATTERNS = {
    'denue': ['*denue*.csv', '*DENUE*.csv'],
    'cpv': ['*RESAGEBURB*.csv', '*resageburb*.csv', '*censo*.csv', '*censo*.xlsx', '*cpv*.csv'],
    'ce': ['*SAIC*.csv', '*saic*.csv', '*exporta*.csv', '*cenu24*.csv', '*tr_ce*.csv', '*ce_*.csv', '*ce2024*.csv'],
    # Keep existing CSV precedence; Excel uses the same selection in all callers.
    'enoe': ['*2026_trim*.csv', '*2024_trim*.csv', '*2025_trim*.csv', '*trim*.csv', '*enoe*.csv', '*ENOE*.csv',
             '*trim*.xls', '*enoe*.xls', '*ENOE*.xls', '*trim*.xlsx', '*enoe*.xlsx', '*ENOE*.xlsx'],
    'conapo': ['*pobproy*.csv', '*quinq*.csv', '*pob_proy*.csv', '*conapo*.csv', 'data-*.csv', '*proyeccion*.csv'],
    'marco': ['*mza*.shp', '*mza*.geojson', '*mza*.gpkg', '*ageb*.shp', '*ageb*.geojson',
              '*ageb*.gpkg', '*manzana*.shp', '*manzana*.geojson'],
}


def select_sources(project_dir, national_dir, kind, exclusions=()):
    found, seen = [], set()
    excluded = {str(name).lower() for name in exclusions}
    for folder in dict.fromkeys((str(project_dir), str(national_dir))):
        for pattern in PATTERNS[kind]:
            for name in sorted(glob.glob(str(Path(folder) / pattern))):
                path = Path(name).resolve()
                if path.is_file() and path.name.lower() not in excluded and path not in seen:
                    found.append(str(path))
                    seen.add(path)
    return found
