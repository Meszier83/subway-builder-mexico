# DENUE mixed-encoding fix results

Date: 2026-11-05. Status: reader fix implemented and activated; the CDMX retry
passed DENUE ingestion, then failed in a separate demand-support memory allocation.
No demand export or ZIP was produced.

## Changes

`sb_mexico/denue_encoding.py` validates complete sources with strict incremental
UTF-8/CP1252 decoders. Files that need mixed recovery are normalized to temporary
UTF-8 in bounded blocks before pandas or the identity ledger processes a row.
Only valid printable UTF-8 sequences containing an otherwise undefined CP1252
byte are recovered. Ordinary CP1252 text and ambiguous CP1252-valid byte pairs
retain their existing interpretation. Unexplained bytes fail with source path
and absolute offset; bytes and rows are never silently discarded.

The normalizer handles byte-boundary splits and BOM/header preparation. It
records source SHA-256, reader revision, recovery count, bounded byte offsets,
and normalized SHA-256. Original file identity/hash is verified before and after
consumption; temporary files and pandas readers close on errors as well as
success. Valid homogeneous files are read directly without a normalized copy.

`load_denue()` now covers the complete reader lifecycle and propagates parser
errors with the original source path. Missing selected files are explicit
failures. Existing identity, duplicate, municipal-total, stratum, and BBOX/full-
scope calculations remain in place. A mixed source cannot be represented by a
single original-file encoding through the legacy detection helper; production
loading uses the prepared stream instead.

Demand V2 code fingerprints now include `inegi.py` and the new helper. The fine-
workplace memory cache includes the reader revision, and its report retains the
decoding audit and helper implementation hash. No source CSV, city YAML,
calibration formula, routing rule, or cartography asset was changed.

## Verification

- 67 targeted Windows tests passed across encoding, INEGI, workplace loading,
  automatic workplace selection, Demand V2 warm-cache behavior, and repository
  encoding checks, including valid two-, three-, and four-byte recovery across
  small read boundaries.
- Fixtures cover a failure beyond 64 KiB and after a completed 100,000-row
  chunk, UTF-8 boundary splits, BOM, CP1252 punctuation, quoted delimiters and
  embedded newlines, leading-zero IDs, full-scope records outside the BBOX,
  duplicate paths, unexplained bytes, invalid/truncated required recovery,
  source mutation, cleanup, and source-specific parser errors.
- Fine-workplace reader-revision changes missed the old memory cache. Changing
  either loader/helper code hash changed Demand V2 evidence identity.
- All eight selected real CDMX sources parsed completely: 2,253,173 rows. Every
  column of every parsed chunk matched an independent reference.
- For the seven CP1252-valid sources, the reference was their original strict
  CP1252 parse. For Guerrero, a temporary reference copy replaced exactly the
  evidenced `C3 81` bytes at offset 43,871,194 with CP1252 `C1`, then used the
  ordinary CP1252 CSV reader. The original Guerrero file was untouched.
- The production full-scope Guerrero loader retained all 185,797 records with
  zero identity duplicates or missing identities. Its 85 municipal totals sum
  to 739,993.16 raw stratum-based job units, before CE calibration.
- Guerrero required exactly one recovery, at undefined-byte offset 43,871,195.
  The field decoded as `GRANJAS DEL MÁRQUEZ`. All other parsed fields matched
  the independent reference. Existing CP1252-valid mojibake is outside this fix.
- All eight original raw SHA-256 hashes were checked again before the retry and
  remained unchanged. Five cartography files required by the existing package
  path matched the previous validated artifact hashes.

Raw validation details are under `reports/denue-encoding-fix/`, including
`source-validation.json`, `guerrero-loader-validation.json`, and `preflight.json`.
Reference CSV copies were temporary and were cleaned up.

## Live activation and retry

The idle Wizard PID 15108 was verified against its project root and executable,
then replaced by PID 20700 using the same Python interpreter and port 8080.
The retry explicitly sets `skip_map=True`, `skip_demand=False`, and
`skip_packaging=False` for `cities/ciudad_de_mexico.yaml`.
Its build ID is `de1bfd3f91af485f97b246077f4ec964`; the matching stage is
`dist/ciudad_de_mexico/wizard-builds/de1bfd3f91af485f97b246077f4ec964/`.

The live log confirms cartography was skipped, the current OSRM network was
reused, and ingestion passed the original Guerrero failure and reached all
selected DENUE files.
The evidence cache was written after ingestion, and OSRM connectivity probes
completed their logged batches. The Wizard's coarse ingestion progress label
does not expose the later Demand V2 substage; CPU activity alone is not a
completed-export claim.
The build log is `reports/denue-encoding-fix/wizard.log`; errors, if any, are
captured separately in `wizard.err.log`.

## Retry outcome: separate demand-memory blocker

The build manifest records `status=error` and no package path. The exception is
`numpy._core._exceptions._ArrayMemoryError`: unable to allocate 3.43 GiB for
460,715,568 `float64` distances in `demand_v2/allocation.py:59`.
`engine.py:81` calls `support(result['statistical_points'], parameters)` as the
fixed-cohort support precheck, before OD allocation/export. This complete
origin/destination support is accumulated in per-origin arrays and then
concatenated, so the failed new array is only part of the peak memory demand.
The observed Wizard private memory reached about 16.8 GB before the failure;
Windows reported about 15.2 GiB of physical RAM on the machine.

This is independent of decoding. All eight original source hashes and all five
reused cartography hashes were checked again after failure and remain unchanged.
The failed stage has no `demand_data.json` and no ZIP. The manifest and traceback
were preserved; `reports/denue-encoding-fix/retry-outcome.json` records the outcome.

The next change should bound demand-support construction and subsequent solver
memory while preserving permitted pairs, retained commuters, and demand rules.
A blocked/CSR or disk-backed representation needs its downstream consumers
reviewed too; replacing just `np.concatenate` would not establish that the full
solver fits. Dropping distant destinations or changing cohort settings would
change the model and is outside this reader repair. No such changes were made.

Game import/save/reload has not been tested because this attempt produced no
package.
