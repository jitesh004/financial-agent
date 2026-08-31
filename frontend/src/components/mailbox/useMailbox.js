import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../lib';

/* The mailbox import, as a state machine driven by the server.
 *
 * The wizard this replaces held its progress in a promise chain:
 *
 *     const { job_id } = await api.gmailScan(...);
 *     const done = await pollJob(job_id, setJob);   // <- blocks here
 *     setRows(done.result.attachments);
 *
 * That works exactly as long as nobody navigates away. Unmount the component
 * and the await never resolves into anything: the server finished the scan,
 * the results existed, and the UI had thrown away the only reference to them.
 *
 * Here nothing is awaited across a stage. A job id is written down, an effect
 * polls it, and the STAGE IS DERIVED from what the server says that job is
 * doing. Closing the modal, switching tabs, reloading the page or restarting
 * the API all resolve back to the same stage, because none of them is where
 * the state lives.
 */

const KEY = 'fa-mailbox';
const KINDS = new Set(['scan', 'download', 'process', 'alerts']);

/* Poll fast enough to feel live while someone is watching, slowly enough not
   to be silly when nobody is. */
const INTERVAL_WATCHING = 700;
const INTERVAL_BACKGROUND = 3000;
const INTERVAL_IDLE = 15000;

function readStored() {
  try {
    return JSON.parse(localStorage.getItem(KEY)) || {};
  } catch {
    return {};
  }
}

function store(patch) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...readStored(), ...patch }));
  } catch { /* private mode, quota - the server state is the important half */ }
}

export const rowKey = (r) => `${r.message_id}:${r.filename}:${r.size}`;

/* Emails to read for a given look-back window.

   Statement mail runs to a few hundred a year across a dozen institutions, and
   the scan reads newest-first - so the cap, not the date filter, is what really
   decides how far back a scan reaches. Defaulting these together stops the
   most confusing failure mode: choosing "10 years" and still seeing one. */
export function suggestedCap(months) {
  if (months === null) return 5000;   // whole mailbox
  if (months <= 3) return 250;
  if (months <= 12) return 500;
  if (months <= 36) return 1000;
  if (months <= 60) return 2500;
  return 5000;
}

/* One job's kind and status decide the stage. Written as a lookup rather than
   as branches inside the poller so that "what should the UI show?" has exactly
   one answer for any server state, including the ones nobody planned for. */
export function stageFor(job) {
  if (!job) return 'idle';
  if (job.status === 'interrupted') return 'interrupted';
  // A run that stopped early has no result worth showing. Without this,
  // `cancelled` fell through to the finished branches below and a parse the
  // user stopped by hand was presented as a completed import - the one thing
  // the UI must never claim.
  if (job.status === 'failed' || job.status === 'cancelled') {
    return job.kind === 'scan' ? 'idle' : 'select';
  }
  if (job.kind === 'scan') return job.active ? 'scanning' : 'select';
  if (job.kind === 'download') return job.active ? 'downloading' : 'downloaded';
  if (job.kind === 'process') return job.active ? 'processing' : 'done';
  // Alerts skip the download stage entirely: the amount is in the body, so
  // there is nothing to fetch between reading the mail and writing the rows.
  if (job.kind === 'alerts') return job.active ? 'processing' : 'done';
  return 'idle';
}

/* Whether a stopped job needs explaining rather than just clearing. */
export function stoppedNote(job) {
  if (!job) return null;
  if (job.status === 'cancelled') return 'That run was cancelled.';
  if (job.status === 'failed') return job.errors?.join('; ') || 'That run failed.';
  return null;
}

export default function useMailbox({ open, onImported }) {
  const stored = useRef(readStored()).current;

  const [status, setStatus] = useState(null);
  const [periods, setPeriods] = useState([]);
  const [intents, setIntents] = useState([]);
  const [error, setError] = useState(null);

  // Server-owned. `scanJob` is kept separately from `job` because the file
  // list lives in the scan's result and has to survive the download and
  // process jobs that follow it.
  const [job, setJob] = useState(null);
  const [scanJob, setScanJob] = useState(null);
  const [activeCount, setActiveCount] = useState(0);

  // Browser-owned: choices, not results. localStorage is right for these -
  // they are worthless to anyone else and legitimately differ per machine.
  const [selection, setSelection] = useState(() => new Set(stored.selection || []));
  const [months, setMonths] = useState(
    stored.months === undefined ? 12 : stored.months);
  const [maxMessages, setMaxMessages] = useState(
    stored.maxMessages || suggestedCap(stored.months === undefined ? 12 : stored.months));
  const [capTouched, setCapTouched] = useState(Boolean(stored.capTouched));
  const [intent, setIntentState] = useState(stored.intent || 'statement');
  const [ignoredSenders, setIgnoredSenders] = useState([]);

  const refreshStatus = useCallback(
    () => api.gmailStatus().then(setStatus).catch((e) => setError(e.message)), []);

  useEffect(() => { refreshStatus(); }, [refreshStatus]);
  useEffect(() => { api.gmailPeriods().then(setPeriods).catch(() => {}); }, []);
  useEffect(() => { api.gmailIntents().then(setIntents).catch(() => {}); }, []);
  useEffect(() => {
    api.gmailIgnored()
      .then((r) => setIgnoredSenders(r.excluded_senders || []))
      .catch(() => {});
  }, []);

  // ---- the poller ---------------------------------------------------------

  const jobIdRef = useRef(stored.jobId || null);
  const scanIdRef = useRef(stored.scanJobId || null);
  const completedRef = useRef(stored.completedJobId || null);

  const adopt = useCallback((next) => {
    if (!next) return;
    jobIdRef.current = next.id;
    store({ jobId: next.id });
    setJob(next);
    if (next.kind === 'scan') {
      scanIdRef.current = next.id;
      setScanJob(next);
      store({ scanJobId: next.id });
    }
  }, []);

  const tick = useCallback(async () => {
    try {
      // Anything running is adopted even if this browser never started it -
      // a second tab, or a job resumed from somewhere else, is still work in
      // progress and belongs on screen.
      const { jobs: running, active_count: count } = await api.activeJobs();
      setActiveCount(count || 0);
      const live = (running || []).find((one) => KINDS.has(one.kind));

      let current = live;
      if (!current && jobIdRef.current) {
        current = await api.job(jobIdRef.current).catch(() => null);
      }

      if (current) {
        // A finished download hands off to the parse job it already created,
        // so the chain is followed rather than reconstructed.
        const next = !current.active && current.kind === 'download'
          && current.result?.next_job_id;
        if (next) {
          const following = await api.job(current.result.next_job_id).catch(() => null);
          if (following) current = following;
        }
        adopt(current);

        if (current.kind === 'process' && current.status === 'complete'
            && completedRef.current !== current.id) {
          // Fire once per job, not once per poll: the ledger reload behind
          // this is expensive and the job stays complete forever.
          completedRef.current = current.id;
          store({ completedJobId: current.id });
          onImported?.(current.result);
        }
      }

      // The scan's result is the file list, needed long after the scan job
      // has stopped being the current one.
      if (scanIdRef.current && (!current || current.kind !== 'scan')) {
        const scan = await api.job(scanIdRef.current).catch(() => null);
        if (scan) setScanJob(scan);
      }
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [adopt, onImported]);

  const busy = Boolean(job?.active) || activeCount > 0;

  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const loop = async () => {
      if (cancelled) return;
      await tick();
      if (cancelled) return;
      const delay = open ? (busy ? INTERVAL_WATCHING : INTERVAL_BACKGROUND)
        : (busy ? INTERVAL_BACKGROUND : INTERVAL_IDLE);
      timer = setTimeout(loop, delay);
    };
    loop();

    return () => { cancelled = true; clearTimeout(timer); };
  }, [tick, open, busy]);

  // ---- derived ------------------------------------------------------------

  const scanResult = scanJob?.status === 'complete' ? scanJob.result : null;
  const scanIntent = scanResult?.intent || 'statement';

  const rows = useMemo(
    () => (scanResult?.attachments || []), [scanResult]);
  /* Alerts come back already parsed, each carrying the decision the server
     made about it. Only the importable ones can be selected; the rest are
     shown with their reason, because a silent skip is indistinguishable from
     a scan that missed something. */
  const alerts = useMemo(() => (scanResult?.alerts || []), [scanResult]);
  const importableAlerts = useMemo(
    () => alerts.filter((a) => a.status === 'imported'), [alerts]);
  const excluded = useMemo(() => scanResult?.excluded || [], [scanResult]);
  const ignoredCount = scanResult?.ignored_by_rule || 0;
  const summary = job?.kind === 'process' && job.status === 'complete'
    ? job.result : null;

  const stage = useMemo(() => {
    if (!status?.available) return 'setup';
    if (!status?.connected) return 'connect';
    const fromJob = stageFor(job);
    // 'downloaded' means a download finished with nothing chained after it;
    // there is nothing further to watch, so fall back to the file list.
    if (fromJob === 'downloaded') return 'select';
    // A scan that finished but returned nothing is not a selection screen.
    if (fromJob === 'select') {
      const found = scanIntent === 'transactional' ? alerts.length : rows.length;
      return found ? 'select' : 'idle';
    }
    return fromJob;
  }, [status, job, rows.length, alerts.length, scanIntent]);

  // ---- actions ------------------------------------------------------------

  /* Accepts a Set or an updater, because callers legitimately need both: a
     checkbox knows the whole new selection, while the preselect has to look at
     what is already there before deciding.

     Guarding on `typeof next === 'array'` does not work - typeof returns
     'object' for both a Set and an array and never 'array' - so a guard
     written that way makes this a no-op and nothing is ever selected. */
  const persistSelection = useCallback((next) => {
    setSelection((previous) => {
      const resolved = typeof next === 'function' ? next(previous) : next;
      const set = resolved instanceof Set ? resolved : new Set(resolved || []);
      store({ selection: [...set] });
      return set;
    });
  }, []);

  const setIntent = useCallback((next) => {
    setIntentState(next);
    store({ intent: next });
  }, []);

  const startScan = useCallback(async () => {
    setError(null);
    try {
      const { job_id: id } = await api.gmailScan(maxMessages, months, intent);
      // Cleared rather than kept: results from the previous scan would
      // otherwise show under the new one's progress bar.
      scanIdRef.current = id;
      setScanJob(null);
      persistSelection(new Set());
      store({ scanJobId: id, jobId: id });
      jobIdRef.current = id;
      await tick();
    } catch (e) { setError(e.message); }
  }, [maxMessages, months, intent, persistSelection, tick]);

  /* Alerts are written straight from the scan - there is no file to fetch -
     but still only on an explicit action. These are figures nothing has
     checked, and they reach the ledger because someone read the list. */
  const importAlerts = useCallback(async (messageIds) => {
    if (!messageIds.length || !scanIdRef.current) return;
    setError(null);
    try {
      const { job_id: id } = await api.gmailImportAlerts(
        messageIds, scanIdRef.current);
      jobIdRef.current = id;
      store({ jobId: id });
      await tick();
    } catch (e) { setError(e.message); }
  }, [tick]);

  const startImport = useCallback(async (attachments, useLlm = false) => {
    if (!attachments.length) return;
    setError(null);
    try {
      // then_process runs the parse on the server when the download finishes,
      // so closing this modal mid-import no longer strands the files.
      const { job_id: id } = await api.gmailDownload(attachments, {
        thenProcess: true, useLlm,
      });
      jobIdRef.current = id;
      store({ jobId: id });
      await tick();
    } catch (e) { setError(e.message); }
  }, [tick]);

  const cancel = useCallback(async () => {
    if (!job?.id) return;
    try { await api.cancelJob(job.id); } catch { /* best effort */ }
    await tick();
  }, [job, tick]);

  const resume = useCallback(async () => {
    if (!job?.id) return;
    setError(null);
    try {
      const { job_id: id } = await api.resumeJob(job.id);
      jobIdRef.current = id;
      store({ jobId: id });
      await tick();
    } catch (e) { setError(e.message); }
  }, [job, tick]);

  const reset = useCallback(() => {
    jobIdRef.current = null;
    scanIdRef.current = null;
    setJob(null);
    setScanJob(null);
    persistSelection(new Set());
    store({ jobId: null, scanJobId: null });
  }, [persistSelection]);

  const connect = useCallback(async () => {
    setError(null);
    try { await api.gmailConnect(); await refreshStatus(); }
    catch (e) { setError(e.message); }
  }, [refreshStatus]);

  const setLookback = useCallback((next) => {
    setMonths(next);
    store({ months: next });
    if (!capTouched) {
      const cap = suggestedCap(next);
      setMaxMessages(cap);
      store({ maxMessages: cap });
    }
  }, [capTouched]);

  const setCap = useCallback((next) => {
    setMaxMessages(next);
    setCapTouched(true);
    store({ maxMessages: next, capTouched: true });
  }, []);

  const setIgnored = useCallback(async (next) => {
    try {
      await api.gmailSetIgnored(next);
      setIgnoredSenders(next);
    } catch (e) { setError(e.message); }
  }, []);

  return {
    status, periods, intents, error, setError, stage, job, scanJob, busy,
    activeCount, rows, excluded, ignoredCount, summary,
    intent, setIntent, scanIntent, alerts, importableAlerts, importAlerts,
    selection, setSelection: persistSelection,
    months, setLookback, maxMessages, setCap,
    ignoredSenders, setIgnored,
    startScan, startImport, cancel, resume, reset, connect, refresh: tick,
  };
}
