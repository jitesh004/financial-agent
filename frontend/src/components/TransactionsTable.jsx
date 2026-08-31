import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api, dateLabel, money, titleCase } from '../lib';
import { downloadCsv, toCsv, usePrefs } from '../prefs';
import { Callout, Card, Chip, Empty, PromptButton } from './ui';

const PAGE = 100;

/* Where a category came from. Shown so a user can tell a hard rule from a model
   guess and knows which ones are worth checking. */
const SOURCE_TONE = {
  rule: '', merchant_cache: '', llm: 'warn', user: 'pos', default: 'warn',
};
const SOURCE_LABEL = {
  rule: 'rule', merchant_cache: 'learned', llm: 'AI', user: 'you', default: 'guess',
};

const SORT_FIELDS = [
  ['date', 'Date'],
  ['amount', 'Amount'],
  ['balance', 'Balance'],
];

/**
 * The one filterable, sortable transaction table every tab is built from.
 *
 * `accounts` is the pool this view is allowed to show - the full list for the
 * general "Transactions" tab, or a pre-filtered subset (savings accounts only,
 * cards only) for a scoped tab. Every account in that pool starts selected;
 * the chip row lets the user narrow to one or a few without leaving the tab.
 *
 * `fixedRail` / `fixedCategory` lock a filter a scoped tab already implies
 * (UPI-only, EMI-only) so it can't be accidentally cleared, and hide the
 * control for it entirely rather than showing a redundant, disabled picker.
 */
export default function TransactionsTable({
  accounts,
  title = 'Transactions',
  showRailToggle = false,
  fixedRail = null,
  fixedCategory = null,
  emptyHint = 'Try clearing the filters.',
}) {
  const allIds = useMemo(() => accounts.map((a) => a.id), [accounts]);
  const [selected, setSelected] = useState(() => new Set(allIds));
  // A newly-arrived account (e.g. after a retry) should default to selected,
  // and one that vanished should not leave a stale id in the filter.
  useEffect(() => {
    setSelected((prev) => {
      const next = new Set(allIds.filter((id) => prev.has(id) || !prev.size));
      return next.size ? next : new Set(allIds);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allIds.join(',')]);

  const [rows, setRows] = useState([]);
  const [categories, setCategories] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [category, setCategory] = useState(fixedCategory || '');
  const [rail, setRail] = useState(fixedRail || '');
  const [sortBy, setSortBy] = useState('date');
  const [sortDir, setSortDir] = useState('desc');
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(null);
  // Row selection for bulk edits. Distinct from `selected`, which is the
  // set of ACCOUNTS in scope for this view.
  const [picked, setPicked] = useState(() => new Set());
  const [bulkCat, setBulkCat] = useState('');
  const [prefs] = usePrefs();
  const pageSize = Number(prefs.pageSize) || PAGE;
  const compact = prefs.density === 'compact';

  useEffect(() => { api.categories().then(setCategories).catch(() => {}); }, []);

  const accountParam = useMemo(() => {
    // Always send an explicit list - never omit the filter just because every
    // account IN THIS VIEW is selected. `accounts` is often a pre-filtered
    // subset (Savings, Cards), and "all of my subset" is not "no filter at
    // all": omitting it let a scoped tab silently show every OTHER account's
    // transactions too, which is worse than the query being a few ids longer.
    // Zero selected must return zero rows for the same reason "Clear all"
    // implies emptiness, not everything - no real account id equals this.
    if (selected.size === 0) return '__none__';
    return [...selected].join(',');
  }, [selected]);

  useEffect(() => { setPage(0); }, [accountParam, category, rail, sortBy, sortDir]);

  // A named loader, so a bulk edit can refresh without duplicating the query.
  const [reloadToken, setReloadToken] = useState(0);
  const load = useCallback(() => setReloadToken((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.transactions({
      account_id: accountParam || undefined,
      category: fixedCategory || category || undefined,
      rail: fixedRail || rail || undefined,
      sort_by: sortBy, sort_dir: sortDir,
      limit: pageSize, offset: page * pageSize,
    })
      .then((res) => {
        if (cancelled) return;
        setRows(res.transactions);
        setTotal(res.total);
        setError(null);
      })
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [accountParam, category, rail, sortBy, sortDir, page, fixedCategory,
      fixedRail, pageSize, reloadToken]);

  // Search filters the loaded page only. Pushing it server-side would be the
  // next step; for now the UI says so rather than pretending it searched all.
  const visible = useMemo(() => {
    const base = prefs.hideExcluded ? rows.filter((r) => !r.excluded) : rows;
    if (!search.trim()) return base;
    const needle = search.toLowerCase();
    return base.filter((r) =>
      r.description.toLowerCase().includes(needle) ||
      (r.merchant || '').toLowerCase().includes(needle));
  }, [rows, search, prefs.hideExcluded]);

  // One place for every per-row edit, so the optimistic update and the error
  // handling are not written out once per action.
  async function patch(txn, fields) {
    setSaving(txn.id);
    try {
      const res = await api.updateTransaction(txn.id, fields);
      const updated = res.transaction || { ...txn, ...fields };
      setRows((prev) => prev.map((r) => (r.id === txn.id ? updated : r)));
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(null);
    }
  }

  async function addNote(txn, note) {
    await patch(txn, { note });
  }

  async function markNotMine(txn, who) {
    if (!who) return;
    setSaving(txn.id);
    try {
      await api.claimTransaction(txn.id, {
        counterparty: who, direction: 'owed_to_me', amount: txn.amount,
      });
      setRows((prev) => prev.map(
        (r) => (r.id === txn.id ? { ...r, excluded: true } : r)));
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(null);
    }
  }

  function togglePicked(id) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function applyBulk(fields) {
    const ids = [...picked];
    if (!ids.length) return;
    setSaving('bulk');
    try {
      await api.bulkUpdate(ids, fields);
      // Re-fetched rather than patched in place: a bulk category change also
      // teaches the merchant cache, which can move rows this view is
      // filtering on.
      setPicked(new Set());
      setBulkCat('');
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(null);
    }
  }

  function exportCsv() {
    const columns = [
      ['date', 'Date'],
      ['description', 'Description'],
      [(r) => accountName(r.account_id), 'Account'],
      ['category', 'Category'],
      ['flow_role', 'Counts as'],
      [(r) => (r.direction === 'credit' ? r.amount : -r.amount), 'Amount'],
      ['balance_after', 'Balance'],
      ['note', 'Note'],
    ];
    downloadCsv(`transactions-${new Date().toISOString().slice(0, 10)}.csv`,
                toCsv(visible, columns));
  }

  async function recategorize(txn, cat) {
    if (cat === txn.category) return;
    setSaving(txn.id);
    try {
      const res = await api.recategorize(txn.id, cat);
      setRows((prev) => prev.map((r) => (r.id === txn.id ? res.transaction : r)));
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(null);
    }
  }

  function toggleAccount(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next.size ? next : new Set(allIds); // never let the filter go empty
    });
  }

  const pages = Math.ceil(total / pageSize) || 1;
  const accountName = (id) => accounts.find((a) => a.id === id)?.display_name || '—';
  const allSelected = selected.size === accounts.length;

  return (
    <>
      <div className="section-title">
        {title} {total > 0 && `· ${total.toLocaleString('en-IN')} total`}
      </div>

      {accounts.length > 1 && (
        <Card style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-2)' }}>
              Accounts
            </span>
            <button
              className="btn"
              style={{ padding: '2px 8px', fontSize: 11 }}
              onClick={() => setSelected(allSelected ? new Set() : new Set(allIds))}
            >
              {allSelected ? 'Clear all' : 'Select all'}
            </button>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {accounts.map((a) => (
              <button
                key={a.id}
                className={`chip-toggle ${selected.has(a.id) ? 'selected' : ''}`}
                onClick={() => toggleAccount(a.id)}
                title={a.display_name}
              >
                {a.display_name}
              </button>
            ))}
          </div>
        </Card>
      )}

      <Card>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12, alignItems: 'center' }}>
          {!fixedCategory && (
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">All categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>{titleCase(c)}</option>
              ))}
            </select>
          )}

          {showRailToggle && !fixedRail && (
            <div className="seg">
              {[['', 'All'], ['upi', 'UPI'], ['non_upi', 'Other']].map(([v, label]) => (
                <button
                  key={v || 'all'}
                  className={`seg-btn ${rail === v ? 'active' : ''}`}
                  onClick={() => setRail(v)}
                >
                  {label}
                </button>
              ))}
            </div>
          )}

          <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
            {SORT_FIELDS.map(([v, label]) => (
              <option key={v} value={v}>Sort: {label}</option>
            ))}
          </select>
          <button
            className="btn icon"
            title={sortDir === 'asc' ? 'Ascending' : 'Descending'}
            onClick={() => setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))}
          >
            {sortDir === 'asc' ? '↑' : '↓'}
          </button>

          <input
            type="search"
            placeholder="Filter this page…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ flex: 1, minWidth: 180 }}
          />

          <button
            className="btn"
            onClick={exportCsv}
            disabled={!visible.length}
            title="Download what is currently shown, as CSV"
          >
            Export
          </button>

          {(category || rail || search || !allSelected) && (
            <button
              className="btn"
              onClick={() => {
                if (!fixedCategory) setCategory('');
                if (!fixedRail) setRail('');
                setSearch('');
                setSelected(new Set(allIds));
              }}
            >
              Clear
            </button>
          )}
        </div>

        {error && <Callout tone="neg">{error}</Callout>}

        {loading ? (
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', padding: 24 }}>
            <div className="spinner" /> Loading transactions…
          </div>
        ) : visible.length === 0 ? (
          <Empty title="No transactions match">{emptyHint}</Empty>
        ) : (
          // Fragment: this branch renders the bulk bar AND the table, and a
          // ternary arm takes one expression.
          <>
          {/* Appears only with a selection, so it costs no space otherwise. */}
          {picked.size > 0 && (
            <div
              className="card"
              style={{
                display: 'flex', gap: 10, alignItems: 'center',
                flexWrap: 'wrap', padding: 10, marginBottom: 10,
                background: 'var(--accent-soft, var(--surface-2))',
              }}
            >
              <strong>{picked.size} selected</strong>
              <select
                value={bulkCat}
                onChange={(e) => { setBulkCat(e.target.value);
                                   applyBulk({ category: e.target.value }); }}
                disabled={saving === 'bulk'}
              >
                <option value="">Set category…</option>
                {categories.map((c) => (
                  <option key={c} value={c}>{titleCase(c)}</option>
                ))}
              </select>
              <button
                className="btn"
                disabled={saving === 'bulk'}
                onClick={() => applyBulk({ excluded: true })}
              >
                Exclude from totals
              </button>
              <button
                className="btn"
                disabled={saving === 'bulk'}
                onClick={() => applyBulk({ excluded: false })}
              >
                Include
              </button>
              <PromptButton
                className="btn"
                disabled={saving === 'bulk'}
                placeholder="Note for all selected"
                onSubmit={(note) => applyBulk({ note })}
              >
                Add note
              </PromptButton>
              <div style={{ flex: 1 }} />
              <button className="btn" onClick={() => setPicked(new Set())}>
                Clear selection
              </button>
            </div>
          )}

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style={{ width: 28 }}>
                    <input
                      type="checkbox"
                      aria-label="Select every row on this page"
                      checked={visible.length > 0 && picked.size === visible.length}
                      onChange={(e) => setPicked(
                        e.target.checked ? new Set(visible.map((r) => r.id)) : new Set())}
                    />
                  </th>
                  <th>Date</th>
                  <th>Description</th>
                  <th>Account</th>
                  <th>Category</th>
                  {prefs.showRole && <th>Counts as</th>}
                  <th className="right">Amount</th>
                  {prefs.showBalance && <th className="right">Balance</th>}
                  <th className="right">Edit</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((t) => (
                  <tr
                    key={t.id}
                    style={{
                      opacity: t.excluded ? 0.45 : 1,
                      lineHeight: compact ? 1.15 : undefined,
                    }}
                  >
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Select ${t.description}`}
                        checked={picked.has(t.id)}
                        onChange={() => togglePicked(t.id)}
                      />
                    </td>
                    <td className="nowrap">{dateLabel(t.date)}</td>
                    <td>
                      <div style={{ wordBreak: 'break-word', whiteSpace: 'normal', lineHeight: '1.4' }}>{t.description}</div>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
                        {t.is_internal_transfer && (
                          <Chip tone="accent">
                            {t.is_mirror_leg ? 'transfer (mirror)' : 'transfer'}
                          </Chip>
                        )}
                        {t.excluded && <Chip tone="warn">excluded</Chip>}
                        {t.needs_review && <Chip tone="warn">needs review</Chip>}
                      </div>
                      {t.note && (
                        <div style={{ color: 'var(--text-3)', fontSize: 12, marginTop: 2 }}>
                          {t.note}
                        </div>
                      )}
                    </td>
                    <td className="nowrap" style={{ color: 'var(--text-3)', fontSize: 12 }}>
                      {accountName(t.account_id)}
                    </td>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <select
                          value={t.category}
                          disabled={saving === t.id}
                          onChange={(e) => recategorize(t, e.target.value)}
                          style={{ fontSize: 12, padding: '3px 6px', maxWidth: 150 }}
                        >
                          {categories.map((c) => (
                            <option key={c} value={c}>{titleCase(c)}</option>
                          ))}
                        </select>
                        {prefs.showSource && (
                          <Chip tone={SOURCE_TONE[t.category_source]}>
                            {SOURCE_LABEL[t.category_source] || t.category_source}
                          </Chip>
                        )}
                      </div>
                    </td>
                    {prefs.showRole && (
                      <td className="nowrap">
                        <Chip>
                          {titleCase((t.flow_role || 'unclassified').replace(/_/g, ' '))}
                        </Chip>
                      </td>
                    )}
                    <td className="right num nowrap" style={{
                      color: t.direction === 'credit' ? 'var(--positive)' : 'inherit',
                      fontWeight: 550,
                    }}>
                      {t.direction === 'credit' ? '+' : '−'}{money(t.amount, true)}
                    </td>
                    {prefs.showBalance && (
                      <td className="right num nowrap" style={{ color: 'var(--text-3)' }}>
                        {t.balance_after != null ? money(t.balance_after) : '—'}
                      </td>
                    )}
                    <td className="right nowrap">
                      <PromptButton
                        className="btn icon"
                        title={t.note ? 'Edit note' : 'Add a note'}
                        disabled={saving === t.id}
                        initial={t.note || ''}
                        placeholder="Note for this transaction"
                        onSubmit={(note) => addNote(t, note)}
                      >
                        {t.note ? '✎' : '+'}
                      </PromptButton>
                      <button
                        className="btn icon"
                        title={t.excluded
                          ? 'Put this back in your totals'
                          : 'Leave this out of every total'}
                        disabled={saving === t.id}
                        onClick={() => patch(t, { excluded: !t.excluded })}
                      >
                        {t.excluded ? '↺' : '⊘'}
                      </button>
                      {t.direction === 'debit' && !t.is_internal_transfer && (
                        <PromptButton
                          className="btn icon"
                          title="This purchase was not mine - track it as owed to me"
                          disabled={saving === t.id}
                          placeholder="Whose expense was this?"
                          submitLabel="Mark owed"
                          onSubmit={(who) => markNotMine(t, who)}
                        >
                          ⇄
                        </PromptButton>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </>
        )}

        {pages > 1 && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 14 }}>
            <button className="btn" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
              Previous
            </button>
            <span style={{ fontSize: 13, color: 'var(--text-2)' }}>
              Page {page + 1} of {pages}
            </span>
            <button className="btn" disabled={page >= pages - 1} onClick={() => setPage((p) => p + 1)}>
              Next
            </button>
          </div>
        )}

        <Callout style={{ marginTop: 12 }}>
          Changing a category also teaches the merchant permanently — every future
          statement with that merchant will use your choice instead of a rule or
          model guess.
        </Callout>
      </Card>
    </>
  );
}
