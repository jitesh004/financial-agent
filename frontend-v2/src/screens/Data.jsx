/* File bookkeeping, in one place instead of three.
 *
 * "Files & quality", "Files & passwords" and "Data" were three separate nav
 * entries about the same subject: which files came in, what happened to them,
 * and what to do about the ones that failed. Three destinations meant the
 * answer to "why is this statement missing?" lived somewhere you had to guess.
 *
 * Three sections here instead, each a URL of its own, ordered by the question
 * being asked: what is covered, what happened to each file, and what can be
 * undone.
 */

import React, { useMemo, useState } from 'react';
import { api } from '../core/api';
import { invalidate, useQuery } from '../core/store';
import { useRouteParam } from '../core/router';
import { useToast } from '../core/toast';
import { useDashboard } from '../core/ledger';
import {
  bytes, count, dateLabel, money, monthLabel, monthLabelLong, stampLabel, titleCase,
} from '../core/format';
import {
  Button, Callout, Card, Chip, ConfirmButton, Empty, Loading, Search, Section,
  Segmented, Select, Stat, Table,
} from '../ui';
import JobProgress from '../ui/JobProgress';

const SECTIONS = [
  ['coverage', 'Coverage & quality',
    'Which months each account has statements for, and which parses reconciled.'],
  ['files', 'Files & passwords',
    'Every file ever attempted, whatever happened to it — including the ones still locked.'],
  ['manage', 'Manage data', 'Snapshots, clearing scopes, and rebuilding.'],
];

export default function Data({ onImport }) {
  const [section, setSection] = useRouteParam('section', 'coverage');
  const active = SECTIONS.find((s) => s[0] === section) || SECTIONS[0];

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h2 className="h2">Data</h2>
          <p className="lead">{active[2]}</p>
        </div>
        <Segmented value={section} onChange={setSection} ariaLabel="Section"
          options={SECTIONS.map(([v, l, hint]) => [v, l, hint])} />
      </div>

      {section === 'coverage' && <Coverage onImport={onImport} />}
      {section === 'files' && <Files onImport={onImport} />}
      {section === 'manage' && <Manage />}
    </>
  );
}

/* ═══════════════════════════════════════════════════ coverage & quality ═ */

const STATUS = {
  ok: ['pos', 'Reconciled'],
  unreconciled: ['warn', 'Did not balance'],
  failed: ['neg', 'Could not parse'],
  needs_password: ['warn', 'Password needed'],
};
const RECON = {
  passed: ['pos', 'Balances tie out'],
  failed: ['neg', 'Discrepancy'],
  not_applicable: ['', 'No balances stated'],
};

function Coverage({ onImport }) {
  const { data } = useDashboard();
  const statements = data?.statements || [];
  const accounts = data?.accounts || [];
  const transfers = data?.transfers || {};
  const quality = data?.data_quality || {};

  if (!statements.length) {
    return (
      <>
        <CoverageGrid />
        <Empty title="No files analysed yet" icon="file"
          action={onImport && (
            <Button variant="primary" icon="upload" onClick={onImport}>Import statements</Button>
          )}>
          Import a statement or scan your mailbox, and the coverage grid and parse quality
          for every file appear here.
        </Empty>
      </>
    );
  }

  return (
    <>
      <CoverageGrid />

      <Section title="Accounts detected" note={`${accounts.length}`} />
      <div className="grid cols-3">
        {accounts.map((a) => (
          <Card key={a.id} title={a.display_name} sub={titleCase(a.account_type)}>
            <div className="stat-label">{a.is_liability ? 'Outstanding' : 'Balance'}</div>
            <div className={`stat-value sm ${a.is_liability ? 'neg' : ''}`}>
              {money(a.is_liability ? a.principal_outstanding : a.current_balance)}
            </div>
            <div className="row tight" style={{ marginTop: 10 }}>
              {a.interest_rate && <Chip>{a.interest_rate}% p.a.</Chip>}
              {a.emi_amount && <Chip>EMI {money(a.emi_amount)}</Chip>}
              {a.credit_limit && <Chip>Limit {money(a.credit_limit)}</Chip>}
              {a.holder_name && <Chip>{a.holder_name}</Chip>}
            </div>
          </Card>
        ))}
      </div>

      <Section title="Reconciliation" />
      <div className="grid cols-4">
        <Stat label="Files processed"
          value={String(quality.files_processed ?? statements.length)} />
        <Stat label="Reconciled" value={String(quality.files_reconciled ?? 0)} tone="pos"
          note="Transactions explain the full balance movement" />
        <Stat label="Unreconciled" value={String(quality.files_unreconciled ?? 0)}
          tone={quality.files_unreconciled ? 'neg' : undefined} />
        <Stat label="Duplicates removed" value={String(quality.duplicates_removed ?? 0)}
          note="From overlapping statement periods" />
      </div>

      <Card pad={false}>
        <Table>
          <thead>
            <tr>
              <th>File</th><th>Format</th><th>Period</th>
              <th className="right">Rows</th>
              <th className="right">Opening</th><th className="right">Closing</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {statements.map((s, i) => {
              const [tone, label] = STATUS[s.status] || ['', s.status];
              const recon = s.reconciliation
                ? RECON[s.reconciliation.status] || ['', s.reconciliation.status] : null;
              return (
                <tr key={`${s.filename}-${i}`}>
                  <td>
                    <div className="truncate" style={{ maxWidth: 240 }}>{s.filename}</div>
                    {s.extractor && <span className="tiny dim">via {s.extractor}</span>}
                  </td>
                  <td><Chip>{(s.format || '').toUpperCase()}</Chip></td>
                  <td className="nowrap small">
                    {s.period_start
                      ? `${dateLabel(s.period_start)} → ${dateLabel(s.period_end)}` : '—'}
                  </td>
                  <td className="right num">{s.transaction_count ?? 0}</td>
                  <td className="right num nowrap">
                    {s.opening_balance != null ? money(s.opening_balance) : '—'}
                  </td>
                  <td className="right num nowrap">
                    {s.closing_balance != null ? money(s.closing_balance) : '—'}
                  </td>
                  <td>
                    <div className="col" style={{ gap: 4, alignItems: 'flex-start' }}>
                      <Chip tone={tone}>{label}</Chip>
                      {recon && <Chip tone={recon[0]}>{recon[1]}</Chip>}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </Card>

      {statements.some((s) => s.reconciliation?.status === 'failed' || s.warnings?.length) && (
        <>
          <Section title="Parse notes" />
          <Card pad>
            <div className="col">
              {statements.map((s, i) => {
                const notes = [
                  ...(s.reconciliation?.status === 'failed' ? [s.reconciliation.message] : []),
                  ...(s.warnings || []),
                ];
                if (!notes.length) return null;
                return (
                  <div key={i}>
                    <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
                      {s.filename}
                    </div>
                    <div className="col">
                      {notes.map((n, j) => (
                        <Callout key={j}
                          tone={s.reconciliation?.status === 'failed' && j === 0 ? 'neg' : 'warn'}>
                          {n}
                        </Callout>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        </>
      )}

      {transfers.pairs?.length > 0 && (
        <>
          <Section title="Transfers matched between your accounts"
            note={`${money(transfers.double_count_avoided)} kept out of spending totals`} />
          <Card pad={false}>
            <div style={{ padding: '12px 16px 0' }}>
              <Callout tone="pos">
                Each of these appears on two statements — money leaving one account and
                arriving in another. Counting both sides would have inflated your spending
                by {money(transfers.double_count_avoided)}.
              </Callout>
            </div>
            <Table scrollY maxHeight={360}>
              <thead>
                <tr>
                  <th>Type</th><th>From</th><th>To</th>
                  <th className="right">Amount</th><th className="right">Gap</th>
                </tr>
              </thead>
              <tbody>
                {transfers.pairs.map((p) => (
                  <tr key={p.pair_id}>
                    <td><Chip tone="acc">{p.kind.replace(/_/g, ' ')}</Chip></td>
                    <td className="truncate" style={{ maxWidth: 190 }}>{p.from}</td>
                    <td className="truncate" style={{ maxWidth: 190 }}>{p.to}</td>
                    <td className="right num nowrap">{money(p.amount)}</td>
                    <td className="right num">{p.day_gap}d</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
        </>
      )}
    </>
  );
}

/* One row per account, one cell per calendar month: green (parsed), amber (a
   file exists but failed to parse — click to retry), red (nothing was ever
   found for this month — click to search Gmail for just it), or a dim cell
   before that account's own history begins. */
function CoverageGrid() {
  const toast = useToast();
  const { data, loading, refetch } = useQuery('coverage', () => api.coverage());
  const [busyCell, setBusyCell] = useState(null);
  const [bulkJob, setBulkJob] = useState(null);
  const [bulkRunning, setBulkRunning] = useState(false);

  const rows = data?.accounts || [];

  // Every account starts its OWN month list from its first known statement, so
  // the columns need a shared, full-width axis to line up as a real grid.
  const allMonths = useMemo(() => {
    const set = new Set();
    rows.forEach((r) => r.months.forEach((m) => set.add(m.month)));
    return [...set].sort((a, b) => b.localeCompare(a));
  }, [rows]);

  if (loading) return <Loading label="Reading coverage…" />;
  if (!rows.length) return null;

  const cellFor = (row, month) => row.months.find((m) => m.month === month);

  async function retry(fileId, key) {
    setBusyCell(key);
    try {
      const res = await api.retryFile(fileId);
      toast[res.status === 'ok' ? 'ok' : 'warn'](
        res.message || `Retry finished: ${res.status}`);
      refetch();
      invalidate('files', 'dashboard');
    } catch (e) { toast.fail('The retry failed', e.message); }
    finally { setBusyCell(null); }
  }

  async function fetchMonth(accountId, month, key, label) {
    setBusyCell(key);
    try {
      const { job_id: jobId } = await api.fetchMonth(accountId, month);
      for (;;) {
        // eslint-disable-next-line no-await-in-loop
        const job = await api.job(jobId).catch(() => null);
        if (!job) break;
        if (!job.active) {
          const result = job.result || {};
          toast[result.status === 'ok' || result.status === 'unreconciled' ? 'ok' : 'warn'](
            result.message || `${label} · ${monthLabel(month)}: ${job.message || 'done'}`);
          break;
        }
        // eslint-disable-next-line no-await-in-loop
        await new Promise((r) => { setTimeout(r, 800); });
      }
      refetch();
      invalidate('files', 'dashboard');
    } catch (e) { toast.fail('That month could not be fetched', e.message); }
    finally { setBusyCell(null); }
  }

  async function fetchAllMissing() {
    setBulkRunning(true);
    try {
      const { job_id: jobId } = await api.fetchAllMissing();
      for (;;) {
        // eslint-disable-next-line no-await-in-loop
        const job = await api.job(jobId).catch(() => null);
        if (!job) break;
        setBulkJob(job);
        if (!job.active) break;
        // eslint-disable-next-line no-await-in-loop
        await new Promise((r) => { setTimeout(r, 900); });
      }
      refetch();
      invalidate('files', 'dashboard');
    } catch (e) { toast.fail('That run failed', e.message); }
    finally { setBulkRunning(false); }
  }

  const missingCount = rows.reduce(
    (n, r) => n + r.months.filter((m) => m.status === 'missing').length, 0);

  return (
    <Card
      title="Coverage"
      sub="One cell per account per month. Click an amber cell to retry the file, a red one to search your mailbox for just that month."
      tools={missingCount > 0 && (
        <Button size="sm" icon="download" busy={bulkRunning} onClick={fetchAllMissing}>
          Fetch {missingCount} missing
        </Button>
      )}
    >
      {bulkJob && bulkRunning && <JobProgress job={bulkJob} title="Fetching missing months" />}

      <div className="cov" style={{ overflowX: 'auto' }}>
        <div className="cov-row">
          <span />
          <div className="cov-months">
            {allMonths.map((m) => <span className="cov-month" key={m}>{monthLabel(m)}</span>)}
          </div>
        </div>
        {rows.map((row) => (
          <div className="cov-row" key={row.account_id}>
            <span className="cov-name" title={row.label}>{row.label}</span>
            <div className="cov-cells">
              {allMonths.map((month) => {
                const cell = cellFor(row, month);
                const status = cell?.status || 'na';
                const key = `${row.account_id}:${month}`;
                const clickable = status === 'failed' || status === 'missing';
                return (
                  <button
                    key={month}
                    type="button"
                    className={`cov-cell ${status}`}
                    disabled={!clickable || busyCell === key}
                    title={`${row.label} · ${monthLabelLong(month)} — ${
                      status === 'parsed' ? 'parsed'
                        : status === 'failed' ? 'a file exists but failed to parse; click to retry'
                          : status === 'missing' ? 'nothing found; click to search your mailbox'
                            : 'before this account’s history'}`}
                    onClick={() => {
                      if (status === 'failed' && cell?.file_id) retry(cell.file_id, key);
                      else if (status === 'missing') {
                        fetchMonth(row.account_id, month, key, row.label);
                      }
                    }}
                  />
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <div className="legend">
        <span className="legend-item"><i className="swatch" style={{ background: 'var(--pos)' }} />Parsed</span>
        <span className="legend-item"><i className="swatch" style={{ background: 'var(--warn)' }} />Failed to parse</span>
        <span className="legend-item"><i className="swatch" style={{ background: 'var(--neg)', opacity: .55 }} />Not available</span>
        <span className="legend-item"><i className="swatch" style={{ background: 'var(--surface-3)' }} />Before this account began</span>
      </div>
    </Card>
  );
}

/* ═══════════════════════════════════════════════════════ file registry ══ */

const FILE_STATUS = {
  parsed: ['pos', 'Parsed'],
  unreconciled: ['warn', 'Parsed · did not balance'],
  failed: ['neg', 'Failed'],
  needs_password: ['warn', 'Password needed'],
  duplicate: ['', 'Duplicate, skipped'],
  pending: ['', 'Pending'],
};
const PASSWORD = {
  open: ['pos', 'Open'], not_encrypted: ['', 'Not protected'],
  locked: ['neg', 'Locked'], unknown: ['', 'Unknown'],
};
const SOURCE = { gmail: 'Gmail', upload: 'Uploaded' };

function Files({ onImport }) {
  const toast = useToast();
  const { data: files, loading, error, refetch } = useQuery('files', () => api.files());
  const [filter, setFilter] = useState('');
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState(null);
  const [retrying, setRetrying] = useState(null);
  const [passwords, setPasswords] = useState({});

  const rows = useMemo(() => {
    let list = files || [];
    if (filter === 'failed') {
      list = list.filter((f) => ['failed', 'needs_password'].includes(f.parse_status));
    }
    if (filter === 'parsed') {
      list = list.filter((f) => ['parsed', 'unreconciled'].includes(f.parse_status));
    }
    const q = search.trim().toLowerCase();
    if (q) list = list.filter((f) => `${f.filename} ${f.institution_guess}`.toLowerCase().includes(q));
    return list;
  }, [files, filter, search]);

  const counts = useMemo(() => {
    const c = { all: files?.length || 0, parsed: 0, failed: 0 };
    (files || []).forEach((f) => {
      if (['parsed', 'unreconciled'].includes(f.parse_status)) c.parsed += 1;
      if (['failed', 'needs_password'].includes(f.parse_status)) c.failed += 1;
    });
    return c;
  }, [files]);

  if (error) return <Callout tone="neg">{error.message}</Callout>;
  if (loading) return <Loading label="Reading the file registry…" />;
  if (!files.length) {
    return (
      <Empty title="No files yet" icon="folder"
        action={onImport && (
          <Button variant="primary" icon="upload" onClick={onImport}>Import statements</Button>
        )}>
        Import some statements to see them here.
      </Empty>
    );
  }

  async function retry(file) {
    setRetrying(file.id);
    try {
      const res = await api.retryFile(file.id, passwords[file.id]);
      toast[res.status === 'ok' ? 'ok' : 'warn'](
        res.status === 'ok'
          ? `${file.filename}: parsed ${res.transaction_count} transaction(s) into ${res.account}.`
          : res.message || `${file.filename}: still ${res.status}.`);
      refetch();
      invalidate('dashboard', 'coverage');
    } catch (e) {
      toast.fail(`${file.filename} could not be retried`, e.message);
    } finally { setRetrying(null); }
  }

  return (
    <>
      <div className="row">
        <Segmented
          value={filter} onChange={setFilter} ariaLabel="Filter files"
          options={[
            ['', `All (${counts.all})`],
            ['parsed', `Parsed (${counts.parsed})`],
            ['failed', `Needs attention (${counts.failed})`],
          ]}
        />
        <Search value={search} onChange={setSearch} placeholder="Filter by filename…"
          style={{ flex: 1, minWidth: 180 }} />
      </div>

      <Card pad={false}>
        <Table>
          <thead>
            <tr>
              <th>File</th><th>Source</th><th className="right">Size</th>
              <th>Password</th><th>Status</th><th className="right">Rows</th>
              <th>Last attempt</th><th />
            </tr>
          </thead>
          <tbody>
            {rows.map((f) => {
              const [tone, label] = FILE_STATUS[f.parse_status] || ['', f.parse_status];
              const [pwTone, pwLabel] = PASSWORD[f.password_status] || ['', f.password_status];
              const canRetry = ['failed', 'needs_password', 'unreconciled'].includes(f.parse_status);
              const canDrill = ['parsed', 'unreconciled'].includes(f.parse_status);
              return (
                <React.Fragment key={f.id}>
                  <tr>
                    <td>
                      <div className="truncate" style={{ maxWidth: 260 }} title={f.filename}>
                        {f.filename}
                      </div>
                      {f.institution_guess && (
                        <span className="tiny dim">
                          {f.institution_guess}
                          {f.account_type_guess ? ` · ${titleCase(f.account_type_guess)}` : ''}
                        </span>
                      )}
                    </td>
                    <td><Chip>{SOURCE[f.source] || f.source}</Chip></td>
                    <td className="right num nowrap tiny dim">{bytes(f.size_bytes)}</td>
                    <td>
                      <div className="col" style={{ gap: 3, alignItems: 'flex-start' }}>
                        <Chip tone={pwTone}>{pwLabel}</Chip>
                        {f.password_redacted && (
                          <span className="tiny dim mono">{f.password_redacted}</span>
                        )}
                      </div>
                    </td>
                    <td><Chip tone={tone}>{label}</Chip></td>
                    <td className="right num">{f.transaction_count || '—'}</td>
                    <td className="nowrap tiny dim">{dateLabel(f.last_attempted_at)}</td>
                    <td>
                      <div className="row tight" style={{ justifyContent: 'flex-end' }}>
                        {canDrill && (
                          <Button size="xs"
                            onClick={() => setExpanded(expanded === f.id ? null : f.id)}>
                            {expanded === f.id ? 'Hide' : 'View rows'}
                          </Button>
                        )}
                        {canRetry && (
                          <Button variant="primary" size="xs" busy={retrying === f.id}
                            onClick={() => retry(f)}>
                            Retry
                          </Button>
                        )}
                      </div>
                      {f.parse_status === 'needs_password' && (
                        <input
                          type="text"
                          placeholder="Try a specific password…"
                          value={passwords[f.id] || ''}
                          onChange={(e) => setPasswords((p) => ({ ...p, [f.id]: e.target.value }))}
                          style={{ marginTop: 6, height: 26, fontSize: 12, width: 190 }}
                        />
                      )}
                    </td>
                  </tr>
                  {expanded === f.id && (
                    <tr className="no-hover">
                      <td colSpan={8} style={{ padding: 0, background: 'var(--surface-2)' }}>
                        <FileRows id={f.id} />
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </Table>
      </Card>

      <Callout>
        A password that works is remembered against the file&apos;s own content, so the
        next load opens it directly — even under a different filename. Retrying re-parses
        only this one file; everything else stays untouched.
      </Callout>
    </>
  );
}

function FileRows({ id }) {
  const { data, loading, error } = useQuery(`file-txns:${id}`, () => api.fileTransactions(id));
  if (loading) return <Loading label="Loading rows…" pad={14} />;
  if (error) return <div style={{ padding: 14 }}><Callout tone="neg">{error.message}</Callout></div>;
  const rows = data?.transactions || [];
  if (!rows.length) {
    return <div className="small dim" style={{ padding: 14 }}>No transactions recorded for this file.</div>;
  }
  return (
    <Table scrollY maxHeight={320} className="on-2">
      <thead>
        <tr><th>Date</th><th>Description</th><th>Category</th><th className="right">Amount</th></tr>
      </thead>
      <tbody>
        {rows.map((t) => (
          <tr key={t.id}>
            <td className="nowrap">{dateLabel(t.date)}</td>
            <td style={{ overflowWrap: 'anywhere' }}>{t.description}</td>
            <td><Chip>{titleCase(t.category)}</Chip></td>
            <td className="right num nowrap"
              style={{ color: t.direction === 'credit' ? 'var(--pos)' : 'inherit' }}>
              {t.direction === 'credit' ? '+' : '−'}{money(t.amount, true)}
            </td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

/* ═══════════════════════════════════════════════════════ manage data ════ */

/* The clearing actions, in place of one unlabelled Reset.
 *
 * The old button deleted the ledger, the file registry AND every uploaded
 * file, so somebody clearing a bad parse lost the only copy of the statement
 * that produced it. These are ordered by what it costs to get the data back -
 * seconds, CPU, network, money, or nothing at all because a person typed it -
 * and each says what it keeps as loudly as what it removes. */

/* Rows per table the preview endpoint returns; only used to decide whether to
   render the count as "200+". */
const PREVIEW_CAP = 200;

const REBUILD_SCOPES = [
  ['0', 'All time'], ['3', 'Last 3 months'], ['6', 'Last 6 months'],
  ['12', 'Last 12 months'], ['24', 'Last 24 months'],
];

function Manage() {
  const toast = useToast();
  const { data: inv, loading, refetch } = useQuery('inventory', () => api.inventory());
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(null);
  const [typed, setTyped] = useState('');
  const [previewOf, setPreviewOf] = useState(null);
  const [rebuildMonths, setRebuildMonths] = useState('0');

  if (loading) return <Loading label="Counting what is stored…" />;
  if (!inv) return null;

  const c = inv.counts || {};
  const f = inv.files || {};

  async function run(action) {
    if (action.confirm_phrase && typed !== action.confirm_phrase) {
      setPending(action.scope);
      return;
    }
    setBusy(true);
    try {
      const res = await api.clearData(action.scope, action.confirm_phrase || undefined);
      const removed = Object.entries(res.removed || {})
        .map(([t, n]) => `${n} ${t.replace(/_/g, ' ')}`).join(', ');
      toast.ok(`${action.label} done`,
        `${removed ? `Removed ${removed}. ` : 'Nothing to remove. '}`
        + `A snapshot was saved first (${res.snapshot}), so this is undoable.`);
      setPending(null);
      setTyped('');
      invalidate('inventory', 'dashboard', 'analysis', 'txns', 'files', 'coverage', 'workflow');
      refetch();
    } catch (e) { toast.fail('That did not run', e.message); }
    finally { setBusy(false); }
  }

  return (
    <>
      <div className="grid cols-4">
        {/* Counts as strings - Stat formats any number as currency. */}
        <Stat label="Transactions" value={count(c.transactions ?? 0)} />
        <Stat label="Accounts" value={String(c.accounts ?? 0)} />
        <Stat label="Statement files" value={String(f.count ?? 0)} note={bytes(f.bytes || 0)} />
        <Stat label="Your decisions" value={String(c.user_overrides ?? 0)}
          note="cannot be regenerated" tone="accent" />
      </div>

      {(f.uploaded_count > 0 || f.gmail_cached_count > 0) && (
        <Callout tone={f.uploaded_count ? 'warn' : 'pos'}>
          {f.gmail_cached_count || 0} file(s) came from Gmail and could be downloaded
          again.{' '}
          {f.uploaded_count
            ? `${f.uploaded_count} were uploaded by hand and exist nowhere else — `
              + 'clearing files destroys those permanently.'
            : 'Nothing was uploaded by hand.'}
        </Callout>
      )}

      <Card title="Clearing actions" sub="Ordered by what it costs to get the data back" pad>
        <div className="rule-list">
          {(inv.actions || []).map((a) => (
            <div key={a.scope} className="list-row" style={{ flexWrap: 'wrap' }}>
              <div className="grow">
                <div className="list-title row tight">
                  {a.label}
                  {a.destructive && <Chip tone="neg">destructive</Chip>}
                </div>
                <div className="list-sub">{a.description}</div>
                <div className="row tight" style={{ marginTop: 6 }}>
                  {(a.preserves || []).map((p) => <Chip key={p} tone="pos">keeps {p}</Chip>)}
                </div>
              </div>
              <div className="col" style={{ alignItems: 'flex-end', gap: 6 }}>
                <Button variant={a.destructive ? 'danger' : ''} disabled={busy}
                  onClick={() => run(a)}>
                  {a.label}
                </Button>
                <Button size="xs"
                  onClick={() => setPreviewOf(previewOf === a.scope ? null : a.scope)}>
                  {previewOf === a.scope ? 'Hide preview' : 'Preview data'}
                </Button>
              </div>

              {previewOf === a.scope && (
                <div style={{ flexBasis: '100%', marginTop: 12 }}>
                  <Preview scope={a.scope} />
                </div>
              )}

              {pending === a.scope && (
                <div style={{ flexBasis: '100%', marginTop: 12 }}>
                  <Callout tone="neg">
                    This removes {(a.clears || []).join(', ')}. Type{' '}
                    <strong>{a.confirm_phrase}</strong> to confirm.
                  </Callout>
                  <div className="row" style={{ marginTop: 8 }}>
                    <input autoFocus value={typed} placeholder={a.confirm_phrase}
                      onChange={(e) => setTyped(e.target.value)} style={{ flex: 1 }} />
                    <Button variant="danger solid" disabled={typed !== a.confirm_phrase || busy}
                      onClick={() => run(a)}>
                      Confirm
                    </Button>
                    <Button onClick={() => { setPending(null); setTyped(''); }}>Cancel</Button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </Card>

      <Card title="Rebuild" sub="Re-run existing statement files through the full pipeline" pad>
        <div className="list-row">
          <div className="grow">
            <div className="list-title">Rebuild the ledger from statements</div>
            <div className="list-sub">
              Drop all parsed data and re-run the statement files already on disk. Use
              after a parsing rule change. It runs in the background.
            </div>
          </div>
          <Select value={rebuildMonths} onChange={setRebuildMonths} options={REBUILD_SCOPES}
            aria-label="Rebuild scope" disabled={busy} />
          <ConfirmButton
            variant="primary" disabled={busy}
            question={`Wipe all parsed data and rebuild ${rebuildMonths === '0'
              ? 'every statement' : `the last ${rebuildMonths} months`} from the files on disk?`}
            confirmLabel="Start rebuild"
            onConfirm={async () => {
              setBusy(true);
              try {
                const res = await api.reanalyze(Number(rebuildMonths) || null);
                toast.ok('Rebuild started',
                  `Run ${res.run_id} · rebuilding ${res.file_count} file(s). `
                  + 'Refresh in a couple of minutes.');
                refetch();
              } catch (e) { toast.fail('The rebuild did not start', e.message); }
              finally { setBusy(false); }
            }}
          >
            Start rebuild
          </ConfirmButton>
        </div>
      </Card>

      <Card title="Snapshots" sub="Taken automatically before anything destructive" pad>
        {!(inv.snapshots || []).length && (
          <div className="dim small">
            No snapshots yet — one is written the first time you clear anything.
          </div>
        )}
        <div className="rule-list">
          {(inv.snapshots || []).map((s) => (
            <div key={s.name} className="list-row">
              <div className="grow">
                <div className="truncate list-title">{s.name}</div>
                <div className="tiny dim">{stampLabel(s.created_at)} · {bytes(s.size_bytes)}</div>
              </div>
              <ConfirmButton
                disabled={busy}
                question={`Restore ${s.name}? The state before this restore is snapshotted too.`}
                confirmLabel="Restore"
                onConfirm={async () => {
                  setBusy(true);
                  try {
                    await api.restoreSnapshot(s.name);
                    toast.ok(`Restored ${s.name}`,
                      'The state before this restore was snapshotted too.');
                    invalidate('inventory', 'dashboard', 'analysis', 'txns', 'files');
                    refetch();
                  } catch (e) { toast.fail('The restore failed', e.message); }
                  finally { setBusy(false); }
                }}
              >
                Restore
              </ConfirmButton>
              <ConfirmButton
                variant="danger" disabled={busy}
                question={`Permanently delete snapshot ${s.name}?`}
                confirmLabel="Delete"
                onConfirm={async () => {
                  setBusy(true);
                  try { await api.deleteSnapshot(s.name); refetch(); }
                  catch (e) { toast.fail('It could not be deleted', e.message); }
                  finally { setBusy(false); }
                }}
              >
                Delete
              </ConfirmButton>
            </div>
          ))}
        </div>
      </Card>

      <Callout tone="acc">
        Every action on this screen takes a snapshot first, so none of them is final.
      </Callout>
    </>
  );
}

function Preview({ scope }) {
  const { data, loading, error } = useQuery(`preview:${scope}`, () => api.previewData(scope));
  if (loading) return <Loading label="Loading preview…" pad={10} />;
  if (error) {
    return <Callout tone="neg">Preview failed: {error.message}</Callout>;
  }
  const tables = Object.entries(data || {});
  if (!tables.length) {
    return <div className="small dim">Nothing stored — this action would delete no rows.</div>;
  }
  return (
    <div className="col" style={{ gap: 18 }}>
      {tables.map(([name, rows]) => (
        <div key={name}>
          <div style={{ fontWeight: 600, marginBottom: 6, textTransform: 'capitalize' }}>
            {name.replace(/_/g, ' ')}{' '}
            <span className="dim" style={{ fontWeight: 400 }}>
              ({rows.length}{rows.length >= PREVIEW_CAP ? '+' : ''} rows)
            </span>
          </div>
          {!rows.length ? (
            <div className="small dim">Table is empty.</div>
          ) : (
            <Table scrollY maxHeight={320} className="on-2">
              <thead>
                <tr>
                  {Object.keys(rows[0]).map((k) => (
                    <th key={k} style={{ textTransform: 'capitalize' }}>{k.replace(/_/g, ' ')}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    {Object.values(r).map((v, j) => (
                      <td key={j} className="nowrap truncate" style={{ maxWidth: 240 }}
                        title={String(v)}>
                        {v === null ? <span className="dim">null</span> : String(v)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </div>
      ))}
    </div>
  );
}
