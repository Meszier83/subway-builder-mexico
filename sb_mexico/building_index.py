"""Disk-backed versions of depot 1.2.7 index exporters; preserve its format math."""
import gzip
import json
import math
import shutil
import struct
from pathlib import Path
from shapely.geometry import shape, Polygon, MultiPolygon
from .building_storage import DiskList, DiskCells, disk_buffer, dump_index, iter_features, workspace

@workspace
def _convert_to_game_format(self, input_path, default_height=4.0):
    """
    Converts GeoJSON buildings into a spatial grid-indexed JSON for the
    game engine.
    """
    output_path = input_path.replace('cleaned', 'index')
    CS = 0.0009  # Cell size constant

    def calculate_polygon_centroid(coords):
        area = 0.0
        cx, cy = 0.0, 0.0
        if not coords or not coords[0]: return [0, 0]
        ring = coords[0]
        n = len(ring) - 1
        for i in range(n):
            x0, y0 = ring[i]
            x1, y1 = ring[i+1]
            cross_product = (x0 * y1 - x1 * y0)
            area += cross_product
            cx += (x0 + x1) * cross_product
            cy += (y0 + y1) * cross_product
        area *= 0.5
        if area == 0: return ring[0]
        cx /= (6 * area)
        cy /= (6 * area)
        return [cx, cy]

    if self.verb:
        print(f"***** Converting {self.city} buildings to game format *****")
    try:
        data = {'features': iter_features(input_path)}
    except Exception as e:
        raise RuntimeError(f"Failed to load building data: {e}")

    items = data.get('features', data.get('geometries', []))
    if not items:
        print("WARNING: No buildings found to index.")
        return

    buildings = DiskList()
    min_lon, min_lat = float('inf'), float('inf')
    max_lon, max_lat = float('-inf'), float('-inf')
    max_found_depth = 1

    for item in items:
        geom = item.get('geometry', item)
        props = item.get('properties', {})
        if not geom or geom.get('type') not in ['Polygon', 'MultiPolygon']:
            continue

        polys_coords = [geom['coordinates']] if geom['type'] == 'Polygon' \
                        else geom['coordinates']
        height = props.get('height')
        if height is None:
            height = default_height

        for poly_coord in polys_coords:
            cleaned_p = []
            b_minx, b_miny = float('inf'), float('inf')
            b_maxx, b_maxy = float('-inf'), float('-inf')

            # Determine foundation depth
            if self.create_building_foundations:
                if isinstance(poly_coord, list) and isinstance(poly_coord[0], list):
                    coord = poly_coord[0]
                else:
                    coord = poly_coord
                foundation = self._calculate_building_foundation(coord, height)
            else:
                foundation = 10

            for ring in poly_coord:
                if len(ring) < 3: continue
                if ring[0] != ring[-1]: ring.append(ring[0])

                cleaned_ring = []
                for p in ring:
                    px, py = p[0], p[1]
                    cleaned_ring.append([px, py])
                    if px < b_minx: b_minx = px
                    if py < b_miny: b_miny = py
                    if px > b_maxx: b_maxx = px
                    if py > b_maxy: b_maxy = py
                cleaned_p.append(cleaned_ring)

            if not cleaned_p: continue

            # Update global bbox
            min_lon, min_lat = min(min_lon, b_minx), min(min_lat, b_miny)
            max_lon, max_lat = max(max_lon, b_maxx), max(max_lat, b_maxy)
            max_found_depth = max(max_found_depth, foundation)

            buildings.append({
                "b": [b_minx, b_miny, b_maxx, b_maxy],
                "f": foundation,
                "p": cleaned_p,
                "center": calculate_polygon_centroid(cleaned_p)
            })

    if not buildings:
        print("STOP: No valid buildings found after processing!")
        return

    # Grid Calculation
    lat_mid = (min_lat + max_lat) / 2
    distortion_factor = 1 / math.cos(math.radians(lat_mid))
    cs_x = CS * distortion_factor
    grid_width_cols = math.ceil((max_lon - min_lon) / cs_x)
    grid_height_rows = math.ceil((max_lat - min_lat) / CS)

    cells = DiskCells()
    for idx, b in enumerate(buildings):
        cx, cy = b['center']
        gx = max(0, min(int((cx - min_lon) / cs_x), grid_width_cols - 1))
        gy = max(0, min(int((cy - min_lat) / CS), grid_height_rows - 1))

        key = (gx, gy)

        cells.setdefault(key, []).append(idx)
        # Temporary centroid is removed by the streaming JSON writer.

    final_json = {
        "cs": CS,
        "bbox": [min_lon, min_lat, max_lon, max_lat],
        "grid": [grid_width_cols + 1, grid_height_rows + 1],
        "cells": DiskList([x, y] + idxs for (x, y), idxs in cells.items()),
        "buildings": buildings,
        "stats": {"count": len(buildings), "maxDepth": max_found_depth}
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        dump_index(final_json, f)

    with gzip.open(output_path+'.gz', "wt", encoding="utf-8") as f:
        dump_index(final_json, f)

    if self.verb:
        print(f"Successfully saved building index to {output_path}")

@workspace
def create_buildings_index_binary(self, input_path, default_height=4.0):
    """
    Converts GeoJSON buildings into packed binary index streams (.bin and .bin.gz).
    """
    # Pre-compiled Struct layouts for buildings index binary
    _HEADER_STRUCT = struct.Struct("<IBBHI IIIIIII d ddddd")
    _UINT32 = struct.Struct("<I")
    _FLOAT32 = struct.Struct("<f")
    _COORD2D = struct.Struct("<2d")
    _BINARY_MAGIC = 0x49424253
    _BINARY_VERSION = 1
    _HEADER_SIZE = 88
    # Build output filename
    output_bin = input_path.replace("cleaned", "index").replace(".json", ".bin")
    if output_bin == input_path:
        output_base, _ = os.path.splitext(input_path)
        output_bin = f"{output_base}_index.bin"
    output_bin_gz = f"{output_bin}.gz"

    CS = 0.0009  # Spatial grid cell size constant (~100m)

    def round_num(num: float) -> float:
        return math.floor(num * 100000.0 + 0.5) / 100000.0

    def round_coords(coords: list) -> list:
        if not coords:
            return coords
        if isinstance(coords[0], (int, float)):
            return [round_num(c) for c in coords]
        return [round_coords(item) for item in coords]

    if self.verb:
        print(f"***** Generating building spatial index for {self.city} *****")

    try:
        data = {'features': iter_features(input_path)}
    except Exception as e:
        raise RuntimeError(f"Failed to load building data: {e}")

    items = data.get("features", data.get("geometries", []))
    if not items:
        print("WARNING: No buildings found to index.")
        return

    buildings = DiskList()
    min_lon, min_lat = float("inf"), float("inf")
    max_lon, max_lat = float("-inf"), float("-inf")

    for index, item in enumerate(items):
        geom = item.get("geometry", item)
        props = item.get("properties", {})
        if not geom or "coordinates" not in geom:
            continue

        height = props.get('height')
        if height is None:
            height = default_height

        try:
            # Parse geometry via Shapely
            geom_shape = shape(geom)

            if geom_shape.is_empty:
                continue

            # Handle MultiPolygons
            if isinstance(geom_shape, MultiPolygon):
                # Fallback Strategy: Extract the largest constituent polygon component by area
                geom_shape = max(geom_shape.geoms, key=lambda p: p.area)

            if not isinstance(geom_shape, Polygon):
                continue

            # Reconstruct coordinates from the chosen/resolved single Polygon
            # Exterior ring is first, followed by internal holes (interiors)
            polygon_coords = [list(geom_shape.exterior.coords)] + \
                             [list(hole.coords) for hole in geom_shape.interiors]

            cleaned_polygon = []
            b_minx, b_miny = float("inf"), float("inf")
            b_maxx, b_maxy = float("-inf"), float("-inf")

            for ring in polygon_coords:
                if len(ring) < 3:
                    continue
                # Ensure closure
                if ring[0] != ring[-1]:
                    ring.append(ring[0])

                cleaned_ring = []
                for pt in ring:
                    px, py = pt[0], pt[1]
                    cleaned_ring.append([px, py])
                    if px < b_minx:
                        b_minx = px
                    if py < b_miny:
                        b_miny = py
                    if px > b_maxx:
                        b_maxx = px
                    if py > b_maxy:
                        b_maxy = py
                cleaned_polygon.append(cleaned_ring)

            if not cleaned_polygon:
                continue

            b_minx, b_miny = round_num(b_minx), round_num(b_miny)
            b_maxx, b_maxy = round_num(b_maxx), round_num(b_maxy)

            # Determine foundation depth
            if self.create_building_foundations:
                if isinstance(cleaned_polygon, list) and isinstance(cleaned_polygon[0], list):
                    coord = cleaned_polygon[0]
                else:
                    coord = cleaned_polygon
                foundation = self._calculate_building_foundation(coord, height)
            else:
                foundation = 10

            buildings.append(
                {
                    "bounds": (b_minx, b_miny, b_maxx, b_maxy),
                    "foundationDepth": foundation,
                    "polygon": round_coords(cleaned_polygon),
                }
            )

            if b_minx < min_lon:
                min_lon = b_minx
            if b_maxx > max_lon:
                max_lon = b_maxx
            if b_miny < min_lat:
                min_lat = b_miny
            if b_maxy > max_lat:
                max_lat = b_maxy

        except Exception as error:
            print(f"Warning: Error processing building at index {index}: {error}")

    if not buildings:
        print("STOP: No valid buildings found after processing!")
        return

    min_lon, min_lat = round_num(min_lon), round_num(min_lat)
    max_lon, max_lat = round_num(max_lon), round_num(max_lat)

    cellSizeLon = CS / math.cos((((min_lat + max_lat) / 2) * math.pi) / 180)
    cols = math.ceil((max_lon - min_lon) / cellSizeLon)
    rows = math.ceil((max_lat - min_lat) / CS)

    cells_map = DiskCells()
    for building_id, b in enumerate(buildings):
        b_min_x, b_min_y, b_max_x, b_max_y = b["bounds"]
        min_col = math.floor((b_min_x - min_lon) / cellSizeLon)
        max_col = math.floor((b_max_x - min_lon) / cellSizeLon)
        min_row = math.floor((b_min_y - min_lat) / CS)
        max_row = math.floor((b_max_y - min_lat) / CS)

        for col in range(max(0, min_col), min(cols - 1, max_col) + 1):
            for row in range(max(0, min_row), min(rows - 1, max_row) + 1):
                cells_map.setdefault((col, row), []).append(building_id)

    sorted_cells = DiskList(
        {"col": k[0], "row": k[1], "buildingIds": v}
        for k, v in cells_map.items(sorted_cells=True)
        if v
    )

    total_rings = sum(len(b["polygon"]) for b in buildings)
    total_coords = sum(len(ring) for b in buildings for ring in b["polygon"])
    total_cell_refs = sum(len(c["buildingIds"]) for c in sorted_cells)
    max_found_depth = max(b["foundationDepth"] for b in buildings)

    if any(value < 0 or value > 0xFFFFFFFF for value in
           (len(buildings), cols, rows, total_rings, total_coords, len(sorted_cells), total_cell_refs)):
        raise ValueError('Building index exceeds its 32-bit format limits')

    # Calculate buffer allocations
    o = _HEADER_SIZE
    bounds_offset = o
    o += len(buildings) * 32
    foundation_depths_offset = o
    o += len(buildings) * 4
    o = (o + 7) & ~7
    building_ring_offsets_offset = o
    o += (len(buildings) + 1) * 4
    ring_coord_offsets_offset = o
    o += (total_rings + 1) * 4
    o = (o + 7) & ~7
    coords_offset = o
    o += total_coords * 16
    cell_row_starts_offset = o
    o += (rows + 1) * 4
    cell_cols_offset = o
    o += len(sorted_cells) * 4
    cell_building_offsets_offset = o
    o += (len(sorted_cells) + 1) * 4
    cell_building_ids_offset = o
    o += total_cell_refs * 4

    if shutil.disk_usage(Path(output_bin).parent).free < o * 2 + 64 * 1024**2:
        raise RuntimeError('Insufficient disk space for building binary index and compression')
    buffer = disk_buffer(o)

    # Write header metadata
    _HEADER_STRUCT.pack_into(
        buffer,
        0,
        _BINARY_MAGIC,
        _BINARY_VERSION,
        0,
        0,
        len(buildings),
        cols,
        rows,
        total_rings,
        total_coords,
        len(sorted_cells),
        total_cell_refs,
        0,
        CS,
        float(max_found_depth),
        min_lon,
        min_lat,
        max_lon,
        max_lat,
    )

    # 1. Bounds entries
    off = bounds_offset
    for b in buildings:
        struct.pack_into("<4d", buffer, off, *b["bounds"])
        off += 32

    # 2. Foundation depths
    off = foundation_depths_offset
    for b in buildings:
        _FLOAT32.pack_into(buffer, off, b["foundationDepth"])
        off += 4

    # 3. Geometry lookups
    ring_cursor = coord_cursor = 0
    ring_off = building_ring_offsets_offset
    coord_off = ring_coord_offsets_offset
    c_off = coords_offset

    for b in buildings:
        _UINT32.pack_into(buffer, ring_off, ring_cursor)
        ring_off += 4

        for ring in b["polygon"]:
            _UINT32.pack_into(buffer, coord_off, coord_cursor)
            coord_off += 4

            for pt in ring:
                _COORD2D.pack_into(buffer, c_off, pt[0], pt[1])
                c_off += 16
                coord_cursor += 1
            ring_cursor += 1

    _UINT32.pack_into(buffer, ring_off, ring_cursor)
    _UINT32.pack_into(buffer, coord_off, coord_cursor)

    # 4. Spatial index sequences
    ref_cursor = cell_row = 0
    _UINT32.pack_into(buffer, cell_row_starts_offset, 0)

    for i, cell in enumerate(sorted_cells):
        while cell_row < cell["row"]:
            cell_row += 1
            _UINT32.pack_into(
                buffer, cell_row_starts_offset + (cell_row * 4), i
            )

        _UINT32.pack_into(buffer, cell_cols_offset, cell["col"])
        cell_cols_offset += 4

        _UINT32.pack_into(
            buffer, cell_building_offsets_offset, ref_cursor
        )
        cell_building_offsets_offset += 4

        for b_id in cell["buildingIds"]:
            _UINT32.pack_into(buffer, cell_building_ids_offset, b_id)
            cell_building_ids_offset += 4
            ref_cursor += 1

    _UINT32.pack_into(
        buffer, cell_building_offsets_offset, ref_cursor
    )

    while cell_row < rows:
        cell_row += 1
        _UINT32.pack_into(
            buffer,
            cell_row_starts_offset + (cell_row * 4),
            len(sorted_cells),
        )

    # Stream outputs
    buffer.seek(0)
    with open(output_bin, "wb") as f:
        shutil.copyfileobj(buffer, f, 1024 * 1024)

    with open(output_bin, "rb") as src, gzip.open(output_bin_gz, "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
