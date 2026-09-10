/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 - Staged Documents Review & Ledger Build Steps
   Step 5 (Review): What has been read into staging, verification & gates.
   Step 6 (Build): Deterministic compilation of the selected documents into the active ledger.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../core/api';
import { count, money } from '../../core/format';
import {
  Badge, Button, Callout, Card, Chip, ConfirmButton, Empty, Icon, IconButton, Loading,
} from '../../ui';
import JobProgress from '../../ui/JobProgress';

const TYPE_LABEL = {
  credit_card: 'Credit Card',
  savings: 'Savings Account',
  current: 'Current Account',
  loan: 'Loan / Mortgage',
  investment: 'Investment Portfolio',
  credit_report: 'Credit Bureau Report',
  unknown: 'Unclassified Document',
};

const RECON_WORDS = {
  passed: 'balances perfectly',
  ok: 'balances perfectly',
  failed: 'does not balance with printed totals',
  unreconciled: 'no printed total to verify against',
  not_applicable: 'no balances printed',
};

function fileNote(file) {
  if (file.superseded_by) {
    return `Superseded by ${file.superseded_by_name || 'a formal statement'} — the statement covering these dates is the primary verified record.`;
  }
  if (file.parse_status === 'needs_password') {
    return 'Encrypted / Password locked. No password derived from your profile opened it.';
  }
  if (file.parse_status === 'failed') return file.parse_message || 'Could not parse this document format.';
  if (file.parse_status === 'empty') return 'Document was read, but no transaction records were found.';
  if (file.parse_status === 'pending') return 'Queued for parsing.';
  if (file.recon_status === 'failed') {
    return file.parse_message || 'The sum of transaction rows does not match the statement opening/closing balance.';
  }
  if (!file.row_count) {
    return file.parse_message || 'Parsed successfully, but 0 valid records were extracted.';
  }
  return null;
}

function fileWarnings(file) {
  const warnings = file.warnings || [];
  if (!warnings.length) return null;
  const note = fileNote(file);
  return warnings.filter((w) => w !== note);
}

export function ReviewStep({ onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(() => new Set());
  const auto = useRef(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const next = await api.stagingReview();
      setData(next);
      onChanged?.(next);
      if (!auto.current) {
        auto.current = true;
        const notable = (next.groups || []).filter(
          (g) => !g.row_count || g.failed_count > 0 || g.unbalanced_count > 0
            || (g.files || []).some((f) => (f.warnings || []).length > 0)
        );
        if (notable.length) setOpen(new Set(notable.map((g) => g.key)));
      }
    } catch (e) {
      setError(e.message);
    }
  }, [onChanged]);

  useEffect(() => { load(); }, [load]);

  const groups = data?.groups || [];

  const totals = useMemo(() => {
    const months = new Set();
    let rows = 0;
    for (const g of groups) {
      if (!g.included) continue;
      if (g.kind === 'statement' || g.kind === 'alert') rows += (g.row_count || 0);
      if (g.first) months.add(g.first.slice(0, 7));
      if (g.last) months.add(g.last.slice(0, 7));
    }
    const sorted = [...months].sort();
    return { rows, from: sorted[0], to: sorted[sorted.length - 1] };
  }, [groups]);

  const send = async (body) => {
    setBusy(true);
    try {
      await api.stagingSelect(body);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const forget = async (ids) => {
    setBusy(true);
    try {
      await api.stagingRemove(ids);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (error) return <Callout tone="neg">{error}</Callout>;
  if (!data) return <Loading message="Inspecting staged financial documents…" />;
  if (!groups.length) {
    return (
      <Empty title="No Documents Staged Yet" icon="inbox">
        Scan your linked mailbox or drop PDF/CSV files in the Source step.
        Everything parsed lands here in an isolated staging sandbox before touching your active ledger.
      </Empty>
    );
  }

  const off = groups.filter((g) => !g.included).length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <p className="lead" style={{ margin: 0 }}>
        All parsed documents grouped by verified institution account.{' '}
        <strong style={{ color: 'var(--text)' }}>None of this is in your active ledger yet.</strong>{' '}
        Review the reconciliation status, include or exclude specific instruments, and proceed to build.
      </p>

      {/* Summary Toolbar */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 8,
          padding: '10px 14px',
          borderRadius: 'var(--r)',
          background: 'var(--surface-2)',
          border: '1px solid var(--line)',
        }}
      >
        <Badge tone="acc">{count(totals.rows)} rows will count</Badge>
        {totals.from && <Badge>{totals.from} → {totals.to}</Badge>}
        {off > 0 && <Badge tone="warn">{off} group{off === 1 ? '' : 's'} excluded</Badge>}
        {data.superseded > 0 && <Badge tone="warn">{data.superseded} superseded by statements</Badge>}

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
          <Button
            size="sm"
            disabled={busy}
            onClick={() => send({ groups: groups.map((g) => ({ key: g.key, include: true })) })}
          >
            Include all
          </Button>
          <Button
            size="sm"
            disabled={busy}
            onClick={() => send({ groups: groups.map((g) => ({ key: g.key, include: false })) })}
          >
            Exclude all
          </Button>
        </div>
      </div>

      {/* Group List */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {groups.map((group) => {
          const expanded = open.has(group.key);
          const hasIssues = group.failed_count > 0 || group.unbalanced_count > 0;

          return (
            <div
              key={group.key}
              style={{
                borderRadius: 'var(--r)',
                background: 'var(--surface-2)',
                border: `1px solid ${hasIssues ? 'var(--warn-border)' : 'var(--line)'}`,
                overflow: 'hidden',
                transition: 'border-color var(--t-fast)',
              }}
            >
              {/* Group Header Row */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  padding: '12px 16px',
                  background: group.included ? 'transparent' : 'rgba(0,0,0,0.15)',
                  opacity: group.included ? 1 : 0.65,
                }}
              >
                <input
                  type="checkbox"
                  checked={group.included}
                  disabled={busy}
                  ref={(el) => { if (el) el.indeterminate = Boolean(group.partial); }}
                  onChange={() => send({ groups: [{ key: group.key, include: !group.included }] })}
                  style={{
                    width: 16,
                    height: 16,
                    accentColor: 'var(--accent)',
                    cursor: busy ? 'not-allowed' : 'pointer',
                  }}
                />

                <IconButton
                  icon={expanded ? 'chevron-down' : 'chevron-right'}
                  size="sm"
                  label={expanded ? 'Collapse files' : 'Expand files'}
                  onClick={() => setOpen((p) => {
                    const next = new Set(p);
                    if (next.has(group.key)) next.delete(group.key);
                    else next.add(group.key);
                    return next;
                  })}
                />

                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                    <strong style={{ fontSize: 13.5, color: 'var(--text)' }}>
                      {group.account_label}
                    </strong>
                    <span style={{ fontSize: 11.5, color: 'var(--text-3)', fontWeight: 500 }}>
                      {TYPE_LABEL[group.account_type] || group.account_type}
                    </span>
                    <Badge size="sm">{group.kind_label}</Badge>
                    <Badge size="sm">
                      {group.selected_count} of {group.file_count} file{group.file_count === 1 ? '' : 's'}
                    </Badge>
                    {group.row_count > 0 && (
                      <Badge tone="pos" size="sm">{group.row_count} records</Badge>
                    )}
                    {!group.row_count && !group.failed_count && !group.superseded_count && (
                      <Badge tone="warn" size="sm">No records extracted</Badge>
                    )}
                    {group.failed_count > 0 && (
                      <Badge tone="neg" size="sm">{group.failed_count} unreadable</Badge>
                    )}
                    {group.unbalanced_count > 0 && (
                      <Badge tone="warn" size="sm">{group.unbalanced_count} unbalanced</Badge>
                    )}
                    {group.superseded_count > 0 && (
                      <Badge tone="warn" size="sm">{group.superseded_count} superseded</Badge>
                    )}
                  </div>

                  {(group.first || group.kind_note) && (
                    <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 4 }}>
                      {group.first ? `${group.first} → ${group.last}` : ''}
                      {group.first && group.kind_note ? ' · ' : ''}
                      {group.kind_note}
                    </div>
                  )}
                </div>

                {/* Amount Totals */}
                <div style={{ textAlign: 'right', fontSize: 12.5, whiteSpace: 'nowrap' }}>
                  {Number(group.debits) > 0 && (
                    <div style={{ color: 'var(--neg)', fontWeight: 600 }}>
                      −{money(group.debits)}
                    </div>
                  )}
                  {Number(group.credits) > 0 && (
                    <div style={{ color: 'var(--pos)', fontWeight: 600 }}>
                      +{money(group.credits)}
                    </div>
                  )}
                </div>

                {/* Forget Group */}
                <ConfirmButton
                  size="sm"
                  disabled={busy}
                  title="Remove all documents in this group from staging"
                  question={`Forget all ${group.file_count} documents for ${group.account_label}?`}
                  confirmLabel="Forget group"
                  onConfirm={() => forget(group.files.map((f) => f.id))}
                >
                  Forget
                </ConfirmButton>
              </div>

              {/* Nested Files Sub-List */}
              {expanded && (
                <div style={{ borderTop: '1px solid var(--line)', background: 'var(--surface-3)' }}>
                  {group.files.map((file) => {
                    const note = fileNote(file);
                    const dead = Boolean(file.superseded_by);
                    const isUnbalanced = file.recon_status === 'failed';
                    const isFailed = file.parse_status === 'failed' || file.parse_status === 'needs_password';

                    return (
                      <div
                        key={file.id}
                        style={{
                          display: 'flex',
                          alignItems: 'flex-start',
                          gap: 12,
                          padding: '10px 16px 10px 44px',
                          borderTop: '1px solid var(--line)',
                          opacity: file.selected && !dead ? 1 : 0.5,
                          background: dead ? 'rgba(0,0,0,0.1)' : 'transparent',
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={file.selected && !dead}
                          disabled={busy || dead}
                          onChange={() => send({ files: [{ ids: [file.id], include: !file.selected }] })}
                          style={{
                            marginTop: 3,
                            accentColor: 'var(--accent)',
                            cursor: dead || busy ? 'not-allowed' : 'pointer',
                          }}
                        />

                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div
                            style={{
                              fontSize: 12.5,
                              fontWeight: 600,
                              color: dead ? 'var(--text-3)' : 'var(--text)',
                              textDecoration: dead ? 'line-through' : 'none',
                              wordBreak: 'break-all',
                            }}
                          >
                            {file.filename}
                          </div>

                          <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 2 }}>
                            {file.period_start ? `${file.period_start} → ${file.period_end}` : 'Undated'}
                            {file.row_count ? ` · ${file.row_count} transactions` : ''}
                            {file.origin === 'upload' ? ' · local file upload' : ' · gmail sync'}
                            {file.recon_status && (
                              <span style={{ color: isUnbalanced ? 'var(--warn)' : 'var(--pos)' }}>
                                {` · ${RECON_WORDS[file.recon_status] || file.recon_status}`}
                              </span>
                            )}
                          </div>

                          {note && (
                            <div
                              style={{
                                fontSize: 11.5,
                                marginTop: 3,
                                color: isFailed ? 'var(--neg)' : isUnbalanced ? 'var(--warn)' : 'var(--text-3)',
                              }}
                            >
                              {note}
                            </div>
                          )}

                          {(fileWarnings(file) || []).map((warning) => (
                            <div
                              key={warning}
                              style={{ fontSize: 11, marginTop: 2, color: 'var(--text-3)' }}
                            >
                              ⚠ {warning}
                            </div>
                          ))}
                        </div>

                        <div style={{ textAlign: 'right', fontSize: 12, whiteSpace: 'nowrap' }}>
                          {Number(file.debits) > 0 && <div>−{money(file.debits)}</div>}
                          {Number(file.credits) > 0 && <div style={{ color: 'var(--pos)' }}>+{money(file.credits)}</div>}
                        </div>

                        <ConfirmButton
                          size="sm"
                          disabled={busy}
                          title="Remove this specific document from staging"
                          confirmLabel="Forget"
                          onConfirm={() => forget([file.id])}
                        >
                          ✕
                        </ConfirmButton>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════ Step 6: Build ═══════ */

export function BuildStep({ staged, job, stage, onRun, onFinished }) {
  const running = stage === 'processing';
  const done = stage === 'done' && job?.kind === 'stage_process' && job.status === 'complete';
  const result = done ? (job.result || {}) : null;

  const rows = staged?.rows || 0;
  const selected = staged?.selected || 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div>
        <h3 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 6px', color: 'var(--text)' }}>
          Compile Ledger From Verified Statements
        </h3>
        <p className="lead" style={{ margin: '0 0 12px' }}>
          Your active ledger will be reconstructed from precisely the verified documents selected in Review.
          All categories, inter-account transfers, recurrent cash flows, and balance sheets recompute instantly.
          Every user override and custom classification rule is preserved.
        </p>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          <Badge tone="acc">{selected} document{selected === 1 ? '' : 's'} selected</Badge>
          <Badge>{count(rows)} verified transaction rows</Badge>
          {staged?.superseded > 0 && (
            <Badge tone="warn">{staged.superseded} superseded records bypassed</Badge>
          )}
          {staged?.pending > 0 && (
            <Badge tone="warn">{staged.pending} unread documents</Badge>
          )}
        </div>
      </div>

      {!selected && (
        <Callout tone="warn">
          No documents are currently selected in Review. Return to Step 5 and enable at least one account.
        </Callout>
      )}

      {staged?.processed > 0 && !done && (
        <Callout tone="acc">
          Your system currently displays an active ledger with <strong>{count(staged.processed)}</strong> records
          from the last compilation. Nothing in staging has modified your live data yet.
        </Callout>
      )}

      {running && (
        <div style={{ padding: '16px 20px', borderRadius: 'var(--r)', background: 'var(--surface-2)', border: '1px solid var(--line)' }}>
          <JobProgress job={job} title="Rebuilding active ledger &amp; recomputing invariants" trace />
        </div>
      )}

      {done && result && (
        <Callout tone="pos" icon="check-circle">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <strong style={{ fontSize: 14 }}>
              Ledger successfully compiled! {count(result.transactions || 0)} transactions across {result.accounts || 0} accounts.
            </strong>
            <div style={{ fontSize: 12.5, color: 'var(--text-2)' }}>
              Integrated {result.statements || 0} bank/card statements
              {result.bureau_reports ? `, ${result.bureau_reports} credit bureau reports` : ''}
              {result.portfolios ? `, ${result.portfolios} investment portfolios` : ''}.
              {typeof result.decisions_applied === 'number' && (
                ` ${result.decisions_applied} manual classifications restored.`
              )}
              {result.decisions_orphaned > 0 && (
                ` (${result.decisions_orphaned} orphaned decisions preserved for future matching).`
              )}
            </div>
            {result.uncategorized > 0 && (
              <div style={{ fontSize: 12, color: 'var(--warn)' }}>
                {result.uncategorized} rows await rule assignment in the Review queue.
              </div>
            )}
            {result.unread > 0 && (
              <div style={{ fontSize: 12, color: 'var(--warn)' }}>
                {result.unread} documents were skipped due to password lock.
              </div>
            )}
            {result.failed > 0 && (
              <div style={{ fontSize: 12, color: 'var(--neg)' }}>
                {result.failed} issues encountered: {(result.failures || []).join('; ')}
              </div>
            )}
          </div>
        </Callout>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8 }}>
        <Button
          variant="primary"
          disabled={running || !selected}
          onClick={onRun}
          icon={done ? 'refresh' : 'play'}
        >
          {running ? 'Rebuilding…' : done ? 'Rebuild again' : 'Build Active Ledger'}
        </Button>

        {done && (
          <Button variant="secondary" onClick={onFinished}>
            Close and explore ledger
          </Button>
        )}

        {!running && !done && Boolean(selected) && (
          <span style={{ fontSize: 12, color: 'var(--text-3)' }}>
            Replaces current ledger data with deterministic statement truth.
          </span>
        )}
      </div>
    </div>
  );
}
