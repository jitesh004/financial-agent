/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Power Terminal Ledger
   High-density transactional register with faceted filters, slide-out inspector & bulk actions.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../core/api';
import { invalidate, useDebounced, useQuery } from '../core/store';
import { useRouteParam } from '../core/router';
import { usePeriod } from '../core/period';
import { usePrefs } from '../core/prefs';
import { useToast } from '../core/toast';
import { useAccounts, useCategories } from '../core/ledger';
import {
  count, dateLabel, downloadCsv, money, monthLabel, titleCase, toCsv, today,
} from '../core/format';
import {
  Button, Callout, Card, Empty, Field, IconButton, Loading, Search,
  Segmented, Select, Sheet, Modal,
} from '../ui';
import { VirtualBody } from '../ui/virtual';

const SAVINGS = new Set(['savings', 'current', 'wallet']);

const VIEWS = [
  { key: 'all', label: 'All Accounts', accounts: (all) => all },
  { key: 'bank', label: 'Bank & Savings', accounts: (all) => all.filter((a) => SAVINGS.has(a.account_type)) },
  { key: 'cards', label: 'Credit Cards', accounts: (all) => all.filter((a) => a.account_type === 'credit_card') },
  { key: 'upi', label: 'UPI Payments', accounts: (all) => all, fixedRail: 'upi' },
  { key: 'emi', label: 'EMI & Loans', accounts: (all) => all, fixedCategory: 'emi' },
];

export default function Ledger() {
  const toast = useToast();
  const { params: periodParams, paramsKey, label: periodLabel, scoped } = usePeriod();
  const [prefs, setPref] = usePrefs();
  const { data: accounts = [], loading: loadingAccounts } = useAccounts();
  const { data: categories = [] } = useCategories();

  const [view, setView] = useRouteParam('view', 'all');
  const [category, setCategory] = useRouteParam('cat', '');
  const [rail, setRail] = useRouteParam('rail', '');
  const [search, setSearch] = useRouteParam('q', '');
  const settledSearch = useDebounced(search);

  const [sortBy, setSortBy] = useState('date');
  const [sortDir, setSortDir] = useState('desc');
  const [page, setPage] = useState(0);
  const [picked, setPicked] = useState(() => new Set());
  const [saving, setSaving] = useState(null);
  const [inspectingTxn, setInspectingTxn] = useState(null);
  const [ruleExplanation, setRuleExplanation] = useState(null);
  const [splitOpen, setSplitOpen] = useState(false);
  const [splitParts, setSplitParts] = useState([{ amount: '', category: '' }]);

  const splitTotal = splitParts.reduce((sum, p) => sum + (Number(p.amount) || 0), 0);
  const splitBalanced = inspectingTxn
    ? Math.abs(splitTotal - Number(inspectingTxn.amount || 0)) < 0.01
    : false;

  const scrollRef = useRef(null);
  const activeView = VIEWS.find((v) => v.key === view) || VIEWS[0];
  const inScope = useMemo(() => activeView.accounts(accounts), [activeView, accounts]);
  const scopeIds = useMemo(() => inScope.map((a) => a.id), [inScope]);

  useEffect(() => { setPage(0); }, [view, category, rail, sortBy, sortDir, paramsKey, settledSearch]);

  const accountParam = scopeIds.length ? scopeIds.join(',') : '__none__';
  const pageSize = Number(prefs.pageSize) || 250;

  const query = useMemo(() => ({
    account_id: accountParam,
    category: activeView.fixedCategory || category || undefined,
    rail: activeView.fixedRail || rail || undefined,
    search: settledSearch || undefined,
    sort_by: sortBy,
    sort_dir: sortDir,
    offset: page * pageSize,
    limit: pageSize,
    ...periodParams,
  }), [accountParam, activeView, category, rail, settledSearch, sortBy, sortDir, page, pageSize, periodParams]);

  const ready = !loadingAccounts;
  const key = ready ? `txns:${JSON.stringify(query)}` : null;
  const { data, loading, fetching, error, refetch } =
    useQuery(key, () => api.transactions(query), { enabled: ready });

  const rows = data?.transactions || [];
  const total = data?.total ?? 0;
  const pages = Math.ceil(total / pageSize) || 1;

  const visibleRows = useMemo(
    () => (prefs.hideExcluded ? rows.filter((r) => !r.excluded) : rows),
    [rows, prefs.hideExcluded],
  );

  const rowH = prefs.density === 'compact' ? 44 : prefs.density === 'spacious' ? 68 : 54;

  const inspect = async (txn) => {
    setInspectingTxn(txn);
    setRuleExplanation(null);
    try {
      const exp = await api.explainTransaction(txn.id);
      setRuleExplanation(exp);
    } catch {}
  };

  const patch = async (txn, fields) => {
    setSaving(txn.id);
    try {
      await api.updateTransaction(txn.id, fields);
      invalidate('txns', 'analysis', 'dashboard', 'workflow');
      await refetch();
      if (inspectingTxn?.id === txn.id) {
        setInspectingTxn((prev) => ({ ...prev, ...fields }));
      }
      toast.ok('Transaction updated');
    } catch (e) {
      toast.fail('Update failed', e.message);
    } finally {
      setSaving(null);
    }
  };

  const applyBulk = async (fields) => {
    const ids = [...picked];
    if (!ids.length) return;
    setSaving('bulk');
    try {
      await api.bulkUpdate(ids, fields);
      setPicked(new Set());
      invalidate('txns', 'analysis', 'dashboard', 'workflow');
      await refetch();
      toast.ok(`${ids.length} transactions updated`);
    } catch (e) {
      toast.fail('Bulk update failed', e.message);
    } finally {
      setSaving(null);
    }
  };

  const togglePick = (id) => {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (picked.size === visibleRows.length) setPicked(new Set());
    else setPicked(new Set(visibleRows.map((r) => r.id)));
  };

  const exportCurrent = () => {
    downloadCsv(`transactions-${today()}.csv`, toCsv(visibleRows, [
      ['date', 'Date'],
      [(r) => accountName(r), 'Account'],
      ['description', 'Description'],
      ['category', 'Category'],
      [(r) => (r.direction === 'credit' ? r.amount : -r.amount), 'Amount'],
      ['flow_role', 'Role'],
      [(r) => r.balance_after ?? '', 'Balance'],
      ['excluded', 'Excluded'],
    ]));
  };

  const categoryOptions = useMemo(
    () => (categories || []).map((c) => [c.name || c, c.name || c]),
    [categories],
  );

  const accountName = useMemo(() => {
    const byId = new Map(accounts.map((a) => [a.id, a.display_name || a.institution]));
    return (row) => byId.get(row.account_id) || 'Unknown account';
  }, [accounts]);

  return (
    <div className="flex-col gap-4 animate-fade-in">
      {/* 1. View Tabs & Filter Bar */}
      <div className="glass-card" style={{ padding: '14px 20px' }}>
        <div className="flex items-center justify-between flex-wrap gap-3">
          {/* View Presets */}
          <Segmented
            ariaLabel="Ledger Views"
            value={view}
            onChange={setView}
            options={VIEWS.map((v) => [v.key, v.label])}
          />

          {/* Quick Filters */}
          <div className="flex items-center gap-2 flex-wrap">
            <div style={{ width: 220 }}>
              <Search
                value={search}
                onChange={setSearch}
                placeholder="Search merchant, narration..."
              />
            </div>

            <Select
              size="sm"
              value={category}
              onChange={setCategory}
              placeholder="All categories"
              options={[['', 'All categories'], ...categoryOptions]}
            />

            <Select
              size="sm"
              value={prefs.density || 'comfortable'}
              onChange={(v) => setPref('density', v)}
              options={[
                ['spacious', 'Spacious'],
                ['comfortable', 'Comfortable'],
                ['compact', 'Compact'],
              ]}
            />

            <IconButton icon="download" label="Export CSV" onClick={exportCurrent} />
            <IconButton icon="refresh" label="Refresh" onClick={refetch} />
          </div>
        </div>
      </div>

      {/* 2. Floating Bulk Action Toolbar */}
      {picked.size > 0 && (
        <div className="glass-card flex items-center justify-between" style={{
          padding: '10px 18px', background: 'var(--accent-soft)', border: '1px solid var(--accent-border)',
        }}>
          <div className="flex items-center gap-2">
            <span className="badge badge-accent font-bold tabular-nums">{picked.size}</span>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent-text)' }}>
              transactions selected
            </span>
          </div>

          <div className="flex items-center gap-2">
            <Select
              size="sm"
              placeholder="Assign category..."
              onChange={(cat) => cat && applyBulk({ category: cat })}
              options={categoryOptions}
            />
            <Button size="xs" variant="secondary" onClick={() => applyBulk({ excluded: true })}>
              Exclude
            </Button>
            <Button size="xs" variant="secondary" onClick={() => applyBulk({ needs_review: false })}>
              Mark Reviewed
            </Button>
            <Button size="xs" variant="ghost" onClick={() => setPicked(new Set())}>
              Clear
            </Button>
          </div>
        </div>
      )}

      {/* 3. Transaction Terminal Table */}
      <Card pad={false}>
        {loading && <Loading message="Querying transactions..." />}
        {error && <Callout tone="neg">{error.message}</Callout>}

        {!loading && visibleRows.length === 0 && (
          <Empty title="No transactions match" icon="rows">
            Try adjusting your search query, filters, or selected date period.
          </Empty>
        )}

        {!loading && visibleRows.length > 0 && (
          <div className="table-wrapper" ref={scrollRef} style={{ maxHeight: 'calc(100vh - 280px)', overflow: 'auto' }}>
            <table className={`terminal-table ${prefs.density}`}>
              <thead>
                <tr>
                  <th style={{ width: 36, textAlign: 'center' }}>
                    <input
                      type="checkbox"
                      checked={picked.size === visibleRows.length && visibleRows.length > 0}
                      onChange={toggleAll}
                    />
                  </th>
                  <th style={{ width: 95 }}>Date</th>
                  <th style={{ width: 140 }}>Account</th>
                  <th>Description & Narration</th>
                  <th style={{ width: 160 }}>Category</th>
                  <th style={{ width: 120, textAlign: 'right' }}>Amount</th>
                  {prefs.showBalance && <th style={{ width: 110, textAlign: 'right' }}>Balance</th>}
                  <th style={{ width: 44 }} />
                </tr>
              </thead>
              <VirtualBody
                count={visibleRows.length}
                rowHeight={rowH}
                containerRef={scrollRef}
                columns={8}
                children={(i) => {
                  const r = visibleRows[i];
                  if (!r) return null;
                  const isCredit = r.direction === 'credit';
                  const isSelected = picked.has(r.id);

                  return (
                    <tr
                      key={r.id}
                      onClick={() => inspect(r)}
                      style={{
                        cursor: 'pointer',
                        background: isSelected ? 'var(--accent-soft)' : undefined,
                        opacity: r.excluded ? 0.5 : 1,
                      }}
                    >
                      <td style={{ textAlign: 'center' }} onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => togglePick(r.id)}
                        />
                      </td>
                      <td className="tabular-nums" style={{ color: 'var(--text-3)' }}>
                        {dateLabel(r.date)}
                      </td>
                      <td>
                        <span
                          className="truncate block"
                          style={{ maxWidth: 130, fontWeight: 500 }}
                          title={accountName(r)}
                        >
                          {accountName(r)}
                        </span>
                      </td>
                      <td>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-1 truncate" style={{ maxWidth: 360 }}>
                            {r.description}
                          </span>
                          {r.is_mirror_leg && <span className="chip chip-warn">Mirror</span>}
                          {r.excluded && <span className="chip chip-neg">Excluded</span>}
                          {r.needs_review && <span className="beacon-warn" title="Needs review" />}
                        </div>
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <Select
                          size="sm"
                          value={r.category || ''}
                          options={[['', 'Uncategorized'], ...categoryOptions]}
                          onChange={(cat) => patch(r, { category: cat })}
                          disabled={saving === r.id}
                          style={{ maxWidth: 150 }}
                        />
                      </td>
                      <td className={`tabular-nums text-right font-bold ${isCredit ? 'pos' : 'neg'}`}>
                        {isCredit ? '+' : '-'}{money(r.amount, true)}
                      </td>
                      {prefs.showBalance && (
                        <td className="tabular-nums text-right text-3 text-xs">
                          {r.balance_after != null ? money(r.balance_after) : '—'}
                        </td>
                      )}
                      <td style={{ textAlign: 'center' }}>
                        <IconButton
                          icon="chevron"
                          label="Inspect"
                          onClick={(e) => { e.stopPropagation(); inspect(r); }}
                        />
                      </td>
                    </tr>
                  );
                }}
              />
            </table>
          </div>
        )}

        {/* Pagination Footer */}
        <div className="card-head flex items-center justify-between" style={{ padding: '12px 20px', background: 'var(--surface-2)' }}>
          <div className="text-xs text-3">
            Showing <strong className="tabular-nums">{visibleRows.length}</strong> of <strong className="tabular-nums">{count(total)}</strong> transactions
          </div>
          {pages > 1 && (
            <div className="flex items-center gap-2">
              <Button size="xs" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>
                Previous
              </Button>
              <span className="text-xs text-3 tabular-nums">
                Page {page + 1} of {pages}
              </span>
              <Button size="xs" disabled={page >= pages - 1} onClick={() => setPage((p) => p + 1)}>
                Next
              </Button>
            </div>
          )}
        </div>
      </Card>

      {/* 4. Slide-Out Transaction Inspector Sheet */}
      {inspectingTxn && (
        <Sheet
          open
          onClose={() => setInspectingTxn(null)}
          title="Transaction Inspector"
          subtitle={dateLabel(inspectingTxn.date)}
          width={580}
        >
          <div className="flex-col gap-4">
            {/* Amount & Direct Actions */}
            <div className="glass-card pad flex items-center justify-between">
              <div>
                <div style={{ fontSize: 12, color: 'var(--text-3)', fontWeight: 600 }}>TRANSACTION AMOUNT</div>
                <div className={`tabular-nums font-extrabold ${inspectingTxn.direction === 'credit' ? 'pos' : 'neg'}`} style={{ fontSize: 26 }}>
                  {inspectingTxn.direction === 'credit' ? '+' : '-'}{money(inspectingTxn.amount, true)}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant={inspectingTxn.excluded ? 'primary' : 'secondary'}
                  onClick={() => patch(inspectingTxn, { excluded: !inspectingTxn.excluded })}
                >
                  {inspectingTxn.excluded ? 'Restore to totals' : 'Exclude from totals'}
                </Button>
              </div>
            </div>

            {/* Narration & Account Details */}
            <Card pad title="Statement Record">
              <div className="flex-col gap-2 text-xs">
                <div>
                  <span className="text-3 block">Raw Narration:</span>
                  <div className="mono" style={{ padding: '8px 12px', background: 'var(--surface-2)', borderRadius: 'var(--r-xs)', marginTop: 4 }}>
                    {inspectingTxn.description}
                  </div>
                </div>
                <div className="grid-2" style={{ marginTop: 6 }}>
                  <div>
                    <span className="text-3">Account:</span>
                    <strong className="block text-1">{accountName(inspectingTxn)}</strong>
                  </div>
                  <div>
                    <span className="text-3">Running Balance:</span>
                    <strong className="block tabular-nums">{money(inspectingTxn.balance_after)}</strong>
                  </div>
                </div>
              </div>
            </Card>

            {/* Categorization & Rule Inspector */}
            <Card pad title="Categorization Logic">
              <div className="flex-col gap-3">
                <Field label="Category Assignment">
                  <Select
                    value={inspectingTxn.category || ''}
                    options={[['', 'Uncategorized'], ...categoryOptions]}
                    onChange={(cat) => patch(inspectingTxn, { category: cat })}
                  />
                </Field>

                {ruleExplanation && (
                  <div style={{ padding: '10px 14px', background: 'var(--surface-2)', borderRadius: 'var(--r-sm)', fontSize: 12.5 }}>
                    <div style={{ fontWeight: 700, color: 'var(--text-1)', marginBottom: 4 }}>
                      Rule Match Reason:
                    </div>
                    <div style={{ color: 'var(--text-2)', lineHeight: 1.5 }}>
                      {ruleExplanation.reason || ruleExplanation.explanation || 'Categorized via heuristics engine.'}
                    </div>
                  </div>
                )}
              </div>
            </Card>

            {/* Split Transaction Shortcut */}
            <Card pad title="Split Transaction">
              <p style={{ fontSize: 13, color: 'var(--text-2)' }}>
                Divide this transaction into multiple category buckets with sub-amounts.
              </p>
              <Button
                size="sm"
                variant="secondary"
                icon="split"
                onClick={() => {
                  setSplitParts([
                    { amount: String(Math.round(inspectingTxn.amount / 2)), category: inspectingTxn.category || '' },
                    { amount: String(Math.round(inspectingTxn.amount / 2)), category: '' },
                  ]);
                  setSplitOpen(true);
                }}
              >
                Configure Split
              </Button>
            </Card>
          </div>
        </Sheet>
      )}

      {/* Split Modal */}
      <Modal open={splitOpen} onClose={() => setSplitOpen(false)} title="Split Transaction Parts">
        <div className="flex-col gap-3">
          <p style={{ fontSize: 13, color: 'var(--text-2)' }}>
            Total amount to allocate: <strong>{money(inspectingTxn?.amount)}</strong>
          </p>
          <div className={`tiny ${splitBalanced ? 'pos' : 'warn'}`}>
            Allocated {money(splitTotal)} of {money(inspectingTxn?.amount ?? 0)}
            {splitBalanced ? ' — balanced' : ` — ${money(Math.abs((inspectingTxn?.amount || 0) - splitTotal))} unallocated`}
          </div>
          {splitParts.map((part, idx) => (
            <div key={idx} className="grid-2 gap-2">
              <input
                type="number"
                placeholder="Amount"
                value={part.amount}
                onChange={(e) => {
                  const val = e.target.value;
                  setSplitParts((prev) => prev.map((p, i) => (i === idx ? { ...p, amount: val } : p)));
                }}
                style={{ padding: '6px 10px', borderRadius: 'var(--r-xs)', border: '1px solid var(--line)' }}
              />
              <Select
                size="sm"
                value={part.category}
                options={[['', 'Choose category'], ...categoryOptions]}
                onChange={(cat) => {
                  setSplitParts((prev) => prev.map((p, i) => (i === idx ? { ...p, category: cat } : p)));
                }}
              />
            </div>
          ))}
          <div className="flex items-center justify-between" style={{ marginTop: 10 }}>
            <Button
              size="xs"
              variant="ghost"
              onClick={() => setSplitParts((p) => [...p, { amount: '', category: '' }])}
            >
              + Add another part
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={!splitBalanced}
              onClick={async () => {
                try {
                  await api.splitTransaction(
                    inspectingTxn.id,
                    splitParts.map((p) => ({ amount: Number(p.amount), category: p.category })),
                  );
                  setSplitOpen(false);
                  setInspectingTxn(null);
                  invalidate('txns', 'analysis', 'dashboard', 'workflow');
                  refetch();
                  toast.ok('Transaction split saved');
                } catch (e) {
                  toast.fail('Split failed', e.message);
                }
              }}
            >
              Save Split
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
