"""Authoritative, project-isolated source discovery for statistical inputs."""

from __future__ import annotations

import glob
import os
import re
from typing import Any, Dict, Iterable, List, Optional


DATASET_PATTERNS: Dict[str, List[str]] = {
    "cpv": ["*RESAGEBURB*.csv", "*resageburb*.csv", "*censo*.csv", "*censo*.xlsx", "*cpv*.csv"],
    "denue": ["*denue*.csv", "*DENUE*.csv"],
    "marco": ["*mza*.shp", "*mza*.geojson", "*mza*.gpkg", "*ageb*.shp", "*ageb*.geojson", "*ageb*.gpkg", "*manzana*.shp", "*manzana*.geojson"],
    "ce": ["*SAIC*.csv", "*saic*.csv", "*exporta*.csv", "*cenu24*.csv", "*tr_ce*.csv", "*ce_*.csv", "*ce2024*.csv"],
    "enoe": ["*enoe*.csv", "*ENOE*.csv", "*trim*.csv", "*enoe*.xls", "*ENOE*.xls", "*trim*.xls", "*enoe*.xlsx", "*ENOE*.xlsx", "*trim*.xlsx"],
    "conapo": ["*pobproy*.csv", "*quinq*.csv", "*pob_proy*.csv", "*conapo*.csv", "data-*.csv", "*proyeccion*.csv"],
    "osm": ["*.osm.pbf", "*.osm"],
    "roads": ["roads.geojson"],
}

MULTI_FILE_DATASETS = {"cpv", "denue", "marco"}
NATIONAL_DATASETS = {"conapo", "osm"}


class SourceAmbiguityError(ValueError):
    """Raised when discovery finds several candidates for a singleton source."""


def _stable_files(directory: str, patterns: Iterable[str], exclusions: set[str]) -> List[str]:
    found: Dict[str, str] = {}
    if not directory or not os.path.isdir(directory):
        return []
    for pattern in patterns:
        for path in glob.glob(os.path.join(directory, pattern)):
            absolute = os.path.abspath(path)
            if not os.path.isfile(absolute) or os.path.basename(absolute).lower() in exclusions:
                continue
            found[os.path.normcase(absolute)] = absolute
    return [found[key] for key in sorted(found)]


def _as_paths(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise TypeError("Each data_sources entry must be a path or list of paths")


def _metadata(path: str, dataset: str, project_dir: str, method: str) -> Dict[str, Any]:
    name = os.path.basename(path)
    years = sorted({int(v) for v in re.findall(r"(?<!\d)(20\d{2})(?!\d)", name)})
    period_match = re.search(r"(20\d{2})[-_ ]?(?:T|Q|TRIM)[-_ ]?([1-4])", name, re.IGNORECASE)
    return {
        "path": os.path.abspath(path),
        "dataset": dataset,
        "geographic_scope": os.path.basename(os.path.normpath(project_dir)) if project_dir else "national",
        "year": years[0] if len(years) == 1 else None,
        "period": f"{period_match.group(1)}-T{period_match.group(2)}" if period_match else None,
        "selection_method": method,
        "size_bytes": os.path.getsize(path),
        "modified_ns": os.stat(path).st_mtime_ns,
    }


def resolve_source_manifest(
    config: Dict[str, Any],
    config_path: str,
    root_dir: str,
    data_dir: Optional[str] = None,
    strict_singletons: bool = True,
) -> Dict[str, Any]:
    """Resolve all inputs once, without cross-project statistical fallbacks."""
    city = config.get("city", {})
    city_base = os.path.splitext(os.path.basename(config_path))[0].lower()
    configured_dir = data_dir or config.get("data_dir")
    if configured_dir:
        project_dir = configured_dir if os.path.isabs(configured_dir) else os.path.join(root_dir, configured_dir)
    else:
        by_name = os.path.join(root_dir, "data", city_base)
        by_code = os.path.join(root_dir, "data", str(city.get("code", "")).lower())
        project_dir = by_name if os.path.isdir(by_name) else by_code
    project_dir = os.path.abspath(project_dir)
    national_dir = os.path.abspath(os.path.join(root_dir, "data"))
    if os.path.normcase(project_dir) == os.path.normcase(national_dir):
        raise ValueError("Project data_dir cannot be the shared national data root")
    exclusions = {os.path.basename(str(v)).strip().lower() for v in config.get("data_exclusions", [])}
    explicit = config.get("data_sources") or {}
    entries: Dict[str, List[Dict[str, Any]]] = {}

    for dataset, patterns in DATASET_PATTERNS.items():
        selected: List[str] = []
        method = "discovery"
        if dataset in explicit:
            method = "explicit"
            for raw_path in _as_paths(explicit[dataset]):
                path = raw_path if os.path.isabs(raw_path) else os.path.join(root_dir, raw_path)
                path = os.path.abspath(path)
                in_project = os.path.commonpath([project_dir, path]) == project_dir
                in_national_root = os.path.dirname(path) == national_dir
                if not in_project and not (dataset in NATIONAL_DATASETS and in_national_root):
                    raise ValueError(f"Explicit {dataset} source escapes the project bubble: {path}")
                if os.path.basename(path).lower() in exclusions:
                    raise ValueError(f"Explicit {dataset} source is excluded: {path}")
                if not os.path.isfile(path):
                    raise FileNotFoundError(f"Explicit {dataset} source does not exist: {path}")
                selected.append(path)
            selected = sorted(set(selected), key=os.path.normcase)
        else:
            selected = _stable_files(project_dir, patterns, exclusions)
            if not selected and dataset in NATIONAL_DATASETS:
                selected = _stable_files(national_dir, patterns, exclusions)
                method = "national_fallback" if selected else "discovery"

        if strict_singletons and dataset not in MULTI_FILE_DATASETS and len(selected) > 1:
            names = ", ".join(os.path.basename(path) for path in selected)
            raise SourceAmbiguityError(
                f"Ambiguous {dataset} sources ({names}). Configure data_sources.{dataset} explicitly."
            )
        dataset_entries = [_metadata(path, dataset, project_dir, method) for path in selected]
        inferred_years = {entry["year"] for entry in dataset_entries if entry["year"] is not None}
        if strict_singletons and method != "explicit" and dataset in MULTI_FILE_DATASETS and len(inferred_years) > 1:
            raise SourceAmbiguityError(
                f"Ambiguous {dataset} vintages {sorted(inferred_years)} across multi-file discovery; "
                f"configure data_sources.{dataset} explicitly"
            )
        declared_vintage = ((config.get("temporal") or {}).get("source_vintages") or {}).get(dataset)
        for entry in dataset_entries:
            if entry["year"] is None and isinstance(declared_vintage, int):
                entry["year"] = declared_vintage
                entry["year_source"] = "configuration"
            elif entry["year"] is not None:
                if isinstance(declared_vintage, int) and entry["year"] != declared_vintage:
                    raise ValueError(
                        f"{dataset} filename year {entry['year']} conflicts with declared vintage {declared_vintage}"
                    )
                entry["year_source"] = "filename"
            else:
                entry["year_source"] = None
        entries[dataset] = dataset_entries

    return {
        "project_dir": project_dir,
        "national_dir": national_dir,
        "exclusions": sorted(exclusions),
        "sources": entries,
    }


def manifest_paths(manifest: Dict[str, Any], dataset: str) -> List[str]:
    return [entry["path"] for entry in manifest.get("sources", {}).get(dataset, [])]
