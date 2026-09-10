import React, { useMemo, useState } from 'react';
import { useViewData } from '../core/ledger';
import { usePrefs } from '../core/prefs';
import { dateLabel, money, pct, compact } from '../core/format';
import { Callout, Card, GlassCard, Chip, Empty, Legend, Section, Skeleton, SkeletonStats, Stat, Button, Badge } from '../ui';
import { GlowAreaChart, ComboChart } from '../ui/charts';
import { Icon } from '../ui/icons';

export default function Debt({ onImport }) {
  const { data, loading } = useViewData();
  const [prefs] = usePrefs();

  // Prepayment Simulator State
  const [extraMonthly, setExtraMonthly] = useState(5000);
  const [lumpSum, setLumpSum] = useState(0);
  const [strategy, setStrategy] = useState('avalanche'); // 'avalanche' | 'snowball'

  const loans = data?.loans || [];
  const accounts = data?.accounts || [];
  const cards = accounts.filter((a) => a.account_type === 'credit_card');

  const totalOutstanding = loans.reduce((sum, l) => sum + (l.outstanding || 0), 0);
  const totalInterest = loans.reduce((sum, l) => sum + (l.total_interest_remaining || 0), 0);
  const totalEmi = loans.reduce((sum, l) => sum + (l.emi || 0), 0);
  const totalCardDues = cards.reduce(
    (sum, c) => sum + (Number(c.principal_outstanding)
      || Math.abs(Number(c.current_balance ?? c.balance) || 0)),
    0,
  );

  // Prepayment Simulation Calculations
  const simulation = useMemo(() => {
    if (!loans.length) return null;

    // Clone loans
    let simulatedLoans = loans.map((l) => ({
      id: l.account_id,
      label: l.label,
      rate: (l.annual_rate || 9.0) / 100 / 12,
      annualRate: l.annual_rate || 9.0,
      balance: Math.max(0, (l.outstanding || 0) - (loans.length === 1 ? lumpSum : 0)),
      emi: l.emi || 0,
    }));

    if (loans.length > 1 && lumpSum > 0) {
      // Allocate lumpsum according to strategy
      let remLump = lumpSum;
      const sorted = [...simulatedLoans].sort((a, b) => 
        strategy === 'avalanche' ? b.annualRate - a.annualRate : a.balance - b.balance
      );
      for (const loan of sorted) {
        if (remLump <= 0) break;
        const target = simulatedLoans.find(x => x.id === loan.id);
        const deduction = Math.min(target.balance, remLump);
        target.balance -= deduction;
        remLump -= deduction;
      }
    }

    let totalSimInterest = 0;
    let monthsElapsed = 0;
    const maxMonths = 360;

    while (simulatedLoans.some(l => l.balance > 10) && monthsElapsed < maxMonths) {
      monthsElapsed++;
      let extraPool = extraMonthly;

      // Sort priority for extra pool this month
      const activeLoans = simulatedLoans.filter(l => l.balance > 10);
      activeLoans.sort((a, b) => 
        strategy === 'avalanche' ? b.annualRate - a.annualRate : a.balance - b.balance
      );

      for (const loan of simulatedLoans) {
        if (loan.balance <= 10) continue;
        const interest = loan.balance * loan.rate;
        totalSimInterest += interest;
        let principalPaid = Math.max(0, loan.emi - interest);

        if (loan.id === activeLoans[0]?.id && extraPool > 0) {
          principalPaid += extraPool;
          extraPool = 0;
        }

        loan.balance = Math.max(0, loan.balance - principalPaid);
      }
    }

    const baselineInterest = totalInterest;
    const interestSaved = Math.max(0, baselineInterest - totalSimInterest);
    const maxOriginalMonths = Math.max(...loans.map(l => l.months_remaining || 0), 1);
    const monthsSaved = Math.max(0, maxOriginalMonths - monthsElapsed);

    return {
      monthsElapsed,
      monthsSaved,
      interestSaved,
      simulatedInterest: totalSimInterest,
    };
  }, [loans, extraMonthly, lumpSum, strategy, totalInterest]);

  if (loading) {
    return (
      <div className="debt-screen page-enter">
        <SkeletonStats count={4} />
        <div style={{ marginTop: 24 }}>
          <Skeleton lines={8} height={260} />
        </div>
      </div>
    );
  }

  if (!loans.length && !cards.length) {
    return (
      <Empty
        title="No debt or credit obligations found"
        icon="credit"
        action={onImport && (
          <Button variant="primary" icon="upload" onClick={onImport}>
            Import Statements
          </Button>
        )}
      >
        Import loan statements or credit card bills to see your verified amortization schedules,
        exact payoff horizons, interest costs, and prepayment optimization.
      </Empty>
    );
  }


  return (
    <div className="debt-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <h1 className="h1">Debt & Liabilities</h1>
          <p className="lead">
            Exact arithmetic amortization across verified loan schedules and active credit card revolvers.
          </p>
        </div>
      </div>

      {/* Metric Stats */}
      <div className="stats-grid">
        <Stat
          label="Loan Principal Outstanding"
          value={money(totalOutstanding)}
          tone="neg"
          sub="Principal balances across all active loans"
        />
        <Stat
          label="Total Interest Remaining"
          value={money(totalInterest)}
          tone="neg"
          sub="At contracted rate without prepayment"
        />
        <Stat
          label="Monthly EMI Commitment"
          value={money(totalEmi)}
          sub="Contracted monthly cash outflow"
        />
        <Stat
          label="Credit Card Dues"
          value={money(totalCardDues)}
          tone={totalCardDues > 0 ? 'warn' : 'pos'}
          sub={totalCardDues > 0 ? 'Unsettled statement balance' : 'Zero dues pending'}
        />
      </div>

      {/* Interactive Snowball / Avalanche Prepayment Simulator */}
      {loans.length > 0 && (
        <GlassCard className="prepayment-lab" glowing style={{ padding: 'var(--space-6)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 'var(--space-4)', marginBottom: 'var(--space-5)' }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                <span style={{ color: 'var(--accent-text)', display: 'inline-flex' }}>
                  <Icon name="sparkles" size={20} />
                </span>
                <h3 className="h3" style={{ margin: 0 }}>Prepayment Accelerator Lab</h3>
              </div>
              <p className="small muted" style={{ margin: '4px 0 0 0' }}>
                Simulate how accelerated payments crush contracted interest and collapse repayment tenure.
              </p>
            </div>

            <div style={{
              display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap', maxWidth: '100%',
              background: 'var(--surface-3)', padding: 4, borderRadius: 'var(--radius-md)',
            }}>
              <button
                className={`btn btn-sm ${strategy === 'avalanche' ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setStrategy('avalanche')}
                title="Pay highest interest rate debt first (Maximizes interest saved)"
              >
                Avalanche (Highest Rate)
              </button>
              <button
                className={`btn btn-sm ${strategy === 'snowball' ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setStrategy('snowball')}
                title="Pay smallest balance debt first (Quick psychological wins)"
              >
                Snowball (Lowest Balance)
              </button>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 'var(--space-5)', alignItems: 'center' }}>
            {/* Sliders */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span className="small font-medium">Extra Monthly Prepayment</span>
                  <span className="num font-semibold" style={{ color: 'var(--accent-text)' }}>
                    +{money(extraMonthly)}/mo
                  </span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="50000"
                  step="1000"
                  value={extraMonthly}
                  onChange={(e) => setExtraMonthly(Number(e.target.value))}
                  style={{ width: '100%', accentColor: 'var(--brand-primary)', cursor: 'pointer' }}
                />
              </div>

              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span className="small font-medium">One-Time Lump Sum Prepayment</span>
                  <span className="num font-semibold" style={{ color: 'var(--accent-teal)' }}>
                    {money(lumpSum)}
                  </span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="500000"
                  step="10000"
                  value={lumpSum}
                  onChange={(e) => setLumpSum(Number(e.target.value))}
                  style={{ width: '100%', accentColor: 'var(--accent-teal)', cursor: 'pointer' }}
                />
              </div>
            </div>

            {/* Simulated Outcomes Display */}
            {simulation && (
              <div
                className="grid-2"
                style={{
                  gap: 'var(--space-3)',
                  padding: 'var(--space-4)',
                  background: 'var(--surface-2)',
                  borderRadius: 'var(--radius-lg)',
                  border: '1px solid var(--border-subtle)',
                }}
              >
                <div>
                  <div className="stat-label">Interest Saved</div>
                  <div className="stat-value pos" style={{ fontSize: 'var(--text-2xl)' }}>
                    {money(simulation.interestSaved)}
                  </div>
                  <div className="tiny muted">Avoided finance charges</div>
                </div>
                <div>
                  <div className="stat-label">Time Saved</div>
                  <div className="stat-value brand" style={{ fontSize: 'var(--text-2xl)' }}>
                    {simulation.monthsSaved} mo
                  </div>
                  <div className="tiny muted">
                    {(simulation.monthsSaved / 12).toFixed(1)} years sooner debt-free
                  </div>
                </div>
              </div>
            )}
          </div>
        </GlassCard>
      )}

      {/* Individual Loans Detailed Breakdown */}
      {loans.map((loan) => {
        const emi = Number(loan.emi) || 0;
        /* The exact split is the first row of the schedule the backend
           already sent. Multiplying the EMI by a percentage it had rounded
           to one decimal put 386 on screen where the schedule said 365.42 -
           and left the same card claiming 365 of total remaining interest
           on a loan with one payment left. */
        const first = loan.schedule?.[0];
        const sharePct = Number(loan.next_interest_share_pct) || 0;
        const interestAmt = first ? Number(first.interest) : emi * (sharePct / 100);
        const principalAmt = first ? Number(first.principal) : emi * (1 - sharePct / 100);

        const scheduleData = (loan.schedule || []).map((p) => ({
          label: String(p.date).slice(0, 4),
          closing: p.closing || 0,
          interest: p.interest || 0,
          principal: p.principal || 0,
        }));

        return (
          <div key={loan.account_id} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
            <Section
              title={loan.label}
              subtitle={`Contracted interest rate: ${loan.annual_rate}% p.a. · Next EMI due: ${money(loan.emi)}`}
            />

            <div className="loan-details-grid">
              {/* Terms & Structure Card */}
              <Card title="Loan Contract Terms">
                <dl className="kv" style={{ display: 'grid', gridTemplateColumns: '1fr auto', rowGap: 10, margin: 0 }}>
                  <dt className="muted">Outstanding Principal</dt>
                  <dd className="num font-semibold">{money(loan.outstanding)}</dd>

                  <dt className="muted">Interest Rate</dt>
                  <dd className="num font-semibold">{loan.annual_rate}% p.a.</dd>

                  <dt className="muted">Contracted Monthly EMI</dt>
                  <dd className="num font-semibold">{money(loan.emi)}</dd>

                  <dt className="muted">Remaining Term</dt>
                  <dd className="num">{loan.months_remaining} mo ({loan.years_remaining} yrs)</dd>

                  <dt className="muted">Projected Payoff</dt>
                  <dd className="num">{dateLabel(loan.payoff_date)}</dd>

                  <dt className="muted">Total Remaining Interest</dt>
                  <dd className="num neg font-semibold">{money(loan.total_interest_remaining)}</dd>

                  <dt className="muted">Total Remaining Payable</dt>
                  <dd className="num font-semibold">{money(loan.total_payable_remaining)}</dd>
                </dl>

                {/* EMI Composition */}
                <div style={{ marginTop: 'var(--space-5)', paddingTop: 'var(--space-4)', borderTop: '1px solid var(--border-subtle)' }}>
                  <div className="small font-medium" style={{ marginBottom: 6 }}>
                    Next EMI Composition
                  </div>
                  
                  <div style={{ height: 12, borderRadius: 6, display: 'flex', overflow: 'hidden', background: 'var(--surface-3)' }}>
                    <div
                      style={{
                        width: `${sharePct}%`,
                        background: 'var(--c7)',
                        transition: 'width 0.4s ease',
                      }}
                      title={`Interest: ${pct(sharePct, 1)}`}
                    />
                    <div
                      style={{
                        width: `${100 - sharePct}%`,
                        background: 'var(--accent-teal)',
                        transition: 'width 0.4s ease',
                      }}
                      title={`Principal: ${pct(100 - sharePct, 1)}`}
                    />
                  </div>

                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }} className="tiny muted">
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--c7)' }} />
                      Interest: {money(interestAmt)} ({pct(sharePct, 0)})
                    </span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--accent-teal)' }} />
                      Principal: {money(principalAmt)} ({pct(100 - sharePct, 0)})
                    </span>
                  </div>
                </div>

                {sharePct > 60 && (
                  <Callout tone="warn" style={{ marginTop: 'var(--space-4)' }}>
                    Early in tenure, over 60% of each instalment services interest rather than principal.
                    Prepayments made today have maximal lifetime impact.
                  </Callout>
                )}
                {loan.warnings?.map((w, i) => (
                  <Callout tone="warn" key={i} style={{ marginTop: 'var(--space-3)' }}>{w}</Callout>
                ))}
              </Card>

              {/* Balance Over Remaining Term Chart */}
              <Card title="Amortization Balance Trajectory" subtitle="Principal run-down sampled by year">
                <GlowAreaChart
                  data={scheduleData}
                  dataKey="closing"
                  color="var(--c7)"
                  height={260}
                  fillOpacity={0.22}
                />
              </Card>
            </div>

            {/* Interest vs Principal Yearly Stacked */}
            {scheduleData.length > 1 && (
              <Card
              title="Yearly Payment Breakdown"
              subtitle="Interest portion shrinks as principal pays down over time"
              tools={<Legend items={[
                { label: 'Principal reduction', color: 'var(--accent-teal)' },
                { label: 'Interest serviced', color: 'var(--c7)' },
              ]} />}
            >
                <ComboChart
                  data={scheduleData}
                  bars={[{ key: 'principal', label: 'Principal reduction', color: 'var(--accent-teal)' }]}
                  lines={[{ key: 'interest', label: 'Interest serviced', color: 'var(--c7)' }]}
                  height={220}
                />
              </Card>
            )}
          </div>
        );
      })}

      {/* Credit Cards Utilization Section */}
      {cards.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
          <Section
            title="Credit Cards & Revolving Lines"
            subtitle="Credit bureaus penalize sustained revolving utilization above 30%."
          />

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 'var(--space-4)' }}>
            {cards.map((c) => {
              const limit = Number(c.credit_limit) || 0;
              const outstanding = Number(c.principal_outstanding) || Math.abs(Number(c.current_balance ?? c.balance) || 0);
              const usedRatio = limit > 0 ? outstanding / limit : null;
              const usedPct = usedRatio != null ? Math.min(100, Math.max(0, usedRatio * 100)) : null;
              const isHigh = usedRatio != null && usedRatio > 0.3;

              return (
                <Card key={c.id} title={c.display_name}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 'var(--space-4)' }}>
                    <div>
                      <div className="stat-label">Outstanding Balance</div>
                      <div className="stat-value sm num" style={{ color: isHigh ? 'var(--state-neg)' : 'var(--text-1)' }}>
                        {money(outstanding)}
                      </div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div className="stat-label">Credit Limit</div>
                      <div className="stat-value sm num muted">
                        {c.credit_limit ? money(c.credit_limit) : '—'}
                      </div>
                    </div>
                  </div>

                  {usedRatio != null && (
                    <div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                        <span className="tiny muted">Credit Utilization</span>
                        <Badge tone={isHigh ? 'warn' : 'pos'} size="sm">
                          {pct(usedPct, 1)}
                        </Badge>
                      </div>

                      <div style={{ height: 8, borderRadius: 4, background: 'var(--surface-3)', overflow: 'hidden' }}>
                        <div
                          style={{
                            width: `${usedPct}%`,
                            height: '100%',
                            background: isHigh ? 'var(--state-warn)' : 'var(--state-pos)',
                            borderRadius: 4,
                            transition: 'width 0.4s ease',
                          }}
                        />
                      </div>

                      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }} className="tiny muted">
                        <span>Available: {money(Math.max(0, limit - outstanding))}</span>
                        <span>Threshold: 30%</span>
                      </div>
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
