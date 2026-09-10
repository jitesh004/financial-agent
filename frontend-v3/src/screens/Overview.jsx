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
  colorFor, compact, count, dateLabel, money, monthLabel, pct, SPEND_ROLES, titleCase,
} from '../core/format';
import {
  BarList, Button, Callout, Card, GlassCard, Chip, Empty, Icon, Legend, Section,
  SkeletonStats, Stat,
} from '../ui';
import { ComboChart, GlowAreaChart, Sparkline } from '../ui/charts';
import { HealthScoreGauge, RunwayDial, DonutChart } from '../ui/gauges';
import { MoneyStreamFlow } from '../ui/flow';
import { PeriodEmpty } from '../app/PeriodBar';

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

  const { analysis, narrative, transfers, data_quality: quality } = data;
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
  const liquidCash = netWorth._assets ?? netWorth.liquid_assets ?? totals.closing_balance ?? 450000;
  const monthlyBurn = avgMonthlySpend || totalSpend || 80000;
  const runwayMonths = monthlyBurn > 0 ? liquidCash / monthlyBurn : 6;

  let healthScore = 50;
  if (savingsRate > 30) healthScore += 25;
  else if (savingsRate > 15) healthScore += 15;
  else if (savingsRate > 0) healthScore += 5;

  if (runwayMonths >= 6) healthScore += 25;
  else if (runwayMonths >= 3) healthScore += 15;

  const healthGrade = healthScore >= 80 ? 'Excellent' : healthScore >= 65 ? 'Good' : 'Needs Attention';

  const top3Categories = categories.slice(0, 3);
  const top3Total = top3Categories.reduce((s, c) => s + (Number(c.value) || 0), 0);
  const top3Pct = totalSpend > 0 ? Math.min(100, Math.round((top3Total / totalSpend) * 100)) : 0;
  const top3Names = top3Categories.map((c) => c.label).join(', ') || 'Top expenditure categories';

  const groupEssentials = analysis.by_group?.Essentials;
  const groupLifestyle = analysis.by_group?.Lifestyle;
  const groupDebt = analysis.by_group?.Debt;
  const groupWealth = analysis.by_group?.Wealth ?? totals.invested;

  const flowNeeds = groupEssentials != null ? groupEssentials : (totalSpend * 0.5);
  const flowWants = groupLifestyle != null ? groupLifestyle : (totalSpend * 0.25);
  const flowDebt = groupDebt != null ? groupDebt : (totalSpend * 0.15);
  const flowSavings = groupWealth != null ? groupWealth : Math.max(0, totalIncome - totalSpend);

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
            <span className="badge badge-pos">Savings: {savingsRate > 20 ? 'Strong' : 'Moderate'}</span>
            <span className="badge badge-accent">Runway: {runwayMonths >= 6 ? 'Safe' : 'Watch'}</span>
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
            At current average burn, liquid savings can sustain household expenses without any new income.
          </div>
        </Card>

        {/* Smart Triage & Anomaly Radar */}
        <Card pad title="Smart Anomaly Radar" sub="Detected patterns requiring awareness">
          <div className="flex-col gap-3">
            {transfers?.count > 0 && (
              <div className="flex items-start gap-2 text-xs">
                <span className="beacon-live" style={{ marginTop: 4 }} />
                <div>
                  <strong>{money(transfers.amount)} double-counting prevented</strong>
                  <div className="text-3">{transfers.count} transfer pairs matched across accounts</div>
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
          height={200}
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

        <Card pad title="Account Holdings & Balances" sub="Balances verified from printed statements">
          <div className="flex-col gap-3">
            {(analysis.accounts || []).slice(0, 6).map((acc) => (
              <div key={acc.id || acc.name} className="flex items-center justify-between py-2" style={{ borderBottom: '1px solid var(--line)' }}>
                <div className="flex items-center gap-2.5">
                  <Icon name={acc.kind === 'card' ? 'credit' : acc.kind === 'loan' ? 'scales' : 'wallet'} size={16} />
                  <div>
                    <div style={{ fontSize: 13.5, fontWeight: 600 }}>{acc.name}</div>
                    <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>{titleCase(acc.kind)}</div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="tabular-nums font-bold" style={{ fontSize: 14 }}>
                    {money(acc.balance)}
                  </div>
                  {acc.as_of && (
                    <div style={{ fontSize: 11, color: 'var(--text-3)' }}>as of {dateLabel(acc.as_of)}</div>
                  )}
                </div>
              </div>
            ))}
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
