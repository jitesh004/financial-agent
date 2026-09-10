/* ────────────────────────────────────────────────────────────────────────────
   Ledger hooks: useDashboard, useViewData, useAccounts, useReviewCount
   ──────────────────────────────────────────────────────────────────────── */

import { useMemo } from 'react';
import { api } from './api';
import { useQuery } from './store';
import { usePeriod, useWindowedAnalysis } from './period';

export function useDashboard() {
  const q = useQuery('dashboard', () => api.dashboard());
  return {
    ...q,
    data: q.data?.status === 'ok' ? q.data : null,
    stale: q.data?.status === 'stale' ? q.data.message : null,
    hasLedger: Boolean(q.data?.analysis?.totals),
  };
}

export function useViewData() {
  const dash = useDashboard();
  const { scoped } = usePeriod();
  const windowed = useWindowedAnalysis();

  const data = useMemo(() => {
    if (!dash.data) return null;
    if (!scoped || !windowed.data?.analysis) return { ...dash.data, range: null };
    return {
      ...dash.data,
      analysis: windowed.data.analysis,
      range: windowed.data.range,
      available: windowed.data.available,
    };
  }, [dash.data, scoped, windowed.data]);

  return {
    data,
    hasLedger: dash.hasLedger,
    stale: dash.stale,
    loading: dash.loading,
    windowEmpty: scoped && Boolean(windowed.data)
      && !windowed.data.analysis?.totals?.transaction_count,
    windowing: windowed.fetching,
    error: dash.error || windowed.error,
    refetch: dash.refetch,
  };
}

export function useAccounts() {
  return useQuery('accounts', () => api.accounts());
}

export function useCategories() {
  return useQuery('categories', () => api.categories());
}

export function useReviewCount() {
  const { data } = useQuery('workflow', () => api.workflow());
  return data?.counts?.needs_review || 0;
}

export function useWorkflow() {
  return useQuery('workflow', () => api.workflow());
}
