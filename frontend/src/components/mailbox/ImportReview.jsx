import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api, money } from '../../lib';
import { Callout, Chip, Empty } from '../ui';

/* Step 5: what has been read, and what of it should count.
 *
 * Nothing on this screen is in your ledger. Everything here was parsed into a
 * staging area that no tab, table or total reads, and it stays there until the
 * next step. That is why the step exists: a badly-read statement is something
 * you look at and reject, not something you discover inside a figure that is
 * already wrong.
 *
 * Groups are what you judge - "should this card's statements count at all" -
 * and the files inside them are what you correct: "all of them except that
 * one". Both are checkboxes because both questions get asked.
 */

const TYPE_LABELS = {
  credit_card: 'Card',
  savings: 'Bank',
  current: 'Bank',
  loan: 'Loan',
  investment: 'Investment',
  credit_report: 'Credit report',
  unknown: 'Not yet read',
};

/* What the reconciliation gate concluded, in words that cannot be mistaken
   for "this file could not be read".
 *
 * A bare "failed" beside a filename reads as a broken document. It is not: an
 * uploaded Amex statement showed "· failed" while having parsed perfectly, and
 * what had actually happened is that its rows account for 45,509.92 of a
 * declared 56,858.15 - the statement is short 11,348.23 of real charges. That
 * is the gate doing its job, and the most valuable thing on the screen, but
 * only if it says so. */
const RECON_WORDS = {
  passed: 'balances',
  ok: 'balances',
  failed: 'does not balance',
  unreconciled: 'nothing to check it against',
  not_applicable: 'no balances printed',
};

function fileNote(file) {
  if (file.superseded_by) {
    return `Superseded by ${file.superseded_by_name || 'a statement'} — the `
      + 'statement covering these dates is the reconciled record of the same money.';
  }
  if (file.parse_status === 'needs_password') {
    return 'Locked. No password derived from your profile opened it.';
  }
  if (file.parse_status === 'failed') return file.parse_message;
  if (file.parse_status === 'empty') return 'Read, but no transactions in it.';
  if (file.parse_status === 'pending') return 'Not read yet.';
  // Read fine, but the figures do not add up to what the issuer printed. The
  // message says by how much, which is the whole point of showing it.
  if (file.recon_status === 'failed') return file.parse_message;
  return null;
}

export default function ImportReview({ onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(() => new Set());
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const next = await api.stagingReview();
      setData(next);
      onChanged?.(next);
    } catch (e) { setError(e.message); }
  }, [onChanged]);

  useEffect(() => { load(); }, [load]);

  const groups = data?.groups || [];

  /* Read back from the server after every change rather than recomputed here,
     because supersession is the server's answer to give: unticking a statement
     can bring the alerts it replaced back to life, and no arithmetic on this
     side would know that had happened. */
  const totals = useMemo(() => {
    const months = new Set();
    let rows = 0;
    for (const g of groups) {
      if (!g.included) continue;
      // Only the kinds that carry transactions. A credit report's 27 accounts
      // and a portfolio's 34 holdings are not rows in a ledger, and adding
      // them here made this chip disagree with the next screen by exactly the
      // size of the credit report.
      if (g.kind === 'statement' || g.kind === 'alert') rows += g.row_count || 0;
      if (g.first) months.add(g.first.slice(0, 7));
      if (g.last) months.add(g.last.slice(0, 7));
    }
    const sorted = [...months].sort();
    return { rows, from: sorted[0], to: sorted[sorted.length - 1] };
  }, [groups]);

  const send = async (body) => {
    setBusy(true);
    try {
      await api.stagingSelect(body);
      await load();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const toggleGroup = (group) => send({
    groups: [{ key: group.key, include: !group.included }],
  });

  const toggleFile = (file) => send({
    files: [{ ids: [file.id], include: !file.selected }],
  });

  const setAll = (include) => send({
    groups: groups.map((g) => ({ key: g.key, include })),
  });

  const toggleOpen = (key) => setOpen((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  if (error) return <Callout tone="neg">{error}</Callout>;
  if (!data) return <div className="xp-hint">Reading what came in…</div>;
  if (!groups.length) {
    return (
      <Empty title="Nothing staged yet">
        Scan your mailbox or add a file on the Source step. What gets read
        lands here, and none of it counts until you process it.
      </Empty>
    );
  }

  const off = groups.filter((g) => !g.included).length;

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <p style={{ color: 'var(--text-2)', fontSize: 13, margin: 0 }}>
        Everything that has been read, grouped by account and by where it came
        from. <strong>None of this is in your ledger yet.</strong> Tick what
        should count, then process it on the next step.
      </p>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <Chip tone="accent">{totals.rows.toLocaleString('en-IN')} rows will count</Chip>
        {totals.from && <Chip>{totals.from} → {totals.to}</Chip>}
        {off > 0 && <Chip tone="warn">{off} group{off === 1 ? '' : 's'} off</Chip>}
        {data.superseded > 0 && (
          <Chip tone="warn">{data.superseded} superseded</Chip>
        )}
        <div style={{ flex: 1 }} />
        <button className="btn" disabled={busy}
          style={{ padding: '3px 10px', fontSize: 12 }}
          onClick={() => setAll(true)}>Include all</button>
        <button className="btn" disabled={busy}
          style={{ padding: '3px 10px', fontSize: 12 }}
          onClick={() => setAll(false)}>Exclude all</button>
      </div>

      {groups.map((group) => {
        const expanded = open.has(group.key);
        return (
          <div key={group.key} className="file-group">
            <div style={{
              display: 'grid', gridTemplateColumns: 'auto auto 1fr auto',
              gap: 10, alignItems: 'center', padding: '2px 0 8px',
            }}>
              <input type="checkbox" checked={group.included} disabled={busy}
                ref={(el) => { if (el) el.indeterminate = Boolean(group.partial); }}
                onChange={() => toggleGroup(group)} />
              <button className="btn" onClick={() => toggleOpen(group.key)}
                style={{ padding: '1px 7px', fontSize: 11, lineHeight: 1.6 }}
                aria-label={expanded ? 'Collapse' : 'Expand'}>
                {expanded ? '▾' : '▸'}
              </button>
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <strong style={{ fontSize: 13 }}>{group.account_label}</strong>
                  <span className="xp-hint" style={{ textTransform: 'none' }}>
                    {TYPE_LABELS[group.account_type] || group.account_type}
                  </span>
                  <Chip>{group.kind_label}</Chip>
                  <Chip>{group.selected_count} of {group.file_count} file
                    {group.file_count === 1 ? '' : 's'}</Chip>
                  {group.row_count > 0 && <Chip>{group.row_count} rows</Chip>}
                  {group.failed_count > 0 && (
                    <Chip tone="neg">{group.failed_count} unreadable</Chip>
                  )}
                  {group.unbalanced_count > 0 && (
                    <Chip tone="warn">{group.unbalanced_count} do not balance</Chip>
                  )}
                  {group.superseded_count > 0 && (
                    <Chip tone="warn">{group.superseded_count} superseded</Chip>
                  )}
                </div>
                {(group.first || group.kind_note) && (
                  <div className="xp-hint" style={{ textTransform: 'none', marginTop: 3 }}>
                    {group.first ? `${group.first} → ${group.last} · ` : ''}
                    {group.kind_note}
                  </div>
                )}
              </div>
              <div style={{ textAlign: 'right', fontSize: 12, whiteSpace: 'nowrap' }}>
                {Number(group.debits) > 0 && <div>−{money(group.debits)}</div>}
                {Number(group.credits) > 0 && (
                  <div style={{ color: 'var(--pos, #2e7d32)' }}>+{money(group.credits)}</div>
                )}
              </div>
            </div>

            {expanded && group.files.map((file) => {
              const note = fileNote(file);
              const dead = Boolean(file.superseded_by);
              return (
                <label key={file.id} className="file-row" style={{
                  display: 'grid', gridTemplateColumns: 'auto 1fr auto',
                  gap: 10, alignItems: 'start', padding: '6px 10px',
                  marginLeft: 26,
                  opacity: file.selected && !dead ? 1 : 0.5,
                  cursor: dead ? 'not-allowed' : 'pointer',
                }}>
                  <input type="checkbox" checked={file.selected && !dead}
                    disabled={busy || dead}
                    onChange={() => toggleFile(file)} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{
                      fontSize: 12.5,
                      textDecoration: dead ? 'line-through' : 'none',
                      wordBreak: 'break-all',
                    }}>
                      {file.filename}
                    </div>
                    <div className="xp-hint" style={{ textTransform: 'none', marginTop: 2 }}>
                      {file.period_start ? `${file.period_start} → ${file.period_end}` : '—'}
                      {file.row_count ? ` · ${file.row_count} rows` : ''}
                      {file.origin === 'upload' ? ' · uploaded' : ''}
                      {file.recon_status
                        ? ` · ${RECON_WORDS[file.recon_status] || file.recon_status}`
                        : ''}
                    </div>
                    {note && (
                      <div style={{
                        fontSize: 11.5, marginTop: 2,
                        color: file.parse_status === 'failed'
                          ? 'var(--neg, #c62828)'
                          : file.recon_status === 'failed'
                            ? 'var(--warn, #b8860b)' : 'var(--text-3)',
                      }}>
                        {note}
                      </div>
                    )}
                  </div>
                  <div style={{ textAlign: 'right', fontSize: 11.5, whiteSpace: 'nowrap' }}>
                    {Number(file.debits) > 0 && <div>−{money(file.debits)}</div>}
                    {Number(file.credits) > 0 && (
                      <div style={{ color: 'var(--pos, #2e7d32)' }}>+{money(file.credits)}</div>
                    )}
                  </div>
                </label>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
