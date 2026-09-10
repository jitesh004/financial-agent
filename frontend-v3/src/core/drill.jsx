/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Drill-Down Provider & Slide-Out Transaction Sheet
   Clicking any number reveals the exact ledger rows behind it.
   ──────────────────────────────────────────────────────────────────────── */

import React, {
  createContext, useCallback, useContext, useMemo, useRef, useState,
} from 'react';
import { api } from './api';
import { useQuery, invalidate, setQueryData } from './store';
import { usePeriod } from './period';
import {
  count as fmtCount, dateLabel, downloadCsv, money, slug, titleCase, toCsv,
} from './format';
import { useToast } from './toast';
import { Callout, Empty, Icon, IconButton, Loading, PromptButton, Select, Sheet } from '../ui';
import { VirtualBody } from '../ui/virtual';

const DrillContext = createContext(null);

export function DrillProvider({ children }) {
  const [request, setRequest] = useState(null);
  const drill = useCallback((next) => setRequest(next || null), []);
  const close = useCallback(() => setRequest(null), []);
  const value = useMemo(() => ({ drill, close, request }), [drill, close, request]);

  return (
    <DrillContext.Provider value={value}>
      {children}
      {request && <DrillSheet request={request} onClose={close} />}
    </DrillContext.Provider>
  );
}

export function useDrill() {
  return useContext(DrillContext) || { drill: () => {}, close: () => {}, request: null };
}

const ROW_H = 64;

function DrillSheet({ request, onClose }) {
  const { params: periodParams, label: periodLabel, scoped } = usePeriod();
  const toast = useToast();
  const scrollRef = useRef(null);
  const [saving, setSaving] = useState(null);

  const query = useMemo(() => ({
    ...(request.ignorePeriod ? {} : periodParams),
    ...request.params,
    limit: 5000,
    sort_by: request.sortBy || 'amount',
    sort_dir: request.sortDir || 'desc',
  }), [request, periodParams]);

  const key = `drill:${JSON.stringify(query)}`;
  const { data, error, loading, refetch } = useQuery(key, () => api.transactions(query));
  const { data: categories = [] } = useQuery('categories', () => api.categories());
  const { data: accounts = [] } = useQuery('accounts', () => api.accounts());

  /* Rows reference an account by id only; resolve it so the sheet does not
     print the literal word "Account" against every line. */
  const accountName = useMemo(() => {
    const byId = new Map((accounts || []).map((a) => [a.id, a.display_name || a.institution]));
    return (row) => byId.get(row.account_id) || 'Unknown account';
  }, [accounts]);

  const rows = data?.transactions || [];
  const total = data?.total ?? rows.length;

  const sums = useMemo(() => {
    const out = { inflow: 0, outflow: 0, uncounted: 0, uncountedRows: 0 };
    for (const r of rows) {
      const amount = Math.abs(Number(r.amount) || 0);
      if (r.excluded || r.is_mirror_leg) {
        out.uncounted += amount;
        out.uncountedRows += 1;
      } else if (r.direction === 'credit') out.inflow += amount;
      else out.outflow += amount;
    }
    return out;
  }, [rows]);

  async function patch(txn, fields) {
    setSaving(txn.id);
    try {
      const updated = await api.updateTransaction(txn.id, fields);
      setQueryData(key, (curr) => ({
        ...curr,
        transactions: (curr?.transactions || []).map((t) => (t.id === txn.id ? { ...t, ...updated } : t)),
      }));
      invalidate('dashboard', 'analysis', 'budget', 'spending', 'workflow', 'coverage');
      toast.ok('Transaction updated');
    } catch (e) {
      toast.fail('Could not update', e.message);
    } finally {
      setSaving(null);
    }
  }

  function exportRows() {
    downloadCsv(`${slug(request.title || 'transactions')}.csv`, toCsv(rows, [
      ['date', 'Date'],
      [(r) => accountName(r), 'Account'],
      ['description', 'Description'],
      ['category', 'Category'],
      [(r) => (r.direction === 'credit' ? r.amount : -r.amount), 'Amount'],
      ['flow_role', 'Flow role'],
      ['excluded', 'Excluded'],
    ]));
  }

  const categoryOptions = useMemo(
    () => (categories || []).map((c) => [c.name || c, c.name || c]),
    [categories],
  );

  return (
    <Sheet
      open
      onClose={onClose}
      width={760}
      title={request.title || 'Transactions'}
      subtitle={request.subtitle || (scoped ? `Filtered to ${periodLabel}` : 'All time')}
      tools={
        <div className="flex items-center gap-2">
          <IconButton icon="download" label="Export CSV" onClick={exportRows} />
          <IconButton icon="refresh" label="Refresh" onClick={refetch} />
        </div>
      }
    >
      <div className="drill-summary-bar">
        <div className="drill-stat-item">
          <span className="drill-stat-label">Inflow</span>
          <span className="drill-stat-value pos tabular-nums">{money(sums.inflow)}</span>
        </div>
        <div className="drill-stat-item">
          <span className="drill-stat-label">Outflow</span>
          <span className="drill-stat-value neg tabular-nums">{money(sums.outflow)}</span>
        </div>
        <div className="drill-stat-item">
          <span className="drill-stat-label">Transactions</span>
          <span className="drill-stat-value tabular-nums">{fmtCount(total)}</span>
        </div>
        {sums.uncounted > 0 && (
          <div className="drill-stat-item">
            <span className="drill-stat-label">Excluded/Mirror</span>
            <span className="drill-stat-value warn tabular-nums">
              {money(sums.uncounted)} ({sums.uncountedRows})
            </span>
          </div>
        )}
      </div>

      {loading && <Loading message="Loading transaction records..." />}
      {error && <Callout tone="neg">{error.message}</Callout>}

      {!loading && rows.length === 0 && (
        <Empty title="No matching transactions" icon="rows">
          None of the rows in this period match this drill condition.
        </Empty>
      )}

      {!loading && rows.length > 0 && (
        <div className="drill-table-container" ref={scrollRef}>
          <VirtualBody
            as="div"
            items={rows}
            rowHeight={ROW_H}
            containerRef={scrollRef}
            renderRow={(r) => (
              <div key={r.id} className={`drill-row ${r.excluded ? 'excluded' : ''}`}>
                <div className="drill-col-date">
                  <span className="drill-date-val">{dateLabel(r.date)}</span>
                  <span className="drill-acc-name" title={accountName(r)}>{accountName(r)}</span>
                </div>
                <div className="drill-col-desc">
                  <div className="drill-desc-text" title={r.description}>{r.description}</div>
                  <div className="drill-desc-sub">
                    <span className="drill-cat-badge">{r.category || 'Uncategorized'}</span>
                    {r.flow_role && <span className="drill-role-tag">{r.flow_role}</span>}
                    {r.is_mirror_leg && <span className="chip warn">Mirror leg</span>}
                    {r.excluded && <span className="chip neg">Excluded</span>}
                  </div>
                </div>
                <div className="drill-col-amount">
                  <span className={`drill-amount-val tabular-nums ${r.direction === 'credit' ? 'pos' : 'neg'}`}>
                    {r.direction === 'credit' ? '+' : '-'}{money(r.amount, true)}
                  </span>
                </div>
                <div className="drill-col-action">
                  <Select
                    size="sm"
                    value={r.category || ''}
                    options={[['', 'Uncategorized'], ...categoryOptions]}
                    onChange={(cat) => patch(r, { category: cat })}
                    disabled={saving === r.id}
                  />
                </div>
              </div>
            )}
          />
        </div>
      )}
    </Sheet>
  );
}
