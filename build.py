#!/usr/bin/env python3
"""
Subway Builder México CLI - v6.1
================================
Ejecución:
    python build.py cities/cancun.yaml
    python build.py cities/cancun.yaml --skip-map   (para regenerar solo la demanda)
"""

import sys
import argparse
from sb_mexico.pipeline import execute_pipeline

def main():
    parser = argparse.ArgumentParser(
        description="Subway Builder México Engine v6.1 - Compilador de Mapas y Demanda"
    )
    parser.add_argument(
        "config",
        help="Ruta al archivo YAML de configuración de la ciudad (ej. cities/cancun.yaml)"
    )
    parser.add_argument(
        "--skip-map",
        action="store_true",
        help="Omitir la compilación cartográfica (si ya tienes .pmtiles y roads.geojson generados)"
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directorio de trabajo / salida de los archivos generados (default: .)"
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Directorio donde se ubican los datos fuente del INEGI y OSM (default: igual a --output-dir)"
    )

    args = parser.parse_args()

    try:
        execute_pipeline(
            config_path=args.config,
            skip_map=args.skip_map,
            output_dir=args.output_dir,
            data_dir=args.data_dir
        )
    except Exception as e:
        print(f"\n[ERROR] Falló la ejecución del pipeline: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
