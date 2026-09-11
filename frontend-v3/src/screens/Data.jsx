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
  Button, Callout, Card, GlassCard, Chip, ConfirmButton, Empty, Loading, Search, Section,
  Segmented, Select, Stat, Table, Badge,
} from '../ui';
import JobProgress from '../ui/JobProgress';
import { Icon } from '../ui/icons';

const SECTIONS = [
  ['coverage', 'Coverage Matrix & Quality', 'Account coverage across monthly periods and statement balance reconciliation.'],
  ['files', 'Document Registry & Passwords', 'Every statement ever processed, including encrypted/locked files and re-parse triggers.'],
  ['manage', 'Ledger Integrity & Snapshots', 'Database freeze snapshots, cache recalculation, and reset operations.'],
];

const STATUS_MAP = {
  ok: ['pos', 'Reconciled'],
  parsed: ['pos', 'Parsed'],
  unreconciled: ['warn', 'Unbalanced'],
  failed: ['neg', 'Parse failed'],
  needs_password: ['warn', 'Password protected'],
  pending: ['', 'Pending'],
};

export default function Data({ onImport }) {
  const [section, setSection] = useRouteParam('section', 'coverage');
  const active = SECTIONS.find((s) => s[0] === section) || SECTIONS[0];

  return (
    <div className="data-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>Ledger Data & Ingestion Integrity</h1>
            <Badge tone="brand" size="sm">Audit Grade</Badge>
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            {active[2]}
          </p>
        </div>

        <Segmented
          value={section}
          onChange={setSection}
          ariaLabel="Data sub-view"
          options={SECTIONS.map(([v, l]) => [v, l])}
        />
      </div>

      {section === 'coverage' && <Coverage onImport={onImport} />}
      {section === 'files' && <Files onImport={onImport} />}
      {section === 'manage' && <Manage />}
    </div>
  );
}

/* ═══════════════════════════════════════════════════ Sub-view 1: Coverage ═ */

function Coverage({ onImport }) {
  const { data } = useDashboard();
  const statements = data?.statements || [];
  const accounts = data?.accounts || [];
  const transfers = data?.transfers || {};
  const quality = data?.data_quality || {};

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>
      <CoverageGrid />

      {/* Accounts Detected */}
      {accounts.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <Section title="Ingested Account Identities" subtitle={`${accounts.length} unique accounts verified`} />
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 260px), 1fr))', gap: 'var(--space-4)' }}>
            {accounts.map((a) => (
              <GlassCard key={a.id} style={{ padding: 'var(--space-4)' }}>
                <div className="font-semibold">{a.display_name}</div>
                <div className="tiny muted">{titleCase(a.account_type)}</div>
                <div style={{ marginTop: 'var(--space-3)' }}>
                  <div className="stat-label">{a.is_liability ? 'Outstanding Balance' : 'Liquid Balance'}</div>
                  <div className={`num font-bold ${a.is_liability ? 'neg' : ''}`} style={{ fontSize: 'var(--text-xl)' }}>
                    {money(a.is_liability ? (a.principal_outstanding ?? Math.abs(a.current_balance ?? a.balance ?? 0)) : (a.current_balance ?? a.balance))}
                  </div>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 'var(--space-3)' }}>
                  {a.interest_rate && <Chip size="sm">{a.interest_rate}% p.a.</Chip>}
                  {a.emi_amount && <Chip size="sm">EMI: {money(a.emi_amount)}</Chip>}
                  {a.credit_limit && <Chip size="sm">Limit: {money(a.credit_limit)}</Chip>}
                  {a.holder_name && <Chip size="sm">{a.holder_name}</Chip>}
                </div>
              </GlassCard>
            ))}
          </div>
        </div>
      )}

      {/* Reconciliation Quality Stats */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
        <Section title="Mathematical Reconciliation Telemetry" subtitle="Audited statement balance movement" />
        <div className="stats-grid">
          <Stat label="Total Processed Statements" value={count(quality.files_processed ?? statements.length)} />
          <Stat
            label="Reconciled (100% Tie-out)"
            value={count(quality.files_reconciled ?? 0)}
            tone="pos"
            sub="Transactions equal net balance shift"
          />
          <Stat
            label="Unreconciled Discrepancies"
            value={count(quality.files_unreconciled ?? 0)}
            tone={quality.files_unreconciled ? 'warn' : 'pos'}
          />
          <Stat
            label="Rows Needing Review"
            value={count(quality.needs_review ?? 0)}
            tone={quality.needs_review ? 'warn' : 'pos'}
            sub={`${count(quality.uncategorized ?? 0)} still uncategorised`}
          />
        </div>

        {statements.length > 0 && (
          <Card pad={false}>
            <div style={{ maxHeight: 380, overflow: 'auto' }}>
              <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th>Statement Document</th>
                    <th>Parser Layout</th>
                    <th>Statement Period</th>
                    <th style={{ textAlign: 'right' }}>Parsed Rows</th>
                    <th style={{ textAlign: 'right' }}>Opening</th>
                    <th style={{ textAlign: 'right' }}>Closing</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {statements.map((s, i) => {
                    const [tone, label] = STATUS_MAP[s.status] || ['', s.status];
                    return (
                      <tr key={i} className="terminal-row">
                        <td className="font-medium truncate" style={{ maxWidth: 220 }}>{s.filename}</td>
                        <td><Chip size="sm">{s.parser || s.source}</Chip></td>
                        <td className="nowrap muted tiny">{s.period || '—'}</td>
                        <td className="num nowrap" style={{ textAlign: 'right' }}>{s.transaction_count}</td>
                        <td className="num nowrap" style={{ textAlign: 'right' }}>{s.opening_balance != null ? money(s.opening_balance) : '—'}</td>
                        <td className="num nowrap" style={{ textAlign: 'right' }}>{s.closing_balance != null ? money(s.closing_balance) : '—'}</td>
                        <td><Badge tone={tone || 'brand'} size="sm">{label}</Badge></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>

      {/* Matched Internal Transfer Pairs */}
      {transfers.pairs?.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <Section
            title="Matched Inter-Account Transfer Pairs"
            subtitle={`${money(transfers.double_count_avoided)} in double-counting prevented.`}
          />
          <Card pad={false}>
            <div style={{ maxHeight: 300, overflow: 'auto' }}>
              <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Source Account (Debit)</th>
                    <th>Destination Account (Credit)</th>
                    <th style={{ textAlign: 'right' }}>Amount</th>
                    <th style={{ textAlign: 'right' }}>Day Delta</th>
                  </tr>
                </thead>
                <tbody>
                  {transfers.pairs.map((p) => (
                    <tr key={p.pair_id} className="terminal-row">
                      <td><Chip tone="brand" size="sm">{p.kind.replace(/_/g, ' ')}</Chip></td>
                      <td className="truncate" style={{ maxWidth: 200 }}>{p.from}</td>
                      <td className="truncate" style={{ maxWidth: 200 }}>{p.to}</td>
                      <td className="num pos font-semibold nowrap" style={{ textAlign: 'right' }}>{money(p.amount)}</td>
                      <td className="num nowrap" style={{ textAlign: 'right' }}>{p.day_gap}d</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}

function CoverageGrid() {
  const toast = useToast();
  const { data, loading, refetch } = useQuery('coverage', () => api.coverage());
  const { data: gmail } = useQuery('gmail-status', () => api.gmailStatus());
  const [busyCell, setBusyCell] = useState(null);
  /* Which cell has its rows open, as "accountId:month". One at a time:
     two open months push the grid off the screen and neither is readable. */
  const [openMonth, setOpenMonth] = useState(null);

  const rows = data?.accounts || [];
  const canFetch = Boolean(gmail?.connected);

  /* An amber cell has a file behind it that would not parse. Retrying is
     worth offering because the commonest cause is a password that has
     since been supplied. */
  async function retryCell(fileId, cellKey) {
    setBusyCell(cellKey);
    try {
      await api.retryFile(fileId);
      invalidate('coverage', 'files-registry', 'dashboard');
      await refetch();
      toast.ok('Re-read that file', 'The grid has been refreshed.');
    } catch (e) {
      toast.fail('That file could not be re-read', e.message);
    } finally {
      setBusyCell(null);
    }
  }

  /* A red cell is a month nothing was ever imported for. With a mailbox
     connected, the app can go and look for exactly that one. */
  async function fetchCell(accountId, month, cellKey, label) {
    setBusyCell(cellKey);
    try {
      const { job_id: jobId } = await api.fetchMonth(accountId, month);
      if (!jobId) throw new Error('Nothing was started.');
      toast.ok('Searching your mailbox', `${label} · ${monthLabel(month)}`);
      invalidate('coverage', 'files-registry');
      await refetch();
    } catch (e) {
      toast.fail('That month could not be fetched', e.message);
    } finally {
      setBusyCell(null);
    }
  }

  const allMonths = useMemo(() => {
    const set = new Set();
    rows.forEach((r) => (r.months || []).forEach((m) => set.add(m.month)));
    return [...set].sort((a, b) => b.localeCompare(a));
  }, [rows]);

  /* What the grid is actually saying, counted rather than left to be read
     off 200-odd 20px squares. A screen whose entire content is a heatmap
     has no headline, so "how complete am I?" - the one question it exists
     to answer - was the one thing it never said. */
  const tally = useMemo(() => {
    let parsed = 0; let failed = 0; let missing = 0;
    rows.forEach((r) => (r.months || []).forEach((m) => {
      const st = m.status || 'missing';
      if (st === 'parsed' || st === 'ok') parsed += 1;
      else if (st === 'failed' || st === 'unreconciled') failed += 1;
      else missing += 1;
    }));
    return { parsed, failed, missing, total: parsed + failed + missing };
  }, [rows]);

  const locked = data?.locked || [];

  if (loading) return <Loading message="Calculating account coverage heatmap…" />;
  if (!rows.length) return null;

  return (
    <Card title="Statement Coverage Grid" subtitle="Month-by-month historical completeness per account.">
      <div className="flex items-center gap-4 flex-wrap" style={{ marginBottom: 12, fontSize: 13 }}>
        <span>
          <strong className="tabular-nums">{tally.parsed}</strong>
          <span className="text-3"> of {tally.total} account-months imported</span>
        </span>
        {tally.failed > 0 && (
          <span style={{ color: 'var(--warn)' }}>
            <strong className="tabular-nums">{tally.failed}</strong> did not reconcile
          </span>
        )}
        {tally.missing > 0 && (
          <span style={{ color: 'var(--neg)' }}>
            <strong className="tabular-nums">{tally.missing}</strong> missing
          </span>
        )}
      </div>

      {/* A locked file produces no account and no month, so it appears in
          none of the rows below and the month it covers is drawn as
          "missing" - the same cell a month you never uploaded gets. Those
          are different facts, and conflating them sends someone hunting for
          statements they have already handed over. */}
      {locked.length > 0 && (
        <Callout tone="warn">
          <strong className="tabular-nums">{locked.length}</strong>
          {' '}uploaded {locked.length === 1 ? 'file is' : 'files are'} password-protected
          and could not be opened, so {locked.length === 1 ? 'it does' : 'they do'} not
          appear in this grid. Some of the {tally.missing} months shown as missing may
          already be covered by {locked.length === 1 ? 'it' : 'them'} &mdash; add the
          passwords under Files to find out.
          <div className="text-xs text-3" style={{ marginTop: 6 }}>
            {locked.slice(0, 6).map((f) => f.filename).join(', ')}
            {locked.length > 6 ? ` and ${locked.length - 6} more` : ''}
          </div>
        </Callout>
      )}

      <div style={{ overflowX: 'auto', paddingBottom: 8 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '8px 12px', fontSize: 12, color: 'var(--text-3)' }}>Account</th>
              {allMonths.map((m) => (
                <th key={m} style={{ padding: '6px', fontSize: 11, textAlign: 'center', color: 'var(--text-3)' }}>
                  {monthLabel(m)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const accountLabel = r.display_name || r.name || r.masked || 'Account';
              return (
                <tr key={r.account_id || r.id || accountLabel} style={{ borderTop: '1px solid var(--border-subtle)' }}>
                  <td style={{ padding: '8px 12px', fontSize: 13, fontWeight: 500, whiteSpace: 'nowrap' }}>
                    {accountLabel}
                  </td>
                  {allMonths.map((m) => {
                    const cell = (r.months || []).find((x) => x.month === m);
                    const status = cell?.status || 'missing';

                    let bg = 'transparent';
                    let title = `${accountLabel}: ${monthLabel(m)}`;
                    if (status === 'parsed' || status === 'ok') {
                      bg = 'var(--pos)';
                      title += ' (Statement reconciled)';
                    } else if (status === 'failed' || status === 'unreconciled') {
                      bg = 'var(--warn)';
                      title += ' (Statement unreconciled)';
                    } else if (status === 'missing') {
                      bg = 'rgba(239, 68, 68, 0.25)';
                      title += ' (Missing statement)';
                    }

                    /* What this particular cell can DO, which is not the
                       same as its colour. A failed cell with no file behind
                       it has nothing to retry, and a missing one has
                       nowhere to look without a mailbox - and a cell that
                       cannot act must not look like it can, or clicking it
                       reads as a dead button. */
                    const action = status === 'parsed' || status === 'ok'
                      ? 'rows'
                      : (status === 'failed' || status === 'unreconciled') && cell?.file_id
                        ? 'retry'
                        : status === 'missing' && canFetch ? 'fetch' : null;
                    const cellKey = `${r.account_id}:${m}`;
                    const isOpen = openMonth === cellKey;

                    if (action === 'rows') title += ' — click to see its rows';
                    else if (action === 'retry') title += ' — click to retry the file';
                    else if (action === 'fetch') title += ' — click to search your mailbox';

                    return (
                      <td key={m} style={{ padding: 4, textAlign: 'center' }}>
                        <button
                          type="button"
                          title={title}
                          disabled={!action || busyCell === cellKey}
                          aria-expanded={action === 'rows' ? isOpen : undefined}
                          onClick={() => {
                            if (action === 'rows') setOpenMonth(isOpen ? null : cellKey);
                            else if (action === 'retry') retryCell(cell.file_id, cellKey);
                            else if (action === 'fetch') fetchCell(r.account_id, m, cellKey, accountLabel);
                          }}
                          style={{
                            width: 20,
                            height: 20,
                            padding: 0,
                            borderRadius: 4,
                            background: bg,
                            display: 'block',
                            margin: '0 auto',
                            border: isOpen
                              ? '2px solid var(--text)'
                              : status === 'missing' ? '1px dashed var(--neg)' : '1px solid transparent',
                            cursor: action ? 'pointer' : 'default',
                            opacity: busyCell === cellKey ? 0.4 : 1,
                            transition: 'transform var(--t-fast) var(--e-out)',
                          }}
                          onMouseEnter={(e) => {
                            if (action) e.currentTarget.style.transform = 'scale(1.25)';
                          }}
                          onMouseLeave={(e) => { e.currentTarget.style.transform = ''; }}
                        />
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* The rows a green cell stands for.

          A heatmap that cannot be opened is a claim without evidence: it
          says a month parsed and gives no way to see what it brought in.
          Rendered under the grid rather than inside the table, because a
          full-width drawer inside a horizontally scrolling table scrolls
          away from the cell that opened it. */}
      {openMonth && (
        <MonthRows
          accountId={openMonth.slice(0, openMonth.lastIndexOf(':'))}
          month={openMonth.slice(openMonth.lastIndexOf(':') + 1)}
          label={(rows.find((r) => r.account_id === openMonth.slice(0, openMonth.lastIndexOf(':')))
            || {}).display_name || 'Account'}
          onClose={() => setOpenMonth(null)}
        />
      )}
    </Card>
  );
}


/* Every transaction the app counted for one account in one month.

   Asked by accounting_month, not by statement, because that is the month
   the rest of the app files those rows under - a statement spanning a
   cycle boundary contributes to two, and showing "the statement's rows"
   would disagree with every total elsewhere. */
function MonthRows({ accountId, month, label, onClose }) {
  const { data, loading, error } = useQuery(
    `cov-rows:${accountId}:${month}`,
    () => api.transactions({
      account_id: accountId, accounting_month: month,
      limit: 500, sort_by: 'date', sort_dir: 'asc',
    }),
  );
  const rows = data?.transactions || [];

  return (
    <div style={{
      marginTop: 12,
      border: '1px solid var(--border-subtle)',
      borderRadius: 'var(--radius-md)',
      background: 'var(--surface-2)',
      overflow: 'hidden',
    }}>
      <div className="flex items-center gap-2 flex-wrap"
        style={{ padding: '10px 14px', borderBottom: '1px solid var(--border-subtle)' }}>
        <strong style={{ fontSize: 13 }}>{label}</strong>
        <span className="text-3">·</span>
        <span style={{ fontSize: 13 }}>{monthLabel(month)}</span>
        {data && <Chip size="sm">{count(data.total)} row{data.total === 1 ? '' : 's'}</Chip>}
        <span style={{ flex: 1 }} />
        <Button size="xs" variant="ghost" onClick={onClose}>Hide</Button>
      </div>

      {loading && <Loading message="Reading that month…" />}
      {error && <div style={{ padding: 12 }}><Callout tone="neg">{error.message}</Callout></div>}

      {data && !rows.length && (
        <div className="tiny text-3" style={{ padding: '12px 14px' }}>
          A statement covering this month parsed, but no transaction is
          counted in it. That is a real answer, not a missing one &mdash; a
          card with no spending in a cycle looks exactly like this.
        </div>
      )}

      {rows.length > 0 && (
        <div className="table-wrapper" style={{ maxHeight: 320, overflow: 'auto' }}>
          <table className="terminal-table compact">
            <thead>
              <tr>
                <th style={{ width: 95 }}>Date</th>
                <th>Description</th>
                <th style={{ width: 150 }}>Category</th>
                <th style={{ width: 120, textAlign: 'right' }}>Amount</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.id}>
                  <td className="nowrap">{dateLabel(t.date)}</td>
                  <td style={{ overflowWrap: 'anywhere' }}>{t.description}</td>
                  <td><Chip size="sm">{titleCase(t.category)}</Chip></td>
                  <td className="num nowrap" style={{
                    textAlign: 'right',
                    color: t.direction === 'credit' ? 'var(--pos)' : 'inherit',
                  }}>
                    {t.direction === 'credit' ? '+' : '\u2212'}{money(t.amount)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════ Sub-view 2: Files ═ */

function Files({ onImport }) {
  const toast = useToast();
  /* GET /api/files answers with a bare array; reading `.files` off it left the
     registry permanently empty even with hundreds of documents on record. */
  const { data: files = [], loading, refetch } = useQuery('files-registry', () => api.files());
  const [filterStatus, setFilterStatus] = useState('all');
  const [search, setSearch] = useState('');
  const [passwords, setPasswords] = useState({});
  const [busy, setBusy] = useState(null);

  const counts = useMemo(() => {
    const out = { all: files.length, needs_password: 0, failed: 0, parsed: 0 };
    for (const f of files) {
      if (out[f.parse_status] != null) out[f.parse_status] += 1;
    }
    return out;
  }, [files]);

  const filtered = useMemo(() => {
    let out = files;
    if (filterStatus !== 'all') out = out.filter((f) => f.parse_status === filterStatus);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      out = out.filter((f) => (f.filename || '').toLowerCase().includes(q)
        || (f.institution_guess || '').toLowerCase().includes(q));
    }
    return out;
  }, [files, filterStatus, search]);

  /* Re-parsing is the only server-side action a file supports; there is no
     delete endpoint, so no delete button is offered. */
  const retry = async (file) => {
    setBusy(file.id);
    try {
      const result = await api.retryFile(file.id, passwords[file.id] || '');
      await refetch();
      invalidate('dashboard', 'coverage', 'analysis', 'txns', 'workflow');
      setPasswords((prev) => ({ ...prev, [file.id]: '' }));
      toast.ok(
        result?.status === 'needs_password' ? 'Still locked' : 'File re-parsed',
        result?.message || file.filename,
      );
    } catch (e) {
      toast.fail('Could not re-parse', e.message);
    } finally {
      setBusy(null);
    }
  };

  if (loading) return <Loading message="Loading document registry…" />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 'var(--space-3)' }}>
        <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
          {[
            ['all', `All files (${counts.all})`],
            ['needs_password', `Password locked (${counts.needs_password})`],
            ['failed', `Failed (${counts.failed})`],
            ['parsed', `Parsed (${counts.parsed})`],
          ].map(([key, label]) => (
            <Button
              key={key}
              size="sm"
              variant={filterStatus === key ? 'primary' : 'ghost'}
              onClick={() => setFilterStatus(key)}
            >
              {label}
            </Button>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'center' }}>
          <Search value={search} onChange={setSearch} placeholder="Search file names…" style={{ width: 220 }} />
          {onImport && (
            <Button variant="primary" size="sm" icon="upload" onClick={onImport}>
              Upload Files
            </Button>
          )}
        </div>
      </div>

      <Card pad={false}>
        <div className="table-wrapper" style={{ maxHeight: 540, overflowY: 'auto' }}>
          <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th>Document file</th>
                <th>Detected account</th>
                <th style={{ textAlign: 'right' }}>Rows</th>
                <th style={{ textAlign: 'right' }}>Size</th>
                <th>First seen</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 400).map((f) => {
                const locked = f.parse_status === 'needs_password';
                const [tone, label] = STATUS_MAP[f.parse_status] || ['', f.parse_status || 'pending'];
                return (
                  <tr key={f.id} className="terminal-row">
                    <td>
                      <div className="font-semibold truncate" style={{ maxWidth: 300 }} title={f.filename}>
                        {f.filename}
                      </div>
                      <div className="tiny muted truncate" style={{ maxWidth: 300 }}>
                        {f.source === 'gmail' ? f.sender || 'Gmail' : titleCase(f.source || 'upload')}
                      </div>
                      {f.error_message && <div className="tiny neg">{f.error_message}</div>}
                    </td>
                    <td className="tiny muted truncate" style={{ maxWidth: 190 }}>
                      {f.institution_guess || '—'}
                    </td>
                    <td className="num nowrap" style={{ textAlign: 'right' }}>
                      {f.transaction_count ?? 0}
                    </td>
                    <td className="num tiny muted nowrap" style={{ textAlign: 'right' }}>{bytes(f.size_bytes || 0)}</td>
                    <td className="muted tiny nowrap">{stampLabel(f.first_seen_at)}</td>
                    <td><Badge tone={tone || 'brand'} size="sm">{label}</Badge></td>
                    <td style={{ textAlign: 'right' }}>
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center', justifyContent: 'flex-end' }}>
                        {locked && (
                          <input
                            type="password"
                            className="input"
                            placeholder="PDF password"
                            value={passwords[f.id] || ''}
                            onChange={(e) => setPasswords((prev) => ({ ...prev, [f.id]: e.target.value }))}
                            size="xs" style={{ width: 130 }}
                          />
                        )}
                        <Button
                          size="xs"
                          variant={locked ? 'primary' : 'secondary'}
                          busy={busy === f.id}
                          onClick={() => retry(f)}
                        >
                          {locked ? 'Unlock' : 'Re-parse'}
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {!filtered.length && (
                <tr>
                  <td colSpan={7} className="muted tiny" style={{ padding: 'var(--space-6)', textAlign: 'center' }}>
                    No files match this filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {filtered.length > 400 && (
          <div className="tiny muted" style={{ padding: '10px 16px', borderTop: '1px solid var(--line)' }}>
            Showing the first 400 of {count(filtered.length)} matching files.
          </div>
        )}
      </Card>
    </div>
  );
}

/* ═══════════════════════════════════════════════════ Sub-view 3: Manage ═ */

function Manage() {
  const toast = useToast();
  /* The server owns the list of clearing actions, the snapshot each one takes
     first, and the confirmation phrase the destructive ones require. */
  const { data: inventory, loading, refetch } = useQuery('data-inventory', () => api.inventory());
  const [busy, setBusy] = useState(null);
  const [typed, setTyped] = useState({});

  const counts = inventory?.counts || {};
  const actions = inventory?.actions || [];
  const snapshots = inventory?.snapshots || [];
  const fileStats = inventory?.files || {};

  const refreshAll = () => {
    invalidate('dashboard', 'coverage', 'analysis', 'txns', 'workflow',
      'files-registry', 'data-inventory', 'recurring', 'position');
    return refetch();
  };

  const run = async (work, okTitle, okDetail) => {
    try {
      const result = await work();
      await refreshAll();
      toast.ok(okTitle, okDetail || result?.message);
      return result;
    } catch (e) {
      toast.fail(okTitle.replace(/ed$/, ' failed'), e.message);
      return null;
    } finally {
      setBusy(null);
    }
  };

  if (loading) return <Loading message="Reading ledger inventory…" />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* What is currently stored */}
      <div className="stats-grid">
        <Stat label="Transactions" value={count(counts.transactions ?? 0)} sub="Rows in the ledger" />
        <Stat label="Accounts" value={count(counts.accounts ?? 0)} sub="Distinct account identities" />
        <Stat label="Statements" value={count(counts.statements ?? 0)} sub="Reconciled statement periods" />
        <Stat
          label="Stored Documents"
          value={count(fileStats.count ?? counts.source_files ?? 0)}
          sub={bytes(fileStats.bytes || 0)}
        />
      </div>

      {/* Re-run the analysis pipeline */}
      <Card
        title="Ledger Recalculation & Re-indexing"
        subtitle="Re-runs transfer matching, recurring detection and category rules over the stored statements."
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 'var(--space-3)' }}>
          <div>
            <div className="font-medium">Re-analyse the whole ledger</div>
            <div className="tiny muted">Nothing is deleted — totals, series IDs and accounting months are recomputed.</div>
          </div>
          <Button
            variant="primary"
            busy={busy === 'reanalyze'}
            onClick={() => { setBusy('reanalyze'); run(() => api.reanalyze(), 'Ledger re-analysed'); }}
          >
            Re-analyse Ledger
          </Button>
        </div>
      </Card>

      {/* Scoped clearing actions, straight from the server's own catalogue */}
      <Card
        title="Clear Stored Data"
        subtitle="Each action snapshots the database first, so anything cleared here can be rolled back below."
        pad={false}
      >
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {actions.map((action) => {
            const needsPhrase = Boolean(action.confirm_phrase);
            const phraseOk = !needsPhrase
              || (typed[action.scope] || '').trim() === action.confirm_phrase;
            return (
              <div
                key={action.scope}
                style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
                  gap: 'var(--space-4)', flexWrap: 'wrap',
                  padding: 'var(--space-4)', borderTop: '1px solid var(--line)',
                }}
              >
                <div style={{ flex: 1, minWidth: 240 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="font-semibold">{action.label}</span>
                    {action.destructive && <Badge tone="neg" size="sm">Destructive</Badge>}
                  </div>
                  <div className="tiny muted" style={{ marginTop: 4 }}>{action.description}</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                    {(action.clears || []).map((c) => (
                      <Chip key={c} tone="neg" size="sm">clears {c}</Chip>
                    ))}
                    {(action.preserves || []).map((c) => (
                      <Chip key={c} tone="pos" size="sm">keeps {c}</Chip>
                    ))}
                  </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  {needsPhrase && (
                    <input
                      className="input"
                      placeholder={action.confirm_phrase}
                      value={typed[action.scope] || ''}
                      onChange={(e) => setTyped((prev) => ({ ...prev, [action.scope]: e.target.value }))}
                      style={{ width: 190, height: 30, fontSize: 12 }}
                    />
                  )}
                  <ConfirmButton
                    size="sm"
                    variant={action.destructive ? 'danger' : 'secondary'}
                    disabled={!phraseOk || busy === action.scope}
                    question={`Run "${action.label}" now?`}
                    confirmLabel="Run"
                    onConfirm={() => {
                      setBusy(action.scope);
                      return run(
                        () => api.clearData(action.scope, action.confirm_phrase || undefined),
                        `${action.label} completed`,
                      );
                    }}
                  >
                    {action.label}
                  </ConfirmButton>
                </div>
              </div>
            );
          })}
          {!actions.length && (
            <div className="muted tiny" style={{ padding: 'var(--space-5)', textAlign: 'center' }}>
              No clearing actions are available.
            </div>
          )}
        </div>
      </Card>

      {/* Restore points */}
      <Card
        title="Restore Points"
        subtitle="Automatic snapshots taken immediately before each clearing action."
        pad={false}
      >
        <div className="table-wrapper" style={{ maxHeight: 320, overflowY: 'auto' }}>
          <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th>Snapshot</th>
                <th>Taken</th>
                <th style={{ textAlign: 'right' }}>Size</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {snapshots.map((snap) => (
                <tr key={snap.name} className="terminal-row">
                  <td className="font-medium truncate" style={{ maxWidth: 340 }} title={snap.name}>
                    {snap.name}
                  </td>
                  <td className="muted tiny nowrap">{stampLabel(snap.created_at)}</td>
                  <td className="num tiny muted nowrap" style={{ textAlign: 'right' }}>{bytes(snap.size_bytes)}</td>
                  <td style={{ textAlign: 'right' }}>
                    <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                      <ConfirmButton
                        size="xs"
                        variant="primary"
                        question="Roll the database back to this snapshot?"
                        confirmLabel="Restore"
                        disabled={busy === snap.name}
                        onConfirm={() => {
                          setBusy(snap.name);
                          return run(() => api.restoreSnapshot(snap.name), 'Database restored');
                        }}
                      >
                        Restore
                      </ConfirmButton>
                      <ConfirmButton
                        size="xs"
                        variant="danger"
                        question="Delete this restore point permanently?"
                        confirmLabel="Delete"
                        disabled={busy === snap.name}
                        onConfirm={() => {
                          setBusy(snap.name);
                          return run(() => api.deleteSnapshot(snap.name), 'Snapshot deleted');
                        }}
                      >
                        Delete
                      </ConfirmButton>
                    </div>
                  </td>
                </tr>
              ))}
              {!snapshots.length && (
                <tr>
                  <td colSpan={4} className="muted tiny" style={{ padding: 'var(--space-5)', textAlign: 'center' }}>
                    No restore points yet — one is created automatically the first time you clear anything.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
