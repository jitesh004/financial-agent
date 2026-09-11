import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../core/api';
import { read, write } from '../../core/storage';

const KEY = 'prism-import';
const KINDS = new Set(['scan', 'download', 'process', 'alerts', 'stage_parse', 'stage_process']);

const WATCHING = 700;
const BACKGROUND = 3000;
const IDLE = 60000;

function stored() { return read(KEY, {}) || {}; }
function store(patch) { write(KEY, { ...stored(), ...patch }); }

export const rowKey = (r) => `${r.message_id}:${r.filename}:${r.size}`;

export function suggestedCap(months) {
  if (months === null) return 5000;
  if (months <= 3) return 250;
  if (months <= 12) return 500;
  if (months <= 36) return 1000;
  if (months <= 60) return 2500;
  return 5000;
}

export function stageFor(job) {
  if (!job) return 'idle';
  if (job.status === 'interrupted') return 'interrupted';
  if (job.status === 'failed' || job.status === 'cancelled') {
    return job.kind === 'scan' ? 'idle' : 'select';
  }
  if (job.kind === 'scan') return job.active ? 'scanning' : 'select';
  if (job.kind === 'download') return job.active ? 'downloading' : 'downloaded';
  if (job.kind === 'stage_parse' || job.kind === 'process') return job.active ? 'parsing' : 'staged';
  if (job.kind === 'alerts') return job.active ? 'parsing' : 'staged';
  if (job.kind === 'stage_process') return job.active ? 'processing' : 'done';
  return 'idle';
}

export default function useImport({ open, onImported }) {
  const initial = useRef(stored()).current;

  const [status, setStatus] = useState(null);
  const [periods, setPeriods] = useState([]);
  const [intents, setIntents] = useState([]);
  const [error, setError] = useState(null);

  const [job, setJob] = useState(null);
  const [scanJob, setScanJob] = useState(null);
  const [activeCount, setActiveCount] = useState(0);

  const [selection, setSelection] = useState(() => new Set(initial.selection || []));
  const [sourceSettings, setSourceSettings] = useState(() => initial.sourceSettings || {});
  const [sourceJobs, setSourceJobs] = useState(() => initial.sourceJobs || {});
  const [sections, setSections] = useState([]);
  const [sourceResults, setSourceResults] = useState({});
  const [ignoredSenders, setIgnoredSenders] = useState([]);

  const [chosenIntents, setChosenIntents] = useState(() => {
    const saved = initial.intents || (initial.intent ? [initial.intent] : null);
    return new Set(saved && saved.length ? saved : ['statement']);
  });
  const intent = [...chosenIntents].join(',');

  const refreshStatus = useCallback(
    () => api.gmailStatus().then(setStatus).catch((e) => setError(e.message)),
    []
  );

  useEffect(() => { refreshStatus(); }, [refreshStatus]);
  useEffect(() => { api.gmailPeriods().then(setPeriods).catch(() => {}); }, []);
  useEffect(() => { api.gmailIntents().then(setIntents).catch(() => {}); }, []);
  useEffect(() => {
    api.gmailIgnored().then((r) => setIgnoredSenders(r.excluded_senders || [])).catch(() => {});
  }, []);

  const settingsFor = useCallback((key) => {
    const saved = sourceSettings[key] || {};
    const spec = intents.find((one) => one.key === key);
    const ceiling = spec?.max_months ?? null;
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

  const jobIdRef = useRef(initial.jobId || null);
  const scanIdRef = useRef(initial.scanJobId || null);
  const completedRef = useRef(initial.completedJobId || null);
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
      const { jobs: running, active_count: n } = await api.activeJobs();
      setActiveCount(n || 0);
      let current = (running || []).find((one) => KINDS.has(one.kind));

      if (!current && jobIdRef.current) {
        const cached = settledRef.current;
        if (cached && cached.id === jobIdRef.current) current = cached;
        else {
          current = await api.job(jobIdRef.current).catch(() => null);
          if (current && !current.active) settledRef.current = current;
          if (!current) { jobIdRef.current = null; store({ jobId: null }); }
        }
      }

      if (current) {
        const next = !current.active && current.kind === 'download' && current.result?.next_job_id;
        if (next) {
          const following = await api.job(current.result.next_job_id).catch(() => null);
          if (following) current = following;
        }
        adopt(current);

        if (current.kind === 'stage_process' && current.status === 'complete' && completedRef.current !== current.id) {
          completedRef.current = current.id;
          store({ completedJobId: current.id });
          importedRef.current?.(current.result);
        }
      }

      if (scanIdRef.current && (!current || current.kind !== 'scan') && settledScanRef.current !== scanIdRef.current) {
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

  useEffect(() => {
    if (!open) return undefined;
    const ids = Object.entries(sourceJobs || {});
    if (!ids.length) return undefined;
    let live = true;
    let timer = null;
    const poll = async () => {
      let stillRunning = false;
      for (const [key, id] of ids) {
        const current = await api.job(id).catch(() => null);
        if (!live) return;
        if (current?.active) { stillRunning = true; continue; }
        if (current?.result) {
          setSourceResults((prev) => (prev[key]?.__id === id ? prev : { ...prev, [key]: { ...current.result, __id: id } }));
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
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { if (open) refreshSections(); }, [open, refreshSections]);

  const settledWorkRef = useRef(null);
  useEffect(() => {
    if (!job || job.active) return;
    if (!['stage_parse', 'stage_process', 'alerts'].includes(job.kind)) return;
    if (settledWorkRef.current === job.id) return;
    settledWorkRef.current = job.id;
    refreshSections();
  }, [job, refreshSections]);

  const scanResult = scanJob?.status === 'complete' ? scanJob.result : null;
  const scanIntent = scanResult?.intent || 'statement';

  const rows = useMemo(() => {
    const specificity = { statement: 0, upload: 1, bureau: 2, investment: 2, transactional: 2 };
    const byId = new Map();
    for (const [key, result] of Object.entries(sourceResults || {})) {
      for (const row of result?.attachments || []) {
        const id = rowKey(row);
        const rowIntent = row.intent || key;
        const existing = byId.get(id);
        if (existing && (specificity[existing.intent] ?? 0) >= (specificity[rowIntent] ?? 0)) continue;
        byId.set(id, { ...row, intent: rowIntent });
      }
    }
    if (byId.size) return [...byId.values()];
    return scanResult?.attachments || [];
  }, [sourceResults, scanResult]);

  const alerts = useMemo(() => scanResult?.alerts || [], [scanResult]);
  const importableAlerts = useMemo(() => alerts.filter((a) => a.status === 'imported'), [alerts]);
  const excluded = useMemo(() => scanResult?.excluded || [], [scanResult]);
  const ignoredCount = scanResult?.ignored_by_rule || 0;

  const summary = (['stage_parse', 'alerts', 'stage_process', 'process'].includes(job?.kind) && job.status === 'complete')
    ? job.result : null;

  const stage = useMemo(() => {
    const fromJob = stageFor(job);
    let resolved = fromJob === 'downloaded' ? 'select' : fromJob;
    if (resolved === 'select') {
      const found = scanIntent === 'transactional' ? alerts.length : rows.length;
      resolved = found ? 'select' : 'idle';
    }
    if (resolved !== 'idle') return resolved;
    if (!status?.available) return 'setup';
    if (!status?.connected) return 'connect';
    return 'idle';
  }, [status, job, rows.length, alerts.length, scanIntent]);

  const mailboxReady = Boolean(status?.connected);
  const mailboxAvailable = Boolean(status?.available);
  /* A grant on record that the server will no longer accept. Distinct from
     never having connected: the fix is the same button, but telling someone
     to "connect" a mailbox they already connected reads like the app lost
     track of itself. */
  const mailboxNeedsReconnect = Boolean(status?.needs_reconnect);

  const persistSelection = useCallback((next) => {
    setSelection((prev) => {
      const resolved = typeof next === 'function' ? next(prev) : next;
      const set = resolved instanceof Set ? resolved : new Set(resolved || []);
      store({ selection: [...set] });
      return set;
    });
  }, []);

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

  const stagedAlertsRef = useRef(null);
  useEffect(() => {
    const result = sourceResults?.transactional;
    const alertRows = result?.alerts;
    if (!result?.__id || !alertRows?.length) return;
    if (stagedAlertsRef.current === result.__id) return;
    stagedAlertsRef.current = result.__id;
    api.stageScanResults({
      files: [],
      alerts: alertRows.filter((a) => a.amount && a.date_iso && a.account_suffix),
    })
      .then(() => refreshSections())
      .catch(() => { stagedAlertsRef.current = null; });
  }, [sourceResults, refreshSections]);

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
    mailboxReady, mailboxAvailable, mailboxNeedsReconnect,
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
