#!/usr/bin/env python3
"""
Subway Builder México CLI - v6.3
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
        description="Subway Builder México Engine v6.3 - Compilador de Mapas y Demanda"
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
        default=None,
        help="Directorio de trabajo / salida de los archivos generados (default: dist/<ciudad>/)"
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Directorio donde se ubican los datos fuente del INEGI y OSM (default: data/<ciudad>/)"
    )

    args = parser.parse_args()

    # Si no se especifica output_dir, aislar obligatoriamente en dist/<ciudad>
    target_out = args.output_dir
    if not target_out:
        import os
        city_slug = os.path.splitext(os.path.basename(args.config))[0].lower()
        target_out = os.path.join("dist", city_slug)

    try:
        execute_pipeline(
            config_path=args.config,
            skip_map=args.skip_map,
            output_dir=target_out,
            data_dir=args.data_dir
        )
    except Exception as e:
        print(f"\n[ERROR] Falló la ejecución del pipeline: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
