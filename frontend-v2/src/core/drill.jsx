/* "Show me the rows behind that number."
 *
 * Every figure in this app is a sum over transactions, and the question people
 * ask of a figure they did not expect is always the same one. Answering it
 * without this means going to the Ledger, remembering the category, setting
 * the filter and matching the period by hand - four steps to ask "what is in
 * that?", which is enough friction that nobody asks.
 *
 * So a figure is a link. `drill({ title, params })` from anywhere opens the
 * rows `params` selects, in the period already on screen, with their own
 * total. `params` goes to /api/transactions unchanged, so anything the ledger
 * can filter on can be drilled into.
 *
 * Two properties this has to have to be worth trusting:
 *
 *   - It asks the SERVER, with the same period the figure was computed for.
 *     Filtering rows already in the browser would show one page of them and
 *     call it the answer.
 *   - It totals what it lists, and says when some of those rows are left out
 *     of the headline figures - an excluded row or a mirror leg is really
 *     there and really not counted, and hiding either would make this panel
 *     disagree with the number that opened it.
 */

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
  // Deliberately not throwing: a panel rendered outside the provider should
  // lose its drill-downs, not fail to render at all.
  return useContext(DrillContext) || { drill: () => {}, close: () => {}, request: null };
}

const ROW_H = 62;

function DrillSheet({ request, onClose }) {
  const { params: periodParams, label: periodLabel, scoped } = usePeriod();
  const toast = useToast();
  const scrollRef = useRef(null);
  const [saving, setSaving] = useState(null);

  /* The period is merged in unless the caller pinned its own window - a
     "compare with last month" figure has to keep the month it is about. */
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
      const res = await api.updateTransaction(txn.id, fields);
      const updated = res.transaction || { ...txn, ...fields };
      setQueryData(key, (prev) => ({
        ...prev,
        transactions: (prev?.transactions || []).map((r) => (r.id === txn.id ? updated : r)),
      }));
      // The figure that opened this sheet was computed from these rows, so it
      // is now stale. Dropping the analysis family is what makes the number
      // behind the sheet correct itself when the sheet closes.
      invalidate('analysis', 'dashboard', 'txns');
    } catch (e) {
      toast.fail('That change was not saved', e.message);
    } finally {
      setSaving(null);
    }
  }

  const net = sums.inflow - sums.outflow;
  const truncated = total > rows.length;

  function exportCsv() {
    downloadCsv(`${slug(request.title || 'transactions')}.csv`, toCsv(rows, [
      ['date', 'Date'],
      ['accounting_month', 'Counts in'],
      ['description', 'Description'],
      ['category', 'Category'],
      ['flow_role', 'Counts as'],
      [(r) => (r.direction === 'credit' ? r.amount : -r.amount), 'Amount'],
      ['note', 'Note'],
    ]));
  }

  const strip = rows.length > 0 && (
    <>
      {sums.outflow > 0 && <span><em>Out</em> <strong>{money(sums.outflow)}</strong></span>}
      {sums.inflow > 0 && <span><em>In</em> <strong>{money(sums.inflow)}</strong></span>}
      {sums.inflow > 0 && sums.outflow > 0 && (
        <span><em>Net</em> <strong className={net < 0 ? 'neg' : 'pos'}>{money(net)}</strong></span>
      )}
      <span className="spacer" />
      <button className="btn xs" onClick={exportCsv}>
        <Icon name="download" size={12} /> Export
      </button>
    </>
  );

  return (
    <Sheet
      title={request.title || 'Transactions'}
      sub={
        /* Which window these rows are from, always. The same figure in a
           different period is a different figure. */
        `${request.ignorePeriod ? (request.periodLabel || 'whole ledger')
          : (scoped ? periodLabel : 'all time')}`
        + (data ? ` · ${fmtCount(total)} transaction${total === 1 ? '' : 's'}` : '')
      }
      note={request.subtitle}
      onClose={onClose}
      strip={strip}
      tools={<IconButton icon="refresh" label="Refresh" className="ghost" onClick={refetch} />}
    >
      {sums.uncountedRows > 0 && (
        <div style={{ padding: '12px 16px 0' }}>
          <Callout tone="warn">
            {sums.uncountedRows} of these ({money(sums.uncounted)}) are excluded from your
            totals — a row you set aside, or the mirror leg of a transfer counted on its
            other side. They are listed because they really happened.
          </Callout>
        </div>
      )}

      {error && <div style={{ padding: 16 }}><Callout tone="neg">{error.message}</Callout></div>}
      {loading && <Loading label="Reading the rows…" />}

      {data && rows.length === 0 && (
        <div style={{ padding: 20 }}>
          <Empty title="Nothing here" icon="search">
            No transaction matches this, in this period.
          </Empty>
        </div>
      )}

      {rows.length > 0 && (
        <div ref={scrollRef} style={{ overflowY: 'auto', height: '100%' }}>
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Description</th>
                <th>Category</th>
                <th className="right">Amount</th>
                <th className="right">Edit</th>
              </tr>
            </thead>
            <VirtualBody count={rows.length} rowHeight={ROW_H}
              containerRef={scrollRef} columns={5}>
              {(i) => {
                const r = rows[i];
                return (
                  <tr key={r.id} style={{ opacity: r.excluded || r.is_mirror_leg ? 0.5 : 1 }}>
                    <td className="nowrap">
                      {dateLabel(r.date)}
                      {r.accounting_month
                        && r.accounting_month !== String(r.date).slice(0, 7) && (
                        <div className="tiny dim"
                          title="Counted in this month, not the month of the date">
                          counts in {r.accounting_month}
                        </div>
                      )}
                    </td>
                    <td>
                      <div style={{ lineHeight: 1.4, overflowWrap: 'anywhere' }}>
                        {r.description}
                      </div>
                      <div className="row tight" style={{ marginTop: 3 }}>
                        {r.excluded && <span className="chip warn">excluded</span>}
                        {r.needs_review && <span className="chip warn">needs review</span>}
                        {r.note && <span className="tiny dim">{r.note}</span>}
                      </div>
                    </td>
                    <td>
                      <Select
                        options={categories.map((c) => [c, titleCase(c)])}
                        value={r.category}
                        disabled={saving === r.id}
                        onChange={(cat) => cat !== r.category && patch(r, { category: cat })}
                        style={{ height: 26, fontSize: 12, maxWidth: 150 }}
                      />
                    </td>
                    <td className="right num nowrap"
                      style={{ color: r.direction === 'credit' ? 'var(--pos)' : 'inherit' }}>
                      {r.direction === 'credit' ? '+' : '−'}{money(Math.abs(r.amount))}
                    </td>
                    <td className="right nowrap">
                      <PromptButton
                        size="xs" className="ghost"
                        title={r.note ? 'Edit note' : 'Add a note'}
                        disabled={saving === r.id}
                        initial={r.note || ''}
                        placeholder="Note for this transaction"
                        onSubmit={(note) => patch(r, { note })}
                      >
                        <Icon name={r.note ? 'edit' : 'plus'} size={12} />
                      </PromptButton>
                      <button
                        className="btn ghost icon xs"
                        title={r.excluded
                          ? 'Put this back in your totals'
                          : 'Leave this out of every total'}
                        disabled={saving === r.id}
                        onClick={() => patch(r, { excluded: !r.excluded })}
                      >
                        <Icon name={r.excluded ? 'refresh' : 'slash'} size={12} />
                      </button>
                    </td>
                  </tr>
                );
              }}
            </VirtualBody>
          </table>
          {truncated && (
            <div style={{ padding: 14 }}>
              <Callout tone="warn">
                Showing the {rows.length} largest of {fmtCount(total)}. The Ledger pages
                through all of them.
              </Callout>
            </div>
          )}
        </div>
      )}
    </Sheet>
  );
}

/** A figure that opens its own rows. A button, not a div with a click handler:
    the point is that it is reachable by keyboard and announces itself. */
export function DrillLink({ onDrill, children, className = '', title, ...rest }) {
  if (!onDrill) return children;
  return (
    <button type="button" className={`drill ${className}`} onClick={onDrill}
      title={title || 'Show the transactions behind this'} {...rest}>
      {children}
    </button>
  );
}

