import React, { useMemo, useRef, useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { usePeriod } from '../core/period';
import { useDrill } from '../core/drill';
import { useViewData } from '../core/ledger';
import { count, dateLabel, money, monthLabel, monthLabelLong, titleCase } from '../core/format';
import { Callout, Card, GlassCard, Chip, Empty, Loading, Segmented, Stat, Table, Badge, Button } from '../ui';
import { VirtualBody } from '../ui/virtual';
import { PeriodEmpty } from '../app/PeriodBar';

const ROLE_LABEL = {
  income: 'Income',
  expense: 'Spending',
  investment: 'Invested',
  claim_settlement: 'Money Back',
  refund: 'Refund',
  card_settlement: 'Card Payment',
  transfer_out: 'Transfer Out',
  transfer_in: 'Transfer In',
  excluded: 'Ignored',
};

const ROLE_TONE = {
  income: 'pos',
  claim_settlement: 'pos',
  refund: 'pos',
  investment: 'brand',
  excluded: 'warn',
};

const ROW_LIMIT = 1000;
const ROW_H = 58;

export default function Months() {
  const { params, paramsKey, label, scoped, window: resolved, months, setPeriod } = usePeriod();
  const { data: view, loading: loadingView } = useViewData();
  const { drill } = useDrill();
  const [groupBy, setGroupBy] = useState('date');
  const scrollRef = useRef(null);

  const { data, loading, error } = useQuery(
    `months:${paramsKey}`,
    () => api.transactions({ ...params, limit: ROW_LIMIT, sort_by: 'date', sort_dir: 'desc' })
  );

  const rows = data?.transactions || [];
  const total = data?.total ?? rows.length;
  const monthly = view?.analysis?.monthly || [];

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
              - a[1].reduce((s, r) => s + Math.abs(r.amount), 0)
    );
  }, [rows, groupBy]);

  const net = totals.inflow - (totals.outflow - totals.offsets);
  const singleMonth = resolved?.months === 1;
  const truncated = data && total > rows.length;

  return (
    <div className="months-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>{scoped ? label : 'Accounting Months Matrix'}</h1>
            {scoped && <Badge tone="brand" size="sm">Filtered Period</Badge>}
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            Accounting month allocation (prevents double-counting bank transfers & credit card bill payoffs).
          </p>
        </div>

        <Segmented
          value={groupBy}
          onChange={setGroupBy}
          ariaLabel="Group transactions by"
          options={[
            ['date', 'Chronological'],
            ['category', 'By Category'],
            ['role', 'By Flow Role'],
            ...(singleMonth ? [] : [['month', 'By Month']]),
          ]}
        />
      </div>

      {/* Month Fast Selector Strip */}
      {months.length > 1 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
          <button
            className={`btn btn-sm ${!scoped ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setPeriod({ preset: 'all' })}
          >
            All Recorded Months
          </button>
          {[...months].reverse().map((m) => {
            const inWindow = resolved?.startMonth
              && m.month >= resolved.startMonth && m.month <= resolved.endMonth;
            return (
              <button
                key={m.month}
                className={`btn btn-sm ${inWindow ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setPeriod({
                  preset: 'custom_months',
                  start_month: m.month,
                  end_month: m.month,
                })}
              >
                {monthLabel(m.month)} ({m.count})
              </button>
            );
          })}
        </div>
      )}

      {error && <Callout tone="neg">{error.message}</Callout>}
      {missing.length > 0 && (
        <Callout tone="warn">
          Missing statement coverage detected for: <strong>{missing.map(monthLabelLong).join(', ')}</strong>.
          Check the Data screen matrix to resolve coverage gaps.
        </Callout>
      )}

      {/* Top Level Summary Cards */}
      <div className="stats-grid">
        <Stat
          label="Total Inflow"
          value={money(totals.inflow)}
          tone="pos"
          onDrill={() => drill({
            title: 'Income Rows',
            subtitle: `Verified inflow transactions for ${label}.`,
            params: { flow_role: 'income' },
          })}
        />
        <Stat
          label="Net Outflow"
          value={money(totals.outflow - totals.offsets)}
          tone="neg"
          sub={totals.offsets ? `${money(totals.outflow)} gross less ${money(totals.offsets)} offsets` : undefined}
          onDrill={() => drill({
            title: 'Outflow Rows',
            subtitle: `Spending in ${label}, net of reimbursements and refunds.`,
            params: { flow_role: 'expense,refund,claim_settlement' },
          })}
        />
        <Stat
          label="Invested Wealth"
          value={money(totals.invested)}
          tone="brand"
          onDrill={() => drill({
            title: 'Capital Investments',
            subtitle: `Invested capital allocations in ${label}.`,
            params: { flow_role: 'investment' },
          })}
        />
        <Stat
          label="Net Period Delta"
          value={money(net)}
          tone={net >= 0 ? 'pos' : 'neg'}
          sub={net >= 0 ? 'Net positive accumulation' : 'Deficit drawn from balances'}
        />
      </div>

      {/* Month By Month Breakdown Table */}
      {!singleMonth && monthly.length > 1 && (
        <Card title="Comparative Monthly Trajectory" subtitle={`${monthly.length} accounting months in selected window`}>
          <div style={{ overflowX: 'auto' }}>
            <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th>Month</th>
                  <th style={{ textAlign: 'right' }}>Money In</th>
                  <th style={{ textAlign: 'right' }}>Money Out</th>
                  <th style={{ textAlign: 'right' }}>Invested</th>
                  <th style={{ textAlign: 'right' }}>Net Cashflow</th>
                  <th style={{ textAlign: 'right' }}>Transactions</th>
                </tr>
              </thead>
              <tbody>
                {[...monthly].reverse().map((m) => (
                  <tr key={m.month} className="terminal-row">
                    <td>
                      <Button
                        variant="link"
                        size="sm"
                        onClick={() => setPeriod({
                          preset: 'custom_months',
                          start_month: m.month,
                          end_month: m.month,
                        })}
                      >
                        {monthLabelLong(m.month)}
                      </Button>
                    </td>
                    <td className="num pos nowrap" style={{ textAlign: 'right' }}>{money(m.income)}</td>
                    <td className="num neg nowrap" style={{ textAlign: 'right' }}>{money(m.spend)}</td>
                    <td className="num brand nowrap" style={{ textAlign: 'right' }}>{money(m.invested)}</td>
                    <td className={`num nowrap font-semibold ${m.net < 0 ? 'neg' : 'pos'}`} style={{ textAlign: 'right' }}>
                      {money(m.net)}
                    </td>
                    <td className="num nowrap" style={{ textAlign: 'right' }}>{m.transaction_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {(loading || loadingView) && <Loading label="Synthesizing multi-month records…" />}

      {data && !rows.length && (scoped
        ? <PeriodEmpty available={view?.available} />
        : (
          <Empty title="No ledger records available" icon="calendar">
            Import statements to begin tracking monthly cashflows.
          </Empty>
        ))}

      {truncated && (
        <Callout tone="warn">
          Displaying most recent {rows.length} of {count(total)} transactions in window.
          Use the Ledger terminal to filter or paginate all entries.
        </Callout>
      )}

      {/* Grouped Table View */}
      {rows.length > 0 && grouped.map(([name, groupRows]) => (
        <Card
          key={name || 'all'}
          title={name || 'All Window Transactions'}
          subtitle={`${count(groupRows.length)} record${groupRows.length === 1 ? '' : 's'}`}
          pad={false}
        >
          <div
            ref={groupBy === 'date' ? scrollRef : undefined}
            style={groupBy === 'date' ? { maxHeight: 620, overflowY: 'auto' } : { overflowX: 'auto' }}
          >
            <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Description</th>
                  <th>Category</th>
                  <th>Flow Role</th>
                  <th style={{ textAlign: 'right' }}>Amount</th>
                </tr>
              </thead>
              {groupBy === 'date' ? (
                <VirtualBody count={groupRows.length} rowHeight={ROW_H} containerRef={scrollRef} columns={5}>
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
    </div>
  );
}

function MonthRow({ r }) {
  const isCredit = r.direction === 'credit';
  return (
    <tr className="terminal-row">
      <td className="nowrap muted">
        <div>{dateLabel(r.date)}</div>
        {r.accounting_month && r.accounting_month !== String(r.date).slice(0, 7) && (
          <div className="tiny warn" title="Allocated to this accounting month due to payday timing">
            counts in {monthLabel(r.accounting_month)}
          </div>
        )}
      </td>
      <td>
        <div className="font-semibold" style={{ overflowWrap: 'anywhere' }}>{r.description}</div>
        {r.note && <div className="tiny muted">{r.note}</div>}
      </td>
      <td className="nowrap">{titleCase(r.category)}</td>
      <td className="nowrap">
        <Chip tone={ROLE_TONE[r.flow_role] || ''} size="sm">
          {ROLE_LABEL[r.flow_role] || 'Unclassified'}
        </Chip>
      </td>
      <td
        className={`num nowrap font-semibold ${isCredit ? 'pos' : ''}`}
        style={{
          textAlign: 'right',
          opacity: r.flow_role === 'excluded' ? 0.4 : 1,
        }}
      >
        {isCredit ? '+' : '−'}{money(Math.abs(r.amount))}
      </td>
    </tr>
  );
}
