/* Debt: what is owed, at what rate, and paid off by when.
 *
 * The amortization is arithmetic, not a projection - a loan's schedule follows
 * from its balance, its rate and its instalment, and nothing about it is
 * inferred. That is why this screen states a payoff date plainly where the
 * Forecast screen refuses to state a balance without a range.
 */

import React from 'react';
import { useViewData } from '../core/ledger';
import { usePrefs } from '../core/prefs';
import { dateLabel, money, pct } from '../core/format';
import { Callout, Card, Chip, Empty, Legend, Section, Skeleton, SkeletonStats, Stat } from '../ui';
import { LineChart, StackBar, useChartKeyframes } from '../ui/charts';

export default function Debt({ onImport }) {
  useChartKeyframes();
  const { data, loading } = useViewData();
  const [prefs] = usePrefs();

  if (loading) {
    return <><SkeletonStats /><div className="card"><div className="card-body">
      <Skeleton lines={8} /></div></div></>;
  }

  const loans = data?.loans || [];
  const accounts = data?.accounts || [];
  const cards = accounts.filter((a) => a.account_type === 'credit_card');

  if (!loans.length && !cards.length) {
    return (
      <Empty title="No debt accounts found" icon="credit"
        action={onImport && (
          <button className="btn primary" onClick={onImport}>Import statements</button>
        )}>
        Import a loan or credit card statement to see amortization, payoff dates and
        total interest.
      </Empty>
    );
  }

  const outstanding = loans.reduce((s, l) => s + (l.outstanding || 0), 0);
  const interest = loans.reduce((s, l) => s + (l.total_interest_remaining || 0), 0);
  const emi = loans.reduce((s, l) => s + (l.emi || 0), 0);
  const cardDues = cards.reduce((s, c) => s + (c.principal_outstanding || 0), 0);

  return (
    <>
      <Section title="Debt position" />
      <div className="grid cols-4">
        <Stat label="Loan principal outstanding" value={outstanding} tone="neg" />
        <Stat label="Interest still to pay" value={interest} tone="neg"
          note="At current EMIs, no prepayment" />
        <Stat label="Monthly EMI commitment" value={emi} />
        <Stat label="Credit card dues" value={cardDues} tone={cardDues > 0 ? 'neg' : 'pos'} />
      </div>

      {loans.map((loan) => (
        <React.Fragment key={loan.account_id}>
          <Section title={loan.label} />
          <div className="split">
            <Card title="Terms">
              <dl className="kv">
                <dt>Outstanding principal</dt><dd>{money(loan.outstanding)}</dd>
                <dt>Interest rate</dt><dd>{loan.annual_rate}% p.a.</dd>
                <dt>EMI</dt><dd>{money(loan.emi)}</dd>
                <dt>Remaining term</dt>
                <dd>{loan.months_remaining} months ({loan.years_remaining} yrs)</dd>
                <dt>Projected payoff</dt><dd>{dateLabel(loan.payoff_date)}</dd>
                <dt>Total interest remaining</dt><dd>{money(loan.total_interest_remaining)}</dd>
                <dt>Total still payable</dt><dd>{money(loan.total_payable_remaining)}</dd>
              </dl>

              <div style={{ marginTop: 14 }}>
                <div className="tiny dim" style={{ marginBottom: 5 }}>
                  What your next EMI is made of
                </div>
                <StackBar
                  height={14}
                  total={loan.emi}
                  segments={[
                    {
                      label: 'Interest',
                      value: loan.emi * (loan.next_interest_share_pct / 100),
                      color: 'var(--c7)',
                    },
                    {
                      label: 'Principal',
                      value: loan.emi * (1 - loan.next_interest_share_pct / 100),
                      color: 'var(--c2)',
                    },
                  ]}
                />
                <Legend items={[
                  { label: 'Interest', color: 'var(--c7)', value: pct(loan.next_interest_share_pct, 0) },
                  {
                    label: 'Reduces what you owe',
                    color: 'var(--c2)',
                    value: money(loan.emi * (1 - loan.next_interest_share_pct / 100)),
                  },
                ]} />
              </div>

              {loan.next_interest_share_pct > 60 && (
                <Callout tone="warn" style={{ marginTop: 12 }}>
                  Early in a long loan, most of each payment services interest rather
                  than principal. That is arithmetic, not a fault — but it is why a
                  prepayment made now buys far more than the same amount later.
                </Callout>
              )}
              {loan.warnings?.map((w, i) => (
                <Callout tone="warn" key={i} style={{ marginTop: 10 }}>{w}</Callout>
              ))}
            </Card>

            <Card title="Balance over the remaining term" sub="Sampled yearly">
              <LineChart
                data={(loan.schedule || []).map((p) => ({ ...p, label: String(p.date).slice(0, 4) }))}
                height={250}
                area
                animate={prefs.animate}
                series={[{ key: 'closing', name: 'Balance owed', color: 'var(--c7)' }]}
              />
            </Card>
          </div>

          {loan.schedule?.length > 1 && (
            <Card title="Interest against principal, year by year">
              <LineChart
                data={loan.schedule.map((p) => ({ ...p, label: String(p.date).slice(0, 4) }))}
                height={230}
                animate={prefs.animate}
                series={[
                  { key: 'interest', name: 'Interest portion', color: 'var(--c7)' },
                  { key: 'principal', name: 'Principal portion', color: 'var(--c2)' },
                ]}
              />
              <Legend items={[
                { label: 'Interest portion', color: 'var(--c7)' },
                { label: 'Principal portion', color: 'var(--c2)' },
              ]} />
            </Card>
          )}
        </React.Fragment>
      ))}

      {cards.length > 0 && (
        <>
          <Section title="Credit cards"
            note="bureaus generally treat sustained utilisation above 30% unfavourably" />
          <div className="grid cols-3">
            {cards.map((c) => {
              const used = c.credit_limit ? c.principal_outstanding / c.credit_limit : null;
              return (
                <Card key={c.id} title={c.display_name}>
                  <div className="row" style={{ justifyContent: 'space-between' }}>
                    <div>
                      <div className="stat-label">Outstanding</div>
                      <div className="stat-value sm">{money(c.principal_outstanding)}</div>
                    </div>
                    <div className="right">
                      <div className="stat-label">Credit limit</div>
                      <div className="stat-value sm">
                        {c.credit_limit ? money(c.credit_limit) : '—'}
                      </div>
                    </div>
                  </div>
                  {used != null && (
                    <div style={{ marginTop: 12 }}>
                      <StackBar
                        height={10}
                        total={c.credit_limit}
                        segments={[
                          {
                            label: 'Used',
                            value: c.principal_outstanding,
                            color: used > 0.3 ? 'var(--warn)' : 'var(--pos)',
                          },
                          {
                            label: 'Available',
                            value: Math.max(0, c.credit_limit - c.principal_outstanding),
                            color: 'var(--surface-3)',
                          },
                        ]}
                      />
                      <div className="row tight" style={{ marginTop: 8 }}>
                        <Chip tone={used > 0.3 ? 'warn' : 'pos'}>
                          {pct(used * 100, 0)} utilisation
                        </Chip>
                      </div>
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        </>
      )}
    </>
  );
}
