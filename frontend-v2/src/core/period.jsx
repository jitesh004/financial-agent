/* The period every screen is looking at.
 *
 * One piece of state, shared: pick "last 3 months" on Overview and the Ledger,
 * Months and Spending are all answering the same question when you get to
 * them. A per-screen range would mean four screens quietly describing four
 * different periods.
 *
 * What a period MEANS is decided on the server (backend/app/analytics/
 * periods.py) and read from /api/periods, already resolved. This file holds no
 * calendar arithmetic on purpose: "the last 3 months" implemented twice is two
 * answers that eventually differ, and the one that matters is the one the
 * figures were filtered by.
 *
 * The rule those presets follow is the accounting-month rule. A preset selects
 * whole ACCOUNTING months - the month the ledger counts a row in - so a salary
 * paid on 31 August is August's and the next one, paid on 1 September, is
 * September's. Two salaries never land in one month and no month is left
 * empty. A custom window can be either: whole months, which keeps that
 * behaviour, or exact dates, which is the literal reading and says so.
 */

import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from 'react';
import { api } from './api';
import { useQuery } from './store';
import { monthLabelLong } from './format';
import { read, write } from './storage';

const KEY = 'prism-period';

export const ALL_TIME = { preset: 'all' };

/* The presets worth a button of their own. The rest are all reachable from the
   same control. */
const QUICK = ['all', 'this_month', 'last_month', 'last_3m', 'last_6m', 'last_12m'];

export function isCustom(period) {
  return period?.preset === 'custom' || period?.preset === 'custom_months';
}

export function isAllTime(period) {
  if (!period || period.preset === 'all') return true;
  // A custom window with neither end filled in yet is not a filter.
  if (period.preset === 'custom_months') return !(period.start_month || period.end_month);
  if (period.preset === 'custom') return !(period.start || period.end);
  return false;
}

/* What to send with a request. The server resolves a preset itself, so a
   preset travels as its name; only a custom window carries bounds. */
export function periodParams(period) {
  if (!period || isAllTime(period)) return { preset: 'all' };
  if (period.preset === 'custom_months') {
    return {
      preset: 'custom_months',
      start_month: period.start_month || undefined,
      end_month: period.end_month || undefined,
    };
  }
  if (period.preset === 'custom') {
    return {
      preset: 'custom',
      start: period.start || undefined,
      end: period.end || undefined,
    };
  }
  return { preset: period.preset };
}

/* A label for a window the server has not resolved for us - which is only ever
   a custom one, since every preset arrives resolved. */
function customLabel(period) {
  if (period.preset === 'custom_months') {
    const { start_month: first, end_month: last } = period;
    if (first && last) {
      return first === last ? monthLabelLong(first)
        : `${monthLabelLong(first)} – ${monthLabelLong(last)}`;
    }
    if (first) return `${monthLabelLong(first)} onwards`;
    if (last) return `Up to ${monthLabelLong(last)}`;
  }
  if (period.preset === 'custom') {
    if (period.start && period.end) return `${period.start} – ${period.end}`;
    if (period.start) return `From ${period.start}`;
    if (period.end) return `Until ${period.end}`;
  }
  return 'All time';
}

const PeriodContext = createContext(null);

export function PeriodProvider({ children }) {
  /* Remembered per account: which period you were last looking at is a choice,
     and inheriting the previous occupant's is confusing. */
  const [period, setPeriodState] = useState(() => read(KEY, null) || ALL_TIME);

  const { data: catalogue, error: catalogueError, refetch } =
    useQuery('periods', () => api.periods());

  /* The window as the SERVER resolved it, reported back by whichever screen
     last asked for figures (every period-aware response carries `range`).
     Preferred over anything derived here, because it is the window the rows
     were actually selected by - and the only way a hand-drawn window gets a
     month count without this file learning calendar arithmetic. */
  const [reported, setReported] = useState(null);

  const setPeriod = useCallback((next) => {
    const value = next || ALL_TIME;
    setPeriodState(value);
    write(KEY, value);
  }, []);

  /* `key` is the request's own period params, stringified, so a late answer
     for a window that has since changed is ignored rather than relabelling the
     current one. */
  const reportWindow = useCallback((key, range) => {
    if (range) setReported({ key, range });
  }, []);

  const params = useMemo(() => periodParams(period), [period]);
  const paramsKey = JSON.stringify(params);

  const value = useMemo(() => {
    const presets = catalogue?.presets || [];
    const resolved = presets.find((p) => p.value === period.preset);
    const server = reported?.key === paramsKey ? reported.range : null;
    /* Everything a screen needs to describe what it is showing, without having
       to know whether the answer came from a preset, a picker, or the server's
       own reading of either. */
    const win = isAllTime(period) ? null : {
      label: server?.label || (isCustom(period) ? customLabel(period)
        : (resolved?.resolved_label || resolved?.label || period.preset)),
      basis: server?.basis || (period.preset === 'custom' ? 'date' : 'accounting'),
      startMonth: server?.start_month
        || (period.preset === 'custom_months' ? period.start_month : resolved?.start_month),
      endMonth: server?.end_month
        || (period.preset === 'custom_months' ? period.end_month : resolved?.end_month),
      start: server?.start || (period.preset === 'custom' ? period.start : resolved?.start),
      end: server?.end || (period.preset === 'custom' ? period.end : resolved?.end),
      months: server?.months ?? (period.preset === 'custom' ? null : resolved?.months),
    };
    return {
      period,
      setPeriod,
      reportWindow,
      // "All time" is not a filter, and screens branch on that rather than
      // re-deriving it from four possible shapes.
      scoped: !isAllTime(period),
      label: win?.label || 'All time',
      window: win,
      params,
      paramsKey,
      presets,
      quickPresets: presets.filter((p) => QUICK.includes(p.value)),
      months: catalogue?.months || [],
      earliest: catalogue?.earliest || null,
      latest: catalogue?.latest || null,
      catalogueError,
      reloadCatalogue: refetch,
    };
  }, [period, setPeriod, reportWindow, catalogue, catalogueError, refetch, params,
      paramsKey, reported]);

  return <PeriodContext.Provider value={value}>{children}</PeriodContext.Provider>;
}

export function usePeriod() {
  const value = useContext(PeriodContext);
  if (!value) throw new Error('usePeriod must be used inside <PeriodProvider>');
  return value;
}

/* The one request per window, for the whole app.
 *
 * Overview, Spending and Months all read this same answer, so switching
 * between them does not re-ask the same question three times - and cannot get
 * three different answers. */
export function useWindowedAnalysis() {
  const { params, paramsKey, scoped, reportWindow } = usePeriod();
  const q = useQuery(
    scoped ? `analysis:${paramsKey}` : null,
    () => api.analysis(params),
    { enabled: scoped },
  );

  useEffect(() => {
    if (q.data?.range) reportWindow(paramsKey, q.data.range);
  }, [q.data, paramsKey, reportWindow]);

  return q;
}
