import React, { useMemo, useState } from 'react';
import { formatBytes, money } from '../../lib';
import { Callout, Chip, Empty } from '../ui';
import { rowKey } from './useMailbox';

/* Step 3: what each scan found, one collapsible section per source.
 *
 * Sectioned rather than pooled because the decision differs by source. A year
 * of bank statements is a straightforward yes; a year of alerts is mostly
 * noise the statements will supersede anyway; broker files are quarterly and
 * you may want only the latest. Judging four hundred rows in one merged list
 * means judging none of them.
 *
 * Collapsed by default, because the useful glance is the header - how many
 * were found, how many are ticked, how many were refused - and opening a
 * section is how you say "show me that one".
 */

const REFUSED_LIMIT = 40;

function Files({ rows, selected, onToggle }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? rows : rows.slice(0, 10);
  return (
    <div style={{ marginTop: 8 }}>
      {shown.map((row) => {
        const key = rowKey(row);
        const on = selected.has(key);
        return (
          <label key={key} className="file-row" style={{
            display: 'grid', gridTemplateColumns: 'auto 1fr auto',
            gap: 10, alignItems: 'center', padding: '5px 8px',
            opacity: on ? 1 : 0.55, cursor: 'pointer',
          }}>
            <input type="checkbox" checked={on} onChange={() => onToggle(row)} />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 12.5, wordBreak: 'break-all' }}>
                {row.filename}
              </div>
              <div className="xp-hint" style={{ textTransform: 'none', marginTop: 1 }}>
                {row.institution || row.sender_name}
                {row.date_iso ? ` · ${row.date_iso.slice(0, 10)}` : ''}
                {row.cached ? ' · already downloaded' : ''}
                {row.password_ready === false ? ' · no password for this one' : ''}
              </div>
            </div>
            <span className="xp-hint" style={{ whiteSpace: 'nowrap' }}>
              {row.size ? formatBytes(row.size) : ''}
            </span>
          </label>
        );
      })}
      {rows.length > 10 && (
        <button className="btn" style={{ padding: '2px 9px', fontSize: 11, marginTop: 6 }}
          onClick={() => setExpanded((v) => !v)}>
          {expanded ? 'Show fewer' : `Show all ${rows.length}`}
        </button>
      )}
    </div>
  );
}

/* Every alert the scan read and did not use, as the transactions they would
   have been.
 *
 * A count of refusals answers "how many"; only the rows answer "which". An
 * alert refused for naming a card you hold no statements for is a real
 * payment that is missing from your ledger, and you cannot tell whether that
 * matters without seeing the merchant and the amount. */
function RefusedAlerts({ alerts }) {
  const [expanded, setExpanded] = useState(false);
  if (!alerts.length) return null;
  const shown = expanded ? alerts.slice(0, REFUSED_LIMIT) : alerts.slice(0, 8);

  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 4 }}>
        Not used ({alerts.length})
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="table" style={{ width: '100%', fontSize: 12 }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left' }}>Date</th>
              <th style={{ textAlign: 'left' }}>Merchant</th>
              <th style={{ textAlign: 'left' }}>Account</th>
              <th style={{ textAlign: 'right' }}>Amount</th>
              <th style={{ textAlign: 'left' }}>Why not</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((a, i) => (
              <tr key={`${a.message_id}-${i}`}>
                <td style={{ whiteSpace: 'nowrap' }}>
                  {a.date_iso ? a.date_iso.slice(0, 10) : '—'}
                </td>
                <td style={{ maxWidth: 200, wordBreak: 'break-word' }}>
                  {a.merchant || a.subject || '—'}
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  {a.institution || a.sender_name || '—'}
                  {a.account_suffix ? ` …${a.account_suffix}` : ''}
                </td>
                <td className="num" style={{
                  textAlign: 'right', whiteSpace: 'nowrap',
                  color: a.direction === 'credit' ? 'var(--pos, #2e7d32)' : undefined,
                }}>
                  {a.amount ? `${a.direction === 'credit' ? '+' : '−'}${money(a.amount)}` : '—'}
                </td>
                <td style={{ color: 'var(--text-3)' }}>{a.reason || a.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {alerts.length > 8 && (
        <button className="btn" style={{ padding: '2px 9px', fontSize: 11, marginTop: 6 }}
          onClick={() => setExpanded((v) => !v)}>
          {expanded ? 'Show fewer'
            : `Show ${Math.min(alerts.length, REFUSED_LIMIT)} of ${alerts.length}`}
        </button>
      )}
      {expanded && alerts.length > REFUSED_LIMIT && (
        <div className="xp-hint" style={{ textTransform: 'none', marginTop: 4 }}>
          Showing the first {REFUSED_LIMIT}. The rest are refused for the same
          reasons.
        </div>
      )}
    </div>
  );
}

/* Files a scan looked at and did not offer, summarised by reason. */
function RefusedFiles({ excluded, ignored }) {
  if (!excluded.length && !ignored) return null;
  const reasons = new Map();
  for (const e of excluded) {
    const why = e.reason || 'no reason given';
    reasons.set(why, (reasons.get(why) || 0) + 1);
  }
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 4 }}>
        Not used ({excluded.length}
        {ignored ? `, plus ${ignored} from senders you ignore` : ''})
      </div>
      {[...reasons.entries()].sort((a, b) => b[1] - a[1]).map(([why, n]) => (
        <div key={why} className="xp-hint"
          style={{ textTransform: 'none', padding: '2px 0' }}>
          <strong>{n}</strong> — {why}
        </div>
      ))}
    </div>
  );
}

function Section({ source, result, selected, onToggle, onToggleMany, staged }) {
  const [open, setOpen] = useState(false);

  const rows = useMemo(() => {
    if (!result) return [];
    if (result.attachments) return result.attachments;
    // Alerts have no file to fetch, so what is offered here are the ones that
    // were understood: an amount, a date, and the four digits saying which
    // card they belong to.
    return (result.alerts || [])
      .filter((a) => a.amount && a.date_iso && a.account_suffix)
      .map((a) => ({
        message_id: a.message_id, filename: a.merchant || a.subject || 'alert',
        size: 0, institution: a.institution || a.sender_name,
        date_iso: a.date_iso, cached: true, alert: true,
      }));
  }, [result]);

  /* Refusals worth reading first.
   *
   * An alert refused for naming a card you hold no statements for is a real
   * payment missing from your ledger, and you cannot judge that without the
   * merchant and the amount. An alert refused as "not a completed
   * transaction" is a marketing email. Unsorted, the marketing came first and
   * the money was somewhere below the fold. */
  const refusedAlerts = useMemo(() => {
    const rows = (result?.alerts || []).filter((a) => a.status !== 'imported');
    const weight = (a) => {
      if (a.amount && a.date_iso && a.account_suffix) return 0;  // a real payment
      if (a.amount && a.date_iso) return 1;                      // money, no account
      if (a.amount) return 2;
      return 3;                                                  // not a transaction
    };
    return [...rows].sort((a, b) => weight(a) - weight(b)
      || String(b.date_iso || '').localeCompare(String(a.date_iso || '')));
  }, [result]);
  const excluded = result?.excluded || [];
  const ignored = result?.ignored_by_rule || 0;
  const refusedCount = refusedAlerts.length + excluded.length;

  const mine = rows.filter((r) => selected.has(rowKey(r))).length;
  const all = rows.length > 0 && mine === rows.length;

  return (
    <div className="file-group" style={{ padding: '10px 12px' }}>
      <div style={{
        display: 'grid', gridTemplateColumns: 'auto 1fr auto',
        gap: 10, alignItems: 'center',
      }}>
        <button className="btn" onClick={() => setOpen((v) => !v)}
          style={{ padding: '1px 7px', fontSize: 11, lineHeight: 1.6 }}
          aria-label={open ? 'Collapse' : 'Expand'}>
          {open ? '▾' : '▸'}
        </button>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <strong style={{ fontSize: 13 }}>{source.label}</strong>
          {rows.length > 0 && <Chip tone={mine ? 'accent' : ''}>{mine} of {rows.length} chosen</Chip>}
          {staged > 0 && <Chip tone="pos">{staged} staged</Chip>}
          {refusedCount > 0 && <Chip tone="warn">{refusedCount} not used</Chip>}
          {!result && <Chip>not scanned</Chip>}
        </div>
        {rows.length > 0 && (
          <button className="btn" style={{ padding: '3px 10px', fontSize: 12 }}
            onClick={() => onToggleMany(rows, !all)}>
            {all ? 'Clear' : 'Select all'}
          </button>
        )}
      </div>

      {open && (
        <>
          {!result && (
            <div className="xp-hint" style={{ textTransform: 'none', marginTop: 6 }}>
              Not scanned in this session — anything staged earlier is still on
              Review.
            </div>
          )}
          {result && !rows.length && (
            <div className="xp-hint" style={{ textTransform: 'none', marginTop: 6 }}>
              That scan found nothing to choose from.
            </div>
          )}
          {rows.length > 0 && (
            <Files rows={rows} selected={selected} onToggle={onToggle} />
          )}
          <RefusedAlerts alerts={refusedAlerts} />
          <RefusedFiles excluded={excluded} ignored={ignored} />
        </>
      )}
    </div>
  );
}

export default function ChooseSections({
  intents, chosen, sections, sourceResults, selected, onToggle, onToggleMany,
}) {
  const staged = Object.fromEntries((sections || []).map((s) => [s.key, s]));
  const picked = intents.filter((one) => chosen.has(one.key));

  if (!picked.length) {
    return (
      <Empty title="Nothing to choose from">
        No sources are ticked. Go back to Source and pick at least one.
      </Empty>
    );
  }

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <p style={{ color: 'var(--text-2)', fontSize: 13, margin: 0 }}>
        What each scan found. Open a section to see the files and what was
        refused. Tick what to download and read — nothing is read, and nothing
        counts, until you say so.
      </p>

      {picked.map((one) => (
        <Section key={one.key} source={one} result={sourceResults?.[one.key]}
          staged={staged[one.key]?.staged || 0}
          selected={selected} onToggle={onToggle} onToggleMany={onToggleMany} />
      ))}

      <div className="file-group" style={{ padding: '10px 12px' }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <strong style={{ fontSize: 13 }}>Files from this computer</strong>
          {staged.upload?.staged > 0
            ? <Chip tone="pos">{staged.upload.staged} staged</Chip>
            : <Chip>none added</Chip>}
        </div>
        <div className="xp-hint" style={{ textTransform: 'none', marginTop: 6 }}>
          Uploaded files skip this step — they are already here, so there is
          nothing to download. They are read on <strong>Parse</strong> and
          judged on <strong>Review</strong> with everything else.
        </div>
      </div>

      <Callout>
        Alerts carry their amount in the email body, so there is no file to
        fetch: the ones that were understood go straight to staging when their
        scan runs, and appear on Review.
      </Callout>
    </div>
  );
}
