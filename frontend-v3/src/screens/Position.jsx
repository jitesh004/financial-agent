import React, { useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { useToast } from '../core/toast';
import { dateLabel, money, pct, today } from '../core/format';
import {
  Button, Callout, Card, GlassCard, Chip, ConfirmButton, Empty, InlineEdit, Loading, Select,
  SortHeader, Stat, Table, useSorted, Badge,
} from '../ui';
import { Icon } from '../ui/icons';

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
      toast.fail('Action failed', e.message);
    } finally {
      setBusy(false);
    }
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
    notes: 'Adopted directly from credit bureau file.',
  }));

  if (position.loading) return <Loading message="Compiling certified position telemetry…" />;
  if (position.error) return <Callout tone="neg">{position.error.message}</Callout>;

  const items = data?.items || [];
  const live = items.filter((i) => !i.archived);
  const archived = items.filter((i) => i.archived);
  const loans = live.filter((i) => i.kind === 'loan');
  const cards = live.filter((i) => i.kind === 'card');
  const holdings = live.filter((i) => !['loan', 'card'].includes(i.kind));
  const blindSpots = data?.unaccounted?.bureau || [];

  return (
    <div className="position-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)' }}>
      {/* Header */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>Certified Net Position</h1>
            <Badge tone="brand" size="sm">
              As at {dateLabel(data?.as_of)}
            </Badge>
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            Your attested balance sheet. Every balance carries a confirmation timestamp and rolls forward deterministically
            through verified amortizations.
          </p>
        </div>
      </div>

      {notice && (
        <Callout tone="info">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>{notice}</span>
            <Button size="xs" onClick={() => setNotice(null)}>Dismiss</Button>
          </div>
        </Callout>
      )}

      {!items.length ? (
        <Empty
          title="No certified position established yet"
          icon="shield"
          action={(
            <Button variant="primary" busy={busy} onClick={() => guard(() => api.seedPosition())}>
              Seed from Document Imports
            </Button>
          )}
        >
          Auto-populate all confirmed loans, cards, and balances from your imported statements and bureau reports.
        </Empty>
      ) : (
        <>
          {/* Top Level Metric Stats */}
          <Totals totals={data.totals} />

          {/* Blind Spots Banner */}
          {blindSpots.length > 0 && <BlindSpots bureau={blindSpots} onAdopt={adopt} />}

          {/* Loans Table */}
          {loans.length > 0 && (
            <Card
              title="Contracted Loans"
              subtitle="Rolled forward from review date via exact contractual amortization. Click values to update."
              pad={false}
            >
              <LoanTable
                items={loans}
                mappable={mappable.data}
                onPatch={patch}
                onReview={review}
                onRemove={remove}
              />
            </Card>
          )}

          {/* Credit Cards Table */}
          {cards.length > 0 && (
            <Card
              title="Credit Cards & Revolving Lines"
              subtitle="Card balances track statement cycles and reflect attested reviews."
              pad={false}
            >
              <CardTable
                items={cards}
                mappable={mappable.data}
                onPatch={patch}
                onReview={review}
                onRemove={remove}
              />
            </Card>
          )}

          {/* Asset Holdings */}
          {holdings.length > 0 && (
            <Card title="Liquid Balances & Non-Liability Holdings" pad={false}>
              <HoldingTable
                items={holdings}
                mappable={mappable.data}
                onPatch={patch}
                onReview={review}
                onRemove={remove}
              />
            </Card>
          )}

          {/* Add Item Actions */}
          <GlassCard style={{ padding: 'var(--space-5)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 'var(--space-4)' }}>
              <div>
                <h4 className="h4" style={{ margin: 0 }}>Append External Item</h4>
                <p className="tiny muted" style={{ margin: '2px 0 0 0' }}>
                  Track informal debts, family loans, or offline assets not covered by imported statements.
                </p>
              </div>

              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
                {['loan', 'card', 'account', 'investment', 'other'].map((kind) => (
                  <Button
                    key={kind}
                    size="sm"
                    disabled={busy}
                    onClick={() => guard(() => api.addPositionItem({
                      kind,
                      label: `New ${kind}`,
                      reviewed_on: today(),
                    }))}
                  >
                    + {kind}
                  </Button>
                ))}
                <Button
                  size="sm"
                  variant="primary"
                  disabled={busy}
                  onClick={() => guard(() => api.seedPosition())}
                  title="Detect newly imported accounts"
                >
                  Sync Imports
                </Button>
                {archived.length > 0 && (
                  <Button size="sm" onClick={() => setShowArchived((v) => !v)}>
                    {showArchived ? 'Hide Archived' : `Archived (${archived.length})`}
                  </Button>
                )}
              </div>
            </div>

            {showArchived && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', marginTop: 'var(--space-4)', paddingTop: 'var(--space-3)', borderTop: '1px solid var(--border-subtle)' }}>
                {archived.map((item) => (
                  <Chip key={item.id}>
                    {item.label}
                    <button
                      className="btn link"
                      style={{ marginLeft: 6 }}
                      onClick={() => patch(item.id, { archived: false })}
                    >
                      Restore
                    </button>
                  </Chip>
                ))}
              </div>
            )}
          </GlassCard>

          {/* Freeze / Sign-Off Bar */}
          <ReviewBar
            totals={data.totals}
            busy={busy}
            onReview={(note) => guard(() => api.reviewPosition({ note }))}
          />

          {/* Historical Reviews Snapshots */}
          <Snapshots
            snapshots={snapshots.data?.snapshots || []}
            onOpen={(id) => api.positionSnapshot(id)
              .then((s) => setNotice(
                `On ${dateLabel(s.taken_on)} you signed off ${money(s.totals?.total_owed)} owed across ${s.item_count} obligations (EMIs: ${money(s.totals?.monthly_emi)}/mo)${s.note ? ` — "${s.note}"` : ''}.`
              ))
              .catch((e) => toast.fail('Snapshot retrieval failed', e.message))}
            onDelete={(id) => guard(() => api.deletePositionSnapshot(id))}
          />
        </>
      )}
    </div>
  );
}

function Totals({ totals = {} }) {
  const unknown = totals.unknown || {};
  const blanks = (unknown.loans || 0) + (unknown.cards || 0) + (unknown.assets || 0);

  return (
    <>
      <div className="stats-grid">
        <Stat
          label="Total Liabilities Outstanding"
          value={totals.total_owed == null ? '—' : money(totals.total_owed)}
          tone={totals.total_owed ? 'neg' : undefined}
          sub={totals.unconfirmed_bureau_debt
            ? `${totals.loan_count || 0} Loans · ${totals.card_count || 0} Cards · plus ${money(totals.unconfirmed_bureau_debt)} unconfirmed`
            : `${totals.loan_count || 0} Loans · ${totals.card_count || 0} Cards`}
        />
        <Stat
          label="Monthly Debt Outflow"
          value={totals.monthly_emi == null ? '—' : money(totals.monthly_emi)}
          sub="Contracted EMIs and instalments"
        />
        <Stat
          label="Aggregate Card Utilization"
          value={totals.card_utilisation_pct == null ? '—' : `${totals.card_utilisation_pct}%`}
          tone={totals.card_utilisation_pct >= 30 ? 'warn' : 'pos'}
          sub={totals.card_utilisation_pct == null
            ? 'No card carries both balance & limit'
            : `${money(totals.card_outstanding)} of ${money(totals.credit_limit)}`}
        />
        <Stat
          label="Projected Debt Freedom"
          value={totals.debt_free_on ? dateLabel(totals.debt_free_on) : '—'}
          tone="brand"
          sub={totals.interest_remaining
            ? `${money(totals.interest_remaining)} remaining finance charge`
            : 'Requires active balance, EMI & rate'}
        />
      </div>

      {/* Net worth, stated on the screen that owns the balances - and stated
          on the SAME basis the Overview uses, which includes debt the bureau
          reports but nothing here has adopted. Publishing only the exclusive
          figure is what left the two screens 27,659 apart with neither of
          them saying so. */}
      {totals.net_including_unconfirmed != null && (
        <Callout>
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <strong>Net worth {money(totals.net_including_unconfirmed)}</strong>
              {' — '}{money(totals.assets)} tracked against{' '}
              {money(totals.total_owed_including_unconfirmed)} owed.
            </div>
            {totals.unconfirmed_bureau_debt > 0 && (
              <span className="tiny muted">
                Includes {money(totals.unconfirmed_bureau_debt)} the credit
                bureau reports that no account here has adopted. Excluding it,
                net worth is {money(totals.net)}.
              </span>
            )}
          </div>
        </Callout>
      )}

      {blanks > 0 && (
        <Callout tone="warn">
          {blanks} position item{blanks > 1 ? 's lack' : ' lacks'} verified balance amounts.
          Update them to ensure net liability calculations tie out perfectly.
        </Callout>
      )}
    </>
  );
}

function BlindSpots({ bureau, onAdopt }) {
  return (
    <Callout tone="neg">
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
        <Icon name="alert" size={18} />
        <strong>{bureau.length} Unmapped Credit Bureau Account{bureau.length > 1 ? 's' : ''} Detected</strong>
      </div>
      <p style={{ margin: '6px 0 12px 0', overflowWrap: 'anywhere' }} className="small">
        Credit bureaus report active liabilities that do not yet exist in your position telemetry.
        Adopt them below to eliminate reporting blind spots:
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {bureau.map((b) => (
          <div key={b.id} style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            flexWrap: 'wrap', gap: 8,
            padding: '6px 12px', background: 'var(--surface-2)', borderRadius: 'var(--radius-md)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', minWidth: 0 }}>
              <strong className="small">{b.lender}</strong>
              <Chip size="sm">{String(b.type || '').replace(/_/g, ' ')}</Chip>
              {b.masked && <span className="tiny num">{b.masked}</span>}
              {b.balance != null && b.balance !== '' && (
              <span className="tiny num font-semibold neg">{money(Number(b.balance))}</span>
            )}
            </div>
            <Button size="xs" variant="primary" onClick={() => onAdopt(b)}>
              Adopt into Position
            </Button>
          </div>
        ))}
      </div>
    </Callout>
  );
}

function ReviewBar({ totals, onReview, busy }) {
  const [note, setNote] = useState('');
  return (
    <GlassCard style={{ padding: 'var(--space-5)' }}>
      <div style={{ marginBottom: 'var(--space-3)' }}>
        <h3 className="h3" style={{ margin: 0 }}>Freeze Dated Position Audit</h3>
        <p className="tiny muted" style={{ margin: '4px 0 0 0' }}>
          Taking a signed snapshot seals your current net position into immutable historical records for comparison.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
        <input
          className="input"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Optional memo (e.g., Annual review, Post-bonus paydown)"
          style={{ flex: 1, minWidth: 260 }}
        />
        <Button
          variant="primary"
          busy={busy}
          onClick={() => onReview(note).then(() => setNote(''))}
        >
          Sign Off Position As True
        </Button>
      </div>

      <div className="tiny muted" style={{ marginTop: 8 }}>
        {totals.reviewed_oldest
          ? `Oldest unconfirmed balance is from ${dateLabel(totals.reviewed_oldest)}.`
          : 'Zero unconfirmed items.'}
        {totals.stale_count > 0 && ` · ${totals.stale_count} item(s) require re-confirmation.`}
        {totals.drifting_count > 0 && ` · ${totals.drifting_count} show statement drift.`}
      </div>
    </GlassCard>
  );
}

function Snapshots({ snapshots, onOpen, onDelete }) {
  if (!snapshots.length) return null;

  return (
    <Card title="Historical Position Sign-Offs" subtitle="Permanent audited records of prior attested statements" pad={false}>
      <div style={{ overflowX: 'auto' }}>
        <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Memo</th>
              <th style={{ textAlign: 'right' }}>Total Liabilities</th>
              <th style={{ textAlign: 'right' }}>Monthly Outgo</th>
              <th style={{ textAlign: 'right' }}>Accounts</th>
              <th style={{ textAlign: 'right' }}>Action</th>
            </tr>
          </thead>
          <tbody>
            {snapshots.map((s) => (
              <tr key={s.id} className="terminal-row">
                <td className="nowrap font-medium">
                  <button type="button" className="btn link" onClick={() => onOpen(s.id)}>
                    {dateLabel(s.taken_on)}
                  </button>
                </td>
                <td className="muted">{s.note || '—'}</td>
                <td className="num neg nowrap font-semibold" style={{ textAlign: 'right' }}>
                  {money(s.totals?.total_owed)}
                </td>
                <td className="num nowrap" style={{ textAlign: 'right' }}>{money(s.totals?.monthly_emi)}</td>
                <td className="num nowrap" style={{ textAlign: 'right' }}>{s.item_count}</td>
                <td style={{ textAlign: 'right' }}>
                  <ConfirmButton
                    size="xs"
                    variant="danger"
                    question="Permanently erase this historical snapshot?"
                    confirmLabel="Delete"
                    onConfirm={() => onDelete(s.id)}
                  >
                    Delete
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

function Mapping({ item, mappable, onSave }) {
  const accounts = mappable?.accounts || [];
  const bureau = mappable?.bureau || [];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <select
        className="select"
        value={item.account_id || ''}
        onChange={(e) => onSave({ account_id: e.target.value || null })}
        size="xs" style={{ maxWidth: 160 }}
      >
        <option value="">No linked statement</option>
        {accounts.map((a) => (
          <option key={a.id} value={a.id} disabled={Boolean(a.claimed_by) && a.claimed_by !== item.id}>
            {a.name}{a.claimed_by && a.claimed_by !== item.id ? ' (mapped)' : ''}
          </option>
        ))}
      </select>

      {bureau.length > 0 && (
        <select
          className="select"
          value={item.bureau_account_id || ''}
          onChange={(e) => onSave({ bureau_account_id: e.target.value || null })}
          size="xs" style={{ maxWidth: 160 }}
        >
          <option value="">Not in credit bureau</option>
          {bureau.map((b) => (
            <option key={b.id} value={b.id} disabled={Boolean(b.claimed_by) && b.claimed_by !== item.id}>
              {b.lender} {b.masked || ''}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

function RowActions({ item, onReview, onRemove }) {
  return (
    <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end' }}>
      <Button size="xs" onClick={() => onReview(item.id)} title="Attest figure is verified as of today">
        Attest
      </Button>
      <ConfirmButton
        size="xs"
        variant="danger"
        question="Remove item from active position?"
        confirmLabel="Remove"
        onConfirm={() => onRemove(item.id)}
      >
        ✕
      </ConfirmButton>
    </div>
  );
}

function Aged({ item }) {
  const now = item.outstanding;
  const was = item.attested_outstanding;
  const moved = was != null && now != null && Math.abs(was - now) >= 1;
  return (
    <div style={{ textAlign: 'right' }}>
      <div className="num font-semibold">{now == null ? '—' : money(now)}</div>
      {moved && (
        <div className="tiny muted">
          was {money(was)} ({item.emis_since_review || 0} EMIs ago)
        </div>
      )}
    </div>
  );
}

function Basis({ item }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
      <span className="tiny muted">{item.basis}</span>
      {item.stale && <Chip tone="warn" size="sm">Stale</Chip>}
      {item.drift != null && (
        <Chip tone="neg" size="sm" title="Disagrees with imported statement total">
          Drift: {money(Math.abs(item.drift))}
        </Chip>
      )}
    </div>
  );
}

function LoanTable({ items, mappable, onPatch, onReview, onRemove }) {
  const { sorted, sort, by } = useSorted(items, { key: 'outstanding', dir: 'desc' });

  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <SortHeader label="Obligation" field="label" sort={sort} onSort={by} />
            <SortHeader label="Outstanding" field="outstanding" sort={sort} onSort={by} align="right" />
            <SortHeader label="EMI" field="emi" sort={sort} onSort={by} align="right" />
            <SortHeader label="Rate" field="interest_rate" sort={sort} onSort={by} align="right" />
            <SortHeader label="Months Left" field="months_remaining" sort={sort} onSort={by} align="right" />
            <SortHeader label="Payoff Date" field="payoff_date" sort={sort} onSort={by} />
            <SortHeader label="Future Interest" field="total_interest_remaining" sort={sort} onSort={by} align="right" />
            <th>Linked Statement / Bureau</th>
            <SortHeader label="Attested" field="reviewed_on" sort={sort} onSort={by} />
            <th style={{ textAlign: 'right' }}>Action</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((item) => (
            <React.Fragment key={item.id}>
              <tr className="terminal-row">
                <td>
                  <InlineEdit
                    value={item.label}
                    width={160}
                    align="left"
                    onSave={(v) => onPatch(item.id, { label: v })}
                  />
                  {item.institution && <div className="tiny muted">{item.institution}</div>}
                  <Basis item={item} />
                </td>
                <td style={{ textAlign: 'right' }}>
                  <Aged item={item} />
                  <InlineEdit
                    value={item.attested_outstanding}
                    type="money"
                    width={110}
                    label="Attested outstanding"
                    placeholder="Set attested balance"
                    onSave={(v) => onPatch(item.id, { outstanding: v })}
                  />
                </td>
                <td style={{ textAlign: 'right' }}>
                  <InlineEdit
                    value={item.emi}
                    type="money"
                    width={84}
                    onSave={(v) => onPatch(item.id, { emi: v })}
                  />
                </td>
                <td style={{ textAlign: 'right' }}>
                  <InlineEdit
                    value={item.interest_rate}
                    type="number"
                    suffix="%"
                    width={56}
                    onSave={(v) => onPatch(item.id, { interest_rate: v })}
                  />
                </td>
                <td style={{ textAlign: 'right' }}>
                  <InlineEdit
                    value={item.months_remaining}
                    type="number"
                    width={48}
                    onSave={(v) => onPatch(item.id, { months_remaining: v })}
                  />
                </td>
                <td className="nowrap muted tiny">
                  {item.payoff_date ? dateLabel(item.payoff_date) : '—'}
                </td>
                <td className="num neg nowrap" style={{ textAlign: 'right' }}>
                  {item.total_interest_remaining == null ? '—' : money(item.total_interest_remaining)}
                </td>
                <td>
                  <Mapping item={item} mappable={mappable} onSave={(fields) => onPatch(item.id, fields)} />
                </td>
                <td className="nowrap tiny muted">
                  <InlineEdit
                    value={item.reviewed_on}
                    type="date"
                    width={96}
                    align="left"
                    onSave={(v) => onPatch(item.id, { reviewed_on: v })}
                  />
                </td>
                <td style={{ textAlign: 'right' }}>
                  <RowActions item={item} onReview={onReview} onRemove={onRemove} />
                </td>
              </tr>
            </React.Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CardTable({ items, mappable, onPatch, onReview, onRemove }) {
  const { sorted, sort, by } = useSorted(items, { key: 'utilisation_pct', dir: 'desc' });

  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <SortHeader label="Card" field="label" sort={sort} onSort={by} />
            <SortHeader label="Outstanding" field="outstanding" sort={sort} onSort={by} align="right" />
            <SortHeader label="Limit" field="credit_limit" sort={sort} onSort={by} align="right" />
            <SortHeader label="Utilization" field="utilisation_pct" sort={sort} onSort={by} align="right" />
            <SortHeader label="Due In" field="days_to_due" sort={sort} onSort={by} align="right" />
            <th>Linked Statement / Bureau</th>
            <SortHeader label="Attested" field="reviewed_on" sort={sort} onSort={by} />
            <th style={{ textAlign: 'right' }}>Action</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((item) => (
            <tr key={item.id} className="terminal-row">
              <td>
                <InlineEdit
                  value={item.label}
                  width={160}
                  align="left"
                  onSave={(v) => onPatch(item.id, { label: v })}
                />
                {item.institution && <div className="tiny muted">{item.institution}</div>}
                <Basis item={item} />
              </td>
              <td style={{ textAlign: 'right' }}>
                <InlineEdit
                  value={item.outstanding}
                  type="money"
                  width={96}
                  onSave={(v) => onPatch(item.id, { outstanding: v })}
                />
              </td>
              <td style={{ textAlign: 'right' }}>
                <InlineEdit
                  value={item.credit_limit}
                  type="money"
                  width={96}
                  onSave={(v) => onPatch(item.id, { credit_limit: v })}
                />
              </td>
              <td className="num nowrap" style={{ textAlign: 'right' }}>
                {item.utilisation_pct == null ? '—' : (
                  <Badge tone={item.utilisation_pct >= 30 ? 'warn' : 'pos'} size="sm">
                    {pct(item.utilisation_pct, 0)}
                  </Badge>
                )}
              </td>
              <td className="num nowrap" style={{ textAlign: 'right' }}>
                {item.days_to_due != null ? `${item.days_to_due}d` : '—'}
              </td>
              <td>
                <Mapping item={item} mappable={mappable} onSave={(fields) => onPatch(item.id, fields)} />
              </td>
              <td className="nowrap tiny muted">
                <InlineEdit
                  value={item.reviewed_on}
                  type="date"
                  width={96}
                  align="left"
                  onSave={(v) => onPatch(item.id, { reviewed_on: v })}
                />
              </td>
              <td style={{ textAlign: 'right' }}>
                <RowActions item={item} onReview={onReview} onRemove={onRemove} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HoldingTable({ items, mappable, onPatch, onReview, onRemove }) {
  const { sorted, sort, by } = useSorted(items, { key: 'outstanding', dir: 'desc' });

  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <SortHeader label="Holding" field="label" sort={sort} onSort={by} />
            <SortHeader label="Asset Class" field="kind" sort={sort} onSort={by} />
            <SortHeader label="Attested Value" field="outstanding" sort={sort} onSort={by} align="right" />
            <th>Linked Statement / Account</th>
            <SortHeader label="Attested" field="reviewed_on" sort={sort} onSort={by} />
            <th style={{ textAlign: 'right' }}>Action</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((item) => (
            <tr key={item.id} className="terminal-row">
              <td>
                <InlineEdit
                  value={item.label}
                  width={180}
                  align="left"
                  onSave={(v) => onPatch(item.id, { label: v })}
                />
                <Basis item={item} />
              </td>
              <td>
                <Select
                  value={item.kind}
                  onChange={(v) => onPatch(item.id, { kind: v })}
                  options={[['account', 'Liquid Account'], ['investment', 'Investment'], ['other', 'Other']]}
                  size="xs"
                />
              </td>
              <td style={{ textAlign: 'right' }}>
                <InlineEdit
                  value={item.outstanding}
                  type="money"
                  width={100}
                  onSave={(v) => onPatch(item.id, { outstanding: v })}
                />
              </td>
              <td>
                <Mapping item={item} mappable={mappable} onSave={(fields) => onPatch(item.id, fields)} />
              </td>
              <td className="nowrap tiny muted">
                <InlineEdit
                  value={item.reviewed_on}
                  type="date"
                  width={96}
                  align="left"
                  onSave={(v) => onPatch(item.id, { reviewed_on: v })}
                />
              </td>
              <td style={{ textAlign: 'right' }}>
                <RowActions item={item} onReview={onReview} onRemove={onRemove} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
