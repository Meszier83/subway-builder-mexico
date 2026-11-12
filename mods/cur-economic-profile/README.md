# CUR Economic Pilot 0.1.0

Private experimental mod for Subway Builder **1.7.2** and Railyard Map Loader
**0.2.10+**, targeting the uniquely registered `CUR` / `Cancun / Riviera Maya`.

The citywide income proxy uses INEGI ENOE 2025 Q4 Cancun exact positive-income
respondents. Driving and parking are explicit scenarios. Neighborhood inequality,
car availability and bus/colectivo competition are not represented.

## Install and use

1. Extract the ZIP's `cur-economic-profile` folder into
   `%APPDATA%\metro-maker4\mods\`.
2. Restart the game (or reload mods), enable **CUR Economic Pilot** in the mod
   manager, and load the Railyard CUR map.
3. In the game's settings, turn on **CUR economic pilot (experimental)**.
   This is the single economic activation switch; it starts **off**.

Existing fares are preserved, including custom fare groups. The chosen scale is
10/3 MXN per game unit: the shipped game fare of $3 corresponds to a hypothetical
MXN10 trip. Game dollar labels and all construction/operating costs remain in
game currency. No literal conversion of the full game economy is claimed.

Rules apply to future commute calculations; already computed journeys are not
force-rebuilt. Allow a complete simulated day for a full before/after comparison.
The game can have batches in flight when switching. This is an experimental
transition, not a claim of instant all-passenger recalculation.

Turning the switch off or leaving CUR restores the previous economic values.
Changing them from another mod suspends the pilot instead of overwriting the
other mod's changes. A conflicting/missing CUR registration or unsupported game
version keeps it inactive. The enabled preference is stored per mod and profile
version, not inside the `.metro` save.

`profile.json` is an audit copy; the same profile is embedded in `index.js` by the
builder. Changing the audit file alone does not change the active profile.

## Validation and removal

Use a disposable copy of a save for the first trial. Live city switching,
save/reload, worker delivery and financial playability must be recorded in the
accompanying pilot report; offline tests alone do not verify them.

Before removal, turn the economic switch off. Then disable this mod in the mod
manager and **reload mods or restart the game** before deleting the folder. The
manager's disable checkbox alone takes effect at its next reload. Hot-reload
cleanup uses the inspected 1.7.2 `modding-api-reload-request` event as well as the
supported game-end hook; other game versions require reinspection.

This profile is a gameplay experiment, not a prediction of real Mexican
ridership or a measured income distribution for all Riviera Maya municipalities.
