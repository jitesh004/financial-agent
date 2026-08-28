import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Callout, Card, Chip, Empty } from './ui';
import { api, dateLabel, money, titleCase } from '../lib';

/* Everything the pipeline could not settle on its own.
 *
 * The queue exists because the alternatives are both worse: guessing silently
 * moves money between income and spending with nothing to show for it, and
 * refusing to guess leaves the dashboard incomplete until every item is
 * cleared. So a safe default is applied, the figure is always complete, and
 * the items that carried a judgement call are listed here.
 *
 * Worth knowing while working through it: for an ambiguous inbound amount,
 * net savings is the same either way. Only the split between income and
 * spending moves. */

const ROLE_CHOICES = [
  ['income', 'Income', 'Money that genuinely came in'],
  ['claim_settlement', 'Money back', 'Repays something already counted as spending'],
  ['refund', 'Refund', 'A merchant returning money'],
  ['card_settlement', 'Card payment', 'Settles a card bill, never income'],
  ['transfer_in', 'Transfer', 'Between your own accounts'],
  ['excluded', 'Ignore', 'Leave out of every total'],
];

const REASON_HINT = {
  unknown_funding:
    'No bank statement covering this date is loaded, so there is no way to '
    + 'tell whether you funded this or somebody else did. Loading that month '
    + 'resolves it without a decision.',
};

export default function ReviewQueue() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    api.reviewQueue()
      .then((res) => { setItems(res.transactions || []); setError(null); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  async function resolve(txn, fields) {
    setBusy(txn.id);
    try {
      await api.updateTransaction(txn.id, { needs_review: false, ...fields });
      setItems((prev) => prev.filter((t) => t.id !== txn.id));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  // Grouped by the question being asked, so a run of identical decisions can
  // be worked through as one thought rather than N.
  const groups = useMemo(() => {
    const out = new Map();
    for (const t of items) {
      const key = t.review_reason || 'Needs a look';
      if (!out.has(key)) out.set(key, []);
      out.get(key).push(t);
    }
    return [...out.entries()];
  }, [items]);

  if (loading) return <div className="spinner" style={{ margin: 40 }} />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 className="section-title" style={{ marginBottom: 4 }}>Needs review</h2>
        <p style={{ color: 'var(--text-2)', margin: 0 }}>
          Each of these already has a sensible answer applied, so your totals
          are complete. Confirming just makes them right rather than likely —
          and your choice is remembered even if the statement is re-parsed.
        </p>
      </div>

      {error && <Callout tone="neg">{error}</Callout>}

      {!items.length && (
        <Empty title="Nothing to review">
          Every transaction was classified confidently. New items appear here
          when a card payment has no matching bank debit, when several
          payments look like one settlement, or when money arrives that could
          be either income or a repayment.
        </Empty>
      )}

      {groups.map(([reason, rows]) => (
        <Card
          key={reason}
          title={reason}
          sub={`${rows.length} transaction${rows.length === 1 ? '' : 's'}`}
        >
          {REASON_HINT[reason] && (
            <Callout tone="warn">{REASON_HINT[reason]}</Callout>
          )}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Description</th>
                  <th className="right">Amount</th>
                  <th>Counts as</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => (
                  <tr key={t.id} style={{ opacity: busy === t.id ? 0.45 : 1 }}>
                    <td className="nowrap">{dateLabel(t.date)}</td>
                    <td>
                      <div className="truncate" title={t.description}>
                        {t.description}
                      </div>
                      <div style={{ marginTop: 4, display: 'flex', gap: 6 }}>
                        <Chip>{titleCase(t.category)}</Chip>
                        {t.flow_role && (
                          <Chip tone="accent">
                            now: {titleCase(t.flow_role.replace(/_/g, ' '))}
                          </Chip>
                        )}
                      </div>
                    </td>
                    <td
                      className="right num nowrap"
                      style={{
                        color: t.direction === 'credit'
                          ? 'var(--positive)' : 'var(--text)',
                      }}
                    >
                      {t.direction === 'credit' ? '+' : '−'}{money(Math.abs(t.amount))}
                    </td>
                    <td>
                      <select
                        value={t.flow_role || ''}
                        disabled={busy === t.id}
                        onChange={(e) => resolve(t, { flow_role: e.target.value })}
                      >
                        <option value="" disabled>Choose…</option>
                        {ROLE_CHOICES.map(([value, label, hint]) => (
                          <option key={value} value={value} title={hint}>
                            {label}
                          </option>
                        ))}
                      </select>
                      <button
                        className="btn"
                        style={{ marginLeft: 8 }}
                        disabled={busy === t.id}
                        onClick={() => resolve(t, {})}
                        title="Accept what the app already decided"
                      >
                        Looks right
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ))}
    </div>
  );
}
