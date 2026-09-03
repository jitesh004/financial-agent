import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api, count, dateLabel, monthLabelLong, titleCase } from '../lib';
import { Callout, Card, Chip, Empty, Stat } from './ui';

/* The operator's view: who is on this deployment, and how much they use it.
 *
 * Reachable only for an address listed in FA_ADMIN_EMAILS on the server. The
 * tab is hidden otherwise and the endpoint answers 404 - not 403, because
 * whether this deployment has an operator's view at all is not a useful thing
 * to confirm to somebody who is not its operator.
 *
 * It reports VOLUMES and never AMOUNTS. This app's central promise is that no
 * query of one account can reach a row of another's, and a screen listing
 * everybody's income would contradict it in the one place it matters most. So
 * what is here is operational: statements imported, rows produced, sources
 * used, how often each account comes back. Not what any of it says.
 */

const SORTS = [
  ['requests', 'Most active'],
  ['created', 'Newest'],
  ['transactions', 'Most imported'],
  ['email', 'Address'],
];

export default function Admin() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [sort, setSort] = useState('requests');
  const [search, setSearch] = useState('');

  const load = useCallback(() => {
    setData(null);
    api.adminOverview()
      .then((body) => { setData(body); setError(null); })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(load, [load]);

  const rows = useMemo(() => {
    const all = data?.accounts || [];
    const needle = search.trim().toLowerCase();
    const filtered = needle
      ? all.filter((a) => `${a.email} ${a.name}`.toLowerCase().includes(needle))
      : all;
    const by = {
      requests: (a, b) => b.requests - a.requests,
      created: (a, b) => String(b.created_at).localeCompare(String(a.created_at)),
      transactions: (a, b) => ((b.ledger?.transactions || 0)
        - (a.ledger?.transactions || 0)),
      email: (a, b) => a.email.localeCompare(b.email),
    }[sort];
    return [...filtered].sort(by);
  }, [data, search, sort]);

  if (error) {
    return (
      <Callout tone="neg">
        {error}
        {' — '}
        this view is limited to the addresses in <code>FA_ADMIN_EMAILS</code>.
      </Callout>
    );
  }
  if (!data) return <div className="spinner" style={{ margin: 40 }} />;

  const t = data.totals || {};

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 className="section-title" style={{ marginBottom: 4 }}>
          This deployment
          <span className="section-note">
            you are {data.viewer?.email}
            {data.admins?.length > 1 && ` · ${data.admins.length} admins`}
          </span>
        </h2>
        <p style={{ color: 'var(--text-2)', margin: 0, maxWidth: '80ch' }}>
          Counts only. No account&apos;s amounts, categories or descriptions are
          read here — each account&apos;s figures are counted with that account
          bound as the tenant, through the same row-level security every
          request goes through, so this can count rows it cannot read.
        </p>
      </div>

      <div className="grid cols-4">
        {/* Each note describes the figure above it. A card whose note reports
            a DIFFERENT quantity reads as an explanation of the number and is
            not one, so the population facts stay together on the first. */}
        <Stat label="Accounts" value={count(t.accounts)}
          note={`${t.onboarded || 0} finished setup, `
            + `${t.never_returned || 0} never returned`} />
        <Stat label="Signed in now" value={count(t.signed_in_now)}
          note="with a session still valid" />
        <Stat label="Requests served" value={count(t.requests)}
          note={`across ${count(t.sign_ins)} sign-in${t.sign_ins === 1 ? '' : 's'}`} />
        <Stat label="Transactions stored" value={count(t.transactions)}
          note={`${count(t.files)} document${t.files === 1 ? '' : 's'}, `
            + `${t.with_a_ledger || 0} `
            + `${t.with_a_ledger === 1 ? 'ledger' : 'ledgers'}`} />
      </div>

      <div className="grid cols-2">
        <Card title="Sign-ups by month"
          sub={`${data.signups_by_month?.length || 0} months`}>
          {data.signups_by_month?.length ? (
            <div className="admin-bars">
              {data.signups_by_month.map((row) => {
                const peak = Math.max(
                  ...data.signups_by_month.map((m) => m.count), 1);
                return (
                  <div className="admin-bar" key={row.month}
                    title={`${row.count} in ${monthLabelLong(row.month)}`}>
                    <span className="admin-bar-fill"
                      style={{ height: `${(row.count / peak) * 100}%` }} />
                    <span className="admin-bar-label">
                      {monthLabelLong(row.month).replace(' ', ' ')}
                    </span>
                  </div>
                );
              })}
            </div>
          ) : <Empty title="Nobody has signed up yet" />}
        </Card>

        <Card title="How documents arrive"
          sub="which import route each file came through">
          {Object.keys(data.sources || {}).length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Source</th><th className="right">Documents</th></tr>
                </thead>
                <tbody>
                  {Object.entries(data.sources).map(([source, n]) => (
                    <tr key={source}>
                      <td>{titleCase(source)}</td>
                      <td className="right num">{count(n)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <Empty title="No documents imported yet" />}
        </Card>
      </div>

      {data.institutions?.length > 0 && (
        <Card title="Institutions in use"
          sub="counted per account holding one, not per statement">
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {data.institutions.map((row) => (
              <Chip key={row.institution}>
                {row.institution}
                <strong style={{ marginLeft: 6 }}>{row.accounts}</strong>
              </Chip>
            ))}
          </div>
        </Card>
      )}

      <div className="section-title">
        Accounts
        <span className="section-note">
          {rows.length} shown
          {data.detail_limit > 0 && t.accounts > data.detail_limit
            && ` · ledger figures for the ${data.detail_limit} busiest`}
        </span>
      </div>

      <Card>
        <div style={{ display: 'flex', gap: 8, marginBottom: 12,
          flexWrap: 'wrap' }}>
          <input type="search" placeholder="Filter by address or name…"
            value={search} onChange={(e) => setSearch(e.target.value)}
            style={{ flex: 1, minWidth: 200 }} />
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            {SORTS.map(([value, label]) => (
              <option key={value} value={value}>Sort: {label}</option>
            ))}
          </select>
          <button className="btn" onClick={load}>Refresh</button>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Account</th>
                <th>Joined</th>
                <th>Last seen</th>
                <th className="right">Visits</th>
                <th className="right">Documents</th>
                <th className="right">Transactions</th>
                <th>Covers</th>
                <th>Uses</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const ledger = row.ledger || {};
                const failed = (ledger.files_by_status?.failed || 0)
                  + (ledger.files_by_status?.needs_password || 0);
                return (
                  <tr key={row.id}>
                    <td>
                      <div style={{ fontWeight: 550, wordBreak: 'break-all' }}>
                        {row.email}
                      </div>
                      <div style={{ display: 'flex', gap: 5, marginTop: 3,
                        flexWrap: 'wrap', alignItems: 'center' }}>
                        {row.name && (
                          <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                            {row.name}
                          </span>
                        )}
                        {row.is_admin && <Chip tone="accent">admin</Chip>}
                        {row.status !== 'active' && (
                          <Chip tone="warn">{row.status}</Chip>
                        )}
                        {!row.onboarded && (
                          <Chip tone="warn">setup: {row.onboarding_step}</Chip>
                        )}
                        {row.demo_mode && <Chip tone="accent">in demo</Chip>}
                      </div>
                    </td>
                    <td className="nowrap">{dateLabel(row.created_at)}</td>
                    <td className="nowrap">
                      {row.last_seen ? dateLabel(row.last_seen) : '—'}
                      {row.live_sessions > 0 && (
                        <div style={{ fontSize: 11, color: 'var(--positive)' }}>
                          {row.live_sessions} live
                        </div>
                      )}
                    </td>
                    <td className="right num nowrap">
                      {count(row.requests)}
                      <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
                        {row.sign_ins} sign-in{row.sign_ins === 1 ? '' : 's'}
                      </div>
                    </td>
                    <td className="right num nowrap">
                      {ledger.unavailable ? '—' : count(ledger.files || 0)}
                      {failed > 0 && (
                        <div style={{ fontSize: 11, color: 'var(--warn)' }}>
                          {failed} unread
                        </div>
                      )}
                    </td>
                    <td className="right num nowrap">
                      {ledger.unavailable ? '—' : count(ledger.transactions || 0)}
                      {ledger.accounts ? (
                        <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
                          {ledger.accounts} account
                          {ledger.accounts === 1 ? '' : 's'}
                        </div>
                      ) : null}
                    </td>
                    <td className="nowrap" style={{ fontSize: 12,
                      color: 'var(--text-2)' }}>
                      {ledger.months_covered
                        ? `${ledger.months_covered} months`
                        : '—'}
                      {ledger.first_month && (
                        <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
                          {monthLabelLong(ledger.first_month)} →{' '}
                          {monthLabelLong(ledger.last_month)}
                        </div>
                      )}
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                        {row.gmail_connected && <Chip tone="pos">Gmail</Chip>}
                        {Object.keys(ledger.sources || {})
                          .filter((s) => s !== 'gmail')
                          .map((s) => <Chip key={s}>{titleCase(s)}</Chip>)}
                        {(ledger.institutions || []).length > 0 && (
                          <Chip title={ledger.institutions.join(', ')}>
                            {ledger.institutions.length} institution
                            {ledger.institutions.length === 1 ? '' : 's'}
                          </Chip>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {!rows.length && (
          <Empty title="No accounts match">
            {search ? 'Try a different address.' : 'Nobody has signed up yet.'}
          </Empty>
        )}
      </Card>

      <Callout>{data.note}</Callout>
    </div>
  );
}
