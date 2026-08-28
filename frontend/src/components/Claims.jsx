import React, { useCallback, useEffect, useState } from 'react';
import { Callout, Card, Chip, Empty, Stat } from './ui';
import { api, dateLabel, money } from '../lib';

/* Who owes you, and who you owe.
 *
 * This exists because amount-matching cannot close the loop and cash proves
 * it: if someone repays you in notes, there is no ledger row anywhere for any
 * algorithm to find. So the expense is marked as not-yours when it happens,
 * and repayment becomes a separate event that may leave no trace at all -
 * which is why "Settled in cash" is a first-class button here rather than an
 * afterthought. */

const METHODS = [
  ['cash', 'Cash', 'No ledger row - just close it'],
  ['bank_inflow', 'Into my bank', 'They transferred it to me'],
  ['card_payment', 'Paid my card', 'They paid the card directly'],
  ['netting', 'Netted off', 'Against something I owed them'],
  ['external', 'Paid someone else', 'They settled it with a third party'],
  ['write_off', 'Write off', 'Not coming back - treat as my expense'],
];

const STATUS_TONE = {
  open: 'warn', partial: 'accent', settled: 'pos', written_off: 'neg',
};

function ageInDays(iso) {
  if (!iso) return null;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
}

export default function Claims() {
  const [claims, setClaims] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [settling, setSettling] = useState(null);
  const [form, setForm] = useState({ method: 'cash', amount: '', note: '' });

  const load = useCallback(() => {
    setLoading(true);
    api.claims()
      .then((rows) => { setClaims(rows || []); setError(null); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  async function settle(claim) {
    try {
      const outstanding = Number(claim.amount) - Number(claim.settled_amount || 0);
      await api.settleClaim(claim.id, {
        method: form.method,
        amount: form.amount === '' ? outstanding : Number(form.amount),
        settled_on: new Date().toISOString().slice(0, 10),
        note: form.note,
      });
      setSettling(null);
      setForm({ method: 'cash', amount: '', note: '' });
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  if (loading) return <div className="spinner" style={{ margin: 40 }} />;

  const owedToMe = claims.filter((c) => c.direction === 'owed_to_me'
    && c.status !== 'settled' && c.status !== 'written_off');
  const owedByMe = claims.filter((c) => c.direction === 'owed_by_me'
    && c.status !== 'settled' && c.status !== 'written_off');
  const closed = claims.filter((c) => c.status === 'settled'
    || c.status === 'written_off');

  const outstanding = (rows) => rows.reduce(
    (sum, c) => sum + (Number(c.amount) - Number(c.settled_amount || 0)), 0);

  function renderClaim(claim) {
    const left = Number(claim.amount) - Number(claim.settled_amount || 0);
    const age = ageInDays(claim.opened_on);
    const stale = age !== null && age > 90 && claim.status === 'open';
    return (
      <div key={claim.id} className="file-row">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600 }}>{claim.counterparty || 'Unnamed'}</div>
          <div style={{ color: 'var(--text-2)', fontSize: 13, marginTop: 2 }}>
            Opened {dateLabel(claim.opened_on)}
            {age !== null && ` · ${age} day${age === 1 ? '' : 's'} ago`}
            {claim.note && ` · ${claim.note}`}
          </div>
          <div style={{ marginTop: 6, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <Chip tone={STATUS_TONE[claim.status]}>{claim.status.replace('_', ' ')}</Chip>
            {claim.basis === 'cash' && <Chip>counted when repaid</Chip>}
            {stale && <Chip tone="warn">over 90 days</Chip>}
          </div>
        </div>

        <div style={{ textAlign: 'right' }}>
          <div className="num" style={{ fontWeight: 600 }}>{money(left)}</div>
          {Number(claim.settled_amount) > 0 && (
            <div style={{ color: 'var(--text-3)', fontSize: 12 }}>
              of {money(claim.amount)}
            </div>
          )}
          <button
            className="btn"
            style={{ marginTop: 8 }}
            onClick={() => setSettling(settling === claim.id ? null : claim.id)}
          >
            {settling === claim.id ? 'Cancel' : 'Settle'}
          </button>
        </div>

        {settling === claim.id && (
          <div style={{
            flexBasis: '100%', marginTop: 12, paddingTop: 12,
            borderTop: '1px solid var(--surface-2)', display: 'flex',
            gap: 10, flexWrap: 'wrap', alignItems: 'center',
          }}
          >
            <select
              value={form.method}
              onChange={(e) => setForm({ ...form, method: e.target.value })}
            >
              {METHODS.map(([v, label, hint]) => (
                <option key={v} value={v} title={hint}>{label}</option>
              ))}
            </select>
            <input
              type="number"
              placeholder={`Full amount (${left.toFixed(2)})`}
              value={form.amount}
              onChange={(e) => setForm({ ...form, amount: e.target.value })}
              style={{ width: 170 }}
            />
            <input
              placeholder="Note (optional)"
              value={form.note}
              onChange={(e) => setForm({ ...form, note: e.target.value })}
              style={{ flex: 1, minWidth: 160 }}
            />
            <button className="btn primary" onClick={() => settle(claim)}>
              Record
            </button>
            <div style={{ flexBasis: '100%', color: 'var(--text-3)', fontSize: 12 }}>
              Leave the amount blank to settle it in full. Part-payments are
              fine — the rest stays open.
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 className="section-title" style={{ marginBottom: 4 }}>Owed</h2>
        <p style={{ color: 'var(--text-2)', margin: 0 }}>
          Expenses that were never really yours. Mark a purchase as
          someone else&apos;s from the Transactions tab and it appears here
          until it comes back — by transfer, on your card, or in cash.
        </p>
      </div>

      {error && <Callout tone="neg">{error}</Callout>}

      <div className="grid cols-2">
        <Stat label="Owed to me" value={outstanding(owedToMe)} tone="pos"
              note={`${owedToMe.length} open`} />
        <Stat label="I owe" value={outstanding(owedByMe)} tone="neg"
              note={`${owedByMe.length} open`} />
      </div>

      {!claims.length && (
        <Empty title="No claims yet">
          When a purchase on your card was not yours, open it in Transactions
          and choose &quot;Not my expense&quot;. It stops counting as your
          spending straight away, and you can close it here however the money
          actually comes back.
        </Empty>
      )}

      {owedToMe.length > 0 && (
        <Card title="Owed to me">{owedToMe.map(renderClaim)}</Card>
      )}
      {owedByMe.length > 0 && (
        <Card title="I owe">{owedByMe.map(renderClaim)}</Card>
      )}
      {closed.length > 0 && (
        <Card title="Closed" sub={`${closed.length} settled or written off`}>
          {closed.map(renderClaim)}
        </Card>
      )}
    </div>
  );
}
