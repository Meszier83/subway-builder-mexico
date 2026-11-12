# Map Analyzer: six-feature implementation plan

Prepared 2026-10-27 against the current local analyzer.

Implementation status, 2026-10-27: the six user-facing feature areas are present, but the complete plan has **not** passed every gate below. Shared grid/heat geometry, selection, trip aggregation, paired views, balance shading, comparison, and exports have focused tests or browser checks. The planned separate map-view/export modules and single explicit state object were not introduced: the current implementation extends `app.js` and retains DOM-backed settings. Browser checks have not covered every listed scenario, especially a narrow viewport, exhaustive pan/zoom synchronization, large imports, all PNG variants after the final edits, and all export edge cases. Treat the unchecked gates as outstanding work, not as a certification.

Evidence gathered: existing importer suite passed (12 tests); Node analysis tests passed including weighted trip endpoints, matching grids, missing/empty trips, renamed IDs, and redistribution. Browser checks used synthetic 150-commuter A/B fixtures and Mérida (3,078 points, 13,283 groups, 664,150 commuters). The synthetic selected origin produced 70/30 destination weights; A/B redistribution showed +10 and -10 with zero net. A comparison CSV, selected-area JSON, paired and selected-area demand-only PNGs, a difference PNG, and a PNG with street tiles were inspected. A failed comparison retained the previous B. A Mérida People/Jobs view showed the same 44,371/km² shared maximum on both panes. Node measured the Mérida grid at about 7 ms and heat calculation at about 73 ms on this machine; these are not browser responsiveness or large-file guarantees.

## Objective and scope

Deliver this workflow: find concentrations, inspect an area, follow its modeled trips, compare an export revision, and save the evidence.

Implement these six features in order:

1. Area inspector with explicit cell, circle, and polygon selection.
2. Selected-area origin/destination heatmaps and ranked results.
3. Synchronized People/Jobs maps with independent or shared scales.
4. Balance colors that also communicate activity volume.
5. Two-export comparison of counts and directed trip connections.
6. Annotated PNG and supporting data exports.

Do not add ridership simulation, route recommendations, station coverage, financial calculations, additional correlation statistics, geocoding, PMTiles support, persistent projects, or a new framework. Preserve current correlation, concentration, grid CSV, and strongest-link functionality. No generator, Wizard, city-data, mod, game-file, or active-deployment changes are required.

This is a standalone vanilla JavaScript/Leaflet app served by a Python standard-library server. Keep that deployment model. Use existing local dependencies; do not add a build system or remote scripts. Feature work must not require rewriting the app from scratch.

## How to execute this plan

- Read applicable repository instructions and inspect current diffs before editing. Many analyzer files are currently untracked; they are existing user work, not disposable scaffolding. Other untracked mods/tests/tools are unrelated.
- Follow phases sequentially. Each phase has a completion gate. Fix failures before building dependent features; do not repeatedly run unrelated repository suites.
- If asked to implement the whole plan, continue through all phases without requesting approval at each gate. If asked for one phase, stop after that phase and its verification.
- Keep a short implementation status in this document, including completed gates and actual limitations. Do not mark a phase complete on code inspection alone when it requires browser verification.
- Preserve UTF-8 without BOM and LF for new/edited files. Do not commit, push, publish, or synchronize another checkout unless requested.

## Current files and constraints

| File | Responsibility and intended change |
| --- | --- |
| `tools/map_analyzer.py` | Read-only ZIP/JSON importer and local server. Preserve validation and API. Add explicit static routes only if new JS files require them. |
| `tools/templates/map_analyzer.html` | Controls, map containers, inspector, comparison import, export actions. |
| `tools/static/map-analyzer/app.js` | State, imports, events, map orchestration. Refactor incrementally. |
| `tools/static/map-analyzer/core.js` | Pure analysis helpers, usable through `require()` in Node and `MapAnalysis` in browser. Extend here. |
| `tools/static/map-analyzer/style.css` | Layout and visual states. Keep the current visual identity. |
| `tests/map_analyzer.cjs` | Extend pure-math and regression tests. |
| `tests/test_map_analyzer.py` | Existing importer tests; extend only if importer/server behavior changes. |
| `docs/map-analyzer.md` | Update user documentation after features work. |

Add at most two focused browser modules initially: `map-view.js` for a reusable Leaflet view and `export.js` for export composition. Keep global dependency loading explicit in HTML, with `core.js` before consumers and `app.js` last. Add those exact paths to the server's static whitelist. Do not broaden the server into an arbitrary filesystem server.

Current defaults to preserve: 500 m cells, 600 m smoothing, contrast value 1.8, faded basemap, orange People and purple Jobs. Rename the visible strength label to “Visual contrast”; explain that counts do not change.

Current implementation hazards to handle:

- A single global `map`, `layer`, and legend currently prevent independent rendering of two panes.
- `grid()` derives its reference latitude independently for each input. Calling it separately for A and B does not guarantee aligned cells.
- `heatSurface()` derives raster extent and resolution from positive weights. Independent surfaces can have different sampling geometry even for related inputs.
- `rebuild()` clears selection. Circle/polygon selections should survive changes to cell size.
- Balance currently overwrites the selected display mode. Remember separate People/Jobs display preferences.
- Preserve SVG flow/selection renderers, `interactive: false` for decorative selection shapes, and `bubblingMouseEvents: false` for clickable cells. A prior full-map canvas overlay intercepted grid clicks.
- The browser currently commits an import before rendering finishes. Build candidate derived state first; a failed import/preparation must leave the previous usable view intact.

## Data and calculation contracts

### Imported data is immutable

The existing normalized response is:

```text
Dataset {
  name: string,
  points: [{id: string, location: [longitude, latitude], residents: integer, jobs: integer}],
  pops: [{residenceId: string, jobId: string, size: positive integer}],
  tripsAvailable: boolean,
  warnings: string[]
}
```

`residents` means supplied resident commuters; `jobs` means supplied job arrivals. These are not census population or observed employment. Trip weights always use `pops.size`, never number of groups. Point counts and trip-derived counts remain separate when they disagree. Do not repair, rescale, deduplicate, or mutate imported data.

`tripsAvailable: false` means unavailable; an explicitly empty `pops` array means available with zero trips. Preserve that distinction everywhere, including comparisons and exports. Zero denominators produce `null`/“N/A”, not NaN, Infinity, or fabricated zero percentages.

### Common spatial geometry

Add an explicit reusable grid specification:

```text
GridSpec {sizeM, referenceLat, originXM: 0, originYM: 0}
createGridSpec(allPoints, sizeM) -> GridSpec
grid(points, sizeM, optionalSpec) -> existing result plus spec
```

Retain the current equirectangular projection and floor-based cell indexing. Derive `referenceLat` from the midpoint of minimum/maximum latitude across all relevant points, including zero-weight points. Preserve the existing two-argument behavior. In comparison mode, derive one spec from the union of A and B and apply it to both. Never join separately generated cells by approximate center proximity.

Similarly separate heat geometry from weights:

```text
createHeatSpec(allPoints, radiusM) -> projection, cellM, raster dimensions, padding, bounds
heatSurface(points, field, radiusM, optionalSpec) -> weighted surface on that geometry
```

Keep the existing Gaussian behavior, padded mass conservation, and bounded raster policy. In paired views use one heat spec: all primary points for People/Jobs and selected-trip views; the union of A/B points for version comparison. Empty weights still return usable geometry with zero values. Reuse geometry when switching selection or direction.

Color maps must operate on comparable units:

- Grid: supplied count per same-sized cell.
- Heat: `density = rasterValue / cellM² * 1,000,000`, approximate commuters per km².
- Independent domains: maximum for each displayed layer over the entire dataset.
- Shared domain: maximum density/count across both displayed layers, not viewport-local maxima.
- Version difference: signed cell counts; symmetric domain `[-maxAbsDelta, +maxAbsDelta]`.

For heat rendering use `t = density / domainMax`, then the existing contrast transform. Never normalize a shared scale by each pane's own peak. If the domain is zero, draw no heat and label “No demand”. Contrast, panning, zooming, and window resizing must not change counts or numeric domains. Use matching palettes in A/B comparisons; preserve orange/purple in People/Jobs comparison, with identical numeric ticks and contrast transfer.

### State and render ownership

Use one explicit application state, not values read back from DOM as the source of truth:

```text
datasets: {primary, comparison?}  // wrappers with revision, source filename, byte size, normalized data
viewMode: 'single' | 'people-jobs' | 'versions'
layer: 'residents' | 'jobs' | 'balance'
displayByLayer: {residents: 'heat'|'grid', jobs: 'heat'|'grid'}
scaleMode: 'independent' | 'shared'
cellSizeM, smoothingM, contrast
selection: null | cell/circle/polygon geometry
selectionRevision
tripView: 'off' | 'links' | 'destinations' | 'origins'
versionDisplay: 'side-by-side' | 'difference'
versionMetric: 'residents' | 'jobs' | 'trips'
```

Each map view owns its Leaflet instance, panes, layers, legend, and event listeners. It receives data/render descriptors and reports actions to the controller; it must not overwrite global selection during a repaint. Derived selection IDs belong to a dataset revision, not the geometry itself.

Cache numeric surfaces by dataset revision(s), geometry spec, field, and selection revision/direction when relevant. Contrast changes recolor a cached surface. Pan/zoom never recalculate heat. Retain only caches for current inputs/settings; do not accumulate every slider position. Use independent import sequence tokens for primary and comparison inputs; discard stale results.

## Phase 0 — establish reusable analysis and rendering foundations

- [ ] Record the existing focused test baseline and inspect the current UI.
- [ ] Add GridSpec/HeatSpec helpers and regression tests for old two-argument grid calls and mass conservation.
- [ ] Extract reusable map-view setup/rendering from `app.js`, preserving current single-map behavior before adding a second map.
- [ ] Introduce explicit state, dataset revisions, reusable point-ID lookup, and atomic import preparation/commit.
- [ ] Centralize palette/domain/legend calculations so screen and export renderers share them.

Completion gate: existing tests pass; single-map import, heat, grid, Balance, scatter selection, circle selection, flows, and CSV still work. Invalid or stale imports do not replace the current view. No changes to input counts.

## Phase 1 — area inspector

UI:

- Add an explicit “Inspect: Cell / Circle / Polygon” control. Default to Circle to retain the heatmap click behavior. A grid-cell click in Cell mode selects that cell; Circle mode must still create a circle when clicking a grid cell.
- Put an inspector in a persistent panel next to/below the map, visible without scrolling the long sidebar. On narrow screens use a collapsible panel. Keep clear-selection and trip actions together.
- Show selected people, jobs, count of demand points, approximate area km², people/km², jobs/km², balance, and each count's share of the whole imported dataset.
- Point membership uses original point coordinates; do not select entire bins because they intersect a shape.

Selection geometry:

- Circle: center `[lng, lat]`, radius from current 400/800/1,200 m choices; use existing haversine membership, boundary included. Area is approximately `πr²`.
- Cell: store clicked cell key and GridSpec; selection is exactly `byPoint.get(id) === key`. Area is approximately `sizeM²`. Cell mode works in heat view by converting the click to a cell, including an empty cell.
- Polygon: one simple ring, no holes/multipolygons in this release. Click vertices; visible Finish, Undo vertex, Cancel controls; Escape cancels. Finish requires at least three distinct vertices and positive area. Reject self-intersections with a visible explanation; do not silently fix them. Use explicit Finish rather than double-click completion.
- Validate polygon edges and compute area using the same local projection as the grid. Point-on-edge counts as inside (use a small documented tolerance, e.g. 0.001 projected metres). Apply a bounding-box prefilter, then point-in-polygon. Repeated first/last vertex is normalized once.
- During drawing, suppress ordinary inspection and flow clicks, but allow intentional map navigation. Cancel restores the previous committed selection. Do not add a drawing dependency.
- Preserve circle/polygon geometry across cell-size changes and mode switches. Clear a selected cell when cell size changes and show a short explanation. Successful primary replacement clears selection; a failed replacement preserves it. In comparison mode apply the same geometric boundary to each dataset separately.

Pure helpers: `selectGeometry(points, geometry, gridSpec)`, `selectionSummary(...)`, polygon validation/area/membership. Keep existing `select()` available for old tests/callers.

Completion gate: edge/vertex inclusion, concave polygon, self-intersection rejection, empty selection, zero denominators, and coincident IDs tested. Browser verifies all three tools, cancel/undo, inspector visibility, and selection preservation rules.

## Phase 2 — selected-area trips

Add “Destinations / Origins / Links / Off” actions to the inspector. Origins and Destinations require a committed selection. Retain current global strongest links in single mode when no selection exists.

Pure helper:

```text
selectedTrips(dataset, selectedIds, direction, bins) -> {
  total, insideSelection, outsideSelection,
  endpointWeights: Map<pointId, count>,
  rankedCells: [{key, center, count, share}]
}
```

- Destinations: include a trip iff its residenceId is selected; accumulate `size` at its jobId.
- Origins: include a trip iff its jobId is selected; accumulate `size` at its residenceId.
- `total` is the sum of qualifying trip sizes, independent of supplied point counts.
- `insideSelection` counts qualifying trips whose other endpoint is also selected; `outsideSelection = total - insideSelection`. This differs from existing same-analysis-cell trips; do not conflate the labels.
- Heat includes ALL qualifying endpoints, including endpoints inside the selection and same-cell trips. Use temporary weights without overwriting `residents` or `jobs`. The total endpoint weight equals `total`.
- Render endpoint heat in place of the normal concentration heat, with an explicit title and its own trip-density legend. Keep the selection outline visible. Returning to Off restores the normal layer.
- The table aggregates endpoints by current grid cell, ranks count descending then cell key ascending, shows count and `count/total`, and paginates 20 rows. Show the number/share outside the current page; do not truncate the heat to those rows.
- Row click highlights/pans to the endpoint cell WITHOUT changing the source selection. A separate “Inspect this area” action may replace selection if explicitly clicked.
- Missing trips: disable these actions with a reason. Available zero qualifying trips: show zero, empty table, no heat, and “No matching trips”.
- Keep selected-trip heat in Single mode only for this release. Entering a paired mode turns it off with visible state; it must not silently replace one of the People/Jobs or A/B layers.

Completion gate: weighted totals, incoming/outgoing direction, internal/crossing split, pagination share, zero/missing trips, and unchanged inputs tested. Browser verifies table clicks preserve the source selection and changing direction updates all labels/data.

## Phase 3 — synchronized People/Jobs comparison

- Add a view selector: Single / People & Jobs / Export comparison. Export comparison prompts for B if missing.
- People & Jobs uses two maps: People left, Jobs right. Both share geometry, zoom, center, smoothing, contrast, cell size, and selection boundary. Both use the same display type in this mode; entering initially uses the primary layer's saved display.
- Default this paired view to Shared scale; allow Independent with clearly labeled independent domains. Preserve existing independent behavior in Single mode. Show legend ticks at 0%, 25%, 50%, 75%, and 100% of each numeric domain.
- Synchronize with a reentrancy guard, equality checks, and `animate: false` for mirrored changes. Avoid a feedback loop. On layout changes call `invalidateSize()` after containers have their final dimensions.
- Fit map uses all primary demand points. A selection in either pane updates one shared selection and both outlines. The inspector shows one dataset's statistics, not doubled totals.
- Under approximately 1,000 px available viewport width, stack panes with usable minimum heights; do not render two unusably narrow maps.
- Returning to Single restores the saved layer/display preferences. Balance remains a Single-mode layer; entering it returns to Single visibly.

Completion gate: shared domains equal, independent domains can differ, and shared density uses physical units. Browser verifies bidirectional pan/zoom, no repeated event loop, resizing, selection consistency, all-zero layers, and unchanged domains after viewport changes.

## Phase 4 — activity-aware balance

For each cell use:

```text
activity = residents + jobs
balance = activity ? (jobs - residents) / activity : null
activityFraction = activityMax ? sqrt(activity / activityMax) : 0
fillOpacity = activity ? 0.20 + 0.73 * activityFraction : 0
```

`activityMax` covers the whole current dataset at the current cell size, not the viewport. Preserve current orange/purple hue mapping for the sign/magnitude of balance, but use a visible medium neutral gray (e.g. RGB 125,132,125) at balance zero so busy balanced cells do not vanish into the background. Leave zero-activity cells unfilled. Maintain thin borders.

Add a two-dimensional legend: columns for residential/balanced/employment and rows for 1%, 10%, and 100% of maximum activity. Include a separate empty swatch. Tooltip/inspector exposes exact resident/job counts, activity, and balance. Explain that activity is the sum of two counts, not a count of unique people.

Completion gate: equal ratios with 20 versus 20,000 activity retain equal balance values but visibly different intensity; high-activity balanced cells remain visible; zero activity has no fabricated balance. Pan/zoom cannot change appearance. Visually inspect city overview and neighborhood zoom before accepting the chosen neutral color.

## Phase 5 — export version comparison

Import and UX:

- Primary input is “Baseline A”; separately import “Comparison B” using the existing `/api/import`. Record filenames and byte sizes; do not infer generation dates or provenance from filesystem timestamps.
- Show A/B filenames and totals. Invalid B leaves A and any previous B usable. Successful primary replacement clears B to avoid retaining an unrelated pair. Removing B returns to Single.
- Prepare both grids on one union GridSpec. Fit uses union bounds. Preserve both input warnings. Different city names are not an error; do not claim automatic map identity detection.
- Support `People`, `Jobs`, and `Trip connections` metrics. For People/Jobs, offer Side by side (heat or grid with a shared scale) and Difference (grid only). Shared is mandatory for A/B side-by-side.
- Difference means **B minus A** everywhere. Use blue for decreases, red for increases, and no fill for zero changes; label this palette independently from residential/employment balance. Selected cells show A, B, absolute delta, and percentage change.
- Percent change: `100 * (B-A) / A` when A>0; A=0/B>0 is “New”; A=B=0 is “No change” with percentage N/A. Exports keep the numeric percentage null and provide an explicit status field.
- Join the union of cell keys, supplying zero for absent cells. Compute removed cells as negative changes. Headline total delta equals the sum of cell deltas. Keep raw changes, not normalized shares, as the default and only version-difference measure in this scope.

Trip connection comparison:

- Aggregate each dataset's trips independently to directed `(originCellKey, destinationCellKey)` on the SAME grid; raw point IDs may differ between exports.
- Include same-cell pairs in calculations and table. They cannot be drawn as ordinary inter-cell lines; display them as cell markers when highlighted and report them separately.
- Join union of directed pairs with absent pair weight zero; compute A, B, delta, percentage/status. Rank by absolute delta descending, then origin/destination keys for deterministic ties.
- Show a paginated changed-connection table (20 rows), with increased/decreased/unchanged trip totals and a clearly labeled net change. “Increased” is sum of positive pair deltas; “decreased” is the magnitude of negative pair deltas; unchanged is sum of A weights for pairs with equal A/B. Net equals total B minus total A. Do not describe these as identified people switching destinations.
- Clicking a row highlights its directed connection and endpoint cells. Draw at most the 30 largest nonzero inter-cell changes at once, with direction indicated and omitted absolute-change share reported. Denominator for that share is the sum of absolute inter-cell deltas, not the possibly zero net delta.
- If either dataset lacks trips, disable connection comparison and explain why; point count comparisons continue. Explicitly empty trips compare as zero.
- With a committed area selection, table filtering may be “All / Leaving selection / Entering selection”; default All. For Leaving/Entering, filter original endpoint IDs by the SAME selection geometry in each dataset BEFORE cell aggregation. Label the table and totals as filtered. Never filter by cell-center membership.

Completion gate: A==B has zero changes; renamed/reordered IDs and split cohorts with identical spatial mass have zero changes; new/removed cells are retained; A/B reversal negates absolute deltas; union projection works when extents differ. Test directed pairs, internal pairs, equal-total but redistributed trips, missing versus empty trips, zero baselines, and point/trip inconsistencies without repair.

## Phase 6 — annotated PNG and supporting data

Use one frozen export snapshot of the current derived state. Disable duplicate export clicks until complete. Changes to live controls after export starts must not mix labels from one state with pixels from another.

PNG requirements:

- Export current view, including both panes when paired, current viewport, heat/grid, selection geometry, visible links/highlight, and numeric legend(s).
- Include dataset names/source filenames, metric, B-minus-A convention if applicable, cell size, smoothing where relevant, contrast, scale mode, approximate units, selected-area summary, timestamp, and basemap attribution when used.
- Create the image through an explicit canvas compositor using the same render descriptors, color functions, domains, projected coordinates, and heat rasters as the screen. Do not use a screenshot of the whole application or rebuild the math independently inside the exporter.
- Match viewport aspect ratio; cap output width at 2,400 px and scale both dimensions consistently. Reserve a separate footer/header for metadata so it cannot cover mapped features. Use `canvas.toBlob()` and release object URLs.
- Reproduce grid clipping, heat geographic placement, flow widths/direction, selection outlines, and legend colors. For polygons/circles project coordinates with the map view's current projection. Draw existing selected/scatter highlight if it is visible on the map.
- Basemap inclusion uses only currently loaded visible tiles. Set up cross-origin-compatible tile loading if supported and composite tiles using public projection/tile events with stored tile coordinates; do not prefetch extra tiles or add a proxy. Apply the same background fading as the screen.
- If tiles are incomplete or cannot be exported because of CORS, offer an explicit “Export without street background” action. Produce a complete demand-only image on a fresh canvas with a visible “Street background omitted” note; never silently export a partial street map or claim tiles were included. Do not weaken browser security. Demand-only PNG must work offline.

Supporting data actions (separate downloads, no ZIP dependency):

- Preserve the existing single-grid CSV columns/order; append new fields only if needed. Add dedicated comparison CSV rather than overloading that format.
- Selection/analysis JSON: schemaVersion=1, source filenames/sizes, modes/settings, spatial specs, view bounds, selection geometry, totals/warnings, and the computed inspector values. Serialize Maps/Sets explicitly; exclude DOM objects, functions, and full raw inputs. This is an analysis record, not a promised reloadable project.
- Selected-trip CSV: all ranked endpoint cells, counts, shares, direction, and total denominator; not just the visible page.
- Comparison-cell CSV: all union cells with A/B counts, deltas, nullable percentages, and status fields for both People and Jobs.
- Comparison-trip CSV: all pairs under the active filter, including unchanged/internal pairs, with directed keys, A/B weights, deltas, percentages/status, and coordinates.
- Use a shared CSV serializer with correct quoting. Protect user-controlled textual cells from spreadsheet formula interpretation; do not alter numeric negative deltas. Sanitize download filenames, but retain original names as escaped metadata.

Completion gate: inspect actual downloaded PNGs at readable size, in single, paired, difference, and selected-trip modes. Verify image legends match screen domains and exported numeric data. Test long/non-ASCII names, no-selection, no trips, all-zero layers, background omission, and comparison with zero net but nonzero spatial changes. CSV/JSON totals must reconcile to the displayed analysis.

## Required deterministic fixtures

Create small synthetic fixtures in tests, independent of real city files. Use separate locations far enough apart to occupy distinct 500 m cells unless a test explicitly requires coincidence or shared cells.

Base fixture:

| Point | Residents | Jobs |
| --- | ---: | ---: |
| h1 | 100 | 0 |
| h2 | 50 | 0 |
| w1 | 0 | 90 |
| w2 | 0 | 60 |

Trips: h1→w1=70; h1→w2=30; h2→w1=20; h2→w2=30. Totals are 150 in all three representations.

Expected checks:

- Select h1: outgoing total=100, endpoint weights w1=70/w2=30, shares 70%/30%.
- Select w1: incoming total=90, weights h1=70/h2=20, shares 7/9 and 2/9 before presentation rounding.
- Select h1+w1: outgoing total=100, internal=70, crossing outward=30; incoming total=90, internal=70, crossing inward=20.
- B increases h1→w2 by 10 and updates h1.residents to 110 and w2.jobs to 70: B total=160, people delta=10, jobs delta=10, trip delta=10. Exactly one directed pair changes by +10.
- Another B redistributes 10 from h1→w1 to h1→w2 and updates job counts to 80/70: people delta=0, total jobs delta=0, total trips delta=0, two changed pairs (-10/+10), absolute trip change=20.
- Renaming all IDs with corresponding references, reordering arrays, and splitting each trip into positive subgroups must not change spatial comparison results.
- Separate fixtures: zero supplied counts, missing trips, empty trips, supplied-count/trip mismatch, coincident IDs, a trip internal to one cell but outside the selected polygon, an endpoint exactly on an edge, and a more distant point expanding B's latitude bounds.

Use floating-point tolerances for raster/density calculations; exact integer assertions for counts and trip weights. Confirm analysis helpers never mutate fixtures. Avoid golden tests tied to incidental DOM structure or arbitrary implementation details.

## Verification and delivery

Focused commands from repository root:

```powershell
node tests/map_analyzer.cjs
python -m unittest discover -s tests -p test_map_analyzer.py
node --check tools/static/map-analyzer/core.js
node --check tools/static/map-analyzer/app.js
# Also check map-view.js/export.js if created.
git diff --check
```

`git diff --check` does not cover untracked files; check new files' encoding/whitespace explicitly. Run tests after each meaningful phase, not after every cosmetic edit. No full generator build is needed because the pipeline is outside scope.

Browser verification:

- Run `python tools/map_analyzer.py --port 5053 --no-browser` on an unused test port. Keep the user's existing server untouched; stop your own server when finished. If loopback is sandbox-blocked, use the tool's normal approval mechanism rather than modifying networking settings.
- Use the approved browser tools available in the session. Do not claim browser verification based on static tests.
- Test synthetic fixtures first, then a realistic city export. The local `.cache/analyzer-checks/merida-check.zip` exists at planning time but is an optional ignored artifact; locate a current map if it is unavailable. Never make tests depend on that cache.
- Record actual import totals and compare them before/after actions. Historical screenshot totals are not a substitute for inspecting the file used in this run.
- Verify 1,280×720 and a narrow viewport, inspector reachability, both directions of synchronized navigation, selection behavior, failed/stale imports, no-trip states, exported files, and no browser errors.
- Time import/preparation, selection/direction changes, contrast recoloring, and switching paired modes on the actual machine and dataset. Panning must not recalculate heat. If derived work repeatedly blocks interaction noticeably (roughly >200 ms), batch/defer it or move the expensive pure computation to a local worker with stale-result protection before calling the UI responsive. Do not claim support for 512 MiB data merely because the importer accepts that size.

Update user docs with the implemented controls, units, denominators, comparison semantics, and export limitations. Keep the app's in-product explanations short and put implementation details in docs.

Final implementation report must state: features delivered, tests actually run, datasets and totals used for browser checks, representative screenshots/export examples, remaining limitations, and any incomplete phase. No claim of game simulation accuracy or predicted transit usage.

## Completion checklist

- [ ] Phase 0: current features preserved; shared geometry and view ownership verified.
- [ ] Phase 1: cell/circle/polygon inspector verified.
- [ ] Phase 2: weighted selected-area trip heatmaps and ranked table verified.
- [ ] Phase 3: synchronized People/Jobs maps and numeric scales verified.
- [ ] Phase 4: balance plus activity visually and numerically verified.
- [ ] Phase 5: aligned A/B counts and directed connection changes verified.
- [ ] Phase 6: actual PNG/CSV/JSON outputs inspected and reconciled.
- [ ] Documentation updated; transient server stopped; unrelated work preserved.

## Suggested handoff prompt

> Implement `docs/map-analyzer-implementation-plan.md` in the current checkout. Read repository instructions and preserve existing local work. Complete phases 0–6 sequentially with their acceptance checks, updating the checklist with actual evidence. Keep scope limited to the standalone map analyzer; do not change the generator, Wizard, city inputs, mods, active deployment, or install a new framework. Continue through the complete plan without asking for confirmation between phases. Report any genuine blockers and do not mark unverified work complete. Do not commit or push.
