# Next release preparation

Prepared on 2026-10-08. Version, tag and publication date are not assigned.

## Proposed release notes

- Add an opt-in Demand V2 engine shared by compilation, Wizard previews and POI
  Studio. Its controls cover EIC 2025, official residential placement, automatic
  CE/DENUE employment, integer OD allocation, road connectivity, and adaptive or
  fixed groups of 50, 100 or 200 travelers.
- Add guided source downloads with source requirements, project isolation,
  geographic validation and EIC binding. Document required and optional inputs.
- Add toponymy source scanning, review, stable place identities, selective import
  and delivery in replacement or merge mode.
- Improve Wizard controls, automatic urban-core feedback, asynchronous candidate
  previews and reuse of matching road artifacts from the current build.
- Preserve the full city description in exported configuration.
- Refresh bundled Wizard assets and expand UI regression checks.
- Keep secrets, virtual environments, download fragments, local evidence and raw
  datasets out of commits. Correct Windows-specific test fixture encoding and
  isolate preview tests within their own permitted workspace.

## Verification

- Native Windows Python suite: 539 tests run, 538 passed and one skipped,
  in 76.083 seconds (`python -m unittest discover -s tests`).
- Wizard syntax and UI runner passed (`python tools/check_wizard_ui.py`),
  including the residential-employment harness. Both historical-employment UI
  harnesses also passed.
- Wizard assets rebuilt; all seven manifest SHA-256 entries verified.
- Git whitespace checks passed. No staged local datasets, outputs, downloaded
  evidence, caches or dependency directories. A credential-pattern scan of
  tracked and eligible new files found no matches; this is a limited pattern check.

Local logs are under `reports/release-prep/` and are intentionally excluded
from Git. Tests used Python 3.13.6, pandas 3.0.5, NumPy 2.4.6, Shapely 2.1.2
and GeoPandas 1.1.4. This run did not compile fresh city cartography in WSL.

## Release limits

Demand V2 remains opt-in. No city migration is included. Its observed model
quality and remaining limits are documented in [the current roadmap](demand-playable-roadmap.md)
and [candidate acceptance history](demand-v2-acceptance.md).

Import, simulation, save and reload in the target game version remain pending.
Code and package checks do not establish in-game playability. A published map
release still requires selection and verification of the actual city ZIP and its
SHA-256. The existing `releases/cur-update.json` describes the previous published
map and must be updated only with the new artifact's real identity.
