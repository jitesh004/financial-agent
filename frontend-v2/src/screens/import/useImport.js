/* The import, as a state machine driven by the server.
 *
 * The wizard this descends from held its progress in a promise chain:
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
 * doing. Closing the wizard, changing screen, reloading the page or restarting
 * the API all resolve back to the same stage, because none of them is where
 * the state lives.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../core/api';
import { read, write } from '../../core/storage';

const KEY = 'prism-import';
const KINDS = new Set(['scan', 'download', 'process', 'alerts', 'stage_parse', 'stage_process']);

/* Poll fast enough to feel live while somebody is watching, slowly enough not
   to be silly when nobody is. */
const WATCHING = 700;
const BACKGROUND = 3000;
/* Closed, with nothing running. One request a minute, whose only job is to
   notice work started somewhere else - another tab, or a job resumed after a
   restart. */
const IDLE = 60000;

function stored() { return read(KEY, {}) || {}; }
function store(patch) { write(KEY, { ...stored(), ...patch }); }

export const rowKey = (r) => `${r.message_id}:${r.filename}:${r.size}`;

/* Emails to read for a look-back window.
 *
 * Statement mail runs to a few hundred a year across a dozen institutions, and
 * the scan reads newest-first - so the cap, not the date filter, is what really
 * decides how far back a scan reaches. Defaulting these together stops the most
 * confusing failure: choosing "10 years" and still seeing one. */
export function suggestedCap(months) {
  if (months === null) return 5000;
  if (months <= 3) return 250;
  if (months <= 12) return 500;
  if (months <= 36) return 1000;
  if (months <= 60) return 2500;
  return 5000;
}

/* One job's kind and status decide the stage. Written as a lookup rather than
   as branches inside the poller so "what should the UI show?" has exactly one
   answer for any server state, including the ones nobody planned for. */
export function stageFor(job) {
  if (!job) return 'idle';
  if (job.status === 'interrupted') return 'interrupted';
  // A run that stopped early has no result worth showing. Without this,
  // `cancelled` fell through to the finished branches below and a parse
  // somebody stopped by hand was presented as a completed import - the one
  // thing this flow must never claim.
  if (job.status === 'failed' || job.status === 'cancelled') {
    return job.kind === 'scan' ? 'idle' : 'select';
  }
  if (job.kind === 'scan') return job.active ? 'scanning' : 'select';
  if (job.kind === 'download') return job.active ? 'downloading' : 'downloaded';
  // Parsing fills the staging area and stops there. It ends on 'staged', not
  // on 'done', because nothing has been added to the ledger yet - and saying
  // done here is exactly the claim this whole flow exists to stop making.
  if (job.kind === 'stage_parse' || job.kind === 'process') return job.active ? 'parsing' : 'staged';
  // Alerts skip the download stage entirely: the amount is in the body, so
  // there is nothing to fetch between reading the mail and staging the rows.
  if (job.kind === 'alerts') return job.active ? 'parsing' : 'staged';
  // The only kind that ends on 'done', because it is the only one that changes
  // what any screen shows.
  if (job.kind === 'stage_process') return job.active ? 'processing' : 'done';
  return 'idle';
}

export default function useImport({ open, onImported }) {
  const initial = useRef(stored()).current;

  const [status, setStatus] = useState(null);
  const [periods, setPeriods] = useState([]);
  const [intents, setIntents] = useState([]);
  const [error, setError] = useState(null);

  // Server-owned. `scanJob` is kept apart from `job` because the file list
  // lives in the scan's result and has to survive the download and process
  // jobs that follow it.
  const [job, setJob] = useState(null);
  const [scanJob, setScanJob] = useState(null);
  const [activeCount, setActiveCount] = useState(0);

  // Browser-owned: choices, not results.
  const [selection, setSelection] = useState(() => new Set(initial.selection || []));
  const [sourceSettings, setSourceSettings] = useState(() => initial.sourceSettings || {});
  const [sourceJobs, setSourceJobs] = useState(() => initial.sourceJobs || {});
  const [sections, setSections] = useState([]);
  const [sourceResults, setSourceResults] = useState({});
  const [ignoredSenders, setIgnoredSenders] = useState([]);

  /* What to look for, as a SET. Statements and credit reports are one errand,
     not two, and making them exclusive meant running the wizard twice to
     answer one question. Stored as an array because a Set does not survive
     JSON. */
  const [chosenIntents, setChosenIntents] = useState(() => {
    const saved = initial.intents || (initial.intent ? [initial.intent] : null);
    return new Set(saved && saved.length ? saved : ['statement']);
  });
  const intent = [...chosenIntents].join(',');

  const refreshStatus = useCallback(
    () => api.gmailStatus().then(setStatus).catch((e) => setError(e.message)), []);

  useEffect(() => { refreshStatus(); }, [refreshStatus]);
  useEffect(() => { api.gmailPeriods().then(setPeriods).catch(() => {}); }, []);
  useEffect(() => { api.gmailIntents().then(setIntents).catch(() => {}); }, []);
  useEffect(() => {
    api.gmailIgnored().then((r) => setIgnoredSenders(r.excluded_senders || [])).catch(() => {});
  }, []);

  /* One answer per source, whoever asks.
   *
   * This used to take the cap as an argument, so the screen that DISPLAYED a
   * source's settings passed the source's own limit while the code that SENT
   * the scan did not - alerts showed "250 emails" and requested 500. A number
   * somebody reads and a number the app uses must come from the same place. */
  const settingsFor = useCallback((key) => {
    const saved = sourceSettings[key] || {};
    const spec = intents.find((one) => one.key === key);
    const ceiling = spec?.max_months ?? null;
    // `ceiling` is this source's DEFAULT window, not a limit on it. Alerts
    // start at two months because a year of unreconciled figures is mostly
    // noise the statements supersede - but that is advice printed next to the
    // control, and a window you set yourself is honoured.
    const months = saved.months === undefined ? (ceiling ?? 12) : saved.months;
    return { months, maxMessages: saved.maxMessages || suggestedCap(months), ceiling };
  }, [sourceSettings, intents]);

  const setSourceSetting = useCallback((key, patch) => {
    setSourceSettings((prev) => {
      const next = { ...prev, [key]: { ...(prev[key] || {}), ...patch } };
      store({ sourceSettings: next });
      return next;
    });
  }, []);

  /* ---- the poller ------------------------------------------------------- */

  const jobIdRef = useRef(initial.jobId || null);
  const scanIdRef = useRef(initial.scanJobId || null);
  const completedRef = useRef(initial.completedJobId || null);
  //: The last job seen in a terminal state, and the scan whose result is
  //: final. Both exist so the poller can stop asking questions whose answers
  //: cannot change.
  const settledRef = useRef(null);
  const settledScanRef = useRef(null);

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

  const importedRef = useRef(onImported);
  importedRef.current = onImported;

  const tick = useCallback(async () => {
    try {
      // Anything running is adopted even if this browser never started it - a
      // second tab, or a job resumed from somewhere else, is still work in
      // progress and belongs on screen.
      const { jobs: running, active_count: n } = await api.activeJobs();
      setActiveCount(n || 0);
      let current = (running || []).find((one) => KINDS.has(one.kind));

      if (!current && jobIdRef.current) {
        /* A finished job never changes again, so it is fetched once and kept.
           Without this the poller re-read the same completed job every tick
           for as long as the app stayed open - and kept 404ing on one that had
           been cleared, forever, because a missing job is not a reason to stop
           asking for a job id nothing ever forgets. */
        const cached = settledRef.current;
        if (cached && cached.id === jobIdRef.current) current = cached;
        else {
          current = await api.job(jobIdRef.current).catch(() => null);
          if (current && !current.active) settledRef.current = current;
          if (!current) { jobIdRef.current = null; store({ jobId: null }); }
        }
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

        if (current.kind === 'stage_process' && current.status === 'complete'
            && completedRef.current !== current.id) {
          // Fired once per job, not once per poll: the ledger reload behind
          // this is expensive and the job stays complete forever.
          completedRef.current = current.id;
          store({ completedJobId: current.id });
          importedRef.current?.(current.result);
        }
      }

      /* The scan's result is the file list, needed long after the scan job has
         stopped being the current one - but a completed scan's result is
         fixed, so it is fetched once. */
      if (scanIdRef.current && (!current || current.kind !== 'scan')
          && settledScanRef.current !== scanIdRef.current) {
        const scan = await api.job(scanIdRef.current).catch(() => null);
        if (scan) {
          setScanJob(scan);
          if (!scan.active) settledScanRef.current = scanIdRef.current;
        } else {
          scanIdRef.current = null;
          store({ scanJobId: null });
        }
      }
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [adopt]);

  const busy = Boolean(job?.active) || activeCount > 0;

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const loop = async () => {
      if (cancelled) return;
      await tick();
      if (cancelled) return;
      const delay = open ? (busy ? WATCHING : BACKGROUND) : (busy ? BACKGROUND : IDLE);
      timer = setTimeout(loop, delay);
    };
    loop();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [tick, open, busy]);

  /* Each source's finished scan result, polled while the wizard is open. */
  useEffect(() => {
    if (!open) return undefined;
    const ids = Object.entries(sourceJobs || {});
    if (!ids.length) return undefined;
    let live = true;
    let timer = null;
    const poll = async () => {
      let stillRunning = false;
      for (const [key, id] of ids) {
        // eslint-disable-next-line no-await-in-loop
        const current = await api.job(id).catch(() => null);
        if (!live) return;
        if (current?.active) { stillRunning = true; continue; }
        if (current?.result) {
          setSourceResults((prev) => (prev[key]?.__id === id ? prev
            : { ...prev, [key]: { ...current.result, __id: id } }));
        }
      }
      if (stillRunning && live) timer = setTimeout(poll, 1200);
    };
    poll();
    return () => { live = false; clearTimeout(timer); };
  }, [open, sourceJobs]);

  const refreshSections = useCallback(async () => {
    try {
      const { sections: next } = await api.stagingSections();
      setSections(next || []);
    } catch { /* a count that fails to load is not worth an error banner */ }
  }, []);

  useEffect(() => { if (open) refreshSections(); }, [open, refreshSections]);

  /* Re-read the per-source counts whenever reading or rebuilding finishes. The
     common path starts a parse from Choose - "Download & read" chains download
     into parse - and nothing else would tell the counts to reload afterwards. */
  const settledWorkRef = useRef(null);
  useEffect(() => {
    if (!job || job.active) return;
    if (!['stage_parse', 'stage_process', 'alerts'].includes(job.kind)) return;
    if (settledWorkRef.current === job.id) return;
    settledWorkRef.current = job.id;
    refreshSections();
  }, [job, refreshSections]);

  /* ---- derived ---------------------------------------------------------- */

  const scanResult = scanJob?.status === 'complete' ? scanJob.result : null;
  const scanIntent = scanResult?.intent || 'statement';

  /* Everything every source's scan turned up, each row tagged with the source
     that found it.
   *
     The sources overlap by design: the statement scan's sender list already
     contains every broker, so a holdings PDF is found by both "Account
     statements" and "Investments". Keeping whichever arrived first meant 108
     investment files were attributed to statements, staged as statements, and
     the Investments section reported nothing at all - while Choose went on
     offering them, because it counted each source's own results and so counted
     those files twice. */
  const rows = useMemo(() => {
    const specificity = {
      statement: 0, upload: 1, bureau: 2, investment: 2, transactional: 2,
    };
    const byId = new Map();
    for (const [key, result] of Object.entries(sourceResults || {})) {
      for (const row of result?.attachments || []) {
        const id = rowKey(row);
        const rowIntent = row.intent || key;
        const existing = byId.get(id);
        if (existing && (specificity[existing.intent] ?? 0) >= (specificity[rowIntent] ?? 0)) {
          continue;
        }
        byId.set(id, { ...row, intent: rowIntent });
      }
    }
    if (byId.size) return [...byId.values()];
    // Nothing per-source yet (a scan started from the older single-scan path).
    return scanResult?.attachments || [];
  }, [sourceResults, scanResult]);

  const alerts = useMemo(() => scanResult?.alerts || [], [scanResult]);
  const importableAlerts = useMemo(
    () => alerts.filter((a) => a.status === 'imported'), [alerts]);
  const excluded = useMemo(() => scanResult?.excluded || [], [scanResult]);
  const ignoredCount = scanResult?.ignored_by_rule || 0;

  const summary = (['stage_parse', 'alerts', 'stage_process', 'process'].includes(job?.kind)
    && job.status === 'complete') ? job.result : null;

  const stage = useMemo(() => {
    if (!status?.available) return 'setup';
    if (!status?.connected) return 'connect';
    const fromJob = stageFor(job);
    // 'downloaded' means a download finished with nothing chained after it;
    // there is nothing further to watch, so fall back to the file list.
    if (fromJob === 'downloaded') return 'select';
    if (fromJob === 'select') {
      const found = scanIntent === 'transactional' ? alerts.length : rows.length;
      return found ? 'select' : 'idle';
    }
    return fromJob;
  }, [status, job, rows.length, alerts.length, scanIntent]);

  /* ---- actions ---------------------------------------------------------- */

  /* Accepts a Set or an updater, because callers legitimately need both: a
     checkbox knows the whole new selection, while the preselect has to look at
     what is already there before deciding. */
  const persistSelection = useCallback((next) => {
    setSelection((prev) => {
      const resolved = typeof next === 'function' ? next(prev) : next;
      const set = resolved instanceof Set ? resolved : new Set(resolved || []);
      store({ selection: [...set] });
      return set;
    });
  }, []);

  /* Never empty: a scan for nothing is not a state worth being able to reach,
     so turning the last source off is refused rather than producing a Scan
     button that cannot do anything. */
  const toggleIntent = useCallback((key) => {
    setChosenIntents((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        if (next.size === 1) return prev;
        next.delete(key);
      } else next.add(key);
      store({ intents: [...next] });
      return next;
    });
  }, []);

  /* Alerts are staged from the ALERT source's own result.
   *
   * This used to read the single most recently adopted scan, which stopped
   * being the alert scan the moment another source was scanned after it.
   * Scanning alerts and then statements left the alerts staged nowhere. */
  const stagedAlertsRef = useRef(null);
  useEffect(() => {
    const result = sourceResults?.transactional;
    const alertRows = result?.alerts;
    if (!result?.__id || !alertRows?.length) return;
    if (stagedAlertsRef.current === result.__id) return;
    stagedAlertsRef.current = result.__id;
    api.stageScanResults({
      files: [],
      /* Staged on whether the alert was UNDERSTOOD, not on whether an account
         matched. "No account here ends 4345" is a statement about the ledger,
         and in this flow the ledger may not have been built yet - the
         statement that would create that account can be two steps away. */
      alerts: alertRows.filter((a) => a.amount && a.date_iso && a.account_suffix),
    })
      .then(() => refreshSections())
      .catch(() => { stagedAlertsRef.current = null; });
  }, [sourceResults, refreshSections]);

  /* Scan ONE source. Nothing else is touched, which is what makes the Retry
     button in each section mean "just this one". */
  const scanSource = useCallback(async (key) => {
    setError(null);
    const { months, maxMessages } = settingsFor(key);
    try {
      const { job_id: id } = await api.gmailScan(maxMessages, months, key);
      setSourceJobs((prev) => {
        const next = { ...prev, [key]: id };
        store({ sourceJobs: next });
        return next;
      });
      settledRef.current = null;
      settledScanRef.current = null;
      jobIdRef.current = id;
      scanIdRef.current = id;
      store({ jobId: id, scanJobId: id });
      await tick();
      return id;
    } catch (e) { setError(e.message); return null; }
  }, [settingsFor, tick]);

  /* Read ONE source's staged files. Same reasoning as scanSource. */
  const parseSource = useCallback(async (key) => {
    setError(null);
    try {
      const { job_id: id } = await api.stagingParse(key);
      if (id) {
        jobIdRef.current = id;
        settledRef.current = null;
        store({ jobId: id });
        await tick();
      }
      await refreshSections();
      return id;
    } catch (e) { setError(e.message); return null; }
  }, [tick, refreshSections]);

  const startImport = useCallback(async (attachments, useLlm = false) => {
    if (!attachments.length) return;
    setError(null);
    try {
      // then_process runs the parse on the server when the download finishes,
      // so closing this wizard mid-import no longer strands the files.
      const { job_id: id } = await api.gmailDownload(attachments, {
        thenProcess: true, useLlm,
      });
      jobIdRef.current = id;
      settledRef.current = null;
      store({ jobId: id });
      await tick();
    } catch (e) { setError(e.message); }
  }, [tick]);

  const process = useCallback(async () => {
    setError(null);
    try {
      await api.stagingProcess();
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
      settledRef.current = null;
      store({ jobId: id });
      await tick();
    } catch (e) { setError(e.message); }
  }, [job, tick]);

  const reset = useCallback(() => {
    jobIdRef.current = null;
    scanIdRef.current = null;
    settledRef.current = null;
    settledScanRef.current = null;
    setJob(null);
    setScanJob(null);
    persistSelection(new Set());
    store({ jobId: null, scanJobId: null });
  }, [persistSelection]);

  const forgetAll = useCallback(async () => {
    await api.stagingForget();
    reset();
    await refreshSections();
  }, [reset, refreshSections]);

  const forgetSource = useCallback(async (key) => {
    await api.stagingForget(key);
    await refreshSections();
  }, [refreshSections]);

  const setIgnored = useCallback(async (next) => {
    try {
      await api.gmailSetIgnored(next);
      setIgnoredSenders(next);
    } catch (e) { setError(e.message); }
  }, []);

  return {
    status, periods, intents, error, setError, stage, job, scanJob, busy, activeCount,
    rows, excluded, ignoredCount, summary, alerts, importableAlerts,
    intent, chosenIntents, toggleIntent, scanIntent,
    selection, setSelection: persistSelection,
    ignoredSenders, setIgnored,
    sections, refreshSections, sourceJobs, sourceResults,
    settingsFor, setSourceSetting,
    scanSource, parseSource, startImport, process,
    cancel, resume, reset, forgetAll, forgetSource,
    refresh: tick, refreshStatus,
    connect: () => api.gmailConnect(),
  };
}
