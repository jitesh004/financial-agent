/* The operator's view: who is on this deployment, and how much they use it.
 *
 * Reachable only for an address listed in FA_ADMIN_EMAILS on the server. The
 * entry is hidden otherwise and the endpoint answers 404 - not 403, because
 * whether this deployment has an operator's view at all is not a useful thing
 * to confirm to somebody who is not its operator.
 *
 * It reports VOLUMES and never AMOUNTS. This app's central promise is that no
 * query of one account can reach a row of another's, and a screen listing
 * everybody's income would contradict it in the one place it matters most.
 */

import React, { useMemo, useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { count, dateLabel, monthLabelLong, titleCase } from '../core/format';
import {
  Button, Callout, Card, Chip, Empty, Loading, Search, Section, Select, Stat, Table,
} from '../ui';
import { BarChart, useChartKeyframes } from '../ui/charts';

const SORTS = [
  ['requests', 'Most active'], ['created', 'Newest'],
  ['transactions', 'Most imported'], ['email', 'Address'],
];

export default function Admin() {
  useChartKeyframes();
  const { data, loading, error, refetch } = useQuery('admin', () => api.adminOverview());
  const [sort, setSort] = useState('requests');
  const [search, setSearch] = useState('');

  const rows = useMemo(() => {
    const all = data?.accounts || [];
    const needle = search.trim().toLowerCase();
    const filtered = needle
      ? all.filter((a) => `${a.email} ${a.name}`.toLowerCase().includes(needle)) : all;
    const by = {
      requests: (a, b) => b.requests - a.requests,
      created: (a, b) => String(b.created_at).localeCompare(String(a.created_at)),
      transactions: (a, b) => ((b.ledger?.transactions || 0) - (a.ledger?.transactions || 0)),
      email: (a, b) => a.email.localeCompare(b.email),
    }[sort];
    return [...filtered].sort(by);
  }, [data, search, sort]);

  if (error) {
    return (
      <Callout tone="neg">
        {error.message} — this view is limited to the addresses in{' '}
        <code>FA_ADMIN_EMAILS</code>.
      </Callout>
    );
  }
  if (loading) return <Loading label="Counting…" />;

  const t = data.totals || {};
  const signups = (data.signups_by_month || []).map((m) => ({
    label: monthLabelLong(m.month).replace(' 20', ' '), count: m.count,
  }));

  return (
    <>
      <div>
        <h2 className="h2">
          This deployment
          <span className="section-note" style={{ marginLeft: 10 }}>
            you are {data.viewer?.email}
            {data.admins?.length > 1 && ` · ${data.admins.length} admins`}
          </span>
        </h2>
        <p className="lead">
          Counts only. No account&apos;s amounts, categories or descriptions are read
          here — each account&apos;s figures are counted with that account bound as the
          tenant, through the same row-level security every request goes through, so this
          can count rows it cannot read.
        </p>
      </div>

      <div className="grid cols-4">
        {/* Each note describes the figure above it. A card whose note reports a
            DIFFERENT quantity reads as an explanation of the number and is not
            one, so the population facts stay together on the first. */}
        <Stat label="Accounts" value={count(t.accounts)}
          note={`${t.onboarded || 0} finished setup, ${t.never_returned || 0} never returned`} />
        <Stat label="Signed in now" value={count(t.signed_in_now)}
          note="with a session still valid" />
        <Stat label="Requests served" value={count(t.requests)}
          note={`across ${count(t.sign_ins)} sign-in${t.sign_ins === 1 ? '' : 's'}`} />
        <Stat label="Transactions stored" value={count(t.transactions)}
          note={`${count(t.files)} document${t.files === 1 ? '' : 's'}, ${t.with_a_ledger || 0} `
            + `${t.with_a_ledger === 1 ? 'ledger' : 'ledgers'}`} />
      </div>

      <div className="split">
        <Card title="Sign-ups by month" sub={`${signups.length} months`}>
          {signups.length ? (
            <BarChart
              data={signups}
              height={220}
              axisFormat={(v) => String(Math.round(v))}
              format={(v) => `${v} sign-ups`}
              series={[{ key: 'count', name: 'Sign-ups', color: 'var(--c1)' }]}
            />
          ) : <Empty title="Nobody has signed up yet" icon="users" />}
        </Card>

        <Card title="How documents arrive" sub="which import route each file came through">
          {Object.keys(data.sources || {}).length ? (
            <Table>
              <thead><tr><th>Source</th><th className="right">Documents</th></tr></thead>
              <tbody>
                {Object.entries(data.sources).map(([source, n]) => (
                  <tr key={source}>
                    <td>{titleCase(source)}</td>
                    <td className="right num">{count(n)}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : <Empty title="No documents imported yet" icon="file" />}
        </Card>
      </div>

      {data.institutions?.length > 0 && (
        <Card title="Institutions in use" sub="counted per account holding one, not per statement">
          <div className="row tight">
            {data.institutions.map((row) => (
              <Chip key={row.institution}>
                {row.institution}<strong style={{ marginLeft: 6 }}>{row.accounts}</strong>
              </Chip>
            ))}
          </div>
        </Card>
      )}

      <Section
        title="Accounts"
        note={`${rows.length} shown`
          + (data.detail_limit > 0 && t.accounts > data.detail_limit
            ? ` · ledger figures for the ${data.detail_limit} busiest` : '')}
      />

      <Card pad={false}>
        <div className="row" style={{ padding: 12 }}>
          <Search value={search} onChange={setSearch} placeholder="Filter by address or name…"
            style={{ flex: 1, minWidth: 200 }} />
          <Select value={sort} onChange={setSort} aria-label="Sort"
            options={SORTS.map(([v, l]) => [v, `Sort: ${l}`])} />
          <Button icon="refresh" onClick={refetch}>Refresh</Button>
        </div>

        <Table>
          <thead>
            <tr>
              <th>Account</th><th>Joined</th><th>Last seen</th>
              <th className="right">Visits</th><th className="right">Documents</th>
              <th className="right">Transactions</th><th>Covers</th><th>Uses</th>
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
                    <div style={{ fontWeight: 560, overflowWrap: 'anywhere' }}>{row.email}</div>
                    <div className="row tight" style={{ marginTop: 3 }}>
                      {row.name && <span className="tiny dim">{row.name}</span>}
                      {row.is_admin && <Chip tone="acc">admin</Chip>}
                      {row.status !== 'active' && <Chip tone="warn">{row.status}</Chip>}
                      {!row.onboarded && <Chip tone="warn">setup: {row.onboarding_step}</Chip>}
                      {row.demo_mode && <Chip tone="acc">in demo</Chip>}
                    </div>
                  </td>
                  <td className="nowrap">{dateLabel(row.created_at)}</td>
                  <td className="nowrap">
                    {row.last_seen ? dateLabel(row.last_seen) : '—'}
                    {row.live_sessions > 0 && (
                      <div className="tiny pos">{row.live_sessions} live</div>
                    )}
                  </td>
                  <td className="right num nowrap">
                    {count(row.requests)}
                    <div className="tiny dim">
                      {row.sign_ins} sign-in{row.sign_ins === 1 ? '' : 's'}
                    </div>
                  </td>
                  <td className="right num nowrap">
                    {ledger.unavailable ? '—' : count(ledger.files || 0)}
                    {failed > 0 && <div className="tiny warnc">{failed} unread</div>}
                  </td>
                  <td className="right num nowrap">
                    {ledger.unavailable ? '—' : count(ledger.transactions || 0)}
                    {ledger.accounts ? (
                      <div className="tiny dim">
                        {ledger.accounts} account{ledger.accounts === 1 ? '' : 's'}
                      </div>
                    ) : null}
                  </td>
                  <td className="nowrap small muted">
                    {ledger.months_covered ? `${ledger.months_covered} months` : '—'}
                    {ledger.first_month && (
                      <div className="tiny dim">
                        {monthLabelLong(ledger.first_month)} → {monthLabelLong(ledger.last_month)}
                      </div>
                    )}
                  </td>
                  <td>
                    <div className="row tight">
                      {row.gmail_connected && <Chip tone="pos">Gmail</Chip>}
                      {Object.keys(ledger.sources || {}).filter((s) => s !== 'gmail')
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
        </Table>

        {!rows.length && (
          <div style={{ padding: 16 }}>
            <Empty title="No accounts match" icon="users">
              {search ? 'Try a different address.' : 'Nobody has signed up yet.'}
            </Empty>
          </div>
        )}
      </Card>

      {data.note && <Callout>{data.note}</Callout>}
    </>
  );
}
