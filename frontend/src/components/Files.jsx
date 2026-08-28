import React from 'react';
import { dateLabel, money, titleCase } from '../lib';
import { Callout, Card, Chip, Empty, Stat } from './ui';

const STATUS = {
  ok: { tone: 'pos', label: 'Reconciled' },
  unreconciled: { tone: 'warn', label: 'Did not balance' },
  failed: { tone: 'neg', label: 'Could not parse' },
  needs_password: { tone: 'warn', label: 'Password needed' },
};

const RECON = {
  passed: { tone: 'pos', label: 'Balances tie out' },
  failed: { tone: 'neg', label: 'Discrepancy' },
  not_applicable: { tone: '', label: 'No balances stated' },
};

export default function Files({ data }) {
  const statements = data.statements || [];
  const accounts = data.accounts || [];
  const transfers = data.transfers || {};
  const quality = data.data_quality || {};

  if (!statements.length) {
    return <Empty title="No files analyzed yet" />;
  }

  return (
    <>
      <div className="section-title">Accounts detected</div>
      <div className="grid cols-3">
        {accounts.map((a) => (
          <Card key={a.id} title={a.display_name} sub={titleCase(a.account_type)}>
            <div className="stat">
              <div className="stat-label">
                {a.is_liability ? 'Outstanding' : 'Balance'}
              </div>
              <div className={`stat-value num ${a.is_liability ? 'neg' : ''}`} style={{ fontSize: 21 }}>
                {money(a.is_liability ? a.principal_outstanding : a.current_balance)}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
              {a.interest_rate && <Chip>{a.interest_rate}% p.a.</Chip>}
              {a.emi_amount && <Chip>EMI {money(a.emi_amount)}</Chip>}
              {a.credit_limit && <Chip>Limit {money(a.credit_limit)}</Chip>}
              {a.holder_name && <Chip>{a.holder_name}</Chip>}
            </div>
          </Card>
        ))}
      </div>

      <div className="section-title">Reconciliation</div>
      <div className="grid cols-4">
        <Stat
          label="Files processed"
          value={String(quality.files_processed ?? statements.length)}
        />
        <Stat
          label="Reconciled"
          value={String(quality.files_reconciled ?? 0)}
          tone="pos"
          note="Transactions explain the full balance movement"
        />
        <Stat
          label="Unreconciled"
          value={String(quality.files_unreconciled ?? 0)}
          tone={quality.files_unreconciled ? 'neg' : undefined}
        />
        <Stat
          label="Duplicates removed"
          value={String(quality.duplicates_removed ?? 0)}
          note="From overlapping statement periods"
        />
      </div>

      <Card style={{ marginTop: 14 }}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>File</th><th>Format</th><th>Period</th>
                <th className="right">Rows</th>
                <th className="right">Opening</th><th className="right">Closing</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {statements.map((s, i) => {
                const status = STATUS[s.status] || { tone: '', label: s.status };
                const recon = s.reconciliation
                  ? RECON[s.reconciliation.status] || { tone: '', label: s.reconciliation.status }
                  : null;
                return (
                  <tr key={`${s.filename}-${i}`}>
                    <td>
                      <div className="truncate" style={{ maxWidth: 240 }}>{s.filename}</div>
                      {s.extractor && (
                        <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                          via {s.extractor}
                        </span>
                      )}
                    </td>
                    <td><Chip>{(s.format || '').toUpperCase()}</Chip></td>
                    <td className="nowrap" style={{ fontSize: 12.5 }}>
                      {s.period_start ? `${dateLabel(s.period_start)} → ${dateLabel(s.period_end)}` : '—'}
                    </td>
                    <td className="right num">{s.transaction_count ?? 0}</td>
                    <td className="right num nowrap">{s.opening_balance != null ? money(s.opening_balance) : '—'}</td>
                    <td className="right num nowrap">{s.closing_balance != null ? money(s.closing_balance) : '—'}</td>
                    <td>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-start' }}>
                        <Chip tone={status.tone}>{status.label}</Chip>
                        {recon && <Chip tone={recon.tone}>{recon.label}</Chip>}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {statements.some((s) => s.reconciliation?.status === 'failed' || s.warnings?.length) && (
        <>
          <div className="section-title">Parse notes</div>
          <Card>
            {statements.map((s, i) => {
              const notes = [
                ...(s.reconciliation?.status === 'failed' ? [s.reconciliation.message] : []),
                ...(s.warnings || []),
              ];
              if (!notes.length) return null;
              return (
                <div key={i} style={{ marginBottom: 14 }}>
                  <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>{s.filename}</div>
                  {notes.map((n, j) => (
                    <Callout
                      tone={s.reconciliation?.status === 'failed' && j === 0 ? 'neg' : 'warn'}
                      key={j}
                    >
                      {n}
                    </Callout>
                  ))}
                </div>
              );
            })}
          </Card>
        </>
      )}

      {transfers.pairs?.length > 0 && (
        <>
          <div className="section-title">
            Transfers matched between your accounts
          </div>
          <Card sub={`${money(transfers.double_count_avoided)} kept out of spending totals`}>
            <Callout tone="pos">
              Each of these appears on two statements — money leaving one account and
              arriving in another. Counting both sides would have inflated your
              spending by {money(transfers.double_count_avoided)}.
            </Callout>
            <div className="table-wrap scroll-y" style={{ marginTop: 10 }}>
              <table>
                <thead>
                  <tr>
                    <th>Type</th><th>From</th><th>To</th>
                    <th className="right">Amount</th><th className="right">Gap</th>
                  </tr>
                </thead>
                <tbody>
                  {transfers.pairs.map((p) => (
                    <tr key={p.pair_id}>
                      <td><Chip tone="accent">{p.kind.replace(/_/g, ' ')}</Chip></td>
                      <td className="truncate" style={{ maxWidth: 190 }}>{p.from}</td>
                      <td className="truncate" style={{ maxWidth: 190 }}>{p.to}</td>
                      <td className="right num nowrap">{money(p.amount)}</td>
                      <td className="right num">{p.day_gap}d</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </>
  );
}
