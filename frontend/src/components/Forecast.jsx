import React from 'react';
import {
  Area, CartesianGrid, ComposedChart, Line, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { compact, money, monthLabel, pct } from '../lib';
import { Callout, Card, ChartTooltip, Chip, Empty, Stat, axisProps, moneyAxis } from './ui';

const CONFIDENCE_TONE = { high: 'pos', medium: 'warn', low: 'neg' };

export default function Forecast({ data }) {
  const forecast = data.forecast || {};
  const recurring = data.recurring || [];
  const months = forecast.months || [];

  if (!months.length) {
    return (
      <Empty title="Not enough history to forecast">
        {forecast.warnings?.[0] ||
          'At least two complete months of statements are needed to project forward.'}
      </Empty>
    );
  }

  // Recharts renders a band by stacking a transparent base under a visible
  // range, so the low value is the base and the delta is the ribbon.
  const chart = months.map((m) => ({
    label: monthLabel(m.month),
    base: m.closing_low,
    band: Math.max(0, m.closing_high - m.closing_low),
    expected: m.closing_expected,
  }));

  const outflows = recurring.filter((r) => r.direction === 'debit' && r.is_active);
  const inflows = recurring.filter((r) => r.direction === 'credit' && r.is_active);
  const first = months[0];

  return (
    <>
      <div className="section-title">Projection</div>
      <div className="grid cols-4">
        <Stat
          label="Committed income"
          value={first.committed_income}
          note="Recurring, per month"
        />
        <Stat
          label="Committed outflow"
          value={first.committed_outflow}
          tone="neg"
          note={`${pct(forecast.commitment_ratio_pct, 0)} of committed income`}
        />
        <Stat
          label="Expected discretionary"
          value={first.discretionary_expected}
          note={`Range ${compact(first.discretionary_low)}–${compact(first.discretionary_high)}`}
        />
        <Stat
          label="Cash runway"
          value={forecast.runway_months != null ? `${forecast.runway_months} months` : '—'}
          tone={forecast.runway_months < 3 ? 'neg' : 'pos'}
          note="If all income stopped today"
        />
      </div>

      {forecast.first_shortfall_month && (
        <Callout tone="neg" style={{ marginTop: 12 }}>
          <strong>Projected shortfall.</strong> On current patterns the tracked cash
          balance goes negative in {monthLabel(forecast.first_shortfall_month)}. This
          assumes no change to income or spending, and excludes any investments you
          could draw on.
        </Callout>
      )}

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Card
          title="Projected cash balance"
          sub={(
            <Chip tone={CONFIDENCE_TONE[forecast.confidence] || ''}>
              {forecast.confidence} confidence
            </Chip>
          )}
        >
          <ResponsiveContainer width="100%" height={280}>
            <ComposedChart data={chart} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--border)" vertical={false} />
              <XAxis dataKey="label" {...axisProps} />
              <YAxis {...moneyAxis} />
              <Tooltip
                content={<ChartTooltip />}
                cursor={{ stroke: 'var(--border-strong)' }}
              />
              <ReferenceLine y={0} stroke="var(--negative)" strokeDasharray="3 3" />
              <Area dataKey="base" stackId="band" stroke="none" fill="transparent" name="_" />
              <Area
                dataKey="band" stackId="band" stroke="none"
                fill="var(--c1)" fillOpacity={0.16} name="Range"
              />
              <Line
                dataKey="expected" name="Expected"
                stroke="var(--c1)" strokeWidth={2.4} dot={{ r: 3 }}
              />
            </ComposedChart>
          </ResponsiveContainer>
          <div className="legend">
            <span className="legend-item"><i className="dot" style={{ background: 'var(--c1)' }} />Expected balance</span>
            <span className="legend-item">
              <i style={{ width: 14, height: 8, borderRadius: 2, background: 'var(--c1)', opacity: .25 }} />
              Range if spending runs low or high
            </span>
          </div>
        </Card>

        <Card title="Month by month">
          <div className="table-wrap scroll-y">
            <table>
              <thead>
                <tr>
                  <th>Month</th>
                  <th className="right">In</th>
                  <th className="right">Committed out</th>
                  <th className="right">Discretionary</th>
                  <th className="right">Net</th>
                  <th className="right">Balance</th>
                </tr>
              </thead>
              <tbody>
                {months.map((m) => (
                  <tr key={m.month}>
                    <td className="nowrap">{monthLabel(m.month)}</td>
                    <td className="right num nowrap">{compact(m.committed_income)}</td>
                    <td className="right num nowrap">{compact(m.committed_outflow)}</td>
                    <td className="right num nowrap">{compact(m.discretionary_expected)}</td>
                    <td className="right num nowrap" style={{
                      color: m.net_expected < 0 ? 'var(--negative)' : 'var(--positive)',
                      fontWeight: 550,
                    }}>
                      {compact(m.net_expected)}
                    </td>
                    <td className="right num nowrap" style={{
                      color: m.closing_expected < 0 ? 'var(--negative)' : 'inherit',
                      fontWeight: 550,
                    }}>
                      {compact(m.closing_expected)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <div className="section-title">Recurring commitments</div>
      <div className="grid cols-2">
        <Card title="Money leaving on a schedule" sub={`${outflows.length} detected`}>
          <div className="table-wrap scroll-y">
            <table>
              <thead>
                <tr>
                  <th>What</th><th>Every</th>
                  <th className="right">Amount</th><th className="right">Per month</th>
                </tr>
              </thead>
              <tbody>
                {outflows.map((r) => {
                  // The card bill is a real commitment but is deliberately kept
                  // out of the projection total, because the purchases it
                  // settles are already counted as discretionary spending.
                  const excluded = r.category === 'cc_payment';
                  return (
                    <tr key={r.id} style={excluded ? { opacity: 0.62 } : undefined}>
                      <td>
                        <div className="truncate" style={{ maxWidth: 230 }}>{r.label}</div>
                        <Chip>{r.category.replace(/_/g, ' ')}</Chip>
                        {excluded && <Chip tone="warn">not double-counted</Chip>}
                      </td>
                      <td className="nowrap">{r.cadence}</td>
                      <td className="right num nowrap">{money(r.amount)}</td>
                      <td className="right num nowrap">{money(r.monthly_equivalent)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {outflows.some((r) => r.category === 'cc_payment') && (
            <Callout style={{ marginTop: 10 }}>
              Your credit card bill is listed but excluded from the committed
              outflow above. The purchases it pays for are already counted as
              discretionary spending, so including the bill as well would charge
              you for the same money twice.
            </Callout>
          )}
        </Card>

        <Card title="Money arriving on a schedule" sub={`${inflows.length} detected`}>
          <div className="table-wrap scroll-y">
            <table>
              <thead>
                <tr>
                  <th>What</th><th>Every</th>
                  <th className="right">Amount</th><th className="right">Next expected</th>
                </tr>
              </thead>
              <tbody>
                {inflows.map((r) => (
                  <tr key={r.id}>
                    <td><div className="truncate" style={{ maxWidth: 230 }}>{r.label}</div></td>
                    <td className="nowrap">{r.cadence}</td>
                    <td className="right num nowrap">{money(r.amount)}</td>
                    <td className="right nowrap">{r.next_expected || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <div className="section-title">What this projection assumes</div>
      <Card>
        {(forecast.assumptions || []).map((a, i) => <Callout key={i}>{a}</Callout>)}
        {data.narrative?.forecast_note && (
          <div className="prose" style={{ marginTop: 12 }}>
            <p>{data.narrative.forecast_note}</p>
          </div>
        )}
      </Card>
    </>
  );
}
