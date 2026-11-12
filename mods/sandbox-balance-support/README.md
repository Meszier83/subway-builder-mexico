# Sandbox Balance Support 0.1.0

Small standalone mod for Subway Builder **1.7.2**. Enable it in the normal mod
manager; there are no extra settings and no dependency on CUR Economic Pilot.

## Install

Extract the ZIP's `sandbox-balance-support` folder into
`%APPDATA%\metro-maker4\mods\`, then reload mods or restart the game and enable
**Sandbox Balance Support**. Load a sandbox game.

The mod credits a current-period subsidy to cash and global financial revenue
when needed to keep aggregate operating profit at least **+$1** in the daily
header and the **24H, 48H, 14D and ALL** summaries. The game's normal expense
figures and their red styling remain. Old losing chart bars remain in history.
The subsidy is included in total global revenue; the stock dashboard has no
separate subsidy row.

Line revenue, expenses, profit, fares, trains, ridership, simulation rules and
lifetime statistics are untouched. Normal games, tutorials and challenges do
not receive subsidies. Construction, train purchases and bond payments are
excluded from operating profit, following the game's existing dashboard.

The game saves credited money and financial history normally. Disabling this mod
stops future subsidies after **reload mods or restart**; it does not remove
subsidies already credited. Game exit detaches the financial subscription; a
later game starts it again. Mod reload removes all owned hooks/subscriptions.

## Compatibility and validation

The public `addMoney()` action excludes grants from reported revenue in 1.7.2.
This mod imports the already loaded `index-CuszHwSj.js` module, uses its `aJ`
store export, and updates only `money` and `financialHistory` in one operation.
It does not patch installed game files or write save files itself. Other game
versions or an unrecognized bundle leave it inactive with one error notification.

Build the ZIP with `python tools/build_sandbox_balance_support.py`. Run focused
checks with `python -m unittest discover -s tests -p test_sandbox_balance_support.py`.
The builder also inspects the installed game and verifies the adapter and
dashboard contracts. Offline checks do not certify visible in-game rendering or
native save/reload; see the generated validation report for their status.
