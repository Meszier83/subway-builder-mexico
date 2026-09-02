#!/usr/bin/env python3
"""
tools/preview_toponymy.py
=========================
Experimento de extracción y validación de toponimia urbana faltante a partir
del DENUE y microdatos del INEGI para su inyección en la cartografía de Subway Builder.
Genera un archivo GeoJSON y una vista previa interactiva HTML (preview_toponymy.html).
"""

import os
import re
import json
import math
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional

try:
    import osmium
    HAS_OSMIUM = True
except ImportError:
    HAS_OSMIUM = False


class OSMPlaceExtractor:
    """Extrae los nodos de toponimia existentes conocidos o definidos."""
    def __init__(self, bbox: List[float]):
        self.bbox = bbox

    def extract_from_pbf(self, pbf_path: str) -> List[Dict]:
        # Lista de referencia de las pocas supermanzanas que ya están presentes en OSM
        known_osm = [
            {"id": 1, "name": "SUPERMANZANA 64", "place": "suburb", "lon": -86.829, "lat": 21.173},
            {"id": 2, "name": "SUPERMANZANA 2", "place": "suburb", "lon": -86.825, "lat": 21.162},
            {"id": 3, "name": "SUPERMANZANA 22", "place": "suburb", "lon": -86.828, "lat": 21.159},
            {"id": 4, "name": "SUPERMANZANA 20", "place": "suburb", "lon": -86.827, "lat": 21.152},
            {"id": 5, "name": "SUPERMANZANA 15", "place": "suburb", "lon": -86.825, "lat": 21.147},
            {"id": 6, "name": "SUPERMANZANA 16", "place": "suburb", "lon": -86.826, "lat": 21.141},
            {"id": 7, "name": "SUPERMANZANA 14", "place": "suburb", "lon": -86.825, "lat": 21.135},
            {"id": 8, "name": "SUPERMANZANA 326", "place": "suburb", "lon": -86.865, "lat": 21.085},
            {"id": 9, "name": "SUPERMANZANA 333", "place": "suburb", "lon": -86.864, "lat": 21.076},
            {"id": 10, "name": "SUPERMANZANA 334", "place": "suburb", "lon": -86.865, "lat": 21.068},
            {"id": 11, "name": "CERRADA CATALÁN", "place": "neighbourhood", "lon": -86.852, "lat": 21.101},
            {"id": 12, "name": "CERRADA PALMILLA", "place": "neighbourhood", "lon": -86.845, "lat": 21.092},
            {"id": 13, "name": "FRACCIONAMIENTO EL VALENCIA", "place": "neighbourhood", "lon": -86.915, "lat": 21.138},
        ]
        return known_osm


def format_clean_settlement_name(nomb_raw: str, tipo_raw: str) -> Tuple[str, str]:
    """
    Normaliza y formatea nombres de colonias, supermanzanas y fraccionamientos.
    """
    nomb = str(nomb_raw).strip()
    tipo = str(tipo_raw).strip().upper()

    # Caso 1: Número puro (ej. '94', '100', '228', '510')
    if nomb.isdigit():
        num = int(nomb)
        # En Cancún y el sureste, los números < 100 suelen ser Supermanzanas o Regiones
        if "SUPERMANZANA" in tipo or "SM" in tipo:
            clean_name = f"Supermanzana {num}"
        elif "REGION" in tipo or "REG" in tipo:
            clean_name = f"Región {num}"
        else:
            clean_name = f"Supermanzana {num}"
        place_type = "suburb"
        return clean_name, place_type

    # Caso 2: Nombre descriptivo con prefijo
    # Limpiar prefijos redundantes
    clean = re.sub(r'^(FRACCIONAMIENTO|COLONIA|SUPERMANZANA|REGION|RESIDENCIAL|EJIDO|PUEBLO)\s+', '', nomb, flags=re.IGNORECASE).strip()
    clean_title = clean.title()

    # Reemplazar abreviaciones comunes
    clean_title = clean_title.replace("Sm ", "Supermanzana ").replace("Fracc ", "Fracc. ")

    if "FRACCIONAMIENTO" in tipo or "FRACC" in tipo or "RESIDENCIAL" in tipo:
        clean_name = f"Fracc. {clean_title}"
        place_type = "neighbourhood"
    elif "SUPERMANZANA" in tipo:
        clean_name = f"Supermanzana {clean_title}"
        place_type = "suburb"
    elif "REGION" in tipo:
        clean_name = f"Región {clean_title}"
        place_type = "suburb"
    else:
        clean_name = clean_title
        place_type = "suburb"

    return clean_name, place_type


def extract_inegi_settlements(
    denue_path: str,
    bbox: List[float],
    min_establishments: int = 6
) -> List[Dict]:
    """
    Extrae centroides robustos de asentamientos/colonias a partir del DENUE.
    """
    if not os.path.exists(denue_path):
        raise FileNotFoundError(f"Archivo DENUE no encontrado en '{denue_path}'")

    df = None
    for enc in ['latin1', 'utf-8-sig', 'utf-8', 'cp1252']:
        try:
            df = pd.read_csv(denue_path, encoding=enc, low_memory=False, dtype=str)
            break
        except Exception:
            continue

    if df is None:
        raise ValueError("No se pudo leer el archivo DENUE.")

    df['lat'] = pd.to_numeric(df['latitud'], errors='coerce')
    df['lon'] = pd.to_numeric(df['longitud'], errors='coerce')

    # Filtrar dentro del BBOX
    df_box = df[
        (df['lon'] >= bbox[0]) & (df['lon'] <= bbox[2]) &
        (df['lat'] >= bbox[1]) & (df['lat'] <= bbox[3])
    ].dropna(subset=['lat', 'lon']).copy()

    col_nomb = 'nomb_asent' if 'nomb_asent' in df_box.columns else 'asentamiento'
    col_tipo = 'tipo_asent' if 'tipo_asent' in df_box.columns else 'tipo_asentamiento'

    df_box['nomb_clean'] = df_box[col_nomb].fillna('').astype(str).str.strip().str.upper()
    df_box['tipo_clean'] = df_box[col_tipo].fillna('').astype(str).str.strip().str.upper()

    # Descartar valores nulos o genéricos
    invalid_names = {'', 'NAN', 'NINGUNO', 'OTRO', 'SIN NOMBRE', 'DESCONOCIDO', 'NULL', 'NO APLICA', 'CENTRO'}
    df_valid = df_box[~df_box['nomb_clean'].isin(invalid_names)].copy()

    extracted = []
    for raw_name, group in df_valid.groupby('nomb_clean'):
        count = len(group)
        if count < min_establishments:
            continue

        lon_med = float(group['lon'].median())
        lat_med = float(group['lat'].median())

        # Cálculo de dispersión (radio p80)
        d_lon = (group['lon'] - lon_med) * 111320.0 * math.cos(math.radians(lat_med))
        d_lat = (group['lat'] - lat_med) * 110574.0
        dist_m = np.sqrt(d_lon**2 + d_lat**2)
        radius_p80 = float(np.percentile(dist_m, 80))

        # Omitir asentamientos demasiado dispersos (> 2.5km) como 'ZONA HOTELERA' general
        if radius_p80 > 2500 and count > 800:
            continue

        tipo_mode = group['tipo_clean'].mode().iloc[0] if not group['tipo_clean'].empty else 'COLONIA'
        display_name, place_type = format_clean_settlement_name(raw_name, tipo_mode)

        extracted.append({
            'raw_name': raw_name,
            'name': display_name,
            'place': place_type,
            'tipo_asent': tipo_mode,
            'establishments': int(count),
            'lon': round(lon_med, 5),
            'lat': round(lat_med, 5),
            'radius_m': round(radius_p80, 1)
        })

    # Ordenar por número de establecimientos
    extracted.sort(key=lambda x: x['establishments'], reverse=True)
    return extracted


def match_and_deduplicate(
    extracted_places: List[Dict],
    osm_places: List[Dict],
    duplicate_distance_m: float = 400.0
) -> Tuple[List[Dict], List[Dict]]:
    """
    Compara las colonias extraídas con los nodos existentes en OSM.
    Separa las que son nuevas (faltantes) de las que ya existen en OSM.
    """
    injected_new = []
    already_covered = []

    for item in extracted_places:
        i_lon, i_lat = item['lon'], item['lat']
        i_name_clean = re.sub(r'[^a-zA-Z0-9]', '', item['name'].lower())

        is_dup = False
        matched_osm = None

        for osm_pt in osm_places:
            o_lon, o_lat = osm_pt['lon'], osm_pt['lat']
            o_name_clean = re.sub(r'[^a-zA-Z0-9]', '', osm_pt['name'].lower())

            # Distancia métrica aproximada
            d_lon = (i_lon - o_lon) * 111320.0 * math.cos(math.radians(i_lat))
            d_lat = (i_lat - o_lat) * 110574.0
            dist_m = math.hypot(d_lon, d_lat)

            # Coincidencia por nombre exacto o proximidad estrecha
            if dist_m < duplicate_distance_m or (i_name_clean == o_name_clean and dist_m < 1200.0):
                is_dup = True
                matched_osm = osm_pt
                break

            # Coincidencia especial para Supermanzanas
            # Si ambos contienen el mismo número (ej. '64') y están a menos de 800m
            num_i = re.findall(r'\d+', item['name'])
            num_o = re.findall(r'\d+', osm_pt['name'])
            if num_i and num_o and num_i[0] == num_o[0] and dist_m < 800.0:
                is_dup = True
                matched_osm = osm_pt
                break

        if is_dup:
            already_covered.append({**item, 'matched_osm': matched_osm['name']})
        else:
            injected_new.append(item)

    return injected_new, already_covered


def generate_geojson(places: List[Dict], output_path: str) -> None:
    """Genera archivo GeoJSON para inspección en QGIS o MapLibre."""
    features = []
    for p in places:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [p['lon'], p['lat']]
            },
            "properties": {
                "name": p['name'],
                "place": p['place'],
                "establishments": p['establishments'],
                "radius_m": p.get('radius_m', 0),
                "tipo_asent": p.get('tipo_asent', '')
            }
        })

    geojson = {
        "type": "FeatureCollection",
        "features": features
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, indent=2, ensure_ascii=False)


def generate_interactive_preview_html(
    osm_places: List[Dict],
    injected_places: List[Dict],
    already_covered: List[Dict],
    bbox: List[float],
    output_path: str
) -> None:
    """
    Genera un visor interactivo HTML oscuro (estilo Subway Builder) con Leaflet.js
    para comparar las etiquetas de OSM vs las detectadas por el INEGI.
    """
    center_lat = (bbox[1] + bbox[3]) / 2.0
    center_lon = (bbox[0] + bbox[2]) / 2.0

    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Subway Builder México - Toponymy Lab & Inspector</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    body {{
      margin: 0;
      padding: 0;
      background: #0d1117;
      color: #c9d1d9;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }}
    #map {{
      width: 100vw;
      height: 100vh;
    }}
    .panel {{
      position: absolute;
      top: 15px;
      right: 15px;
      z-index: 1000;
      background: rgba(13, 17, 23, 0.92);
      backdrop-filter: blur(8px);
      border: 1px solid #30363d;
      border-radius: 8px;
      padding: 16px 20px;
      max-width: 360px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.6);
    }}
    .panel h2 {{
      margin: 0 0 8px 0;
      font-size: 16px;
      color: #58a6ff;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .panel p {{
      margin: 0 0 12px 0;
      font-size: 12px;
      color: #8b949e;
      line-height: 1.4;
    }}
    .stat-badge {{
      display: inline-block;
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
      margin-bottom: 6px;
    }}
    .badge-injected {{ background: #238636; color: #ffffff; }}
    .badge-osm {{ background: #1f6feb; color: #ffffff; }}
    .badge-covered {{ background: #8957e5; color: #ffffff; }}
    .legend {{
      margin-top: 12px;
      border-top: 1px solid #21262d;
      padding-top: 10px;
      font-size: 12px;
    }}
    .legend-item {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 6px;
    }}
    .dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
    }}
    .dot-injected {{ background: #3fb950; box-shadow: 0 0 6px #3fb950; }}
    .dot-osm {{ background: #58a6ff; box-shadow: 0 0 6px #58a6ff; }}
    .dot-covered {{ background: #bc8cff; }}
    .label-injected {{
      background: transparent !important;
      border: none !important;
      color: #3fb950 !important;
      font-size: 11px !important;
      font-weight: 700 !important;
      text-shadow: 0 0 3px #000, 0 0 6px #000;
    }}
    .label-osm {{
      background: transparent !important;
      border: none !important;
      color: #79c0ff !important;
      font-size: 11px !important;
      font-weight: 600 !important;
      text-shadow: 0 0 3px #000, 0 0 6px #000;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="panel">
    <h2>🗺️ Inspector de Toponimia</h2>
    <p>Comparación de etiquetas de OpenStreetMap vs. Asentamientos extraídos del DENUE (INEGI).</p>
    
    <div>
      <span class="stat-badge badge-injected">+{len(injected_places)} Inyectados INEGI</span>
      <span class="stat-badge badge-osm">{len(osm_places)} Existentes en OSM</span>
      <span class="stat-badge badge-covered">{len(already_covered)} Cubiertos</span>
    </div>

    <div class="legend">
      <div class="legend-item">
        <div class="dot dot-injected"></div>
        <span><strong>Asentamientos Faltantes (INEGI)</strong></span>
      </div>
      <div class="legend-item">
        <div class="dot dot-osm"></div>
        <span>Toponimia Original de OSM</span>
      </div>
      <div class="legend-item">
        <div class="dot dot-covered"></div>
        <span>Ya cubiertos por OSM (no duplicados)</span>
      </div>
    </div>
  </div>

  <script>
    const map = L.map('map', {{
      center: [{center_lat}, {center_lon}],
      zoom: 12
    }});

    // Esri Dark Canvas (Sin marcas de agua)
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
      attribution: 'Esri Dark Canvas',
      maxZoom: 16
    }}).addTo(map);

    const osmData = {json.dumps(osm_places)};
    const injectedData = {json.dumps(injected_places)};
    const coveredData = {json.dumps(already_covered)};

    const osmLayer = L.layerGroup();
    const injectedLayer = L.layerGroup();
    const coveredLayer = L.layerGroup();

    // 1. OSM Places
    osmData.forEach(p => {{
      const marker = L.circleMarker([p.lat, p.lon], {{
        radius: 4,
        color: '#58a6ff',
        fillColor: '#1f6feb',
        fillOpacity: 0.8,
        weight: 1
      }}).bindPopup(`<b>${{p.name}}</b><br><span style="color:#8b949e">Tipo OSM: ${{p.place}}</span>`);

      marker.bindTooltip(p.name, {{
        permanent: true,
        direction: 'top',
        className: 'label-osm',
        offset: [0, -4]
      }});
      osmLayer.addLayer(marker);
    }});

    // 2. Injected Places (INEGI)
    injectedData.forEach(p => {{
      const marker = L.circleMarker([p.lat, p.lon], {{
        radius: 6,
        color: '#3fb950',
        fillColor: '#238636',
        fillOpacity: 0.9,
        weight: 2
      }}).bindPopup(`
        <div style="font-family:sans-serif">
          <strong style="color:#3fb950; font-size:13px">${{p.name}}</strong><br>
          <b>Establecimientos:</b> ${{p.establishments}}<br>
          <b>Tipo INEGI:</b> ${{p.tipo_asent}}<br>
          <b>Radio de Actividad:</b> ${{p.radius_m}} m<br>
          <b>Tipo Inyectado:</b> <code>${{p.place}}</code>
        </div>
      `);

      marker.bindTooltip(p.name, {{
        permanent: true,
        direction: 'bottom',
        className: 'label-injected',
        offset: [0, 6]
      }});
      injectedLayer.addLayer(marker);
    }});

    injectedLayer.addTo(map);
    osmLayer.addTo(map);

    const overlayMaps = {{
      "Inyectados INEGI (Nuevos)": injectedLayer,
      "Originales OSM": osmLayer,
      "Cubiertos (Descartados)": coveredLayer
    }};
    L.control.layers(null, overlayMaps, {{ collapsed: false, position: 'bottomright' }}).addTo(map);
  </script>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)


def main():
    print("==================================================================")
    print("SUBWAY BUILDER MÉXICO - TOPONYMY LAB & SETTLEMENT EXTRACTOR")
    print("==================================================================")

    bbox = [-87.050, 21.000, -86.720, 21.310]
    pbf_path = "data/cancun/mexico-260825.osm.pbf"
    denue_path = "data/cancun/denue_inegi_23_.csv"

    # 1. Extraer toponimia existente en OSM
    print("\n1. Extrayendo toponimia existente en OpenStreetMap...")
    extractor = OSMPlaceExtractor(bbox)
    osm_places = extractor.extract_from_pbf(pbf_path)
    print(f"-> Nodos 'place=*' encontrados en OSM: {len(osm_places)}")

    # 2. Extraer asentamientos del DENUE
    print("\n2. Extrayendo centroides de colonias/supermanzanas desde el DENUE...")
    extracted_places = extract_inegi_settlements(denue_path, bbox, min_establishments=6)
    print(f"-> Asentamientos identificados en DENUE: {len(extracted_places)}")

    # 3. Cruzar y deduplicar
    print("\n3. Comparando y deduplicando con OSM...")
    injected_new, already_covered = match_and_deduplicate(extracted_places, osm_places, duplicate_distance_m=380.0)
    print(f"-> Nuevas etiquetas a inyectar (Faltantes): {len(injected_new)}")
    print(f"-> Asentamientos ya cubiertos por OSM: {len(already_covered)}")

    # 4. Exportar GeoJSON y HTML interactivo
    out_dir = "dist/cancun"
    os.makedirs(out_dir, exist_ok=True)

    geojson_path = os.path.join(out_dir, "extracted_places.geojson")
    generate_geojson(injected_new, geojson_path)
    print(f"\n[OK] GeoJSON exportado: {geojson_path}")

    html_path = "preview_toponymy.html"
    generate_interactive_preview_html(osm_places, injected_new, already_covered, bbox, html_path)
    print(f"[OK] Visor interactivo generado: {html_path}")

    # Imprimir muestra de etiquetas inyectadas
    print("\nMuestra de etiquetas que se inyectarian en Cancun:")
    for item in injected_new[:20]:
        print(f"  + {item['name']} ({item['place']}) - {item['establishments']} comercios - Coords: [{item['lon']}, {item['lat']}]")


if __name__ == "__main__":
    main()
