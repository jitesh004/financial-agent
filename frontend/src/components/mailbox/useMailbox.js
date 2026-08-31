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
const KINDS = new Set(['scan', 'download', 'process', 'alerts',
  'stage_parse', 'stage_process']);

/* Poll fast enough to feel live while someone is watching, slowly enough not
   to be silly when nobody is. */
const INTERVAL_WATCHING = 700;
const INTERVAL_BACKGROUND = 3000;
/* Closed, with nothing running. One request a minute, and its only job is to
   notice work started somewhere else - another tab, or a job resumed after a
   restart. It used to be every 15 seconds, and it used to be three requests
   rather than one. */
const INTERVAL_IDLE = 60000;

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
  // Parsing fills the staging area and stops there. It ends on 'staged', not
  // on 'done', because nothing has been added to the ledger yet - saying done
  // here is exactly the claim this whole flow exists to stop making.
  if (job.kind === 'stage_parse') return job.active ? 'parsing' : 'staged';
  if (job.kind === 'process') return job.active ? 'parsing' : 'staged';
  // Alerts skip the download stage entirely: the amount is in the body, so
  // there is nothing to fetch between reading the mail and staging the rows.
  if (job.kind === 'alerts') return job.active ? 'parsing' : 'staged';
  // The only kind that ends on 'done', because it is the only one that
  // changes what any tab shows.
  if (job.kind === 'stage_process') return job.active ? 'processing' : 'done';
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

  /* Per-source scan settings, and the scan each one last ran.
   *
   * One shared "look back" was wrong for every source at once: a holdings
   * statement is a photograph of what you own on one date, so last quarter's
   * is history, while a bank statement from the same month is money still to
   * be accounted for - and alerts are capped at two months whatever anyone
   * asks for. One setting could only ever be right for one of them.
   *
   * Each source also keeps its own job id, which is what makes a per-source
   * Retry possible: re-scanning alerts must not disturb the statement scan
   * sitting beside it. */
  const [sourceSettings, setSourceSettings] = useState(
    () => stored.sourceSettings || {});
  const [sourceJobs, setSourceJobs] = useState(() => stored.sourceJobs || {});
  const [sections, setSections] = useState([]);
  /* Each source's finished scan result, kept here rather than fetched inside
     the section that shows it.
   *
     Two things need them and must agree: the Choose screen lists what was
     found, and the footer's "Download & read" button counts what is ticked.
     With each section fetching its own copy, the footer was reading the LAST
     scan's rows only - so ticking anything in another section left the button
     disabled over a perfectly good selection. */
  const [sourceResults, setSourceResults] = useState({});

  /* One answer per source, whoever asks.
   *
   * This used to take the cap as an argument, so the screen that DISPLAYED a
   * source's settings passed the source's own limit while the code that SENT
   * the scan did not - alerts showed "250 emails" and requested 500. A number
   * the user reads and a number the app uses must come from the same place. */
  const settingsFor = useCallback((key) => {
    const saved = sourceSettings[key] || {};
    const spec = intents.find((one) => one.key === key);
    const ceiling = spec?.max_months ?? null;
    // `ceiling` is this source's DEFAULT window, not a limit on it. Alerts
    // start at two months because a year of unreconciled figures is mostly
    // noise the statements supersede - but that is advice printed next to the
    // control, and a window you set yourself is honoured.
    const months = saved.months === undefined ? (ceiling ?? 12) : saved.months;
    return {
      months,
      maxMessages: saved.maxMessages || suggestedCap(months),
      ceiling,
    };
  }, [sourceSettings, intents]);

  const setSourceSetting = useCallback((key, patch) => {
    setSourceSettings((previous) => {
      const next = { ...previous, [key]: { ...(previous[key] || {}), ...patch } };
      store({ sourceSettings: next });
      return next;
    });
  }, []);
  /* What to look for, as a SET. Statements and credit reports are one errand,
     not two, and making them exclusive meant running the wizard twice to
     answer one question. Stored as an array because a Set does not survive
     JSON. */
  const [chosenIntents, setChosenIntents] = useState(() => {
    const saved = stored.intents || (stored.intent ? [stored.intent] : null);
    return new Set(saved && saved.length ? saved : ['statement']);
  });
  const intent = [...chosenIntents].join(',');
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
        /* A finished job never changes again, so it is fetched once and kept.
           Without this the poller re-read the same completed job every tick
           for as long as the app stayed open - and kept 404ing on one that had
           been cleared, forever, because a missing job is not a reason to stop
           asking for a job id nothing ever forgets. */
        const cached = settledRef.current;
        if (cached && cached.id === jobIdRef.current) {
          current = cached;
        } else {
          current = await api.job(jobIdRef.current).catch(() => null);
          if (current && !current.active) settledRef.current = current;
          if (!current) {
            // It is gone. Stop asking.
            jobIdRef.current = null;
            store({ jobId: null });
          }
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
          // Fire once per job, not once per poll: the ledger reload behind
          // this is expensive and the job stays complete forever.
          completedRef.current = current.id;
          store({ completedJobId: current.id });
          onImported?.(current.result);
        }
      }

      /* The scan's result is the file list, needed long after the scan job has
         stopped being the current one - but a completed scan's result is
         fixed, so it is fetched once. This was the second of three requests
         the poller made every tick with nothing running. */
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
  }, [adopt, onImported]);

  const busy = Boolean(job?.active) || activeCount > 0;

  /* Opening the modal used to kick off an alert scan on its own. Removed at
     the user's request: a scan reads the mailbox and costs time, and starting
     one because a window opened is the app deciding to do work nobody asked
     for. Alerts are scanned when Transaction alerts is ticked on Source and
     Scan is pressed, like every other source. */

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

  /* Alerts arrive fully parsed inside the scan's own result - there is no file
     to download, so nothing else in the chain would ever put them in staging.
     Staged once per scan, keyed on the scan's id. */

  const scanIntent = scanResult?.intent || 'statement';

  /* Everything every source's scan turned up, each row tagged with the source
     that found it. The footer counts a selection made anywhere against this,
     which is what makes ticking a file in one section enable the button. */
  const rows = useMemo(() => {
    /* One row per document, attributed to the MOST SPECIFIC scan that found
       it.
     *
     * The sources overlap by design: the statement scan's sender list already
     * contains every broker, so a Zerodha holdings PDF is found by both
     * "Account statements" and "Investments". Keeping whichever arrived first
     * meant 108 investment files were attributed to statements, staged as
     * statements, and the Investments section reported nothing at all - while
     * Choose went on offering them, because it counted each source's own
     * results and so counted those files twice. */
    const specificity = { statement: 0, upload: 1, bureau: 2, investment: 2,
                          transactional: 2 };
    const byId = new Map();
    for (const [key, result] of Object.entries(sourceResults || {})) {
      for (const row of result?.attachments || []) {
        const id = `${row.message_id}:${row.filename}:${row.size}`;
        const intent = row.intent || key;
        const existing = byId.get(id);
        if (existing
            && (specificity[existing.intent] ?? 0) >= (specificity[intent] ?? 0)) {
          continue;
        }
        byId.set(id, { ...row, intent });
      }
    }
    if (byId.size) return [...byId.values()];
    // Nothing per-source yet (a scan started from the old single-scan path).
    return scanResult?.attachments || [];
  }, [sourceResults, scanResult]);
  /* Alerts come back already parsed, each carrying the decision the server
     made about it. Only the importable ones can be selected; the rest are
     shown with their reason, because a silent skip is indistinguishable from
     a scan that missed something. */
  const alerts = useMemo(() => (scanResult?.alerts || []), [scanResult]);
  const importableAlerts = useMemo(
    () => alerts.filter((a) => a.status === 'imported'), [alerts]);
  const excluded = useMemo(() => scanResult?.excluded || [], [scanResult]);
  const ignoredCount = scanResult?.ignored_by_rule || 0;
  /* An alerts run finishes with a result too, and excluding it here meant the
     screen that reports what an import did had nothing to report after one -
     it fell through to "that import produced no statements", which was both
     wrong and the opposite of what had just happened. */
  const summary = (job?.kind === 'stage_parse' || job?.kind === 'alerts'
    || job?.kind === 'stage_process' || job?.kind === 'process')
    && job.status === 'complete' ? job.result : null;

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

  /* Toggling one source on or off. Never empty: a scan for nothing is not a
     state worth being able to reach, so turning the last one off is refused
     rather than producing a Scan button that cannot do anything. */
  const toggleIntent = useCallback((key) => {
    setChosenIntents((previous) => {
      const next = new Set(previous);
      if (next.has(key)) {
        if (next.size === 1) return previous;
        next.delete(key);
      } else {
        next.add(key);
      }
      store({ intents: [...next] });
      return next;
    });
  }, []);


  useEffect(() => {
    if (!open) return undefined;
    let live = true;
    const ids = Object.entries(sourceJobs || {});
    if (!ids.length) return undefined;
    let timer = null;
    const poll = async () => {
      let stillRunning = false;
      for (const [key, id] of ids) {
        // eslint-disable-next-line no-await-in-loop
        const current = await api.job(id).catch(() => null);
        if (!live) return;
        if (current?.active) { stillRunning = true; continue; }
        if (current?.result) {
          setSourceResults((previous) => (
            previous[key]?.__id === id ? previous
              : { ...previous, [key]: { ...current.result, __id: id } }));
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

  /* Re-read the per-source counts whenever reading or rebuilding finishes.
   *
   * The Parse step refreshed only after a run IT started, but the common path
   * starts one from Choose - "Download & read" chains download -> parse - and
   * nothing told the counts to reload afterwards. The screen went on showing
   * the numbers from before the download: 146 statements and no investments,
   * over a staging area that by then held 39 and 107. */
  const settledWorkRef = useRef(null);
  useEffect(() => {
    if (!job || job.active) return;
    if (!['stage_parse', 'stage_process', 'alerts'].includes(job.kind)) return;
    if (settledWorkRef.current === job.id) return;
    settledWorkRef.current = job.id;
    refreshSections();
  }, [job, refreshSections]);

  /* Alerts are staged from the ALERT source's own result.
   *
   * This used to read `scanJob` - the single most recently adopted scan -
   * which stopped being the alert scan the moment a source was scanned after
   * it. Scanning alerts and then statements left the alerts staged nowhere:
   * the Choose step offered 113 of them, ticking made no difference, and
   * Parse reported the source empty. Each source keeps its own result now, so
   * this reads the one it actually needs. */
  const stagedAlertsRef = useRef(null);
  useEffect(() => {
    const result = sourceResults?.transactional;
    const alertRows = result?.alerts;
    if (!result?.__id || !alertRows?.length) return;
    if (stagedAlertsRef.current === result.__id) return;
    stagedAlertsRef.current = result.__id;
    api.stageScanResults({
      files: [],
      /* Staged on whether the alert was UNDERSTOOD, not on whether an
         account matched. "No account here ends 4345" is a statement about
         the ledger, and in this flow the ledger may not have been built yet -
         the statement that would create that account can be two steps away.
         Matching happens when the ledger is built, where there is something
         to match against. */
      alerts: alertRows.filter(
        (a) => a.amount && a.date_iso && a.account_suffix),
    })
      .then(() => refreshSections())
      .catch(() => { stagedAlertsRef.current = null; });
  }, [sourceResults, refreshSections]);

  /* Scan ONE source. Nothing else is touched, which is what makes the Retry
     button in each section mean "just this one" - re-reading alerts must not
     disturb the statement scan sitting beside it. */
  const scanSource = useCallback(async (key) => {
    setError(null);
    const { months: m, maxMessages: cap } = settingsFor(key);
    try {
      const { job_id: id } = await api.gmailScan(cap, m, key);
      setSourceJobs((previous) => {
        const next = { ...previous, [key]: id };
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

  const startScan = useCallback(async () => {
    setError(null);
    try {
      const { job_id: id } = await api.gmailScan(maxMessages, months, intent);
      // Cleared rather than kept: results from the previous scan would
      // otherwise show under the new one's progress bar.
      scanIdRef.current = id;
      settledRef.current = null;
      settledScanRef.current = null;
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
    intent, chosenIntents, toggleIntent, scanIntent,
    alerts, importableAlerts, importAlerts,
    selection, setSelection: persistSelection,
    months, setLookback, maxMessages, setCap,
    ignoredSenders, setIgnored,
    sections, refreshSections, sourceJobs, sourceResults,
    settingsFor, setSourceSetting,
    scanSource, parseSource,
    startScan, startImport, cancel, resume, reset, connect, refresh: tick,
  };
}
