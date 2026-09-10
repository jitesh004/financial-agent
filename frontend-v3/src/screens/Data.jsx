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
  unreconciled: ['warn', 'Unbalanced'],
  failed: ['neg', 'Parse Failed'],
  needs_password: ['warn', 'Password Protected'],
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
          <Stat label="Total Processed Statements" value={String(quality.files_processed ?? statements.length)} />
          <Stat
            label="Reconciled (100% Tie-out)"
            value={String(quality.files_reconciled ?? 0)}
            tone="pos"
            sub="Transactions equal net balance shift"
          />
          <Stat
            label="Unreconciled Discrepancies"
            value={String(quality.files_unreconciled ?? 0)}
            tone={quality.files_unreconciled ? 'warn' : 'pos'}
          />
          <Stat
            label="Duplicate Overlaps Removed"
            value={String(quality.duplicates_removed ?? 0)}
            sub="Overlapping statement date ranges"
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

  const rows = data?.accounts || [];
  const canFetch = Boolean(gmail?.connected);

  const allMonths = useMemo(() => {
    const set = new Set();
    rows.forEach((r) => r.months.forEach((m) => set.add(m.month)));
    return [...set].sort((a, b) => b.localeCompare(a));
  }, [rows]);

  if (loading) return <Loading label="Calculating account coverage heatmap…" />;
  if (!rows.length) return null;

  return (
    <Card title="Statement Coverage Grid" subtitle="Month-by-month historical completeness per account.">
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

                    return (
                      <td key={m} style={{ padding: 4, textAlign: 'center' }}>
                        <div
                          title={title}
                          style={{
                            width: 20,
                            height: 20,
                            borderRadius: 4,
                            background: bg,
                            margin: '0 auto',
                            border: status === 'missing' ? '1px dashed var(--neg)' : 'none',
                          }}
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
    </Card>
  );
}

/* ═══════════════════════════════════════════════════ Sub-view 2: Files ═ */

function Files({ onImport }) {
  const toast = useToast();
  const { data: filesData, loading, refetch } = useQuery('files-registry', () => api.files());
  const [filterStatus, setFilterStatus] = useState('all');
  const [search, setSearch] = useState('');
  const [passwords, setPasswords] = useState({});

  const files = filesData?.files || [];

  const filtered = useMemo(() => {
    let out = [...files];
    if (filterStatus !== 'all') out = out.filter((f) => f.status === filterStatus);
    if (search.trim()) {
      const q = search.toLowerCase();
      out = out.filter((f) => (f.filename || '').toLowerCase().includes(q));
    }
    return out;
  }, [files, filterStatus, search]);

  const handleUnlock = async (fileId) => {
    const pwd = passwords[fileId];
    if (!pwd) return;
    try {
      await api.unlockFile(fileId, pwd);
      refetch();
      invalidate('dashboard', 'coverage');
      toast.ok('File unlocked & parsed successfully');
    } catch (e) {
      toast.fail('Decryption failed', e.message);
    }
  };

  const handleDelete = async (fileId) => {
    try {
      await api.deleteFile(fileId);
      refetch();
      invalidate('dashboard', 'coverage');
      toast.ok('File and derived records deleted');
    } catch (e) {
      toast.fail('Delete failed', e.message);
    }
  };

  if (loading) return <Loading label="Loading document registry…" />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 'var(--space-3)' }}>
        <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
          <Button size="sm" variant={filterStatus === 'all' ? 'primary' : 'ghost'} onClick={() => setFilterStatus('all')}>
            All Files ({files.length})
          </Button>
          <Button size="sm" variant={filterStatus === 'needs_password' ? 'primary' : 'ghost'} onClick={() => setFilterStatus('needs_password')}>
            Password Locked
          </Button>
          <Button size="sm" variant={filterStatus === 'failed' ? 'primary' : 'ghost'} onClick={() => setFilterStatus('failed')}>
            Failed
          </Button>
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
        <div style={{ maxHeight: 540, overflowY: 'auto' }}>
          <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th>Document File</th>
                <th>File Size</th>
                <th>Import Timestamp</th>
                <th>Processing Status</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((f) => (
                <tr key={f.id} className="terminal-row">
                  <td>
                    <div className="font-semibold truncate" style={{ maxWidth: 300 }}>{f.filename}</div>
                    {f.error_message && <div className="tiny neg">{f.error_message}</div>}
                  </td>
                  <td className="num tiny muted">{bytes(f.size_bytes || 0)}</td>
                  <td className="muted tiny nowrap">{stampLabel(f.uploaded_at)}</td>
                  <td>
                    {f.status === 'needs_password' ? (
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                        <input
                          type="password"
                          className="input"
                          placeholder="Decryption password"
                          value={passwords[f.id] || ''}
                          onChange={(e) => setPasswords({ ...passwords, [f.id]: e.target.value })}
                          style={{ height: 26, fontSize: 11, width: 140 }}
                        />
                        <Button size="xs" variant="primary" onClick={() => handleUnlock(f.id)}>
                          Unlock
                        </Button>
                      </div>
                    ) : (
                      <Badge tone={f.status === 'ok' ? 'pos' : f.status === 'failed' ? 'neg' : 'warn'} size="sm">
                        {f.status}
                      </Badge>
                    )}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <ConfirmButton
                      size="xs"
                      variant="danger"
                      question="Permanently delete this file and remove its transactions?"
                      confirmLabel="Delete"
                      onConfirm={() => handleDelete(f.id)}
                    >
                      Delete
                    </ConfirmButton>
                  </td>
                </tr>
              ))}
              {!filtered.length && (
                <tr>
                  <td colSpan={5} className="muted tiny" style={{ padding: 'var(--space-6)', textAlign: 'center' }}>
                    Zero files match filter.
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

/* ═══════════════════════════════════════════════════ Sub-view 3: Manage ═ */

function Manage() {
  const toast = useToast();
  const { data: snapshotsData, refetch } = useQuery('db-snapshots', () => api.snapshots());
  const [busy, setBusy] = useState(false);
  const [snapshotLabel, setSnapshotLabel] = useState('');

  const snapshots = snapshotsData?.snapshots || [];

  const handleCreateSnapshot = async () => {
    setBusy(true);
    try {
      await api.createSnapshot(snapshotLabel.trim() || 'Manual Snapshot');
      setSnapshotLabel('');
      refetch();
      toast.ok('Snapshot sealed and persisted');
    } catch (e) {
      toast.fail('Snapshot failed', e.message);
    } finally {
      setBusy(false);
    }
  };

  const handleRestoreSnapshot = async (id) => {
    setBusy(true);
    try {
      await api.restoreSnapshot(id);
      invalidate('dashboard', 'coverage', 'txns', 'analysis');
      toast.ok('Database restored to historical snapshot');
    } catch (e) {
      toast.fail('Restore failed', e.message);
    } finally {
      setBusy(false);
    }
  };

  const handleRebuildLedger = async () => {
    setBusy(true);
    try {
      await api.rebuildLedger();
      invalidate('dashboard', 'coverage', 'txns', 'analysis');
      toast.ok('Ledger re-indexed and all heuristics re-applied');
    } catch (e) {
      toast.fail('Rebuild failed', e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Snapshots Engine */}
      <GlassCard glowing style={{ padding: 'var(--space-5)' }}>
        <h3 className="h3" style={{ margin: 0 }}>Database Snapshots</h3>
        <p className="tiny muted" style={{ margin: '4px 0 var(--space-4) 0' }}>
          Create complete immutable restore points of all parsed transactions, accounts, rules, and reviews.
        </p>

        <div style={{ display: 'flex', gap: 'var(--space-2)', maxWidth: 460 }}>
          <input
            className="input"
            placeholder="Snapshot descriptor (e.g. Pre-tax season)"
            value={snapshotLabel}
            onChange={(e) => setSnapshotLabel(e.target.value)}
          />
          <Button variant="primary" busy={busy} onClick={handleCreateSnapshot}>
            Create Snapshot
          </Button>
        </div>

        {snapshots.length > 0 && (
          <div style={{ marginTop: 'var(--space-4)' }}>
            <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th>Descriptor</th>
                  <th>Timestamp</th>
                  <th style={{ textAlign: 'right' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {snapshots.map((s) => (
                  <tr key={s.id} className="terminal-row">
                    <td className="font-semibold">{s.label || s.id}</td>
                    <td className="muted tiny">{stampLabel(s.created_at)}</td>
                    <td style={{ textAlign: 'right' }}>
                      <ConfirmButton
                        size="xs"
                        variant="primary"
                        question="Roll back current database to this snapshot?"
                        confirmLabel="Restore"
                        onConfirm={() => handleRestoreSnapshot(s.id)}
                      >
                        Restore Point
                      </ConfirmButton>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </GlassCard>

      {/* Global Maintenance */}
      <Card title="Ledger Recalculation & Re-indexing" subtitle="Re-executes transfer matching, recurring series detection, and category rules across all raw statements.">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div className="font-medium">Rebuild Entire Ledger</div>
            <div className="tiny muted">Re-stamps all series IDs, accounting months, and double-count mitigations.</div>
          </div>
          <Button variant="primary" busy={busy} onClick={handleRebuildLedger}>
            Rebuild Ledger
          </Button>
        </div>
      </Card>
    </div>
  );
}
