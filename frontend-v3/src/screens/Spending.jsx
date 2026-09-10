import React, { useMemo, useState } from 'react';
import { usePeriod } from '../core/period';
import { useDrill } from '../core/drill';
import { useViewData } from '../core/ledger';
import { usePrefs } from '../core/prefs';
import { colorFor, compact, count, dateLabel, money, monthLabel, pct, SPEND_ROLES, titleCase } from '../core/format';
import {
  BarList, Callout, Card, GlassCard, Chip, Empty, Legend, Section, Segmented, Skeleton,
  SkeletonStats, Stat, Badge,
} from '../ui';
import { DonutChart } from '../ui/gauges';
import { StackedBarChart } from '../ui/charts';
import { PeriodEmpty } from '../app/PeriodBar';

export default function Spending() {
  const { data, loading, windowEmpty } = useViewData();
  const { label: periodLabel, scoped } = usePeriod();
  const { drill } = useDrill();
  const [prefs] = usePrefs();
  const [groupBy, setGroupBy] = useState('category'); // 'category' | 'group'
  const [shape, setShape] = useState('bars'); // 'bars' | 'donut'

  const analysis = data?.analysis;

  const categories = useMemo(() => (analysis?.by_category || []).map((c, i) => ({
    ...c,
    label: c.category,
    value: c.total,
    color: colorFor(i),
  })), [analysis]);

  const groups = useMemo(() => Object.entries(analysis?.by_group || {})
    .map(([label, value], i) => ({
      label,
      value,
      color: colorFor(i),
      categories: (analysis?.by_category || [])
        .filter((c) => c.group === label).map((c) => c.category),
    })), [analysis]);

  const stack = useMemo(() => buildStack(analysis), [analysis]);

  if (loading) {
    return (
      <div className="spending-screen page-enter">
        <SkeletonStats count={3} />
        <div style={{ marginTop: 24 }}>
          <Skeleton lines={10} height={300} />
        </div>
      </div>
    );
  }

  if (!data) return <Empty title="No spending analytics synthesized yet" icon="trending" />;
  if (windowEmpty) return <PeriodEmpty available={data.available} />;

  const merchants = analysis?.top_merchants || [];
  const salaryFlows = analysis?.salary_flows || [];
  const latestSalary = salaryFlows[salaryFlows.length - 1];
  const items = groupBy === 'category' ? categories : groups;

  return (
    <div className="spending-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>Spending Breakdown</h1>
            {scoped && <Badge tone="brand" size="sm">{periodLabel}</Badge>}
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            Strict multi-dimensional accounting across verified categories, groups, counterparties, and post-payday velocity.
          </p>
        </div>
      </div>

      {/* Top Headline Stats */}
      <div className="stats-grid">
        <Stat
          label="Total Net Outflow"
          value={money(analysis.totals?.spend)}
          tone="neg"
          sub="All spending rows net of chargebacks & returns"
          onDrill={() => drill({
            title: 'Verified Outflow Rows',
            subtitle: 'Every transaction classified as expenditure in this period.',
            params: { flow_role: SPEND_ROLES },
          })}
        />
        <Stat
          label="Largest Spend Concentration"
          value={categories[0] ? titleCase(categories[0].category) : '—'}
          sub={categories[0] ? `${money(categories[0].total)} (${pct(categories[0].share_pct)} of total spend)` : ''}
        />
        <Stat
          label="Uncategorized Pending"
          value={count(analysis.uncategorized?.count ?? 0)}
          tone={analysis.uncategorized?.count ? 'warn' : 'pos'}
          sub={analysis.uncategorized?.total
            ? `${money(analysis.uncategorized.total)} pending rule resolution`
            : '100% categorised tie-out'}
          onDrill={analysis.uncategorized?.count ? () => drill({
            title: 'Uncategorized Rows',
            subtitle: 'Transactions without deterministic categorization heuristics. Classifying them auto-teaches future imports.',
            params: { category: 'uncategorized' },
          }) : undefined}
        />
      </div>

      {/* Category / Group Breakdown & Top Merchants */}
      <div className="grid-2">
        {/* Category breakdown */}
        <Card
          title={groupBy === 'category' ? 'Outflow by Category' : 'Outflow by Group'}
          tools={(
            <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
              <Segmented
                value={groupBy}
                onChange={setGroupBy}
                ariaLabel="Grouping dimension"
                options={[['category', 'Category'], ['group', 'Group']]}
              />
              <Segmented
                value={shape}
                onChange={setShape}
                ariaLabel="Display format"
                options={[['bars', 'Bars'], ['donut', 'Donut']]}
              />
            </div>
          )}
        >
          {shape === 'bars' ? (
            <BarList
              items={items}
              total={analysis.totals?.spend}
              max={groupBy === 'category' ? 14 : 8}
              onPick={(item) => drill({
                title: titleCase(item.label),
                subtitle: item.categories
                  ? `${money(item.value)} across ${item.categories.length} categories: ${item.categories.map(titleCase).join(', ')}`
                  : `${money(item.value)} across ${item.count} transactions`,
                params: groupBy === 'category'
                  ? { category: item.category }
                  : { category: (item.categories || []).join(',') },
              })}
            />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 'var(--space-4)', padding: 'var(--space-2) 0' }}>
              <DonutChart
                data={items}
                size={220}
                thickness={30}
                centerLabel="Total Outflow"
                centerValue={compact(analysis.totals?.spend)}
              />
              <Legend items={items} />
            </div>
          )}
        </Card>

        {/* Top Merchants Table */}
        <Card
          title="Top Merchants & Counterparties"
          subtitle={`${merchants.length} tracked entities — click to inspect ledger history`}
        >
          <div style={{ maxHeight: 420, overflow: 'auto' }}>
            <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th>Merchant</th>
                  <th style={{ textAlign: 'right' }}>Total</th>
                  <th style={{ textAlign: 'right' }}>Hits</th>
                  <th style={{ textAlign: 'right' }}>Ticket Avg</th>
                </tr>
              </thead>
              <tbody>
                {merchants.map((m) => (
                  <tr
                    key={m.merchant}
                    className="terminal-row"
                    style={{ cursor: 'pointer' }}
                    onClick={() => drill({
                      title: m.merchant,
                      subtitle: `${money(m.total)} across ${m.count} transactions (Average: ${money(m.average)}). First seen ${dateLabel(m.first_seen)}, last seen ${dateLabel(m.last_seen)}.`,
                      params: { merchant: m.merchant },
                    })}
                  >
                    <td>
                      <div className="font-semibold truncate" style={{ maxWidth: 180 }}>{m.merchant}</div>
                      <Chip size="sm">{titleCase(m.category)}</Chip>
                    </td>
                    <td className="num font-semibold nowrap" style={{ textAlign: 'right' }}>
                      {money(m.total)}
                    </td>
                    <td className="num nowrap" style={{ textAlign: 'right' }}>{m.count}</td>
                    <td className="num muted nowrap" style={{ textAlign: 'right' }}>{money(m.average)}</td>
                  </tr>
                ))}
                {!merchants.length && (
                  <tr>
                    <td colSpan={4} className="muted tiny" style={{ padding: 'var(--space-4)', textAlign: 'center' }}>
                      No merchant entities tracked in this timeframe.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {/* Monthly Category Composition */}
      {stack.data.length > 1 && (
        <Card
          title="Monthly Category Composition"
          subtitle="How the top categories stack up month over month"
          tools={<Legend items={stack.keys.map((k, i) => ({ label: titleCase(k), color: colorFor(i) }))} />}
        >
          <StackedBarChart
            data={stack.data}
            height={280}
            series={stack.keys.map((k, i) => ({ key: k, label: titleCase(k), color: colorFor(i) }))}
          />
        </Card>
      )}

      {/* Salary Flow Velocity Tracker */}
      {latestSalary && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <Section
            title="Post-Payday Cashflow Velocity"
            subtitle="Tracks burn rate and capital drainage in the days immediately following salary credit."
          />

          <div className="grid-2">
            <Card
              title={`${monthLabel(latestSalary.month)} Cycle — Salary ${money(latestSalary.salary_amount)}`}
              subtitle={latestSalary.days_to_half_spent != null
                ? `50% of credit depleted in ${latestSalary.days_to_half_spent} days`
                : 'Steady spend pace through cycle'}
            >
              <BarList
                items={latestSalary.allocations.map((a, i) => ({
                  label: a.category,
                  value: a.amount,
                  color: colorFor(i),
                  category: a.category,
                }))}
                total={latestSalary.salary_amount}
                max={8}
                onPick={(item) => drill({
                  title: titleCase(item.label),
                  subtitle: `${money(item.value)} spent between payday and next cycle.`,
                  ignorePeriod: true,
                  periodLabel: `Window following ${dateLabel(latestSalary.salary_date)} salary`,
                  sortBy: 'date',
                  params: {
                    category: item.category,
                    start: latestSalary.salary_date,
                    end: nextSalaryDate(salaryFlows, latestSalary),
                  },
                })}
              />

              <div style={{ marginTop: 'var(--space-4)', paddingTop: 'var(--space-3)', borderTop: '1px solid var(--border-subtle)' }}>
                {latestSalary.left_over >= 0 ? (
                  <Badge tone="pos" size="md">
                    +{money(latestSalary.left_over)} remained unspent prior to subsequent salary credit
                  </Badge>
                ) : (
                  <Badge tone="warn" size="md">
                    Outflow exceeded salary by {money(Math.abs(latestSalary.left_over))} (absorbed by balances or secondary inflow)
                  </Badge>
                )}
              </div>
            </Card>

            <Card title="Historical Cycle Burn Velocity" subtitle="Days required to exhaust half of each incoming paycheck">
              <div style={{ maxHeight: 320, overflow: 'auto' }}>
                <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr>
                      <th>Month</th>
                      <th style={{ textAlign: 'right' }}>Credited</th>
                      <th style={{ textAlign: 'right' }}>Half Gone</th>
                      <th style={{ textAlign: 'right' }}>Net Residual</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...salaryFlows].reverse().map((f) => (
                      <tr key={`${f.month}-${f.salary_date}`} className="terminal-row">
                        <td className="nowrap font-medium">{monthLabel(f.month)}</td>
                        <td className="num nowrap" style={{ textAlign: 'right' }}>{money(f.salary_amount)}</td>
                        <td className="num nowrap" style={{ textAlign: 'right' }}>
                          {f.days_to_half_spent != null ? `${f.days_to_half_spent} days` : '—'}
                        </td>
                        <td
                          className={`num nowrap font-semibold ${f.left_over < 0 ? 'neg' : 'pos'}`}
                          style={{ textAlign: 'right' }}
                        >
                          {money(f.left_over)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        </div>
      )}

      {/* Outliers Radar */}
      {analysis?.unusual?.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <Section
            title="Statistical Outliers Detected"
            subtitle="Transactions exceeding 3σ historical standard deviation for their specific category."
          />

          <Card pad={false}>
            <div style={{ overflowX: 'auto' }}>
              <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Description</th>
                    <th>Category</th>
                    <th style={{ textAlign: 'right' }}>Amount</th>
                    <th>Anomaly Signal</th>
                  </tr>
                </thead>
                <tbody>
                  {analysis.unusual.map((t) => (
                    <tr
                      key={t.id}
                      className="terminal-row"
                      style={{ cursor: 'pointer' }}
                      onClick={() => drill({
                        title: t.merchant || titleCase(t.category),
                        subtitle: `${t.reason}. Shown against full category distribution for context.`,
                        params: { category: t.category },
                      })}
                    >
                      <td className="nowrap muted">{dateLabel(t.date)}</td>
                      <td className="font-semibold">{t.description}</td>
                      <td className="nowrap"><Chip size="sm">{titleCase(t.category)}</Chip></td>
                      <td className="num neg font-semibold nowrap" style={{ textAlign: 'right' }}>
                        {money(t.amount)}
                      </td>
                      <td className="small muted">{t.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}

      {categories.length === 0 && (
        <Empty
          title={scoped ? `No outflow recorded in ${periodLabel}` : 'No spending records found'}
          icon="trending"
        >
          {scoped && 'Widen or reset the period selector above to inspect full ledger history.'}
        </Empty>
      )}

      <Callout tone="info">
        Every metric on this screen is guaranteed by backend ledger balance invariant:
        categories sum to verified net outflow to the exact rupee without approximation.
      </Callout>
    </div>
  );
}

function nextSalaryDate(flows, current) {
  const i = flows.findIndex((f) => f.salary_date === current.salary_date);
  return (i >= 0 ? flows[i + 1] : null)?.salary_date || undefined;
}

function buildStack(analysis) {
  const perMonth = analysis?.monthly_by_category || {};
  const months = analysis?.monthly || [];
  const top = (analysis?.by_category || []).slice(0, 6).map((c) => c.category);
  if (!top.length || !months.length || !Object.keys(perMonth).length) {
    return { data: [], keys: [] };
  }
  const data = months.map((m) => {
    const actual = perMonth[m.month] || {};
    const row = { label: monthLabel(m.month) };
    let assigned = 0;
    for (const key of top) {
      const v = actual[key] || 0;
      row[key] = v;
      assigned += v;
    }
    const everything = Object.values(actual).reduce((a, b) => a + b, 0);
    row.other = Math.max(0, everything - assigned);
    return row;
  });
  return { data, keys: [...top, 'other'] };
}
