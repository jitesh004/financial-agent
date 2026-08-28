import React from 'react';
import TransactionsTable from './TransactionsTable';

/* UPI spends across EVERY account, not just cards - it is a payment rail, not
   an account type, and most of it in India happens straight off a bank
   account rather than a card. */
export default function UpiTransactions({ data }) {
  return (
    <TransactionsTable
      accounts={data.accounts || []}
      title="UPI Transactions"
      fixedRail="upi"
      emptyHint="No UPI transactions matched - try a different account."
    />
  );
}
