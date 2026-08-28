import React, { useState, useEffect } from 'react';
import { Card, Stat } from './ui';
import { monthLabel } from '../lib';
import { api } from '../lib';
import TransactionsTable from './TransactionsTable';

export default function MonthView() {
  const [months, setMonths] = useState([]);
  const [selectedMonth, setSelectedMonth] = useState('');
  const [data, setData] = useState(null);
  
  useEffect(() => {
    const today = new Date();
    const arr = [];
    for(let i=0; i<12; i++) {
        const d = new Date(today.getFullYear(), today.getMonth() - i, 1);
        arr.push(`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2, '0')}`);
    }
    setMonths(arr);
    setSelectedMonth(arr[0]);
  }, []);

  useEffect(() => {
    if(!selectedMonth) return;
    api.transactions({ limit: 5000 }).then(res => {
       const txns = res.transactions.filter(t => t.accounting_month === selectedMonth || (t.date && t.date.startsWith(selectedMonth)));
       let inflow = 0;
       let outflow = 0;
       txns.forEach(t => {
           if(t.is_internal_transfer) return;
           if(t.direction === 'credit') inflow += t.amount;
           if(t.direction === 'debit') outflow += t.amount;
       });
       setData({ transactions: txns, inflow, outflow, net: inflow - outflow });
    });
  }, [selectedMonth]);

  if (!data) return <div className="p-4 spinner" />;

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2>Accounting Period</h2>
        <select value={selectedMonth} onChange={e => setSelectedMonth(e.target.value)} style={{ padding: 8, borderRadius: 6 }}>
            {months.map(m => <option key={m} value={m}>{monthLabel(m)}</option>)}
        </select>
      </div>
      <div style={{ display: 'flex', gap: 14 }}>
          <Stat label="Total Inflow" value={data.inflow} tone="pos" />
          <Stat label="Total Outflow" value={data.outflow} tone="neg" />
          <Stat label="Net Saved" value={data.net} tone={data.net >= 0 ? 'pos' : 'neg'} />
      </div>
      <Card title="Combined Ledger">
          <TransactionsTable accounts={[]} />
      </Card>
    </div>
  );
}
