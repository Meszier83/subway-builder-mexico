# DENUE mixed-encoding fix plan

Date: 2026-11-05. Status: reader fix implemented and validated; retry passed
ingestion but failed on a separate demand-support memory allocation. No ZIP.

## Objective

Load the existing CDMX DENUE sources without losing records or corrupting text,
then allow demand generation and ZIP packaging to continue with cartography
skipped. Preserve the verified map files and the existing demand model.

Implementation was authorized after this plan was written. The reader and
cache changes are active; evidence and the full-build outcome are recorded in
`denue-mixed-encoding-fix-results.md`.

## Confirmed diagnosis

- The latest build successfully skipped cartography and compiled the OSRM road
  network. It failed later during Demand V2 workplace ingestion.
- `sb_mexico/inegi.py::_detect_denue_format()` inspects only 65,536 bytes and
  chooses CP1252 for all eight CDMX DENUE files.
- `data/ciudad_de_mexico/denue_inegi_12_.csv` is the failing Guerrero source:
  100,218,837 bytes. Its initial sample contains single-byte `E1` in
  `Mecánico`, so interpreting the entire file as UTF-8 also fails.
- At zero-based file offset 43,871,195, CP1252 decoding fails on `81`. The byte
  context contains `GRANJAS DEL M\xC3\x81RQUEZ`; `C3 81` is valid UTF-8 for `Á`.
  This is evidence of mixed text encodings, rather than a uniformly UTF-8 file.
- A complete scan found one undefined CP1252 byte in that file (`81`), and no
  `8D`, `8F`, `90`, or `9D`. The other seven sources completed strict decoding
  with their selected CP1252 encoding. This does not certify their full CSV
  structure, source coverage, or freedom from pre-existing mojibake.
- The loader's exception handler covers reader construction, but not the
  subsequent `for chunk in reader` iteration where this exception occurs.
- `RecordIdentityLedger`, municipal employment totals, and retained chunks are
  updated during iteration. Retrying a file after processing some chunks could
  duplicate totals or make records appear to be duplicates of a failed attempt.
- Demand V2's `_code_stamp()` currently omits `inegi.py`. The fine-workplace
  memory cache also lacks an explicit DENUE reader revision in its key.

Guerrero records must not be removed to avoid the error. Full-scope workplace
ingestion uses records before spatial clipping, including records outside the
CDMX BBOX.

## Proposed decoding policy

Use strict, bounded, whole-file validation followed by narrowly scoped recovery.
Keep the original CSVs unchanged.

1. Validate UTF-8 with optional BOM using an incremental decoder through EOF.
   An incomplete multibyte sequence at a read boundary must be carried into
   the next block, rather than classified as an encoding failure.
2. If UTF-8 fails, validate CP1252 through EOF. Use the existing CP1252 path for
   a file that passes. Preserve its accented letters and Windows punctuation.
3. If both fail, inspect every undefined CP1252 byte. Recover a byte only when
   it belongs to a complete, strictly valid UTF-8 sequence whose interpretation
   is unambiguous under this policy. In particular, `C3 81` becomes `Á`.
4. Decode all bytes outside those recovered ranges as strict CP1252. Write a
   temporary canonical UTF-8 CSV incrementally, preserving ASCII CSV syntax,
   field contents, quoting, delimiters, line endings, and record order.
5. Fail with the original filename and absolute byte offset if any undefined
   byte remains unexplained, a sequence is truncated/invalid, or recovery would
   produce an unexplained control character. Do not guess past that failure.

This policy deliberately repairs decoder-blocking mixed sequences. It does not
globally reinterpret CP1252-valid byte pairs as UTF-8: those cases can be
ambiguous and require separate evidence. A valid CP1252 sequence such as a smart
quote must retain its established meaning. Broader mojibake cleanup is outside
this repair.

Likewise, a final byte that is valid CP1252 must not be rejected merely because
it could be the start of a truncated UTF-8 character. Truncation/invalid-sequence
recovery tests must contain a decoder-blocking byte or an explicit UTF-8 contract;
the implementation must distinguish evidence from an encoding guess. Preserve
the recognized BOM/header behavior explicitly when preparing normalized input.

Do not use `errors='ignore'`, replacement characters, unconditional Latin-1
fallback, `on_bad_lines='skip'`, or removal of the Guerrero source. A successful
decode alone is insufficient if text or record counts change unexpectedly.

## Implementation boundaries

| File | Intended change |
| --- | --- |
| `sb_mexico/denue_encoding.py` (new) | Strict incremental validation, bounded mixed-sequence normalization, temporary-input context manager, audit information, and reader revision. |
| `sb_mexico/inegi.py` | Integrate prepared input with existing delimiter detection and `load_denue()` chunking; report original source paths and propagate failures reliably. |
| `sb_mexico/fine_workplace.py` | Include the reader revision in the in-memory cache key; preserve decoding audit information through processing. |
| `sb_mexico/demand_v2/request.py` | Include `inegi.py` and the new encoding helper in evidence/code fingerprints. |
| `tests/test_denue_encoding.py` (new) | Byte-level, boundary, cleanup, and failure tests. |
| `tests/test_inegi.py` | Loader regression tests for record identity, totals, full scope, and late decoding failures. |
| Relevant workplace and Demand V2 tests | Verify the caller path and cache invalidation. |
| `docs/demand-wizard.md` | Explain the corrected failure, audit output, and reuse of current cartography after validation. |

Do not change CSV source files, source-download URLs, city YAML, cartography,
calibration formulas, employment strata, cohort sizes, or routing behavior.
Reuse existing dependencies; a statistical encoding-detector dependency is not
required for this evidenced recovery policy.

## Execution phases

### 1. Capture fixtures and invariants

Record source sizes and SHA-256 hashes, the effective CDMX configuration, and
hashes of the staged map artifacts. Preserve unrelated working-tree changes.
Create synthetic fixtures rather than committing the large raw datasets or
whole business records to the test suite.

Capture the current outputs for valid UTF-8, BOM, CP1252, and Latin-1-compatible
fixtures. Record IDs, names, coordinates, strata, municipal keys, and employment
totals. A fixture with a single-byte accent early in the file and `C3 81` after
the first 64 KiB must reproduce the reported failure before the fix.

Gate: the regression reproduces the failure, while the valid-source fixtures
establish expected text, IDs, and numeric results.

### 2. Implement the prepared-input context manager

Read binary input in bounded blocks, initially 1 MiB. Keep only decoder state
and at most the short pending UTF-8 boundary sequence between blocks. Never
load the entire CSV into a Python string or DataFrame for encoding detection.

Prefer direct reads of validated homogeneous files. For mixed files, finish
normalization and strict UTF-8 validation before yielding the temporary input
to pandas. Temporary output must not replace or rename the original source.
Close temporary handles before pandas opens them on Windows, and remove the
temporary file after reader closure on success, exception, or cancellation.

Detect source changes across validation/normalization using recorded file
identity and streaming hashes. A change during preparation must invalidate the
attempt instead of combining two source revisions.

Audit each source with its original path, raw checksum, validation result,
decoding policy/revision, repair count, repair byte offsets, and normalized
checksum when applicable. Keep log summaries bounded; stream detailed repair
events to a sidecar if necessary. Do not put business names in routine logs.

Gate: the evidenced mixed sequence becomes `Á`, ordinary CP1252 punctuation and
accents remain exact, invalid/unresolved bytes fail explicitly, and temporary
files are cleaned up. Peak normalization memory depends on block size rather
than total CSV size.

### 3. Integrate without partial ingestion or silent omission

Inspect all `_detect_denue_format()` callers before changing its return contract.
Preserve existing delimiter recognition and pandas CSV parsing, including quoted
fields and embedded newlines. Do not normalize text by splitting CSV rows or
fields with ad hoc string operations.

Complete preparation for a source before its first chunk can update the shared
identity ledger or municipal totals. Consume each file exactly once. Do not
restart iteration against already-mutated state after a decoding exception.

Use a reader context manager or `finally` cleanup covering construction and the
entire iteration. Replace the broad construction-time `except: continue` for
selected source failures with an explicit source-specific error; a partially
loaded collection must not masquerade as complete evidence. Preserve intentional
repeated-path handling and distinguish that from an unreadable selected file.

Keep raw source identity in reports and cache keys. Temporary normalized paths
must not become source identities. Attach decoding audit metadata to the returned
frame and ensure workplace processing can retain it across `reset_index()` and
classification. Surface a concise message in the build log.

Gate: valid-source outputs match the baseline; mixed-file fixtures retain every
expected record exactly once; failures after a pandas chunk boundary produce no
successful partial evidence or package.

### 4. Invalidate affected caches

Add the DENUE loader/helper code to Demand V2's `_code_stamp()` and the reader
revision to `_FINE_WORKPLACE_CACHE` identity. Inspect other DENUE consumers for
equivalent caches. A code/policy revision must prevent reuse of old ingestion
results even when raw CSV hashes remain unchanged.

Keep the raw-file fingerprints authoritative, and publish evidence caches only
after successful ingestion. Do not delete all project caches or invalidate the
verified PMTiles, building index, or road geometry to achieve this.

Gate: old-reader cache identities miss, same-reader valid evidence can hit, and
failed normalization/ingestion never publishes a successful evidence cache.

### 5. Run focused regression and real-source validation

Required fixture coverage:

- UTF-8 with/without BOM, CP1252, and Latin-1-compatible accents.
- A UTF-8 character split at 65,536 bytes and at normalization block boundaries.
- The exact mixed `C3 81` case beyond the initial sample and after a completed
  pandas chunk, including an outside-BBOX record retained by full-scope loading.
- Smart quotes, degree symbols, ASCII IDs/leading zeros, quoted delimiters,
  embedded newlines, multiple source files, and repeated paths.
- Unexplained undefined bytes and invalid/overlong/truncated sequences that would
  be required to recover a blocking byte:
  source-specific errors, no skipped records, and no temporary-file leaks.
- Duplicate/conflicting establishment identities and unchanged municipal totals,
  with both `full_scope=False` and `full_scope=True`.
- Stable recovery output across block sizes and correct cache-version behavior.

Run `test_denue_encoding.py`, `test_inegi.py`, relevant source-identity/workplace
tests, and targeted Demand V2 cache tests. Run encoding and diff checks for edited
files. Expand testing only when a failure or affected caller justifies it.

Then validate the complete Guerrero source with the prepared-input reader. Compare
its parsed row count and numeric/identity columns against an independent strict
Latin-1 carrier parse used only as a validation reference. The reference must not
be accepted as a text-quality oracle or production fallback. Verify the repaired
field explicitly and compare the other fields of that record.

Validate all eight selected DENUE files. For the other seven, compare the new
loader with the existing successful CP1252 reader and check unchanged decoded
text, IDs, duplicate handling, retained counts, and municipal employment totals.
Record the final Guerrero results without inventing expected numeric counts in
advance. Confirm every raw-file hash still matches its baseline.

Gate: complete selected-source parsing succeeds; all expected records and totals
are conserved; no evidence of dropped/replayed rows or new text corruption is
found. This proves ingestion, not the later demand allocation or ZIP.

### 6. Activate and resume the CDMX workflow

Verify that the Wizard is idle before restarting its exact owning process to
load the corrected reader and clear old process-local caches. Verify the same
checkout, Python interpreter, port, and replacement instance afterward.

Confirm that the staged cartography hashes and cartography/configuration identity
still match. Resume with cartography skipped, demand generation enabled, and ZIP
packaging enabled. Preserve a failed-build record if a later independent stage
fails; report that stage rather than calling the complete pipeline fixed.

For a complete-build claim, verify the new ZIP against its own build outputs and
check demand references, commuter totals, and applicable allocation reports using
the existing validators. Record separately whether target-game import/save/reload
has been tested. Do not publish a release as part of this repair.

## Completion checklist

- [x] Byte-level regression and valid-source baselines captured.
- [x] Strict bounded preparation and narrowly scoped mixed recovery implemented.
- [x] Loader iteration, failure propagation, and cleanup verified.
- [x] Cache identities and decoding audit propagation verified.
- [x] Complete Guerrero and seven-source comparisons passed; raw hashes unchanged.
- [x] Idle Wizard restarted and cartography reuse reconfirmed.
- [x] Demand/ZIP retry outcome recorded: separate demand-memory failure; no package to validate.

No implementation or validation gate is complete merely because this plan exists.
