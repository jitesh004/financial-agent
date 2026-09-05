import React, { useEffect, useState } from 'react';
import { api, compact, dateLabel, money, titleCase } from '../lib';
import { Callout, Card, Chip, Empty, Stat } from './ui';

/* The credit bureau's account of what you owe, laid against this app's.
 *
 * The valuable output is not the score. It is `bureau_only`: accounts a lender
 * is reporting that no statement here has ever covered. Every total in this
 * app is blind to those, and nothing else in it can discover them. */

const BAND_TONE = {
  excellent: 'pos', 'very good': 'pos', good: 'pos',
  fair: 'warn', poor: 'neg', 'very poor': 'neg',
};

export default function CreditReport({ accounts = [], onImport }) {
  const [overview, setOverview] = useState(null);
  const [recon, setRecon] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = () => Promise.all([
    api.request('/api/bureau'),
    api.request('/api/bureau/reconciliation'),
  ]).then(([o, r]) => { setOverview(o); setRecon(r); setError(null); })
    .catch((e) => setError(e.message));

  useEffect(() => { load(); }, []);

  const decide = async (bureauAccountId, accountId, confirmed) => {
    setBusy(true);
    try {
      await api.request(`/api/bureau/accounts/${bureauAccountId}/match`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_id: accountId, confirmed }),
      });
      await load();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  if (error) return <Callout tone="warn">{error}</Callout>;
  if (!overview) {
    return (
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', padding: 40 }}>
        <div className="spinner" /> Loading…
      </div>
    );
  }

  if (!overview.reports.length) {
    return (
      <Empty title="No credit report imported yet"
        action={onImport && (
          <button className="btn primary" onClick={onImport}>
            Import statements
          </button>
        )}>
        Scan your mailbox for bureau reports, or add a CIBIL, CRIF, Experian
        or Equifax PDF. A bureau lists every account a lender reports on you,
        which is the only way this app can find one it has no statements for.
      </Empty>
    );
  }

  const totals = overview.totals || {};
  const counts = recon?.counts || {};
  const blindSpots = (recon?.bureau_only || []).filter((a) => a.is_blind_spot);
  const suggestions = (recon?.bureau_only || []).filter((a) => a.suggestion);

  return (
    <>
      <div className="grid cols-4">
        {overview.latest_by_bureau.map((report) => (
          <Card key={report.id} title={titleCase(report.bureau)}
            sub={report.pulled_on ? dateLabel(report.pulled_on) : ''}>
            <div className="stat">
              <div className="stat-value num" style={{ fontSize: 30 }}>
                {report.score ?? '—'}
              </div>
              {report.score_band && (
                <Chip tone={BAND_TONE[report.score_band] || ''}>
                  {report.score_band}
                </Chip>
              )}
            </div>
          </Card>
        ))}
        <Stat label="Reported outstanding"
          value={Number(totals.outstanding) || 0}
          note={`${totals.open_accounts || 0} open accounts`} />
        {Number(totals.overdue) > 0 && (
          <Stat label="Overdue now" value={Number(totals.overdue)} tone="neg"
            note={`worst ${totals.worst_dpd} days past due`} />
        )}
      </div>

      {blindSpots.length > 0 && (
        <>
          <div className="section-title">
            Accounts you have no statements for
          </div>
          <Callout tone="warn">
            A lender is reporting {blindSpots.length} open account
            {blindSpots.length === 1 ? '' : 's'} that nothing here covers. Money
            owed on {blindSpots.length === 1 ? 'it is' : 'them is'} missing from
            every total in this app.
          </Callout>
          <Card>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Lender</th><th>Type</th><th>Number</th>
                    <th className="right">Outstanding</th>
                    <th className="right">Overdue</th>
                    <th>Opened</th>
                  </tr>
                </thead>
                <tbody>
                  {blindSpots.map((row) => (
                    <tr key={row.bureau_account_id}>
                      <td>{row.lender}</td>
                      <td><Chip>{titleCase(row.account_type)}</Chip></td>
                      <td className="num">{row.masked || '—'}</td>
                      <td className="right num">
                        {row.balance ? money(Number(row.balance)) : '—'}
                      </td>
                      <td className="right num">
                        {Number(row.overdue) > 0
                          ? <span className="neg">{money(Number(row.overdue))}</span>
                          : '—'}
                      </td>
                      <td className="nowrap">{row.opened_on || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}

      {suggestions.length > 0 && (
        <>
          <div className="section-title">Possible matches, for you to confirm</div>
          <Callout>
            These look like accounts already here, but not certainly enough to
            link on their own. Two cards from the same bank match each other on
            everything except the digits, and guessing wrong would put one
            card&apos;s debt on the other.
          </Callout>
          <Card>
            {suggestions.map((row) => {
              const account = accounts.find((a) => a.id === row.suggestion);
              return (
                <div key={row.bureau_account_id} className="file-row"
                  style={{ display: 'flex', gap: 10, alignItems: 'center',
                    flexWrap: 'wrap', padding: '9px 0' }}>
                  <div style={{ flex: 1, minWidth: 220 }}>
                    <strong>{row.lender}</strong>{' '}
                    <span style={{ color: 'var(--text-3)' }}>{row.masked}</span>
                    <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
                      {row.reason} · {Math.round((row.confidence || 0) * 100)}% sure
                    </div>
                  </div>
                  <div style={{ minWidth: 200 }}>
                    → {account?.label || account?.institution || row.suggestion}
                  </div>
                  <button className="btn primary" disabled={busy}
                    onClick={() => decide(row.bureau_account_id, row.suggestion, true)}>
                    Same account
                  </button>
                  <button className="btn" disabled={busy}
                    onClick={() => decide(row.bureau_account_id, null, false)}>
                    Not a match
                  </button>
                </div>
              );
            })}
          </Card>
        </>
      )}

      {recon?.balance_deltas?.length > 0 && (
        <>
          <div className="section-title">Where the figures disagree</div>
          <Card sub="A bureau reports monthly and can be weeks behind, so a gap
            is worth a look rather than an alarm.">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Lender</th>
                    <th className="right">Bureau says</th>
                    <th className="right">Statements say</th>
                    <th className="right">Difference</th>
                  </tr>
                </thead>
                <tbody>
                  {recon.balance_deltas.map((row) => (
                    <tr key={row.bureau_account_id}>
                      <td>{row.lender} <span style={{ color: 'var(--text-3)' }}>
                        {row.masked}</span></td>
                      <td className="right num">{money(Number(row.bureau_balance))}</td>
                      <td className="right num">{money(Number(row.ledger_balance))}</td>
                      <td className="right num">
                        <span className={Number(row.difference) >= 0 ? 'neg' : 'pos'}>
                          {compact(Number(row.difference))}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}

      <div className="section-title">Everything the bureau reports</div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
        <Chip tone="pos">{counts.linked || 0} matched to an account here</Chip>
        <Chip tone="warn">{counts.blind_spots || 0} with no statements</Chip>
        <Chip>{counts.unreported_here || 0} closed or dormant</Chip>
        <button className="btn" style={{ marginLeft: 'auto', fontSize: 12 }}
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            await api.request('/api/bureau/rematch', { method: 'POST' })
              .catch(() => {});
            await load();
            setBusy(false);
          }}>
          Re-run matching
        </button>
      </div>
      <BureauAccountTable
        rows={[...(recon?.linked || []), ...(recon?.bureau_only || [])]}
        ledger={recon?.ledger_only || []}
        onChanged={load} />
    </>
  );
}

function BureauAccountTable({ rows, ledger = [], onChanged }) {
  const [busy, setBusy] = React.useState('');
  if (!rows.length) return <Empty title="No accounts in this report" />;

  /* The matcher has three answers, and this table used to render two.
   *
   * Above AUTO_LINK_CONFIDENCE a bureau line is linked outright; below
   * SUGGEST_CONFIDENCE it is left alone. In between it is SUGGESTED, which
   * means "this is probably that card, but two cards from the same bank match
   * each other's lender and type exactly and guessing wrong puts one card's
   * debt on the other's row" - so a person has to say.
   *
   * Nothing asked them. The column keyed off `account_id`, which only a link
   * sets, so all nine of this holder's suggestions rendered as "no
   * statements" alongside the genuinely unknown ones, under a heading saying
   * their money was missing from every total. The backend has sent the
   * candidate, the confidence and the reason on every row all along, and the
   * endpoint to confirm or reject one has been there just as long. */
  const nameOf = (accountId) => (ledger.find((a) => a.account_id === accountId)
    || {}).label || 'an account here';

  const decide = async (row, confirmed) => {
    setBusy(row.bureau_account_id);
    try {
      await api.request(
        `/api/bureau/accounts/${row.bureau_account_id}/match`,
        { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            confirmed, account_id: confirmed ? row.suggestion : null }) });
      if (onChanged) await onChanged();
    } catch (e) { /* the row stays as it was; nothing is silently linked */ }
    setBusy('');
  };
  return (
    <Card>
      <div className="table-wrap scroll-y" style={{ maxHeight: 460 }}>
        <table>
          <thead>
            <tr>
              <th>Lender</th><th>Type</th><th>Number</th><th>Status</th>
              <th className="right">Outstanding</th>
              <th className="right">Worst DPD</th>
              <th>Here?</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.bureau_account_id}>
                <td><div className="truncate" style={{ maxWidth: 200 }}>
                  {row.lender}</div></td>
                <td><Chip>{titleCase(row.account_type)}</Chip></td>
                <td className="num">{row.masked || '—'}</td>
                <td>
                  <Chip tone={row.status === 'open' ? 'accent'
                    : row.status === 'delinquent' ? 'neg' : ''}>
                    {row.status}
                  </Chip>
                </td>
                <td className="right num">
                  {row.balance ? money(Number(row.balance)) : '—'}
                </td>
                <td className="right num">
                  {row.worst_dpd > 0
                    ? <span className="neg">{row.worst_dpd}</span> : '0'}
                </td>
                <td>
                  {row.account_id ? <Chip tone="pos">matched</Chip>
                    : row.suggestion ? (
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center',
                        flexWrap: 'wrap' }}>
                        <Chip tone="accent"
                          title={row.reason || ''}>suggested</Chip>
                        <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                          {nameOf(row.suggestion)}
                        </span>
                        <button className="btn" style={{ fontSize: 11 }}
                          disabled={busy === row.bureau_account_id}
                          onClick={() => decide(row, true)}>Link</button>
                        <button className="btn" style={{ fontSize: 11 }}
                          disabled={busy === row.bureau_account_id}
                          onClick={() => decide(row, false)}>Not it</button>
                      </div>
                    )
                    : row.is_blind_spot
                      ? <Chip tone="warn">no statements</Chip>
                      : <Chip>closed</Chip>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
