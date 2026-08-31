import React from 'react';
import { Empty } from './ui';
import TransactionsTable from './TransactionsTable';

/* Credit cards only, with a UPI/Other split - a lot of card spend on Indian
   cards is routed through UPI rather than a POS swipe, and the two behave
   differently enough (small, frequent vs. large, occasional) to be worth
   telling apart without leaving the tab. */
export default function CardTransactions({ data }) {
  const accounts = (data.accounts || []).filter((a) => a.account_type === 'credit_card');

  if (!accounts.length) {
    return <Empty title="No credit cards found">
      Import a card statement from the Import wizard to see them here.
    </Empty>;
  }

  return (
    <TransactionsTable
      accounts={accounts}
      title="Card Transactions"
      showRailToggle
      emptyHint="Try a different card, or switch between UPI and other spends."
    />
  );
}
