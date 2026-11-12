# Map Analyzer: area-to-area demand implementation plan

Prepared 2026-10-27. Status: initial implementation present. Automated counting/import regression checks pass; browser acceptance, real-map reconciliation, responsive layout, and downloaded export inspection remain pending.

## Product decision

Add one mode that answers: **How many imported commuters live in Area A and work in Area B, and how significant is that connection for each area?**

The user explicitly rejected building another simulation engine and questioned the usefulness of unvalidated line scores. This feature describes supplied origin-destination records. It must not propose lines, infer transit ridership, rank routes, or claim that a large flow warrants construction.

Deliver only:

1. Two independently editable circle/polygon selections, called **Area A** and **Area B**.
2. A-to-B and B-to-A weighted home-to-work counts and two percentages for each direction.
3. Highlighted matching home and workplace points for the selected direction.
4. Explicit missing-data, empty-result, mismatch, and shared-point handling.
5. Compatibility with existing modes and exports so they cannot show stale or misleading information.

Do not add line/station sketching, simulations, routing, walking catchments, recommendations, geographic clustering, saved area libraries, additional statistics, new dependencies, or another project. Do not resume the unfinished refactor from `map-analyzer-implementation-plan.md` as a prerequisite. Use a small explicit state object for this feature; broad cleanup is outside scope.

## Current integration points

Inspect these again before implementation; the existing working tree contains substantial untracked work that must be preserved.

| File | Relevant current code | Change |
| --- | --- | --- |
| `tools/static/map-analyzer/core.js` | `selectGeometry`, `polygonInfo`, `selectedTrips`, UMD API export | Add one pure area-pair aggregation helper. Reuse geometry membership. |
| `tools/static/map-analyzer/app.js` | `selectionGeometry`, `polygonDraft`, `selectArea`, `selectPolygon`, `handleMapClick`, `clearSelection`, radius handler | Add independent pair state and route selection events to the active target. |
| Same `app.js` | `setViewMode`, `rebuild`, `importMap`, selected-trip/flow/scatter handlers | Preserve existing behavior and prevent competing selection modes. |
| Same `app.js` | `selectionLayer`, `endpointLayer`, SVG renderers, export handlers | Dedicated pair outlines/highlights; correctly labeled snapshots. |
| `tools/templates/map_analyzer.html` | Collapsible `#inspector` and its tool/trip controls | Add the new analysis-mode controls and result panel. |
| `tools/static/map-analyzer/style.css` | Inspector and responsive rules | Compact readable result cards and distinguishable selection outlines. |
| `tests/map_analyzer.cjs` | Existing pure analysis tests | Add deterministic pair-count, denominator, overlap, and invariance tests. |
| `docs/map-analyzer.md` | Area/trip descriptions | Document the mode and its meaning. |

`tools/map_analyzer.py`, map ZIP contents, generator, Wizard, city inputs, mods, game installation, and deployment copies do not need changes. Preserve importer validation and read-only behavior.

## Counting contract: decide this before writing UI

### Membership and direction

`idsA` and `idsB` are sets of original demand-point IDs obtained by applying the two committed geometries to the **same primary dataset**. Reuse `selectGeometry(points, geometry, fixedSpec)`; circles use the existing haversine membership and polygons use the existing boundary-inclusive logic. Coincident coordinates do not merge different IDs.

For a trip group `p`, the weight is `p.size`, not one group and not a supplied point's residents/jobs count.

```text
countAB = sum(size where residenceId in idsA AND jobId in idsB)
countBA = sum(size where residenceId in idsB AND jobId in idsA)

outA = sum(size where residenceId in idsA)  // all destinations, including A itself
inA  = sum(size where jobId in idsA)        // all origins, including A itself
outB = sum(size where residenceId in idsB)
inB  = sum(size where jobId in idsB)

shareOfAOrigins = countAB / outA
shareOfBArrivals = countAB / inB
shareOfBOrigins = countBA / outB
shareOfAArrivals = countBA / inA
```

Every zero denominator yields `null`, displayed as **N/A**. A zero numerator with a positive denominator yields **0.0%**. Keep full-precision fractions internally and round only for presentation.

These denominators are trip-derived even when supplied point counts disagree. Never substitute `sum(point.residents)` or `sum(point.jobs)` as the percentage denominator. Keep existing mismatch warnings visible; do not correct the inputs. Use UI wording that states this basis.

**B-to-A means different home-to-work records. It is not an inferred evening return journey.** Do not duplicate trips or calculate a daily trip multiplier.

### Overlap policy: include membership honestly

Allow overlapping and identical selections. Do not trim, subtract, assign shared points arbitrarily, or refuse to analyze them.

- Compute `sharedPointCount = |idsA intersect idsB|`.
- Compute `sharedDirectionalWeight = sum(size where residenceId AND jobId are both in idsA intersect idsB)`. These are exactly the records included in both directional counts.
- Count each record once within a direction. It may legitimately satisfy both directional predicates.
- Show the shared-point count. If positive, display: **“Areas share N demand points. Shared points are included in both areas; the two directions may include some of the same commuters.”** If `sharedDirectionalWeight > 0`, also show its weighted count.
- Do not display `countAB + countBA` as a unique or two-way total. No combined total is needed in this release.
- If no points are shared, say **“No shared demand points”**, not “Areas do not overlap.” Shapes can overlap geographically where no demand points exist. The visible outlines identify that geographic relationship; no polygon-intersection engine or overlapping-area calculation is required.
- Identical selections therefore report the same internal home-to-work count in both cards. Explicitly explain the shared records instead of interpreting this as exchange between separate districts.

### Missing and empty data

- Before both selections exist: show “Select both areas”; do not present zero as an answer.
- `tripsAvailable === false`: show “Trip data unavailable in this map.” Counts and shares are unavailable, not zero; selections can still be inspected.
- `tripsAvailable === true` and `pops.length === 0`: valid zero counts, N/A shares.
- Empty membership in either area: valid zero connections once both geometries exist, with denominator-dependent shares. Explain that an area contains no demand points.
- Both nonempty areas but no matching records: “No matching home-to-work commuters in the imported data.” Do not say the areas have no actual travel demand.

## Pure helper API and invariants

Add/export `areaPairDemand(dataset, idsA, idsB)` from `core.js`. It must not depend on Leaflet, the DOM, heatmaps, grid bins, or viewport state.

Suggested result shape:

```text
{
  available: boolean,
  sharedPointCount: integer,
  sharedDirectionalWeight: integer | null,
  totals: {outA, inA, outB, inB},          // integer values, or null if unavailable
  ab: {
    count, originShare, destinationShare,
    originWeights: Map<pointId, integer>,
    destinationWeights: Map<pointId, integer>
  },
  ba: {same fields}
}
```

For unavailable trips, directional counts/shares and totals are null, weight maps are empty. Membership overlap can still be known.

Implementation: build the shared-ID set, then make a single pass over `dataset.pops`, testing all four membership booleans. Accumulate the four marginal totals and two directed results independently. Do not use `else if` between AB and BA: overlapping areas can satisfy both.

For each matched direction accumulate size separately at the residence ID and job ID. For either direction with available data:

```text
sum(originWeights) == count == sum(destinationWeights)
count <= relevant origin total
count <= relevant destination total
0 <= each non-null share <= 1
```

Do not add the origin and destination weight sums and call that commuters; they are two representations of the same records. Do not mutate imported objects or selection sets. Complexity should be O(number of trips + number of selected IDs), without constructing an all-points-by-all-points matrix.

## UI and interaction decisions

Add an **Analysis: Single area / Area to area** selector inside the existing inspector. Keep this separate from the existing **View: Single map / People and jobs / Compare map exports** selector.

The new mode operates only on **Single map and its primary dataset** in this release. “Area A/B” is never the baseline/comparison export A/B; use the full labels consistently. Hide/disable entering Area to area while in a paired/export-comparison view and explain “Use Single map for area-to-area analysis.” Switching to another view exits this analysis mode cleanly and restores the existing inspector; retain pair geometry in memory for later return to the same primary dataset.

Inside Area to area:

1. Show an active target control **Edit Area A / Edit Area B**; default A. Display the active target in the drawing prompt, not just by color.
2. Reuse Circle and Polygon tools. Cell selection remains available only in Single area mode; do not expand scope to two grid-cell selections.
3. After the first valid Area A is committed and B is missing, activate Edit Area B automatically. After B is committed, keep B active so it can be repositioned repeatedly while A stays fixed.
4. A circle click replaces only the active area's circle. Store each circle's own radius. Put radius selection in this panel, not solely far down the sidebar. Changing it updates only the active committed circle; when editing a polygon it changes the next-circle default only. Returning to an existing circle displays that circle's stored radius.
5. Polygons retain Finish, Undo vertex, Cancel and Escape. Drawing starts a draft for the active area, leaving its previously committed geometry and result intact. Cancel/invalid Finish must never erase either committed area. Show a draft status so an old result cannot appear to describe the draft.
6. Switching target, tool, analysis mode, or map view cancels the uncommitted draft and preserves the two committed areas. Do not accidentally complete a polygon into the newly active slot.
7. Provide **Clear Area A**, **Clear Area B**, and **Swap areas**. Swap swaps geometries and their radius settings, then recomputes both cards; it does not merely exchange headings. Hide the old ambiguous Clear action in this mode.
8. Redrawing/repositioning is the editing mechanism for this release. Do not add draggable polygon vertices or a new drawing library.

Show both direction cards at once:

```text
Area A → Area B
70 modeled commuters live in A and work in B
70.0% of all home-to-work commuters originating in A (70 / 100)
77.8% of all home-to-work arrivals in B (70 / 90)

Area B → Area A
[same three metrics with areas reversed]
```

Show area point counts alongside the labels. Use comma-grouped integers and one decimal place for percentages. Include short persistent copy: **“Counts and percentages come from imported home-to-work trip groups. Opposite direction is not a return trip. These are not predicted subway riders.”**

Add a direction control **Highlight A → B / B → A**, default AB. Switching the highlight changes only the endpoint overlay, not the committed geometries, denominators, or result cards. An overlap/mismatch message must remain visible even when the inspector scrolls.

## State and lifecycle

Add a small feature-owned state object, for example:

```text
areaPair = {
  enabled: false,
  datasetRevision: null,
  membershipSpec: null,
  activeArea: 'a',
  highlightDirection: 'ab',
  geometries: {a: null, b: null},
  nextRadiusM: 800,
  draft: null,             // includes the target captured when drawing starts
  result: null,
  memberships: {a: null, b: null}
}
```

- Make these values authoritative for this feature. Controls display state; render functions must not obtain one area's radius from the other's DOM control.
- On first entry for a dataset, derive a fixed membership projection from that dataset using existing `createGridSpec`. Its reference latitude stays fixed for both areas. The cell-size field does not determine circle/polygon membership.
- Keep the original single-area geometry/state separate and restore it on exit. Do not call `selectArea` twice: it currently clears and replaces shared selection/trip state.
- Dispatch map clicks and drawing events before the existing single-area path. Grid rectangle handlers currently sometimes call `selectCell` directly; route them through the pair dispatcher when enabled. Prevent endpoint/shape layers and scatter clicks from accidentally editing the old single-area selection during pair mode. Disable the single-area scatter click action with a clear cue while pair mode is active; keep its statistic visible.
- Hide or disable the old selected-trip controls/global strongest-link toggle while this mode is active. Restore their prior settings on exit. Pair analysis is its own overlay, not a second simultaneous origin/destination heatmap.
- Cell-size, contrast, smoothing, pan, and zoom changes must preserve both area geometries and all six metrics. In particular, bypass the old `rebuild()` selection-clearing branch for pair state. Only underlying map visuals change.
- Successful primary import clears pair state, cached membership, and overlays and increments a dataset revision. Failed imports preserve the existing pair. Reimporting the same filename still represents a new revision.
- Geometry commits recalculate memberships and results once. Highlight-direction changes reuse numeric results; viewport changes redraw visible markers without rescanning trips. Reuse a point-ID lookup for coordinates. Do not cache every past shape or slider position.
- No disk persistence, auto-save, new task/thread, or automatic export is required.

## Map presentation

Use dedicated Leaflet layer groups for Area A, Area B, and matched endpoints. Reuse an SVG renderer or another verified approach that does not place an interactive full-map canvas above the map. Preserve normal map pan/zoom.

- Area A: solid teal outline with an “Area A” label. Area B: dashed dark-blue outline with an “Area B” label. Use low fill opacity and color-independent labels/styles. Keep labels visible in overlapping/identical cases through small opposite label offsets.
- Highlight **original demand points**, not cell centers and not a smoothed surface. Use orange home markers and purple workplace markers for the chosen direction. Marker area increases with commuter weight; a bounded square-root radius mapping is sufficient.
- Use a common weight-to-radius mapping for the two roles in the active direction. Show a small key with actual count examples; radius is for readability, not a probability.
- When the same point has both roles, use a larger home disk and a smaller workplace disk or ring so neither role disappears; label the two roles in the key.
- Match all qualifying records. Viewport culling is allowed for rendering; top-N truncation must not alter statistics. Hide nonmatching marker overlays, but retain the existing underlying map/heat layer as context.
- Decorative outlines/markers are noninteractive so clicks continue selecting the active area. No need for endpoint popups or a new ranked table in this release.
- Do not draw a centroid-to-centroid “recommended line,” station catchments, or any route suggestion. The data shows endpoint relationships only.

## Export compatibility, bounded to the existing export actions

This is not a new export project, but existing exports must not silently describe the previous single-area selection while displaying two areas.

- Extend existing analysis JSON with an additive `areaPair` field: geometry A/B, active highlight direction, availability, four denominator totals, directional counts/shares, and overlap fields. Avoid raw Map/Set values. Serialize numeric results/geometry, not all trip records. Identify that the data is primary-dataset analysis; keep source metadata.
- While pair mode is active, the old single-area `selection` field must be null/inactive rather than presented as the displayed selection. It may remain preserved privately for restoration on exit.
- Existing PNG snapshots must include both outlines, matching endpoints, an A/B and marker-role key, the active direction, that direction's count and two percentages, and an overlap notice if needed. Capture these values before asynchronous image decoding, from the same committed result used by the UI. Ensure pair overlay panes are included in the current compositor's selectors.
- Hide the selected-trip CSV action in pair mode; do not let it export a stale single-area result. The grid CSV remains a clearly labeled whole-map grid export.
- Do not add pair CSV, GeoJSON, raw-trip downloads, a report generator, or reloadable projects in this release. Preserve existing export behavior outside pair mode.

## Deterministic test fixtures

Extend `tests/map_analyzer.cjs`; retain existing tests. Most tests should directly supply ID sets to the pure helper, separating counting from geometry. Add a few integration tests that derive those sets via `selectGeometry`.

### Fixture 1: basic denominators

Reuse existing four-point fixture and groups:

```text
h1 → w1: 70
h1 → w2: 30
h2 → w1: 20
h2 → w2: 30
Area A = {h1}; Area B = {w1}
```

Expected: AB=70; outA=100; inB=90; AB shares=0.7 and 7/9. BA=0; outB=0; inA=0; BA shares=null/null. AB origin and destination weight sums each equal 70.

If B expands to `{w1,w2}`, AB becomes 100, outA remains 100, inB becomes 150; shares become 1 and 2/3. A's geometry/membership must not change.

### Fixture 2: nonzero reverse direction

Add `w1 → h1: 12` and `w1 → h2: 8`; derive consistent supplied point counts separately if needed. Same A/B memberships yield AB=70, BA=12, outB=20, inA=12. Reverse shares are 0.6 and 1. The forward count/shares remain 70, 0.7, 7/9. This catches accidental return-trip duplication.

### Fixture 3: overlapping areas

```text
Area A = {x, s}; Area B = {y, s}
x → y: 10
y → x: 4
s → s: 3
x → s: 2
s → y: 5
```

Expected: AB=20, BA=7; outA=20, inB=20, outB=12, inA=9; AB shares=1/1; BA shares=7/12 and 7/9. Shared points=1, shared directional weight=3. The two directional sums add to 27 although the fixture contains only 24 weighted commuters: this is intentional and must not become a unique-total claim.

Also test identical selections, with AB=BA=internal weighted demand and the shared warning. Overlapping shapes with no shared demand points must not be labeled geometrically disjoint.

### Other required cases

- Both selections absent/one absent: controller shows incomplete state, not a numeric zero result.
- Missing trips versus explicitly empty trips; zero-denominator shares versus zero numerator with positive denominator.
- Supplied count mismatch: alter h1.residents to 999 without changing trips; AB stays 70 and origin share stays 0.7. No clamping or silent repair.
- Duplicate coordinates with distinct IDs; subgroup splitting; array reordering; consistent ID renaming. Weights/results remain invariant.
- Swapping A/B exactly swaps directional results and appropriate denominators. Swapping twice restores the initial state.
- Trip endpoints in the same analysis cell still count when one belongs to A and the other to B. Never exclude them using the existing `flows().internal` rule.
- Boundary inclusion for circle/polygon, concave polygons, invalid polygon rejection/cancel preserving committed state.
- Every available directional count equals each endpoint-role weight sum and is bounded by both denominators.
- Frozen inputs remain unchanged. Use exact integer assertions for counts, tolerances for geometry/fractions.

## Implementation order and completion gates

### Phase 1: pure counting

- [ ] Implement/export `areaPairDemand` and deterministic tests.
- [ ] Prove forward/reverse, mismatched-count, overlap, and endpoint-weight invariants before wiring UI.

### Phase 2: independent selection state and inspector

- [ ] Add the analysis selector, A/B target, Circle/Polygon tools, independent radius, clear/swap actions, and two result cards.
- [ ] Route all relevant click/draft/clear/mode/import/rebuild events without overwriting the preserved single-area state.
- [ ] Verify partial selection, draft cancel, missing trips, and overlap messages.

### Phase 3: highlights and export compatibility

- [ ] Draw both labeled outlines and all matching endpoint roles with a key.
- [ ] Direction changes update only highlights; map display settings do not change pair metrics.
- [ ] JSON and PNG describe the current pair; other exports/modes remain usable.

### Phase 4: live verification and documentation

- [ ] Check deterministic fixtures through browser UI, including independent editing and overlap.
- [ ] Check a real map and reconcile a sampled pair by a separate direct scan of raw `pops`, using the committed geometries/ID sets. Do not use the same new aggregation helper as its own oracle.
- [ ] Check 1280×720 and a genuinely narrow viewport using an available supported browser mechanism. If narrow viewport testing is unavailable, report it rather than mark it passed.
- [ ] Verify old Single area circle/cell/polygon inspection, origins/destinations, People/Jobs paired view, export comparison, and exports are not broken.
- [ ] Update `docs/map-analyzer.md` and this checklist with actual results. Stop only the transient server started for testing.

Run from repo root:

```powershell
node tests/map_analyzer.cjs
python -m unittest discover -s tests -p test_map_analyzer.py
node --check tools/static/map-analyzer/core.js
node --check tools/static/map-analyzer/app.js
git diff --check
```

The importer tests are a bounded regression check, not an invitation to modify the importer. Check UTF-8 without BOM/LF and whitespace for untracked edited files too; `git diff --check` does not cover them. No generator build or full repository audit is required.

Browser acceptance sequence:

1. Import fixture 1, enter Area to area, draw A around h1 and B around w1. Check 70, 70.0%, 77.8%, and the reverse N/A shares.
2. Keep A fixed, redraw B to include w2. Check 100, 100.0%, 66.7%; switch highlighting and confirm zero reverse markers with unchanged cards.
3. Edit A's radius without changing B; edit B's radius without changing A. Try invalid polygon, Undo, Cancel, Escape, switching target mid-draft, and changing tool mid-draft.
4. Exercise clear-one, swap, overlap, and identical selections. Ensure overlap does not produce a fabricated combined total.
5. Change cell size, contrast, smoothing, pan and zoom: committed geometries, memberships, counts, and shares remain unchanged.
6. Exit and reenter the mode, switch to a paired/export-comparison view and back, then fail an import and successfully replace the primary dataset. Verify the specified preservation/reset behavior and no stale overlays.
7. Download/inspect JSON and PNG, then compare their figures to the visible committed result. Trigger a new selection while PNG is preparing and verify the exported snapshot stays internally consistent.
8. Inspect browser errors and record a real-map calculation time. Recompute on committed changes, not each draft vertex or pan; introduce no worker infrastructure unless measured responsiveness requires it for this feature.

Final report: identify the implemented feature, tested fixtures/counts, real-map reconciliation, inspected exports, and remaining limitations. Do not claim route quality, game performance, or completion of unrelated older-plan gates.

## Handoff prompt

> Implement `docs/map-analyzer-area-to-area-plan.md` in this checkout. This task is only two-area home-to-work demand analysis: independent circles/polygons, weighted directional counts and trip-derived shares, matching endpoint highlights, overlap semantics, and compatibility with existing modes/exports. Preserve unrelated local work. Reuse current geometry/import helpers; do not build line sketching, route ranking, simulation, or the unfinished broad refactor from the earlier plan. Complete the listed phases and acceptance checks, updating this plan with evidence. Do not commit, push, publish, or synchronize another checkout. Report genuine blockers and do not mark unverified gates complete.

## Implementation evidence

Implemented independent area geometry, circle/polygon editing, weighted directional helper, overlap reporting, endpoint highlighting, JSON metadata and PNG numeric metadata. Node analysis tests (including basic and overlap fixtures), both JavaScript syntax checks, 12 Python importer tests, and git diff whitespace checks pass. Browser interaction, narrow viewport, real-map timing/reconciliation and downloaded PNG/JSON inspection have not been verified. Completion gates above remain unchecked pending that evidence.
