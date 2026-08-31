import React from 'react';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, ComposedChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { colorFor, compact, dateLabel, money, monthLabel, pct, titleCase } from '../lib';
import { BarList, Callout, Card, ChartTooltip, Chip, Stat, axisProps, moneyAxis } from './ui';
import Claims from './Claims';
import Forecast from './Forecast';

const SEVERITY_TONE = { urgent: 'neg', watch: 'warn', info: 'accent' };

export default function Overview({ data }) {
  const { analysis, narrative, forecast, transfers, data_quality: quality } = data;
  const totals = analysis.totals || {};
  const period = analysis.period || {};

  const monthly = (analysis.monthly || []).map((m) => ({
    ...m, label: monthLabel(m.month),
  }));

  const categories = (analysis.by_category || []).map((c, i) => ({
    label: c.category, value: c.total, color: colorFor(i), ...c,
  }));

  const netWorth = analysis.net_worth || {};

  return (
    <>
      {/* ---- Narrative ---- */}
      {narrative?.headline && (
        <Card className="grid" style={{ gap: 12 }}>
          <div>
            <h2 className="headline">{narrative.headline}</h2>
            <div className="prose"><p>{narrative.summary}</p></div>
            {narrative.generated_by === 'computed' && (
              <Chip tone="warn">Computed summary — no model narration</Chip>
            )}
          </div>
        </Card>
      )}

      {/* ---- Headline numbers ---- */}
      <div className="section-title">
        {period.start && period.end
          ? `${dateLabel(period.start)} → ${dateLabel(period.end)} · ${period.months_covered} months`
          : 'Summary'}
      </div>
      <div className="grid cols-4">
        <Stat
          label="Money in"
          value={totals.income}
          note={`${compact(totals.average_monthly_income)} average per month`}
        />
        <Stat
          label="Money out"
          value={totals.spend}
          note={`${compact(totals.average_monthly_spend)} average per month`}
        />
        <Stat
          label="Net saved"
          value={totals.net_savings}
          tone={totals.net_savings >= 0 ? 'pos' : 'neg'}
          note={`Savings rate ${pct(totals.savings_rate)}`}
        />
        <Stat
          label="Invested"
          value={totals.invested}
          note={`${totals.transaction_count?.toLocaleString('en-IN')} transactions analyzed`}
        />
      </div>

      {/* ---- Cashflow ---- */}
      <div className="section-title">Cashflow</div>
      <div className="grid cols-2">
        <Card title="Income vs outflow by month" sub="Outflow includes EMIs and SIPs">
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={monthly} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis dataKey="label" {...axisProps} />
              <YAxis {...moneyAxis} />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: 'var(--surface-2)' }} />
              <Bar dataKey="income" name="Income" fill="var(--c2)" radius={[3, 3, 0, 0]} maxBarSize={26} />
              <Bar dataKey="total_outflow" name="Outflow" fill="var(--c7)" radius={[3, 3, 0, 0]} maxBarSize={26} />
              <Line dataKey="net" name="Net" stroke="var(--c1)" strokeWidth={2} dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
          <div className="legend">
            <span className="legend-item"><i className="dot" style={{ background: 'var(--c2)' }} />Income</span>
            <span className="legend-item"><i className="dot" style={{ background: 'var(--c7)' }} />Outflow</span>
            <span className="legend-item"><i className="dot" style={{ background: 'var(--c1)' }} />Net</span>
          </div>
        </Card>

        <Card title="Where the money went" sub={`${categories.length} categories`}>
          <BarList items={categories} total={totals.spend} max={11} />
        </Card>
      </div>

      {/* ---- Narrative findings ---- */}
      {narrative?.key_findings?.length > 0 && (
        <>
          <div className="section-title">What stands out</div>
          <Card>
            {narrative.key_findings.map((f, i) => (
              <div className="finding" key={i}>
                <Chip tone={SEVERITY_TONE[f.severity] || 'accent'}>
                  {f.severity || 'info'}
                </Chip>
                <div className="finding-body">
                  <div className="finding-title">{f.title}</div>
                  <div className="finding-detail">{f.detail}</div>
                </div>
              </div>
            ))}
          </Card>
        </>
      )}

      {narrative?.where_money_went && (
        <>
          <div className="section-title">Following the salary</div>
          <Card><div className="prose"><p>{narrative.where_money_went}</p></div></Card>
        </>
      )}

      {narrative?.observations?.length > 0 && (
        <>
          <div className="section-title">Options worth knowing about</div>
          <Card>
            {narrative.observations.map((o, i) => (
              <div className="finding" key={i}>
                <span className="dot" style={{ background: colorFor(i), marginTop: 6 }} />
                <div className="finding-body">
                  <div className="finding-title">{o.title}</div>
                  <div className="finding-detail">{o.detail}</div>
                  {o.mechanism && <div className="finding-mech">{o.mechanism}</div>}
                </div>
              </div>
            ))}
            <Callout>
              These are mechanical observations from your own numbers, not financial
              advice. Anything involving investing, prepaying or restructuring debt is
              worth discussing with a qualified adviser who can see your full picture.
            </Callout>
          </Card>
        </>
      )}

      {/* ---- Position ---- */}
      <div className="section-title">Position</div>
      <div className="grid cols-3">
        <Stat
          label="Assets tracked"
          value={netWorth._assets}
          note="Cash balances from uploaded statements"
        />
        <Stat
          label="Liabilities"
          value={netWorth._liabilities}
          tone="neg"
          note="Loans and card dues outstanding"
        />
        <Stat
          label="Net position"
          value={netWorth._net}
          tone={netWorth._net >= 0 ? 'pos' : 'neg'}
          note="Assets minus liabilities on tracked accounts"
        />
      </div>

      {/* ---- Data quality ---- */}
      <div className="section-title">Data quality</div>
      <Card>
        <div className="grid cols-4" style={{ gap: 10, marginBottom: 12 }}>
          <QualityTile label="Files reconciled" value={`${quality?.files_reconciled ?? 0}/${quality?.files_processed ?? 0}`} tone={quality?.files_unreconciled ? 'warn' : 'pos'} />
          <QualityTile label="Rules-categorized" value={quality?.rules_settled ?? 0} />
          <QualityTile label="Needs review" value={quality?.uncategorized_count ?? 0} tone={quality?.uncategorized_count ? 'warn' : 'pos'} />
          <QualityTile label="Double-count avoided" value={compact(transfers?.double_count_avoided || 0)} tone="pos" />
        </div>

        {transfers?.notes?.map((n, i) => <Callout tone="pos" key={i}>{n}</Callout>)}
        {(narrative?.caveats || []).map((c, i) => <Callout tone="warn" key={`c${i}`}>{c}</Callout>)}
        {(analysis.notes || []).map((n, i) => <Callout key={`n${i}`}>{n}</Callout>)}
      </Card>

      {/* Forecast and Owed were tabs of their own. Both answer a question about
          the position this page already describes - what happens next, and what
          is coming back - so they read better as the end of that story than as
          two more places to go looking. */}
      <div className="section-title">What happens next</div>
      <Forecast data={data} />

      <div className="section-title">Money owed to you</div>
      <Claims />
    </>
  );
}

function QualityTile({ label, value, tone }) {
  return (
    <div style={{
      padding: '10px 12px', borderRadius: 8,
      background: 'var(--surface-2)', border: '1px solid var(--border)',
    }}>
      <div style={{ fontSize: 11.5, color: 'var(--text-3)', marginBottom: 3 }}>{label}</div>
      <div className={`num ${tone === 'pos' ? 'stat-value pos' : tone === 'warn' ? 'stat-value neg' : ''}`}
        style={{ fontSize: 18, fontWeight: 620 }}>
        {value}
      </div>
    </div>
  );
}
