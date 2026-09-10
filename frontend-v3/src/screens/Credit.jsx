import React, { useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { useToast } from '../core/toast';
import { useAccounts } from '../core/ledger';
import { compact, dateLabel, money, titleCase } from '../core/format';
import {
  Button, Callout, Card, GlassCard, Chip, Empty, Loading, Section, Stat, Table, Badge,
} from '../ui';
import { Icon } from '../ui/icons';

const BAND_MAP = {
  excellent: 'pos',
  'very good': 'pos',
  good: 'brand',
  fair: 'warn',
  poor: 'neg',
  'very poor': 'neg',
};

export default function Credit({ onImport }) {
  const toast = useToast();
  const [busy, setBusy] = useState('');
  const overview = useQuery('bureau', () => api.bureau());
  const recon = useQuery('bureau-recon', () => api.bureauReconciliation());
  const { data: accounts = [] } = useAccounts();

  const reload = () => Promise.all([overview.refetch(), recon.refetch()]);

  const decide = async (bureauAccountId, accountId, confirmed) => {
    setBusy(bureauAccountId);
    try {
      await api.matchBureauAccount(bureauAccountId, accountId, confirmed);
      await reload();
      toast.ok('Reconciliation updated');
    } catch (e) {
      toast.fail('Match update failed', e.message);
    } finally {
      setBusy('');
    }
  };

  if (overview.error) return <Callout tone="warn">{overview.error.message}</Callout>;
  if (overview.loading) return <Loading message="Retrieving credit bureau files and cross-referencing statements…" />;

  const data = overview.data;
  if (!data?.reports?.length) {
    return (
      <Empty
        title="No credit bureau reports imported yet"
        icon="shield"
        action={onImport && (
          <Button variant="primary" icon="upload" onClick={onImport}>
            Import Bureau Report
          </Button>
        )}
      >
        Import a CIBIL, CRIF High Mark, Experian, or Equifax credit report PDF.
        Bureau records audit every credit line reported by lenders, exposing accounts lacking local statements.
      </Empty>
    );
  }

  const totals = data.totals || {};
  const counts = recon.data?.counts || {};
  const bureauOnly = recon.data?.bureau_only || [];
  const blindSpots = bureauOnly.filter((a) => a.is_blind_spot);
  const suggestions = bureauOnly.filter((a) => a.suggestion);

  return (
    <div className="credit-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>Credit Bureau Intelligence & Reconciliation</h1>
            <Badge tone="brand" size="sm">Multi-Bureau Cross-Check</Badge>
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            Audited reconciliation between official credit bureau reporting (CIBIL/CRIF/Experian) and your imported statement accounts.
          </p>
        </div>
      </div>

      {/* Top Bureau Score Cards */}
      <div className="stats-grid">
        {(data.latest_by_bureau || []).map((report) => (
          <GlassCard key={report.id} glowing style={{ padding: 'var(--space-5)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <span className="font-semibold">{titleCase(report.bureau)} Score</span>
              {report.score_band && (
                <Badge tone={BAND_MAP[report.score_band] || 'brand'} size="sm">
                  {report.score_band}
                </Badge>
              )}
            </div>
            <div className="num font-bold" style={{ fontSize: 'var(--text-3xl)', color: 'var(--accent-text)' }}>
              {report.score ?? '—'}
            </div>
            <div className="tiny muted" style={{ marginTop: 4 }}>
              {report.pulled_on ? `Pulled on ${dateLabel(report.pulled_on)}` : 'Audited bureau record'}
            </div>
          </GlassCard>
        ))}

        <Stat
          label="Total Reported Outstanding"
          value={money(Number(totals.outstanding) || 0)}
          tone="neg"
          sub={`${totals.open_accounts || 0} open credit accounts`}
        />

        {Number(totals.overdue) > 0 ? (
          <Stat
            label="Delinquent / Overdue"
            value={money(Number(totals.overdue))}
            tone="neg"
            sub={`Worst DPD: ${totals.worst_dpd} days past due`}
          />
        ) : (
          <Stat
            label="Delinquency Status"
            value="Clean"
            tone="pos"
            sub="0 days past due across all reported trades"
          />
        )}
      </div>

      {/* Blind Spots Section */}
      {blindSpots.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <Section
            title="Statement Coverage Blind Spots"
            subtitle="Live credit accounts reported by lenders that have zero corresponding statement uploads."
          />

          <Callout tone="warn">
            <strong>{blindSpots.length} unreported credit account{blindSpots.length === 1 ? '' : 's'} identified.</strong> Lenders
            report active balances on these accounts, but no statement PDF covers them.
            Balances owed on them are missing from your ledger totals until imported or mapped.
          </Callout>

          <Card pad={false}>
            <div style={{ overflowX: 'auto' }}>
              <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th>Lender</th>
                    <th>Account Type</th>
                    <th>Masked Number</th>
                    <th style={{ textAlign: 'right' }}>Reported Balance</th>
                    <th style={{ textAlign: 'right' }}>Overdue</th>
                    <th>Opened On</th>
                  </tr>
                </thead>
                <tbody>
                  {blindSpots.map((row) => (
                    <tr key={row.bureau_account_id} className="terminal-row">
                      <td className="font-semibold">{row.lender}</td>
                      <td><Chip size="sm">{titleCase(row.account_type)}</Chip></td>
                      <td className="num">{row.masked || '—'}</td>
                      <td className="num neg nowrap font-semibold" style={{ textAlign: 'right' }}>
                        {row.balance ? money(Number(row.balance)) : '—'}
                      </td>
                      <td className="num nowrap" style={{ textAlign: 'right' }}>
                        {Number(row.overdue) > 0 ? (
                          <span className="neg font-semibold">{money(Number(row.overdue))}</span>
                        ) : '—'}
                      </td>
                      <td className="nowrap muted tiny">{dateLabel(row.opened_on)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}

      {/* Suggestions Verification Queue */}
      {suggestions.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <Section
            title="Probabilistic Account Matches"
            subtitle="High-confidence matching candidates requiring human confirmation."
          />

          <Card style={{ padding: 'var(--space-4)' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
              {suggestions.map((row) => {
                const account = accounts.find((a) => a.id === row.suggestion);
                return (
                  <div
                    key={row.bureau_account_id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      flexWrap: 'wrap',
                      gap: 'var(--space-3)',
                      padding: 'var(--space-3) var(--space-4)',
                      background: 'var(--surface-2)',
                      borderRadius: 'var(--radius-md)',
                      border: '1px solid var(--border-subtle)',
                    }}
                  >
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <strong className="font-medium">{row.lender}</strong>
                        <span className="tiny num muted">{row.masked}</span>
                      </div>
                      <div className="tiny muted" style={{ marginTop: 2 }}>
                        {row.reason} · {Math.round((row.confidence || 0) * 100)}% match confidence
                      </div>
                    </div>

                    <div style={{
                      display: 'flex', alignItems: 'center', gap: 'var(--space-4)',
                      flexWrap: 'wrap', minWidth: 0,
                    }}>
                      <span className="small font-medium brand" style={{ overflowWrap: 'anywhere' }}>
                        → {account?.display_name || account?.institution || row.suggestion}
                      </span>
                      <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
                        <Button
                          variant="primary"
                          size="sm"
                          disabled={busy === row.bureau_account_id}
                          onClick={() => decide(row.bureau_account_id, row.suggestion, true)}
                        >
                          Confirm Link
                        </Button>
                        <Button
                          size="sm"
                          disabled={busy === row.bureau_account_id}
                          onClick={() => decide(row.bureau_account_id, null, false)}
                        >
                          Not a Match
                        </Button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        </div>
      )}

      {/* Balance Deltas / Statement Drift */}
      {recon.data?.balance_deltas?.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <Section
            title="Statement vs. Bureau Discrepancies"
            subtitle="Bureau balances update on 30-day reporting cycles and may reflect trailing timing differences."
          />

          <Card pad={false}>
            <div style={{ overflowX: 'auto' }}>
              <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th>Lender</th>
                    <th style={{ textAlign: 'right' }}>Credit Report Balance</th>
                    <th style={{ textAlign: 'right' }}>Statement Ledger Balance</th>
                    <th style={{ textAlign: 'right' }}>Discrepancy Delta</th>
                  </tr>
                </thead>
                <tbody>
                  {recon.data.balance_deltas.map((row) => (
                    <tr key={row.bureau_account_id} className="terminal-row">
                      <td>
                        <div className="font-semibold">{row.lender}</div>
                        <span className="tiny num muted">{row.masked}</span>
                      </td>
                      <td className="num nowrap" style={{ textAlign: 'right' }}>{money(Number(row.bureau_balance))}</td>
                      <td className="num nowrap" style={{ textAlign: 'right' }}>{money(Number(row.ledger_balance))}</td>
                      <td className="num nowrap font-semibold" style={{ textAlign: 'right' }}>
                        <span className={Number(row.difference) >= 0 ? 'neg' : 'pos'}>
                          {compact(Number(row.difference))}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}

      {/* Comprehensive Bureau Accounts Registry */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 'var(--space-3)' }}>
          <div>
            <h3 className="h3" style={{ margin: 0 }}>Full Bureau Reported Trades</h3>
            <p className="tiny muted" style={{ margin: '2px 0 0 0' }}>
              {counts.linked || 0} mapped to statements · {counts.blind_spots || 0} unmapped blind spots · {counts.unreported_here || 0} dormant
            </p>
          </div>

          <Button
            size="sm"
            icon="refresh"
            disabled={Boolean(busy)}
            onClick={async () => {
              setBusy('rematch');
              await api.rematchBureau().catch(() => {});
              await reload();
              setBusy('');
              toast.ok('Bureau heuristic matching re-executed');
            }}
          >
            Re-Run Heuristic Matching
          </Button>
        </div>

        <BureauTable
          rows={[...(recon.data?.linked || []), ...bureauOnly]}
          ledger={recon.data?.ledger_only || []}
          busy={busy}
          onDecide={decide}
        />
      </div>
    </div>
  );
}

function BureauTable({ rows, ledger = [], busy, onDecide }) {
  if (!rows.length) return <Empty title="No accounts discovered in this bureau report" icon="shield" />;
  const nameOf = (id) => (ledger.find((a) => a.account_id === id) || {}).label || 'Linked account';

  return (
    <Card pad={false}>
      <div style={{ maxHeight: 520, overflowY: 'auto' }}>
        <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>Lender</th>
              <th>Type</th>
              <th>Number</th>
              <th>Status</th>
              <th style={{ textAlign: 'right' }}>Outstanding</th>
              <th style={{ textAlign: 'right' }}>Worst DPD</th>
              <th>Reconciliation Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.bureau_account_id} className="terminal-row">
                <td className="font-semibold">{row.lender}</td>
                <td><Chip size="sm">{titleCase(row.account_type)}</Chip></td>
                <td className="num">{row.masked || '—'}</td>
                <td>
                  <Badge tone={row.status === 'open' ? 'brand' : row.status === 'delinquent' ? 'neg' : undefined} size="sm">
                    {row.status}
                  </Badge>
                </td>
                <td className="num font-semibold nowrap" style={{ textAlign: 'right' }}>
                  {row.balance ? money(Number(row.balance)) : '—'}
                </td>
                <td className="num nowrap" style={{ textAlign: 'right' }}>
                  {row.worst_dpd > 0 ? <span className="neg font-bold">{row.worst_dpd}</span> : '0'}
                </td>
                <td>
                  {row.account_id ? (
                    <Badge tone="pos" size="sm">Matched to Statement</Badge>
                  ) : row.suggestion ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span className="tiny brand">{nameOf(row.suggestion)}</span>
                      <Button
                        size="xs"
                        variant="primary"
                        disabled={busy === row.bureau_account_id}
                        onClick={() => onDecide(row.bureau_account_id, row.suggestion, true)}
                      >
                        Link
                      </Button>
                      <Button
                        size="xs"
                        disabled={busy === row.bureau_account_id}
                        onClick={() => onDecide(row.bureau_account_id, null, false)}
                      >
                        ✕
                      </Button>
                    </div>
                  ) : row.is_blind_spot ? (
                    <Badge tone="warn" size="sm">Uncovered Blind Spot</Badge>
                  ) : (
                    <span className="tiny muted">Closed trade</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
