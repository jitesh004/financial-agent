import React, { useCallback, useEffect, useState } from 'react';
import { ArchivedList, CardTable, HoldingTable, LoanTable } from './Tables';
import { Callout, Card, Chip, ConfirmButton, Empty, Stat } from '../ui';
import { api, dateLabel, money } from '../../lib';

/* Position: what you have reviewed and confirmed is true.

   Everything else in this app is derived from a document, and is therefore
   only as complete as the documents that have been imported. This screen is
   the other half - the place where a person says "this is my reality, I have
   been through it" - and it exists because there are facts no statement
   carries: a loan serviced from an account nobody uploaded, a tenure agreed
   verbally, a card whose PDF is lost.

   The thing that stops it becoming a stale form is that nothing here is
   displayed as typed. A loan is rolled forward from the day it was reviewed
   through the same amortization the Debt tab uses, so a balance confirmed in
   January reads three instalments lighter in April; and where a statement
   does exist, the two are compared and the difference is shown rather than
   one quietly winning.

   A card is the deliberate exception. Its balance depends on what was spent,
   so projecting it would be inventing a liability - the cycle rolls, the
   balance goes stale and says so. */

const TODAY = () => new Date().toISOString().slice(0, 10);

/* A blank is not a zero, and the tiles say so.

   The alternative - defaulting a missing balance to nothing - produced
   "assets: ₹0" and a net worth stated as if the person owned nothing, from a
   position with one empty field in it. Every tile here shows an em dash where
   a figure is genuinely unknown, and the strip underneath names how many rows
   are still blank rather than letting a short total pass as a final one. */
function Totals({ totals }) {
  const unknown = totals.unknown || {};
  const blanks = (unknown.loans || 0) + (unknown.cards || 0)
    + (unknown.assets || 0);
  return (
    <>
      <div className="grid cols-4">
        <Stat label="Owed, all in"
          value={totals.total_owed == null ? '—' : totals.total_owed}
          tone={totals.total_owed ? 'neg' : undefined}
          note={`${totals.loan_count} loan(s) · ${totals.card_count} card(s)`} />
        <Stat label="Going out a month"
          value={totals.monthly_emi == null ? '—' : totals.monthly_emi}
          note="EMIs at the current instalments" />
        <Stat label="Card utilisation"
          value={totals.card_utilisation_pct == null ? '—'
            : `${totals.card_utilisation_pct}%`}
          tone={totals.card_utilisation_pct >= 30 ? 'neg' : undefined}
          note={totals.card_utilisation_pct == null
            ? 'no card has both a balance and a limit'
            : `${money(totals.card_outstanding)} of ${money(totals.credit_limit)}`} />
        <Stat label="Debt free"
          value={totals.debt_free_on ? dateLabel(totals.debt_free_on) : '—'}
          note={totals.interest_remaining
            ? `${money(totals.interest_remaining)} of interest to come`
            : 'needs a balance, an EMI and a rate'} />
      </div>
      {blanks > 0 && (
        <div style={{ fontSize: 12.5, color: 'var(--text-3)', marginTop: -6 }}>
          {blanks} row{blanks > 1 ? 's have' : ' has'} no amount recorded yet,
          so the totals above are short by whatever{' '}
          {blanks > 1 ? 'they hold' : 'it holds'}. Fill{' '}
          {blanks > 1 ? 'them' : 'it'} in and every figure here closes.
        </div>
      )}
    </>
  );
}

/* The credit accounts nothing in the position covers.

   The single most important thing on this screen. A lender has told a bureau
   these are live, and if the position does not include them then every total
   here - and every answer an agent gives - is short by whatever they hold.
   Nothing else in this app can tell you that. */
function BlindSpots({ bureau, onAdopt }) {
  if (!bureau.length) return null;
  return (
    <Callout tone="neg">
      <strong>
        {bureau.length} live credit account{bureau.length > 1 ? 's' : ''} your
        position does not cover
      </strong>
      <p style={{ margin: '4px 0 10px', lineHeight: 1.55 }}>
        A lender has reported {bureau.length > 1 ? 'these' : 'this'} to the
        bureau. Until {bureau.length > 1 ? 'they are' : 'it is'} here, every
        total on this screen is short by whatever{' '}
        {bureau.length > 1 ? 'they hold' : 'it holds'} — and so is anything an
        agent tells you.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {bureau.map((b) => (
          <div key={b.id} style={{ display: 'flex', gap: 10,
            alignItems: 'center', flexWrap: 'wrap' }}
          >
            <strong style={{ fontSize: 13.5 }}>{b.lender}</strong>
            <Chip>{String(b.type || '').replace(/_/g, ' ')}</Chip>
            {b.masked && <span style={{ fontSize: 12 }}>{b.masked}</span>}
            {b.balance && (
              <span className="num" style={{ fontSize: 13 }}>
                {money(Number(b.balance))}
              </span>
            )}
            {b.emi && (
              <span style={{ fontSize: 12, color: 'var(--text-3)' }}>
                EMI {money(Number(b.emi))}
              </span>
            )}
            <button className="btn" onClick={() => onAdopt(b)}>
              Add to my position
            </button>
          </div>
        ))}
      </div>
    </Callout>
  );
}

function ReviewBar({ totals, onReview, busy }) {
  const [note, setNote] = useState('');
  const oldest = totals.reviewed_oldest;
  return (
    <Card
      title="Sign it off"
      sub="Confirming freezes a dated copy of the whole position. That record is what makes it a fact rather than a guess — and what a later reading can be checked against."
    >
      <div style={{ display: 'flex', gap: 10, alignItems: 'center',
        flexWrap: 'wrap' }}
      >
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Optional note — what prompted this review"
          style={{ flex: 1, minWidth: 220, fontSize: 13 }}
        />
        <button className="btn primary" disabled={busy}
          onClick={() => onReview(note).then(() => setNote(''))}
        >
          {busy ? 'Saving…' : 'I have checked all of this'}
        </button>
      </div>
      <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--text-3)' }}>
        {oldest
          ? `Oldest unconfirmed figure is from ${dateLabel(oldest)}.`
          : 'Nothing reviewed yet.'}
        {totals.stale_count > 0
          && ` ${totals.stale_count} row(s) have gone past the point they can be trusted.`}
        {totals.drifting_count > 0
          && ` ${totals.drifting_count} disagree with your statements.`}
      </div>
    </Card>
  );
}

function Snapshots({ snapshots, onOpen, onDelete }) {
  if (!snapshots.length) return null;
  return (
    <Card title="Every review"
      sub="What you were carrying, each time you signed it off."
    >
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Reviewed</th>
              <th>Note</th>
              <th className="right">Owed</th>
              <th className="right">EMIs a month</th>
              <th className="right">Items</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {snapshots.map((s) => (
              <tr key={s.id}>
                <td className="nowrap">
                  <button type="button" className="drill-link"
                    onClick={() => onOpen(s.id)}
                  >
                    {dateLabel(s.taken_on)}
                  </button>
                </td>
                <td>{s.note || <span style={{ color: 'var(--text-3)' }}>—</span>}</td>
                <td className="right num nowrap">
                  {money(s.totals?.total_owed)}
                </td>
                <td className="right num nowrap">
                  {money(s.totals?.monthly_emi)}
                </td>
                <td className="right num">{s.item_count}</td>
                <td className="right">
                  <ConfirmButton className="btn danger"
                    question="Delete this review? The record of what you signed off goes with it."
                    confirmLabel="Delete"
                    onConfirm={() => onDelete(s.id)}
                  >
                    ✕
                  </ConfirmButton>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export default function Position() {
  const [data, setData] = useState(null);
  const [mappable, setMappable] = useState(null);
  const [snapshots, setSnapshots] = useState([]);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [showArchived, setShowArchived] = useState(false);

  const load = useCallback(async () => {
    try {
      const [position, maps, snaps] = await Promise.all([
        api.position(true), api.positionMappable(), api.positionSnapshots(),
      ]);
      setData(position);
      setMappable(maps);
      setSnapshots(snaps.snapshots || []);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function guard(work) {
    setBusy(true);
    try {
      await work();
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const patch = (id, fields) =>
    guard(() => api.updatePositionItem(id, fields));
  const review = (id) => guard(() => api.reviewPositionItem(id, TODAY()));
  const remove = (id) => guard(() => api.deletePositionItem(id));

  const adopt = (bureauLine) => guard(() => api.addPositionItem({
    kind: String(bureauLine.type || '').includes('card') ? 'card' : 'loan',
    label: `${bureauLine.lender}${bureauLine.masked ? ` ${bureauLine.masked}` : ''}`,
    institution: bureauLine.lender || '',
    bureau_account_id: bureauLine.id,
    outstanding: bureauLine.balance || null,
    emi: bureauLine.emi || null,
    reviewed_on: TODAY(),
    notes: 'From your credit report.',
  }));

  const addBlank = (kind) => guard(() => api.addPositionItem({
    kind, label: `New ${kind}`, reviewed_on: TODAY(),
  }));

  if (!data && !error) return <div className="spinner" style={{ margin: 40 }} />;

  const items = data?.items || [];
  const live = items.filter((i) => !i.archived);
  const archived = items.filter((i) => i.archived);
  const loans = live.filter((i) => i.kind === 'loan');
  const cards = live.filter((i) => i.kind === 'card');
  const holdings = live.filter(
    (i) => !['loan', 'card'].includes(i.kind));
  const blindSpots = data?.unaccounted?.bureau || [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 className="section-title" style={{ marginBottom: 4 }}>
          Position
          <span className="section-note">as at {dateLabel(data?.as_of)}</span>
        </h2>
        <p style={{ color: 'var(--text-2)', margin: 0, maxWidth: 760,
          lineHeight: 1.6 }}
        >
          What you have checked yourself. Every figure carries the date you
          confirmed it and is aged forward from there — a loan balance you
          signed off in January reads three instalments lighter in April,
          because that is what it is. Where a statement exists, the two are
          compared and any disagreement is shown rather than one of them
          quietly winning.
        </p>
      </div>

      {error && <Callout tone="neg">{error}</Callout>}
      {notice && (
        <Callout tone="accent">
          {notice}
          <button className="btn" style={{ marginLeft: 10 }}
            onClick={() => setNotice(null)}
          >
            Dismiss
          </button>
        </Callout>
      )}

      {!items.length ? (
        <Empty title="Nothing here yet"
          action={(
            <button className="btn primary" disabled={busy}
              onClick={() => guard(() => api.seedPosition())}
            >
              Draft it from what I have imported
            </button>
          )}
        >
          Nobody types twelve accounts in from memory. Every figure your
          statements and your credit report already carry gets filled in
          first — your job is to correct what is wrong and confirm the rest,
          which is a five-minute pass rather than an afternoon.
        </Empty>
      ) : (
        <>
          <Totals totals={data.totals} />
          <BlindSpots bureau={blindSpots} onAdopt={adopt} />

          {loans.length > 0 && (
            <Card title="Loans"
              sub="Rolled forward from the day you confirmed each one. Click any figure to correct it."
            >
              <LoanTable items={loans} mappable={mappable} onPatch={patch}
                onReview={review} onRemove={remove} />
            </Card>
          )}

          {cards.length > 0 && (
            <Card title="Cards"
              sub="A card balance is never projected — it depends on what you spent. The cycle is, so the next due date is real; the balance is only as good as your last review."
            >
              <CardTable items={cards} mappable={mappable} onPatch={patch}
                onReview={review} onRemove={remove} />
            </Card>
          )}

          {holdings.length > 0 && (
            <Card title="What you hold">
              <HoldingTable items={holdings} mappable={mappable} onPatch={patch}
                onReview={review} onRemove={remove} />
            </Card>
          )}

          <Card title="Add something"
            sub="A loan from family, a card whose statement you cannot find, anything the imports cannot see. That is what this screen is for."
          >
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {['loan', 'card', 'account', 'investment', 'other'].map((kind) => (
                <button key={kind} className="btn" disabled={busy}
                  onClick={() => addBlank(kind)}
                >
                  + {kind}
                </button>
              ))}
              <button className="btn" disabled={busy}
                onClick={() => guard(() => api.seedPosition())}
                title="Picks up anything imported since you last did this. Nothing already here is touched."
              >
                Pick up new imports
              </button>
              {archived.length > 0 && (
                <button className="btn"
                  onClick={() => setShowArchived((v) => !v)}
                >
                  {showArchived ? 'Hide' : `Removed (${archived.length})`}
                </button>
              )}
            </div>
            {showArchived && (
              <div style={{ marginTop: 12 }}>
                <ArchivedList items={archived} onPatch={patch} />
              </div>
            )}
          </Card>

          <ReviewBar totals={data.totals} busy={busy}
            onReview={(note) => guard(() => api.reviewPosition({ note }))} />

          <Snapshots
            snapshots={snapshots}
            onOpen={(id) => api.positionSnapshot(id)
              .then((s) => setNotice(
                `On ${dateLabel(s.taken_on)} you signed off `
                + `${money(s.totals?.total_owed)} owed across ${s.item_count} `
                + `items, with ${money(s.totals?.monthly_emi)} of EMIs a month`
                + `${s.note ? ` — "${s.note}"` : ''}.`))
              .catch((e) => setError(e.message))}
            onDelete={(id) => guard(() => api.deletePositionSnapshot(id))}
          />
        </>
      )}
    </div>
  );
}
