import React, { useMemo, useState } from 'react';
import { dateLabel, money, titleCase } from '../../lib';
import { Callout, Chip, Empty } from '../ui';

/* What a transaction-alert scan found, and what it would do with each one.
 *
 * Every alert is listed, including the ones the server refused, because a
 * silent skip is indistinguishable from a scan that simply missed something.
 * Only the importable ones can be ticked - the rest carry the reason they
 * cannot be, which is usually "no account here ends 1234" and is genuinely
 * useful: it says which statement is missing. */

const STATUS = {
  imported: { tone: 'pos', label: 'ready' },
  duplicate: { tone: '', label: 'already here' },
  superseded: { tone: '', label: 'statement already has it' },
  skipped: { tone: 'warn', label: 'not imported' },
};

export default function AlertReview({ alerts, selected, onToggle, onToggleAll }) {
  const [showSkipped, setShowSkipped] = useState(false);

  const importable = useMemo(
    () => alerts.filter((a) => a.status === 'imported'), [alerts]);
  const rest = useMemo(
    () => alerts.filter((a) => a.status !== 'imported'), [alerts]);

  const byReason = useMemo(() => {
    const counts = new Map();
    for (const one of rest) {
      const key = `${STATUS[one.status]?.label || one.status}`;
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [rest]);

  if (!alerts.length) {
    return (
      <Empty title="No transaction alerts found">
        Nothing in the last two months matched. Alerts only help when your bank
        sends them, and only for accounts already imported here.
      </Empty>
    );
  }

  const total = importable
    .filter((a) => selected.has(a.message_id))
    .reduce((sum, a) => sum + (Number(a.amount) || 0), 0);

  return (
    <>
      <Callout tone="warn">
        <strong>These are not reconciled.</strong> An alert has no opening or
        closing balance to check against, so each one is imported as an
        unreviewed row and is replaced automatically once the statement
        covering it arrives. They exist to cover the fortnight before a
        statement is cut, not to replace it.
      </Callout>

      {rest.length > 0 && (
        <div style={{
          display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap',
          padding: '9px 12px', borderRadius: 8, background: 'var(--surface-2)',
          border: '1px solid var(--border)', fontSize: 12.5,
        }}>
          <span><strong>{rest.length}</strong> not importable</span>
          {byReason.map(([label, count]) => (
            <Chip key={label}>{count} {label}</Chip>
          ))}
          <button className="btn" style={{ marginLeft: 'auto', padding: '3px 10px', fontSize: 12 }}
            onClick={() => setShowSkipped((v) => !v)}>
            {showSkipped ? 'Hide' : 'Review'}
          </button>
        </div>
      )}

      <div style={{
        display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center',
        padding: '9px 12px', borderRadius: 8, background: 'var(--surface-2)',
        border: '1px solid var(--border)', fontSize: 12.5,
      }}>
        <span>
          <strong className="num">
            {importable.filter((a) => selected.has(a.message_id)).length}
          </strong>{' '}
          of {importable.length} selected
        </span>
        <span className="num" style={{ color: 'var(--text-3)' }}>{money(total)}</span>
        <button className="btn" style={{ marginLeft: 'auto', padding: '3px 10px', fontSize: 12 }}
          onClick={onToggleAll}>
          Select / deselect all
        </button>
      </div>

      <div className="table-wrap scroll-y" style={{ maxHeight: 380 }}>
        <table>
          <thead>
            <tr>
              <th style={{ width: 30 }} />
              <th>Date</th>
              <th>Account</th>
              <th>Payee</th>
              <th className="right">Amount</th>
              <th>From</th>
            </tr>
          </thead>
          <tbody>
            {importable.map((one) => {
              const checked = selected.has(one.message_id);
              return (
                <tr key={one.message_id} style={{ opacity: checked ? 1 : 0.5 }}>
                  <td>
                    <input type="checkbox" checked={checked}
                      onChange={() => onToggle(one.message_id)} />
                  </td>
                  <td className="nowrap num">
                    {one.date_iso ? dateLabel(one.date_iso) : '—'}
                  </td>
                  <td>
                    <div className="truncate" style={{ maxWidth: 190 }}>
                      {one.account || '—'}
                    </div>
                  </td>
                  <td>
                    <div className="truncate" style={{ maxWidth: 190 }}
                      title={one.subject}>
                      {one.merchant || titleCase(one.direction)}
                    </div>
                  </td>
                  <td className="right num">
                    <span className={one.direction === 'credit' ? 'pos' : ''}>
                      {one.direction === 'credit' ? '+' : ''}
                      {money(Number(one.amount) || 0)}
                    </span>
                  </td>
                  <td>
                    <div className="truncate" style={{ maxWidth: 140 }}
                      title={one.sender}>
                      {one.sender_name}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {showSkipped && (
        <div className="table-wrap scroll-y" style={{ maxHeight: 260 }}>
          <table>
            <thead>
              <tr>
                <th>From</th><th>Subject</th><th>Amount</th><th>Why not</th>
              </tr>
            </thead>
            <tbody>
              {rest.map((one) => (
                <tr key={one.message_id}>
                  <td>
                    <div className="truncate" style={{ maxWidth: 140 }}>
                      {one.sender_name}
                    </div>
                  </td>
                  <td>
                    <div className="truncate" style={{ maxWidth: 240 }}
                      title={one.subject}>{one.subject}</div>
                  </td>
                  <td className="num nowrap">
                    {one.amount ? money(Number(one.amount)) : '—'}
                  </td>
                  <td>
                    <Chip tone={STATUS[one.status]?.tone}>
                      {STATUS[one.status]?.label || one.status}
                    </Chip>
                    <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
                      {one.reason}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
