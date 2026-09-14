"""
tools.generate_toponymy_preview
===============================
Genera un visor cartográfico interactivo HTML (Leaflet + Esri Dark Canvas)
para inspeccionar todas las Supermanzanas, Regiones y Colonias extraídas de
OpenStreetMap (nodos y polígonos) y el DENUE del INEGI.
"""

import os
import json
import argparse
from typing import Dict, List, Any


def build_preview_html(
    manifest_path: str,
    output_html_path: str
) -> str:
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    city_name = manifest.get("city", "Ciudad")
    osm_nodes = manifest.get("osm_nodes", [])
    osm_polygons = manifest.get("osm_polygons", [])
    denue_places = manifest.get("denue_places", [])

    total_count = len(osm_nodes) + len(osm_polygons) + len(denue_places)

    # Calcular centro aproximado
    all_lats = [p["lat"] for p in osm_nodes + osm_polygons + denue_places]
    all_lons = [p["lon"] for p in osm_nodes + osm_polygons + denue_places]
    center_lat = sum(all_lats) / len(all_lats) if all_lats else 21.1619
    center_lon = sum(all_lons) / len(all_lons) if all_lons else -86.8515

    raw_data_nodes = json.dumps(osm_nodes, ensure_ascii=False)
    raw_data_polys = json.dumps(osm_polygons, ensure_ascii=False)
    raw_data_denue = json.dumps(denue_places, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Subway Builder México - Inspector de Toponimia ({city_name})</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    body {{ margin: 0; padding: 0; background: #0a0e14; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #e6edf3; }}
    #map {{ position: absolute; top: 0; bottom: 0; width: 100%; height: 100%; }}
    .panel {{
      position: absolute; top: 16px; left: 16px; z-index: 1000;
      background: rgba(13, 17, 23, 0.92); backdrop-filter: blur(10px);
      border: 1px solid #30363d; border-radius: 12px;
      padding: 16px 20px; width: 340px;
      box-shadow: 0 12px 32px rgba(0,0,0,0.7);
    }}
    .panel h2 {{ margin: 0 0 6px 0; font-size: 16px; color: #58a6ff; display: flex; align-items: center; gap: 8px; }}
    .panel p {{ margin: 0 0 12px 0; font-size: 12px; color: #8b949e; line-height: 1.4; }}
    .search-box {{
      width: 100%; box-sizing: border-box; background: #0d1117; border: 1px solid #30363d;
      border-radius: 6px; padding: 8px 12px; color: #c9d1d9; font-size: 13px; margin-bottom: 12px;
      outline: none; transition: border-color 0.2s;
    }}
    .search-box:focus {{ border-color: #58a6ff; }}
    .stats-row {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }}
    .stat-badge {{
      padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; cursor: pointer;
      display: inline-flex; align-items: center; gap: 4px;
    }}
    .badge-total {{ background: #21262d; color: #f0f6fc; }}
    .badge-nodes {{ background: #1f6feb; color: #fff; }}
    .badge-polys {{ background: #0e7490; color: #fff; }}
    .badge-denue {{ background: #238636; color: #fff; }}
    .layer-toggle {{
      display: flex; align-items: center; gap: 8px; font-size: 12px; margin-bottom: 6px; cursor: pointer;
    }}
    .layer-toggle input {{ cursor: pointer; accent-color: #58a6ff; }}
    .lbl-osm-node {{
      background: transparent !important; border: none !important; color: #79c0ff !important;
      font-size: 10px !important; font-weight: 600 !important; text-shadow: 0 0 3px #000, 0 0 6px #000;
    }}
    .lbl-osm-poly {{
      background: transparent !important; border: none !important; color: #00e5ff !important;
      font-size: 10px !important; font-weight: 700 !important; text-shadow: 0 0 3px #000, 0 0 6px #000;
    }}
    .lbl-denue {{
      background: transparent !important; border: none !important; color: #4ade80 !important;
      font-size: 10px !important; font-weight: 700 !important; text-shadow: 0 0 3px #000, 0 0 6px #000;
    }}
    .leaflet-popup-content-wrapper {{
      background: #161b22; color: #c9d1d9; border: 1px solid #30363d; border-radius: 8px;
    }}
    .leaflet-popup-tip {{ background: #161b22; }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="panel">
    <h2>Inspector de Toponimia</h2>
    <p>Visualización en vivo de Supermanzanas y Colonias activas para <strong>{city_name}</strong>.</p>

    <input type="text" id="search" class="search-box" placeholder="Buscar Supermanzana, Región, Fracc..." oninput="filterMarkers()" />

    <div class="stats-row">
      <span class="stat-badge badge-total">Total: {total_count}</span>
      <span class="stat-badge badge-nodes">OSM Nodos: {len(osm_nodes)}</span>
      <span class="stat-badge badge-polys">OSM Polígonos: {len(osm_polygons)}</span>
      <span class="stat-badge badge-denue">DENUE: {len(denue_places)}</span>
    </div>

    <div style="border-top: 1px solid #30363d; padding-top: 10px;">
      <label class="layer-toggle">
        <input type="checkbox" id="chk-nodes" checked onchange="toggleLayers()" />
        <span style="color:#79c0ff">●</span> Nodos OSM Originales ({len(osm_nodes)})
      </label>
      <label class="layer-toggle">
        <input type="checkbox" id="chk-polys" checked onchange="toggleLayers()" />
        <span style="color:#00e5ff">●</span> Polígonos OSM Recuperados ({len(osm_polygons)})
      </label>
      <label class="layer-toggle">
        <input type="checkbox" id="chk-denue" checked onchange="toggleLayers()" />
        <span style="color:#4ade80">●</span> Asentamientos DENUE INEGI ({len(denue_places)})
      </label>
    </div>
  </div>

  <script>
    const map = L.map('map', {{
      center: [{center_lat:.4f}, {center_lon:.4f}],
      zoom: 12,
      wheelPxPerZoomLevel: 60,
      zoomSnap: 0.5
    }});

    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
      attribution: 'Esri Dark Canvas',
      maxNativeZoom: 16,
      maxZoom: 20
    }}).addTo(map);

    const dataNodes = {raw_data_nodes};
    const dataPolys = {raw_data_polys};
    const dataDenue = {raw_data_denue};

    const layerNodes = L.layerGroup().addTo(map);
    const layerPolys = L.layerGroup().addTo(map);
    const layerDenue = L.layerGroup().addTo(map);

    const allMarkers = [];

    function createMarker(p, color, labelClass, group, srcLabel) {{
      const m = L.circleMarker([p.lat, p.lon], {{
        radius: 5,
        color: color,
        fillColor: color,
        fillOpacity: 0.85,
        weight: 1.5
      }});

      let popupContent = '<div style="font-size:12px">' +
        '<strong style="color:' + color + '; font-size:13px">' + p.name + '</strong><br>' +
        '<b>Origen:</b> ' + srcLabel + '<br>' +
        '<b>Coordenadas:</b> [' + p.lon.toFixed(4) + ', ' + p.lat.toFixed(4) + ']<br>';
      if (p.establishments) {{
        popupContent += '<b>Comercios censados:</b> ' + p.establishments + '<br>';
      }}
      popupContent += '</div>';
      m.bindPopup(popupContent);

      m.bindTooltip(p.name, {{
        permanent: true,
        direction: 'top',
        className: labelClass,
        offset: [0, -5]
      }});

      m.placeData = p;
      m.targetGroup = group;
      group.addLayer(m);
      allMarkers.push(m);
      return m;
    }}

    dataNodes.forEach(p => createMarker(p, '#38bdf8', 'lbl-osm-node', layerNodes, 'OSM (Nodo Original)'));
    dataPolys.forEach(p => createMarker(p, '#00e5ff', 'lbl-osm-poly', layerPolys, 'OSM (Centroide Poligonal Recuperado)'));
    dataDenue.forEach(p => createMarker(p, '#4ade80', 'lbl-denue', layerDenue, 'DENUE (INEGI Complementario)'));

    function toggleLayers() {{
      const showNodes = document.getElementById('chk-nodes').checked;
      const showPolys = document.getElementById('chk-polys').checked;
      const showDenue = document.getElementById('chk-denue').checked;

      if (showNodes) map.addLayer(layerNodes); else map.removeLayer(layerNodes);
      if (showPolys) map.addLayer(layerPolys); else map.removeLayer(layerPolys);
      if (showDenue) map.addLayer(layerDenue); else map.removeLayer(layerDenue);
    }}

    function filterMarkers() {{
      const q = document.getElementById('search').value.toLowerCase().trim();
      let matchedFirst = null;

      allMarkers.forEach(m => {{
        const name = m.placeData.name.toLowerCase();
        const matches = !q || name.includes(q);
        const group = m.targetGroup;

        if (matches) {{
          if (!group.hasLayer(m)) group.addLayer(m);
          if (q && !matchedFirst && name.includes(q)) matchedFirst = m;
        }} else {{
          if (group.hasLayer(m)) group.removeLayer(m);
        }}
      }});

      if (matchedFirst && q.length >= 2) {{
        map.flyTo([matchedFirst.placeData.lat, matchedFirst.placeData.lon], 14, {{ duration: 0.8 }});
        matchedFirst.openPopup();
      }}
    }}
  </script>
</body>
</html>
"""
    os.makedirs(os.path.dirname(os.path.abspath(output_html_path)), exist_ok=True)
    with open(output_html_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_html_path


if __name__ == "__main__":
    manifest_file = "dist/cancun_riviera_maya/toponymy_manifest.json"
    html_file = "dist/cancun_riviera_maya/preview_toponymy.html"
    if os.path.exists(manifest_file):
        out = build_preview_html(manifest_file, html_file)
        print(f"[OK] Visor interactivo generado en: {out}")
    else:
        print(f"[ERROR] No se encontró {manifest_file}")
