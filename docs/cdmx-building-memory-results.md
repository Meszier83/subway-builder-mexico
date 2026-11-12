# CDMX building-memory repair results

Date: 2026-11-05. Status: implemented and activated. The default cartography
backend is `bounded`; the explicit `legacy` selector remains available.

CDMX cartography completed using the configured extent and existing building
selection rules. Both final PMTiles archives passed `pmtiles verify`. The binary
index passed an independent structural reader, and both compressed indexes
reproduced their originals exactly. WSL memory and swap settings were unchanged.

## Validation method and limits

The large run acquired, converted, filtered, and indexed the complete CDMX input.
Its Java tile subprocess was intentionally stopped after the index stages:
that process had loaded an earlier implementation revision with the installed
engine's fixed 16 GB heap. Tile generation then resumed from the completed
building/road files using the final heap budget and two threads, and completed
successfully. The original cold-run report therefore records a nonzero exit;
this was a controlled stage restart, not another OOM event.

This is validation of the complete cartography workflow in stages, plus a
separate integration run of the final production `process_buildings()` method
on 1,000 real CDMX records. It is not a second uninterrupted full-city run of
the final revision. Warm raw-cache reuse was verified separately; another full
warm cartography rebuild was not run. Full Wizard demand/ZIP delivery and game
import, save, and reload remain unverified. These artifacts are not a claim of
playability or a published map release.

## Measured results

| Check | Result |
| --- | --- |
| WSL at diagnosis | Approximately 7.4 GiB RAM and 2 GiB swap. |
| Complete Overture acquisition | 8,694,531 footprints; 934,012,458-byte raw Parquet cache. |
| Retained before the existing cap | 4,710,980 footprints. |
| Exported under the existing global cap | 850,000 footprints; 3,860,980 cap exclusions. |
| Building/index run sampled process-tree peak | 4.36 GiB, including the initial base extraction and interrupted initial Java stage. |
| Final tile-stage sampled process-tree peak | 2.36 GiB. |
| Warm raw-cache reuse | Approximately 1.5 seconds, with remote release lookup disabled. |
| Independent binary validation | 850,000 buildings, 852,134 rings, 6,847,453 coordinates, 1,338,602 valid cell references. |
| Final production-method integration fixture | 946 of 1,000 real records retained; manifest, compressed index, lock release, and native buffer release completed. |
| Generated scaling workload | 10,000 records: about 175 MiB peak RSS; 100,000: about 199 MiB. Tenfold volume added about 24 MiB. |
| High-vertex workload | 1,000 records with 10,000 vertices each completed at about 883 MiB peak, including synthetic Parquet generation. |
| Final regression run with bounded as default | 140 tests passed. |

Build telemetry samples aggregate process-tree RSS once per second. The two
large phases ran in separate Python processes, so their peaks are reported
separately. These measurements are not a universal memory guarantee for other
geometry complexity or competing workloads.

The final regression run covered building stages, run ownership, cache failures,
cartography resilience, ocean/cache behavior, Wizard delivery, toponymy, and
encoding. An earlier broader run including urban-LOD checks also passed. Small
fixture JSON/binary outputs matched the installed depot exporters, including
both foundation settings and Polygon/MultiPolygon/hole handling. Tests also
covered cross-batch global selection, centroid-boundary membership, a geometry
fixture using the current Merida BBOX, spawned-worker serialization, backend
version rejection, Windows-to-WSL option forwarding, and corrupt index references.
The Merida check was a local fixture, not a second full real-city build.

## Artifacts and evidence

Local evidence is under `reports/cdmx-building-memory/`:

- `validation-summary.json`: phase outcomes and explicit limitations.
- `cold/build.log` and `cold/resources.json`: acquisition through indexes and
  the intentionally interrupted initial tile process.
- `tile-resume/build.log`, `resources.json`, and `artifacts.json`: final tile
  generation, resource measurements, and artifact hashes.
- `real-index-validation.json`, `archive-validation.json`, and PMTiles metadata
  snapshots: independent index/archive checks and recorded layers.
- `method-smoke/validation.json`, `warm-cache.json`, and `scaling.json`:
  final production-method integration, cache reuse, and scaling measurements.

The copied validation outputs are in `cold/artifacts/`:

| File | Bytes |
| --- | ---: |
| `CDMX.pmtiles` | 76,198,628 |
| `CDMX_foundations.pmtiles` | 56,304,806 |
| `buildings_index.bin.gz` | 67,196,221 |
| `roads.geojson` | 88,934,978 |
| `runways_taxiways.geojson` | 1,746,187 |
| `ocean_depth_index.json.gz` | 4,733,030 |
| `ocean_depth_index_contours.json.gz` | 3,869,366 |

Archive bounds match the configured BBOX exactly:
`[-99.8291, 18.7608, -98.4481, 20.0634]`. The map contains building, land-use,
commercial, industrial, residential, hospital, water, ocean-foundation, and
city/suburb/neighborhood-label layers. The separate foundation archive contains
its `foundations` layer. No game-layer compatibility beyond the inspected depot
format and archive checks is asserted.

## Implementation and operation

Repository-owned MapGen stages write raw WKB to Parquet, filter geometry in
small batches, select records through SQLite, and stream cleaned/tile/foundation
GeoJSON. The index exporters retain depot 1.2.7 format calculations while moving
record/cell storage and binary assembly to disk. JSON and gzip are streamed.
Unused Arrow/native allocator buffers are released before tile generation.

Full builds use isolated run directories and exact current-run artifact paths.
Raw caches are checksum validated and writer locked; immutable raw files are
published through an atomic manifest. Geometry/source fingerprints guard
labels-only reuse. Native Linux and WSL share importable adapters for worker
serialization. Existing reference archives can be reused while temporary tile
files remain specific to the run.

The global 850,000-building cap is unchanged. Equal-area cutoff ties now use
raw-cache scan order, recorded explicitly with cutoff counts. The legacy NumPy
sort has no equivalent stable tie guarantee; exact retained membership at an
equal-area cutoff is not claimed. Building defaults, area/core predicates,
simplification, and index-format behavior were checked against small legacy
fixtures rather than assuming all edge cases are equivalent.

The supported bounded backend is pinned to `depot==1.2.7`. Another depot version
requires compatibility review or explicit `SB_BUILDINGS_BACKEND=legacy`.
`SB_BUILDINGS_REFRESH=1` requests a new raw acquisition. Windows forwards both
controls to the WSL runner. Cached Overture releases remain fixed until refresh.
After an uncatchable termination, verify the owner PID has stopped before
removing a writer lock whose path appears in the error.

The existing idle Wizard was restarted with its original Python interpreter;
the verified replacement is PID 28388 on port 8080, serving this checkout and
reporting idle. Its next cartography build uses the bounded default. City YAML,
demand data, WSL limits, and release feeds were not changed by this repair.
