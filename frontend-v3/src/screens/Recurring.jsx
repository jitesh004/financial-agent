import React, { useMemo, useState } from 'react';
import { api } from '../core/api';
import { invalidate, useQuery } from '../core/store';
import { usePeriod } from '../core/period';
import { useToast } from '../core/toast';
import { dateLabel, money, titleCase } from '../core/format';
import {
  Button, Callout, Card, GlassCard, Chip, ConfirmButton, Empty, Loading, Stat, Table, Badge,
} from '../ui';
import { Icon } from '../ui/icons';

const CADENCE = {
  7: 'weekly',
  14: 'fortnightly',
  28: 'four-weekly',
  30: 'monthly',
  61: 'bi-monthly',
  91: 'quarterly',
  182: 'half-yearly',
  365: 'yearly',
};

const TREND_MAP = {
  rose: ['warn', 'Price increased'],
  fell: ['pos', 'Price decreased'],
  drifting: ['brand', 'Fluctuating'],
};

const cadenceLabel = (s) => s.cadence_name || CADENCE[s.cadence_days] || `every ${s.cadence_days} days`;

export default function Recurring() {
  const toast = useToast();
  const { label: periodLabel, scoped, window: resolved } = usePeriod();
  const { data: series = [], loading, error, refetch } = useQuery('recurring', () => api.recurring());
  const [open, setOpen] = useState(null);
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState('');

  const { data: memberRows, loading: loadingMembers } = useQuery(
    open ? `recurring-members:${open}` : null,
    () => api.transactions({ recurring_series_id: open, limit: 500, sort_by: 'date', sort_dir: 'desc' }),
    { enabled: Boolean(open) }
  );

  const visible = useMemo(() => {
    if (!scoped || !resolved) return series;
    const from = resolved.start;
    const until = resolved.end;
    return series.filter((s) => {
      const first = s.first_seen || s.last_seen;
      const last = s.last_seen || s.first_seen;
      if (!first || !last) return true;
      if (until && first > until) return false;
      return !(from && last < from);
    });
  }, [series, scoped, resolved]);

  async function patch(id, fields) {
    try {
      await api.updateSeries(id, fields);
      setEditing(null);
      invalidate('recurring');
      refetch();
      toast.ok('Updated', 'Series parameters saved.');
    } catch (e) {
      toast.fail('Could not update series', e.message);
    }
  }

  async function remove(id) {
    try {
      await api.deleteSeries(id);
      invalidate('recurring');
      refetch();
      toast.ok('Tracking stopped', 'The underlying transactions remain untouched.');
    } catch (e) {
      toast.fail('Could not remove series', e.message);
    }
  }

  if (loading) return <Loading message="Discovering recurring series and cadences…" />;
  if (error) return <Callout tone="neg">{error.message}</Callout>;

  const active = visible.filter((s) => s.is_active);
  const amountOf = (s) => Number(s.amount ?? s.median_amount) || 0;
  const monthlyTotal = active.reduce(
    (sum, s) => (s.direction === 'debit' ? sum + (Number(s.monthly_equivalent) || 0) : sum),
    0
  );

  return (
    <div className="recurring-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>Recurring & Subscriptions</h1>
            {scoped && <Badge tone="brand" size="sm">{periodLabel}</Badge>}
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            Algorithmic cadence detection: verified recurring debits, subscriptions, and standing instructions.
          </p>
        </div>
      </div>

      {/* Top Headline Stats */}
      <div className="stats-grid">
        <Stat label="Active Tracked Series" value={String(active.length)} sub="Systematic repeating commitments" />
        <Stat
          label="Total Monthly Commitment"
          value={money(monthlyTotal)}
          tone="neg"
          sub="Normalized 30-day monthly outflow"
        />
        <Stat
          label="Muted / Ignored Series"
          value={String(visible.length - active.length)}
          sub="Excluded from budget totals"
        />
      </div>

      {!visible.length && (
        <Empty
          icon="repeat"
          title={series.length ? `No recurring obligations found in ${periodLabel}` : 'No recurring patterns detected'}
        >
          {series.length
            ? 'Existing series lie outside the selected window. Widen the period to see all commitments.'
            : 'A recurring commitment requires at least 3 occurrences at a consistent cadence and amount.'}
        </Empty>
      )}

      {/* Series Cards */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
        {visible.map((s) => {
          const isOpen = open === s.id;
          const rows = isOpen && !loadingMembers ? (memberRows?.transactions || []) : null;

          return (
            <GlassCard
              key={s.id}
              style={{
                padding: 'var(--space-5)',
                opacity: s.is_active ? 1 : 0.6,
                borderColor: s.status === 'overdue' ? 'var(--state-warn)' : undefined,
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 'var(--space-4)' }}>
                {/* Main Identity Info */}
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-3)', flex: 1, minWidth: 280 }}>
                  <button
                    className="btn btn-ghost btn-icon"
                    onClick={() => setOpen(isOpen ? null : s.id)}
                    title={isOpen ? 'Collapse history' : 'Expand transaction audit'}
                    style={{ marginTop: 2 }}
                  >
                    <Icon name={isOpen ? 'chevron-down' : 'chevron'} size={18} />
                  </button>

                  <div style={{ flex: 1 }}>
                    {editing === s.id ? (
                      <div style={{ display: 'flex', gap: 'var(--space-2)', maxWidth: 420 }}>
                        <input
                          autoFocus
                          className="input"
                          value={draft}
                          onChange={(e) => setDraft(e.target.value)}
                          style={{ flex: 1 }}
                        />
                        <Button variant="primary" size="sm" onClick={() => patch(s.id, { label: draft })}>
                          Save
                        </Button>
                        <Button size="sm" onClick={() => setEditing(null)}>
                          Cancel
                        </Button>
                      </div>
                    ) : (
                      <div
                        style={{
                          fontSize: 'var(--text-lg)', fontWeight: 600, cursor: 'pointer',
                          overflowWrap: 'anywhere',
                        }}
                        onClick={() => setOpen(isOpen ? null : s.id)}
                      >
                        {s.label}
                      </div>
                    )}

                    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 'var(--space-2)', marginTop: 'var(--space-2)' }}>
                      <Chip size="sm">{titleCase(s.category)}</Chip>
                      <Chip size="sm">{cadenceLabel(s)}</Chip>
                      <span className="tiny muted">{s.occurrences} occurrences</span>

                      {s.missed > 0 && (
                        <span className="tiny warn">· {s.missed} skipped</span>
                      )}

                      {s.next_expected && (
                        <span className="tiny muted">· Next: {dateLabel(s.next_expected)}</span>
                      )}

                      {TREND_MAP[s.amount_trend] && (
                        <Badge tone={TREND_MAP[s.amount_trend][0]} size="sm">
                          {TREND_MAP[s.amount_trend][1]}
                        </Badge>
                      )}

                      {s.status === 'overdue' && <Badge tone="warn" size="sm">Overdue</Badge>}
                      {s.status === 'ended' && <Badge size="sm">Concluded</Badge>}
                      {!s.is_active && <Badge tone="warn" size="sm">Muted</Badge>}
                    </div>
                  </div>
                </div>

                {/* Amount & Actions */}
                <div style={{ textAlign: 'right', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 'var(--space-2)' }}>
                  <div className="num font-bold" style={{ fontSize: 'var(--text-xl)', color: s.direction === 'credit' ? 'var(--state-pos)' : 'var(--text-1)' }}>
                    {money(amountOf(s))}
                  </div>

                  {s.lifetime_median != null && Math.abs(s.lifetime_median - amountOf(s)) > 1 && (
                    <div className="tiny muted">
                      was {money(s.lifetime_median)} {s.changed_on ? `until ${dateLabel(s.changed_on)}` : ''}
                    </div>
                  )}

                  <div style={{ display: 'flex', gap: 'var(--space-2)', marginTop: 'var(--space-2)' }}>
                    <Button size="sm" onClick={() => { setEditing(s.id); setDraft(s.label); }}>
                      Rename
                    </Button>
                    <Button size="sm" onClick={() => patch(s.id, { is_active: !s.is_active })}>
                      {s.is_active ? 'Mute' : 'Track'}
                    </Button>
                    <ConfirmButton
                      size="sm"
                      variant="danger"
                      question="Stop tracking this recurring pattern? Raw transactions will not be deleted."
                      confirmLabel="Confirm"
                      onConfirm={() => remove(s.id)}
                    >
                      Delete
                    </ConfirmButton>
                  </div>
                </div>
              </div>

              {/* Collapsible Inspection Panel */}
              {isOpen && (
                <div style={{ marginTop: 'var(--space-4)', paddingTop: 'var(--space-4)', borderTop: '1px solid var(--border-subtle)' }}>
                  {s.evidence?.length > 0 && (
                    <div style={{ marginBottom: 'var(--space-3)' }}>
                      <div className="tiny font-semibold muted" style={{ marginBottom: 4 }}>Detection Heuristics:</div>
                      <ul style={{ margin: 0, paddingLeft: 18 }} className="tiny muted">
                        {s.evidence.map((line, i) => (
                          <li key={i}>{line}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {!rows && <Loading message="Loading constituent transaction evidence…" />}

                  {rows && !rows.length && (
                    <div className="tiny muted" style={{ padding: 'var(--space-3)' }}>
                      No transactions currently stamped with this series ID. (Series relationships re-link on ledger rebuild).
                    </div>
                  )}

                  {rows && rows.length > 0 && (
                    <div style={{ maxHeight: 280, overflowY: 'auto' }}>
                      <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead>
                          <tr>
                            <th>Date</th>
                            <th>Description</th>
                            <th>Category</th>
                            <th style={{ textAlign: 'right' }}>Amount</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((t) => (
                            <tr key={t.id} className="terminal-row">
                              <td className="nowrap muted">{dateLabel(t.date)}</td>
                              <td className="font-medium">{t.description}</td>
                              <td className="nowrap"><Chip size="sm">{titleCase(t.category)}</Chip></td>
                              <td className="num font-semibold nowrap" style={{ textAlign: 'right' }}>
                                {money(Math.abs(t.amount))}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </GlassCard>
          );
        })}
      </div>
    </div>
  );
}
