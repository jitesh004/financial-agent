import React, { useCallback, useEffect, useState } from 'react';
import CardTransactions from './components/CardTransactions';
import Debt from './components/Debt';
import EmiPayments from './components/EmiPayments';
import Files from './components/Files';
import FilesAndPasswords from './components/FilesAndPasswords';
import Forecast from './components/Forecast';
import GmailWizard from './components/GmailWizard';
import Overview from './components/Overview';
import Profile from './components/Profile';
import SavingsAccounts from './components/SavingsAccounts';
import Spending from './components/Spending';
import Transactions from './components/Transactions';
import Upload from './components/Upload';
import UpiTransactions from './components/UpiTransactions';
import { Callout, Empty, ThemeToggle } from './components/ui';
import { api } from './lib';

const TABS = [
  ['overview', 'Overview'],
  ['spending', 'Spending'],
  ['debt', 'Debt'],
  ['emi', 'EMI Payments'],
  ['forecast', 'Forecast'],
  ['transactions', 'Transactions'],
  ['savings', 'Savings Accounts'],
  ['cards', 'Card Transactions'],
  ['upi', 'UPI Transactions'],
  ['files', 'Files & quality'],
  ['file-registry', 'Files & Passwords'],
];

export default function App() {
  const [data, setData] = useState(null);
  const [tab, setTab] = useState('overview');
  const [showProfile, setShowProfile] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [theme, setTheme] = useState(
    () => localStorage.getItem('fa-theme')
      || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
  );

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('fa-theme', theme);
  }, [theme]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const dashboard = await api.dashboard();
      setData(dashboard.status === 'ok' ? dashboard : null);
      setError(dashboard.status === 'stale' ? dashboard.message : null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onComplete = (result) => {
    setData(result);
    setTab('overview');
  };

  async function reset() {
    if (!window.confirm(
      'This permanently deletes every uploaded statement and all analyzed data. Continue?',
    )) return;
    setBusy(true);
    try {
      await api.reset();
      setData(null);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const hasData = Boolean(data?.analysis?.totals);

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <div className="brand-mark" />
          Financial Agent
        </div>

        {hasData && (
          <nav className="tabs" role="tablist">
            {TABS.map(([key, label]) => (
              <button
                key={key}
                className="tab"
                role="tab"
                aria-selected={tab === key}
                onClick={() => setTab(key)}
              >
                {label}
              </button>
            ))}
          </nav>
        )}

        <div className="header-spacer" />

        <button className="btn" onClick={() => setShowProfile(true)}>Profile</button>
        {hasData && (
          <>
            <button className="btn" onClick={() => setData(null)}>Upload more</button>
            <button className="btn danger" onClick={reset} disabled={busy}>Reset</button>
          </>
        )}
        <ThemeToggle theme={theme} onToggle={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))} />
      </header>

      <main className="main">
        {showProfile ? (
          <Profile onSaved={() => setShowProfile(false)} />
        ) : loading ? (
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', padding: 40 }}>
            <div className="spinner" /> Loading…
          </div>
        ) : !hasData ? (
          <>
            <div style={{ maxWidth: 760, margin: '0 auto 22px', textAlign: 'center' }}>
              <h1 style={{ fontSize: 26, fontWeight: 660, letterSpacing: '-.6px', margin: '18px 0 8px' }}>
                Understand where your money actually goes
              </h1>
              <p style={{ color: 'var(--text-2)', fontSize: 15, lineHeight: 1.6, margin: 0 }}>
                Upload bank, card, loan and investment statements in any format. Every
                figure is reconciled against the balances your bank printed, so the
                totals are checked rather than estimated.
              </p>
              <p style={{ color: 'var(--text-3)', fontSize: 13, marginTop: 8 }}>
                Password-protected PDFs? Add your details in{' '}
                <button className="btn" style={{ padding: '2px 8px', fontSize: 12 }}
                  onClick={() => setShowProfile(true)}>Profile</button>{' '}
                and they'll open automatically.
              </p>
            </div>
            {error && <Callout tone="warn" style={{ marginBottom: 14 }}>{error}</Callout>}
            {/* Wider than a typical form column: the Gmail review table carries
                seven columns and is unusable squeezed into 820px. */}
            <div style={{ maxWidth: 1180, margin: '0 auto', display: 'grid', gap: 14 }}>
              <div style={{ maxWidth: 820, width: '100%', margin: '0 auto' }}>
                <Upload onComplete={onComplete} />
              </div>
              <div style={{ textAlign: 'center', color: 'var(--text-3)', fontSize: 12, textTransform: 'uppercase', letterSpacing: '.08em' }}>
                or
              </div>
              <GmailWizard onComplete={load} />
            </div>
          </>
        ) : (
          <>
            {error && <Callout tone="warn">{error}</Callout>}
            {tab === 'overview' && <Overview data={data} />}
            {tab === 'spending' && <Spending data={data} />}
            {tab === 'debt' && <Debt data={data} />}
            {tab === 'forecast' && <Forecast data={data} />}
            {tab === 'emi' && <EmiPayments data={data} />}
            {tab === 'transactions' && <Transactions data={data} />}
            {tab === 'savings' && <SavingsAccounts data={data} />}
            {tab === 'cards' && <CardTransactions data={data} />}
            {tab === 'upi' && <UpiTransactions data={data} />}
            {tab === 'files' && <Files data={data} />}
            {tab === 'file-registry' && <FilesAndPasswords />}
          </>
        )}
      </main>
    </div>
  );
}
