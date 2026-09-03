import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { api, count as fmtCount, dateLabel, money, titleCase } from './lib';
import { usePeriod } from './period';
import { downloadCsv, toCsv } from './prefs';
import { PromptButton } from './components/ui';

/* "Show me the rows behind that number."
 *
 * Every figure in this app is a sum over transactions, and the question people
 * ask of a figure they did not expect is always the same one. Answering it
 * used to mean going to the Ledger, remembering the category, setting the
 * filter and matching the period by hand - four steps to ask "what is in
 * that?", which is enough friction that nobody asks.
 *
 * So a figure is a link. `drill({ title, params })` from anywhere opens the
 * rows `params` selects, in the period already on screen, with their own
 * total. `params` goes to /api/transactions unchanged, so anything the ledger
 * can filter on can be drilled into: a category, a group of them, a merchant,
 * a month, a flow role, an account.
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

const DrillContext = createContext(null);

const PAGE = 200;

export function DrillProvider({ children }) {
  const [request, setRequest] = useState(null);

  const drill = useCallback((next) => setRequest(next || null), []);
  const close = useCallback(() => setRequest(null), []);

  const value = useMemo(() => ({ drill, close, request }), [drill, close, request]);

  return (
    <DrillContext.Provider value={value}>
      {children}
      {request && <DrillPanel request={request} onClose={close} />}
    </DrillContext.Provider>
  );
}

export function useDrill() {
  const value = useContext(DrillContext);
  // Deliberately not throwing: a panel rendered outside the provider should
  // lose its drill-downs, not fail to render at all.
  return value || { drill: () => {}, close: () => {}, request: null };
}

/* The rows themselves, in a sheet over the page.
 *
 * A sheet rather than a new tab: the figure you clicked is the context, and
 * navigating away from it means coming back and finding the period, the tab
 * and the scroll position again. */
function DrillPanel({ request, onClose }) {
  const { params: periodParams, label: periodLabel, scoped } = usePeriod();
  const [rows, setRows] = useState(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState(null);

  /* The period is merged in unless the caller pinned its own window - a
     "compare with last month" figure has to keep the month it is about. */
  const query = useMemo(() => ({
    ...(request.ignorePeriod ? {} : periodParams),
    ...request.params,
    limit: 5000,
    sort_by: request.sortBy || 'amount',
    sort_dir: request.sortDir || 'desc',
  }), [request, periodParams]);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    api.transactions(query)
      .then((res) => {
        if (cancelled) return;
        setRows(res.transactions || []);
        setTotal(res.total ?? (res.transactions || []).length);
        setError(null);
      })
      .catch((e) => !cancelled && setError(e.message));
    return () => { cancelled = true; };
  }, [JSON.stringify(query)]);

  const [categories, setCategories] = useState([]);
  const [saving, setSaving] = useState(null);

  useEffect(() => {
    api.categories().then(setCategories).catch(() => {});
  }, []);

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

  const sums = useMemo(() => {
    const out = { inflow: 0, outflow: 0, uncounted: 0, uncountedRows: 0 };
    for (const r of rows || []) {
      const amount = Math.abs(Number(r.amount) || 0);
      const counted = !r.excluded && !r.is_mirror_leg;
      if (!counted) {
        out.uncounted += amount;
        out.uncountedRows += 1;
        continue;
      }
      if (r.direction === 'credit') out.inflow += amount;
      else out.outflow += amount;
    }
    return out;
  }, [rows]);

  const net = sums.inflow - sums.outflow;
  const truncated = total > (rows?.length || 0);

  function exportCsv() {
    downloadCsv(
      `${(request.title || 'transactions').replace(/[^\w-]+/g, '-').toLowerCase()}.csv`,
      toCsv(rows || [], [
        ['date', 'Date'],
        ['accounting_month', 'Counts in'],
        ['description', 'Description'],
        ['category', 'Category'],
        ['flow_role', 'Counts as'],
        [(r) => (r.direction === 'credit' ? r.amount : -r.amount), 'Amount'],
        ['note', 'Note'],
      ]),
    );
  }

  return (
    <div className="drill-backdrop" onClick={onClose}>
      <aside
        className="drill"
        role="dialog"
        aria-label={request.title || 'Transactions'}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="drill-head">
          <div style={{ minWidth: 0 }}>
            <div className="drill-title">{request.title || 'Transactions'}</div>
            <div className="drill-sub">
              {/* Which window these rows are from, always. The same figure in
                  a different period is a different figure. */}
              {request.ignorePeriod
                ? (request.periodLabel || 'whole ledger')
                : (scoped ? periodLabel : 'all time')}
              {rows && ` · ${fmtCount(total)} transaction${total === 1 ? '' : 's'}`}
            </div>
            {request.subtitle && (
              <div className="drill-note">{request.subtitle}</div>
            )}
          </div>
          <button className="btn icon" aria-label="Close" onClick={onClose}>✕</button>
        </header>

        {rows && rows.length > 0 && (
          <div className="drill-totals">
            {sums.outflow > 0 && (
              <span><em>Out</em> <strong className="num">{money(sums.outflow)}</strong></span>
            )}
            {sums.inflow > 0 && (
              <span><em>In</em> <strong className="num">{money(sums.inflow)}</strong></span>
            )}
            {sums.inflow > 0 && sums.outflow > 0 && (
              <span><em>Net</em> <strong className="num">{money(net)}</strong></span>
            )}
            <span className="drill-spacer" />
            <button className="btn" onClick={exportCsv}>Export</button>
          </div>
        )}

        {sums.uncountedRows > 0 && (
          <div className="drill-caveat">
            {sums.uncountedRows} of these ({money(sums.uncounted)}) are excluded
            from your totals — a row you set aside, or the mirror leg of a
            transfer counted on its other side. They are listed because they
            really happened.
          </div>
        )}

        {error && <div className="callout neg" style={{ margin: 14 }}>{error}</div>}
        {rows === null && <div className="spinner" style={{ margin: 40 }} />}
        {rows && rows.length === 0 && (
          <div className="empty" style={{ padding: '40px 20px' }}>
            <h3>Nothing here</h3>
            <p>No transaction matches this, in this period.</p>
          </div>
        )}

        {rows && rows.length > 0 && (
          <div className="drill-rows">
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
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} style={{ opacity: r.excluded || r.is_mirror_leg ? 0.5 : 1 }}>
                    <td className="nowrap">
                      {dateLabel(r.date)}
                      {r.accounting_month
                        && r.accounting_month !== String(r.date).slice(0, 7) && (
                        <div className="drill-counts-in"
                          title="Counted in this month, not the month of the date">
                          counts in {r.accounting_month}
                        </div>
                      )}
                    </td>
                    <td>
                      <div className="drill-desc">{r.description}</div>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
                        {r.excluded && <span className="chip warn">excluded</span>}
                        {r.needs_review && <span className="chip warn">needs review</span>}
                      </div>
                      {r.note && <div className="drill-counts-in">{r.note}</div>}
                    </td>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <select
                          value={r.category}
                          disabled={saving === r.id}
                          onChange={(e) => recategorize(r, e.target.value)}
                          style={{ fontSize: 12, padding: '3px 6px', maxWidth: 150 }}
                        >
                          {categories.map((c) => (
                            <option key={c} value={c}>{titleCase(c)}</option>
                          ))}
                        </select>
                      </div>
                    </td>
                    <td className="right num nowrap" style={{
                      color: r.direction === 'credit' ? 'var(--positive)' : 'inherit',
                    }}>
                      {r.direction === 'credit' ? '+' : '−'}{money(Math.abs(r.amount))}
                    </td>
                    <td className="right nowrap">
                      <PromptButton
                        className="btn icon"
                        title={r.note ? 'Edit note' : 'Add a note'}
                        disabled={saving === r.id}
                        initial={r.note || ''}
                        placeholder="Note for this transaction"
                        onSubmit={(note) => addNote(r, note)}
                      >
                        {r.note ? '✎' : '+'}
                      </PromptButton>
                      <button
                        className="btn icon"
                        title={r.excluded
                          ? 'Put this back in your totals'
                          : 'Leave this out of every total'}
                        disabled={saving === r.id}
                        onClick={() => patch(r, { excluded: !r.excluded })}
                      >
                        {r.excluded ? '↺' : '⊘'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {truncated && (
          <div className="drill-caveat">
            Showing the {rows.length} largest of {fmtCount(total)}. The Ledger
            tab pages through all of them.
          </div>
        )}
      </aside>
    </div>
  );
}

/* A figure that opens its own rows.
 *
 * Used wherever a number is worth a click. It is a button, not a div with an
 * onClick: the whole point is that it is reachable by keyboard and announces
 * itself as something that can be pressed. */
export function DrillLink({ onDrill, children, className = '', title, ...rest }) {
  if (!onDrill) return <>{children}</>;
  return (
    <button
      type="button"
      className={`drill-link ${className}`}
      onClick={onDrill}
      title={title || 'Show the transactions behind this'}
      {...rest}
    >
      {children}
    </button>
  );
}
