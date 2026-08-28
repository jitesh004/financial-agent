import React from 'react';
import { Empty } from './ui';
import TransactionsTable from './TransactionsTable';

const SAVINGS_TYPES = new Set(['savings', 'current', 'wallet']);

/* Bank accounts only - credit cards and loans have their own tabs, and mixing
   them in here would make "money actually in the bank" impossible to read. */
export default function SavingsAccounts({ data }) {
  const accounts = (data.accounts || []).filter((a) => SAVINGS_TYPES.has(a.account_type));

  if (!accounts.length) {
    return <Empty title="No savings or current accounts found">
      Upload a bank statement, or connect Gmail, to see them here.
    </Empty>;
  }

  return (
    <TransactionsTable
      accounts={accounts}
      title="Savings & Current Accounts"
      emptyHint="Try selecting a different account or clearing the filters."
    />
  );
}
