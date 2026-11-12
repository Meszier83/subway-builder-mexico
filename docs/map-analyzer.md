# Map Analyzer

A separate local viewer for the people, jobs, and trips represented in a Subway
Builder map. It imports files read-only and does not run the map generator or a
ridership simulation. No extra Python packages are required.

Double-click `map_analyzer.bat` on Windows, or run from the repository:

```powershell
python tools/map_analyzer.py
```

The browser opens at `http://127.0.0.1:5052`. Use `--port 5053` for another port
or `--no-browser` to start only the server. Stop with Ctrl+C in its terminal.

## Inputs and views

Open a game map ZIP containing one `demand_data.json`, or that JSON directly.
A ZIP's adjacent `config.json` supplies the map name. The browser reads the
archive directory and only the required members. The selected file and the
uncompressed demand JSON each have a 512 MiB limit. The optional configuration
has a 4 MiB limit. For larger packages or unsupported ZIP layouts, select the
demand JSON directly. The viewer uses an OpenStreetMap street
background rather than the ZIP's PMTiles. Leaflet and analysis assets are local;
the street background needs internet. Demand overlays work without street tiles.

- **People / Jobs:** heatmaps weighted by point `residents` / `jobs`, rather than
  point counts. Saturated colors and a faded street background emphasize demand.
  **Visual contrast** boosts intermediate colors without changing counts or their
  ordering; the default is 1.8× visual strength. **Smoothing radius** is in ground
  metres (600 m by default), so the same neighborhoods remain coherent as you zoom.
  The image and its density scale cover the whole dataset and stay fixed during
  pan, zoom, and window resizing. People and jobs have independent scales; changing
  layer or smoothing radius changes the legend. The legend reports approximate
  smoothed density per km²; use the count grid for exact totals.
  A Mercator raster scaled at the map's midpoint latitude approximates city-scale
  ground distance. Raster resolution is normally one sixth of the radius, with
  coarser sampling for exceptionally wide maps to bound memory; the legend then
  reports that sampling distance.
- **Count grid:** 250 m, 500 m, or 1 km approximate ground cells, with exact imported
  counts summed per cell. Colors use a map-wide scale for the selected layer.
  The local equirectangular projection is suitable for city-scale exploration;
  it is not an equal-area global analysis. The grid reference latitude is the
  midpoint of the demand bounds and stays fixed when panning or zooming.
- **Balance:** `(jobs - residents) / (jobs + residents)`, from orange residential
  concentration through gray to purple employment concentration. Opacity also
  indicates activity (`residents + jobs`) relative to the map's busiest cell. The
  two-dimensional legend shows both; activity is not a count of unique people.
  Empty cells have no fill. Hover for counts.
- **Correlation:** Pearson's r on residents and jobs in the same cells. Cells
  empty in both layers are excluded. The scatter plot links to cell selection.
  Constant layers or fewer than three occupied cells show N/A. Results depend
  on cell size; co-location does not establish causation or OD connectivity.
- **Concentration:** share held in the top 10% of occupied cells, rounding cell
  count up and ranking people and jobs separately.
- **Area inspection:** use Cell, Circle, or Polygon in the map inspector. Circles
  use a 400 m, 800 m, or 1.2 km straight-line radius selected in the sidebar.
  Click polygon vertices and use Finish; Undo and Cancel are available. Only simple
  polygons are accepted. Boundary points are included. Areas and densities are
  approximate, and these are not walking-network catchments.
- **Selected-area trips:** choose destinations to see where selected residents go,
  or origins to see where workers arrive from. Heat and the paginated cell table use
  trip-group sizes, including endpoints within the selection. Shares use all selected
  trip weights as the denominator. The heatmap is not a transit-ridership forecast.
- **People and jobs view:** synchronized maps use either a shared or independent
  scale. Shared scales compare counts or approximate smoothed density on the same
  geometry; independent scales reveal each layer's own hotspots.
- **Export comparison:** open a second map as B, then compare counts and directed
  connections against baseline A. Both datasets use the same grid. Differences mean
  B minus A; blue is a decrease and red is an increase. Connection counts use trip
  weights, not point IDs, and within-cell pairs remain in the table. Optional filters
  select original endpoints inside the selected geometry before aggregation. A change
  in aggregated connections does not identify individual people changing trips.
- **Trips:** aggregate `pops.size` by directed origin/destination analysis cell.
  Show the 30 largest links, or restrict to trips leaving/entering selected points.
  Selection filters exact point membership before cell aggregation. The summary
  reports omitted links and within-cell trips; white dots mark destinations.
  Connections are straight lines, not actual travel routes or predicted transit usage.
- **Exports:** the original grid CSV remains available. Analysis JSON records settings,
  sources and selection; additional CSVs contain selected endpoints and comparison
  cells or directed connections. PNG can include currently loaded street tiles when
  permitted by the tile server. If tiles are unavailable or cannot be exported, use
  the explicitly labeled demand-only PNG. Analysis JSON is not a reloadable project.

## Meaning and validation

For this repository's exports, `residents` represents resident commuters and
`jobs` represents realized model arrivals. Neither is a complete census population
or an observed workplace headcount. Other publishers may use different conventions;
the analyzer displays their supplied counts without inferring their provenance.

Imports validate coordinates, unique point IDs, integer nonnegative counts, positive
trip group sizes, and all trip references. A missing `pops` array allows heatmaps
with an explicit flows-unavailable notice. If point counts disagree with trip sums,
the viewer shows a warning and preserves both quantities rather than repairing them.
Original inputs are never overwritten; the CSV is an explicit browser download.

Checks:

```powershell
python -m unittest discover -s tests -p test_map_analyzer.py
node tests/map_analyzer.cjs
node tests/map_analyzer_import.cjs
```

## Area-to-area demand

Choose Analysis → Area to area in the inspector while using Single map. Draw independent circles or polygons for Area A and Area B. Keep A fixed and redraw B to compare destinations. Both cards count weighted imported home-to-work groups; percentages use trip-derived origin and destination totals. B → A describes separate home-to-work records, not return journeys. Shared points remain in both areas and shared records are disclosed. Orange markers show matching homes and purple rings show workplaces for the highlighted direction. JSON includes both geometries and numeric results; PNG includes the overlays and directional figures. These figures do not predict subway ridership.

## Default visibility

Dark demand focus is the default background. People use blue-to-ice colors and jobs violet-to-pale colors; brighter colors indicate higher intensity. Heatmaps and count grids use a fixed logarithmic color curve to reveal secondary concentrations without clipping peaks or changing counts. The curve and map-wide scales stay fixed when panning and zooming; shared comparisons use the same curve and domain. Legends and PNGs follow the display curve. Light streets remains available under Map background, and the contrast slider is optional under Fine-tune appearance. JSON records the theme and contrast curve.

## Browser-local import

The file picker reads JSON and ordinary stored or Deflate map ZIPs in the
browser. It bounds decompression by declared sizes, checks CRC32 and validates
the demand before replacing the current map. The import status shows elapsed
time and the current stage. ZIP64, encrypted members and unsupported compression
require selecting `demand_data.json` directly.

In the recorded browser check, Mérida's `MID.zip` loaded in 0.7 seconds with a
visible heatmap, enabled controls, 3,078 points, 13,283 trip groups, 664,150
commuters and no console errors.
