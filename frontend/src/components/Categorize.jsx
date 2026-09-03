import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Callout, Card, Chip, Empty, Stat } from './ui';
import { api, dateLabel, money, titleCase } from '../lib';
import { usePeriod } from '../period';

/* Bulk triage, grouped by merchant.
 *
 * Categorising one transaction at a time is the wrong unit of work: 476
 * unlabelled rows on this ledger were 285 distinct merchants, and most of
 * those repeat. Deciding once per merchant and applying it to every matching
 * row is roughly twenty times less clicking, and it is also what the learned
 * cache stores - so the same decision holds for next month's statement. */

const SORTS = [
  ['value', 'Largest first'],
  ['count', 'Most frequent'],
  ['name', 'Name'],
];

export default function Categorize() {
  const [rows, setRows] = useState(null);
  const [categories, setCategories] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [sort, setSort] = useState('value');
  const [scope, setScope] = useState('uncategorized');
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState(null);
  const [done, setDone] = useState(0);

  // Triage follows the app's period too: clearing a backlog is work anyone
  // does a month at a time.
  const { params: periodParams, label: periodLabel, scoped } = usePeriod();
  const periodKey = JSON.stringify(periodParams);

  const load = useCallback(() => {
    setRows(null);
    const params = { limit: 1000, ...periodParams };
    if (scope === 'uncategorized') params.category = 'uncategorized';
    if (scope === 'review') params.needs_review = true;
    Promise.all([api.transactions(params), api.categories()])
      .then(([txnRes, cats]) => {
        setRows(txnRes.transactions || []);
        setCategories(cats || []);
        setError(null);
      })
      .catch((e) => { setError(e.message); setRows([]); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, periodKey]);

  useEffect(load, [load]);

  // One entry per merchant, because that is the unit a decision applies to.
  const groups = useMemo(() => {
    if (!rows) return [];
    const byKey = new Map();
    for (const t of rows) {
      const key = (t.merchant || t.description || '').trim().toUpperCase().slice(0, 48)
        || '(no description)';
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
      // One request for the whole merchant. The backend also writes it to the
      // learned merchant cache, so next month's statement arrives already
      // categorised rather than back in this queue.
      await api.bulkUpdate(group.items.map((t) => t.id), { category });
      setRows((prev) => prev.filter(
        (t) => !group.items.some((g) => g.id === t.id)));
      setDone((n) => n + group.items.length);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  const remaining = groups.reduce((n, g) => n + g.items.length, 0);
  const remainingValue = groups.reduce((n, g) => n + g.total, 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 className="section-title" style={{ marginBottom: 4 }}>Categorize</h2>
        <p style={{ color: 'var(--text-2)', margin: 0 }}>
          Grouped by merchant, because one decision covers every transaction
          that shares it — and every future one too.
        </p>
      </div>

      {error && <Callout tone="neg">{error}</Callout>}
      {done > 0 && (
        <Callout tone="pos">
          {done} transaction{done === 1 ? '' : 's'} categorized this session.
          Each merchant is remembered, so it will not come back.
        </Callout>
      )}

      <div className="grid cols-3">
        <Stat label="Merchants to decide" value={String(groups.length)} />
        <Stat label="Transactions" value={String(remaining)} />
        <Stat label="Value" value={remainingValue} tone="warn" />
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <div className="seg">
          {[['uncategorized', 'Uncategorized'], ['review', 'Needs review'],
            ['all', 'Everything']].map(([v, label]) => (
              <button
                key={v}
                className={`seg-btn ${scope === v ? 'active' : ''}`}
                onClick={() => setScope(v)}
              >
                {label}
              </button>
          ))}
        </div>
        <select value={sort} onChange={(e) => setSort(e.target.value)}>
          {SORTS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
        <input
          placeholder="Filter merchants…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ flex: 1, minWidth: 180 }}
        />
      </div>

      {rows === null && <div className="spinner" style={{ margin: 40 }} />}

      {rows && !groups.length && (
        <Empty title={scoped ? `Nothing left to categorize in ${periodLabel}`
          : 'Nothing left to categorize'}>
          Every transaction in this view has a category. Switch the filter
          above{scoped && ', or widen the period,'} to review other groups.
        </Empty>
      )}

      {groups.map((g) => (
        <Card key={g.key}>
          <div style={{
            display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap',
            opacity: busy === g.key ? 0.5 : 1,
          }}
          >
            <div style={{ flex: 1, minWidth: 200 }}>
              <div style={{ fontWeight: 600 }}>{g.key}</div>
              <div style={{ color: 'var(--text-2)', fontSize: 13, marginTop: 2 }}>
                {g.items.length} transaction{g.items.length === 1 ? '' : 's'}
                {' · '}{money(g.total)}
                {' · '}
                <button
                  className="btn"
                  style={{
                    padding: 0, background: 'none', border: 'none',
                    color: 'var(--accent)', cursor: 'pointer', font: 'inherit',
                  }}
                  onClick={() => setExpanded(expanded === g.key ? null : g.key)}
                >
                  {expanded === g.key ? 'hide' : 'show'} transactions
                </button>
              </div>
            </div>

            <select
              defaultValue=""
              disabled={busy === g.key}
              onChange={(e) => assign(g, e.target.value)}
              style={{ minWidth: 190 }}
            >
              <option value="">Categorize all {g.items.length}…</option>
              {categories.map((c) => (
                <option key={c} value={c}>{titleCase(c)}</option>
              ))}
            </select>
          </div>

          {expanded === g.key && (
            <div className="table-wrap" style={{ marginTop: 12 }}>
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Description</th>
                    <th className="right">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {g.items.map((t) => (
                    <tr key={t.id}>
                      <td className="nowrap">{dateLabel(t.date)}</td>
                      <td>
                        <div style={{ wordBreak: 'break-word', whiteSpace: 'normal', lineHeight: '1.4' }}>
                          {t.description}
                        </div>
                      </td>
                      <td className="right num nowrap">{money(Math.abs(t.amount))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      ))}
    </div>
  );
}
