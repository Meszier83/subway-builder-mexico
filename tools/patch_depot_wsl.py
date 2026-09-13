"""
tools.patch_depot_wsl
=====================
Aplica los parches canonicos a depot.maps en el entorno WSL Ubuntu:
1. Etiquetado dual explicito:
   - type: 'college', kind: 'college' para instituciones educativas.
   - type: 'commercial', kind: final_kind para comercios.
2. Regla canonica 'Campus Wins' (disyuntividad geometrica estricta):
   - Generacion de college_mask.
   - Sustraccion de college_mask sobre poligonos comerciales superpuestos.
3. Desbloqueo de CPU para calculo de oceanos (100% nucleos asignados).
4. Optimizacion de zoom en mascara de agua (z14 en vez de z15 para decodificacion de teselas).
"""

import os
import sys


def patch_campus_wins(content: str) -> tuple[str, bool]:
    """Aplica el parche Campus Wins de disyuntividad comercial/educativa."""
    if "college_mask" in content and "Campus Wins" in content:
        print("[OK] depot.maps ya cuenta con el parche canonico 'Campus Wins'.")
        return content, False

    modified = False

    # 1. Parche de etiquetado
    old_tagging = """                if final_kind != "college":
                    props = {'kind': final_kind, 'sort_rank': final_rank}
                else:
                    props = {'type': final_kind, 'sort_rank': final_rank}"""

    new_tagging = """                if final_kind == "college":
                    props = {'type': 'college', 'kind': 'college', 'sort_rank': final_rank}
                elif dest == "commercial":
                    props = {'type': 'commercial', 'kind': final_kind, 'sort_rank': final_rank}
                else:
                    props = {'kind': final_kind, 'sort_rank': final_rank}"""

    if old_tagging in content:
        content = content.replace(old_tagging, new_tagging, 1)
        modified = True

    # 2. Parche de mascara college
    old_mask = """        # Build commercial mask
        commercial_mask = None
        if "commercial" in new_layers_data:
            commercial_geoms = []
            for feat in new_layers_data["commercial"]:
                if feat["properties"].get("kind") == "commercial":
                    a_geom = shape(feat["geometry"]).intersection(tile_bounds)
                    if not a_geom.is_empty:
                        if not a_geom.is_valid:
                            a_geom = a_geom.buffer(0)
                        commercial_geoms.append(a_geom)
            if commercial_geoms:
                commercial_mask = unary_union(commercial_geoms)"""

    new_mask = """        # Build commercial mask
        commercial_mask = None
        if "commercial" in new_layers_data:
            commercial_geoms = []
            for feat in new_layers_data["commercial"]:
                if feat["properties"].get("kind") in ["commercial", "retail"] or feat["properties"].get("type") == "commercial":
                    a_geom = shape(feat["geometry"]).intersection(tile_bounds)
                    if not a_geom.is_empty:
                        if not a_geom.is_valid:
                            a_geom = a_geom.buffer(0)
                        commercial_geoms.append(a_geom)
            if commercial_geoms:
                commercial_mask = unary_union(commercial_geoms)

        # Build college mask for "Campus Wins" disjointness
        college_mask = None
        if "commercial" in new_layers_data:
            college_geoms = []
            for feat in new_layers_data["commercial"]:
                if feat["properties"].get("type") == "college" or feat["properties"].get("kind") == "college":
                    c_geom = shape(feat["geometry"]).intersection(tile_bounds)
                    if not c_geom.is_empty:
                        if not c_geom.is_valid:
                            c_geom = c_geom.buffer(0)
                        college_geoms.append(c_geom)
            if college_geoms:
                college_mask = unary_union(college_geoms)"""

    if old_mask in content:
        content = content.replace(old_mask, new_mask, 1)
        modified = True

    # 3. Parche de sustraccion comercial (Campus Wins)
    old_subtraction = """        # Subtract water and aerodrome from commercial
        if "commercial" in new_layers_data:
            kept = []
            for feat in new_layers_data["commercial"]:
                kind = feat["properties"].get("kind")
                
                if kind not in ["commercial"]:
                    kept.append(feat)
                    continue
                
                geom = shape(feat["geometry"])
                geom = geom.intersection(tile_bounds)
                if geom.is_empty:
                    continue
                if not geom.is_valid:
                    geom = geom.buffer(0)
                
                # Subtract water from commercial
                if merged_result is not None and not merged_result.is_empty:
                    if geom.intersects(merged_result):
                        geom = geom.difference(merged_result)
                        if not geom.is_valid:
                            geom = geom.buffer(0)
                
                # Subtract aerodromes from commercial
                if kind == "commercial" and aerodrome_mask is not None and not aerodrome_mask.is_empty:
                    if geom.intersects(aerodrome_mask):
                        geom = geom.difference(aerodrome_mask)
                        if not geom.is_valid:
                            geom = geom.buffer(0)
                
                # Final geometry verification & cleaning
                if geom.is_empty or geom.area < 1.0:
                    continue  

                if geom.geom_type not in ("Polygon", "MultiPolygon"):
                    parts = [g for g in geom.geoms 
                             if g.geom_type in ("Polygon", "MultiPolygon")]
                    if not parts:
                        continue
                    geom = unary_union(parts) if len(parts) > 1 else parts[0]
                feat["geometry"] = mapping(geom)
                kept.append(feat)
            new_layers_data["commercial"] = kept"""

    new_subtraction = """        # Subtract water, aerodrome and college from commercial ("Campus Wins")
        if "commercial" in new_layers_data:
            kept = []
            for feat in new_layers_data["commercial"]:
                is_college = feat["properties"].get("type") == "college" or feat["properties"].get("kind") == "college"
                
                geom = shape(feat["geometry"])
                geom = geom.intersection(tile_bounds)
                if geom.is_empty:
                    continue
                if not geom.is_valid:
                    geom = geom.buffer(0)
                
                # Subtract water from commercial and college
                if merged_result is not None and not merged_result.is_empty:
                    if geom.intersects(merged_result):
                        geom = geom.difference(merged_result)
                        if not geom.is_valid:
                            geom = geom.buffer(0)
                
                if is_college:
                    # College keeps its geometry intact (Campus Wins)
                    if geom.is_empty or geom.area < 1.0:
                        continue
                    if geom.geom_type not in ("Polygon", "MultiPolygon"):
                        parts = [g for g in geom.geoms 
                                 if g.geom_type in ("Polygon", "MultiPolygon")]
                        if not parts:
                            continue
                        geom = unary_union(parts) if len(parts) > 1 else parts[0]
                    feat["geometry"] = mapping(geom)
                    kept.append(feat)
                    continue

                # Commercial features: subtract aerodromes and college
                if aerodrome_mask is not None and not aerodrome_mask.is_empty:
                    if geom.intersects(aerodrome_mask):
                        geom = geom.difference(aerodrome_mask)
                        if not geom.is_valid:
                            geom = geom.buffer(0)
                
                # Subtract college from commercial ("Campus Wins")
                if college_mask is not None and not college_mask.is_empty:
                    if geom.intersects(college_mask):
                        geom = geom.difference(college_mask)
                        if not geom.is_valid:
                            geom = geom.buffer(0)
                
                # Final geometry verification & cleaning
                if geom.is_empty or geom.area < 1.0:
                    continue  

                if geom.geom_type not in ("Polygon", "MultiPolygon"):
                    parts = [g for g in geom.geoms 
                             if g.geom_type in ("Polygon", "MultiPolygon")]
                    if not parts:
                        continue
                    geom = unary_union(parts) if len(parts) > 1 else parts[0]
                feat["geometry"] = mapping(geom)
                kept.append(feat)
            new_layers_data["commercial"] = kept"""

    if old_subtraction in content:
        content = content.replace(old_subtraction, new_subtraction, 1)
        modified = True

    if modified:
        print("[OK] Parche 'Campus Wins' incorporado.")
    return content, modified


def patch_ocean_cpu(content: str) -> tuple[str, bool]:
    """Desbloquea el 100% de los nucleos asignados para el calculo paralelo de oceano."""
    if "Subway Builder Mexico: use all allocated cores" in content:
        print("[OK] depot.maps ya cuenta con el parche de desbloqueo de CPU para oceano.")
        return content, False

    old_cpu = """        # Select optimal number of parallel workers
        # Never use more than 1/2 the available cores
        ncores = max(1, min(self.ncores, os.cpu_count() // 2))"""

    new_cpu = """        # Select optimal number of parallel workers
        # Subway Builder Mexico: use all allocated cores (unthrottled)
        ncores = max(1, self.ncores)"""

    if old_cpu not in content:
        print("[WARN] No se encontro el bloque de throttling de CPU en depot/maps.py.")
        return content, False

    content = content.replace(old_cpu, new_cpu, 1)
    print("[OK] Parche de desbloqueo de CPU para oceano incorporado.")
    return content, True


def patch_ocean_water_zoom(content: str) -> tuple[str, bool]:
    """Optimiza el zoom de decodificacion de teselas de agua a z14 en vez de z15."""
    if "Subway Builder Mexico: optimized water_zoom" in content:
        print("[OK] depot.maps ya cuenta con el parche de optimizacion de zoom de agua (z14).")
        return content, False

    old_zoom_block = """        if self.verb:
            print(f"  Decoding water & ocean polygons directly from {self.raw_mbtiles} at zoom {self.maxzoom}")
            
        water_polygons = []
        # Target high resolution Zoom level `self.maxzoom`
        tiles = list(mercantile.tiles(self.bbox[0], self.bbox[1], 
                                      self.bbox[2], self.bbox[3], 
                                      self.maxzoom))
        
        conn = sqlite3.connect(self.raw_mbtiles)
        cursor = conn.cursor()
        
        for t in tiles:
            tms_y = (1 << self.maxzoom) - 1 - t.y # Invert Y for standard TMS lookup scheme
            cursor.execute(
                f"SELECT tile_data FROM tiles WHERE zoom_level={self.maxzoom} AND tile_column=? AND tile_row=?",
                (t.x, tms_y)
            )"""

    new_zoom_block = """        # Subway Builder Mexico: optimized water_zoom (z14 vs z15 reduces tile decode burden by 4x)
        water_zoom = min(14, self.maxzoom)
        if self.verb:
            print(f"  Decoding water & ocean polygons directly from {self.raw_mbtiles} at zoom {water_zoom}")
            
        water_polygons = []
        # Target high resolution Zoom level `water_zoom`
        tiles = list(mercantile.tiles(self.bbox[0], self.bbox[1], 
                                      self.bbox[2], self.bbox[3], 
                                      water_zoom))
        
        conn = sqlite3.connect(self.raw_mbtiles)
        cursor = conn.cursor()
        
        for t in tiles:
            tms_y = (1 << water_zoom) - 1 - t.y # Invert Y for standard TMS lookup scheme
            cursor.execute(
                f"SELECT tile_data FROM tiles WHERE zoom_level={water_zoom} AND tile_column=? AND tile_row=?",
                (t.x, tms_y)
            )"""

    old_buffer_line = """                        if geom_shape.geom_type in ["LineString", "MultiLineString"]:
                            safe_buffer = self._calculate_buffer(self.maxzoom)"""

    new_buffer_line = """                        if geom_shape.geom_type in ["LineString", "MultiLineString"]:
                            safe_buffer = self._calculate_buffer(water_zoom)"""

    if old_zoom_block not in content:
        print("[WARN] No se encontro el bloque de decodificacion de teselas a zoom {self.maxzoom} en depot/maps.py.")
        return content, False

    content = content.replace(old_zoom_block, new_zoom_block, 1)

    if old_buffer_line in content:
        content = content.replace(old_buffer_line, new_buffer_line, 1)

    print("[OK] Parche de optimizacion de zoom de agua (z14) incorporado.")
    return content, True


def patch_ram_safety(content: str) -> tuple[str, bool]:
    """Previene que valores ya en MB (>100) sean multiplicados accidentalmente por 1000."""
    if "Subway Builder Mexico: Guard against values already in MB" in content:
        print("[OK] depot.maps ya cuenta con el parche de seguridad de RAM.")
        return content, False

    old_setter = """        # GB uses base 10 (1000) - not to be confused with GiB (1024)
        # We store as MB for internal CLI tool flags
        self._RAM = int(value * 1000)"""

    new_setter = """        # GB uses base 10 (1000) - not to be confused with GiB (1024)
        # We store as MB for internal CLI tool flags
        # Subway Builder Mexico: Guard against values already in MB (>100)
        if value > 100:
            self._RAM = int(value)
        else:
            self._RAM = int(value * 1000)"""

    if old_setter not in content:
        print("[WARN] No se encontro el bloque del setter de RAM en depot/maps.py.")
        return content, False

    content = content.replace(old_setter, new_setter, 1)
    print("[OK] Parche de seguridad de RAM incorporado.")
    return content, True


def patch_resilient_buildings(content: str) -> tuple[str, bool]:
    """
    Subway Builder Mexico: Reemplaza Mapshaper por motor vectorial nativo en Python/Shapely.
    Elimina los cuellos de botella de heap en V8 (4GB) y evita colapsos por OOM en mega-ciudades.
    """
    if "Subway Builder Mexico: Resilient C++ (GEOS/Shapely) building processor" in content:
        print("[OK] depot.maps ya cuenta con el motor de resiliencia de edificios.")
        return content, False

    helper_code = '''    def _cleanup_buildings_resilient(self, output_cleaned_json, output_zoom_json=None, max_buildings=850000):
        """
        Subway Builder Mexico: Resilient C++ (GEOS/Shapely) building processor.
        Eliminates Mapshaper V8 4GB heap limit and WSL OOM crashes on large cities.
        """
        import os, time, math, json, shutil
        import pandas as pd
        import geopandas as gpd
        import shapely

        t0 = time.time()
        buildings_pkl = os.path.join(self.city_dir, "buildings.pkl")

        if os.path.exists(buildings_pkl):
            df = pd.read_pickle(buildings_pkl)
        elif self.buildings_geojson and os.path.exists(self.buildings_geojson):
            try:
                import pyogrio
                df = pyogrio.read_dataframe(self.buildings_geojson)
            except Exception:
                df = gpd.read_file(self.buildings_geojson)
        else:
            raise FileNotFoundError(f"No building data found for {self.city}")

        if df.empty:
            print("WARNING: No buildings found to clean.")
            empty_geojson = '{"type":"FeatureCollection","features":[]}'
            with open(output_cleaned_json, "w", encoding="utf-8") as f:
                f.write(empty_geojson)
            if output_zoom_json:
                with open(output_zoom_json, "w", encoding="utf-8") as f:
                    f.write(empty_geojson)
            return

        total_raw = len(df)
        if self.verb:
            print(f"  [Resilient Buildings Engine] Processing {total_raw:,} buildings with native GEOS/Shapely...")

        # 1. Compute geodesic areas in m2
        geoms = df['geometry'].values
        lat = (self.bbox[1] + self.bbox[3]) / 2.0
        m2_factor = (111320.0 * math.cos(math.radians(lat))) * 110574.0
        areas = shapely.area(geoms) * m2_factor

        # 2. Filter by building_index_filter_size and valid geometry
        min_area = getattr(self, "building_index_filter_size", 15.0)
        mask = (areas > min_area) & shapely.is_valid(geoms) & (~shapely.is_empty(geoms))

        # Subway Builder Mexico: Urban Core AOI LOD Filtering
        core_geojson = os.environ.get("SB_URBAN_CORE_GEOJSON")
        if not core_geojson or not os.path.exists(core_geojson):
            cand_core = os.path.join(self.city_dir, "urban_core.geojson")
            if os.path.exists(cand_core):
                core_geojson = cand_core

        if core_geojson and os.path.exists(core_geojson):
            try:
                import numpy as np
                with open(core_geojson, "r", encoding="utf-8") as cf:
                    cdata = json.load(cf)
                from shapely.geometry import shape
                core_geom = None
                if cdata.get("type") == "FeatureCollection" and cdata.get("features"):
                    core_geoms = [shape(f["geometry"]) for f in cdata["features"] if f.get("geometry")]
                    core_geom = shapely.unary_union(core_geoms) if core_geoms else None
                elif cdata.get("type") == "Feature" and cdata.get("geometry"):
                    core_geom = shape(cdata["geometry"])
                elif cdata.get("type") in ("Polygon", "MultiPolygon"):
                    core_geom = shape(cdata)

                if core_geom and not core_geom.is_empty:
                    if not core_geom.is_valid:
                        core_geom = core_geom.buffer(0)
                    centroids = shapely.centroid(geoms)
                    in_core = shapely.intersects(core_geom, centroids)
                    mask = mask & in_core
                    if self.verb:
                        print(f"  [Urban Core LOD] Edificios 3D restringidos al poligono nucleo ({int(np.sum(mask)):,} conservados).")
            except Exception as ce:
                print(f"  [WARN] No se pudo aplicar filtro LOD a edificios: {ce}")

        df_filtered = df[mask].copy()
        areas_filtered = areas[mask]

        # 3. Cap to max_buildings for WebGL stability in mega-metropolises
        if len(df_filtered) > max_buildings:
            if self.verb:
                print(f"  [Resilient Buildings Engine] Mega-city detected ({len(df_filtered):,} buildings > {min_area}m2). Prioritizing top {max_buildings:,} for 60 FPS WebGL stability...")
            top_indices = (-areas_filtered).argsort()[:max_buildings]
            df_filtered = df_filtered.iloc[top_indices].copy()

        # 4. Fill default height where missing
        if 'height' not in df_filtered.columns:
            df_filtered['height'] = 4.0
        else:
            df_filtered['height'] = df_filtered['height'].fillna(4.0)
            df_filtered['height'] = pd.to_numeric(df_filtered['height'], errors='coerce').fillna(4.0)

        # 5. Simplification for index
        idx_simp = getattr(self, "building_index_simplification", 0.2)
        tol_deg_idx = idx_simp / (111000.0 * math.cos(math.radians(lat)))
        df_cleaned = df_filtered.copy()
        df_cleaned['geometry'] = shapely.simplify(df_cleaned['geometry'].values, tolerance=tol_deg_idx, preserve_topology=True)

        gdf_cleaned = gpd.GeoDataFrame(df_cleaned, geometry='geometry', crs='EPSG:4326')
        try:
            gdf_cleaned.to_file(output_cleaned_json, driver='GeoJSON', engine='pyogrio')
        except Exception:
            gdf_cleaned.to_file(output_cleaned_json, driver='GeoJSON')

        if output_zoom_json:
            tile_simp = getattr(self, "building_tile_simplification", 0.2)
            if abs(tile_simp - idx_simp) < 1e-4:
                shutil.copyfile(output_cleaned_json, output_zoom_json)
            else:
                tol_deg_tile = tile_simp / (111000.0 * math.cos(math.radians(lat)))
                df_zoom = df_filtered.copy()
                df_zoom['geometry'] = shapely.simplify(df_zoom['geometry'].values, tolerance=tol_deg_tile, preserve_topology=True)
                gdf_zoom = gpd.GeoDataFrame(df_zoom, geometry='geometry', crs='EPSG:4326')
                try:
                    gdf_zoom.to_file(output_zoom_json, driver='GeoJSON', engine='pyogrio')
                except Exception:
                    gdf_zoom.to_file(output_zoom_json, driver='GeoJSON')

        elapsed = time.time() - t0
        if self.verb:
            size_mb = os.path.getsize(output_cleaned_json) / (1024 * 1024)
            print(f"  [OK] [Resilient Buildings Engine] {len(df_filtered):,} buildings saved ({size_mb:.1f} MB) in {elapsed:.1f}s.")

'''

    old_process_bldg = """        # 2. Mapshaper Cleanup
        cleaned_json = os.path.join(self.city_dir, "buildings_cleaned.json")
        mapshaper_cmd = (
            f"node --max-old-space-size={self.RAM} $(which mapshaper) "
            f"{self.buildings_geojson} -proj {self.epsg} -snap 0.5 -clean "
            f"-filter 'this.area > {self.building_index_filter_size}' "
            f"-simplify dp interval={self.building_index_simplification} "
            f"-proj wgs84 -o precision=0.00001 {cleaned_json}"
        )
        self._run_command(mapshaper_cmd)"""

    new_process_bldg = """        # 2. Resilient Buildings Cleanup (Subway Builder Mexico)
        cleaned_json = os.path.join(self.city_dir, "buildings_cleaned.json")
        self.buildings_zoom_geojson = os.path.join(self.city_dir, "buildings_zoom.geojson")
        self._cleanup_buildings_resilient(cleaned_json, self.buildings_zoom_geojson)"""

    old_tiles_block = """        mapshaper_cmd = (
            f"node --max-old-space-size={self.RAM} $(which mapshaper) "
            f"{self.buildings_geojson} -proj {self.epsg} -snap 0.5 "
            f"-filter 'this.area > {self.building_index_filter_size}' -clean "
            f"-simplify dp interval={self.building_tile_simplification} "
            f"-proj wgs84 -o precision=0.00001 {self.buildings_zoom_geojson}"
        )
        self._run_command(mapshaper_cmd)
        
        # Remove any features with no geometry
        with open(self.buildings_zoom_geojson, 'r') as f:
            geojson_data = json.load(f)
        geojson_data['features'] = [f for f in geojson_data['features'] \\
                                    if 'geometry' in f.keys() and f['geometry'] is not None]
        # Save the modified data
        with open(self.buildings_zoom_geojson, 'w', encoding='utf-8') as f:
            json.dump(geojson_data, f, indent=2)
        
        # Add default building height where needed
        self._set_default_building_height()"""

    new_tiles_block = """        # Subway Builder Mexico: Ensure buildings_zoom_geojson is generated
        if not hasattr(self, 'buildings_zoom_geojson') or not self.buildings_zoom_geojson or not os.path.exists(self.buildings_zoom_geojson):
            self.buildings_zoom_geojson = os.path.join(self.city_dir, "buildings_zoom.geojson")
            cleaned_json = os.path.join(self.city_dir, "buildings_cleaned.json")
            self._cleanup_buildings_resilient(cleaned_json, self.buildings_zoom_geojson)"""

    old_fetch_block = """        else:
            if self.verb:
                print("***** Loading previously downloaded buildings file: *****")
                print("    "+buildings_pkl)
            df = pd.read_pickle(buildings_pkl)
        
        gdf = gpd.GeoDataFrame(df, geometry='geometry', crs="EPSG:4326")
        
        if self.verb:
            print(f"Saving to {self.buildings_geojson}...", flush=True)
        gdf.to_file(self.buildings_geojson, driver='GeoJSON')"""

    new_fetch_block = """        else:
            if self.verb:
                print("***** Using previously downloaded buildings pickle: *****")
                print("    "+buildings_pkl)
            return"""

    old_foundations_call = """        # Make buildings foundations file
        self._create_building_foundation_files()"""

    new_foundations_call = """        # Make buildings foundations file
        if self.create_building_foundations:
            self._create_building_foundation_files()"""

    modified = False

    # Insert helper before def process_buildings
    if "def process_buildings(self):" in content and "_cleanup_buildings_resilient" not in content:
        content = content.replace("    def process_buildings(self):", helper_code + "    def process_buildings(self):", 1)
        modified = True

    if old_process_bldg in content:
        content = content.replace(old_process_bldg, new_process_bldg, 1)
        modified = True

    if old_tiles_block in content:
        content = content.replace(old_tiles_block, new_tiles_block, 1)
        modified = True

    if old_fetch_block in content:
        content = content.replace(old_fetch_block, new_fetch_block, 1)
        modified = True

    if old_foundations_call in content:
        content = content.replace(old_foundations_call, new_foundations_call, 1)
        modified = True

    if modified:
        print("[OK] Parche del motor de resiliencia de edificios incorporado.")
    return content, modified


def patch_depot_maps(file_path: str = None) -> bool:
    if file_path is None:
        try:
            import depot.maps
            file_path = depot.maps.__file__
        except ImportError:
            print("[WARN] depot.maps no esta instalado en este entorno.")
            return False

    if not os.path.exists(file_path):
        print(f"[WARN] Archivo no encontrado: {file_path}")
        return False

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    any_modified = False

    # 1. Parche Campus Wins
    content, mod_cw = patch_campus_wins(content)
    any_modified = any_modified or mod_cw

    # 2. Parche Desbloqueo CPU Oceano
    content, mod_cpu = patch_ocean_cpu(content)
    any_modified = any_modified or mod_cpu

    # 3. Parche Zoom Mascara Agua
    content, mod_zoom = patch_ocean_water_zoom(content)
    any_modified = any_modified or mod_zoom

    # 4. Parche Seguridad RAM
    content, mod_ram = patch_ram_safety(content)
    any_modified = any_modified or mod_ram

    # 5. Parche Resiliencia Edificios 3D
    content, mod_bldg = patch_resilient_buildings(content)
    any_modified = any_modified or mod_bldg

    # 6. Parche Filtrado de Parques Urbanos vs Selva/Bosques
    content, mod_parks = patch_urban_parks(content)
    any_modified = any_modified or mod_parks

    # 7. Parche Filtrado Urban Core AOI LOD para Edificios 3D
    content, mod_lod = patch_urban_core_lod(content)
    any_modified = any_modified or mod_lod

    if not any_modified:
        print("[OK] Todos los parches de depot.maps ya estan aplicados.")
        return True

    # Crear backup antes de escribir
    backup_path = file_path + ".bak"
    if not os.path.exists(backup_path):
        with open(backup_path, "w", encoding="utf-8") as bf:
            with open(file_path, "r", encoding="utf-8") as orig:
                bf.write(orig.read())

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"[OK] depot.maps actualizado y guardado exitosamente en {file_path}")
    return True


def patch_urban_parks(content: str) -> tuple[str, bool]:
    """
    Parche para permitir la exclusion de macro-reservas, selvas y bosques rurales
    cuando la variable de entorno SB_URBAN_PARKS_ONLY=1 esta activa.
    """
    if "SB_URBAN_PARKS_ONLY" in content:
        print("[OK] depot.maps ya cuenta con el soporte para SB_URBAN_PARKS_ONLY.")
        return content, False

    old_block = """        if any(x in v for x in ['park', 'nature_reserve', 'cemetery', 'pitch', 
                                'zoo', 'grass', 'wood', 'forest', 'scrub', 
                                'wetland', 'wilderness_area', 
                                'wildlife_sanctuary', 'state_forest', 
                                'national_wildlife_refuge', 'management_area', 
                                'wildlife_management_area']):
            return 'park', None, priority['park']"""

    new_block = """        # Subway Builder Mexico: Soporte opcional para solo parques urbanos
        urban_only = os.environ.get("SB_URBAN_PARKS_ONLY", "0") == "1"
        if urban_only:
            is_macro_wilderness = any(x in v for x in [
                'nature_reserve', 'wood', 'forest', 'scrub', 'wetland', 
                'wilderness_area', 'wildlife_sanctuary', 'state_forest', 
                'national_wildlife_refuge', 'management_area', 'wildlife_management_area',
                'national_park', 'protected_area'
            ])
            if not is_macro_wilderness and any(x in v for x in [
                'park', 'cemetery', 'pitch', 'zoo', 'grass', 'garden', 
                'recreation_ground', 'village_green', 'dog_park', 'playground'
            ]):
                return 'park', None, priority['park']
        else:
            if any(x in v for x in ['park', 'nature_reserve', 'cemetery', 'pitch', 
                                    'zoo', 'grass', 'wood', 'forest', 'scrub', 
                                    'wetland', 'wilderness_area', 
                                    'wildlife_sanctuary', 'state_forest', 
                                    'national_wildlife_refuge', 'management_area', 
                                    'wildlife_management_area']):
                return 'park', None, priority['park']"""

    if old_block in content:
        content = content.replace(old_block, new_block, 1)
        print("[OK] Parche SB_URBAN_PARKS_ONLY incorporado en depot.maps.")
        return content, True
    else:
        print("[WARN] No se encontro el bloque exacto de _get_kind_and_rank para parques.")
        return content, False


def patch_urban_core_lod(content: str) -> tuple[str, bool]:
    """
    Subway Builder Mexico: Soporte para Urban Core AOI LOD Filtering en edificios 3D.
    Excluye edificios 3D fuera del poligono nucleo urbano para acelerar compilacion
    y mantener 60 FPS en WebGL.
    """
    if "SB_URBAN_CORE_GEOJSON" in content:
        print("[OK] depot.maps ya cuenta con el soporte para SB_URBAN_CORE_GEOJSON.")
        return content, False

    old_block = """        # 2. Filter by building_index_filter_size and valid geometry
        min_area = getattr(self, "building_index_filter_size", 15.0)
        mask = (areas > min_area) & shapely.is_valid(geoms) & (~shapely.is_empty(geoms))
        df_filtered = df[mask].copy()"""

    new_block = """        # 2. Filter by building_index_filter_size and valid geometry
        min_area = getattr(self, "building_index_filter_size", 15.0)
        mask = (areas > min_area) & shapely.is_valid(geoms) & (~shapely.is_empty(geoms))

        # Subway Builder Mexico: Urban Core AOI LOD Filtering
        core_geojson = os.environ.get("SB_URBAN_CORE_GEOJSON")
        if not core_geojson or not os.path.exists(core_geojson):
            cand_core = os.path.join(self.city_dir, "urban_core.geojson")
            if os.path.exists(cand_core):
                core_geojson = cand_core

        if core_geojson and os.path.exists(core_geojson):
            try:
                import numpy as np
                with open(core_geojson, "r", encoding="utf-8") as cf:
                    cdata = json.load(cf)
                from shapely.geometry import shape
                core_geom = None
                if cdata.get("type") == "FeatureCollection" and cdata.get("features"):
                    core_geoms = [shape(f["geometry"]) for f in cdata["features"] if f.get("geometry")]
                    core_geom = shapely.unary_union(core_geoms) if core_geoms else None
                elif cdata.get("type") == "Feature" and cdata.get("geometry"):
                    core_geom = shape(cdata["geometry"])
                elif cdata.get("type") in ("Polygon", "MultiPolygon"):
                    core_geom = shape(cdata)

                if core_geom and not core_geom.is_empty:
                    if not core_geom.is_valid:
                        core_geom = core_geom.buffer(0)
                    centroids = shapely.centroid(geoms)
                    in_core = shapely.intersects(core_geom, centroids)
                    mask = mask & in_core
                    if self.verb:
                        print(f"  [Urban Core LOD] Edificios 3D restringidos al poligono nucleo ({int(np.sum(mask)):,} conservados).")
            except Exception as ce:
                print(f"  [WARN] No se pudo aplicar filtro LOD a edificios: {ce}")

        df_filtered = df[mask].copy()"""

    if old_block in content:
        content = content.replace(old_block, new_block, 1)
        print("[OK] Parche SB_URBAN_CORE_GEOJSON incorporado en depot.maps.")
        return content, True
    else:
        print("[WARN] No se encontro el bloque exacto de filtrado de edificios para SB_URBAN_CORE_GEOJSON.")
        return content, False


if __name__ == "__main__":
    patch_depot_maps()

