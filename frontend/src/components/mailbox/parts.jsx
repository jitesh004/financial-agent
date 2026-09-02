import React, { useState } from 'react';
import { dateLabel, formatBytes } from '../../lib';
import { Callout, Card, Chip, Empty } from '../ui';
import { rowKey } from './useMailbox';

/* Presentation for the mailbox import, lifted wholesale out of the old
   787-line GmailWizard. Nothing here holds import state: every one of these
   takes what it draws as props, which is what let the state move to the server
   without any of this having to change. */

/* Sender category -> chip tone. The categories are the backend's
   (`rules.institutions.CLASSIFY_ORDER`), so all of them must be here: a
   missing key renders an undefined tone, which is how bureau rows lost
   their colour. */
export const CATEGORY_TONE = {
  bank: 'accent', card: 'pos', loan: 'warn', bureau: 'neg', broker: '',
  unknown: '',
};

export function StageStrip({ active }) {
  const stages = [['downloading', 'Download'], ['processing', 'Parse']];
  return (
    <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
      {stages.map(([key, label]) => (
        <Chip key={key} tone={active === key ? 'accent' : ''}>{label}</Chip>
      ))}
    </div>
  );
}

/* The steps of an import, as somewhere you can move around rather than a
   position you are held at.
 *
 * The import itself is server-side and its stage is derived from a job, so
 * this cannot drive the work - it decides which step you are LOOKING at. The
 * two are separate on purpose: going back to check what was selected while a
 * download runs should not touch the download, and reaching the end should not
 * lock away the screens that got you there.
 *
 * A step you have not reached yet is not clickable. A step behind you always
 * is - that is the whole point of it. */
export function StepRail({ steps, current, reached, onGo }) {
  return (
    <div className="step-rail" style={{
      display: 'flex', alignItems: 'center', gap: 4,
      flexWrap: 'wrap', margin: '0 0 4px',
    }}>
      {steps.map((step, index) => {
        const done = index < current;
        const here = index === current;
        /* Every step is reachable, always.
         *
         * Gating them on progress made sense when the wizard was one linear
         * import. Now each step is a set of sections that explains its own
         * empty state - "no sources ticked", "nothing staged yet" - which
         * tells you far more than a greyed-out button does. It also stopped
         * being survivable: with the view no longer following the work,
         * nothing advanced `reached` on a fresh session, and the wizard could
         * not be left at step one. */
        const open = true;
        return (
          <React.Fragment key={step.key}>
            {index > 0 && (
              <span aria-hidden style={{
                width: 14, height: 1, background: 'var(--line)', opacity: 0.7,
              }} />
            )}
            <button
              type="button"
              className="btn"
              disabled={!open}
              onClick={() => open && onGo(index)}
              title={open ? step.label : 'Not reached yet'}
              style={{
                padding: '3px 10px', fontSize: 12,
                display: 'inline-flex', alignItems: 'center', gap: 6,
                background: here ? 'var(--accent-soft, var(--bg-2))' : undefined,
                borderColor: here ? 'var(--accent)' : undefined,
                fontWeight: here ? 620 : 400,
                opacity: open ? 1 : 0.45,
                cursor: open ? 'pointer' : 'default',
              }}
            >
              <span style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                width: 16, height: 16, borderRadius: 9, fontSize: 10, lineHeight: 1,
                background: here ? 'var(--accent)' : 'var(--bg-3, var(--bg-2))',
                color: here ? 'var(--on-accent, #fff)' : 'var(--text-2)',
              }}>
                {done ? '✓' : index + 1}
              </span>
              {step.label}
            </button>
          </React.Fragment>
        );
      })}
    </div>
  );
}

export function FilterBar({
  ignoredSenders, ignoredCount, onIgnoreSender, onUnignoreAll,
  categories, excludedCategories, onToggleCategory,
  senders, excludedSenders, onToggleSender,
  search, onSearch, onlyMissingPassword, onToggleMissing,
}) {
  const [showSenders, setShowSenders] = useState(false);
  const excludedCount = excludedSenders.size;

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <input type="search" placeholder="Search filename, sender or subject…"
          value={search} onChange={(e) => onSearch(e.target.value)}
          style={{ flex: 1, minWidth: 200 }} />
        <button className="btn" onClick={() => setShowSenders((v) => !v)}>
          Senders {excludedCount > 0 && `(${excludedCount} hidden)`}
        </button>
        <label className="chip" style={{ cursor: 'pointer' }}>
          <input type="checkbox" checked={onlyMissingPassword} onChange={onToggleMissing}
            style={{ marginRight: 4 }} />
          Only missing password
        </label>
      </div>

      {ignoredSenders?.length > 0 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: 'var(--text-3)' }}>
            Permanently ignored{ignoredCount ? ` (${ignoredCount} skipped this scan)` : ''}:
          </span>
          {ignoredSenders.map((f) => <Chip key={f} tone="warn">{f}</Chip>)}
          <button className="btn" style={{ padding: '2px 9px', fontSize: 11.5 }}
            onClick={onUnignoreAll}>
            Clear
          </button>
        </div>
      )}

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontSize: 12, color: 'var(--text-3)' }}>Type:</span>
        {categories.map(([cat, n]) => {
          const off = excludedCategories.has(cat);
          return (
            <button key={cat} onClick={() => onToggleCategory(cat)}
              className={`chip ${off ? '' : CATEGORY_TONE[cat] || 'accent'}`}
              style={{ cursor: 'pointer', opacity: off ? 0.45 : 1, border: 0 }}
              title={off ? `Click to include ${cat}` : `Click to exclude ${cat}`}>
              {off ? '✕ ' : '✓ '}{cat} ({n})
            </button>
          );
        })}
      </div>

      {showSenders && (
        <div style={{
          border: '1px solid var(--border)', borderRadius: 8, padding: 10,
          background: 'var(--surface-2)', maxHeight: 190, overflowY: 'auto',
        }}>
          <div style={{ fontSize: 12, color: 'var(--text-3)', marginBottom: 8 }}>
            Untick to hide for this scan. <strong>Ignore</strong> removes the
            account permanently — use it for a family member's or a business
            account that shouldn't appear in your dashboard.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(230px,1fr))', gap: 4 }}>
            {senders.map((s) => (
              <label key={s.key} style={{
                display: 'flex', gap: 7, alignItems: 'center',
                fontSize: 12.5, cursor: 'pointer', padding: '2px 0',
              }}>
                <input type="checkbox" checked={!excludedSenders.has(s.key)}
                  onChange={() => onToggleSender(s.key)} />
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {s.name}
                </span>
                <Chip>{s.count}</Chip>
                <button
                  className="btn"
                  style={{ padding: '1px 7px', fontSize: 11 }}
                  title={`Never import from ${s.key} again`}
                  onClick={(e) => { e.preventDefault(); onIgnoreSender(s.key); }}
                >
                  Ignore
                </button>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function ExcludedPanel({ excluded, open, onToggle }) {
  /* What the filter rejected, and why.

     Shown rather than hidden: a filter you cannot inspect is one you have to
     second-guess, and the cost of a wrong exclusion (a silently missing month
     of history) is much higher than the cost of a wrong inclusion. */
  if (!excluded.length) return null;

  const byReason = excluded.reduce((acc, e) => {
    acc[e.reason] = (acc[e.reason] || 0) + 1;
    return acc;
  }, {});

  return (
    <div style={{
      border: '1px solid var(--border)', borderRadius: 8,
      background: 'var(--surface-2)', padding: '9px 12px', marginBottom: 10,
    }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12.5, color: 'var(--text-2)' }}>
          <strong>{excluded.length}</strong> email
          {excluded.length === 1 ? '' : 's'} excluded
        </span>
        {Object.entries(byReason)
          .sort((a, b) => b[1] - a[1])
          .map(([reason, n]) => (
            <Chip key={reason} tone={reason === 'marketing' ? '' : 'warn'}>
              {n} {reason}
            </Chip>
          ))}
        <button className="btn" style={{ marginLeft: 'auto', padding: '3px 10px', fontSize: 12 }}
          onClick={onToggle}>
          {open ? 'Hide' : 'Review'}
        </button>
      </div>

      {open && (
        <div className="scroll-y" style={{
          maxHeight: 220, marginTop: 10,
          border: '1px solid var(--border)', borderRadius: 6,
          background: 'var(--surface)',
        }}>
          <div className="table-wrap" style={{ margin: 0, padding: 0 }}>
            <table style={{ tableLayout: 'fixed', width: '100%' }}>
              <thead>
                <tr>
                  <th style={{ width: 84 }}>Date</th>
                  <th style={{ width: '28%' }}>Sender</th>
                  <th>Subject</th>
                  <th style={{ width: 130 }}>Excluded as</th>
                </tr>
              </thead>
              <tbody>
                {excluded.map((e, i) => (
                  <tr key={i}>
                    <td className="nowrap num" style={{ fontSize: 12 }}>
                      {e.date_iso ? dateLabel(e.date_iso) : '—'}
                    </td>
                    <td>
                      <div className="truncate" title={e.sender}>{e.sender_name}</div>
                    </td>
                    <td>
                      <div className="truncate" title={e.subject}>{e.subject}</div>
                    </td>
                    <td><Chip tone={e.reason === 'marketing' ? '' : 'warn'}>{e.reason}</Chip></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export function SelectionSummary({ visible, total, selected, bytes, cached, missingPassword }) {
  return (
    <div style={{
      display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center',
      padding: '9px 12px', borderRadius: 8, background: 'var(--surface-2)',
      border: '1px solid var(--border)', fontSize: 12.5, marginBottom: 10,
    }}>
      <span><strong className="num">{selected}</strong> selected</span>
      <span style={{ color: 'var(--text-3)' }}>
        {visible} shown of {total} found
      </span>
      <span className="num" style={{ color: 'var(--text-3)' }}>{formatBytes(bytes)}</span>
      {cached > 0 && <Chip tone="pos">{cached} already cached</Chip>}
      {missingPassword > 0 && (
        <Chip tone="warn">{missingPassword} need profile details</Chip>
      )}
    </div>
  );
}

export function AttachmentTable({ rows, selected, onToggle, onToggleAll, sort, onSort }) {
  if (!rows.length) {
    return <Empty title="Nothing matches these filters">Adjust the type or sender filters above.</Empty>;
  }
  const allChecked = rows.every((r) => selected.has(rowKey(r)));
  const arrow = (key) => (sort.key === key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : '');

  return (
    <div className="table-wrap scroll-y" style={{ maxHeight: 460 }}>
      <table>
        <thead>
          <tr>
            <th style={{ width: 30 }}>
              <input type="checkbox" checked={allChecked} onChange={onToggleAll}
                title="Select all shown" />
            </th>
            <th onClick={() => onSort('filename')} style={{ cursor: 'pointer' }}>
              File{arrow('filename')}
            </th>
            <th onClick={() => onSort('sender_name')} style={{ cursor: 'pointer' }}>
              Sender{arrow('sender_name')}
            </th>
            <th>Subject</th>
            <th onClick={() => onSort('category')} style={{ cursor: 'pointer' }}>
              Type{arrow('category')}
            </th>
            <th>Password</th>
            <th className="right" onClick={() => onSort('size')} style={{ cursor: 'pointer' }}>
              Size{arrow('size')}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const checked = selected.has(rowKey(r));
            return (
              <tr key={rowKey(r)} style={{ opacity: checked ? 1 : 0.5 }}>
                <td>
                  <input type="checkbox" checked={checked} onChange={() => onToggle(r)} />
                </td>
                <td>
                  <div className="truncate" style={{ maxWidth: 210 }} title={r.filename}>
                    {r.filename}
                  </div>
                  {r.cached && <Chip tone="pos">cached</Chip>}
                </td>
                <td>
                  <div className="truncate" style={{ maxWidth: 140 }} title={r.sender}>
                    {r.sender_name}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-3)' }}>{r.sender_domain}</div>
                </td>
                <td>
                  <div className="truncate" style={{ maxWidth: 240 }} title={r.subject}>
                    {r.subject}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
                    {r.date_iso ? dateLabel(r.date_iso) : dateLabel(r.date)}
                  </div>
                </td>
                <td><Chip tone={CATEGORY_TONE[r.category]}>{r.category}</Chip></td>
                <td>
                  <div title={r.password_explanation} style={{ cursor: 'help' }}>
                    <Chip tone={r.password_ready ? 'pos' : 'warn'}>
                      {r.password_rule}
                    </Chip>
                  </div>
                  {!r.password_ready && (
                    <div style={{ fontSize: 10.5, color: 'var(--warn)' }}>
                      missing profile detail
                    </div>
                  )}
                </td>
                <td className="right num nowrap">{formatBytes(r.size)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function ResultTable({ statements }) {
  const tone = {
    ok: 'pos', unreconciled: 'warn', failed: 'neg',
    needs_password: 'warn', duplicate: '',
  };
  return (
    <div className="table-wrap scroll-y" style={{ maxHeight: 320, marginTop: 12 }}>
      <table>
        <thead>
          <tr>
            <th>File</th><th>Account</th>
            <th className="right">Rows</th><th>Status</th><th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {statements.map((s, i) => (
            <tr key={i}>
              <td><div className="truncate" style={{ maxWidth: 200 }}>{s.filename}</div></td>
              <td><div className="truncate" style={{ maxWidth: 160 }}>{s.account || '—'}</div></td>
              <td className="right num">{s.transaction_count ?? 0}</td>
              <td><Chip tone={tone[s.status]}>{s.status}</Chip></td>
              <td style={{ fontSize: 11.5, color: 'var(--text-3)' }}>
                <div className="truncate" style={{ maxWidth: 280 }} title={s.message}>
                  {s.message}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SetupInstructions() {
  return (
    <Card title="Import from Gmail" sub="Setup needed">
      <p style={{ color: 'var(--text-2)', fontSize: 13, marginTop: 0 }}>
        Finds your bank and card statement emails, downloads the PDFs and analyzes
        them. Access is <strong>read-only</strong>, and consent happens on Google’s
        own page — this app never sees your Gmail password.
      </p>
      <Callout>
        <strong>What you need:</strong> the same Google OAuth client the app
        signs in with, configured on the server. It isn’t anyone’s password —
        it’s a free ID card from Google that registers this app so Google will
        accept the sign-in.
      </Callout>
      <ol style={{ color: 'var(--text-2)', fontSize: 13, lineHeight: 1.75, paddingLeft: 20 }}>
        <li>Open <a href="https://console.cloud.google.com" target="_blank" rel="noreferrer"
          style={{ color: 'var(--accent)' }}>console.cloud.google.com</a> and create a project.</li>
        <li>Search <strong>Gmail API</strong> and click <strong>Enable</strong>.</li>
        <li>Open the <strong>OAuth consent screen</strong>, choose <strong>External</strong>,
          and add your own Gmail under <strong>Test users</strong>.</li>
        <li><strong>Credentials → Create Credentials → OAuth client ID</strong>,
          type <strong>Web application</strong>, and add{' '}
          <code>{`${window.location.origin}/api/auth/google/callback`}</code>{' '}
          under <strong>Authorised redirect URIs</strong>.</li>
        <li>Set <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code>{' '}
          in the server’s <code>.env</code>, and restart the API.</li>
      </ol>
      <Callout>
        Check it with{' '}
        <code>.venv/Scripts/python backend/tools/check_gmail_setup.py</code>
      </Callout>
    </Card>
  );
}
