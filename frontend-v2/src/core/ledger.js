/* The ledger, as the screens read it.
 *
 * Two requests, deliberately kept apart.
 *
 * `/api/dashboard` is the whole-ledger payload: accounts, loans, the forecast,
 * the transfer report and the narrative. None of those are period-scoped
 * facts - a balance is as-of, an amortization runs forward, and prose is
 * written once when the statements are parsed.
 *
 * `/api/analysis?preset=…` is the arithmetic, recomputed server-side for one
 * window.
 *
 * `useViewData` merges them the only way that is honest: the window REPLACES
 * `analysis` and nothing else. The narrative is passed through untouched and
 * labelled where it is shown, rather than being silently re-titled as if a
 * model had written about this window.
 */

import { useMemo } from 'react';
import { api } from './api';
import { useQuery } from './store';
import { usePeriod, useWindowedAnalysis } from './period';

export function useDashboard() {
  const q = useQuery('dashboard', () => api.dashboard());
  return {
    ...q,
    // `status: 'stale'` is the server saying "here is what I have, but it may
    // be behind" - a message worth showing rather than an error.
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
    // A window with nothing in it, as opposed to a ledger with nothing in it.
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

/* The Review badge, re-counted rather than counted once. Clearing an item from
   the Review screen changes this number, and a badge that goes on saying 96
   while the queue empties beneath it is a number on the navigation that
   disagrees with the screen it points at. */
export function useReviewCount() {
  const { data } = useQuery('workflow', () => api.workflow());
  return data?.counts?.needs_review || 0;
}

export function useWorkflow() {
  return useQuery('workflow', () => api.workflow());
}
