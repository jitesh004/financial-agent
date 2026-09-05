import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Callout, Card, Chip, Empty, Stat } from './ui';
import { PeriodEmpty } from './PeriodPicker';
import { api, dateLabel, money, monthLabel, monthLabelLong, titleCase } from '../lib';
import { usePeriod } from '../period';
import { useDrill } from '../drill';

/* The period, every account, in one list.
 *
 * Two things this has to get right that a naive version does not:
 *
 * 1. Inflow and outflow come from each row's ROLE, not its direction. A card
 *    bill payment is a credit on the card and a debit in the bank; counting
 *    both by direction inflates income and spending by the whole bill, which
 *    is the double-count the accounting model exists to remove.
 *
 * 2. The month is the ACCOUNTING month, not the calendar month of the date. A
 *    salary paid on the last working day lands on the 31st one month and the
 *    1st two months later; bucketing by raw date puts two salaries in one
 *    month and none in the next.
 *
 * The second rule used to live only here, behind this tab's own month picker.
 * It is now the rule the whole app's period control follows, so this tab reads
 * that period instead of keeping a second one - and the month strip below sets
 * it, rather than a private copy that could disagree with the Overview.
 */

const INFLOW_ROLES = new Set(['income']);
const OUTFLOW_ROLES = new Set(['expense', 'investment']);
const OFFSET_ROLES = new Set(['claim_settlement', 'refund']);

const ROLE_LABEL = {
  income: 'Income', expense: 'Spending', investment: 'Invested',
  claim_settlement: 'Money back', refund: 'Refund',
  card_settlement: 'Card payment', transfer_out: 'Transfer out',
  transfer_in: 'Transfer in', excluded: 'Ignored',
};

const ROLE_TONE = {
  income: 'pos', claim_settlement: 'pos', refund: 'pos',
  expense: '', investment: 'accent',
  card_settlement: '', transfer_out: '', transfer_in: '', excluded: 'warn',
};

//: The ledger can be long. One page of rows is what a screen can show, and
//: the per-month summary above it is the answer for a window this big.
const ROW_LIMIT = 1000;

export default function MonthView({ data }) {
  const {
    params, label, scoped, window: resolved, months, setPeriod,
  } = usePeriod();
  const { drill } = useDrill();
  const [rows, setRows] = useState(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState(null);
  const [groupBy, setGroupBy] = useState('date');

  const periodKey = JSON.stringify(params);

  const load = useCallback(() => {
    setRows(null);
    // Filtered server-side on the accounting month, so this never pulls the
    // whole ledger down to throw most of it away.
    api.transactions({ ...params, limit: ROW_LIMIT, sort_by: 'date',
                       sort_dir: 'desc' })
      .then((res) => {
        setRows(res.transactions || []);
        setTotal(res.total ?? (res.transactions || []).length);
        setError(null);
      })
      .catch((e) => { setError(e.message); setRows([]); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [periodKey]);

  useEffect(load, [load]);

  const monthly = data?.analysis?.monthly || [];

  /* The tiles total the PERIOD, not the page.
   *
   * They used to be summed from `rows`, which is capped at ROW_LIMIT. On a
   * ledger with more rows than that the four figures at the top quietly
   * described the most recent thousand and nothing said so - "Money in
   * 17,19,649" above a month-by-month table on the same screen whose eight
   * rows added to 21,83,336, the 4.6 lakh difference being the 193 oldest
   * transactions that never got loaded.
   *
   * `analysis.monthly` is the server's own per-month figures, the same ones
   * the table below renders and the Overview reports, so the two now agree by
   * construction. Falling back to the row sum only when there are no monthly
   * figures to read - a single-month window still has one. */
  const totals = useMemo(() => {
    const t = { inflow: 0, outflow: 0, offsets: 0, invested: 0, neutral: 0 };
    if (monthly.length) {
      for (const m of monthly) {
        t.inflow += Number(m.income) || 0;
        t.outflow += Number(m.gross_spend ?? m.spend) || 0;
        t.offsets += Number(m.offsets) || 0;
        t.invested += Number(m.invested) || 0;
      }
      return t;
    }
    for (const r of rows || []) {
      const amount = Math.abs(Number(r.amount) || 0);
      const role = r.flow_role || '';
      if (INFLOW_ROLES.has(role)) t.inflow += amount;
      else if (OFFSET_ROLES.has(role)) t.offsets += amount;
      else if (role === 'investment') t.invested += amount;
      else if (OUTFLOW_ROLES.has(role)) t.outflow += amount;
      else t.neutral += amount;
    }
    return t;
  }, [rows, monthly]);

  /* Months in the window with nothing in them.
   *
   * A month with no statement loaded looks like a genuine dip in spending,
   * which is the most misleading thing a chart can do. The analysis reports a
   * row per month it found figures for, so a month in the window absent from
   * that list is a hole - and naming the holes beats presenting a partial
   * period as a whole one.
   *
   * The span is enumerated here only to name those months. Which months a
   * period COVERS is the server's answer (see period.jsx); this is a label. */
  const missing = useMemo(() => {
    const first = resolved?.startMonth;
    const last = resolved?.endMonth;
    if (!first || !last || !monthly.length) return [];
    const have = new Set(monthly.map((m) => m.month));
    const gaps = [];
    for (let [year, month] = first.split('-').map(Number);
      `${year}-${String(month).padStart(2, '0')}` <= last;) {
      const key = `${year}-${String(month).padStart(2, '0')}`;
      if (!have.has(key)) gaps.push(key);
      month += 1;
      if (month > 12) { month = 1; year += 1; }
    }
    return gaps;
  }, [monthly, resolved]);

  const grouped = useMemo(() => {
    if (!rows) return [];
    if (groupBy === 'date') return [['', rows]];
    const key = groupBy === 'category'
      ? (r) => titleCase(r.category)
      : groupBy === 'month'
        ? (r) => monthLabelLong(r.accounting_month || r.date?.slice(0, 7))
        : (r) => ROLE_LABEL[r.flow_role] || 'Unclassified';
    const out = new Map();
    for (const r of rows) {
      const k = key(r);
      if (!out.has(k)) out.set(k, []);
      out.get(k).push(r);
    }
    return [...out.entries()].sort(
      (a, b) => b[1].reduce((s, r) => s + Math.abs(r.amount), 0)
              - a[1].reduce((s, r) => s + Math.abs(r.amount), 0));
  }, [rows, groupBy]);

  const net = totals.inflow - (totals.outflow - totals.offsets);
  const singleMonth = resolved?.months === 1;
  const truncated = rows !== null && total > rows.length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        alignItems: 'center', gap: 12, flexWrap: 'wrap',
      }}
      >
        <div>
          <h2 className="section-title" style={{ marginBottom: 4 }}>
            {scoped ? label : 'Every month'}
          </h2>
          <p style={{ color: 'var(--text-2)', margin: 0 }}>
            Cards and bank accounts together, counted once each — in the month
            each row is counted in, not the month printed on it.
          </p>
        </div>
        <div className="seg">
          {[['date', 'By date'], ['category', 'By category'],
            ['role', 'By type'], ...(singleMonth ? [] : [['month', 'By month']])]
            .map(([v, name]) => (
              <button
                key={v}
                className={`seg-btn ${groupBy === v ? 'active' : ''}`}
                onClick={() => setGroupBy(v)}
              >
                {name}
              </button>
            ))}
        </div>
      </div>

      {/* Every month there is data for, as one click each. The tab is called
          Months because this is how it is usually read - one month at a time -
          and this IS the period control on this tab: it sets the app's period
          rather than keeping a second one of its own, which is why the bar
          that appears above the other tabs is not repeated here. */}
      {months.length > 1 && (
        <div className="month-strip" role="group" aria-label="Choose a month">
          <button
            className={`chip-toggle ${scoped ? '' : 'selected'}`}
            title="Every month, together"
            onClick={() => setPeriod({ preset: 'all' })}
          >
            All
          </button>
          {[...months].reverse().map((m) => {
            const inWindow = resolved?.startMonth
              && m.month >= resolved.startMonth && m.month <= resolved.endMonth;
            return (
              <button
                key={m.month}
                className={`chip-toggle ${inWindow ? 'selected' : ''}`}
                title={`${m.count} transaction${m.count === 1 ? '' : 's'}`}
                onClick={() => setPeriod({ preset: 'custom_months',
                  start_month: m.month, end_month: m.month })}
              >
                {monthLabel(m.month)}
              </button>
            );
          })}
        </div>
      )}

      {error && <Callout tone="neg">{error}</Callout>}
      {missing.length > 0 && (
        <Callout tone="warn">
          No statement covering {missing.map(monthLabelLong).join(', ')} has been
          parsed yet, so this period is incomplete. The Coverage grid under Data
          shows which months are missing per account.
        </Callout>
      )}

      <div className="grid cols-4">
        <Stat label="Money in" value={totals.inflow} tone="pos"
          onDrill={() => drill({
            title: 'Money in',
            subtitle: `Everything counted as income in ${label}.`,
            params: { flow_role: 'income' },
          })} />
        <Stat
          label="Money out"
          value={totals.outflow - totals.offsets}
          tone="neg"
          note={totals.offsets
            ? `${money(totals.outflow)} less ${money(totals.offsets)} back`
            : undefined}
          onDrill={() => drill({
            title: 'Money out',
            subtitle: `Spending in ${label}, net of anything that came back.`,
            params: { flow_role: 'expense,refund,claim_settlement' },
          })}
        />
        <Stat label="Invested" value={totals.invested} tone="accent"
          onDrill={() => drill({
            title: 'Invested',
            subtitle: `Money moved into investments in ${label}.`,
            params: { flow_role: 'investment' },
          })} />
        <Stat label="Net" value={net} tone={net >= 0 ? 'pos' : 'neg'} />
      </div>

      {/* A window of several months reads as a table of months first, and the
          rows behind them second. */}
      {!singleMonth && monthly.length > 1 && (
        <Card title="Month by month"
          sub={`${monthly.length} months in this period`}>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Month</th>
                  <th className="right">Money in</th>
                  <th className="right">Money out</th>
                  <th className="right">Invested</th>
                  <th className="right">Net</th>
                  <th className="right">Rows</th>
                </tr>
              </thead>
              <tbody>
                {[...monthly].reverse().map((m) => (
                  <tr key={m.month}>
                    <td className="nowrap">
                      <button className="btn link"
                        title={`Show only ${monthLabelLong(m.month)}`}
                        onClick={() => setPeriod({ preset: 'custom_months',
                          start_month: m.month, end_month: m.month })}>
                        {monthLabelLong(m.month)}
                      </button>
                    </td>
                    <td className="right num nowrap">{money(m.income)}</td>
                    <td className="right num nowrap">{money(m.spend)}</td>
                    <td className="right num nowrap">{money(m.invested)}</td>
                    <td className={`right num nowrap ${m.net < 0 ? 'stat-value neg' : ''}`}
                      style={{ fontSize: 13 }}>
                      {money(m.net)}
                    </td>
                    <td className="right num">{m.transaction_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {rows === null && <div className="spinner" style={{ margin: 40 }} />}
      {rows && !rows.length && (scoped
        ? <PeriodEmpty available={data?.available} />
        : (
          <Empty title="Nothing recorded yet">
            Import a statement, or scan your mailbox, to see your months here.
          </Empty>
        ))}

      {truncated && (
        <Callout tone="warn">
          Showing the {rows.length} most recent of {total} transactions in this
          period. Narrow the period, or use the Ledger tab, to page through the
          rest.
        </Callout>
      )}

      {rows && rows.length > 0 && grouped.map(([groupName, groupRows]) => (
        <Card
          key={groupName || 'all'}
          title={groupName || 'All transactions'}
          sub={`${groupRows.length} transaction${groupRows.length === 1 ? '' : 's'}`}
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Description</th>
                  <th>Category</th>
                  <th>Counts as</th>
                  <th className="right">Amount</th>
                </tr>
              </thead>
              <tbody>
                {groupRows.map((r) => (
                  <tr key={r.id}>
                    <td className="nowrap">
                      {dateLabel(r.date)}
                      {/* The month it is counted in, shown only when that is
                          not the month it is dated in - which is exactly the
                          case this whole model exists for. */}
                      {r.accounting_month
                        && r.accounting_month !== String(r.date).slice(0, 7) && (
                        <div style={{ color: 'var(--text-3)', fontSize: 11 }}
                          title="Counted in this month, not the month of the date">
                          counts in {monthLabel(r.accounting_month)}
                        </div>
                      )}
                    </td>
                    <td>
                      <div style={{ wordBreak: 'break-word', whiteSpace: 'normal', lineHeight: '1.4' }}>
                        {r.description}
                      </div>
                      {r.note && (
                        <div style={{ color: 'var(--text-3)', fontSize: 12 }}>
                          {r.note}
                        </div>
                      )}
                    </td>
                    <td className="nowrap">{titleCase(r.category)}</td>
                    <td className="nowrap">
                      <Chip tone={ROLE_TONE[r.flow_role] || ''}>
                        {ROLE_LABEL[r.flow_role] || 'Unclassified'}
                      </Chip>
                    </td>
                    <td
                      className="right num nowrap"
                      style={{
                        color: r.direction === 'credit'
                          ? 'var(--positive)' : 'var(--text)',
                        opacity: r.flow_role === 'excluded' ? 0.5 : 1,
                      }}
                    >
                      {r.direction === 'credit' ? '+' : '−'}
                      {money(Math.abs(r.amount))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ))}
    </div>
  );
}
