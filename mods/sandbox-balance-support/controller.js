/* Global finance subsidy only. No route, fare, simulation or lifetime-stat writes. */
(function (root) {
  'use strict';
  const OPERATING = ['trainOperational', 'trackMaintenance', 'stationMaintenance',
    'gradeCrossingMaintenance', 'other'];
  const HOUR = 3600;
  const DAY = 24 * HOUR;

  function number(value) {
    if (!Number.isFinite(value)) throw new Error('Invalid financial number');
    return value;
  }
  function operating(categories) {
    return OPERATING.reduce((sum, key) => sum + number(categories?.[key] ?? 0), 0);
  }
  function addDay(day, revenue, categories) {
    day.revenue += revenue;
    for (const key of OPERATING) day.categories[key] = (day.categories[key] || 0) + (categories?.[key] || 0);
    return day;
  }
  function dayProfit(day) { return day.revenue - operating(day.categories); }

  function createController(store, options = {}) {
    if (!store || !['getState', 'setState', 'subscribe'].every(key => typeof store[key] === 'function')) {
      throw new Error('Unsupported game state store');
    }
    let unsubscribe = null, disposed = false, writing = false, failed = false;
    let injections = 0, totalInjected = 0, lastError = '';
    let cachedEntries = null, cachedArchive = null;

    function archive(entries) {
      if (!Array.isArray(entries)) throw new Error('Unsupported financial history');
      if (entries === cachedEntries) return cachedArchive;
      const days = new Map(), tails = [{profit: 0, revenue: 0, expenses: 0}];
      const start = entries[0]?.timestamp || 0;
      let previous = -1;
      for (const entry of entries) {
        const timestamp = number(entry.timestamp);
        if (timestamp < previous || timestamp < 0) throw new Error('Unordered financial history');
        previous = timestamp;
        number(entry.hourlyExpenses);
        const revenue = number(entry.hourlyRevenue);
        operating(entry.expenseCategories);
        const day = Math.floor((timestamp - start) / DAY) + 1;
        days.set(day, addDay(days.get(day) || {day, revenue: 0, categories: {}}, revenue, entry.expenseCategories));
      }
      // Preserve the game's left-to-right floating-point arithmetic, including
      // its separate revenue/expense sums in the header and category sums per day.
      for (let count = 1; count <= Math.min(49, entries.length); count++) {
        tails.push(entries.slice(-count).reduce((sum, entry) => {
          const expenses = operating(entry.expenseCategories);
          return {profit: sum.profit + (entry.hourlyRevenue - expenses),
            revenue: sum.revenue + entry.hourlyRevenue, expenses: sum.expenses + expenses};
        }, {profit: 0, revenue: 0, expenses: 0}));
      }
      const sorted = [...days.values()].sort((a, b) => a.day - b.day);
      cachedEntries = entries;
      cachedArchive = {tails, start, days: sorted.slice(-14),
        allDays: number(sorted.reduce((sum, day) => sum + dayProfit(day), 0)),
        earlierDays: number(sorted.slice(0, -1).reduce((sum, day) => sum + dayProfit(day), 0))};
      return cachedArchive;
    }

    // The header uses 24 records; the 24H/48H summary uses 25/49 records.
    // Daily groups are anchored at the FIRST archived timestamp, as in 1.7.2.
    function profits(history, includeEmptyCurrent = false) {
      const base = archive(history.entries);
      const revenue = number(history.currentHourRevenue);
      const expenses = number(history.currentHourExpenses);
      const timestamp = number(history.lastHourTimestamp);
      if (timestamp < 0 || (history.entries.at(-1)?.timestamp ?? -1) > timestamp) {
        throw new Error('Unsupported current financial period');
      }
      const included = includeEmptyCurrent || revenue > 0 || expenses > 0;
      const currentExpenses = operating(history.currentHourExpenseCategories);
      const current = included ? revenue - currentExpenses : 0;
      const tail = count => base.tails[Math.min(count - Number(included), base.tails.length - 1)];
      const days = base.days.map(day => ({...day, categories: {...day.categories}}));
      let allDays = base.allDays;
      if (included) {
        const day = Math.floor((timestamp - base.start) / DAY) + 1;
        if (days.at(-1)?.day === day) {
          addDay(days.at(-1), revenue, history.currentHourExpenseCategories);
          allDays = base.earlierDays + dayProfit(days.at(-1));
        } else {
          const latest = addDay({day, revenue: 0, categories: {}}, revenue, history.currentHourExpenseCategories);
          days.push(latest); allDays += dayProfit(latest);
        }
      }
      return {header: number((tail(24).revenue + (included ? revenue : 0)) -
          (tail(24).expenses + (included ? currentExpenses : 0))),
        hourly: number(tail(25).profit + current), '48hours': number(tail(49).profit + current),
        daily: number(days.slice(-14).reduce((sum, day) => sum + dayProfit(day), 0)),
        'all-time': number(allDays)};
    }

    function normalizeHour(state) {
      const history = state.financialHistory;
      const elapsed = number(state.timeConfig.elapsedSeconds);
      const hour = Math.floor(elapsed / HOUR) * HOUR;
      if (elapsed < 0 || history.lastHourTimestamp > hour) throw new Error('Financial clock mismatch');
      if (hour === history.lastHourTimestamp) return history;
      // Archive the old open bucket exactly as the game does, without editing
      // an existing entry or distributing the grant backwards through history.
      const entries = history.entries.at(-1)?.timestamp === history.lastHourTimestamp
        ? history.entries : [...history.entries, {
          timestamp: history.lastHourTimestamp, balance: state.money,
          hourlyRevenue: history.currentHourRevenue, hourlyExpenses: history.currentHourExpenses,
          expenseCategories: {...history.currentHourExpenseCategories}
        }];
      return {...history, entries, lastHourTimestamp: hour, currentHourRevenue: 0,
        currentHourExpenses: 0, currentHourExpenseCategories: {}};
    }

    function eligible(state) {
      return state.gameMode === 'sandbox' && state.saveCheckPending === false &&
        !!state.cityCode && !state.challengeData && (options.isActive?.() ?? true);
    }
    function stop() {
      unsubscribe?.(); unsubscribe = null;
      cachedEntries = null; cachedArchive = null;
    }
    function reconcile() {
      if (disposed || failed || writing) return;
      try {
        const state = store.getState();
        if (!eligible(state)) return;
        number(state.money);
        const before = profits(state.financialHistory);
        const history = normalizeHour(state);
        const projected = profits(history, true);
        if (Math.min(...Object.values(before), ...Object.values(projected)) >= 1) return;
        // Whole-dollar rounding avoids repeated grants caused by tiny float
        // residuals. The injected bucket must also be visible to the dashboard.
        const amount = number(Math.max(1, Math.ceil(1 - Math.min(...Object.values(projected))),
          Math.ceil(1 - history.currentHourRevenue)));
        const nextHistory = {...history, currentHourRevenue: number(history.currentHourRevenue + amount)};
        const money = number(state.money + amount);
        if (Math.min(...Object.values(profits(nextHistory))) < 1) {
          throw new Error('Subsidy cannot be represented accurately');
        }
        writing = true;
        try {
          // One Zustand update; never call revenue/expense/route actions.
          store.setState({money, financialHistory: nextHistory});
          injections++; totalInjected += amount;
        } finally { writing = false; }
      } catch (error) {
        failed = true; lastError = String(error.message || error); stop();
        options.onError?.(lastError);
      }
    }
    function changed(state, previous) {
      if (!previous || state.financialHistory !== previous.financialHistory || state.money !== previous.money ||
          state.gameMode !== previous.gameMode || state.cityCode !== previous.cityCode ||
          state.saveCheckPending !== previous.saveCheckPending || state.challengeData !== previous.challengeData ||
          Math.floor(state.timeConfig?.elapsedSeconds / HOUR) !== Math.floor(previous.timeConfig?.elapsedSeconds / HOUR)) {
        reconcile();
      }
    }
    return {
      start() {
        if (disposed || failed) return;
        if (!unsubscribe) unsubscribe = store.subscribe(changed);
        reconcile();
      },
      stop,
      dispose() { disposed = true; stop(); cachedEntries = null; cachedArchive = null; },
      getState: () => ({listening: !!unsubscribe, disposed, failed, injections, totalInjected, lastError})
    };
  }
  root.createSandboxBalanceSupport = createController;
})(globalThis);
