import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Callout, Card, Chip, Empty, Stat } from './ui';
import { api, dateLabel, money, monthLabel, titleCase } from '../lib';

/* One month, every account, in one list.
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
 *    month and none in the next. */

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

function recentMonths(count = 18) {
  const now = new Date();
  return Array.from({ length: count }, (_, i) => {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  });
}

export default function MonthView({ data }) {
  const months = useMemo(recentMonths, []);
  const [month, setMonth] = useState(months[0]);
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [groupBy, setGroupBy] = useState('date');

  const load = useCallback(() => {
    setRows(null);
    // Filtered server-side on accounting_month, so this never pulls the whole
    // ledger down to throw most of it away.
    api.transactions({ accounting_month: month, limit: 1000, sort_by: 'date' })
      .then((res) => { setRows(res.transactions || []); setError(null); })
      .catch((e) => { setError(e.message); setRows([]); });
  }, [month]);

  useEffect(load, [load]);

  const totals = useMemo(() => {
    const t = { inflow: 0, outflow: 0, offsets: 0, invested: 0, neutral: 0 };
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
  }, [rows]);

  // Months with no statement loaded look like a genuine dip in spending,
  // which is the most misleading thing a chart can do. The coverage grid
  // knows which months are incomplete; surface that here rather than
  // presenting a partial month as a real one.
  const incomplete = useMemo(() => {
    const monthly = data?.analysis?.monthly || [];
    return monthly.length > 0 && !monthly.some((m) => m.month === month);
  }, [data, month]);

  const grouped = useMemo(() => {
    if (!rows) return [];
    if (groupBy === 'date') return [['', rows]];
    const key = groupBy === 'category'
      ? (r) => titleCase(r.category)
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        alignItems: 'center', gap: 12, flexWrap: 'wrap',
      }}
      >
        <div>
          <h2 className="section-title" style={{ marginBottom: 4 }}>
            {monthLabel(month)}
          </h2>
          <p style={{ color: 'var(--text-2)', margin: 0 }}>
            Cards and bank accounts together, counted once each.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <select value={month} onChange={(e) => setMonth(e.target.value)}>
            {months.map((m) => (
              <option key={m} value={m}>{monthLabel(m)}</option>
            ))}
          </select>
          <div className="seg">
            {[['date', 'By date'], ['category', 'By category'], ['role', 'By type']]
              .map(([v, label]) => (
                <button
                  key={v}
                  className={`seg-btn ${groupBy === v ? 'active' : ''}`}
                  onClick={() => setGroupBy(v)}
                >
                  {label}
                </button>
              ))}
          </div>
        </div>
      </div>

      {error && <Callout tone="neg">{error}</Callout>}
      {incomplete && (
        <Callout tone="warn">
          No statement covering this month has been parsed yet, so these
          figures are incomplete. Check the Coverage grid under Files &amp;
          Passwords.
        </Callout>
      )}

      <div className="grid cols-4">
        <Stat label="Money in" value={totals.inflow} tone="pos" />
        <Stat
          label="Money out"
          value={totals.outflow - totals.offsets}
          tone="neg"
          note={totals.offsets
            ? `${money(totals.outflow)} less ${money(totals.offsets)} back`
            : undefined}
        />
        <Stat label="Invested" value={totals.invested} tone="accent" />
        <Stat label="Net" value={net} tone={net >= 0 ? 'pos' : 'neg'} />
      </div>

      {rows === null && <div className="spinner" style={{ margin: 40 }} />}
      {rows && !rows.length && (
        <Empty title={`Nothing recorded for ${monthLabel(month)}`}>
          Either no statement covering this month has been parsed, or the
          ledger has not been re-analysed since accounting periods were
          introduced. Re-parse from the Data tab to populate them.
        </Empty>
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
                    <td className="nowrap">{dateLabel(r.date)}</td>
                    <td>
                      <div className="truncate" title={r.description}>
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
