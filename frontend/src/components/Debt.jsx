import React from 'react';
import {
  Area, AreaChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { compact, dateLabel, money, monthLabel, pct } from '../lib';
import { Callout, Card, ChartTooltip, Chip, Empty, Stat, axisProps, moneyAxis } from './ui';

export default function Debt({ data }) {
  const loans = data.loans || [];
  const accounts = data.accounts || [];
  const cards = accounts.filter((a) => a.account_type === 'credit_card');

  if (!loans.length && !cards.length) {
    return (
      <Empty title="No debt accounts found">
        Upload a loan or credit card statement to see amortization, payoff dates
        and total interest.
      </Empty>
    );
  }

  const totalOutstanding = loans.reduce((s, l) => s + (l.outstanding || 0), 0);
  const totalInterest = loans.reduce((s, l) => s + (l.total_interest_remaining || 0), 0);
  const totalEmi = loans.reduce((s, l) => s + (l.emi || 0), 0);
  const cardDues = cards.reduce((s, c) => s + (c.principal_outstanding || 0), 0);

  return (
    <>
      <div className="section-title">Debt position</div>
      <div className="grid cols-4">
        <Stat label="Loan principal outstanding" value={totalOutstanding} tone="neg" />
        <Stat
          label="Interest still to pay"
          value={totalInterest}
          tone="neg"
          note="At current EMIs, no prepayment"
        />
        <Stat label="Monthly EMI commitment" value={totalEmi} />
        <Stat label="Credit card dues" value={cardDues} tone={cardDues > 0 ? 'neg' : 'pos'} />
      </div>

      {loans.map((loan) => (
        <React.Fragment key={loan.account_id}>
          <div className="section-title">{loan.label}</div>
          <div className="grid cols-2">
            <Card title="Terms">
              <div className="table-wrap">
                <table>
                  <tbody>
                    <Row label="Outstanding principal" value={money(loan.outstanding)} />
                    <Row label="Interest rate" value={`${loan.annual_rate}% p.a.`} />
                    <Row label="EMI" value={money(loan.emi)} />
                    <Row
                      label="Remaining term"
                      value={`${loan.months_remaining} months (${loan.years_remaining} yrs)`}
                    />
                    <Row label="Projected payoff" value={dateLabel(loan.payoff_date)} />
                    <Row
                      label="Total interest remaining"
                      value={money(loan.total_interest_remaining)}
                    />
                    <Row
                      label="Total still payable"
                      value={money(loan.total_payable_remaining)}
                    />
                  </tbody>
                </table>
              </div>

              <Callout tone={loan.next_interest_share_pct > 60 ? 'warn' : ''}>
                <strong>{pct(loan.next_interest_share_pct, 0)}</strong> of your next EMI
                is interest — only {money(loan.emi * (1 - loan.next_interest_share_pct / 100))} of
                it reduces what you owe.
                {loan.next_interest_share_pct > 60 && (
                  ' Early in a long loan, most of each payment services interest rather than principal.'
                )}
              </Callout>

              {loan.warnings?.map((w, i) => <Callout tone="warn" key={i}>{w}</Callout>)}
            </Card>

            <Card title="Balance over the remaining term" sub="Sampled yearly">
              <ResponsiveContainer width="100%" height={250}>
                <AreaChart data={loan.schedule} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id={`g-${loan.account_id}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--c7)" stopOpacity={0.32} />
                      <stop offset="100%" stopColor="var(--c7)" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--border)" vertical={false} />
                  <XAxis
                    dataKey="date" {...axisProps}
                    tickFormatter={(d) => String(d).slice(0, 4)}
                  />
                  <YAxis {...moneyAxis} />
                  <Tooltip
                    content={<ChartTooltip />}
                    labelFormatter={(d) => dateLabel(d)}
                  />
                  <Area
                    dataKey="closing" name="Balance owed"
                    stroke="var(--c7)" strokeWidth={2}
                    fill={`url(#g-${loan.account_id})`}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </Card>
          </div>

          {loan.schedule?.length > 1 && (
            <Card title="Interest vs principal, year by year" style={{ marginTop: 14 }}>
              <ResponsiveContainer width="100%" height={230}>
                <LineChart data={loan.schedule} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="date" {...axisProps} tickFormatter={(d) => String(d).slice(0, 4)} />
                  <YAxis {...moneyAxis} />
                  <Tooltip content={<ChartTooltip />} labelFormatter={(d) => dateLabel(d)} />
                  <Line dataKey="interest" name="Interest" stroke="var(--c7)" strokeWidth={2} dot={false} />
                  <Line dataKey="principal" name="Principal" stroke="var(--c2)" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
              <div className="legend">
                <span className="legend-item"><i className="dot" style={{ background: 'var(--c7)' }} />Interest portion</span>
                <span className="legend-item"><i className="dot" style={{ background: 'var(--c2)' }} />Principal portion</span>
              </div>
            </Card>
          )}
        </React.Fragment>
      ))}

      {cards.length > 0 && (
        <>
          <div className="section-title">Credit cards</div>
          <div className="grid cols-2">
            {cards.map((c) => (
              <Card key={c.id} title={c.display_name}>
                <div className="grid cols-2" style={{ gap: 10 }}>
                  <div className="stat">
                    <div className="stat-label">Outstanding</div>
                    <div className="stat-value num" style={{ fontSize: 20 }}>
                      {money(c.principal_outstanding)}
                    </div>
                  </div>
                  <div className="stat">
                    <div className="stat-label">Credit limit</div>
                    <div className="stat-value num" style={{ fontSize: 20 }}>
                      {c.credit_limit ? money(c.credit_limit) : '—'}
                    </div>
                  </div>
                </div>
                {c.credit_limit > 0 && (
                  <>
                    <div className="bar" style={{ marginTop: 12 }}>
                      <span style={{
                        width: `${Math.min(100, (c.principal_outstanding / c.credit_limit) * 100)}%`,
                        background: (c.principal_outstanding / c.credit_limit) > 0.3
                          ? 'var(--warn)' : 'var(--positive)',
                      }} />
                    </div>
                    <div style={{ marginTop: 8 }}>
                      <Chip tone={(c.principal_outstanding / c.credit_limit) > 0.3 ? 'warn' : 'pos'}>
                        {pct((c.principal_outstanding / c.credit_limit) * 100, 0)} utilisation
                      </Chip>
                      <span style={{ fontSize: 12, color: 'var(--text-3)', marginLeft: 8 }}>
                        Credit scoring models generally treat sustained utilisation above
                        30% unfavourably.
                      </span>
                    </div>
                  </>
                )}
              </Card>
            ))}
          </div>
        </>
      )}
    </>
  );
}

function Row({ label, value }) {
  return (
    <tr>
      <td style={{ color: 'var(--text-2)' }}>{label}</td>
      <td className="right num nowrap" style={{ fontWeight: 550 }}>{value}</td>
    </tr>
  );
}
