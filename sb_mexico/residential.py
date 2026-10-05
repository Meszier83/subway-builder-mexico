"""Opt-in census placement using official INEGI polygons and explicit provenance."""

from pathlib import Path
import re

import geopandas as gpd
import numpy as np
import pandas as pd


PLACEMENT_MODES = ("legacy", "official_blocks")
AREA_KEYS = ["cve_mun_clean", "loc_clean", "ageb_clean"]
BLOCK_KEYS = AREA_KEYS + ["mza_clean"]


def validate_placement_mode(value):
    if value not in PLACEMENT_MODES:
        raise ValueError("city.residential_placement must be legacy or official_blocks")
    return value


def geo_code(value, width):
    """Normalize a code without inventing a missing locality or AGEB."""
    if pd.isna(value):
        return None
    value = str(value).strip().upper()
    if value.endswith(".0"):
        value = value[:-2]
    if not re.fullmatch(r"[0-9A-Z]+", value) or len(value) > width:
        return None
    return value.zfill(width)


def discover_marco_layers(project_dir):
    """Find city-local supported files; the loader validates their attributes."""
    if not project_dir or not Path(project_dir).is_dir():
        return []
    root = Path(project_dir).resolve()
    found = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in (".shp", ".geojson", ".gpkg"):
            continue
        # No symlink may make a recursive city lookup escape to another project.
        if not path.resolve().is_relative_to(root):
            continue
        name = path.stem.lower()
        hinted = any(token in name for token in ("mza", "manzana", "ageb")) or re.fullmatch(r"\d{2}[ma]", name)
        supported = False
        if not hinted:
            try:
                sample = gpd.read_file(path, rows=1)
                columns = {str(c).upper().strip() for c in sample.columns}
                full_col = next((c for c in sample if str(c).upper().strip() == "CVEGEO"), None)
                full_identity = full_col is not None and sample[full_col].astype(str).str.len().isin([13, 16]).any()
                supported = full_identity or (bool(columns & {"CVE_AGEB", "AGEB"})
                            and bool(columns & {"CVE_ENT", "ENTIDAD"})
                            and bool(columns & {"CVE_MUN", "MUN"}))
                supported = bool(supported and sample.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).any())
            except (ValueError, OSError, RuntimeError):
                pass
        if hinted or supported:
            found.append(str(path.resolve()))
    return sorted(set(found))


def load_official_layers(paths):
    """Return block/AGEB points, full identities, and diagnostic layer outcomes."""
    from sb_mexico.inegi import format_cve_mun

    blocks, areas, outcomes = [], [], []
    for path in dict.fromkeys(map(str, paths or [])):
        try:
            frame = gpd.read_file(path)
            geom_name = frame.geometry.name
            frame = frame.rename(columns={c: str(c).strip().upper() for c in frame.columns if c != geom_name})
            aliases = {"ENTIDAD": "CVE_ENT", "MUN": "CVE_MUN", "LOC": "CVE_LOC",
                       "AGEB": "CVE_AGEB", "MZA": "CVE_MZA", "MANZANA": "CVE_MZA"}
            frame = frame.rename(columns={c: v for c, v in aliases.items() if c in frame and v not in frame})
            # Full CVEGEO is authoritative when present and has the correct length.
            role = "block" if "CVE_MZA" in frame else "ageb"
            if "CVEGEO" in frame and frame["CVEGEO"].astype(str).str.len().eq(16).any():
                role = "block"
            widths = {"CVE_ENT": (0, 2), "CVE_MUN": (2, 5), "CVE_LOC": (5, 9), "CVE_AGEB": (9, 13)}
            if role == "block":
                widths["CVE_MZA"] = (13, 16)
            full = frame.get("CVEGEO", pd.Series(None, index=frame.index, dtype=object)).astype(str)
            usable_full = full.str.fullmatch(r"\d{9}[0-9A-Z]{4}" + (r"\d{3}" if role == "block" else ""))
            for col, (start, end) in widths.items():
                if col not in frame:
                    frame[col] = None
                frame[col] = frame[col].where(~usable_full, full.str.slice(start, end))
            if frame.empty or not frame["CVE_AGEB"].notna().any():
                raise ValueError("not a supported block/AGEB layer")
            if frame.crs is None:
                raise ValueError("missing CRS; coordinates were not assumed to be WGS84")
            valid = frame.geometry.notna() & ~frame.geometry.is_empty & frame.geometry.is_valid
            valid &= frame.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
            invalid_count = int((~valid).sum())
            frame = frame.loc[valid].copy()
            if frame.empty:
                raise ValueError("no usable polygon geometries")
            projected = frame if frame.crs.is_projected else frame.to_crs(frame.estimate_utm_crs())
            points = gpd.GeoSeries(projected.geometry.representative_point(), crs=projected.crs).to_crs(4326)
            result = pd.DataFrame({
                "cve_mun_clean": [format_cve_mun(m, e) for m, e in zip(frame.CVE_MUN, frame.CVE_ENT)],
                "loc_clean": frame.CVE_LOC.map(lambda v: geo_code(v, 4)),
                "ageb_clean": frame.CVE_AGEB.map(lambda v: geo_code(v, 4)),
                "lon_geo": points.x, "lat_geo": points.y,
            })
            keys = AREA_KEYS
            if role == "block":
                result["mza_clean"] = frame.CVE_MZA.map(lambda v: geo_code(v, 3))
                keys = BLOCK_KEYS
            valid_keys = result[keys].notna().all(axis=1) & result.cve_mun_clean.ne("-1")
            if role == "block":
                valid_keys &= result.mza_clean.ne("000")
            invalid_keys = int((~valid_keys).sum())
            result = result.loc[valid_keys].reset_index(drop=True)
            if result.empty:
                raise ValueError(f"no complete geographic identities ({invalid_keys} rejected rows)")
            (blocks if role == "block" else areas).append(result)
            outcomes.append({"path": path, "status": "loaded", "role": role,
                             "rows": len(result), "invalid_geometry": invalid_count,
                             "invalid_identity": invalid_keys})
        except (ValueError, TypeError, OSError, AttributeError, RuntimeError) as exc:
            outcomes.append({"path": path, "status": "unusable", "reason": str(exc)})
    def combine(parts, keys):
        if not parts:
            return None
        frame = pd.concat(parts, ignore_index=True)
        # Identical repeated geometry is safe. Distinct points for one identity are not.
        frame = frame.drop_duplicates(keys + ["lon_geo", "lat_geo"])
        conflicts = frame.duplicated(keys, keep=False)
        if conflicts.any():
            raise ValueError(f"Conflicting official geometry for {int(conflicts.sum())} records: "
                             f"{frame.loc[conflicts, keys].head(3).to_dict('records')}")
        frame.attrs["layer_diagnostics"] = outcomes
        return frame
    return combine(blocks, BLOCK_KEYS), combine(areas, AREA_KEYS), outcomes


def _match_points(census, candidates, keys):
    """Full joins first; abbreviated matches only where the target is unique."""
    coords = pd.DataFrame(np.nan, index=census.index, columns=["lon", "lat"])
    if candidates is None or candidates.empty:
        return coords
    target = candidates.dropna(subset=["lon_geo", "lat_geo"]).copy()
    target = target[np.isfinite(target.lon_geo) & np.isfinite(target.lat_geo)]
    target = target[target.lon_geo.between(-180, 180) & target.lat_geo.between(-90, 90)]
    left = census[keys].copy()
    left["_row"] = np.arange(len(census))
    complete = left[keys].notna().all(axis=1)
    full_target = target.dropna(subset=keys)
    # Caller ensures one coordinate pair per full key.
    matches = left.loc[complete].merge(full_target, on=keys, how="left", validate="many_to_one")
    positions = matches["_row"].to_numpy()
    coords.iloc[positions] = matches[["lon_geo", "lat_geo"]].to_numpy()
    short = [key for key in keys if key != "loc_clean"]
    # Do not drop locality from known-but-unmatched census records.
    unknown = left.loc[left.loc_clean.isna() & left[short].notna().all(axis=1)]
    # Count full identities, not distinct coordinates: equal coordinates do not
    # make two localities interchangeable.
    unique = target.loc[~target.duplicated(short, keep=False)]
    matches = unknown.merge(unique[short + ["lon_geo", "lat_geo"]], on=short,
                            how="left", validate="many_to_one")
    coords.iloc[matches["_row"].to_numpy()] = matches[["lon_geo", "lat_geo"]].to_numpy()
    # If the target itself lacks locality, use it only if the census short key
    # also identifies one locality (or exclusively legacy locality-less records).
    missing_target = target.loc[target.loc_clean.isna() & ~target.duplicated(short, keep=False)]
    identities = census[keys].drop_duplicates()
    ambiguous = identities.loc[identities.duplicated(short, keep=False), short].drop_duplicates()
    safe_left = left.merge(ambiguous.assign(_ambiguous=True), on=short, how="left")
    safe_left = safe_left.loc[safe_left._ambiguous.isna()]
    matches = safe_left.merge(missing_target[short + ["lon_geo", "lat_geo"]], on=short,
                              how="left", validate="many_to_one")
    for row, lon, lat in matches[["_row", "lon_geo", "lat_geo"]].itertuples(index=False, name=None):
        if pd.isna(coords.iloc[row, 0]) and pd.notna(lon) and pd.notna(lat):
            coords.iloc[row] = [lon, lat]
    return coords


def mass_summary(frame):
    return {"blocks": len(frame), "population": float(frame.pobtot_adj.sum()),
            "pea": float(frame.pea_real.sum())}


def place_census(census, denue, blocks, areas, bbox, layer_diagnostics):
    frame = census.copy().reset_index(drop=True)
    frame["ageb_clean"] = frame.ageb_clean.map(lambda v: geo_code(v, 4))
    frame["mza_clean"] = frame.mza_clean.map(lambda v: geo_code(v, 3))
    frame["lon"], frame["lat"] = np.nan, np.nan
    frame["placement_source"] = "unlocated"
    d = denue.copy() if denue is not None else pd.DataFrame()
    required = ["cve_mun_clean", "ageb_clean", "mza_clean", "lon", "lat"]
    d_blocks = d_areas = None
    if all(col in d for col in required):
        d["loc_clean"] = d.get("cve_loc", pd.Series(None, index=d.index, dtype=object)).map(lambda v: geo_code(v, 4))
        d["ageb_clean"] = d.ageb_clean.map(lambda v: geo_code(v, 4))
        d["mza_clean"] = d.mza_clean.map(lambda v: geo_code(v, 3))
        d["lon"] = pd.to_numeric(d.lon, errors="coerce")
        d["lat"] = pd.to_numeric(d.lat, errors="coerce")
        d = d[d.lon.between(-180, 180) & d.lat.between(-90, 90)].copy()
        d_blocks = d.groupby(BLOCK_KEYS, dropna=False)[["lon", "lat"]].mean().reset_index().rename(columns={"lon": "lon_geo", "lat": "lat_geo"})
        d_areas = d.groupby(AREA_KEYS, dropna=False)[["lon", "lat"]].mean().reset_index().rename(columns={"lon": "lon_geo", "lat": "lat_geo"})
    for name, candidates, keys in [("official_block", blocks, BLOCK_KEYS),
                                   ("denue_block", d_blocks, BLOCK_KEYS),
                                   ("official_ageb", areas, AREA_KEYS),
                                   ("denue_ageb", d_areas, AREA_KEYS)]:
        matched = _match_points(frame, candidates, keys)
        use = frame.lon.isna() & matched.lon.notna() & matched.lat.notna()
        frame.loc[use, ["lon", "lat"]] = matched.loc[use, ["lon", "lat"]]
        frame.loc[use, "placement_source"] = name
    sources = {str(name): mass_summary(group) for name, group in frame.groupby("placement_source")}
    unlocated = frame.lon.isna() | frame.lat.isna()
    inside = frame.lon.between(bbox["min_lon"], bbox["max_lon"]) & frame.lat.between(bbox["min_lat"], bbox["max_lat"])
    retained = frame.loc[~unlocated & inside].copy()
    report = {"mode": "official_blocks", "layers": layer_diagnostics,
              "input": mass_summary(frame), "sources_before_bbox": sources,
              "unlocated": mass_summary(frame.loc[unlocated]),
              "outside_bbox": mass_summary(frame.loc[~unlocated & ~inside]),
              "retained": mass_summary(retained),
              "sources_retained": {str(k): mass_summary(v) for k, v in retained.groupby("placement_source")}}
    retained.attrs["residential_placement"] = report
    employment_report = census.attrs.get('residential_employment')
    if employment_report is not None:
        from sb_mexico.residential_employment import record_placement
        record_placement(employment_report, frame, retained, unlocated, ~unlocated & ~inside)
    return retained


def grid_accounting(census, points, exclusion_zones=None, urban_core_polygon=None,
                    restrict_demand_to_urban_core=True):
    """Measure the existing grid's filters/rounding without changing its rules."""
    from sb_mexico.gravity import is_point_in_exclusion_zone, prepare_polygon_geom, is_point_in_prepared_polygon
    polygon = prepare_polygon_geom(urban_core_polygon) if restrict_demand_to_urban_core and urban_core_polygon else None
    exclusion = pd.Series([is_point_in_exclusion_zone(r.lon, r.lat, exclusion_zones)
                          for r in census.itertuples()], index=census.index, dtype=bool)
    core = pd.Series([not is_point_in_prepared_polygon(r.lon, r.lat, polygon) if polygon else False
                      for r in census.itertuples()], index=census.index, dtype=bool) & ~exclusion
    retained = census.loc[~exclusion & ~core]
    rounded_pop = sum(p["residents"] for p in points)
    rounded_pea = sum(p["pea_15ymas"] for p in points)
    return {"excluded": mass_summary(census.loc[exclusion]),
            "outside_core": mass_summary(census.loc[core]), "retained_before_rounding": mass_summary(retained),
            "grid_population": rounded_pop, "grid_pea": rounded_pea,
            "rounding_delta_population": rounded_pop - float(retained.pobtot_adj.sum()),
            "rounding_delta_pea": rounded_pea - float(retained.pea_real.sum())}
