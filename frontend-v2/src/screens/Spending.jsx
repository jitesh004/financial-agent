/* Spending: where it went, in four ways of asking.
 *
 * By category or by group, by merchant, by month, and - the one nothing else
 * in the app answers - what happened in the days after each salary landed.
 */

import React, { useMemo, useState } from 'react';
import { usePeriod } from '../core/period';
import { useDrill } from '../core/drill';
import { useViewData } from '../core/ledger';
import { usePrefs } from '../core/prefs';
import { colorFor, count, dateLabel, money, monthLabel, pct, SPEND_ROLES, titleCase } from '../core/format';
import {
  BarList, Callout, Card, Chip, Empty, Legend, Section, Segmented, Skeleton,
  SkeletonStats, Stat, Table,
} from '../ui';
import { BarChart, Donut, useChartKeyframes } from '../ui/charts';
import { PeriodEmpty } from '../app/PeriodBar';

export default function Spending() {
  useChartKeyframes();
  const { data, loading, windowEmpty } = useViewData();
  const { label: periodLabel, scoped } = usePeriod();
  const { drill } = useDrill();
  const [prefs] = usePrefs();
  const [groupBy, setGroupBy] = useState('category');
  const [shape, setShape] = useState('bars');

  const analysis = data?.analysis;

  const categories = useMemo(() => (analysis?.by_category || []).map((c, i) => ({
    ...c, label: c.category, value: c.total, color: colorFor(i),
  })), [analysis]);

  const groups = useMemo(() => Object.entries(analysis?.by_group || {})
    .map(([label, value], i) => ({
      label,
      value,
      color: colorFor(i),
      // A group is a set of categories, and the transactions endpoint takes a
      // list - so a group drills into exactly the categories it is made of,
      // read off the same breakdown the bars came from.
      categories: (analysis?.by_category || [])
        .filter((c) => c.group === label).map((c) => c.category),
    })), [analysis]);

  const stack = useMemo(() => buildStack(analysis), [analysis]);

  if (loading) {
    return <><SkeletonStats n={3} /><div className="card"><div className="card-body">
      <Skeleton lines={10} /></div></div></>;
  }
  if (!data) return <Empty title="Nothing analysed yet" icon="trending" />;
  if (windowEmpty) return <PeriodEmpty available={data.available} />;

  const merchants = analysis.top_merchants || [];
  const salaryFlows = analysis.salary_flows || [];
  const latest = salaryFlows[salaryFlows.length - 1];
  const items = groupBy === 'category' ? categories : groups;

  return (
    <>
      <Section title="Spending breakdown" note={scoped ? periodLabel : undefined} />
      <div className="grid cols-3">
        <Stat
          label="Total spent" value={analysis.totals?.spend} tone="neg"
          onDrill={() => drill({
            title: 'Total spent',
            subtitle: 'Every row counted as spending in this period, net of anything '
              + 'that came back against it.',
            params: { flow_role: SPEND_ROLES },
          })}
        />
        <Stat
          label="Biggest category"
          value={categories[0] ? titleCase(categories[0].category) : '—'}
          note={categories[0]
            ? `${money(categories[0].total)} · ${pct(categories[0].share_pct)}` : ''}
        />
        {/* `count()`, not a bare number: Stat money-formats anything numeric,
            so a tally of uncategorised rows would render as "₹293" directly
            above a real rupee figure. */}
        <Stat
          label="Uncategorised"
          value={count(analysis.uncategorized?.count ?? 0)}
          tone={analysis.uncategorized?.count ? 'warn' : 'pos'}
          note={analysis.uncategorized?.total
            ? `${money(analysis.uncategorized.total)} outside the breakdown`
            : 'Everything categorised'}
          onDrill={analysis.uncategorized?.count ? () => drill({
            title: 'Uncategorised',
            subtitle: 'No rule matched these, so they sit outside the category '
              + 'breakdown. Setting a category here also teaches the merchant for '
              + 'every future statement.',
            params: { category: 'uncategorized' },
          }) : undefined}
        />
      </div>

      <div className="split">
        <Card
          title={groupBy === 'category' ? 'By category' : 'By group'}
          tools={(
            <>
              <Segmented value={groupBy} onChange={setGroupBy} ariaLabel="Break down by"
                options={[['category', 'Category'], ['group', 'Group']]} />
              <Segmented value={shape} onChange={setShape} ariaLabel="Shape"
                options={[['bars', 'Bars'], ['donut', 'Donut']]} />
            </>
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
                  ? `${money(item.value)} across ${item.categories.length} `
                    + `categor${item.categories.length === 1 ? 'y' : 'ies'}: `
                    + item.categories.map(titleCase).join(', ')
                  : `${money(item.value)} across ${item.count} `
                    + `transaction${item.count === 1 ? '' : 's'}`,
                params: {
                  category: item.categories ? item.categories.join(',') : item.category,
                  flow_role: SPEND_ROLES,
                },
              })}
            />
          ) : (
            <>
              <Donut
                height={260}
                data={items.filter((i) => i.value > 0).slice(0, 9)
                  .map((i) => ({ label: titleCase(i.label), value: i.value, color: i.color }))}
                centerLabel={groupBy === 'category' ? 'top categories' : 'all groups'}
              />
              <Legend items={items.filter((i) => i.value > 0).slice(0, 9)
                .map((i) => ({ label: titleCase(i.label), color: i.color }))} />
            </>
          )}
        </Card>

        <Card title="Top merchants" sub={`${merchants.length} tracked — click one for its history`}>
          <Table scrollY>
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
                <tr key={m.merchant} className="row-click"
                  title={`Show every ${m.merchant} transaction`}
                  onClick={() => drill({
                    title: m.merchant,
                    subtitle: `${money(m.total)} over ${m.count} `
                      + `transaction${m.count === 1 ? '' : 's'}, averaging ${money(m.average)}. `
                      + `First seen ${dateLabel(m.first_seen)}, last ${dateLabel(m.last_seen)}.`,
                    params: { merchant: m.merchant },
                  })}>
                  <td>
                    <div className="truncate" style={{ maxWidth: 210 }}>{m.merchant}</div>
                    <Chip>{titleCase(m.category)}</Chip>
                  </td>
                  <td className="right num nowrap">{money(m.total)}</td>
                  <td className="right num">{m.count}</td>
                  <td className="right num nowrap">{money(m.average)}</td>
                </tr>
              ))}
              {!merchants.length && (
                <tr><td colSpan={4} className="dim small">No merchants tracked yet.</td></tr>
              )}
            </tbody>
          </Table>
        </Card>
      </div>

      {stack.data.length > 0 && (
        <>
          <Section title="Category trend" note="the six biggest, everything else folded in" />
          <Card title="Monthly spend by top categories">
            <BarChart
              data={stack.data}
              stacked
              animate={prefs.animate}
              height={290}
              series={stack.keys.map((key, i) => ({
                key, name: titleCase(key), color: colorFor(i),
              }))}
            />
            <Legend items={stack.keys.map((key, i) => ({
              label: titleCase(key), color: colorFor(i),
            }))} />
          </Card>
        </>
      )}

      {/* ---- salary flow ---- */}
      {latest && (
        <>
          <Section title="After the salary landed" />
          <div className="split">
            <Card
              title={`${monthLabel(latest.month)} — salary of ${money(latest.salary_amount)}`}
              sub={latest.days_to_half_spent != null
                ? `Half gone in ${latest.days_to_half_spent} days` : undefined}
            >
              <BarList
                items={latest.allocations.map((a, i) => ({
                  label: a.category, value: a.amount, color: colorFor(i), category: a.category,
                }))}
                total={latest.salary_amount}
                max={10}
                onPick={(item) => drill({
                  title: titleCase(item.label),
                  subtitle: `${money(item.value)} in the days between this salary and the `
                    + 'next one.',
                  // The window is the gap between two paydays, which is a
                  // stretch of DAYS and not a month - so this pins its own
                  // dates rather than taking the page's period.
                  ignorePeriod: true,
                  periodLabel: `after the ${dateLabel(latest.salary_date)} salary`,
                  sortBy: 'date',
                  params: {
                    category: item.category,
                    start: latest.salary_date,
                    end: nextSalaryDate(salaryFlows, latest),
                  },
                })}
              />
              <div style={{ marginTop: 12 }}>
                {latest.left_over >= 0 ? (
                  <Chip tone="pos">
                    {money(latest.left_over)} still unspent when the next salary arrived
                  </Chip>
                ) : (
                  <Chip tone="warn">
                    Outflow exceeded this salary by {money(Math.abs(latest.left_over))} —
                    covered by other income or an existing balance
                  </Chip>
                )}
              </div>
            </Card>

            <Card title="Salary burn each month" sub="How fast half the salary was spent">
              <Table scrollY>
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
                      <td className={`right num nowrap ${f.left_over < 0 ? 'neg' : ''}`}>
                        {money(f.left_over)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </Card>
          </div>
        </>
      )}

      {/* ---- outliers ---- */}
      {analysis.unusual?.length > 0 && (
        <>
          <Section title="Unusually large for their category" />
          <Card>
            <Table>
              <thead>
                <tr>
                  <th>Date</th><th>Description</th><th>Category</th>
                  <th className="right">Amount</th><th>Why flagged</th>
                </tr>
              </thead>
              <tbody>
                {analysis.unusual.map((t) => (
                  <tr key={t.id} className="row-click"
                    title={`Show every ${t.merchant || t.category} transaction`}
                    onClick={() => drill({
                      title: t.merchant || titleCase(t.category),
                      subtitle: `${t.reason} Shown against everything else in this `
                        + 'category, so you can see what typical looks like.',
                      params: { category: t.category },
                    })}>
                    <td className="nowrap">{dateLabel(t.date)}</td>
                    <td><div style={{ overflowWrap: 'anywhere' }}>{t.description}</div></td>
                    <td><Chip>{titleCase(t.category)}</Chip></td>
                    <td className="right num nowrap">{money(t.amount)}</td>
                    <td className="small dim">{t.reason}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
        </>
      )}

      {categories.length === 0 && (
        <Empty title={scoped ? `No spending counted in ${periodLabel}` : 'No spending to show'}
          icon="trending">
          {scoped && 'Widen the period, or clear it, to see the whole ledger.'}
        </Empty>
      )}

      <Callout>
        Every bar and every row on this screen is the server's own arithmetic over the
        rows in this window — click any of them to see exactly which rows.
      </Callout>
    </>
  );
}

/* When the next salary arrived, which is where a salary's window closes. The
   last one has no next, so the window stays open-ended rather than being
   closed at an invented date. */
function nextSalaryDate(flows, current) {
  const i = flows.findIndex((f) => f.salary_date === current.salary_date);
  return (i >= 0 ? flows[i + 1] : null)?.salary_date || undefined;
}

/* Stack the six biggest categories per month and fold the rest into "other",
   because a stacked bar with fifteen bands is decoration, not information.
 *
 * Every value here is the server's actual per-month, per-category total. An
 * earlier version approximated it by spreading each month's spend across
 * period-wide category shares, which looked identical but was invented -
 * exactly the kind of plausible-but-wrong figure this app exists to avoid. */
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
