import React, { useMemo, useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { count, dateLabel, monthLabelLong, titleCase } from '../core/format';
import {
  Button, Callout, Card, GlassCard, Chip, Empty, Loading, Search, Section, Select, Stat, Table, Badge,
} from '../ui';
import { ComboChart } from '../ui/charts';

const SORTS = [
  ['requests', 'Most Active API Requests'],
  ['created', 'Newest Registrations'],
  ['transactions', 'Most Ingested Transactions'],
  ['email', 'Alphabetical Email'],
];

export default function Admin() {
  const { data, loading, error, refetch } = useQuery('admin', () => api.adminOverview());
  const [sort, setSort] = useState('requests');
  const [search, setSearch] = useState('');

  const rows = useMemo(() => {
    const all = data?.accounts || [];
    const needle = search.trim().toLowerCase();
    const filtered = needle
      ? all.filter((a) => `${a.email} ${a.name}`.toLowerCase().includes(needle))
      : all;
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
        {error.message} — This operator view is strictly restricted to authorized emails in <code>FA_ADMIN_EMAILS</code>.
      </Callout>
    );
  }
  if (loading) return <Loading label="Compiling system deployment metrics…" />;

  const t = data.totals || {};
  const signups = (data.signups_by_month || []).map((m) => ({
    label: monthLabelLong(m.month).replace(' 20', ' '),
    count: m.count,
  }));

  return (
    <div className="admin-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>Deployment Telemetry & Administration</h1>
            <Badge tone="brand" size="sm">
              Operator: {data.viewer?.email}
            </Badge>
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            System-level throughput, active user accounts, and parser volume. Strict zero-knowledge privacy:
            only volumetric metadata is visible; raw transaction amounts and narrations are mathematically unreachable.
          </p>
        </div>
      </div>

      {/* Top Telemetry Stats */}
      <div className="stats-grid">
        <Stat
          label="Registered Accounts"
          value={count(t.accounts)}
          sub={`${t.onboarded || 0} onboarded · ${t.never_returned || 0} dormant`}
        />
        <Stat
          label="Active Live Sessions"
          value={count(t.signed_in_now)}
          tone="pos"
          sub="Valid authenticated JWTs"
        />
        <Stat
          label="Total API Calls"
          value={count(t.requests)}
          sub={`Across ${count(t.sign_ins)} sign-ins`}
        />
        <Stat
          label="Transactions Managed"
          value={count(t.transactions)}
          tone="brand"
          sub={`Across ${count(t.files)} files in ${t.with_a_ledger || 0} ledgers`}
        />
      </div>

      {/* Sign-ups Trajectory & Ingestion Routes */}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(340px, 1fr) minmax(340px, 1fr)', gap: 'var(--space-4)' }}>
        <Card title="Monthly User Registrations" subtitle={`${signups.length} months active`}>
          {signups.length ? (
            <ComboChart
              data={signups}
              height={220}
              bars={[{ key: 'count', name: 'New Accounts', color: 'var(--brand-primary)' }]}
            />
          ) : (
            <Empty title="No user registrations recorded" icon="briefcase" />
          )}
        </Card>

        <Card title="Statement Ingestion Channels" subtitle="Route through which files entered the system" pad={false}>
          <div style={{ maxHeight: 250, overflowY: 'auto' }}>
            <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th>Channel Route</th>
                  <th style={{ textAlign: 'right' }}>Documents Ingested</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(data.sources || {}).map(([src, n]) => (
                  <tr key={src} className="terminal-row">
                    <td className="font-medium">{titleCase(src)}</td>
                    <td className="num font-semibold nowrap" style={{ textAlign: 'right' }}>{count(n)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {/* Recognized Institutions in Use */}
      {data.institutions?.length > 0 && (
        <Card title="Institutions Held Across Deployment" subtitle="Counted per distinct account holder">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {data.institutions.map((row) => (
              <Chip key={row.institution}>
                {row.institution} <span className="font-bold brand" style={{ marginLeft: 4 }}>{row.accounts}</span>
              </Chip>
            ))}
          </div>
        </Card>
      )}

      {/* Tenant Accounts Table */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 'var(--space-3)' }}>
          <Section title="Deployment Tenants" subtitle={`${rows.length} accounts on record`} />
          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
            <Select value={sort} onChange={setSort} options={SORTS} style={{ minWidth: 220 }} />
            <Search value={search} onChange={setSearch} placeholder="Filter accounts…" style={{ width: 200 }} />
          </div>
        </div>

        <Card pad={false}>
          <div style={{ maxHeight: 520, overflowY: 'auto' }}>
            <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th>Account Identity</th>
                  <th>Registered</th>
                  <th style={{ textAlign: 'right' }}>API Requests</th>
                  <th style={{ textAlign: 'right' }}>Files Processed</th>
                  <th style={{ textAlign: 'right' }}>Stored Transactions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.id} className="terminal-row">
                    <td>
                      <div className="font-semibold">{a.name || a.email}</div>
                      <div className="tiny num muted">{a.email}</div>
                    </td>
                    <td className="nowrap muted tiny">{dateLabel(a.created_at)}</td>
                    <td className="num nowrap font-medium" style={{ textAlign: 'right' }}>{count(a.requests)}</td>
                    <td className="num nowrap" style={{ textAlign: 'right' }}>{count(a.ledger?.files || 0)}</td>
                    <td className="num nowrap font-semibold brand" style={{ textAlign: 'right' }}>
                      {count(a.ledger?.transactions || 0)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  );
}
