import React, { useMemo, useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { colorFor, compact, dateLabel, money, monthLabel, pct, titleCase } from '../lib';
import { BarList, Card, ChartTooltip, Chip, Empty, Stat, axisProps, moneyAxis } from './ui';
import { usePeriod } from '../period';

export default function Spending({ data }) {
  const { analysis } = data;
  const [groupBy, setGroupBy] = useState('category');
  // Every figure on this page came from the window's rows; this names it.
  const { label: periodLabel, scoped } = usePeriod();

  const categories = (analysis.by_category || []).map((c, i) => ({
    ...c, label: c.category, value: c.total, color: colorFor(i),
  }));

  const groups = Object.entries(analysis.by_group || {}).map(([label, value], i) => ({
    label, value, color: colorFor(i),
  }));

  const merchants = analysis.top_merchants || [];
  const salaryFlows = analysis.salary_flows || [];
  const latestFlow = salaryFlows[salaryFlows.length - 1];

  const monthlyByTop = useMemo(() => buildStack(analysis), [analysis]);

  return (
    <>
      <div className="section-title">
        Spending breakdown
        {scoped && <span className="section-note">{periodLabel}</span>}
      </div>
      <div className="grid cols-3">
        <Stat label="Total spent" value={analysis.totals?.spend} />
        <Stat
          label="Biggest category"
          value={categories[0] ? titleCase(categories[0].category) : '—'}
          note={categories[0] ? `${money(categories[0].total)} · ${pct(categories[0].share_pct)}` : ''}
        />
        <Stat
          label="Needs review"
          value={analysis.uncategorized?.count ?? 0}
          tone={analysis.uncategorized?.count ? 'neg' : 'pos'}
          note={analysis.uncategorized?.total ? money(analysis.uncategorized.total) : 'Everything categorized'}
        />
      </div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Card
          title={groupBy === 'category' ? 'By category' : 'By group'}
          sub={(
            <select value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
              <option value="category">Category</option>
              <option value="group">Group</option>
            </select>
          )}
        >
          <BarList
            items={groupBy === 'category' ? categories : groups}
            total={analysis.totals?.spend}
            max={groupBy === 'category' ? 14 : 8}
          />
        </Card>

        <Card title="Top merchants" sub={`${merchants.length} tracked`}>
          <div className="table-wrap scroll-y">
            <table>
              <thead>
                <tr>
                  <th>Merchant</th>
                  <th className="right">Total</th>
                  <th className="right">Times</th>
                  <th className="right">Average</th>
                </tr>
              </thead>
              <tbody>
                {merchants.map((m) => (
                  <tr key={m.merchant}>
                    <td>
                      <div className="truncate" style={{ maxWidth: 200 }}>{m.merchant}</div>
                      <Chip>{titleCase(m.category)}</Chip>
                    </td>
                    <td className="right num nowrap">{money(m.total)}</td>
                    <td className="right num">{m.count}</td>
                    <td className="right num nowrap">{money(m.average)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {monthlyByTop.data.length > 0 && (
        <>
          <div className="section-title">Category trend</div>
          <Card title="Monthly spend by top categories">
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={monthlyByTop.data} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="var(--border)" vertical={false} />
                <XAxis dataKey="label" {...axisProps} />
                <YAxis {...moneyAxis} />
                <Tooltip content={<ChartTooltip />} cursor={{ fill: 'var(--surface-2)' }} />
                {monthlyByTop.keys.map((key, i) => (
                  <Bar key={key} dataKey={key} name={titleCase(key)} stackId="s"
                    fill={colorFor(i)} maxBarSize={34} />
                ))}
              </BarChart>
            </ResponsiveContainer>
            <div className="legend">
              {monthlyByTop.keys.map((key, i) => (
                <span className="legend-item" key={key}>
                  <i className="dot" style={{ background: colorFor(i) }} />{titleCase(key)}
                </span>
              ))}
            </div>
          </Card>
        </>
      )}

      {/* ---- Salary flow ---- */}
      {latestFlow && (
        <>
          <div className="section-title">After the salary landed</div>
          <div className="grid cols-2">
            <Card
              title={`${monthLabel(latestFlow.month)} — salary of ${money(latestFlow.salary_amount)}`}
              sub={latestFlow.days_to_half_spent != null
                ? `Half gone in ${latestFlow.days_to_half_spent} days`
                : ''}
            >
              <BarList
                items={latestFlow.allocations.map((a, i) => ({
                  label: a.category, value: a.amount, color: colorFor(i),
                }))}
                total={latestFlow.salary_amount}
                max={10}
              />
              <div style={{ marginTop: 12 }}>
                {latestFlow.left_over >= 0 ? (
                  <Chip tone="pos">{money(latestFlow.left_over)} still unspent when the next salary arrived</Chip>
                ) : (
                  <Chip tone="warn">
                    Outflow exceeded this salary by {money(Math.abs(latestFlow.left_over))} — covered by
                    other income or existing balance
                  </Chip>
                )}
              </div>
            </Card>

            <Card title="Salary burn each month" sub="How fast half the salary was spent">
              <div className="table-wrap scroll-y">
                <table>
                  <thead>
                    <tr>
                      <th>Month</th>
                      <th className="right">Salary</th>
                      <th className="right">Half spent</th>
                      <th className="right">Left over</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...salaryFlows].reverse().map((f) => (
                      <tr key={`${f.month}-${f.salary_date}`}>
                        <td className="nowrap">{monthLabel(f.month)}</td>
                        <td className="right num nowrap">{money(f.salary_amount)}</td>
                        <td className="right num">
                          {f.days_to_half_spent != null ? `${f.days_to_half_spent}d` : '—'}
                        </td>
                        <td className={`right num nowrap ${f.left_over < 0 ? 'stat-value neg' : ''}`}
                          style={{ fontSize: 13 }}>
                          {money(f.left_over)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        </>
      )}

      {/* ---- Outliers ---- */}
      {analysis.unusual?.length > 0 && (
        <>
          <div className="section-title">Unusually large for their category</div>
          <Card>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Date</th><th>Description</th><th>Category</th>
                    <th className="right">Amount</th><th>Why flagged</th>
                  </tr>
                </thead>
                <tbody>
                  {analysis.unusual.map((t) => (
                    <tr key={t.id}>
                      <td className="nowrap">{dateLabel(t.date)}</td>
                      <td><div style={{ wordBreak: 'break-word', whiteSpace: 'normal', lineHeight: '1.4' }}>{t.description}</div></td>
                      <td><Chip>{titleCase(t.category)}</Chip></td>
                      <td className="right num nowrap">{money(t.amount)}</td>
                      <td style={{ color: 'var(--text-3)', fontSize: 12.5 }}>{t.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}

      {categories.length === 0 && (
        <Empty title={scoped ? `No spending counted in ${periodLabel}`
          : 'No spending to show'}>
          {scoped && 'Widen the period, or clear it, to see the whole ledger.'}
        </Empty>
      )}
    </>
  );
}

/* Stack the six biggest categories per month and fold the rest into "other",
   because a stacked bar with fifteen bands is decoration, not information.

   Every value here is the server's actual per-month, per-category total. An
   earlier version approximated it by spreading each month's spend across
   period-wide category shares, which looked identical but was invented -
   exactly the kind of plausible-but-wrong figure this whole app exists to
   avoid. */
function buildStack(analysis) {
  const perMonth = analysis.monthly_by_category || {};
  const months = analysis.monthly || [];
  const top = (analysis.by_category || []).slice(0, 6).map((c) => c.category);
  if (!top.length || !months.length || !Object.keys(perMonth).length) {
    return { data: [], keys: [] };
  }

  const data = months.map((m) => {
    const actual = perMonth[m.month] || {};
    const row = { label: monthLabel(m.month) };
    let assigned = 0;
    for (const key of top) {
      const value = actual[key] || 0;
      row[key] = value;
      assigned += value;
    }
    const everything = Object.values(actual).reduce((a, b) => a + b, 0);
    row.other = Math.max(0, everything - assigned);
    return row;
  });

  return { data, keys: [...top, 'other'] };
}
