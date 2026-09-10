/* ────────────────────────────────────────────────────────────────────────────
   Period Context: Global Accounting & Calendar Windows
   ──────────────────────────────────────────────────────────────────────── */

import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from 'react';
import { api } from './api';
import { useQuery } from './store';
import { monthLabelLong } from './format';
import { read, write } from './storage';

const KEY = 'prism-v3-period';

export const ALL_TIME = { preset: 'all' };

const QUICK = ['all', 'this_month', 'last_month', 'last_3m', 'last_6m', 'last_12m'];

export function isCustom(period) {
  return period?.preset === 'custom' || period?.preset === 'custom_months';
}

export function isAllTime(period) {
  if (!period || period.preset === 'all') return true;
  if (period.preset === 'custom_months') return !(period.start_month || period.end_month);
  if (period.preset === 'custom') return !(period.start || period.end);
  return false;
}

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
  const [period, setPeriodState] = useState(() => read(KEY, null) || ALL_TIME);
  const { data: catalogue, error: catalogueError, refetch } =
    useQuery('periods', () => api.periods());
  const [reported, setReported] = useState(null);

  const setPeriod = useCallback((next) => {
    const value = next || ALL_TIME;
    setPeriodState(value);
    write(KEY, value);
  }, []);

  const reportWindow = useCallback((key, range) => {
    if (range) setReported({ key, range });
  }, []);

  const params = useMemo(() => periodParams(period), [period]);
  const paramsKey = JSON.stringify(params);

  const value = useMemo(() => {
    const presets = catalogue?.presets || [];
    const resolved = presets.find((p) => p.value === period.preset);
    const server = reported?.key === paramsKey ? reported.range : null;
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
