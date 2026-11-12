# CDMX building-memory implementation plan

Date: 2026-11-05. Status: implemented and activated; validation evidence and remaining limits
are recorded in `cdmx-building-memory-results.md`.

## Objective

Complete CDMX cartography on the current WSL memory allocation by bounding memory
throughout building acquisition, geometry conversion, cleanup, tile preparation,
foundation generation, and index serialization. Keep the configured map extent
and existing building-selection rules.

Implementation was authorized after the plan was created. WSL memory and swap
settings remain unchanged. Progress and measured results are recorded in
`cdmx-building-memory-results.md`.

## Confirmed failure and baseline

- The supplied build log reached `Converting WKB to Geometry...` after base
  extraction completed, then reported WSL exit code 9.
- The retained kernel journal records an OOM kill of `python3`, PID 983, at
  2026-11-05 00:44:22 America/Mexico_City (06:44:22 UTC). Its anonymous resident
  memory was 7,221,428 KiB, approximately 6.9 GiB. Free swap was zero; total swap
  was 2 GiB. The base GeoJSON was written approximately nine minutes earlier.
- At diagnosis, WSL reported 7,729,808 KiB total memory, approximately 7.4 GiB,
  and 2 GiB swap. This is a current measurement, not a complete memory trace of
  the failed run. Docker also appears in the OOM event; it is competing workload,
  not evidence that Docker caused the loader's allocation pattern.
- Installed `depot.maps.MapGen._fetch_overture_buildings()` calls
  `con.query(query).to_df()` and converts the entire geometry column with
  `Series.apply(wkb.loads)`. It saves `buildings.pkl` afterward. No building
  pickle exists in the failed run's output directory.
- The urban-core and area filters execute in `_cleanup_buildings_resilient()`,
  after acquisition and conversion. The existing 850,000-building cap is also
  applied after loading all buildings, so neither prevents the initial spike.
- Later exporters use `json.load()` for the complete building collection. The
  binary exporter holds geometry lists, cell-reference lists, a full bytearray,
  a bytes copy, and compressed bytes. Foundation generation also loads the
  entire tile GeoJSON. Replacing only the first DataFrame load is insufficient.
- The geometry warnings in the supplied log preceded successful extraction;
  they do not explain the confirmed OOM termination.

Inspected runtime: depot 1.2.7, DuckDB 1.5.5, PyArrow 25.0.1, Shapely 2.1.2,
GeoPandas 1.1.4, pandas 3.0.5. Installed source:
`/home/keppl/.local/lib/python3.14/site-packages/depot/maps.py`.
Its SHA-256 at inspection was
`321ad3716ef70e6ee3fdc3c4be4733d8b8f98d73e692f575003ec3f6176fc014`.
Recheck this runtime before implementation; project dependency ranges do not
pin these versions.

## Scope and contracts

Preserve CDMX's BBOX, urban-core polygon, demand inputs and totals, LOD modes,
building-area thresholds, simplification tolerances, default-height behavior,
foundation settings, tile limits, layer names, and final artifact names. Do not
shrink the city, lower the existing building cap, or sample buildings as a memory
workaround. Increasing WSL RAM/swap is an optional operational workaround, not
the repair's acceptance criterion.

Match the installed loader's BBOX containment predicate. Match the installed
cleanup's area approximation and strict thresholds. Urban-core membership uses
the building centroid intersecting the core, including its boundary; replacing
this with footprint intersection would change selection. Preserve `none`,
`large_only`, and `all`, including the current large-building threshold and
behavior when no core is configured.

Keep the existing global 850,000-building cap across the complete filtered
dataset, not per batch. Characterize ordering and equal-area cutoff ties before
choosing a disk-backed equivalent. Preserve reproducible legacy results; record
any unavoidable tie-order difference explicitly rather than claiming exact
selection parity. Do not remove duplicate source records without a separate
data-quality decision.

The existing JSON and binary exporters have different polygon and grid rules.
Capture both as separate contracts. In particular, preserve the binary format's
largest-component treatment of MultiPolygons, holes, coordinate rounding,
foundation depths, cell coverage, header layout, and reference offsets. This
repair does not redefine the game's building format.

## Implementation approach

Use repository-owned building stages and importable MapGen adapters. Do not add
another source-text replacement to the installed depot package for this repair.
Existing unrelated depot patches remain in place. Check compatibility explicitly
and report an unsupported backend before starting an expensive build.

The intended flow is:

```text
Pinned Overture release + existing BBOX predicate
  -> DuckDB COPY to staged raw WKB Parquet
  -> bounded Arrow batches + Shapely conversion
  -> area/core/validity filters -> SQLite staging
  -> global selection with disk-backed ordering
  -> streamed cleaned/tile/foundation GeoJSON
  -> disk-backed JSON and binary spatial indexes
  -> existing tile tools -> validated current-run artifacts
```

DuckDB supports copying query results directly to Parquet, avoiding a full
Python DataFrame at the acquisition boundary. Use the installed release's
[COPY API](https://duckdb.org/docs/1.5/sql/statements/copy) and
[batch-reading API](https://duckdb.org/docs/1.5/clients/python/reference/).
An available `to_arrow_reader()` method was also verified in the installed
1.5.5 runtime. Do not assume APIs from the latest documentation are available.

### Files and responsibilities

| File | Planned responsibility |
| --- | --- |
| `sb_mexico/building_pipeline.py` (new) | Resource policy, acquisition, cache manifests, batch conversion, filtering, global selection, and streamed GeoJSON. |
| `sb_mexico/building_index.py` (new) | Disk-backed spatial-index assembly, compatible JSON/binary serialization, and streaming gzip. |
| `sb_mexico/cartography_backend.py` (new) | Module-level MapGen adapters combining the building stages with existing planned-label behavior. |
| `sb_mexico/cartography_runner.py` | WSL integration, build identity, resource reporting, artifact ownership, and failure reporting. |
| `sb_mexico/cartography.py` | Shared adapter integration for native Linux, cache handling, and Windows-side failure reporting. |
| `sb_mexico/toponymy_tiles.py` | Retain label behavior; adjust composition only if needed by the shared adapter. |
| `sb_mexico/build_delivery.py` | Extend existing build provenance only where needed to identify the new building outputs. |
| `requirements.txt` | Declare directly used building dependencies and tested minimums; avoid unrelated upgrades. |
| `tests/test_building_pipeline.py` (new) | Acquisition, filtering, cap, cache, resource-policy, and failure behavior. |
| `tests/test_building_index.py` (new) | Independent decoding and legacy-output comparisons. |
| `tests/test_cartography_backend.py` (new) | Adapter composition, multiprocessing, current-run artifact transfer, and failure propagation. |
| Existing cartography/LOD/cache/delivery tests | Focused regressions for changed integration boundaries. |
| `docs/demand-wizard.md` | Explain build progress, cache reuse, and actionable memory/disk failures after implementation. |

## Execution phases and completion gates

### 1. Freeze representative behavior

Record the effective CDMX build snapshot, including any unsaved Wizard settings,
PBF identity, runtime versions, installed depot source hash, and memory/swap
limits. Preserve unrelated local edits. Use isolated output directories; do not
rerun the failing full-city legacy acquisition merely to reproduce an OOM.

Create small local WKB/Parquet fixtures covering polygons, holes, MultiPolygons,
null/invalid geometry, core-boundary centroids, all peripheral modes, area
threshold boundaries, and height values accepted by the current implementation.
Run the installed legacy cleanup/exporters on these small fixtures to capture
their actual outputs. Use an injectable small cap to exercise global selection
and ties without generating 850,000 fixture geometries.

Gate: expected retained records, normalized GeoJSON, decoded JSON/binary indexes,
and documented legacy edge cases exist. All further phases compare against
these contracts rather than source-string assertions alone.

### 2. Bound acquisition and establish a reusable raw cache

Resolve the Overture release once per acquisition and record its immutable tag.
Select only the fields currently required: source ID, WKB geometry, name, height,
and any explicit ordering/provenance fields. Keep the current spatial predicate.
Use DuckDB `COPY (SELECT ...) TO ... (FORMAT PARQUET)` in WSL's ext4 filesystem.
Do not call `to_df()`, `fetchall()`, or concatenate result batches.

Start validation with two DuckDB threads and a 2 GiB DuckDB memory budget on the
observed WSL allocation. These are initial tuning values, not proven limits.
Derive a lower policy for smaller effective limits, including cgroup constraints;
report insufficient headroom before acquisition. Configure a run-owned spill
directory and explicit spill-space limits, and report free disk space. DuckDB's
limit does not bound every allocation, so process measurements remain necessary.
[DuckDB OOM guidance](https://duckdb.org/docs/1.5/guides/performance/oom)
supports reducing threads and reserving memory outside its buffer manager.

Write `raw.parquet.partial`, validate its schema, footer, row count, size, and
checksum, then atomically publish it with a completed raw-cache manifest. Close
DuckDB before starting geometry conversion. Failed downloads leave no completed
cache entry. A retry can reuse a complete raw stage; interrupted remote scans
restart rather than pretending arbitrary scan offsets are resumable.

Gate: a local multi-batch acquisition fixture completes without materializing
the complete query in Python; network/disk failures preserve previous complete
caches and never mark partial output as reusable.

### 3. Convert and filter without accumulating geometry

Read cached Parquet in Arrow batches, initially at most 25,000 rows. Split large
batches by WKB bytes before Shapely conversion; start with a 32 MiB conversion
sub-batch target and measure native geometry expansion. A row count alone is
not a memory guarantee. Report an oversized single feature explicitly if it
cannot fit the configured budget; do not silently discard it.

Apply validity, emptiness, area, and core/LOD predicates per batch with the
captured semantics. Persist accepted WKB, source identity/order, area, and
properties into a run-owned SQLite database with bounded page cache and
disk-backed temporary storage. Release each Arrow/Shapely batch before reading
the next. Do not keep a list of previous batches or a growing DataFrame.

Apply the existing global cap using a disk-backed ordering pass. Stream selected
records into valid cleaned and tile FeatureCollections, preserving each output's
configured simplification. Generate foundation GeoJSON incrementally as well;
avoid calling the installed whole-file foundation and default-height loaders.
Keep the existing Tippecanoe layer names and options. Count rejections by reason
and report raw, retained-before-cap, capped, and exported totals separately.

Gate: retained source records and geometry semantics match the phase 1 fixtures
across batch sizes and LOD modes. Cap behavior is global. Empty input produces
an explicit tested outcome with no stale index or tile reuse; use a clear build
failure until a compatible empty-index contract has been verified.

### 4. Remove full-file allocations from indexes and compression

Build index records and geometry sections in disk-backed spools. Use multiple
bounded passes to calculate global bounds, grid dimensions, section lengths,
and cell references. Store spatial references in SQLite and iterate in the
required row/column/building order; do not hold the full cell map in a dict.

Write the existing binary header and aligned sections by seeking or copying
bounded spool blocks into a staged file. Preserve all terminal offsets and
32-bit field limits; fail clearly on overflow. Stream JSON arrays from cursors.
Compress from disk through `gzip.GzipFile` or an equivalent streaming writer,
without a complete bytearray, `bytes(buffer)`, or `gzip.compress(full_output)`.

Keep both legacy JSON outputs and the existing binary outputs unless inspection
establishes that a specific artifact is unused and its removal is separately
within scope. Write outputs atomically as a group using a completion manifest.
Validate generated indexes with a reader independent of the new writer.

Gate: decoded records, bounds, cells, references, geometry, foundations, and
header values match the small legacy fixtures. Gzip round-trips and truncated
outputs are detected. Memory measurements include native allocations and child
processes, not only Python's allocation tracker.

### 5. Integrate adapters, cache identity, and failure reporting

Compose building and planned-label stages in module-level importable classes.
The current label factory can return a `functools.partial`, and depot submits
bound methods to multiprocessing workers; a dynamically nested subclass is not
sufficient. Test actual worker serialization with the intended start methods.
Use the same factory in the WSL runner and native Linux entry point, preserving
`labels_only`, `skip_map`, and planned-label behavior.

Place reusable raw caches under a versioned city-specific cache root. Raw cache
identity includes city/source identity, exact BBOX, pinned release, query/schema
version, and checksum. Derived cache identity additionally includes core-geometry
hash, LOD mode, thresholds, cap/tie policy, simplification, foundation options,
and writer version. Use manifest identity rather than the current BBOX-only
pickle check. Existing pickles remain untouched and are not loaded wholesale
by the bounded path; refetch or rebuild explicitly when no compatible cache
exists. A request to refresh buildings must still be honored.

Build into a run-specific ext4 output directory, separate from reusable caches.
Transfer artifacts from that run's exact MapGen output path. Replace recursive
first-match discovery for these artifacts with an explicit inventory so that
older roads, tiles, or indexes cannot satisfy a failed current build. Preserve
the existing Wizard rule that failed builds do not become downloadable success.
Use a per-city/cache writer lock and atomic manifests to prevent concurrent
builds from mixing outputs.

Log the active stage, release, cache status, processed counts, resource budgets,
peak process-tree RSS, spill/disk use, and final artifact hashes. On abrupt
termination, report the last completed stage. Attribute a kill to OOM only when
kernel evidence matches the run's time/PID; exit code 9 or 137 alone is not proof.
Cancellation must remain distinguishable from OOM and preserve completed caches.

Gate: WSL/native fixture builds and planned-label worker tests pass; interrupted
or concurrent builds cannot publish mixed artifacts. An explicit bounded/legacy
backend selector supports isolated comparison and rollback. Do not automatically
fall back to the memory-heavy loader after a bounded-path failure.

### 6. Validate on CDMX and roll out

Run the new focused tests and affected existing suites, including
`test_cartography_resilience.py`, `test_urban_lod.py`, `test_ocean_cache.py`,
`test_wizard_delivery.py`, and relevant planned-label tests. Update cache tests
to assert actual reuse/invalidation behavior, not merely marker strings. Run
the repository's encoding checks and `git diff --check` for edited files.

Use a generated local Parquet workload at two sizes with the same geometry
complexity distribution. Run each in a fresh subprocess. A tenfold row-count
increase should add no more than 512 MiB to peak RSS at a fixed batch/resource
budget; record any native allocator or cache effect rather than hiding it.
Include a separate high-vertex workload to expose geometry-expansion costs.

Then run an isolated real CDMX cartography build from the effective configuration
with a cold cache, followed by a warm-cache build. Keep the existing WSL RAM and
swap allocation. Record other active workload; do not stop unrelated services.
Measure the building stages and the whole cartography process tree separately,
including Tippecanoe and depot multiprocessing overlap.

Completion criteria:

- CDMX passes acquisition, conversion, cleanup, indexes, building/foundation
  tiles, and final PMTiles assembly without an OOM kill.
- Building-related stages target at most 5 GiB aggregate process-tree RSS on
  the observed 7.4 GiB WSL allocation, with at least 1.5 GiB effective headroom
  after accounting for other active workload. If that headroom is unavailable,
  lower concurrency/budgets or record a resource failure; do not claim a pass
  based on a larger machine or increased swap.
- Accepted source records, cap exclusions, output counts, layers, bounds, and
  independently decoded indexes agree with the recorded contracts. A complete
  legacy CDMX build is not required as a comparison when it cannot fit memory.
- Warm-cache execution avoids a repeated remote download. Changing the BBOX,
  release, core, LOD, or geometry settings invalidates the appropriate stage.
- Injected interruption, corrupt cache, disk-full, and unsupported-backend cases
  fail clearly without promoting partial files or a previous package.
- A small second-city regression demonstrates that the shared backend preserves
  behavior outside CDMX. Keep its artifacts and caches separate.

After those gates pass, enable the bounded backend by default in the repository,
retain explicit rollback for the initial rollout, and document measured time,
memory, disk cost, runtime versions, and remaining limits in a results document.
Validate a complete Wizard delivery if packaging is claimed. Import, inspect,
save, and reload in the target game version before calling the map playable;
otherwise report game validation as pending.

## Progress checklist

- [x] Baseline fixtures and compatibility contracts captured.
- [x] Disk-backed acquisition and completed raw-cache reuse verified.
- [x] Bounded conversion, filtering, global cap, and foundation outputs verified.
- [x] JSON/binary index and compression parity verified.
- [x] Shared adapters, worker serialization, manifests, and failures verified.
- [x] CDMX cartography validated in stages; memory and warm raw-cache checks passed.
  A second full warm rebuild was not executed; see the results document.
- [ ] Full second-city cartography, Wizard ZIP delivery, and game validation remain pending.
  The local Merida geometry/index fixture passed.
- [x] Results and operational documentation written; default activated.

No implementation or build-validation gate is complete merely because this
plan has been written.
