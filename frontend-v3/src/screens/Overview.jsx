/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Executive Financial Command Center (Overview)
   Financial Health Score, Money Stream routing, Combo Cashflow & Anomaly Radar.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useMemo } from 'react';
import { Link } from '../core/router';
import { usePeriod } from '../core/period';
import { useDrill } from '../core/drill';
import { useViewData } from '../core/ledger';
import { usePrefs } from '../core/prefs';
import { useCopilot } from '../core/copilot';
import {
  colorFor, compact, count, dateLabel, money, monthLabel, pct, titleCase,
} from '../core/format';
import {
  BarList, Button, Callout, Card, GlassCard, Empty, Icon, Legend,
  SkeletonStats, Stat,
} from '../ui';
import { ComboChart } from '../ui/charts';
import { HealthScoreGauge, RunwayDial } from '../ui/gauges';
import { MoneyStreamFlow } from '../ui/flow';
import { PeriodEmpty } from '../app/PeriodBar';

const LIQUID_TYPES = new Set(['savings', 'current', 'wallet']);

const ACCOUNT_ICON = {
  credit_card: 'credit',
  home_loan: 'scales',
  personal_loan: 'scales',
  auto_loan: 'scales',
  investment: 'briefcase',
  savings: 'wallet',
  current: 'wallet',
};

export default function Overview({ onImport }) {
  const { data, loading, windowEmpty, error } = useViewData();
  const { label: periodLabel, scoped, window: resolved } = usePeriod();
  const { drill } = useDrill();
  const { openCopilot } = useCopilot();
  const [prefs] = usePrefs();

  if (loading) {
    return <SkeletonStats />;
  }
  if (error && !data) return <Callout tone="neg">{error.message}</Callout>;
  if (!data) {
    return (
      <Empty
        title="Nothing analysed yet"
        icon="inbox"
        action={<Button variant="primary" icon="upload" onClick={onImport}>Import statements</Button>}
      >
        Import a bank or card statement to unlock your reconciled executive command center.
      </Empty>
    );
  }
  if (windowEmpty) return <PeriodEmpty available={data.available} />;

  const {
    analysis, narrative, transfers = {}, accounts = [], forecast = {},
  } = data;
  const totals = analysis.totals || {};
  const period = analysis.period || {};
  const netWorth = analysis.net_worth || {};
  const position = analysis.position || {};

  const totalIncome = Number(totals.income) || 0;
  const totalSpend = Number(totals.spend ?? totals.spending) || 0;
  const avgMonthlyIncome = Number(totals.average_monthly_income) || 0;
  const avgMonthlySpend = Number(totals.average_monthly_spend ?? totals.average_monthly_spending) || 0;
  const netCashflow = totals.net_savings ?? totals.net_cashflow ?? (totalIncome - totalSpend);
  const netWorthVal = netWorth._net ?? netWorth.total ?? totals.closing_balance;

  const monthly = (analysis.monthly || []).map((m) => {
    const inc = Number(m.income) || 0;
    const sp = Number(m.spend ?? m.expense) || 0;
    const net = Number(m.net ?? (inc - sp)) || 0;
    return {
      ...m,
      label: monthLabel(m.month),
      income: inc,
      spend: sp,
      savings: net,
    };
  });

  const categories = (analysis.by_category || []).map((c, i) => ({
    ...c, label: titleCase(c.category), value: c.total, color: colorFor(i),
  }));

  // Calculate Financial Health Score (0-100)
  const rawSavingsRate = totals.savings_rate;
  const savingsRate = rawSavingsRate != null
    ? (Math.abs(rawSavingsRate) <= 1 ? rawSavingsRate * 100 : rawSavingsRate)
    : (totalIncome > 0 ? ((totalIncome - totalSpend) / totalIncome) * 100 : 0);
  /* "Liquid" means cash that can be spent this week: bank and wallet balances
     only. Retirement and demat holdings are inside `_assets` but cannot fund
     next month's bills, and counting them overstated the runway ~90x. The
     backend forecast resolves the same figure, so prefer it and fall back to
     summing the cash accounts. */
  const cashAccounts = accounts.filter(
    (a) => LIQUID_TYPES.has(a.account_type) && !a.is_liability,
  );
  const cashFromAccounts = cashAccounts.reduce(
    (sum, a) => sum + (Number(a.current_balance ?? a.balance) || 0), 0,
  );
  const liquidCash = Number.isFinite(Number(forecast.opening_balance))
    ? Number(forecast.opening_balance)
    : cashFromAccounts;
  const monthlyBurn = avgMonthlySpend || totalSpend || 0;
  const runwayMonths = Number.isFinite(Number(forecast.runway_months))
    ? Number(forecast.runway_months)
    : (monthlyBurn > 0 ? liquidCash / monthlyBurn : 0);

  /* Scored across the three things this card claims to weigh - savings rate,
     debt load and runway. Debt was advertised in the subtitle but never
     actually entered the score. */
  const liabilities = Number(netWorth._liabilities) || 0;
  const annualIncome = avgMonthlyIncome * 12;
  const debtToIncome = annualIncome > 0 ? liabilities / annualIncome : null;

  let healthScore = 40;
  if (savingsRate > 30) healthScore += 25;
  else if (savingsRate > 15) healthScore += 17;
  else if (savingsRate > 0) healthScore += 8;

  if (runwayMonths >= 6) healthScore += 25;
  else if (runwayMonths >= 3) healthScore += 15;
  else if (runwayMonths >= 1) healthScore += 7;

  if (debtToIncome == null) healthScore += 12;
  else if (debtToIncome <= 1) healthScore += 25;
  else if (debtToIncome <= 2) healthScore += 17;
  else if (debtToIncome <= 3.5) healthScore += 9;

  healthScore = Math.max(0, Math.min(100, healthScore));
  const healthGrade = healthScore >= 80 ? 'Excellent'
    : healthScore >= 65 ? 'Good'
      : healthScore >= 45 ? 'Fair' : 'Needs Attention';

  /* Largest exposure first, liabilities included - a card you owe on is as
     much a holding as a bank balance, and hiding it flatters the card. */
  const withBalance = accounts.filter((a) => (a.current_balance ?? a.balance) != null);
  const topAccounts = [...withBalance]
    .sort((a, b) => Math.abs(Number(b.current_balance ?? b.balance) || 0)
      - Math.abs(Number(a.current_balance ?? a.balance) || 0))
    .slice(0, 6);

  const top3Categories = categories.slice(0, 3);
  const top3Total = top3Categories.reduce((s, c) => s + (Number(c.value) || 0), 0);
  const top3Pct = totalSpend > 0 ? Math.min(100, Math.round((top3Total / totalSpend) * 100)) : 0;
  const top3Names = top3Categories.map((c) => c.label).join(', ') || 'Top expenditure categories';

  const transferPairs = transfers.pairs?.length ?? 0;
  const uncategorised = Number(analysis.uncategorized?.count) || 0;

  const groupEssentials = analysis.by_group?.Essentials;
  const groupLifestyle = analysis.by_group?.Lifestyle;
  const groupDebt = analysis.by_group?.Debt;
  const groupWealth = analysis.by_group?.Wealth ?? totals.invested;

  const flowNeeds = groupEssentials != null ? groupEssentials : (totalSpend * 0.5);
  const flowWants = groupLifestyle != null ? groupLifestyle : (totalSpend * 0.25);
  const flowDebt = groupDebt != null ? groupDebt : (totalSpend * 0.15);
  const flowSavings = groupWealth != null ? groupWealth : Math.max(0, totalIncome - totalSpend);
  /* Whatever the grouping rules could not place still left the account; the
     chart has to show it or its percentages describe a smaller total than
     the outflow headline above it. */
  /* The residual, not `by_group.Other` directly: the group map also carries a
     negative Income entry for refunds netted off spending, so summing the
     groups lands ~50k above the outflow headline. Taking the difference from
     `totals.spend` makes the pillars tie out to the rupee. */
  const flowOther = Math.max(
    0, totalSpend - (flowNeeds + flowWants + flowDebt + flowSavings),
  );

  return (
    <div className="flex-col gap-6 animate-fade-in">
      {/* 1. Executive Narrative Banner */}
      {narrative?.headline && (
        <GlassCard pad style={{ borderLeft: '4px solid var(--accent)' }}>
          <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
            <div className="flex items-center gap-2">
              <span className="badge badge-accent">AI Executive Brief</span>
              {scoped && (
                <span className="text-3 text-xs">Describes whole ledger</span>
              )}
            </div>
            <Button
              size="sm"
              variant="ghost"
              icon="sparkles"
              onClick={() => openCopilot('overview', 'Expand on this financial summary and suggest optimizations')}
            >
              Ask Copilot
            </Button>
          </div>
          <h2 style={{ fontSize: 20, fontWeight: 800, marginBottom: 8 }}>{narrative.headline}</h2>
          <p style={{ color: 'var(--text-2)', fontSize: 14, lineHeight: 1.6 }}>{narrative.summary}</p>
        </GlassCard>
      )}

      {/* 2. Top-Level Metric Stats Grid */}
      <div className="stats-grid">
        <Stat
          label="Total Inflow (Income)"
          value={totalIncome}
          tone="pos"
          icon="trending"
          sub={`${compact(avgMonthlyIncome)} / month average`}
          onDrill={() => drill({
            title: 'Money In',
            subtitle: 'Reconciled income, salaries, and dividends.',
            params: { flow_role: 'income' },
          })}
        />
        <Stat
          label="Total Outflow (Spending)"
          value={totalSpend}
          tone="neg"
          icon="wallet"
          sub={`${compact(avgMonthlySpend)} / month average`}
          onDrill={() => drill({
            title: 'Money Out',
            subtitle: 'Real net spending excluding transfer legs and refunds.',
            params: { flow_role: 'expense' },
          })}
        />
        <Stat
          label="Net Cashflow"
          value={netCashflow}
          tone={netCashflow >= 0 ? 'pos' : 'neg'}
          icon="activity"
          sub={`${pct(savingsRate)} savings rate`}
          onDrill={() => drill({
            title: 'All Cashflow Rows',
            subtitle: 'Transactions determining overall net cashflow.',
            params: {},
          })}
        />
        <Stat
          label="Net Worth"
          value={netWorthVal}
          tone="brand"
          icon="scales"
          sub={position.as_of ? `As of ${dateLabel(position.as_of)}` : netWorth.as_of ? `As of ${dateLabel(netWorth.as_of)}` : 'Current ledger position'}
        />
      </div>

      {/* 3. Visual Financial Vitality: Health Score & Money Stream Routing */}
      <div className="grid-3" style={{ gap: 20 }}>
        {/* Financial Health Score Gauge */}
        <Card pad title="Financial Health Vitality" sub="Composite score across savings, debt & runway">
          <HealthScoreGauge
            score={healthScore}
            grade={healthGrade}
            note={`${pct(savingsRate)} savings rate · ${runwayMonths.toFixed(1)} mo runway`}
          />
          <div className="flex items-center justify-center gap-3" style={{ marginTop: 16 }}>
            <span className={`badge ${savingsRate > 20 ? 'badge-pos' : savingsRate > 0 ? 'badge-warn' : 'badge-neg'}`}>
              Savings: {savingsRate > 20 ? 'Strong' : savingsRate > 0 ? 'Moderate' : 'Negative'}
            </span>
            <span className={`badge ${runwayMonths >= 6 ? 'badge-pos' : runwayMonths >= 3 ? 'badge-warn' : 'badge-neg'}`}>
              Runway: {runwayMonths >= 6 ? 'Safe' : runwayMonths >= 3 ? 'Watch' : 'Thin'}
            </span>
          </div>
        </Card>

        {/* Liquidity Runway Dial */}
        <Card pad title="Emergency Buffer" sub="Calculated from real monthly burn">
          <RunwayDial
            months={runwayMonths}
            burnMonthly={monthlyBurn}
            liquidTotal={liquidCash}
          />
          <div style={{ fontSize: 12.5, color: 'var(--text-3)', marginTop: 12, lineHeight: 1.5 }}>
            Spendable bank and wallet balances only — locked retirement and demat
            holdings are excluded, since they cannot cover next month&rsquo;s bills.
          </div>
        </Card>

        {/* Smart Triage & Anomaly Radar */}
        <Card pad title="Smart Anomaly Radar" sub="Detected patterns requiring awareness">
          <div className="flex-col gap-3">
            {transferPairs > 0 && (
              <div className="flex items-start gap-2 text-xs">
                <span className="beacon-live" style={{ marginTop: 4 }} />
                <div>
                  <strong>{money(transfers.double_count_avoided)} double-counting prevented</strong>
                  <div className="text-3">
                    {count(transferPairs)} transfer pair{transferPairs === 1 ? '' : 's'} matched across accounts
                  </div>
                </div>
              </div>
            )}
            {uncategorised > 0 && (
              <div className="flex items-start gap-2 text-xs">
                <span className="beacon-neg" style={{ marginTop: 4 }} />
                <div>
                  <strong>{count(uncategorised)} rows still uncategorised</strong>
                  <div className="text-3">
                    <Link to="/review">Triage them</Link> so category totals tie out.
                  </div>
                </div>
              </div>
            )}
            <div className="flex items-start gap-2 text-xs">
              <span className="beacon-warn" style={{ marginTop: 4 }} />
              <div>
                <strong>Top {top3Categories.length || 3} categories take {pct(top3Pct)} of spend</strong>
                <div className="text-3">{top3Names} represent majority outflow</div>
              </div>
            </div>
            <div style={{ marginTop: 8 }}>
              <Button
                size="sm"
                variant="ghost"
                icon="sparkles"
                onClick={() => openCopilot('overview', 'Audit all transactions for unusual price spikes')}
                style={{ width: '100%', justifyContent: 'center' }}
              >
                Run AI Anomaly Audit
              </Button>
            </div>
          </div>
        </Card>
      </div>

      {/* 4. Cashflow Money Stream (Sankey-style flow) */}
      <Card pad title="Cashflow Stream Routing" sub="How incoming income distributes across expenditure pillars">
        <MoneyStreamFlow
          income={totalIncome}
          needs={flowNeeds}
          wants={flowWants}
          debt={flowDebt}
          savings={flowSavings}
          other={flowOther}
          height={230}
        />
      </Card>

      {/* 5. Monthly Cashflow Trend Combo Chart */}
      <Card
        pad
        title="Cashflow Velocity"
        sub="Monthly Inflow vs Outflow with Net Savings trend line"
        tools={
          <Legend items={[
            { label: 'Income', color: 'var(--c2)' },
            { label: 'Spending', color: 'var(--c7)' },
            { label: 'Net Savings', color: 'var(--accent)' },
          ]} />
        }
      >
        <ComboChart
          data={monthly}
          height={280}
          xKey="label"
          bars={[
            { key: 'income', label: 'Income', color: 'var(--c2)' },
            { key: 'spend', label: 'Spending', color: 'var(--c7)' },
          ]}
          lines={[
            { key: 'savings', label: 'Net Savings', color: 'var(--accent)' },
          ]}
        />
      </Card>

      {/* 6. Spending Breakdown by Category */}
      <div className="grid-2">
        <Card pad title="Top Spending Categories" sub="Ranked by total expenditure in this period">
          <BarList items={categories.slice(0, 7)} />
          <div style={{ marginTop: 16, textAlign: 'right' }}>
            <Link to="/spending" className="btn btn-ghost btn-sm">
              View full spending breakdown →
            </Link>
          </div>
        </Card>

        <Card
          pad
          title="Account Holdings & Balances"
          sub={topAccounts.length
            ? `${count(withBalance.length)} of ${count(accounts.length)} accounts carry a verified balance`
            : 'Balances verified from printed statements'}
        >
          <div className="flex-col gap-3">
            {topAccounts.map((acc) => (
              <div key={acc.id} className="flex items-center justify-between py-2" style={{ borderBottom: '1px solid var(--line)' }}>
                <div className="flex items-center gap-2.5" style={{ minWidth: 0 }}>
                  <Icon name={ACCOUNT_ICON[acc.account_type] || 'wallet'} size={16} />
                  <div style={{ minWidth: 0 }}>
                    <div className="truncate" style={{ fontSize: 13.5, fontWeight: 600, maxWidth: 240 }}>
                      {acc.display_name}
                    </div>
                    <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>{titleCase(acc.account_type)}</div>
                  </div>
                </div>
                <div className="text-right">
                  <div
                    className={`tabular-nums font-bold ${acc.is_liability ? 'neg' : ''}`}
                    style={{ fontSize: 14 }}
                  >
                    {money(acc.current_balance ?? acc.balance)}
                  </div>
                  {acc.balance_as_of && (
                    <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
                      as of {dateLabel(acc.balance_as_of)}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {!topAccounts.length && (
              <div className="tiny muted" style={{ padding: '12px 0' }}>
                No account carries a statement-verified balance yet.
              </div>
            )}
          </div>
          <div style={{ marginTop: 16, textAlign: 'right' }}>
            <Link to="/position" className="btn btn-ghost btn-sm">
              View net worth statement →
            </Link>
          </div>
        </Card>
      </div>
    </div>
  );
}
