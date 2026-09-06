/* Who owes you, and who you owe.
 *
 * This exists because amount-matching cannot close the loop and cash proves
 * it: if somebody repays you in notes, there is no ledger row anywhere for any
 * algorithm to find. So the expense is marked as not-yours when it happens,
 * and repayment becomes a separate event that may leave no trace at all -
 * which is why "settled in cash" is a first-class option here rather than an
 * afterthought.
 */

import React, { useState } from 'react';
import { api } from '../core/api';
import { invalidate, useQuery } from '../core/store';
import { useToast } from '../core/toast';
import { dateLabel, money, today } from '../core/format';
import { Button, Callout, Card, Chip, Empty, Loading, Select, Stat } from '../ui';

const METHODS = [
  ['cash', 'Cash — no ledger row, just close it'],
  ['bank_inflow', 'Into my bank — they transferred it'],
  ['card_payment', 'Paid my card directly'],
  ['netting', 'Netted off against something I owed them'],
  ['external', 'They settled it with a third party'],
  ['write_off', 'Write off — not coming back'],
];

const STATUS_TONE = { open: 'warn', partial: 'acc', settled: 'pos', written_off: 'neg' };

const ageInDays = (iso) => (iso
  ? Math.floor((Date.now() - new Date(iso).getTime()) / 86400000) : null);

export default function Owed() {
  const toast = useToast();
  const { data: claims = [], loading, error, refetch } = useQuery('claims', () => api.claims());
  const [settling, setSettling] = useState(null);
  const [form, setForm] = useState({ method: 'cash', amount: '', note: '' });

  if (loading) return <Loading label="Reading what is outstanding…" />;
  if (error) return <Callout tone="neg">{error.message}</Callout>;

  const open = (dir) => claims.filter((c) => c.direction === dir
    && c.status !== 'settled' && c.status !== 'written_off');
  const owedToMe = open('owed_to_me');
  const owedByMe = open('owed_by_me');
  const closed = claims.filter((c) => c.status === 'settled' || c.status === 'written_off');
  const outstanding = (rows) => rows.reduce(
    (s, c) => s + (Number(c.amount) - Number(c.settled_amount || 0)), 0);

  async function settle(claim) {
    try {
      const left = Number(claim.amount) - Number(claim.settled_amount || 0);
      await api.settleClaim(claim.id, {
        method: form.method,
        amount: form.amount === '' ? left : Number(form.amount),
        settled_on: today(),
        note: form.note,
      });
      setSettling(null);
      setForm({ method: 'cash', amount: '', note: '' });
      invalidate('claims', 'dashboard');
      refetch();
      toast.ok('Recorded', 'The claim has been settled.');
    } catch (e) { toast.fail('That could not be recorded', e.message); }
  }

  const renderClaim = (claim) => {
    const left = Number(claim.amount) - Number(claim.settled_amount || 0);
    const age = ageInDays(claim.opened_on);
    const stale = age !== null && age > 90 && claim.status === 'open';
    return (
      <div key={claim.id} className="list-row" style={{ borderTop: '1px solid var(--line)' }}>
        <div className="grow">
          <div className="list-title">{claim.counterparty || 'Unnamed'}</div>
          <div className="list-sub">
            Opened {dateLabel(claim.opened_on)}
            {age !== null && ` · ${age} day${age === 1 ? '' : 's'} ago`}
            {claim.note && ` · ${claim.note}`}
          </div>
          <div className="row tight" style={{ marginTop: 5 }}>
            <Chip tone={STATUS_TONE[claim.status]}>{claim.status.replace('_', ' ')}</Chip>
            {claim.basis === 'cash' && <Chip>counted when repaid</Chip>}
            {stale && <Chip tone="warn">over 90 days</Chip>}
          </div>
        </div>

        <div className="right">
          <div className="num" style={{ fontWeight: 620 }}>{money(left)}</div>
          {Number(claim.settled_amount) > 0 && (
            <div className="tiny dim">of {money(claim.amount)}</div>
          )}
          <Button size="sm" style={{ marginTop: 8 }}
            onClick={() => setSettling(settling === claim.id ? null : claim.id)}>
            {settling === claim.id ? 'Cancel' : 'Settle'}
          </Button>
        </div>

        {settling === claim.id && (
          <div style={{ flexBasis: '100%', marginTop: 10, paddingTop: 10,
            borderTop: '1px dashed var(--line)' }}>
            <div className="row">
              <Select
                value={form.method}
                onChange={(v) => setForm({ ...form, method: v })}
                options={METHODS}
                style={{ minWidth: 230 }}
              />
              <input type="number" placeholder={`Full amount (${left.toFixed(2)})`}
                value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })}
                style={{ width: 180 }} />
              <input placeholder="Note (optional)" value={form.note}
                onChange={(e) => setForm({ ...form, note: e.target.value })}
                style={{ flex: 1, minWidth: 160 }} />
              <Button variant="primary" onClick={() => settle(claim)}>Record</Button>
            </div>
            <div className="tiny dim" style={{ marginTop: 6 }}>
              Leave the amount blank to settle it in full. Part-payments are fine — the
              rest stays open.
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <>
      <div>
        <h2 className="h2">Money owed</h2>
        <p className="lead">
          Expenses that were never really yours. Mark a purchase as somebody else&apos;s
          from the Ledger and it appears here until it comes back — by transfer, on your
          card, or in cash.
        </p>
      </div>

      <div className="grid cols-2">
        <Stat label="Owed to me" value={outstanding(owedToMe)} tone="pos"
          note={`${owedToMe.length} open`} />
        <Stat label="I owe" value={outstanding(owedByMe)} tone="neg"
          note={`${owedByMe.length} open`} />
      </div>

      {!claims.length && (
        <Empty title="No claims yet" icon="hand">
          When a purchase on your card was not yours, open it in the Ledger and choose
          “not my expense”. It stops counting as your spending straight away, and you
          can close it here however the money actually comes back.
        </Empty>
      )}

      {owedToMe.length > 0 && (
        <Card title="Owed to me" pad={false}>
          <div style={{ padding: '0 16px 8px' }}>{owedToMe.map(renderClaim)}</div>
        </Card>
      )}
      {owedByMe.length > 0 && (
        <Card title="I owe" pad={false}>
          <div style={{ padding: '0 16px 8px' }}>{owedByMe.map(renderClaim)}</div>
        </Card>
      )}
      {closed.length > 0 && (
        <Card title="Closed" sub={`${closed.length} settled or written off`} pad={false}>
          <div style={{ padding: '0 16px 8px' }}>{closed.map(renderClaim)}</div>
        </Card>
      )}
    </>
  );
}
