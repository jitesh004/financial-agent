/* The credit bureau's account of what you owe, laid against this app's.
 *
 * The valuable output is not the score. It is `bureau_only`: accounts a lender
 * is reporting that no statement here has ever covered. Every total in this
 * app is blind to those, and nothing else in it can discover them.
 */

import React, { useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { useToast } from '../core/toast';
import { useAccounts } from '../core/ledger';
import { compact, dateLabel, money, titleCase } from '../core/format';
import {
  Button, Callout, Card, Chip, Empty, Loading, Section, Stat, Table,
} from '../ui';

const BAND = {
  excellent: 'pos', 'very good': 'pos', good: 'pos',
  fair: 'warn', poor: 'neg', 'very poor': 'neg',
};

export default function Credit({ onImport }) {
  const toast = useToast();
  const [busy, setBusy] = useState('');
  const overview = useQuery('bureau', () => api.bureau());
  const recon = useQuery('bureau-recon', () => api.bureauReconciliation());
  const { data: accounts = [] } = useAccounts();

  const reload = () => Promise.all([overview.refetch(), recon.refetch()]);

  const decide = async (bureauAccountId, accountId, confirmed) => {
    setBusy(bureauAccountId);
    try {
      await api.matchBureauAccount(bureauAccountId, accountId, confirmed);
      await reload();
    } catch (e) { toast.fail('That match was not saved', e.message); }
    finally { setBusy(''); }
  };

  if (overview.error) return <Callout tone="warn">{overview.error.message}</Callout>;
  if (overview.loading) return <Loading label="Reading your credit report…" />;

  const data = overview.data;
  if (!data.reports.length) {
    return (
      <Empty title="No credit report imported yet" icon="shield"
        action={onImport && (
          <Button variant="primary" icon="upload" onClick={onImport}>Import statements</Button>
        )}>
        Scan your mailbox for bureau reports, or add a CIBIL, CRIF, Experian or Equifax
        PDF. A bureau lists every account a lender reports on you, which is the only way
        this app can find one it has no statements for.
      </Empty>
    );
  }

  const totals = data.totals || {};
  const counts = recon.data?.counts || {};
  const bureauOnly = recon.data?.bureau_only || [];
  const blindSpots = bureauOnly.filter((a) => a.is_blind_spot);
  const suggestions = bureauOnly.filter((a) => a.suggestion);

  return (
    <>
      <div className="grid cols-4">
        {data.latest_by_bureau.map((report) => (
          <Card key={report.id} title={titleCase(report.bureau)}
            sub={report.pulled_on ? dateLabel(report.pulled_on) : ''}>
            <div className="stat-value" style={{ fontSize: 32 }}>{report.score ?? '—'}</div>
            {report.score_band && (
              <Chip tone={BAND[report.score_band] || ''}>{report.score_band}</Chip>
            )}
          </Card>
        ))}
        <Stat label="Reported outstanding" value={Number(totals.outstanding) || 0}
          note={`${totals.open_accounts || 0} open accounts`} tone="neg" />
        {Number(totals.overdue) > 0 && (
          <Stat label="Overdue now" value={Number(totals.overdue)} tone="neg"
            note={`worst ${totals.worst_dpd} days past due`} />
        )}
      </div>

      {blindSpots.length > 0 && (
        <>
          <Section title="Accounts you have no statements for" />
          <Callout tone="warn">
            A lender is reporting {blindSpots.length} open account
            {blindSpots.length === 1 ? '' : 's'} that nothing here covers. Money owed on{' '}
            {blindSpots.length === 1 ? 'it is' : 'them is'} missing from every total in
            this app — and from anything an agent tells you.
          </Callout>
          <Card pad={false}>
            <Table>
              <thead>
                <tr>
                  <th>Lender</th><th>Type</th><th>Number</th>
                  <th className="right">Outstanding</th><th className="right">Overdue</th>
                  <th>Opened</th>
                </tr>
              </thead>
              <tbody>
                {blindSpots.map((row) => (
                  <tr key={row.bureau_account_id}>
                    <td>{row.lender}</td>
                    <td><Chip>{titleCase(row.account_type)}</Chip></td>
                    <td className="num">{row.masked || '—'}</td>
                    <td className="right num">{row.balance ? money(Number(row.balance)) : '—'}</td>
                    <td className="right num">
                      {Number(row.overdue) > 0
                        ? <span className="neg">{money(Number(row.overdue))}</span> : '—'}
                    </td>
                    <td className="nowrap">{row.opened_on || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
        </>
      )}

      {suggestions.length > 0 && (
        <>
          <Section title="Possible matches, for you to confirm" />
          <Callout>
            These look like accounts already here, but not certainly enough to link on
            their own. Two cards from the same bank match each other on everything
            except the digits, and guessing wrong would put one card&apos;s debt on the
            other.
          </Callout>
          <Card pad>
            <div className="col">
              {suggestions.map((row) => {
                const account = accounts.find((a) => a.id === row.suggestion);
                return (
                  <div key={row.bureau_account_id} className="list-row">
                    <div className="grow">
                      <strong>{row.lender}</strong>{' '}
                      <span className="dim">{row.masked}</span>
                      <div className="tiny dim">
                        {row.reason} · {Math.round((row.confidence || 0) * 100)}% sure
                      </div>
                    </div>
                    <div style={{ minWidth: 190 }}>
                      → {account?.display_name || account?.institution || row.suggestion}
                    </div>
                    <Button variant="primary" size="sm"
                      disabled={busy === row.bureau_account_id}
                      onClick={() => decide(row.bureau_account_id, row.suggestion, true)}>
                      Same account
                    </Button>
                    <Button size="sm" disabled={busy === row.bureau_account_id}
                      onClick={() => decide(row.bureau_account_id, null, false)}>
                      Not a match
                    </Button>
                  </div>
                );
              })}
            </div>
          </Card>
        </>
      )}

      {recon.data?.balance_deltas?.length > 0 && (
        <>
          <Section title="Where the figures disagree" />
          <Card pad={false}
            sub="A bureau reports monthly and can be weeks behind, so a gap is worth a look rather than an alarm.">
            <Table>
              <thead>
                <tr>
                  <th>Lender</th>
                  <th className="right">Bureau says</th>
                  <th className="right">Statements say</th>
                  <th className="right">Difference</th>
                </tr>
              </thead>
              <tbody>
                {recon.data.balance_deltas.map((row) => (
                  <tr key={row.bureau_account_id}>
                    <td>{row.lender} <span className="dim">{row.masked}</span></td>
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
            </Table>
          </Card>
        </>
      )}

      <Section
        title="Everything the bureau reports"
        actions={(
          <Button size="sm" icon="refresh" disabled={Boolean(busy)}
            onClick={async () => {
              setBusy('rematch');
              await api.rematchBureau().catch(() => {});
              await reload();
              setBusy('');
            }}>
            Re-run matching
          </Button>
        )}
      />
      <div className="row tight">
        <Chip tone="pos">{counts.linked || 0} matched to an account here</Chip>
        <Chip tone="warn">{counts.blind_spots || 0} with no statements</Chip>
        <Chip>{counts.unreported_here || 0} closed or dormant</Chip>
      </div>

      <BureauTable
        rows={[...(recon.data?.linked || []), ...bureauOnly]}
        ledger={recon.data?.ledger_only || []}
        busy={busy}
        onDecide={decide}
      />
    </>
  );
}

/* The matcher has three answers, and this table renders all of them.
 *
 * Above the auto-link threshold a bureau line is linked outright; below the
 * suggest threshold it is left alone. In between it is SUGGESTED, which means
 * "this is probably that card, but two cards from the same bank match each
 * other's lender and type exactly and guessing wrong puts one card's debt on
 * the other" - so a person has to say. */
function BureauTable({ rows, ledger = [], busy, onDecide }) {
  if (!rows.length) return <Empty title="No accounts in this report" icon="shield" />;
  const nameOf = (id) => (ledger.find((a) => a.account_id === id) || {}).label
    || 'an account here';

  return (
    <Card pad={false}>
      <Table scrollY maxHeight={520}>
        <thead>
          <tr>
            <th>Lender</th><th>Type</th><th>Number</th><th>Status</th>
            <th className="right">Outstanding</th><th className="right">Worst DPD</th>
            <th>Here?</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.bureau_account_id}>
              <td><div className="truncate" style={{ maxWidth: 200 }}>{row.lender}</div></td>
              <td><Chip>{titleCase(row.account_type)}</Chip></td>
              <td className="num">{row.masked || '—'}</td>
              <td>
                <Chip tone={row.status === 'open' ? 'acc'
                  : row.status === 'delinquent' ? 'neg' : ''}>
                  {row.status}
                </Chip>
              </td>
              <td className="right num">{row.balance ? money(Number(row.balance)) : '—'}</td>
              <td className="right num">
                {row.worst_dpd > 0 ? <span className="neg">{row.worst_dpd}</span> : '0'}
              </td>
              <td>
                {row.account_id ? <Chip tone="pos">matched</Chip>
                  : row.suggestion ? (
                    <div className="row tight">
                      <Chip tone="acc" title={row.reason || ''}>suggested</Chip>
                      <span className="tiny dim">{nameOf(row.suggestion)}</span>
                      <Button size="xs" disabled={busy === row.bureau_account_id}
                        onClick={() => onDecide(row.bureau_account_id, row.suggestion, true)}>
                        Link
                      </Button>
                      <Button size="xs" disabled={busy === row.bureau_account_id}
                        onClick={() => onDecide(row.bureau_account_id, null, false)}>
                        Not it
                      </Button>
                    </div>
                  ) : row.is_blind_spot
                    ? <Chip tone="warn">no statements</Chip>
                    : <Chip>closed</Chip>}
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
    </Card>
  );
}
