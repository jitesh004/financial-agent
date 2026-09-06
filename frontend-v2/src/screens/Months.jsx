/* The period, every account, one month at a time.
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
 * The month strip below IS this screen's period control - it sets the app's
 * period rather than keeping a second one of its own, which is why the bar
 * that appears above the other screens is not repeated here.
 */

import React, { useMemo, useRef, useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { usePeriod } from '../core/period';
import { useDrill } from '../core/drill';
import { useViewData } from '../core/ledger';
import {
  count, dateLabel, money, monthLabel, monthLabelLong, titleCase,
} from '../core/format';
import { Callout, Card, Chip, Empty, Loading, Segmented, Stat, Table, Toggle } from '../ui';
import { VirtualBody } from '../ui/virtual';
import { PeriodEmpty } from '../app/PeriodBar';

const ROLE_LABEL = {
  income: 'Income', expense: 'Spending', investment: 'Invested',
  claim_settlement: 'Money back', refund: 'Refund', card_settlement: 'Card payment',
  transfer_out: 'Transfer out', transfer_in: 'Transfer in', excluded: 'Ignored',
};
const ROLE_TONE = {
  income: 'pos', claim_settlement: 'pos', refund: 'pos', investment: 'acc',
  excluded: 'warn',
};

//: The ledger can be long. One page of rows is what a screen can show, and the
//: per-month summary above it is the answer for a window this big.
const ROW_LIMIT = 1000;
const ROW_H = 58;

export default function Months() {
  const {
    params, paramsKey, label, scoped, window: resolved, months, setPeriod,
  } = usePeriod();
  const { data: view, loading: loadingView } = useViewData();
  const { drill } = useDrill();
  const [groupBy, setGroupBy] = useState('date');
  const scrollRef = useRef(null);

  // Filtered server-side on the accounting month, so this never pulls the
  // whole ledger down to throw most of it away.
  const { data, loading, error } = useQuery(
    `months:${paramsKey}`,
    () => api.transactions({ ...params, limit: ROW_LIMIT, sort_by: 'date', sort_dir: 'desc' }),
  );

  const rows = data?.transactions || [];
  const total = data?.total ?? rows.length;
  const monthly = view?.analysis?.monthly || [];

  /* The tiles total the PERIOD, not the page.
   *
   * They used to be summed from `rows`, which is capped. On a ledger with more
   * rows than that the four figures at the top quietly described the most
   * recent thousand and nothing said so - "Money in 17,19,649" above a table
   * on the same screen whose rows added to 21,83,336. `analysis.monthly` is
   * the server's own per-month figures, so the two now agree by construction. */
  const totals = useMemo(() => {
    const t = { inflow: 0, outflow: 0, offsets: 0, invested: 0 };
    if (monthly.length) {
      for (const m of monthly) {
        t.inflow += Number(m.income) || 0;
        t.outflow += Number(m.gross_spend ?? m.spend) || 0;
        t.offsets += Number(m.offsets) || 0;
        t.invested += Number(m.invested) || 0;
      }
      return t;
    }
    for (const r of rows) {
      const amount = Math.abs(Number(r.amount) || 0);
      const role = r.flow_role || '';
      if (role === 'income') t.inflow += amount;
      else if (role === 'claim_settlement' || role === 'refund') t.offsets += amount;
      else if (role === 'investment') t.invested += amount;
      else if (role === 'expense') t.outflow += amount;
    }
    return t;
  }, [rows, monthly]);

  /* Months in the window with nothing in them.
   *
   * A month with no statement loaded looks like a genuine dip in spending,
   * which is the most misleading thing a chart can do. The analysis reports a
   * row per month it found figures for, so a month in the window absent from
   * that list is a hole - and naming the holes beats presenting a partial
   * period as a whole one. */
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
    if (!rows.length) return [];
    if (groupBy === 'date') return [['', rows]];
    const keyOf = groupBy === 'category' ? (r) => titleCase(r.category)
      : groupBy === 'month' ? (r) => monthLabelLong(r.accounting_month || r.date?.slice(0, 7))
        : (r) => ROLE_LABEL[r.flow_role] || 'Unclassified';
    const out = new Map();
    for (const r of rows) {
      const k = keyOf(r);
      if (!out.has(k)) out.set(k, []);
      out.get(k).push(r);
    }
    return [...out.entries()].sort(
      (a, b) => b[1].reduce((s, r) => s + Math.abs(r.amount), 0)
              - a[1].reduce((s, r) => s + Math.abs(r.amount), 0));
  }, [rows, groupBy]);

  const net = totals.inflow - (totals.outflow - totals.offsets);
  const singleMonth = resolved?.months === 1;
  const truncated = data && total > rows.length;

  return (
    <>
      <div className="page-head">
        <div className="grow">
          <h2 className="h2">{scoped ? label : 'Every month'}</h2>
          <p className="lead">
            Cards and bank accounts together, counted once each — in the month each row
            is counted in, not the month printed on it.
          </p>
        </div>
        <Segmented
          value={groupBy}
          onChange={setGroupBy}
          ariaLabel="Group rows by"
          options={[
            ['date', 'By date'], ['category', 'By category'], ['role', 'By type'],
            ...(singleMonth ? [] : [['month', 'By month']]),
          ]}
        />
      </div>

      {/* Every month there is data for, one click each. */}
      {months.length > 1 && (
        <div className="row tight" role="group" aria-label="Choose a month">
          <Toggle on={!scoped} onClick={() => setPeriod({ preset: 'all' })}
            title="Every month, together">
            All
          </Toggle>
          {[...months].reverse().map((m) => {
            const inWindow = resolved?.startMonth
              && m.month >= resolved.startMonth && m.month <= resolved.endMonth;
            return (
              <Toggle
                key={m.month}
                on={Boolean(inWindow)}
                title={`${m.count} transaction${m.count === 1 ? '' : 's'}`}
                onClick={() => setPeriod({
                  preset: 'custom_months', start_month: m.month, end_month: m.month,
                })}
              >
                {monthLabel(m.month)}
              </Toggle>
            );
          })}
        </div>
      )}

      {error && <Callout tone="neg">{error.message}</Callout>}
      {missing.length > 0 && (
        <Callout tone="warn">
          No statement covering {missing.map(monthLabelLong).join(', ')} has been parsed
          yet, so this period is incomplete. The coverage grid under Data shows which
          months are missing per account.
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
            ? `${money(totals.outflow)} less ${money(totals.offsets)} back` : undefined}
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
        <Card title="Month by month" sub={`${monthly.length} months in this period`}>
          <Table>
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
                    <button className="btn link" title={`Show only ${monthLabelLong(m.month)}`}
                      onClick={() => setPeriod({
                        preset: 'custom_months', start_month: m.month, end_month: m.month,
                      })}>
                      {monthLabelLong(m.month)}
                    </button>
                  </td>
                  <td className="right num nowrap">{money(m.income)}</td>
                  <td className="right num nowrap">{money(m.spend)}</td>
                  <td className="right num nowrap">{money(m.invested)}</td>
                  <td className={`right num nowrap ${m.net < 0 ? 'neg' : ''}`}>{money(m.net)}</td>
                  <td className="right num">{m.transaction_count}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      {(loading || loadingView) && <Loading label="Reading the rows…" />}

      {data && !rows.length && (scoped
        ? <PeriodEmpty available={view?.available} />
        : (
          <Empty title="Nothing recorded yet" icon="calendar">
            Import a statement, or scan your mailbox, to see your months here.
          </Empty>
        ))}

      {truncated && (
        <Callout tone="warn">
          Showing the {rows.length} most recent of {count(total)} transactions in this
          period. Narrow the period, or use the Ledger, to page through the rest.
        </Callout>
      )}

      {rows.length > 0 && grouped.map(([name, groupRows]) => (
        <Card
          key={name || 'all'}
          title={name || 'All transactions'}
          sub={`${count(groupRows.length)} transaction${groupRows.length === 1 ? '' : 's'}`}
          pad={false}
        >
          <div ref={groupBy === 'date' ? scrollRef : undefined}
            style={groupBy === 'date'
              ? { maxHeight: 620, overflowY: 'auto' } : { overflowX: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th>Date</th><th>Description</th><th>Category</th><th>Counts as</th>
                  <th className="right">Amount</th>
                </tr>
              </thead>
              {groupBy === 'date' ? (
                <VirtualBody count={groupRows.length} rowHeight={ROW_H}
                  containerRef={scrollRef} columns={5}>
                  {(i) => <MonthRow key={groupRows[i].id} r={groupRows[i]} />}
                </VirtualBody>
              ) : (
                <tbody>
                  {groupRows.map((r) => <MonthRow key={r.id} r={r} />)}
                </tbody>
              )}
            </table>
          </div>
        </Card>
      ))}
    </>
  );
}

function MonthRow({ r }) {
  return (
    <tr>
      <td className="nowrap">
        {dateLabel(r.date)}
        {/* The month it is counted in, shown only when that is not the month
            it is dated in - which is exactly the case this model exists for. */}
        {r.accounting_month && r.accounting_month !== String(r.date).slice(0, 7) && (
          <div className="tiny dim" title="Counted in this month, not the month of the date">
            counts in {monthLabel(r.accounting_month)}
          </div>
        )}
      </td>
      <td>
        <div style={{ overflowWrap: 'anywhere', lineHeight: 1.4 }}>{r.description}</div>
        {r.note && <div className="tiny dim">{r.note}</div>}
      </td>
      <td className="nowrap">{titleCase(r.category)}</td>
      <td className="nowrap">
        <Chip tone={ROLE_TONE[r.flow_role] || ''}>
          {ROLE_LABEL[r.flow_role] || 'Unclassified'}
        </Chip>
      </td>
      <td className="right num nowrap" style={{
        color: r.direction === 'credit' ? 'var(--pos)' : 'var(--text)',
        opacity: r.flow_role === 'excluded' ? 0.5 : 1,
      }}>
        {r.direction === 'credit' ? '+' : '−'}{money(Math.abs(r.amount))}
      </td>
    </tr>
  );
}
