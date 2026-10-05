# Integer O/D export contract

`macroeconomics.od_allocation: balanced_integer_v1` selects the shared candidate
path for any city. It is now the common default following the user's confirmation
that the candidate works in Subway Builder. No separate Wizard control
or city-specific source audit is required for this allocation method.

The candidate changes allocation and packing, not source quality. Destination
weights remain DENUE estimates, optionally informed by historical CE transfer.
Exact model margins do not establish measured workplace counts or observed O/D.

## Authoritative margins

1. Run the existing seeded gravity/POI procedure to obtain a sparse proposal.
2. Finalize the existing spatial clustering and retain its original-to-final ID map.
3. Roll up the independent original origin budgets and the requested/effective
   destination target vectors. Preserve special POI locations, realized quotas
   and origin consumption. Remaining budgets are regular employment demand.
4. Normalize only accumulated float32 total noise, within a checked bound, per
   isolated zone before freezing fractional targets. Report that delta separately
   from the existing reachability correction. No new employment calibration occurs.
5. Build support on final IDs and geometry. Ban self edges and cross-zone edges.
   Preserve the existing zero-row/column nearest-five distance fallback policy,
   excluding self edges even when only one destination exists. Report its pairs.
6. Choose each integer destination target jointly within floor/ceiling bounds,
   with an exact zone total and a feasible integer transport. Independent largest
   remainder rounding can violate a tight support cut.
7. Pack each exact pair independently, respecting `max_pop_size`. `min_pop_size`
   is best effort: never move a one-person residue into another pair to satisfy it.

The solver repairs the proposal through integer residual flow using compiled
Dinic. It expands support from sampled pairs plus nearest permitted neighbors
in both directions (8, 32, 128, then complete support). This favors a sparse
continuation of the proposal; it does not claim a globally optimal gravity-cost
solution. Infeasibility is declared only after complete permitted support fails,
with a capacity-cut witness and affected IDs. No silent legacy fallback occurs.

## Delivery and verification

`od_allocation_report.json` records source origin budgets, point mapping,
requested/effective fractional targets, integer targets, POI quotas, support
attempts, exact pair masses and small-cohort counts. These diagnostics stay out
of the game's JSON schema and ZIP members.

Compare exported rows, columns and pairs against this independent authority
before orphan removal, after same-pair packing, after serialization, and inside
the ZIP. Wizard package validation repeats that check. `/api/demand-preview`
reads the same compiled artifact and validates its sidecar before displaying
“Márgenes enteros del modelo conservados” and “Destinos estimados”.

Positive targets can restore points that sampling previously left without
arrivals. Targets rounded to zero remain in the audit, even if their points are
removed from the delivered geometry. Fractional rounding error is disclosed
separately; zero integer residual does not imply zero fractional error.

## Candidate validation and adoption

The isolated candidate reports are in `reports/od-marginals-correction/`.
They use the frozen CUR reference build `0b6fbd124dda48259974533bef25f87f` and
also exercise Mérida's bounded DENUE fallback. Existing `dist` packages are
preserved. The initial CUR adoption gates are at most 38,775 cohorts and at most
twice baseline allocation/packing time and peak memory on identical inputs.

Passing source, numerical, package and Wizard checks does not establish game
compatibility. The user subsequently confirmed the candidate works in the game;
this is user-reported validation, not an automated game test.
`DEFAULT_OD_ALLOCATION` in `sb_mexico/config_defaults.py` now enables the common
path for future compilations. Unspecified projects are not saved with an implicit
legacy override. Explicit `od_allocation: legacy` remains reproducible.
