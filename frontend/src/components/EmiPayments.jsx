import React from 'react';
import { compact, money, titleCase } from '../lib';
import { Card, Chip, Empty } from './ui';
import TransactionsTable from './TransactionsTable';

/* The Debt tab already shows every loan and its forward-looking amortization
   projection - what's still owed, at what rate, paid off by when. This is the
   other half: what was ACTUALLY paid, month by month, which can differ from
   the nominal EMI (a part-payment, a missed month, a revised rate). */
export default function EmiPayments({ data }) {
  const loans = data.loans || [];
  const accounts = data.accounts || [];

  // A loan ACCOUNT (its own statement, with a principal/rate/tenure) is one
  // way EMIs show up; the far more common one is a bare EMI debit line on a
  // savings account paying someone else's loan servicer, with no loan account
  // of its own to attach to. Only hide the transaction table - never the
  // cards, which genuinely have nothing to show - when there is truly no
  // loan account AT ALL; the table's own empty state handles "no EMI rows".
  if (!loans.length) {
    return (
      <>
        <Empty title="No loan accounts found">
          No account carries a home/personal/auto loan's own principal, rate or
          tenure - but a payment can still count as an EMI below.
        </Empty>
        <TransactionsTable
          accounts={accounts}
          title="EMI Payments"
          fixedCategory="emi"
          emptyHint="No EMI transactions matched the current filters."
        />
      </>
    );
  }

  return (
    <>
      <div className="section-title">Loans</div>
      <div className="grid cols-3">
        {loans.map((loan) => {
          const account = accounts.find((a) => a.id === loan.account_id);
          return (
            <Card key={loan.account_id} title={loan.label} sub={account?.institution}>
              <div className="stat">
                <div className="stat-label">Outstanding</div>
                <div className="stat-value num neg" style={{ fontSize: 21 }}>
                  {compact(loan.outstanding)}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
                {loan.emi != null && <Chip>EMI {money(loan.emi)}</Chip>}
                {loan.annual_rate != null && <Chip>{loan.annual_rate}% p.a.</Chip>}
                {loan.months_remaining != null && (
                  <Chip>{loan.years_remaining}y remaining</Chip>
                )}
              </div>
            </Card>
          );
        })}
      </div>

      <TransactionsTable
        accounts={accounts}
        title="EMI Payments"
        fixedCategory="emi"
        emptyHint="No EMI transactions matched the current filters."
      />
    </>
  );
}
