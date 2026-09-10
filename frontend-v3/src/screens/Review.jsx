import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../core/api';
import { invalidate, useQuery } from '../core/store';
import { useRouteParam } from '../core/router';
import { usePeriod } from '../core/period';
import { useToast } from '../core/toast';
import { useCategories } from '../core/ledger';
import { count, dateLabel, money, titleCase } from '../core/format';
import {
  Button, Callout, Card, GlassCard, Chip, Empty, Loading, Search, Segmented, Select, Stat, Table, Badge,
} from '../ui';
import { Icon } from '../ui/icons';

const MODES = [
  ['queue', 'One At A Time', 'Line item decision triage: evaluate individual context, flow role, and ambiguity.'],
  ['bulk', 'By Merchant Aggregate', 'Batch classification: tag identical merchants once and learn future rule mapping.'],
];

const ROLES = [
  ['income', 'Income (Genuine inbound revenue)'],
  ['claim_settlement', 'Money Back (Reimburses prior personal spend)'],
  ['refund', 'Merchant Refund (Reverses purchase)'],
  ['card_settlement', 'Credit Card Payment (Transfers & settlements)'],
  ['transfer_in', 'Inter-Account Transfer'],
  ['excluded', 'Exclude / Ignore (Filter out of totals)'],
];

const PAGE_SIZE = 40;

function usePaged(rows, step = PAGE_SIZE) {
  const [shown, setShown] = useState(step);
  useEffect(() => { setShown(step); }, [rows.length === 0, step]);
  return {
    slice: rows.length > shown ? rows.slice(0, shown) : rows,
    hidden: Math.max(0, rows.length - shown),
    more: () => setShown((n) => n + step),
    all: () => setShown(rows.length),
  };
}

export default function Review() {
  const [mode, setMode] = useRouteParam('mode', 'queue');
  const active = MODES.find((m) => m[0] === mode) || MODES[0];

  return (
    <div className="review-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>Triage & Categorization Review</h1>
            <Badge tone="brand" size="sm">Rule Learning Engine</Badge>
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            {active[2]}
          </p>
        </div>

        <Segmented
          value={mode}
          onChange={setMode}
          ariaLabel="Triage Mode"
          options={MODES.map(([v, l]) => [v, l])}
        />
      </div>

      {mode === 'bulk' ? <ByMerchant /> : <Queue />}
    </div>
  );
}

function Queue() {
  const toast = useToast();
  const { params, paramsKey, label: periodLabel, scoped } = usePeriod();
  const { data, loading, error, refetch } = useQuery(
    `review:${paramsKey}`,
    () => api.reviewQueue(params)
  );
  const [busy, setBusy] = useState(null);

  const items = data?.transactions || [];

  const groups = useMemo(() => {
    const out = new Map();
    for (const t of items) {
      const k = t.review_reason || 'Requires Human Discretion';
      if (!out.has(k)) out.set(k, []);
      out.get(k).push(t);
    }
    return [...out.entries()];
  }, [items]);

  async function resolve(txn, fields) {
    setBusy(txn.id);
    try {
      await api.updateTransaction(txn.id, { flow_role: txn.flow_role, ...fields });
      invalidate('review', 'workflow', 'analysis', 'dashboard', 'txns');
      await refetch();
      toast.ok('Transaction classified', 'Rule learned for future statements.');
    } catch (e) {
      toast.fail('Classification failed', e.message);
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <Loading label="Loading unconfirmed transactions for review triage…" />;
  if (error) return <Callout tone="neg">{error.message}</Callout>;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>
      <Callout tone="info">
        Every row carries a sensible baseline default, preserving dashboard completeness.
        Confirming explicitly locks in classification and trains the deterministic heuristic engine.
      </Callout>

      {!items.length && (
        <Empty
          icon="shield"
          title={scoped ? `No pending reviews in ${periodLabel}` : 'Zero items pending review'}
        >
          {scoped
            ? 'Every transaction in this window was confidently classified.'
            : 'All transactions verified. New items appear here if an ambiguous transfer or unlinked card payment is parsed.'}
        </Empty>
      )}

      {(data?.total ?? 0) > items.length && (
        <Callout tone="warn">
          {count(data.total)} transactions pending review. Displaying the {count(items.length)} oldest.
        </Callout>
      )}

      {groups.map(([reason, rows]) => (
        <QueueGroup key={reason} reason={reason} rows={rows} busy={busy} resolve={resolve} />
      ))}
    </div>
  );
}

function QueueGroup({ reason, rows, busy, resolve }) {
  const paged = usePaged(rows);

  return (
    <Card title={reason} subtitle={`${rows.length} pending record${rows.length === 1 ? '' : 's'}`} pad={false}>
      <div style={{ overflowX: 'auto' }}>
        <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>Date</th>
              <th>Description</th>
              <th style={{ textAlign: 'right' }}>Amount</th>
              <th>Designate Flow Role</th>
            </tr>
          </thead>
          <tbody>
            {paged.slice.map((t) => (
              <tr key={t.id} className="terminal-row" style={{ opacity: busy === t.id ? 0.4 : 1 }}>
                <td className="nowrap muted tiny">{dateLabel(t.date)}</td>
                <td>
                  <div className="font-medium" style={{ overflowWrap: 'anywhere' }}>{t.description}</div>
                  <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
                    <Chip size="sm">{titleCase(t.category)}</Chip>
                    {t.flow_role && (
                      <Chip tone="brand" size="sm">
                        Current: {titleCase(t.flow_role.replace(/_/g, ' '))}
                      </Chip>
                    )}
                  </div>
                </td>
                <td
                  className={`num nowrap font-semibold ${t.direction === 'credit' ? 'pos' : ''}`}
                  style={{ textAlign: 'right' }}
                >
                  {t.direction === 'credit' ? '+' : '−'}{money(Math.abs(t.amount))}
                </td>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                    <Select
                      value={t.flow_role || ''}
                      placeholder="Assign Role…"
                      disabled={busy === t.id}
                      onChange={(v) => resolve(t, { flow_role: v })}
                      options={ROLES}
                      style={{ minWidth: 220, maxWidth: 360, fontSize: 12, height: 30 }}
                    />
                    <Button
                      size="sm"
                      disabled={busy === t.id}
                      onClick={() => resolve(t, {})}
                      title="Accept pipeline proposed classification"
                    >
                      Looks Right
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {paged.hidden > 0 && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderTop: '1px solid var(--border-subtle)' }}>
          <span className="tiny muted">Showing {count(paged.slice.length)} of {count(rows.length)}</span>
          <div style={{ display: 'flex', gap: 6 }}>
            <Button size="sm" onClick={paged.more}>Show {Math.min(paged.hidden, PAGE_SIZE)} more</Button>
            {paged.hidden > PAGE_SIZE && <Button size="sm" onClick={paged.all}>Show all {count(rows.length)}</Button>}
          </div>
        </div>
      )}
    </Card>
  );
}

const SORTS = [['value', 'Largest Value First'], ['count', 'Most Frequent Hits'], ['name', 'Alphabetical by Merchant']];

function ByMerchant() {
  const toast = useToast();
  const { params, paramsKey, label: periodLabel, scoped } = usePeriod();
  const { data: categories = [] } = useCategories();
  const [scope, setScope] = useState('uncategorized');
  const [sort, setSort] = useState('value');
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState(null);
  const [busy, setBusy] = useState(null);
  const [done, setDone] = useState(0);

  const query = useMemo(() => {
    const q = { limit: api.PAGE_MAX, ...params };
    if (scope === 'uncategorized') q.category = 'uncategorized';
    if (scope === 'review') q.needs_review = true;
    return q;
  }, [scope, params]);

  const { data, loading, error, refetch } = useQuery(
    `review-bulk:${scope}:${paramsKey}`,
    () => api.transactions(query)
  );

  const rows = data?.transactions || [];

  const groups = useMemo(() => {
    const byKey = new Map();
    for (const t of rows) {
      const key = (t.merchant || t.description || '').trim().toUpperCase().slice(0, 48) || '(unidentified merchant)';
      if (!byKey.has(key)) byKey.set(key, { key, items: [], total: 0 });
      const g = byKey.get(key);
      g.items.push(t);
      g.total += Math.abs(Number(t.amount) || 0);
    }
    let out = [...byKey.values()];
    if (search.trim()) {
      const q = search.trim().toUpperCase();
      out = out.filter((g) => g.key.includes(q));
    }
    const cmp = {
      value: (a, b) => b.total - a.total,
      count: (a, b) => b.items.length - a.items.length,
      name: (a, b) => a.key.localeCompare(b.key),
    }[sort];
    return out.sort(cmp);
  }, [rows, sort, search]);

  async function assign(group, category) {
    if (!category) return;
    setBusy(group.key);
    try {
      await api.bulkUpdate(group.items.map((t) => t.id), { category });
      invalidate('review', 'workflow', 'analysis', 'dashboard', 'txns');
      await refetch();
      setDone((n) => n + group.items.length);
      toast.ok(`Classified ${group.items.length} transactions as ${titleCase(category)}`);
    } catch (e) {
      toast.fail('Bulk assignment failed', e.message);
    } finally {
      setBusy(null);
    }
  }

  const remaining = groups.reduce((n, g) => n + g.items.length, 0);
  const totalValue = groups.reduce((n, g) => n + g.total, 0);
  const paged = usePaged(groups, 25);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>
      {error && <Callout tone="neg">{error.message}</Callout>}
      {done > 0 && (
        <Callout tone="pos">
          Successfully batch-categorized {done} transactions. Learned rules will auto-apply to subsequent imports.
        </Callout>
      )}

      {/* Stats Header */}
      <div className="stats-grid">
        <Stat label="Distinct Entities" value={String(groups.length)} sub="Merchants awaiting category rule" />
        <Stat label="Pending Transactions" value={String(remaining)} sub="Unclassified line items" />
        <Stat label="Total Capital Value" value={money(totalValue)} tone="warn" sub="Aggregate unclassified outflow" />
      </div>

      {/* Control Bar */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-3)', alignItems: 'center' }}>
        <Segmented
          value={scope}
          onChange={setScope}
          ariaLabel="Filter scope"
          options={[
            ['uncategorized', 'Uncategorized'],
            ['review', 'Needs Review'],
            ['all', 'All Entries'],
          ]}
        />
        <Select value={sort} onChange={setSort} options={SORTS} aria-label="Sort order" style={{ minWidth: 200 }} />
        <Search
          value={search}
          onChange={setSearch}
          placeholder="Filter merchant names…"
          style={{ flex: 1, minWidth: 220 }}
        />
      </div>

      {loading && <Loading label="Aggregating merchant clusters…" />}

      {!loading && !groups.length && (
        <Empty
          icon="shield"
          title={scoped ? `Zero unclassified merchants in ${periodLabel}` : 'All merchants categorized'}
        >
          Every transaction in this view has a deterministic category.
        </Empty>
      )}

      {/* Merchant Cards */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
        {paged.slice.map((g) => (
          <GlassCard key={g.key} style={{ padding: 'var(--space-4)', opacity: busy === g.key ? 0.5 : 1 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 'var(--space-3)' }}>
              <div>
                <div style={{ fontSize: 'var(--text-md)', fontWeight: 600 }}>{g.key}</div>
                <div className="tiny muted" style={{ marginTop: 2 }}>
                  {g.items.length} transaction{g.items.length === 1 ? '' : 's'} · {money(g.total)} ·{' '}
                  <button
                    className="btn link"
                    onClick={() => setExpanded(expanded === g.key ? null : g.key)}
                  >
                    {expanded === g.key ? 'Hide rows' : 'Inspect rows'}
                  </button>
                </div>
              </div>

              <Select
                value=""
                placeholder={`Categorize all ${g.items.length}…`}
                disabled={busy === g.key}
                onChange={(v) => assign(g, v)}
                options={categories.map((c) => [c, titleCase(c)])}
                style={{ minWidth: 220 }}
              />
            </div>

            {expanded === g.key && (
              <div style={{ marginTop: 'var(--space-3)', paddingTop: 'var(--space-3)', borderTop: '1px solid var(--border-subtle)', maxHeight: 260, overflowY: 'auto' }}>
                <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Description</th>
                      <th style={{ textAlign: 'right' }}>Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {g.items.map((t) => (
                      <tr key={t.id} className="terminal-row">
                        <td className="nowrap muted tiny">{dateLabel(t.date)}</td>
                        <td className="font-medium">{t.description}</td>
                        <td className="num nowrap" style={{ textAlign: 'right' }}>{money(Math.abs(t.amount))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </GlassCard>
        ))}
      </div>

      {paged.hidden > 0 && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 'var(--space-3)' }}>
          <span className="tiny muted">
            Showing {count(paged.slice.length)} of {count(groups.length)} merchants
          </span>
          <div style={{ display: 'flex', gap: 6 }}>
            <Button size="sm" onClick={paged.more}>Show {Math.min(paged.hidden, 25)} more</Button>
            {paged.hidden > 25 && <Button size="sm" onClick={paged.all}>Show all {count(groups.length)}</Button>}
          </div>
        </div>
      )}
    </div>
  );
}
