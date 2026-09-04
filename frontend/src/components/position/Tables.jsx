import React from 'react';
import { Aged, Basis, Cell, SortHeader, useSorted } from './parts';
import { Chip, ConfirmButton } from '../ui';
import { dateLabel, money } from '../../lib';

/* One table per kind, because the columns that matter genuinely differ.

   A loan is priced by its rate and its remaining term; a card by its limit,
   its utilisation and when the payment is due. Forcing both into one grid
   would give every row a dozen empty cells and hide the four that matter.

   Every column sorts. "Which card is nearest its limit", "which loan has the
   longest left to run", "what is due first" are the questions this screen
   exists for, and each of them is one click. */

function Mapping({ item, mappable, onSave }) {
  const accounts = mappable?.accounts || [];
  const bureau = mappable?.bureau || [];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <select
        value={item.account_id || ''}
        onChange={(e) => onSave({ account_id: e.target.value || null })}
        style={{ fontSize: 12, maxWidth: 190 }}
        title="The statement this is the same thing as"
      >
        <option value="">No statement</option>
        {accounts.map((a) => (
          <option key={a.id} value={a.id}
            disabled={Boolean(a.claimed_by) && a.claimed_by !== item.id}
          >
            {a.name}
            {a.claimed_by && a.claimed_by !== item.id ? ' (taken)' : ''}
          </option>
        ))}
      </select>
      {bureau.length > 0 && (
        <select
          value={item.bureau_account_id || ''}
          onChange={(e) => onSave({ bureau_account_id: e.target.value || null })}
          style={{ fontSize: 12, maxWidth: 190 }}
          title="The line on your credit report this is the same debt as"
        >
          <option value="">Not on the credit report</option>
          {bureau.map((b) => (
            <option key={b.id} value={b.id}
              disabled={Boolean(b.claimed_by) && b.claimed_by !== item.id}
            >
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
    <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
      <button className="btn" onClick={() => onReview(item.id)}
        title="This is right, as of today. Resets what it ages from."
      >
        Confirm
      </button>
      <ConfirmButton className="btn danger"
        question="Remove this from your position? It is kept on any review you have already saved."
        confirmLabel="Remove"
        onConfirm={() => onRemove(item.id)}
      >
        ✕
      </ConfirmButton>
    </div>
  );
}

export function LoanTable({ items, mappable, onPatch, onReview, onRemove }) {
  const { sorted, sort, by } = useSorted(items, { key: 'outstanding',
    dir: 'desc' });
  if (!items.length) return null;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <SortHeader label="Loan" field="label" sort={sort} onSort={by} />
            <SortHeader label="Outstanding" field="outstanding" sort={sort}
              onSort={by} align="right"
              title="What it must be today, rolled forward from your review" />
            <SortHeader label="EMI" field="emi" sort={sort} onSort={by}
              align="right" />
            <SortHeader label="Rate" field="interest_rate" sort={sort}
              onSort={by} align="right" />
            <SortHeader label="Left" field="months_remaining" sort={sort}
              onSort={by} align="right" title="Instalments remaining" />
            <SortHeader label="Paid off" field="payoff_date" sort={sort}
              onSort={by} />
            <SortHeader label="Interest to come" field="total_interest_remaining"
              sort={sort} onSort={by} align="right" />
            <SortHeader label="EMI day" field="due_day" sort={sort} onSort={by}
              align="right" />
            <th>Same as</th>
            <SortHeader label="Reviewed" field="reviewed_on" sort={sort}
              onSort={by} />
            <th />
          </tr>
        </thead>
        <tbody>
          {sorted.map((item) => (
            <React.Fragment key={item.id}>
              <tr>
                <td>
                  <Cell value={item.label} width={170} align="left"
                    onSave={(v) => onPatch(item.id, { label: v })} />
                  {item.institution && (
                    <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>
                      {item.institution}
                    </div>
                  )}
                </td>
                <td className="right">
                  <Aged item={item} />
                  <Cell value={item.attested_outstanding} type="money"
                    width={110}
                    onSave={(v) => onPatch(item.id, { outstanding: v })} />
                </td>
                <td className="right">
                  <Cell value={item.emi} type="money" width={90}
                    onSave={(v) => onPatch(item.id, { emi: v })} />
                </td>
                <td className="right">
                  <Cell value={item.interest_rate} type="number" suffix="%"
                    width={62}
                    onSave={(v) => onPatch(item.id, { interest_rate: v })} />
                </td>
                <td className="right">
                  <Cell value={item.months_remaining} type="number" width={52}
                    onSave={(v) => onPatch(item.id, { months_remaining: v })} />
                </td>
                <td className="nowrap">
                  {item.payoff_date ? dateLabel(item.payoff_date) : '—'}
                </td>
                <td className="right num nowrap">
                  {item.total_interest_remaining == null ? '—'
                    : money(item.total_interest_remaining)}
                </td>
                <td className="right">
                  <Cell value={item.due_day} type="number" width={38}
                    placeholder="—"
                    onSave={(v) => onPatch(item.id, { due_day: v })} />
                </td>
                <td><Mapping item={item} mappable={mappable}
                  onSave={(fields) => onPatch(item.id, fields)} /></td>
                <td className="nowrap">
                  <Cell value={item.reviewed_on} type="date" width={120}
                    align="left"
                    onSave={(v) => onPatch(item.id, { reviewed_on: v })} />
                </td>
                <td><RowActions item={item} onReview={onReview}
                  onRemove={onRemove} /></td>
              </tr>
              <tr>
                <td colSpan={11} style={{ paddingTop: 0, borderTop: 0 }}>
                  <Basis item={item} />
                  {item.warnings?.map((w, i) => (
                    <div key={i} style={{ fontSize: 12.5,
                      color: 'var(--warn)', marginTop: 3 }}
                    >
                      {w}
                    </div>
                  ))}
                </td>
              </tr>
            </React.Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CardTable({ items, mappable, onPatch, onReview, onRemove }) {
  const { sorted, sort, by } = useSorted(items, { key: 'utilisation_pct',
    dir: 'desc' });
  if (!items.length) return null;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <SortHeader label="Card" field="label" sort={sort} onSort={by} />
            <SortHeader label="Outstanding" field="outstanding" sort={sort}
              onSort={by} align="right" />
            <SortHeader label="Limit" field="credit_limit" sort={sort}
              onSort={by} align="right" />
            <SortHeader label="Used" field="utilisation_pct" sort={sort}
              onSort={by} align="right"
              title="Utilisation. Above 30% is what bureaus generally start to mark down." />
            <SortHeader label="Min due" field="min_due" sort={sort} onSort={by}
              align="right" />
            <SortHeader label="Statement" field="statement_day" sort={sort}
              onSort={by} align="right" title="Day of the month it is generated" />
            <SortHeader label="Due" field="next_due_on" sort={sort} onSort={by}
              title="The next payment date" />
            <SortHeader label="In" field="days_to_due" sort={sort} onSort={by}
              align="right" />
            <th>Same as</th>
            <SortHeader label="Reviewed" field="reviewed_on" sort={sort}
              onSort={by} />
            <th />
          </tr>
        </thead>
        <tbody>
          {sorted.map((item) => (
            <React.Fragment key={item.id}>
              <tr>
                <td>
                  <Cell value={item.label} width={160} align="left"
                    onSave={(v) => onPatch(item.id, { label: v })} />
                  {item.institution && (
                    <div style={{ fontSize: 11.5, color: 'var(--text-3)' }}>
                      {item.institution}
                    </div>
                  )}
                </td>
                <td className="right">
                  <Cell value={item.outstanding} type="money" width={100}
                    onSave={(v) => onPatch(item.id, { outstanding: v })} />
                </td>
                <td className="right">
                  <Cell value={item.credit_limit} type="money" width={100}
                    onSave={(v) => onPatch(item.id, { credit_limit: v })} />
                </td>
                <td className="right num nowrap">
                  {item.utilisation_pct == null ? '—' : (
                    <span style={{
                      color: item.utilisation_pct >= 70 ? 'var(--negative)'
                        : item.utilisation_pct >= 30 ? 'var(--warn)'
                          : 'var(--positive)',
                      fontWeight: 600,
                    }}
                    >
                      {item.utilisation_pct}%
                    </span>
                  )}
                </td>
                <td className="right">
                  <Cell value={item.min_due} type="money" width={80}
                    onSave={(v) => onPatch(item.id, { min_due: v })} />
                </td>
                <td className="right">
                  <Cell value={item.statement_day} type="number" width={38}
                    placeholder="—"
                    onSave={(v) => onPatch(item.id, { statement_day: v })} />
                </td>
                <td className="nowrap">
                  <Cell value={item.due_day} type="number" width={38}
                    placeholder="set day" align="left"
                    onSave={(v) => onPatch(item.id, { due_day: v })} />
                  {item.next_due_on && (
                    <span style={{ fontSize: 11.5, color: 'var(--text-3)',
                      marginLeft: 6 }}
                    >
                      {dateLabel(item.next_due_on)}
                    </span>
                  )}
                </td>
                <td className="right num nowrap">
                  {item.days_to_due == null ? '—' : (
                    <span style={{
                      color: item.days_to_due <= 3 ? 'var(--negative)'
                        : item.days_to_due <= 7 ? 'var(--warn)' : 'inherit',
                    }}
                    >
                      {item.days_to_due}d
                    </span>
                  )}
                </td>
                <td><Mapping item={item} mappable={mappable}
                  onSave={(fields) => onPatch(item.id, fields)} /></td>
                <td className="nowrap">
                  <Cell value={item.reviewed_on} type="date" width={120}
                    align="left"
                    onSave={(v) => onPatch(item.id, { reviewed_on: v })} />
                </td>
                <td><RowActions item={item} onReview={onReview}
                  onRemove={onRemove} /></td>
              </tr>
              <tr>
                <td colSpan={11} style={{ paddingTop: 0, borderTop: 0 }}>
                  <Basis item={item} />
                </td>
              </tr>
            </React.Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function HoldingTable({ items, mappable, onPatch, onReview, onRemove }) {
  const { sorted, sort, by } = useSorted(items, { key: 'outstanding',
    dir: 'desc' });
  if (!items.length) return null;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <SortHeader label="What" field="label" sort={sort} onSort={by} />
            <SortHeader label="Kind" field="kind" sort={sort} onSort={by} />
            <SortHeader label="Balance" field="outstanding" sort={sort}
              onSort={by} align="right" />
            <th>Same as</th>
            <SortHeader label="Reviewed" field="reviewed_on" sort={sort}
              onSort={by} />
            <th />
          </tr>
        </thead>
        <tbody>
          {sorted.map((item) => (
            <tr key={item.id}>
              <td>
                <Cell value={item.label} width={190} align="left"
                  onSave={(v) => onPatch(item.id, { label: v })} />
                <Basis item={item} />
              </td>
              <td>
                <select value={item.kind} style={{ fontSize: 12 }}
                  onChange={(e) => onPatch(item.id, { kind: e.target.value })}
                >
                  <option value="account">Account</option>
                  <option value="investment">Investment</option>
                  <option value="other">Other</option>
                </select>
              </td>
              <td className="right">
                <Cell value={item.outstanding} type="money" width={110}
                  onSave={(v) => onPatch(item.id, { outstanding: v })} />
              </td>
              <td><Mapping item={item} mappable={mappable}
                onSave={(fields) => onPatch(item.id, fields)} /></td>
              <td className="nowrap">
                <Cell value={item.reviewed_on} type="date" width={120}
                  align="left"
                  onSave={(v) => onPatch(item.id, { reviewed_on: v })} />
              </td>
              <td><RowActions item={item} onReview={onReview}
                onRemove={onRemove} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ArchivedList({ items, onPatch }) {
  if (!items.length) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      {items.map((item) => (
        <Chip key={item.id}>
          {item.label}
          <button className="btn" style={{ marginLeft: 8, padding: '0 6px' }}
            onClick={() => onPatch(item.id, { archived: false })}
          >
            restore
          </button>
        </Chip>
      ))}
    </div>
  );
}
