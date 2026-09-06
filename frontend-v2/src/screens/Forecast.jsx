/* What happens next.
 *
 * A projection is honest only if it is a range. Every month here carries a
 * low, an expected and a high, and the band is drawn rather than described,
 * because a single line through the middle of a wide range reads as a
 * prediction and this is not one.
 *
 * Nothing on this screen follows the period control: an amortization and a
 * runway both run from today forward, whatever window the rest of the app is
 * showing.
 */

import React from 'react';
import { useViewData } from '../core/ledger';
import { usePrefs } from '../core/prefs';
import { compact, dateLabel, money, monthLabel, pct } from '../core/format';
import {
  Callout, Card, Chip, Empty, Legend, Section, Skeleton, SkeletonStats, Stat, Table,
} from '../ui';
import { LineChart, useChartKeyframes } from '../ui/charts';

const CONFIDENCE = { high: 'pos', medium: 'warn', low: 'neg' };

export default function Forecast() {
  useChartKeyframes();
  const { data, loading } = useViewData();
  const [prefs] = usePrefs();

  if (loading) {
    return <><SkeletonStats /><div className="card"><div className="card-body">
      <Skeleton lines={9} /></div></div></>;
  }

  const forecast = data?.forecast || {};
  const recurring = data?.recurring || [];
  const months = forecast.months || [];

  if (!months.length) {
    return (
      <Empty title="Not enough history to forecast" icon="gauge">
        {forecast.warnings?.[0]
          || 'At least two complete months of statements are needed to project forward.'}
      </Empty>
    );
  }

  const chart = months.map((m) => ({
    label: monthLabel(m.month),
    low: m.closing_low,
    high: m.closing_high,
    expected: m.closing_expected,
  }));

  const outflows = recurring.filter((r) => r.direction === 'debit' && r.is_active);
  const inflows = recurring.filter((r) => r.direction === 'credit' && r.is_active);
  const first = months[0];

  return (
    <>
      <Section title="Projection" note="from today, whatever period the rest of the app shows" />
      <div className="grid cols-4">
        <Stat label="Committed income" value={first.committed_income} tone="pos"
          note="Recurring, per month" />
        <Stat label="Committed outflow" value={first.committed_outflow} tone="neg"
          note={`${pct(forecast.commitment_ratio_pct, 0)} of committed income`} />
        <Stat label="Expected discretionary" value={first.discretionary_expected}
          note={`Range ${compact(first.discretionary_low)}–${compact(first.discretionary_high)}`} />
        <Stat
          label="Cash runway"
          value={forecast.runway_months != null ? `${forecast.runway_months} months` : '—'}
          tone={forecast.runway_months < 3 ? 'neg' : 'pos'}
          note="If all income stopped today"
        />
      </div>

      {forecast.first_shortfall_month && (
        <Callout tone="neg">
          <strong>Projected shortfall.</strong> On current patterns the tracked cash
          balance goes negative in {monthLabel(forecast.first_shortfall_month)}. This
          assumes no change to income or spending, and excludes any investments you
          could draw on.
        </Callout>
      )}

      <div className="split">
        <Card
          title="Projected cash balance"
          tools={<Chip tone={CONFIDENCE[forecast.confidence] || ''}>
            {forecast.confidence} confidence
          </Chip>}
        >
          <LineChart
            data={chart}
            height={300}
            animate={prefs.animate}
            zeroLine
            dots
            band={{ lowKey: 'low', highKey: 'high', color: 'var(--c1)' }}
            series={[{ key: 'expected', name: 'Expected balance', color: 'var(--c1)', width: 2.4 }]}
          />
          <Legend items={[
            { label: 'Expected balance', color: 'var(--c1)' },
            { label: 'Range if spending runs low or high', color: 'var(--accent-border)' },
          ]} />
        </Card>

        <Card title="Month by month">
          <Table scrollY>
            <thead>
              <tr>
                <th>Month</th>
                <th className="right" title="Committed income">In</th>
                <th className="right" title="Committed outflow">Out</th>
                <th className="right" title="Expected discretionary spending">Spend</th>
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
                  <td className={`right num nowrap ${m.net_expected < 0 ? 'neg' : 'pos'}`}>
                    {compact(m.net_expected)}
                  </td>
                  <td className={`right num nowrap ${m.closing_expected < 0 ? 'neg' : ''}`}
                    style={{ fontWeight: 560 }}>
                    {compact(m.closing_expected)}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      </div>

      <Section title="Recurring commitments" />
      <div className="split">
        <Card title="Money leaving on a schedule" sub={`${outflows.length} detected`}>
          <Table scrollY>
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
                      <div className="row tight" style={{ marginTop: 3 }}>
                        <Chip>{r.category.replace(/_/g, ' ')}</Chip>
                        {excluded && <Chip tone="warn">not double-counted</Chip>}
                      </div>
                    </td>
                    <td className="nowrap">{r.cadence}</td>
                    <td className="right num nowrap">{money(r.amount)}</td>
                    <td className="right num nowrap">{money(r.monthly_equivalent)}</td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
          {outflows.some((r) => r.category === 'cc_payment') && (
            <Callout style={{ marginTop: 10 }}>
              Your credit card bill is listed but excluded from the committed outflow
              above. The purchases it pays for are already counted as discretionary
              spending, so including the bill as well would charge you for the same
              money twice.
            </Callout>
          )}
        </Card>

        <Card title="Money arriving on a schedule" sub={`${inflows.length} detected`}>
          <Table scrollY>
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
                  <td className="right nowrap">{dateLabel(r.next_expected)}</td>
                </tr>
              ))}
              {!inflows.length && (
                <tr><td colSpan={4} className="dim small">Nothing recurring on the way in.</td></tr>
              )}
            </tbody>
          </Table>
        </Card>
      </div>

      <Section title="What this projection assumes" />
      <Card pad>
        <div className="col">
          {(forecast.assumptions || []).map((a, i) => <Callout key={i}>{a}</Callout>)}
          {data?.narrative?.forecast_note && (
            <div className="prose"><p>{data.narrative.forecast_note}</p></div>
          )}
        </div>
      </Card>
    </>
  );
}
