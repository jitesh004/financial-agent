/* The data layer: one cache, shared by every screen.
 *
 * The app this replaces fetched inside components. That is fine until two
 * panels want the same answer, and then it is three problems at once: the
 * request is made twice, the two copies can disagree, and every navigation
 * re-fetches something that has not changed - so moving between Overview and
 * Spending flashed a spinner over figures the browser was already holding.
 *
 * What is here instead is about 150 lines of stale-while-revalidate:
 *
 *   - One entry per key. Two components asking for the same key while a
 *     request is open share that request; they never make a second one.
 *   - A cached answer is returned immediately and refreshed in the background,
 *     so a revisit paints instantly and corrects itself a moment later.
 *   - Refreshing never unmounts what is on screen. `data` stays, `fetching`
 *     goes true, and a panel reporting what a run just did does not lose that
 *     report to the very refresh that proves the run worked.
 *   - Invalidation is by key PREFIX, because a mutation usually invalidates a
 *     family: writing a category has to drop every `txns:` entry, not the one
 *     the current screen happens to hold.
 *
 * Deliberately not a dependency. The whole contract is `useQuery`,
 * `useMutation`, `invalidate` and `prefetch`; a library that does this brings
 * 12KB and a second mental model for the same four ideas.
 */

import { useCallback, useDebugValue, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { api } from './api';

const DEFAULT_STALE = 30_000;

/** key -> { data, error, at, promise, fetching, listeners:Set } */
const cache = new Map();

function entry(key) {
  let e = cache.get(key);
  if (!e) {
    e = { data: undefined, error: null, at: 0, promise: null, fetching: false, listeners: new Set() };
    cache.set(key, e);
  }
  return e;
}

function emit(e) {
  // A new object identity per change, so useSyncExternalStore's snapshot
  // comparison sees it. Mutating in place would render nothing.
  e.snapshot = {
    data: e.data, error: e.error, fetching: e.fetching, at: e.at,
  };
  e.listeners.forEach((fn) => fn());
}

function snapshotOf(e) {
  if (!e.snapshot) {
    e.snapshot = { data: e.data, error: e.error, fetching: e.fetching, at: e.at };
  }
  return e.snapshot;
}

/* Start (or join) a fetch for `key`. Returns the promise, so a caller that
   wants to await the result can, and one that only wants the cache warmed can
   ignore it. */
export function fetchQuery(key, fetcher, { force = false, stale = DEFAULT_STALE } = {}) {
  const e = entry(key);
  if (e.promise) return e.promise;
  const fresh = e.at && Date.now() - e.at < stale;
  if (!force && fresh && e.data !== undefined) return Promise.resolve(e.data);

  e.fetching = true;
  emit(e);

  e.promise = Promise.resolve()
    .then(() => fetcher())
    .then((data) => {
      e.data = data;
      e.error = null;
      e.at = Date.now();
      return data;
    })
    .catch((err) => {
      e.error = err;
      // The previous answer is kept on purpose. A failed refresh should leave
      // the figures that were on screen there, with the error beside them,
      // rather than replacing a correct dashboard with an error page.
      e.at = Date.now();
      throw err;
    })
    .finally(() => {
      e.promise = null;
      e.fetching = false;
      emit(e);
    });

  // Swallowed here so an unhandled rejection is never logged for an error the
  // hook is about to render. Callers that awaited the returned promise still
  // see the rejection.
  e.promise.catch(() => {});
  return e.promise;
}

/* Warm the cache without rendering anything. Used on hover and on focus over
   a navigation item: by the time the click lands, the screen's first request
   has usually already answered. */
export function prefetch(key, fetcher) {
  if (!key) return;
  const e = cache.get(key);
  if (e?.promise || (e?.at && Date.now() - e.at < DEFAULT_STALE)) return;
  fetchQuery(key, fetcher).catch(() => {});
}

/* Drop everything whose key starts with one of these prefixes, and refetch the
   entries something is currently watching. Anything nobody is watching is left
   dropped, to be fetched when a screen next needs it. */
export function invalidate(...prefixes) {
  const list = prefixes.flat().filter(Boolean);
  cache.forEach((e, key) => {
    if (!list.some((p) => key === p || key.startsWith(p))) return;
    e.at = 0;
    if (e.listeners.size && e.refetch) e.refetch();
    else emit(e);
  });
}

/* Write an answer into the cache directly. Used for an optimistic update, and
   for a response that already contains the row a later read would have asked
   for. */
export function setQueryData(key, updater) {
  const e = entry(key);
  e.data = typeof updater === 'function' ? updater(e.data) : updater;
  e.at = Date.now();
  e.error = null;
  emit(e);
}

export function getQueryData(key) {
  return cache.get(key)?.data;
}

export function clearCache() {
  cache.forEach((e) => { e.data = undefined; e.at = 0; e.error = null; emit(e); });
}

/**
 * Read `key`, fetching it if the cache has nothing fresh.
 *
 * Returns `{ data, error, loading, fetching, refetch }`, where `loading` means
 * "there is nothing to show yet" and `fetching` means "a request is open".
 * They are different states and screens want both: the first chooses between a
 * skeleton and content, the second drives a quiet indicator over content that
 * is already there.
 */
export function useQuery(key, fetcher, options = {}) {
  const { enabled = true, stale = DEFAULT_STALE, refetchOnFocus = false } = options;
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const subscribe = useCallback((notify) => {
    if (!key) return () => {};
    const e = entry(key);
    e.listeners.add(notify);
    // Held on the entry so `invalidate` can re-run the right fetcher without
    // knowing which component asked for it.
    e.refetch = () => fetchQuery(key, () => fetcherRef.current(), { force: true });
    return () => {
      e.listeners.delete(notify);
      if (!e.listeners.size) delete e.refetch;
    };
  }, [key]);

  const getSnapshot = useCallback(
    () => (key ? snapshotOf(entry(key)) : EMPTY),
    [key],
  );

  const state = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  useEffect(() => {
    if (!enabled || !key) return;
    fetchQuery(key, () => fetcherRef.current(), { stale }).catch(() => {});
  }, [key, enabled, stale]);

  /* Re-check when the tab comes back, for the screens that ask for it. Off by
     default: most of this data changes only when the person changes it, and a
     burst of requests every time somebody alt-tabs is rude to a server that is
     usually somebody's own laptop. */
  useEffect(() => {
    if (!refetchOnFocus || !enabled || !key) return undefined;
    const onFocus = () => {
      if (document.visibilityState === 'visible') {
        fetchQuery(key, () => fetcherRef.current(), { force: true }).catch(() => {});
      }
    };
    document.addEventListener('visibilitychange', onFocus);
    return () => document.removeEventListener('visibilitychange', onFocus);
  }, [key, enabled, refetchOnFocus]);

  const refetch = useCallback(
    () => (key ? fetchQuery(key, () => fetcherRef.current(), { force: true }) : Promise.resolve()),
    [key],
  );

  useDebugValue(key);

  return {
    data: state.data,
    error: state.error,
    fetching: state.fetching,
    loading: enabled && state.data === undefined && !state.error,
    refetch,
  };
}

const EMPTY = { data: undefined, error: null, fetching: false, at: 0 };

/**
 * Run a write, and say what it invalidates.
 *
 * `run` resolves with the result and rejects with the error, so a caller can
 * still await it; the hook's own `error` is there for the common case of
 * rendering it beside the control that failed.
 */
export function useMutation(fn, { invalidates = [], onSuccess, onError } = {}) {
  const [state, setState] = useState({ busy: false, error: null });
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const invRef = useRef(invalidates);
  invRef.current = invalidates;
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const run = useCallback(async (...args) => {
    setState({ busy: true, error: null });
    try {
      const result = await fnRef.current(...args);
      if (invRef.current.length) invalidate(...invRef.current);
      onSuccess?.(result, ...args);
      if (alive.current) setState({ busy: false, error: null });
      return result;
    } catch (error) {
      if (alive.current) setState({ busy: false, error });
      onError?.(error, ...args);
      throw error;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reset = useCallback(() => setState({ busy: false, error: null }), []);

  return { run, busy: state.busy, error: state.error, reset };
}

/* Poll a job to the end, keeping the latest snapshot in state.
 *
 * Every long-running thing in this app is a job - an import, a rebuild, a
 * model categorisation, an agent run - and they are all watched the same way,
 * so the polling loop is written once. Stops on its own when the job stops,
 * and cancels cleanly if the screen goes away mid-run. */
export function useJobWatch(jobId, { interval = 800, onDone } = {}) {
  const [job, setJob] = useState(null);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => {
    if (!jobId) { setJob(null); return undefined; }
    let live = true;
    let timer = null;
    const poll = async () => {
      const current = await api.job(jobId).catch(() => null);
      if (!live) return;
      setJob(current);
      if (current?.active) {
        timer = setTimeout(poll, interval);
      } else if (current) {
        doneRef.current?.(current);
      }
    };
    poll();
    return () => { live = false; clearTimeout(timer); };
  }, [jobId, interval]);

  return job;
}
