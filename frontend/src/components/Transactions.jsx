import React from 'react';
import TransactionsTable from './TransactionsTable';

/* Every account, every filter available - the master view. Scoped tabs
   (Savings, Cards, UPI, Loans) are the same table pre-filtered to a subset. */
export default function Transactions({ data }) {
  return <TransactionsTable accounts={data.accounts || []} title="Transactions" />;
}
