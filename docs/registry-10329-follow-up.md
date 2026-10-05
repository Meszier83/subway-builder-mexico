Hi @rslurry and @ahkimn, thank you for the questions. I reviewed the actual build path and rebuilt Cancún/Riviera Maya. My earlier explanation overstated what the CE adjustment and establishment coordinates establish, so I would like to correct it.

**Workplace data:** the current build uses DENUE employee-size bands as estimates, with conditional historical CE transfer where published detail is usable. The CE reference is activity in **2023**, grouped by **municipality, SCIAN sector and establishment-size stratum** (sectors 31–33, 43, 46, 53 and 72). It is not a six-digit SCIAN calibration: six-digit codes identify the DENUE records; the controls are broader sector groups.

Within an eligible group, the historical occupied-personnel / establishment mean informs current DENUE weights, bounded by the applicable size band. In the selected full source scope, 30,585 of 69,260 establishments receive transfer and 38,675 retain bounded DENUE estimates. Those are source-scope counts, before clipping to the map.

This does **not** force current destination totals to match CE 2023. I am therefore withdrawing the earlier claim of an exact municipal CE fit and an ENOE-bounded informal-employment expansion. The current build performs no TIL1 expansion. It also does not certify complete rural coverage or treat DENUE as an exclusively formal-employment register. Source establishment locations are subsequently aggregated into map demand points, so I should not have described every exported point as an untouched establishment coordinate.

**Residents:** the current default uses published block-level POCUPADA, with bounded reconstruction where a usable count is missing, followed by the declared municipal CONAPO projection. It does not use a single ENOE participation rate to generate the whole spatial employment distribution. The projected result remains a model-year estimate.

**O/D:** I also found and corrected a separate export problem: sampled destination allocations and later small-cohort consolidation could alter local margins despite preserving the grand total. The rebuilt map now freezes budgets on final point IDs, chooses feasible floor/ceiling destination rounding jointly, and preserves each assigned pair through packing and ZIP export. It retains **898,658 commuters**, split **848,780 mainland / 49,878 Cozumel**, with **27,966 cohorts**, zero integer origin/destination residuals, no self-commutes or cross-island pairs, and the same realized special-POI quotas. The pipeline tests and Wizard delivery checks pass, and the rebuilt candidate works in-game.

Those exact margins are **estimated model targets**, not observed commute flows or independently measured contemporary workplace totals. The integer-allocation fix is not, by itself, evidence for `synthetic_measured_marginals` or a higher workplace tier. I am not repeating my previous request for `fine_types_calibrated` on the basis of address coordinates alone.

The implementation and corrected methodology are here: [current methodology](https://github.com/Meszier83/subway-builder-mexico/blob/main/METHODOLOGY.md) and [integer O/D contract](https://github.com/Meszier83/subway-builder-mexico/blob/main/docs/od-integer-allocation.md).

These results refer to the locally rebuilt map; the registry download has not been replaced by this comment. Could you review the revised provenance and advise which data-quality answers should describe this method? I would rather have an accurate classification than preserve a claim the sources do not support.

Thank you again for pointing out the distinctions.
