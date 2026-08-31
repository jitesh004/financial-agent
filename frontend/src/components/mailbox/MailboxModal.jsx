import React, { useEffect, useMemo, useState } from 'react';
import { formatBytes } from '../../lib';
import AttachmentGroups from '../AttachmentGroups';
import AlertReview from './AlertReview';
import JobProgress from '../JobProgress';
import { Callout, Chip, Empty } from '../ui';
import {
  AttachmentTable, ExcludedPanel, FilterBar, ResultTable, SelectionSummary,
  SetupInstructions, StageStrip,
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
export default function MailboxModal({ mailbox, open, onClose }) {
  const {
    status, periods, intents, error, setError, stage, job, busy,
    rows, excluded, ignoredCount, summary, selection, setSelection,
    months, setLookback, maxMessages, setCap, ignoredSenders, setIgnored,
    startScan, startImport, cancel, resume, reset, connect,
    intent, setIntent, scanIntent, alerts, importableAlerts, importAlerts,
  } = mailbox;

  // An alert scan produces a different kind of list with a different action,
  // so the select step branches on what the LAST SCAN was for - not on what
  // the picker currently shows, which the user may already have changed.
  const reviewingAlerts = scanIntent === 'transactional';

  // Table controls. Deliberately local: these are how you are looking at the
  // list right now, not part of the import, and resetting them on close is
  // the behaviour people expect from a filter box.
  const [search, setSearch] = useState('');
  const [excludedSenders, setExcludedSenders] = useState(() => new Set());
  const [excludedCategories, setExcludedCategories] = useState(() => new Set(['broker']));
  const [sort, setSort] = useState({ key: 'date_iso', dir: 'desc' });
  const [grouped, setGrouped] = useState(true);
  const [dateOrder, setDateOrder] = useState('desc');
  const [onlyMissingPassword, setOnlyMissingPassword] = useState(false);
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
    setSelection((previous) => {
      if (previous.size) return previous;
      return new Set(
        rows.filter((r) => !excludedCategories.has(r.category)).map(rowKey));
    });
    // excludedCategories is deliberately not a dependency: re-running this on
    // every filter change would keep resurrecting a selection the user cleared.
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

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const out = rows.filter((r) => {
      if (excludedCategories.has(r.category)) return false;
      if (excludedSenders.has(r.sender_domain || r.sender_name)) return false;
      if (onlyMissingPassword && r.password_ready) return false;
      if (!needle) return true;
      return `${r.filename} ${r.sender_name} ${r.subject}`.toLowerCase().includes(needle);
    });
    const dir = sort.dir === 'asc' ? 1 : -1;
    return [...out].sort((a, b) => {
      const av = a[sort.key] ?? '';
      const bv = b[sort.key] ?? '';
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
  }, [rows, search, excludedSenders, excludedCategories, onlyMissingPassword, sort]);

  // Intersected with what is visible, so a hidden row can never be silently
  // downloaded - what you see selected is exactly what gets fetched.
  const effective = useMemo(
    () => visible.filter((r) => selection.has(rowKey(r))), [visible, selection]);

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
    const allSelected = visible.every((r) => selection.has(rowKey(r)));
    const next = new Set(selection);
    for (const r of visible) {
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
            <div className="xp-modal-title">Scan mailbox</div>
            <div className="xp-hint" style={{ textTransform: 'none' }}>{subtitle}</div>
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

        <div className="xp-modal-body">
          {error && <Callout tone="neg">{error}</Callout>}

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

          {stage === 'idle' && (
            <div>
              <div className="xp-field" style={{ marginBottom: 14 }}>
                <span className="xp-legend">What to look for</span>
                <div className="seg" style={{ flexWrap: 'wrap' }}>
                  {intents.map((one) => (
                    <button key={one.key} type="button"
                      className={`seg-btn ${intent === one.key ? 'active' : ''}`}
                      onClick={() => setIntent(one.key)}>
                      {one.label}
                    </button>
                  ))}
                </div>
                <p style={{ color: 'var(--text-2)', fontSize: 13, margin: '8px 0 0' }}>
                  {intents.find((one) => one.key === intent)?.description
                    || 'Scans your mailbox and shows you everything it finds.'}
                </p>
              </div>

              {intent === 'transactional' && (
                <Callout tone="warn">
                  Alerts are <strong>not reconciled</strong>. They cover the
                  fortnight before a statement is cut, and each one is replaced
                  automatically when its statement arrives. Only alerts for
                  accounts already imported here can be used - the email gives
                  four digits and nothing else to go on.
                </Callout>
              )}

              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap',
                alignItems: 'flex-end', margin: '14px 0' }}>
                <label>
                  <div style={{ fontSize: 12.5, fontWeight: 550, marginBottom: 5 }}>
                    Look back
                  </div>
                  <select className="xp-select" value={months ?? ''}
                    disabled={intent === 'transactional'}
                    onChange={(e) => setLookback(e.target.value ? Number(e.target.value) : null)}>
                    {periods.map((p) => (
                      <option key={p.label} value={p.months ?? ''}>{p.label}</option>
                    ))}
                  </select>
                  {intent === 'transactional' && (
                    <div className="xp-hint" style={{ textTransform: 'none' }}>
                      Fixed at 2 months for alerts.
                    </div>
                  )}
                </label>

                <label>
                  <div style={{ fontSize: 12.5, fontWeight: 550, marginBottom: 5 }}>
                    Max emails to read
                  </div>
                  <select className="xp-select" value={maxMessages}
                    onChange={(e) => setCap(Number(e.target.value))}>
                    {[100, 250, 500, 1000, 2500, 5000].map((n) => (
                      <option key={n} value={n}>{n}</option>
                    ))}
                  </select>
                </label>

                <button className="btn primary" onClick={startScan}>Scan mailbox</button>
              </div>

              <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
                A wider window finds more history but takes longer to read — roughly
                a second per 15 emails. Raise the email limit too: a 10-year window
                capped at 500 emails only reaches back about a year. Files already
                downloaded are re-used from the local cache either way.
              </div>

              {stoppedNote(job) && (
                <Callout tone={job.status === 'cancelled' ? 'warn' : 'neg'}
                  style={{ marginTop: 12 }}>
                  {stoppedNote(job)}
                </Callout>
              )}
            </div>
          )}

          {stage === 'scanning' && (
            <JobProgress job={job} title="Scanning your mailbox" onCancel={cancel} />
          )}

          {stage === 'select' && reviewingAlerts && (
            <AlertReview
              alerts={alerts}
              selected={selection}
              onToggle={(messageId) => {
                const next = new Set(selection);
                if (next.has(messageId)) next.delete(messageId);
                else next.add(messageId);
                setSelection(next);
              }}
              onToggleAll={() => {
                const all = importableAlerts.every(
                  (a) => selection.has(a.message_id));
                setSelection(all ? new Set()
                  : new Set(importableAlerts.map((a) => a.message_id)));
              }}
            />
          )}

          {stage === 'select' && !reviewingAlerts && (
            <>
              <div style={{ margin: '4px 0 10px' }}>
                <FilterBar
                  ignoredSenders={ignoredSenders}
                  ignoredCount={ignoredCount}
                  onIgnoreSender={ignoreSender}
                  onUnignoreAll={() => setIgnored([])}
                  categories={categories}
                  excludedCategories={excludedCategories}
                  onToggleCategory={toggleSet(setExcludedCategories)}
                  senders={senders}
                  excludedSenders={excludedSenders}
                  onToggleSender={toggleSet(setExcludedSenders)}
                  search={search}
                  onSearch={setSearch}
                  onlyMissingPassword={onlyMissingPassword}
                  onToggleMissing={() => setOnlyMissingPassword((v) => !v)}
                />
              </div>

              <ExcludedPanel excluded={excluded} open={showExcluded}
                onToggle={() => setShowExcluded((v) => !v)} />

              <SelectionSummary
                visible={visible.length}
                total={rows.length}
                selected={effective.length}
                bytes={effective.reduce((sum, r) => sum + (r.size || 0), 0)}
                cached={effective.filter((r) => r.cached).length}
                missingPassword={effective.filter((r) => !r.password_ready).length}
              />

              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                <button className="btn" style={{ padding: '3px 10px', fontSize: 12 }}
                  onClick={() => setGrouped((v) => !v)}>
                  {grouped ? 'Flat list' : 'Group by institution'}
                </button>
                <button className="btn" style={{ padding: '3px 10px', fontSize: 12 }}
                  onClick={toggleAllVisible}>
                  Select / deselect all shown
                </button>
              </div>

              {grouped ? (
                <AttachmentGroups
                  rows={visible} selected={selection}
                  onToggle={toggleRow} onToggleMany={toggleMany}
                  dateOrder={dateOrder}
                  onToggleDateOrder={() => setDateOrder((o) => (o === 'asc' ? 'desc' : 'asc'))}
                />
              ) : (
                <AttachmentTable
                  rows={visible} selected={selection} onToggle={toggleRow}
                  onToggleAll={toggleAllVisible} sort={sort}
                  onSort={(key) => setSort((s) => ({
                    key, dir: s.key === key && s.dir === 'asc' ? 'desc' : 'asc',
                  }))}
                />
              )}

              {stoppedNote(job) && (
                <Callout tone={job.status === 'cancelled' ? 'warn' : 'neg'}
                  style={{ marginTop: 12 }}>
                  {stoppedNote(job)} Your selection is still here — start the
                  import again when you are ready.
                </Callout>
              )}
            </>
          )}

          {(stage === 'downloading' || stage === 'processing') && (
            <>
              <StageStrip active={stage} />
              <JobProgress
                job={job}
                title={stage === 'downloading' ? 'Downloading statements'
                  : job?.kind === 'alerts' ? 'Adding the alerts'
                    : 'Parsing statements'}
                onCancel={cancel}
              />
              <Callout style={{ marginTop: 12 }}>
                You can close this. The import keeps running on the server, and
                the button in the header shows how it is getting on.
              </Callout>
            </>
          )}

          {stage === 'interrupted' && (
            <>
              <Callout tone="warn">
                <strong>This import stopped when the server restarted.</strong>{' '}
                {job.current} of {job.total} finished before it did.
              </Callout>
              <JobProgress job={job} title="Interrupted" showTrace />
              <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
                {job.resumable && (
                  <button className="btn primary" onClick={resume}>
                    Resume what is left
                  </button>
                )}
                <button className="btn" onClick={reset}>Start over</button>
              </div>
            </>
          )}

          {stage === 'done' && summary && job?.kind === 'alerts' && (
            <Callout tone="pos">
              <strong>{summary.imported} unreconciled row
              {summary.imported === 1 ? '' : 's'} added.</strong>{' '}
              Each sits in the review queue and is replaced automatically when
              the statement covering it arrives.
            </Callout>
          )}

          {stage === 'done' && summary && job?.kind !== 'alerts' && (
            <>
              <Callout tone="pos">
                <strong>Import complete.</strong>{' '}
                {summary.transaction_count?.toLocaleString('en-IN')} transactions
                across {summary.account_count} accounts.
              </Callout>
              <ResultTable statements={summary.statements || []} />
            </>
          )}

          {stage === 'done' && !summary && (
            <Empty title="Nothing to show">That import produced no statements.</Empty>
          )}
        </div>

        <div className="xp-modal-foot">
          {stage === 'select' && reviewingAlerts && (
            <>
              <button className="btn primary"
                disabled={!importableAlerts.some((a) => selection.has(a.message_id))}
                onClick={() => importAlerts(
                  importableAlerts.filter((a) => selection.has(a.message_id))
                    .map((a) => a.message_id))}>
                Add {importableAlerts.filter((a) => selection.has(a.message_id)).length}
                {' '}unreconciled row
                {importableAlerts.filter((a) => selection.has(a.message_id)).length === 1
                  ? '' : 's'}
              </button>
              <button className="btn" onClick={startScan}>Re-scan</button>
            </>
          )}
          {stage === 'select' && !reviewingAlerts && (
            <>
              <button className="btn primary" disabled={!effective.length}
                onClick={() => startImport(effective)}>
                Download &amp; process {effective.length} file
                {effective.length === 1 ? '' : 's'}
                {effective.length > 0 && (
                  <span style={{ opacity: 0.75 }}>
                    {' '}· {formatBytes(effective.reduce((s, r) => s + (r.size || 0), 0))}
                  </span>
                )}
              </button>
              <button className="btn" onClick={startScan}>Re-scan</button>
            </>
          )}
          {stage === 'done' && (
            <button className="btn primary" onClick={reset}>Import more</button>
          )}
          <div style={{ flex: 1 }} />
          <button className="btn" onClick={onClose}>
            {busy ? 'Close and keep running' : 'Close'}
          </button>
        </div>
      </div>
    </div>
  );
}
