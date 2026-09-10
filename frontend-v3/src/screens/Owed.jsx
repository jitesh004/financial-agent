import React, { useState } from 'react';
import { api } from '../core/api';
import { invalidate, useQuery } from '../core/store';
import { useToast } from '../core/toast';
import { dateLabel, money, today } from '../core/format';
import { Button, Callout, Card, GlassCard, Chip, Empty, Loading, Select, Stat, Table, Badge } from '../ui';
import { Icon } from '../ui/icons';

const SETTLEMENT_METHODS = [
  ['cash', 'Cash (No ledger row; settle directly)'],
  ['bank_inflow', 'Bank Transfer (Inbound credit to account)'],
  ['card_payment', 'Direct Credit Card Bill Settlement'],
  ['netting', 'Netting (Offset against reciprocal debt)'],
  ['external', 'Third-Party Settlement'],
  ['write_off', 'Write-Off (Bad debt / Unrecoverable)'],
];

const STATUS_TONE_MAP = {
  open: 'warn',
  partial: 'brand',
  settled: 'pos',
  written_off: 'neg',
};

/* `Table` takes tuple columns - [key, label, width, align] - where a
   FUNCTION in the key slot is called with the row. Declared out here so the
   array is not rebuilt on every render. */
const P2P_COLUMNS = [
  ['counterparty', 'Counterparty', 200],
  [(r) => <span className="num">{money(r.sent)}</span>, 'You sent', 120, 'right'],
  [(r) => <span className="num">{money(r.received)}</span>, 'You received', 120, 'right'],
  [(r) => (
    <span
      className="num"
      style={{
        color: Number(r.net_owed_to_me) > 0 ? 'var(--pos)'
          : Number(r.net_owed_to_me) < 0 ? 'var(--neg)' : 'inherit',
      }}
    >
      {money(r.net_owed_to_me)}
    </span>
  ), 'Net', 130, 'right'],
  ['count', 'Rows', 70, 'right'],
  [(r) => dateLabel(r.last_activity), 'Last seen', 110],
];

const ageInDays = (iso) => (iso ? Math.floor((Date.now() - new Date(iso).getTime()) / 86400000) : null);

export default function Owed() {
  const toast = useToast();
  const { data: claims = [], loading, error, refetch } = useQuery('claims', () => api.claims());
  const { data: analysisData } = useQuery('analysis', () => api.analysis());
  const [settling, setSettling] = useState(null);
  const [form, setForm] = useState({ method: 'cash', amount: '', note: '' });

  if (loading) return <Loading message="Retrieving counterparty claims and receivables…" />;
  if (error) return <Callout tone="neg">{error.message}</Callout>;

  const openClaims = (dir) => claims.filter((c) => c.direction === dir && c.status !== 'settled' && c.status !== 'written_off');
  const owedToMe = openClaims('owed_to_me');
  const owedByMe = openClaims('owed_by_me');
  const closed = claims.filter((c) => c.status === 'settled' || c.status === 'written_off');

  const totalOutstanding = (rows) => rows.reduce((s, c) => s + (Number(c.amount) - Number(c.settled_amount || 0)), 0);

  const p2p = (analysisData?.analysis?.p2p_balances || [])
    .filter((b) => Math.abs(Number(b.net_owed_to_me)) >= 1);

  async function handleSettle(claim) {
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
      toast.ok('Settlement recorded', 'Claim status updated successfully.');
    } catch (e) {
      toast.fail('Settlement failed', e.message);
    }
  }

  const renderClaimCard = (claim) => {
    const left = Number(claim.amount) - Number(claim.settled_amount || 0);
    const age = ageInDays(claim.opened_on);
    const isStale = age !== null && age > 90 && claim.status === 'open';

    return (
      <div
        key={claim.id}
        style={{
          padding: 'var(--space-4)',
          borderRadius: 'var(--radius-md)',
          background: 'var(--surface-2)',
          border: '1px solid var(--border-subtle)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--space-3)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
          <div>
            <div style={{ fontSize: 'var(--text-base)', fontWeight: 600 }}>{claim.counterparty || 'Unnamed Counterparty'}</div>
            <div className="tiny muted" style={{ marginTop: 2 }}>
              Opened {dateLabel(claim.opened_on)}
              {age !== null && ` · ${age} day${age === 1 ? '' : 's'} ago`}
              {claim.note && ` · "${claim.note}"`}
            </div>
            <div style={{ display: 'flex', gap: 6, marginTop: 6, alignItems: 'center' }}>
              <Badge tone={STATUS_TONE_MAP[claim.status] || 'brand'} size="sm">
                {claim.status.replace('_', ' ')}
              </Badge>
              {claim.basis === 'cash' && <Chip size="sm">Cash Basis</Chip>}
              {isStale && <Badge tone="warn" size="sm">&gt;90 Days Aged</Badge>}

            </div>
          </div>

          <div style={{ textAlign: 'right', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
            <div className="num font-bold" style={{ fontSize: 'var(--text-lg)' }}>{money(left)}</div>
            {Number(claim.settled_amount) > 0 && (
              <div className="tiny muted">Partially settled ({money(claim.amount)} total)</div>
            )}
            <Button
              size="sm"
              variant={settling === claim.id ? 'ghost' : 'primary'}
              onClick={() => setSettling(settling === claim.id ? null : claim.id)}
            >
              {settling === claim.id ? 'Cancel' : 'Record Settlement'}
            </Button>
          </div>
        </div>

        {/* Inline Settlement Panel */}
        {settling === claim.id && (
          <div
            style={{
              paddingTop: 'var(--space-3)',
              borderTop: '1px dashed var(--border-subtle)',
              display: 'flex',
              flexDirection: 'column',
              gap: 'var(--space-3)',
            }}
          >
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
              <Select
                value={form.method}
                onChange={(v) => setForm({ ...form, method: v })}
                options={SETTLEMENT_METHODS}
                style={{ minWidth: 220 }}
              />
              <input
                type="number"
                placeholder={`Amount (Blank = ${left.toFixed(2)})`}
                value={form.amount}
                onChange={(e) => setForm({ ...form, amount: e.target.value })}
                className="input"
                style={{ width: 190 }}
              />
              <input
                placeholder="Audit memo / reference"
                value={form.note}
                onChange={(e) => setForm({ ...form, note: e.target.value })}
                className="input"
                style={{ flex: 1, minWidth: 180 }}
              />
              <Button variant="primary" onClick={() => handleSettle(claim)}>
                Confirm Settle
              </Button>
            </div>
            <div className="tiny muted">
              Leave amount blank to close the obligation in full. Partial amounts remain active under partial status.
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="owed-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <h1 className="h1" style={{ margin: 0 }}>Money Owed & Receivables</h1>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            Track non-personal shared expenses, peer reimbursements, and claims marked from the Ledger until settled.
          </p>
        </div>
      </div>

      {/* Metric Tiles */}
      <div className="grid-2">
        <Stat
          label="Owed to Me (Receivables)"
          value={money(totalOutstanding(owedToMe))}
          tone="pos"
          sub={`${owedToMe.length} pending claims`}
        />
        <Stat
          label="I Owe to Others (Payables)"
          value={money(totalOutstanding(owedByMe))}
          tone="neg"
          sub={`${owedByMe.length} pending obligations`}
        />
      </div>

      {!claims.length && (
        <Empty title="No claims or receivables on record" icon="credit">
          When an expense paid on your card was for a friend or business reimbursement, open it in the Ledger
          terminal and select &ldquo;Not my expense&rdquo;. It is instantly removed from your personal spending and tracked here.
        </Empty>
      )}

      {/* Owed To Me */}
      {owedToMe.length > 0 && (
        <Card title="Receivables (Owed to Me)" subtitle={`${owedToMe.length} active claims pending recovery`}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
            {owedToMe.map(renderClaimCard)}
          </div>
        </Card>
      )}

      {/* I Owe */}
      {owedByMe.length > 0 && (
        <Card title="Payables (I Owe)" subtitle={`${owedByMe.length} shared balances pending settlement`}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
            {owedByMe.map(renderClaimCard)}
          </div>
        </Card>
      )}

      {/* Observed person-to-person flow, as distinct from the claims above.
          A claim is something you declared; this is what the statements
          show, and it is the only thing that makes the P2P credits
          readable. 5.41 lakh arriving from named individuals is counted as
          income because nothing can prove otherwise - the debits to those
          same people are the evidence that some of it was a repayment. */}
      {p2p.length > 0 && (
        <Card
          title="Observed Person-to-Person Flow"
          subtitle="Netted from your statements, not declared. Nothing here is counted as a claim."
        >
          <Callout>
            These are UPI and bank transfers to and from named individuals. Money
            received from a person is counted as <strong>income</strong> unless you
            say otherwise, because the alternative silently erases real money &mdash;
            so a name with more received than sent may be inflating your income.
            Open it in the Ledger and mark it a transfer or a repayment if it was one.
            <div style={{ marginTop: 6 }}>
              <strong>Net</strong> is what you sent minus what you received:
              positive means they have had more from you than you from them.
            </div>
          </Callout>
          <Table
            keyField="counterparty"
            columns={P2P_COLUMNS}
            rows={p2p.slice(0, 25)}
          />
          {p2p.length > 25 && (
            <div className="tiny muted" style={{ marginTop: 8 }}>
              Showing the 25 largest of {p2p.length} counterparties.
            </div>
          )}
        </Card>
      )}

      {/* Closed History */}
      {closed.length > 0 && (
        <Card title="Concluded & Settled History" subtitle={`${closed.length} historical claims settled or written off`}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
            {closed.map(renderClaimCard)}
          </div>
        </Card>
      )}
    </div>
  );
}
