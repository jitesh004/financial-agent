import React, { useEffect, useMemo, useState } from 'react';
import { api, formatBytes } from '../../lib';
import AttachmentGroups from '../AttachmentGroups';
import AlertReview from './AlertReview';
import ChooseSections from './ChooseSections';
import ImportReview from './ImportReview';
import ParseSections from './ParseSections';
import ScanSections from './ScanSections';
import SourceSections from './SourceSections';
import ProcessStep from './ProcessStep';
import Upload from '../Upload';
import JobProgress from '../JobProgress';
import { Callout, Chip, ConfirmButton, Empty } from '../ui';
import {
  AttachmentTable, ExcludedPanel, FilterBar, ResultTable, SelectionSummary,
  SetupInstructions, StageStrip, StepRail,
} from './parts';
import { rowKey, stoppedNote } from './useMailbox';

/* The mailbox import, as a modal reachable from the header at any time.
 *
 * It used to live inline on the empty-state landing page, which meant it was
 * unreachable the moment there was any data - the one screen you could not get
 * back to was the one that put the data there.
 *
 * Closing this does not stop anything. The work is server-side and the stage
 * is derived from it (see useMailbox), so closing and reopening lands back
 * exactly where the import had got to, including across a page reload or an
 * API restart.
 */

/* The hook lives in App rather than here, so the header button and this share
   one poller. Mounting a second copy would mean two requests per tick and two
   answers that could disagree about whether anything is running. */
export default function MailboxModal({ mailbox, open, onClose, onUploaded }) {
  const {
    status, periods, intents, error, setError, stage, job, busy,
    rows, excluded, ignoredCount, summary, selection, setSelection,
    months, setLookback, maxMessages, setCap, ignoredSenders, setIgnored,
    startScan, startImport, cancel, resume, reset, connect,
    intent, chosenIntents: chosen, toggleIntent, scanIntent,
    alerts, importableAlerts, importAlerts,
  } = mailbox;

  // An alert scan produces a different kind of list with a different action,
  // so the select step branches on what the LAST SCAN was for - not on what
  // the picker currently shows, which the user may already have changed.
  const reviewingAlerts = scanIntent === 'transactional';

  /* ---- Steps -------------------------------------------------------------
   *
   * The server's `stage` says how far the import HAS got. `step` says which
   * screen you are looking at, and the two are deliberately not the same
   * thing: an import runs on the server whether or not this modal is open, so
   * binding the view to it meant every screen you had already passed was
   * unreachable the moment the next one started. Going back to check what was
   * selected while a download runs now costs nothing and changes nothing.
   *
   * `reached` is the furthest step the work justifies. It only ever moves
   * forward within a run, so stepping back does not close the door behind you.
   */
  const STEPS = [
    { key: 'source', label: 'Source' },
    { key: 'scanning', label: 'Scanning' },
    { key: 'choose', label: 'Choose' },
    { key: 'parse', label: 'Parse' },
    { key: 'review', label: 'Review', always: true },
    { key: 'process', label: 'Process data', always: true },
  ];

  /* Which step the WORK has reached. Deliberately separate from which step you
     are looking at: the import runs on the server whether or not this is open,
     so binding the view to it made every screen you had passed unreachable the
     moment the next one started. */
  const stepForStage = (s) => {
    if (s === 'scanning') return 1;
    if (s === 'select' || s === 'downloaded') return 2;
    if (s === 'downloading' || s === 'parsing' || s === 'interrupted') return 3;
    if (s === 'staged') return 4;
    if (s === 'processing') return 5;
    if (s === 'done') return 5;
    return 0;
  };

  const [step, setStep] = useState(0);
  const [reached, setReached] = useState(0);

  const stageStep = stepForStage(stage);

  useEffect(() => { setReached((r) => Math.max(r, stageStep)); }, [stageStep]);

  /* The view does NOT follow the work any more.
   *
   * With one linear import it was helpful: the screen moved on as the import
   * did. With a section per source it is the opposite - scanning four sources
   * finishes four times, and each completion yanked the screen somewhere
   * else. Scanning alerts threw you onto Process; the next one threw you onto
   * Review. The rail unlocks as steps are reached; where you look is yours. */

  const goTo = (index) => setStep(index);
  const rejoin = () => setStep(stageStep);

  const view = STEPS[step].key;
  // Only ever a hint now - "there is work happening on another step" - never
  // a reason to move the user.
  const behind = busy && step !== stageStep;

  /* Starting something is a request to watch it. Without this, scanning from
     a step you had walked back to left you reading a stale screen while the
     work you just asked for ran somewhere you could not see. */
  const andFollow = (action) => (...args) => action(...args);

  /* What is in staging, kept here because two steps need it: Review sets it
     and Process reads it. Fetched by ImportReview rather than separately, so
     there is one request and one answer rather than two that can disagree. */
  const [staged, setStaged] = useState({});
  /* Files picked on Source but not read yet. Held here because the
     picking happens on one step and the reading on the next. */
  const [pendingUploads, setPendingUploads] = useState({ count: 0 });

  const doProcess = async () => {
    setError(null);
    try {
      await api.stagingProcess();
      await mailbox.refresh?.();
    } catch (e) { setError(e.message); }
  };
  const doScan = andFollow(startScan);
  /* Pressing Download & read moves you to Parse, where the progress is.
   *
   * The view no longer follows the work on its own - four sources finishing
   * in turn used to yank the screen about - but moving on an explicit button
   * press is different: you asked for this, and the thing you asked for is
   * reported one step along. Without it the download ran with no visible
   * sign of it anywhere. */
  const PARSE_STEP = STEPS.findIndex((s) => s.key === 'parse');
  const doImport = async (...args) => {
    const result = await startImport(...args);
    goTo(PARSE_STEP);
    return result;
  };
  const doImportAlerts = andFollow(importAlerts);
  const doResume = andFollow(resume);

  // Table controls. Deliberately local: these are how you are looking at the
  // list right now, not part of the import, and resetting them on close is
  // the behaviour people expect from a filter box.
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState({ key: 'date_iso', dir: 'desc' });
  const [grouped, setGrouped] = useState(true);
  const [dateOrder, setDateOrder] = useState('desc');
  const [showExcluded, setShowExcluded] = useState(false);

  // Escape closes; the work keeps running.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // A fresh scan's results arrive with nothing ticked. Everything outside the
  // excluded categories is preselected, which is what the old wizard did the
  // moment its scan resolved - here it has to react to the rows appearing,
  // because the scan may well have finished while this modal was closed.
  const scanId = mailbox.scanJob?.id;
  useEffect(() => {
    if (reviewingAlerts) {
      if (!importableAlerts.length) return;
      setSelection((previous) => (previous.size ? previous
        : new Set(importableAlerts.map((a) => a.message_id))));
      return;
    }
    if (!rows.length) return;
    // Everything a scan found starts ticked. Excluding "broker" here was the
    // other half of the same silent filter - it pre-unticked every investment
    // file in a wizard that now gives investments a section of their own.
    setSelection((previous) => (previous.size ? previous
      : new Set(rows.map(rowKey))));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanId, rows.length, reviewingAlerts, importableAlerts.length]);

  const senders = useMemo(() => {
    const counts = new Map();
    for (const r of rows) {
      const key = r.sender_domain || r.sender_name;
      const prev = counts.get(key)
        || { key, name: r.sender_name, count: 0, category: r.category };
      prev.count += 1;
      counts.set(key, prev);
    }
    return [...counts.values()].sort((a, b) => b.count - a.count);
  }, [rows]);

  const categories = useMemo(() => {
    const counts = new Map();
    for (const r of rows) counts.set(r.category, (counts.get(r.category) || 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [rows]);

  /* What the footer will actually fetch: everything ticked, anywhere.
   *
   * This used to be intersected with a `visible` list carried over from the
   * old single-table Choose screen - which filtered by a search box, a sender
   * list, and a set of excluded categories defaulting to ["broker"]. None of
   * those controls exist any more, so the filtering was invisible: the
   * Investments section read "398 of 398 chosen" while the button offered to
   * download 169, silently dropping every broker file. The sections ARE the
   * filter now, and what you tick is what you get. */
  const effective = useMemo(
    () => rows.filter((r) => selection.has(rowKey(r))), [rows, selection]);

  const toggleRow = (r) => {
    const next = new Set(selection);
    const key = rowKey(r);
    if (next.has(key)) next.delete(key); else next.add(key);
    setSelection(next);
  };

  const toggleMany = (items, select) => {
    const next = new Set(selection);
    for (const r of items) {
      if (select) next.add(rowKey(r)); else next.delete(rowKey(r));
    }
    setSelection(next);
  };

  const toggleAllVisible = () => {
    const allSelected = rows.every((r) => selection.has(rowKey(r)));
    const next = new Set(selection);
    for (const r of rows) {
      if (allSelected) next.delete(rowKey(r)); else next.add(rowKey(r));
    }
    setSelection(next);
  };

  const ignoreSender = async (domain) => {
    await setIgnored([...new Set([...ignoredSenders, domain])]);
  };

  const toggleSet = (setter) => (key) => setter((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  if (!open) return null;

  const subtitle = status?.connected
    ? (status.cached_files > 0
      ? `Connected · ${status.cached_files} files cached locally` : 'Connected')
    : 'Not connected';

  return (
    <div className="xp-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="xp-modal mailbox">
        <div className="xp-modal-head">
          <div>
            <div className="xp-modal-title">Import</div>
            <div className="xp-hint" style={{ textTransform: 'none' }}>
              {subtitle}
            </div>
          </div>
          <div style={{ flex: 1 }} />
          {busy && (
            <Chip tone="accent">
              <span className="spinner" style={{ width: 10, height: 10, marginRight: 6 }} />
              running
            </Chip>
          )}
          <button className="xp-icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {status?.connected && (
          <div style={{
            padding: '8px 16px 0', display: 'flex',
            alignItems: 'center', gap: 10, flexWrap: 'wrap',
          }}>
            <StepRail steps={STEPS} current={step} reached={reached} onGo={goTo} />
            {behind && (
              <button className="btn" style={{ padding: '2px 9px', fontSize: 11 }}
                onClick={rejoin}>
                Back to {STEPS[stageStep].label}
              </button>
            )}
          </div>
        )}

        <div className="xp-modal-body">
          {error && <Callout tone="neg">{error}</Callout>}

          {behind && (
            <Callout>
              You are looking at <strong>{STEPS[step].label}</strong>; the
              import is on <strong>{STEPS[stageStep].label}</strong>.
              {busy
                ? ' It is still running — nothing here interrupts it.'
                : ' Nothing is running.'}
            </Callout>
          )}

          {status?.connected && !status.profile_ready && (
            <Callout tone="warn">
              Your profile has no name, date of birth or PAN yet, so
              password-protected statements will not open. Add them in{' '}
              <strong>Profile</strong> first.
            </Callout>
          )}

          {!status && <div className="spinner" />}

          {stage === 'setup' && <SetupInstructions />}

          {stage === 'connect' && (
            <>
              <Callout>
                Read-only access. Sign-in happens on Google&apos;s own page — this
                app never sees your password, and the scope granted cannot send
                or delete mail.
              </Callout>
              <button className="btn primary" style={{ marginTop: 12 }} onClick={connect}>
                Connect Gmail
              </button>
            </>
          )}

          {view === 'source' && (
            <SourceSections
              intents={intents}
              chosen={chosen}
              onToggle={toggleIntent}
              settingsFor={mailbox.settingsFor}
              onSetting={mailbox.setSourceSetting}
              sections={mailbox.sections}
              onUploaded={() => {
                setPendingUploads({ count: 0 });
                mailbox.refreshSections?.();
                mailbox.refresh?.();
              }}
              onFilesChange={(files, submit) => setPendingUploads({
                count: files.length, submit,
              })}
            />
          )}

          {view === 'scanning' && (
            <ScanSections
              intents={intents}
              chosen={chosen}
              sections={mailbox.sections}
              sourceJobs={mailbox.sourceJobs}
              busy={busy}
              pendingUploads={pendingUploads}
              onScan={async (key) => {
                const id = await mailbox.scanSource(key);
                mailbox.refreshSections?.();
                return id;
              }}
              onForget={async (key) => {
                await api.stagingForget(key);
                await mailbox.refreshSections?.();
                setStaged({});
              }}
            />
          )}

          {view === 'choose' && (
            <ChooseSections
              intents={intents}
              chosen={chosen}
              sections={mailbox.sections}
              sourceResults={mailbox.sourceResults}
              rows={rows}
              selected={selection}
              onToggle={toggleRow}
              onToggleMany={toggleMany}
            />
          )}

          {view === 'parse' && (
            <ParseSections
              intents={intents}
              chosen={chosen}
              /* How many of each source's files are ticked on Choose but not
                 fetched yet - the difference between "you have not chosen
                 anything" and "you chose and did not press the button". */
              chosenCounts={rows.reduce((acc, r) => {
                if (!selection.has(rowKey(r))) return acc;
                const key = r.intent || 'statement';
                acc[key] = (acc[key] || 0) + 1;
                return acc;
              }, {})}
              sections={mailbox.sections}
              busy={busy}
              onParse={mailbox.parseSource}
              onRefresh={mailbox.refreshSections}
            />
          )}

          {view === 'parse' && (stage === 'downloading' || stage === 'parsing') && (
            <div style={{ marginTop: 14 }}>
              <StageStrip active={stage} />
              <JobProgress
                job={job}
                title={stage === 'downloading' ? 'Downloading documents'
                  : 'Reading documents'}
                onCancel={cancel}
              />
              <Callout style={{ marginTop: 12 }}>
                Nothing read here is in your ledger — it goes to Review first.
                You can close this. The work keeps running on the server, and
                the button in the header shows how it is getting on.
              </Callout>
            </div>
          )}

          {view === 'parse' && stage === 'interrupted' && (
            <Callout tone="warn" style={{ marginTop: 12 }}>
              <strong>That run stopped when the server restarted.</strong>{' '}
              {job.current} of {job.total} finished. Press Read again — anything
              already read is not read twice.
            </Callout>
          )}
          {view === 'review' && (
            <ImportReview onChanged={setStaged} />
          )}

          {view === 'process' && (
            <ProcessStep
              /* Falls back to the per-source counts, which are loaded whenever
                 the modal opens. Reading them only from the Review screen
                 meant arriving here without visiting Review first showed
                 "0 files selected" over a ledger of two and a half thousand
                 rows. */
              staged={staged?.selected != null ? staged : {
                selected: (mailbox.sections || []).reduce(
                  (n, x) => n + (x.selected || 0), 0),
                rows: (mailbox.sections || []).reduce(
                  (n, x) => n + (x.rows || 0), 0),
                pending: (mailbox.sections || []).reduce(
                  (n, x) => n + (x.pending || 0), 0),
                processed: staged?.processed,
              }}
              job={job}
              stage={stage}
              onRun={doProcess}
              onFinished={() => { onUploaded?.(null); onClose(); }}
            />
          )}
        </div>

        <div className="xp-modal-foot">
          {view === 'choose' && (
            <button className="btn primary" disabled={!effective.length}
              onClick={() => doImport(effective)}>
              Download &amp; read {effective.length} file
              {effective.length === 1 ? '' : 's'}
              {effective.length > 0 && (
                <span style={{ opacity: 0.75 }}>
                  {' '}· {formatBytes(effective.reduce((s, r) => s + (r.size || 0), 0))}
                </span>
              )}
            </button>
          )}
          {view === 'process' && stage === 'done' && (
            <button className="btn primary" onClick={() => { reset(); goTo(0); }}>
              Import more
            </button>
          )}
          {status?.connected && (
            <>
              <button className="btn" disabled={step === 0}
                onClick={() => goTo(step - 1)}>← Back</button>
              <button className="btn" disabled={step >= STEPS.length - 1}
                onClick={() => goTo(step + 1)}>Next →</button>
            </>
          )}
          <div style={{ flex: 1 }} />
          <ConfirmButton
            title="Empty the wizard and start again. Your ledger is untouched."
            question="Forget every staged document? Your ledger is untouched."
            confirmLabel="Forget everything"
            onConfirm={async () => {
              await api.stagingForget();
              reset();
              setStaged({});
              await mailbox.refreshSections?.();
              goTo(0);
            }}>
            Start over
          </ConfirmButton>
          <button className="btn" onClick={onClose}>
            {busy ? 'Close and keep running' : 'Close'}
          </button>
        </div>
      </div>
    </div>
  );
}
