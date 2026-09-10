import React, { useMemo, useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { usePeriod } from '../core/period';
import { useDrill } from '../core/drill';
import { compact, count, dateLabel, money, pct, titleCase } from '../core/format';
import {
  Button, Callout, Card, GlassCard, Chip, Empty, Legend, Loading, Section, Stat, Table, Badge,
} from '../ui';
import { Icon } from '../ui/icons';

const KIND_META = {
  debt: {
    label: 'Debt Obligations',
    note: 'Contractual liability paydowns with fixed payoff dates.',
    color: 'var(--c7)',
  },
  spending: {
    label: 'Fixed Operational Spending',
    note: 'Baseline bills, utilities, and subscriptions that recur every month.',
    color: 'var(--c3)',
  },
  saving: {
    label: 'Committed Savings & Investments',
    note: 'Systematic transfers (SIPs/EPF/RDs) that retain wealth rather than spend it.',
    color: 'var(--accent-teal)',
  },
};

export default function Budget() {
  const { params, paramsKey, label, scoped, months: knownMonths, setPeriod } = usePeriod();
  const { drill } = useDrill();
  const { data, loading, error } = useQuery(`budget:${paramsKey}`, () => api.budget(params));

  // Interactive Cut Simulator state: percentage reduction on variable categories
  const [variableCutPct, setVariableCutPct] = useState(0);

  const byKind = useMemo(() => {
    const out = { debt: [], spending: [], saving: [] };
    for (const c of data?.commitments || []) {
      (out[c.kind] || out.spending).push(c);
    }
    return out;
  }, [data]);

  if (error) return <Callout tone="neg">{error.message}</Callout>;
  if (loading) return <Loading label="Synthesizing typical baseline budget from statement history…" />;

  if (data?.status === 'empty') {
    return (
      <Empty title="No budget telemetry available yet" icon="wallet">
        Import statement files or sync your accounts to automatically derive your fixed commitments,
        typical monthly headroom, and recurring obligations.
      </Empty>
    );
  }

  const t = data.totals || {};
  const committed = t.committed_total || 0;
  const everyMonth = (data.variable || []).filter((v) => v.every_month);
  const occasional = (data.variable || []).filter((v) => !v.every_month);

  const income = t.income_typical || 0;
  const debt = t.committed_debt || 0;
  const fixedSpend = t.committed_spending || 0;
  const committedSave = t.committed_saving || 0;
  const varSpend = t.variable_typical || 0;
  const rawHeadroom = t.headroom || 0;

  // Simulator calculation
  const simSavingsMonthly = (varSpend * (variableCutPct / 100));
  const simAnnualSavings = simSavingsMonthly * 12;
  const simAdjustedHeadroom = rawHeadroom + simSavingsMonthly;

  // 50/30/20 Framework Evaluation (Needs: debt + fixed spend + essential var; Wants: discretionary; Savings: committed save + headroom)
  const needsTotal = debt + fixedSpend + (varSpend * 0.6);
  const wantsTotal = varSpend * 0.4;
  const savingsTotal = committedSave + Math.max(0, rawHeadroom);

  const needsPct = income > 0 ? (needsTotal / income) * 100 : 0;
  const wantsPct = income > 0 ? (wantsTotal / income) * 100 : 0;
  const savingsPct = income > 0 ? (savingsTotal / income) * 100 : 0;

  return (
    <div className="budget-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>A Typical Month</h1>
            <Badge tone="brand" size="sm">
              {scoped ? label : 'Full Ledger Aggregate'}
              {data.months ? ` · ${data.months} month${data.months === 1 ? '' : 's'} baseline` : ''}
            </Badge>
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            Empirically derived from your statements: commitments are verified recurring series, and variable
            figures represent monthly medians (resistant to single-month outlier spikes).
          </p>
        </div>
      </div>

      {/* Top Telemetry Stats */}
      <div className="stats-grid">
        <Stat
          label="Median Monthly Inflow"
          value={money(t.income_typical)}
          tone="pos"
          sub="Typical middle month income"
          onDrill={() => drill({
            title: 'Verified Inflow Rows',
            subtitle: 'All transaction records classified as income during this period.',
            params: { flow_role: 'income' },
          })}
        />
        <Stat
          label="Contractually Committed"
          value={money(committed)}
          tone="neg"
          sub={t.income_typical ? `${pct(t.committed_ratio)} of monthly inflow spoken for` : 'Total recurring commitments'}
        />
        <Stat
          label="Typical Monthly Outflow"
          value={money(t.monthly_cost)}
          tone="neg"
          sub="Committed bills + median variable spend"
        />
        <Stat
          label="Monthly Discretionary Headroom"
          value={money(t.headroom)}
          tone={(t.headroom || 0) >= 0 ? 'pos' : 'neg'}
          sub={(t.headroom || 0) >= 0 ? 'Surplus cash left over for wealth creation' : 'Monthly outgo exceeds typical income'}
        />
      </div>

      {/* 50 / 30 / 20 Framework & Cashflow Distribution Bar */}
      {income > 0 && (
        <GlassCard glowing style={{ padding: 'var(--space-6)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 'var(--space-4)', marginBottom: 'var(--space-4)' }}>
            <div>
              <h3 className="h3" style={{ margin: 0 }}>Monthly Cashflow Allocation vs. 50/30/20 Ideal</h3>
              <p className="small muted" style={{ margin: '4px 0 0 0' }}>
                Standard benchmark targets 50% Needs, 30% Wants, 20% Wealth Building.
              </p>
            </div>
            <div style={{ display: 'flex', gap: 'var(--space-3)' }}>
              <Badge tone={needsPct <= 50 ? 'pos' : 'warn'}>Needs: {pct(needsPct, 0)} (Goal ≤50%)</Badge>
              <Badge tone={wantsPct <= 30 ? 'pos' : 'warn'}>Wants: {pct(wantsPct, 0)} (Goal ≤30%)</Badge>
              <Badge tone={savingsPct >= 20 ? 'pos' : 'warn'}>Savings: {pct(savingsPct, 0)} (Goal ≥20%)</Badge>
            </div>
          </div>

          {/* Allocation Stack Bar */}
          <div style={{ height: 24, borderRadius: 'var(--radius-md)', display: 'flex', overflow: 'hidden', background: 'var(--surface-3)', position: 'relative' }}>
            {debt > 0 && (
              <div
                style={{ width: `${(debt / income) * 100}%`, background: 'var(--c7)' }}
                title={`Debt: ${money(debt)} (${pct((debt / income) * 100, 1)})`}
              />
            )}
            {fixedSpend > 0 && (
              <div
                style={{ width: `${(fixedSpend / income) * 100}%`, background: 'var(--c3)' }}
                title={`Fixed Bills: ${money(fixedSpend)} (${pct((fixedSpend / income) * 100, 1)})`}
              />
            )}
            {committedSave > 0 && (
              <div
                style={{ width: `${(committedSave / income) * 100}%`, background: 'var(--accent-teal)' }}
                title={`Committed Savings: ${money(committedSave)} (${pct((committedSave / income) * 100, 1)})`}
              />
            )}
            {varSpend > 0 && (
              <div
                style={{ width: `${(varSpend / income) * 100}%`, background: 'var(--c6)' }}
                title={`Variable Spend: ${money(varSpend)} (${pct((varSpend / income) * 100, 1)})`}
              />
            )}
            {rawHeadroom > 0 && (
              <div
                style={{ width: `${(rawHeadroom / income) * 100}%`, background: 'var(--surface-4)' }}
                title={`Surplus Headroom: ${money(rawHeadroom)} (${pct((rawHeadroom / income) * 100, 1)})`}
              />
            )}
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-4)', marginTop: 'var(--space-3)' }} className="tiny muted">
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--c7)' }} />
              Debt: {compact(debt)} ({pct((debt / income) * 100, 0)})
            </span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--c3)' }} />
              Fixed Bills: {compact(fixedSpend)} ({pct((fixedSpend / income) * 100, 0)})
            </span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--accent-teal)' }} />
              Committed Savings: {compact(committedSave)} ({pct((committedSave / income) * 100, 0)})
            </span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--c6)' }} />
              Variable Spend: {compact(varSpend)} ({pct((varSpend / income) * 100, 0)})
            </span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--surface-4)' }} />
              Uncommitted Headroom: {compact(rawHeadroom)} ({pct((rawHeadroom / income) * 100, 0)})
            </span>
          </div>
        </GlassCard>
      )}

      {/* Interactive Expense Trim & Optimization Simulator */}
      {varSpend > 0 && (
        <Card style={{ padding: 'var(--space-6)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 'var(--space-4)' }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                <span style={{ color: 'var(--accent-emerald)', display: 'inline-flex' }}>
                  <Icon name="sparkles" size={18} />
                </span>
                <h3 className="h3" style={{ margin: 0 }}>Discretionary Spend Trim Simulator</h3>
              </div>
              <p className="small muted" style={{ margin: '4px 0 0 0' }}>
                Simulate modest percentage reductions in discretionary lifestyle spend to observe compound savings.
              </p>
            </div>

            <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
              {[0, 5, 10, 15, 20].map((pctVal) => (
                <button
                  key={pctVal}
                  className={`btn btn-sm ${variableCutPct === pctVal ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() => setVariableCutPct(pctVal)}
                >
                  {pctVal === 0 ? 'Baseline' : `−${pctVal}%`}
                </button>
              ))}
            </div>
          </div>

          <div style={{ marginTop: 'var(--space-4)', display: 'grid', gridTemplateColumns: '1fr 280px', gap: 'var(--space-6)', alignItems: 'center' }}>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                <span className="small font-medium">Variable Discretionary Budget Trim</span>
                <span className="num font-semibold" style={{ color: 'var(--accent-emerald)' }}>
                  −{variableCutPct}%
                </span>
              </div>
              <input
                type="range"
                min="0"
                max="30"
                step="1"
                value={variableCutPct}
                onChange={(e) => setVariableCutPct(Number(e.target.value))}
                style={{ width: '100%', accentColor: 'var(--accent-emerald)', cursor: 'pointer' }}
              />
            </div>

            <div style={{
              padding: 'var(--space-3) var(--space-4)',
              background: 'var(--surface-2)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-subtle)',
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}>
              <div className="tiny muted">Monthly Cash Boost:</div>
              <div className="num font-bold pos" style={{ fontSize: 'var(--text-lg)' }}>
                +{money(simSavingsMonthly)}/mo
              </div>
              <div className="tiny muted">Annual Compound Surplus:</div>
              <div className="num font-semibold brand">
                +{money(simAnnualSavings)}/yr
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* Contractual Commitments Tables by Kind */}
      <Section
        title="Fixed Commitments"
        subtitle={`${data.commitments?.length || 0} active recurring series verified from statement history`}
      />

      {['debt', 'spending', 'saving'].filter((k) => byKind[k]?.length > 0).map((kind) => {
        const meta = KIND_META[kind];
        const rows = byKind[kind];
        const kindTotal = rows.reduce((s, r) => s + (r.monthly || 0), 0);

        return (
          <Card key={kind} title={meta.label} subtitle={meta.note}>
            <div style={{ overflowX: 'auto' }}>
              <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th>Commitment</th>
                    <th>Cadence</th>
                    <th style={{ textAlign: 'right' }}>Per Month</th>
                    <th style={{ textAlign: 'right' }}>Consistency</th>
                    <th>Next Expected</th>
                    <th>Tenure / End</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((c) => (
                    <tr
                      key={c.series_id}
                      className="terminal-row"
                      style={{ cursor: 'pointer' }}
                      onClick={() => drill({
                        title: c.label,
                        subtitle: `${money(c.monthly)}/mo (${c.cadence || 'recurring'}), seen ${c.occurrences} times across your ledger.`,
                        params: { category: c.category },
                      })}
                    >
                      <td>
                        <div className="font-semibold">{c.label}</div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
                          <Chip size="sm">{titleCase(c.category)}</Chip>
                          {c.account && <span className="tiny muted">{c.account}</span>}
                        </div>
                      </td>
                      <td className="nowrap">{c.cadence || `${c.cadence_days}d`}</td>
                      <td className="num font-semibold nowrap" style={{ textAlign: 'right' }}>
                        {money(c.monthly)}
                      </td>
                      <td className="num nowrap" style={{ textAlign: 'right' }}>
                        {c.months_seen}/{data.months} mo
                        {c.months_seen < 2 && <span className="tiny warn" style={{ marginLeft: 4 }}>New</span>}
                      </td>
                      <td className="nowrap muted">
                        {c.next_expected ? dateLabel(c.next_expected) : '—'}
                      </td>
                      <td className="nowrap">
                        {c.ends_on ? (
                          <div>
                            <div>{dateLabel(c.ends_on)}</div>
                            <div className="tiny muted">{c.months_left} payments remaining</div>
                          </div>
                        ) : (
                          <span className="muted tiny">{kind === 'debt' ? 'No schedule linked' : 'Indefinite / Ongoing'}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr style={{ borderTop: '2px solid var(--border-subtle)', fontWeight: 600 }}>
                    <td colSpan={2}>Subtotal {meta.label}</td>
                    <td className="num" style={{ textAlign: 'right' }}>{money(kindTotal)}</td>
                    <td colSpan={3} />
                  </tr>
                </tfoot>
              </table>
            </div>
          </Card>
        );
      })}

      {/* Variable Categories Table */}
      <Section
        title="Variable Spend Distribution"
        subtitle="Calculated as median monthly spend per category to prevent outlier distortions."
      />

      {everyMonth.length > 0 && (
        <VariableSpendCard
          title="Consistently Present Every Month"
          subtitle="Categories that incur outflow every single cycle, but with variable rupee totals."
          rows={everyMonth}
          months={data.months}
          drill={drill}
        />
      )}

      {occasional.length > 0 && (
        <VariableSpendCard
          title="Intermittent & Occasional Categories"
          subtitle="Categories that appear in select months (travel, gadgets, festive shopping)."
          rows={occasional}
          months={data.months}
          drill={drill}
        />
      )}

      {knownMonths?.length > 1 && scoped && (
        <Callout tone="info">
          Baseline calculations become increasingly accurate with more history. Currently calibrated from {data.months} months.
          {' '}<Button variant="link" size="sm" onClick={() => setPeriod({ preset: 'all' })}>
            View lifetime full-ledger aggregate
          </Button>
        </Callout>
      )}
    </div>
  );
}

function VariableSpendCard({ title, subtitle, rows, months, drill }) {
  const sumOfMedians = rows.reduce((s, r) => s + (r.typical_monthly || 0), 0);

  return (
    <Card title={title} subtitle={subtitle}>
      <div style={{ overflowX: 'auto' }}>
        <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>Category</th>
              <th style={{ textAlign: 'right' }}>Typical (Median)</th>
              <th style={{ textAlign: 'right' }}>Quietest Month</th>
              <th style={{ textAlign: 'right' }}>Peak Month</th>
              <th style={{ textAlign: 'right' }}>Months Active</th>
              <th style={{ textAlign: 'right' }}>Total Transactions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((v) => (
              <tr
                key={v.category}
                className="terminal-row"
                style={{ cursor: 'pointer' }}
                onClick={() => drill({
                  title: titleCase(v.category),
                  subtitle: `Typically ${money(v.typical_monthly)}/mo (Range: ${money(v.low_monthly)} – ${money(v.high_monthly)}). ${money(v.total)} total across ${v.months_seen} months.`,
                  params: { category: v.category },
                })}
              >
                <td>
                  <div className="font-semibold">{titleCase(v.category)}</div>
                  <div className="tiny muted">{v.group}</div>
                </td>
                <td className="num font-semibold nowrap" style={{ textAlign: 'right' }}>
                  {money(v.typical_monthly)}
                  {v.typical_monthly < 0 && <Chip tone="pos" size="sm" style={{ marginLeft: 6 }}>Refunds</Chip>}
                </td>
                <td className="num muted nowrap" style={{ textAlign: 'right' }}>{money(v.low_monthly)}</td>
                <td className="num muted nowrap" style={{ textAlign: 'right' }}>{money(v.high_monthly)}</td>
                <td className="num nowrap" style={{ textAlign: 'right' }}>{v.months_seen}/{months}</td>
                <td className="num nowrap" style={{ textAlign: 'right' }}>{count(v.count)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr style={{ borderTop: '2px solid var(--border-subtle)', fontWeight: 600 }}>
              <td>Sum of Individual Medians</td>
              <td className="num" style={{ textAlign: 'right' }}>{money(sumOfMedians)}</td>
              <td colSpan={4} />
            </tr>
          </tfoot>
        </table>
      </div>
    </Card>
  );
}
