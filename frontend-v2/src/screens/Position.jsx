/* Position: what you have reviewed and confirmed is true.
 *
 * Everything else in this app is derived from a document, and is therefore
 * only as complete as the documents that have been imported. This screen is
 * the other half - where a person says "this is my reality, I have been
 * through it" - and it exists because there are facts no statement carries: a
 * loan serviced from an account nobody uploaded, a tenure agreed verbally, a
 * card whose PDF is lost.
 *
 * What stops it becoming a stale form is that nothing here is displayed as
 * typed. A loan is rolled forward from the day it was reviewed through the
 * same amortization the Debt screen uses, so a balance confirmed in January
 * reads three instalments lighter in April; and where a statement does exist,
 * the two are compared and the difference is shown rather than one quietly
 * winning.
 *
 * A card is the deliberate exception. Its balance depends on what was spent,
 * so projecting it would be inventing a liability - the cycle rolls, the
 * balance goes stale and says so.
 */

import React, { useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { useToast } from '../core/toast';
import { dateLabel, money, pct, today } from '../core/format';
import {
  Button, Callout, Card, Chip, ConfirmButton, Empty, InlineEdit, Loading, Select,
  SortHeader, Stat, Table, useSorted,
} from '../ui';

export default function Position() {
  const toast = useToast();
  const [showArchived, setShowArchived] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);

  const position = useQuery('position', () => api.position(true));
  const mappable = useQuery('position-mappable', () => api.positionMappable());
  const snapshots = useQuery('position-snapshots', () => api.positionSnapshots());

  const data = position.data;

  async function guard(work) {
    setBusy(true);
    try {
      await work();
      await Promise.all([position.refetch(), mappable.refetch(), snapshots.refetch()]);
    } catch (e) {
      toast.fail('That change was not saved', e.message);
    } finally { setBusy(false); }
  }

  const patch = (id, fields) => guard(() => api.updatePositionItem(id, fields));
  const review = (id) => guard(() => api.reviewPositionItem(id, today()));
  const remove = (id) => guard(() => api.deletePositionItem(id));

  const adopt = (line) => guard(() => api.addPositionItem({
    kind: String(line.type || '').includes('card') ? 'card' : 'loan',
    label: `${line.lender}${line.masked ? ` ${line.masked}` : ''}`,
    institution: line.lender || '',
    bureau_account_id: line.id,
    outstanding: line.balance || null,
    emi: line.emi || null,
    reviewed_on: today(),
    notes: 'From your credit report.',
  }));

  if (position.loading) return <Loading label="Reading your position…" />;
  if (position.error) return <Callout tone="neg">{position.error.message}</Callout>;

  const items = data?.items || [];
  const live = items.filter((i) => !i.archived);
  const archived = items.filter((i) => i.archived);
  const loans = live.filter((i) => i.kind === 'loan');
  const cards = live.filter((i) => i.kind === 'card');
  const holdings = live.filter((i) => !['loan', 'card'].includes(i.kind));
  const blindSpots = data?.unaccounted?.bureau || [];

  return (
    <>
      <div>
        <h2 className="h2">
          Position
          <span className="section-note" style={{ marginLeft: 10 }}>
            as at {dateLabel(data?.as_of)}
          </span>
        </h2>
        <p className="lead">
          What you have checked yourself. Every figure carries the date you confirmed it
          and is aged forward from there — a loan balance signed off in January reads
          three instalments lighter in April, because that is what it is. Where a
          statement exists, the two are compared and any disagreement is shown rather
          than one of them quietly winning.
        </p>
      </div>

      {notice && (
        <Callout tone="acc">
          {notice}
          <Button size="xs" style={{ marginLeft: 10 }} onClick={() => setNotice(null)}>
            Dismiss
          </Button>
        </Callout>
      )}

      {!items.length ? (
        <Empty
          title="Nothing here yet"
          icon="scales"
          action={(
            <Button variant="primary" busy={busy}
              onClick={() => guard(() => api.seedPosition())}>
              Draft it from what I have imported
            </Button>
          )}
        >
          Nobody types twelve accounts in from memory. Every figure your statements and
          your credit report already carry gets filled in first — your job is to correct
          what is wrong and confirm the rest, which is a five-minute pass rather than an
          afternoon.
        </Empty>
      ) : (
        <>
          <Totals totals={data.totals} />
          {blindSpots.length > 0 && <BlindSpots bureau={blindSpots} onAdopt={adopt} />}

          {loans.length > 0 && (
            <Card
              title="Loans"
              sub="Rolled forward from the day you confirmed each one. Click any figure to correct it."
              pad={false}
            >
              <LoanTable items={loans} mappable={mappable.data} onPatch={patch}
                onReview={review} onRemove={remove} />
            </Card>
          )}

          {cards.length > 0 && (
            <Card
              title="Cards"
              sub="A card balance is never projected — it depends on what you spent. The cycle is, so the next due date is real; the balance is only as good as your last review."
              pad={false}
            >
              <CardTable items={cards} mappable={mappable.data} onPatch={patch}
                onReview={review} onRemove={remove} />
            </Card>
          )}

          {holdings.length > 0 && (
            <Card title="What you hold" pad={false}>
              <HoldingTable items={holdings} mappable={mappable.data} onPatch={patch}
                onReview={review} onRemove={remove} />
            </Card>
          )}

          <Card
            title="Add something"
            sub="A loan from family, a card whose statement you cannot find, anything the imports cannot see. That is what this screen is for."
          >
            <div className="row">
              {['loan', 'card', 'account', 'investment', 'other'].map((kind) => (
                <Button key={kind} size="sm" icon="plus" disabled={busy}
                  onClick={() => guard(() => api.addPositionItem({
                    kind, label: `New ${kind}`, reviewed_on: today(),
                  }))}>
                  {kind}
                </Button>
              ))}
              <Button size="sm" disabled={busy}
                title="Picks up anything imported since you last did this. Nothing already here is touched."
                onClick={() => guard(() => api.seedPosition())}>
                Pick up new imports
              </Button>
              {archived.length > 0 && (
                <Button size="sm" onClick={() => setShowArchived((v) => !v)}>
                  {showArchived ? 'Hide removed' : `Removed (${archived.length})`}
                </Button>
              )}
            </div>
            {showArchived && (
              <div className="row tight" style={{ marginTop: 12 }}>
                {archived.map((item) => (
                  <Chip key={item.id}>
                    {item.label}
                    <button className="btn link" style={{ marginLeft: 6 }}
                      onClick={() => patch(item.id, { archived: false })}>restore</button>
                  </Chip>
                ))}
              </div>
            )}
          </Card>

          <ReviewBar
            totals={data.totals}
            busy={busy}
            onReview={(note) => guard(() => api.reviewPosition({ note }))}
          />

          <Snapshots
            snapshots={snapshots.data?.snapshots || []}
            onOpen={(id) => api.positionSnapshot(id)
              .then((s) => setNotice(
                `On ${dateLabel(s.taken_on)} you signed off ${money(s.totals?.total_owed)} `
                + `owed across ${s.item_count} items, with ${money(s.totals?.monthly_emi)} `
                + `of EMIs a month${s.note ? ` — “${s.note}”` : ''}.`))
              .catch((e) => toast.fail('That review could not be opened', e.message))}
            onDelete={(id) => guard(() => api.deletePositionSnapshot(id))}
          />
        </>
      )}
    </>
  );
}

/* A blank is not a zero, and the tiles say so. Defaulting a missing balance to
   nothing produced "assets: ₹0" and a net worth stated as if the person owned
   nothing, from a position with one empty field in it. */
function Totals({ totals }) {
  const unknown = totals.unknown || {};
  const blanks = (unknown.loans || 0) + (unknown.cards || 0) + (unknown.assets || 0);
  return (
    <>
      <div className="grid cols-4">
        <Stat
          label="Owed, all in"
          value={totals.total_owed == null ? '—' : totals.total_owed}
          tone={totals.total_owed ? 'neg' : undefined}
          note={`${totals.loan_count} loan(s) · ${totals.card_count} card(s)`}
        />
        <Stat
          label="Going out a month"
          value={totals.monthly_emi == null ? '—' : totals.monthly_emi}
          note="EMIs at the current instalments"
        />
        <Stat
          label="Card utilisation"
          value={totals.card_utilisation_pct == null ? '—' : `${totals.card_utilisation_pct}%`}
          tone={totals.card_utilisation_pct >= 30 ? 'neg' : undefined}
          note={totals.card_utilisation_pct == null
            ? 'no card has both a balance and a limit'
            : `${money(totals.card_outstanding)} of ${money(totals.credit_limit)}`}
        />
        <Stat
          label="Debt free"
          value={totals.debt_free_on ? dateLabel(totals.debt_free_on) : '—'}
          note={totals.interest_remaining
            ? `${money(totals.interest_remaining)} of interest to come`
            : 'needs a balance, an EMI and a rate'}
        />
      </div>
      {blanks > 0 && (
        <div className="small dim">
          {blanks} row{blanks > 1 ? 's have' : ' has'} no amount recorded yet, so the
          totals above are short by whatever {blanks > 1 ? 'they hold' : 'it holds'}.
          Fill {blanks > 1 ? 'them' : 'it'} in and every figure here closes.
        </div>
      )}
    </>
  );
}

/* The credit accounts nothing in the position covers.
 *
 * The single most important thing on this screen. A lender has told a bureau
 * these are live, and if the position does not include them then every total
 * here - and every answer an agent gives - is short by whatever they hold.
 * Nothing else in this app can tell you that. */
function BlindSpots({ bureau, onAdopt }) {
  return (
    <Callout tone="neg">
      <strong>
        {bureau.length} live credit account{bureau.length > 1 ? 's' : ''} your position
        does not cover
      </strong>
      <p style={{ margin: '4px 0 10px', lineHeight: 1.6 }}>
        A lender has reported {bureau.length > 1 ? 'these' : 'this'} to the bureau. Until{' '}
        {bureau.length > 1 ? 'they are' : 'it is'} here, every total on this screen is
        short by whatever {bureau.length > 1 ? 'they hold' : 'it holds'} — and so is
        anything an agent tells you.
      </p>
      <div className="col" style={{ gap: 6 }}>
        {bureau.map((b) => (
          <div className="row tight" key={b.id}>
            <strong style={{ fontSize: 13.5 }}>{b.lender}</strong>
            <Chip>{String(b.type || '').replace(/_/g, ' ')}</Chip>
            {b.masked && <span className="small">{b.masked}</span>}
            {b.balance && <span className="num small">{money(Number(b.balance))}</span>}
            {b.emi && <span className="tiny dim">EMI {money(Number(b.emi))}</span>}
            <Button size="xs" onClick={() => onAdopt(b)}>Add to my position</Button>
          </div>
        ))}
      </div>
    </Callout>
  );
}

function ReviewBar({ totals, onReview, busy }) {
  const [note, setNote] = useState('');
  return (
    <Card
      title="Sign it off"
      sub="Confirming freezes a dated copy of the whole position. That record is what makes it a fact rather than a guess — and what a later reading can be checked against."
    >
      <div className="row">
        <input value={note} onChange={(e) => setNote(e.target.value)}
          placeholder="Optional note — what prompted this review"
          style={{ flex: 1, minWidth: 220 }} />
        <Button variant="primary" busy={busy}
          onClick={() => onReview(note).then(() => setNote(''))}>
          I have checked all of this
        </Button>
      </div>
      <div className="small dim" style={{ marginTop: 8 }}>
        {totals.reviewed_oldest
          ? `Oldest unconfirmed figure is from ${dateLabel(totals.reviewed_oldest)}.`
          : 'Nothing reviewed yet.'}
        {totals.stale_count > 0
          && ` ${totals.stale_count} row(s) have gone past the point they can be trusted.`}
        {totals.drifting_count > 0 && ` ${totals.drifting_count} disagree with your statements.`}
      </div>
    </Card>
  );
}

function Snapshots({ snapshots, onOpen, onDelete }) {
  if (!snapshots.length) return null;
  return (
    <Card title="Every review" sub="What you were carrying, each time you signed it off."
      pad={false}>
      <Table>
        <thead>
          <tr>
            <th>Reviewed</th><th>Note</th>
            <th className="right">Owed</th><th className="right">EMIs a month</th>
            <th className="right">Items</th><th />
          </tr>
        </thead>
        <tbody>
          {snapshots.map((s) => (
            <tr key={s.id}>
              <td className="nowrap">
                <button type="button" className="drill" onClick={() => onOpen(s.id)}>
                  {dateLabel(s.taken_on)}
                </button>
              </td>
              <td>{s.note || <span className="dim">—</span>}</td>
              <td className="right num nowrap">{money(s.totals?.total_owed)}</td>
              <td className="right num nowrap">{money(s.totals?.monthly_emi)}</td>
              <td className="right num">{s.item_count}</td>
              <td className="right">
                <ConfirmButton
                  size="xs" variant="danger"
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
      </Table>
    </Card>
  );
}

/* ── the tables ──────────────────────────────────────────────────────────── */

function Mapping({ item, mappable, onSave }) {
  const accounts = mappable?.accounts || [];
  const bureau = mappable?.bureau || [];
  return (
    <div className="col" style={{ gap: 4 }}>
      <select
        value={item.account_id || ''}
        onChange={(e) => onSave({ account_id: e.target.value || null })}
        style={{ fontSize: 12, maxWidth: 190, height: 26 }}
        title="The statement this is the same thing as"
      >
        <option value="">No statement</option>
        {accounts.map((a) => (
          <option key={a.id} value={a.id}
            disabled={Boolean(a.claimed_by) && a.claimed_by !== item.id}>
            {a.name}{a.claimed_by && a.claimed_by !== item.id ? ' (taken)' : ''}
          </option>
        ))}
      </select>
      {bureau.length > 0 && (
        <select
          value={item.bureau_account_id || ''}
          onChange={(e) => onSave({ bureau_account_id: e.target.value || null })}
          style={{ fontSize: 12, maxWidth: 190, height: 26 }}
          title="The line on your credit report this is the same debt as"
        >
          <option value="">Not on the credit report</option>
          {bureau.map((b) => (
            <option key={b.id} value={b.id}
              disabled={Boolean(b.claimed_by) && b.claimed_by !== item.id}>
              {b.lender} {b.masked || ''}
              {b.claimed_by && b.claimed_by !== item.id ? ' (taken)' : ''}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

function RowActions({ item, onReview, onRemove }) {
  return (
    <div className="row tight" style={{ justifyContent: 'flex-end' }}>
      <Button size="xs" onClick={() => onReview(item.id)}
        title="This is right, as of today. Resets what it ages from.">
        Confirm
      </Button>
      <ConfirmButton
        size="xs" variant="danger"
        question="Remove this from your position? It is kept on any review you have already saved."
        confirmLabel="Remove"
        onConfirm={() => onRemove(item.id)}
      >
        ✕
      </ConfirmButton>
    </div>
  );
}

/* A figure that has aged, and the baseline it aged from. The whole design in
   one place: the big number is what the balance must be today, the small one
   under it is what was actually confirmed and when. */
function Aged({ item }) {
  const now = item.outstanding;
  const was = item.attested_outstanding;
  const moved = was != null && now != null && Math.abs(was - now) >= 1;
  return (
    <div className="right">
      <div className="num" style={{ fontWeight: 620 }}>{now == null ? '—' : money(now)}</div>
      {moved && (
        <div className="tiny dim">
          from {money(was)}
          {item.emis_since_review
            ? ` · ${item.emis_since_review} EMI${item.emis_since_review > 1 ? 's' : ''} on` : ''}
        </div>
      )}
    </div>
  );
}

/* Why this row's number is what it is. Every figure in this app can be traced
   to the document it came from; an attested one has no document, so it carries
   a sentence instead - and the sentence is not optional, because a number a
   person typed and a number a bank printed must never look the same. */
function Basis({ item }) {
  return (
    <div className="row tight">
      <span className="tiny dim">{item.basis}</span>
      {item.stale && <Chip tone="warn">needs a look</Chip>}
      {item.drift != null && (
        <Chip tone="neg" title="Your figure and the statements disagree">
          off by {money(Math.abs(item.drift))}
        </Chip>
      )}
      {item.derived?.length > 0 && (
        <Chip title={`Worked out, not confirmed: ${item.derived.join(', ')}`}>
          {item.derived.length} worked out
        </Chip>
      )}
    </div>
  );
}

function LoanTable({ items, mappable, onPatch, onReview, onRemove }) {
  const { sorted, sort, by } = useSorted(items, { key: 'outstanding', dir: 'desc' });
  return (
    <Table>
      <thead>
        <tr>
          <SortHeader label="Loan" field="label" sort={sort} onSort={by} />
          <SortHeader label="Outstanding" field="outstanding" sort={sort} onSort={by}
            align="right" title="What it must be today, rolled forward from your review" />
          <SortHeader label="EMI" field="emi" sort={sort} onSort={by} align="right" />
          <SortHeader label="Rate" field="interest_rate" sort={sort} onSort={by} align="right" />
          <SortHeader label="Left" field="months_remaining" sort={sort} onSort={by}
            align="right" title="Instalments remaining" />
          <SortHeader label="Paid off" field="payoff_date" sort={sort} onSort={by} />
          <SortHeader label="Interest to come" field="total_interest_remaining" sort={sort}
            onSort={by} align="right" />
          <SortHeader label="EMI day" field="due_day" sort={sort} onSort={by} align="right" />
          <th>Same as</th>
          <SortHeader label="Reviewed" field="reviewed_on" sort={sort} onSort={by} />
          <th />
        </tr>
      </thead>
      <tbody>
        {sorted.map((item) => (
          <React.Fragment key={item.id}>
            <tr>
              <td>
                <InlineEdit value={item.label} width={170} align="left"
                  onSave={(v) => onPatch(item.id, { label: v })} />
                {item.institution && <div className="tiny dim">{item.institution}</div>}
              </td>
              <td className="right">
                <Aged item={item} />
                <InlineEdit value={item.attested_outstanding} type="money" width={110}
                  onSave={(v) => onPatch(item.id, { outstanding: v })} />
              </td>
              <td className="right">
                <InlineEdit value={item.emi} type="money" width={90}
                  onSave={(v) => onPatch(item.id, { emi: v })} />
              </td>
              <td className="right">
                <InlineEdit value={item.interest_rate} type="number" suffix="%" width={62}
                  onSave={(v) => onPatch(item.id, { interest_rate: v })} />
              </td>
              <td className="right">
                <InlineEdit value={item.months_remaining} type="number" width={52}
                  onSave={(v) => onPatch(item.id, { months_remaining: v })} />
              </td>
              <td className="nowrap">
                {item.payoff_date ? dateLabel(item.payoff_date) : '—'}
              </td>
              <td className="right num nowrap">
                {item.total_interest_remaining == null ? '—' : money(item.total_interest_remaining)}
              </td>
              <td className="right">
                <InlineEdit value={item.due_day} type="number" width={38}
                  onSave={(v) => onPatch(item.id, { due_day: v })} />
              </td>
              <td>
                <Mapping item={item} mappable={mappable}
                  onSave={(fields) => onPatch(item.id, fields)} />
              </td>
              <td className="nowrap">
                <InlineEdit value={item.reviewed_on} type="date" width={110} align="left"
                  onSave={(v) => onPatch(item.id, { reviewed_on: v })} />
              </td>
              <td><RowActions item={item} onReview={onReview} onRemove={onRemove} /></td>
            </tr>
            <tr className="no-hover">
              <td colSpan={11} style={{ paddingTop: 0, borderTop: 0 }}>
                <Basis item={item} />
                {item.warnings?.map((w, i) => (
                  <div key={i} className="tiny warnc" style={{ marginTop: 3 }}>{w}</div>
                ))}
              </td>
            </tr>
          </React.Fragment>
        ))}
      </tbody>
    </Table>
  );
}

function CardTable({ items, mappable, onPatch, onReview, onRemove }) {
  const { sorted, sort, by } = useSorted(items, { key: 'utilisation_pct', dir: 'desc' });
  return (
    <Table>
      <thead>
        <tr>
          <SortHeader label="Card" field="label" sort={sort} onSort={by} />
          <SortHeader label="Outstanding" field="outstanding" sort={sort} onSort={by} align="right" />
          <SortHeader label="Limit" field="credit_limit" sort={sort} onSort={by} align="right" />
          <SortHeader label="Used" field="utilisation_pct" sort={sort} onSort={by} align="right"
            title="Utilisation. Above 30% is what bureaus generally start to mark down." />
          <SortHeader label="Min due" field="min_due" sort={sort} onSort={by} align="right" />
          <SortHeader label="Statement" field="statement_day" sort={sort} onSort={by}
            align="right" title="Day of the month it is generated" />
          <SortHeader label="Due" field="next_due_on" sort={sort} onSort={by}
            title="The next payment date" />
          <SortHeader label="In" field="days_to_due" sort={sort} onSort={by} align="right" />
          <th>Same as</th>
          <SortHeader label="Reviewed" field="reviewed_on" sort={sort} onSort={by} />
          <th />
        </tr>
      </thead>
      <tbody>
        {sorted.map((item) => (
          <React.Fragment key={item.id}>
            <tr>
              <td>
                <InlineEdit value={item.label} width={160} align="left"
                  onSave={(v) => onPatch(item.id, { label: v })} />
                {item.institution && <div className="tiny dim">{item.institution}</div>}
              </td>
              <td className="right">
                <InlineEdit value={item.outstanding} type="money" width={100}
                  onSave={(v) => onPatch(item.id, { outstanding: v })} />
              </td>
              <td className="right">
                <InlineEdit value={item.credit_limit} type="money" width={100}
                  onSave={(v) => onPatch(item.id, { credit_limit: v })} />
              </td>
              <td className="right num nowrap">
                {item.utilisation_pct == null ? '—' : (
                  <span style={{
                    fontWeight: 620,
                    color: item.utilisation_pct >= 70 ? 'var(--neg)'
                      : item.utilisation_pct >= 30 ? 'var(--warn)' : 'var(--pos)',
                  }}>
                    {pct(item.utilisation_pct, 0)}
                  </span>
                )}
              </td>
              <td className="right">
                <InlineEdit value={item.min_due} type="money" width={80}
                  onSave={(v) => onPatch(item.id, { min_due: v })} />
              </td>
              <td className="right">
                <InlineEdit value={item.statement_day} type="number" width={38}
                  onSave={(v) => onPatch(item.id, { statement_day: v })} />
              </td>
              <td className="nowrap">
                <InlineEdit value={item.due_day} type="number" width={38} align="left"
                  placeholder="set day"
                  onSave={(v) => onPatch(item.id, { due_day: v })} />
                {item.next_due_on && (
                  <span className="tiny dim" style={{ marginLeft: 6 }}>
                    {dateLabel(item.next_due_on)}
                  </span>
                )}
              </td>
              <td className="right num nowrap">
                {item.days_to_due == null ? '—' : (
                  <span style={{
                    color: item.days_to_due <= 3 ? 'var(--neg)'
                      : item.days_to_due <= 7 ? 'var(--warn)' : 'inherit',
                  }}>
                    {item.days_to_due}d
                  </span>
                )}
              </td>
              <td>
                <Mapping item={item} mappable={mappable}
                  onSave={(fields) => onPatch(item.id, fields)} />
              </td>
              <td className="nowrap">
                <InlineEdit value={item.reviewed_on} type="date" width={110} align="left"
                  onSave={(v) => onPatch(item.id, { reviewed_on: v })} />
              </td>
              <td><RowActions item={item} onReview={onReview} onRemove={onRemove} /></td>
            </tr>
            <tr className="no-hover">
              <td colSpan={11} style={{ paddingTop: 0, borderTop: 0 }}>
                <Basis item={item} />
              </td>
            </tr>
          </React.Fragment>
        ))}
      </tbody>
    </Table>
  );
}

function HoldingTable({ items, mappable, onPatch, onReview, onRemove }) {
  const { sorted, sort, by } = useSorted(items, { key: 'outstanding', dir: 'desc' });
  return (
    <Table>
      <thead>
        <tr>
          <SortHeader label="What" field="label" sort={sort} onSort={by} />
          <SortHeader label="Kind" field="kind" sort={sort} onSort={by} />
          <SortHeader label="Balance" field="outstanding" sort={sort} onSort={by} align="right" />
          <th>Same as</th>
          <SortHeader label="Reviewed" field="reviewed_on" sort={sort} onSort={by} />
          <th />
        </tr>
      </thead>
      <tbody>
        {sorted.map((item) => (
          <tr key={item.id}>
            <td>
              <InlineEdit value={item.label} width={190} align="left"
                onSave={(v) => onPatch(item.id, { label: v })} />
              <Basis item={item} />
            </td>
            <td>
              <Select
                value={item.kind}
                onChange={(v) => onPatch(item.id, { kind: v })}
                options={[['account', 'Account'], ['investment', 'Investment'], ['other', 'Other']]}
                style={{ fontSize: 12, height: 26 }}
              />
            </td>
            <td className="right">
              <InlineEdit value={item.outstanding} type="money" width={110}
                onSave={(v) => onPatch(item.id, { outstanding: v })} />
            </td>
            <td>
              <Mapping item={item} mappable={mappable}
                onSave={(fields) => onPatch(item.id, fields)} />
            </td>
            <td className="nowrap">
              <InlineEdit value={item.reviewed_on} type="date" width={110} align="left"
                onSave={(v) => onPatch(item.id, { reviewed_on: v })} />
            </td>
            <td><RowActions item={item} onReview={onReview} onRemove={onRemove} /></td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
