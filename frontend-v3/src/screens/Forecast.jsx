import React from 'react';
import { useViewData } from '../core/ledger';
import { usePrefs } from '../core/prefs';
import { compact, dateLabel, money, monthLabel, pct } from '../core/format';
import { Callout, Card, GlassCard, Chip, Empty, Legend, Section, Skeleton, SkeletonStats, Stat, Table, Badge } from '../ui';
import { ConfidenceBandChart } from '../ui/charts';
import { RunwayDial } from '../ui/gauges';

const CONFIDENCE_TONE = { high: 'pos', medium: 'warn', low: 'neg' };

export default function Forecast() {
  const { data, loading } = useViewData();
  const [prefs] = usePrefs();

  if (loading) {
    return (
      <div className="forecast-screen page-enter">
        <SkeletonStats count={4} />
        <div style={{ marginTop: 24 }}>
          <Skeleton lines={9} height={300} />
        </div>
      </div>
    );
  }

  const forecast = data?.forecast || {};
  const recurring = data?.recurring || [];
  const months = forecast.months || [];

  if (!months.length) {
    return (
      <Empty title="Insufficient statement history for runway forecasting" icon="gauge">
        {forecast.warnings?.[0] || 'At least two complete months of statement data are required to extrapolate honest cashflow cones.'}
      </Empty>
    );
  }

  const chartData = months.map((m) => ({
    label: monthLabel(m.month),
    low: m.closing_low,
    high: m.closing_high,
    expected: m.closing_expected,
  }));

  const outflows = recurring.filter((r) => r.direction === 'debit' && r.is_active);
  const inflows = recurring.filter((r) => r.direction === 'credit' && r.is_active);
  const first = months[0];

  return (
    <div className="forecast-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>Cashflow Horizon & Runway</h1>
            <Badge tone={CONFIDENCE_TONE[forecast.confidence] || 'brand'} size="sm">
              {forecast.confidence ? `${forecast.confidence.toUpperCase()} CONFIDENCE` : 'EMPIRICAL CONE'}
            </Badge>
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            Statistical cash projection over the next 90–180 days: confidence cones illustrate low, expected, and high spending variance.
          </p>
        </div>
      </div>

      {/* Top Telemetry Stats */}
      <div className="stats-grid">
        <Stat
          label="Contracted Inflow"
          value={money(first.committed_income)}
          tone="pos"
          sub="Predictable monthly recurring income"
        />
        <Stat
          label="Contracted Outflow"
          value={money(first.committed_outflow)}
          tone="neg"
          sub={`${pct(forecast.commitment_ratio_pct, 0)} of contracted inflow`}
        />
        <Stat
          label="Expected Discretionary"
          value={money(first.discretionary_expected)}
          sub={`Estimated variance: ${compact(first.discretionary_low)} – ${compact(first.discretionary_high)}`}
        />
        <Stat
          label="Estimated Zero-Income Runway"
          value={forecast.runway_months != null ? `${forecast.runway_months} Months` : '—'}
          tone={forecast.runway_months < 3 ? 'neg' : 'pos'}
          sub="If all inflows halted immediately"
        />
      </div>

      {forecast.first_shortfall_month && (
        <Callout tone="neg">
          <strong>Liquidity Shortfall Warning.</strong> At current median velocity, tracked liquid balances are projected
          to go negative in <strong>{monthLabel(forecast.first_shortfall_month)}</strong> without expenditure intervention or external replenishment.
        </Callout>
      )}

      {/* Main Trajectory Projection Chart & Summary Grid */}
      <div className="forecast-main-grid">
        <GlassCard
          glowing
          title="Projected Cash Balance Cone"
          subtitle="Honest range representation (low to high variance boundaries)"
          tools={<Badge tone={CONFIDENCE_TONE[forecast.confidence] || 'brand'}>{forecast.confidence} confidence</Badge>}
        >
          <ConfidenceBandChart
            data={chartData}
            height={280}
            expectedKey="expected"
            lowKey="low"
            highKey="high"
            color="var(--brand-primary)"
          />
        </GlassCard>

        <Card title="Monthly Cash Ledger Progression">
          <div style={{ maxHeight: 320, overflow: 'auto' }}>
            <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th>Month</th>
                  <th style={{ textAlign: 'right' }}>In</th>
                  <th style={{ textAlign: 'right' }}>Out</th>
                  <th style={{ textAlign: 'right' }}>Net</th>
                  <th style={{ textAlign: 'right' }}>Closing</th>
                </tr>
              </thead>
              <tbody>
                {months.map((m) => (
                  <tr key={m.month} className="terminal-row">
                    <td className="nowrap font-medium">{monthLabel(m.month)}</td>
                    <td className="num pos nowrap" style={{ textAlign: 'right' }}>{compact(m.committed_income)}</td>
                    <td className="num neg nowrap" style={{ textAlign: 'right' }}>{compact(m.committed_outflow)}</td>
                    <td className={`num nowrap ${m.net_expected < 0 ? 'neg' : 'pos'}`} style={{ textAlign: 'right' }}>
                      {compact(m.net_expected)}
                    </td>
                    <td className={`num nowrap font-bold ${m.closing_expected < 0 ? 'neg' : ''}`} style={{ textAlign: 'right' }}>
                      {compact(m.closing_expected)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {/* Contractual Inflows & Outflows Tables */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
        <Section
          title="Recurring Cashflow Drivers"
          subtitle="Contractual obligations anchoring the projection model."
        />

        <div className="grid-2">
          <Card title="Scheduled Outflow Commitments" subtitle={`${outflows.length} active outflows detected`}>
            <div style={{ maxHeight: 300, overflow: 'auto' }}>
              <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th>Commitment</th>
                    <th>Cadence</th>
                    <th style={{ textAlign: 'right' }}>Per Month</th>
                  </tr>
                </thead>
                <tbody>
                  {outflows.map((r) => {
                    const excluded = r.category === 'cc_payment';
                    return (
                      <tr key={r.id} className="terminal-row" style={{ opacity: excluded ? 0.5 : 1 }}>
                        <td>
                          <div className="font-semibold truncate" style={{ maxWidth: 180 }}>{r.label}</div>
                          <div style={{ display: 'flex', gap: 4, marginTop: 2 }}>
                            <Chip size="sm">{r.category.replace(/_/g, ' ')}</Chip>
                            {excluded && <Chip tone="warn" size="sm">Settlement</Chip>}
                          </div>
                        </td>
                        <td className="nowrap muted tiny">{r.cadence}</td>
                        <td className="num neg nowrap font-semibold" style={{ textAlign: 'right' }}>
                          {money(r.monthly_equivalent)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {outflows.some((r) => r.category === 'cc_payment') && (
              <Callout tone="info" style={{ marginTop: 'var(--space-3)' }}>
                Credit card bill payments are excluded from committed outflow to prevent double-counting
                the discretionary purchases already settled on the card.
              </Callout>
            )}
          </Card>

          <Card title="Expected Inbound Cashflows" subtitle={`${inflows.length} active inflows detected`}>
            <div style={{ maxHeight: 300, overflow: 'auto' }}>
              <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th>Inflow</th>
                    <th>Cadence</th>
                    <th style={{ textAlign: 'right' }}>Amount</th>
                    <th>Next Expected</th>
                  </tr>
                </thead>
                <tbody>
                  {inflows.map((r) => (
                    <tr key={r.id} className="terminal-row">
                      <td className="font-semibold">{r.label}</td>
                      <td className="nowrap muted tiny">{r.cadence}</td>
                      <td className="num pos nowrap font-semibold" style={{ textAlign: 'right' }}>{money(r.amount)}</td>
                      <td className="nowrap muted tiny">{dateLabel(r.next_expected)}</td>
                    </tr>
                  ))}
                  {!inflows.length && (
                    <tr>
                      <td colSpan={4} className="muted tiny" style={{ padding: 'var(--space-4)', textAlign: 'center' }}>
                        No recurring inbound salaries or retainers detected yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      </div>

      {/* Assumptions & Mathematical Baseline */}
      <Card title="Underlying Model Hypotheses" subtitle="Deterministic ground rules applied in forecasting">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          {(forecast.assumptions || []).map((a, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 8 }} className="small muted">
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--brand-primary)', flexShrink: 0 }} />
              <span>{a}</span>
            </div>
          ))}
          {data?.narrative?.forecast_note && (
            <div style={{ marginTop: 'var(--space-2)', fontStyle: 'italic' }} className="small muted">
              {data.narrative.forecast_note}
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
