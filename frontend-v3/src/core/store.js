/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Data Store & Cache Layer
   Stale-while-revalidate client cache backed by useSyncExternalStore.
   ──────────────────────────────────────────────────────────────────────── */

import { useCallback, useDebugValue, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { api } from './api';

const DEFAULT_STALE = 30_000;

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
      e.at = Date.now();
      throw err;
    })
    .finally(() => {
      e.promise = null;
      e.fetching = false;
      emit(e);
    });

  e.promise.catch(() => {});
  return e.promise;
}

export function prefetch(key, fetcher) {
  if (!key) return;
  const e = cache.get(key);
  if (e?.promise || (e?.at && Date.now() - e.at < DEFAULT_STALE)) return;
  fetchQuery(key, fetcher).catch(() => {});
}

export function invalidate(...prefixes) {
  const list = prefixes.flat().filter(Boolean);
  cache.forEach((e, key) => {
    if (!list.some((p) => key === p || key.startsWith(p))) return;
    e.at = 0;
    if (e.listeners.size && e.refetch) e.refetch();
    else emit(e);
  });
}

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

export function useQuery(key, fetcher, options = {}) {
  const { enabled = true, stale = DEFAULT_STALE, refetchOnFocus = false } = options;
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const subscribe = useCallback((notify) => {
    if (!key) return () => {};
    const e = entry(key);
    e.listeners.add(notify);
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
  }, [onSuccess, onError]);

  const reset = useCallback(() => setState({ busy: false, error: null }), []);

  return { run, busy: state.busy, error: state.error, reset };
}

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

export function useDebounced(value, delay = 250) {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    if (value === settled) return undefined;
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);

  return settled;
}
