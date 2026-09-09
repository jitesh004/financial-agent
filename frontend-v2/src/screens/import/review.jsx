/* Steps 5 and 6: what has been read, and letting it count.
 *
 * Nothing on the Review step is in the ledger. Everything there was parsed
 * into a staging area no screen, table or total reads, and it stays there
 * until Build runs. That is why the step exists: a badly-read statement is
 * something you look at and reject, not something you discover inside a figure
 * that is already wrong.
 *
 * Groups are what you judge - "should this card's statements count at all" -
 * and the files inside them are what you correct: "all of them except that
 * one". Both are checkboxes, because both questions get asked.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../core/api';
import { count, money } from '../../core/format';
import {
  Button, Callout, Chip, ConfirmButton, Empty, IconButton, Loading,
} from '../../ui';
import JobProgress from '../../ui/JobProgress';

const TYPE_LABEL = {
  credit_card: 'Card', savings: 'Bank', current: 'Bank', loan: 'Loan',
  investment: 'Investment', credit_report: 'Credit report', unknown: 'Not yet read',
};

/* What the reconciliation gate concluded, in words that cannot be mistaken for
   "this file could not be read".
 *
 * A bare "failed" beside a filename reads as a broken document. It is not: a
 * card statement showed "· failed" having parsed perfectly, and what had
 * actually happened is that its rows account for 45,509.92 of a declared
 * 56,858.15 - the statement is short 11,348.23 of real charges. That is the
 * gate doing its job, and the most valuable thing on the screen, but only if
 * it says so. */
const RECON_WORDS = {
  passed: 'balances', ok: 'balances', failed: 'does not balance',
  unreconciled: 'nothing to check it against', not_applicable: 'no balances printed',
};

function fileNote(file) {
  if (file.superseded_by) {
    return `Superseded by ${file.superseded_by_name || 'a statement'} — the statement `
      + 'covering these dates is the reconciled record of the same money.';
  }
  if (file.parse_status === 'needs_password') {
    return 'Locked. No password derived from your profile opened it.';
  }
  if (file.parse_status === 'failed') return file.parse_message;
  if (file.parse_status === 'empty') return 'Read, but no transactions in it.';
  if (file.parse_status === 'pending') return 'Not read yet.';
  // Read fine, but the figures do not add up to what the issuer printed.
  if (file.recon_status === 'failed') return file.parse_message;
  /* Read fine, and empty.
   *
   * The reader says so - "0 holding(s). No holdings were read." - and this
   * used to say nothing at all, because the parse did not fail and the
   * reconciliation did not either. So a holdings statement whose table could
   * not be found sat here ticked, looking exactly like the four beside it that
   * had six hundred rows between them, was included in the build, and
   * contributed nothing. The Portfolio screen then stayed empty with no
   * explanation anywhere in the app. */
  if (!file.row_count) {
    return file.parse_message
      || 'Read, but nothing was found in it to count.';
  }
  return null;
}

/* What the reader could not do, in its own words.
 *
 * Separate from the note above because these are not one sentence and not
 * always about failure - "no valuation date found; the holdings cannot be
 * dated" is a real limit on a document that otherwise read perfectly. The
 * server has always sent them and nothing has ever shown them. */
function fileWarnings(file) {
  const warnings = file.warnings || [];
  if (!warnings.length) return null;
  // Not repeated when the note above already says the same thing.
  const note = fileNote(file);
  return warnings.filter((w) => w !== note);
}

export function ReviewStep({ onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  /* Collapsed by default, EXCEPT where something needs explaining. A group
     that produced no rows, could not be read, did not balance, or whose reader
     reported a limit is one whose per-file notes are the reason somebody is on
     this screen - so those open themselves rather than hiding the answer
     behind a chevron. */
  const [open, setOpen] = useState(() => new Set());
  const auto = useRef(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const next = await api.stagingReview();
      setData(next);
      onChanged?.(next);
      if (!auto.current) {
        auto.current = true;
        const notable = (next.groups || []).filter(
          (g) => !g.row_count || g.failed_count > 0 || g.unbalanced_count > 0
            || (g.files || []).some((f) => (f.warnings || []).length));
        if (notable.length) setOpen(new Set(notable.map((g) => g.key)));
      }
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
      // them made this chip disagree with the next screen.
      if (g.kind === 'statement' || g.kind === 'alert') rows += g.row_count || 0;
      if (g.first) months.add(g.first.slice(0, 7));
      if (g.last) months.add(g.last.slice(0, 7));
    }
    const sorted = [...months].sort();
    return { rows, from: sorted[0], to: sorted[sorted.length - 1] };
  }, [groups]);

  const send = async (body) => {
    setBusy(true);
    try { await api.stagingSelect(body); await load(); } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  /* Unticking and forgetting are different answers. Untick means "not this
     time" - the document stays read, and re-ticking costs nothing. Forget
     means "this should not be here at all", which is what you want after
     narrowing a scan: staging keeps everything it has ever read. */
  const forget = async (ids) => {
    setBusy(true);
    try { await api.stagingRemove(ids); await load(); } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  if (error) return <Callout tone="neg">{error}</Callout>;
  if (!data) return <Loading label="Reading what came in…" />;
  if (!groups.length) {
    return (
      <Empty title="Nothing staged yet" icon="inbox">
        Scan your mailbox or add a file on the Source step. What gets read lands here,
        and none of it counts until you build the ledger.
      </Empty>
    );
  }

  const off = groups.filter((g) => !g.included).length;

  return (
    <>
      <p className="lead" style={{ margin: 0 }}>
        Everything that has been read, grouped by account and by where it came from.{' '}
        <strong>None of this is in your ledger yet.</strong> Tick what should count,
        then build on the next step. Untick means “not this time”;{' '}
        <strong>Forget</strong> removes a document from the wizard altogether.
      </p>

      <div className="row">
        <Chip tone="acc">{count(totals.rows)} rows will count</Chip>
        {totals.from && <Chip>{totals.from} → {totals.to}</Chip>}
        {off > 0 && <Chip tone="warn">{off} group{off === 1 ? '' : 's'} off</Chip>}
        {data.superseded > 0 && <Chip tone="warn">{data.superseded} superseded</Chip>}
        <span className="spacer" />
        <Button size="sm" disabled={busy}
          onClick={() => send({ groups: groups.map((g) => ({ key: g.key, include: true })) })}>
          Include all
        </Button>
        <Button size="sm" disabled={busy}
          onClick={() => send({ groups: groups.map((g) => ({ key: g.key, include: false })) })}>
          Exclude all
        </Button>
      </div>

      {groups.map((group) => {
        const expanded = open.has(group.key);
        return (
          <div className="card sunken" key={group.key} style={{ padding: '10px 12px' }}>
            <div className="row" style={{ flexWrap: 'nowrap', alignItems: 'flex-start' }}>
              <input
                type="checkbox" checked={group.included} disabled={busy}
                ref={(el) => { if (el) el.indeterminate = Boolean(group.partial); }}
                onChange={() => send({ groups: [{ key: group.key, include: !group.included }] })}
                style={{ marginTop: 4, accentColor: 'var(--accent)' }}
              />
              <IconButton
                icon={expanded ? 'chevron-down' : 'chevron'} size="xs" className="ghost"
                label={expanded ? 'Collapse' : 'Expand'}
                onClick={() => setOpen((p) => {
                  const next = new Set(p);
                  if (next.has(group.key)) next.delete(group.key); else next.add(group.key);
                  return next;
                })}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="row tight">
                  <strong style={{ fontSize: 13 }}>{group.account_label}</strong>
                  <span className="tiny dim">
                    {TYPE_LABEL[group.account_type] || group.account_type}
                  </span>
                  <Chip>{group.kind_label}</Chip>
                  <Chip>
                    {group.selected_count} of {group.file_count} file
                    {group.file_count === 1 ? '' : 's'}
                  </Chip>
                  {group.row_count > 0 && <Chip>{group.row_count} rows</Chip>}
                  {/* A group that read fine and produced nothing has no row
                      count to show, so without this it looked exactly like one
                      whose rows had simply not been counted yet - ticked, and
                      about to contribute nothing to the build. */}
                  {!group.row_count && !group.failed_count && !group.superseded_count && (
                    <Chip tone="warn">nothing read from these</Chip>
                  )}
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
                  <div className="tiny dim" style={{ marginTop: 3 }}>
                    {group.first ? `${group.first} → ${group.last} · ` : ''}
                    {group.kind_note}
                  </div>
                )}
              </div>
              <div className="right tiny nowrap">
                {Number(group.debits) > 0 && <div>−{money(group.debits)}</div>}
                {Number(group.credits) > 0 && (
                  <div className="pos">+{money(group.credits)}</div>
                )}
              </div>
              <ConfirmButton
                size="xs" disabled={busy}
                title="Remove these documents from staging entirely"
                question={`Forget all ${group.file_count}?`}
                confirmLabel="Forget"
                onConfirm={() => forget(group.files.map((f) => f.id))}
              >
                Forget
              </ConfirmButton>
            </div>

            {expanded && group.files.map((file) => {
              const note = fileNote(file);
              const dead = Boolean(file.superseded_by);
              return (
                <label
                  key={file.id}
                  className="row"
                  style={{
                    marginLeft: 26, padding: '6px 8px', alignItems: 'flex-start',
                    opacity: file.selected && !dead ? 1 : 0.5,
                    cursor: dead ? 'not-allowed' : 'pointer',
                    borderTop: '1px solid var(--line)',
                  }}
                >
                  <input
                    type="checkbox" checked={file.selected && !dead} disabled={busy || dead}
                    onChange={() => send({ files: [{ ids: [file.id], include: !file.selected }] })}
                    style={{ marginTop: 3, accentColor: 'var(--accent)' }}
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontSize: 12.5,
                      textDecoration: dead ? 'line-through' : 'none',
                      overflowWrap: 'anywhere',
                    }}>
                      {file.filename}
                    </div>
                    <div className="tiny dim">
                      {file.period_start ? `${file.period_start} → ${file.period_end}` : '—'}
                      {file.row_count ? ` · ${file.row_count} rows` : ''}
                      {file.origin === 'upload' ? ' · uploaded' : ''}
                      {file.recon_status
                        ? ` · ${RECON_WORDS[file.recon_status] || file.recon_status}` : ''}
                    </div>
                    {note && (
                      <div className="tiny" style={{
                        marginTop: 2,
                        color: file.parse_status === 'failed' ? 'var(--neg)'
                          : (file.recon_status === 'failed' || !file.row_count)
                            ? 'var(--warn)' : 'var(--text-3)',
                      }}>
                        {note}
                      </div>
                    )}
                    {(fileWarnings(file) || []).map((warning) => (
                      <div key={warning} className="tiny"
                        style={{ marginTop: 2, color: 'var(--text-3)' }}>
                        {warning}
                      </div>
                    ))}
                  </div>
                  <div className="right tiny nowrap">
                    {Number(file.debits) > 0 && <div>−{money(file.debits)}</div>}
                    {Number(file.credits) > 0 && <div className="pos">+{money(file.credits)}</div>}
                  </div>
                  <ConfirmButton
                    size="xs" disabled={busy}
                    title="Remove this document from staging entirely"
                    confirmLabel="Forget"
                    onConfirm={() => forget([file.id])}
                  >
                    ✕
                  </ConfirmButton>
                </label>
              );
            })}
          </div>
        );
      })}
    </>
  );
}

/* ═══════════════════════════════════════════════════════ 6. build ═══════ */

/* The only step that changes what any screen shows.
 *
 * Everything before this reads documents into a staging area. This rebuilds
 * the ledger from the files ticked on Review - all of them, every time, not
 * just the new ones. That is deliberate: the selection IS the ledger, so a
 * rebuild that added to the previous one could never remove anything, and
 * unticking a file would be a button that does nothing.
 */
export function BuildStep({ staged, job, stage, onRun, onFinished }) {
  const running = stage === 'processing';
  const done = stage === 'done' && job?.kind === 'stage_process' && job.status === 'complete';
  const result = done ? (job.result || {}) : null;

  const rows = staged?.rows || 0;
  const selected = staged?.selected || 0;

  return (
    <>
      <div>
        <div style={{ fontWeight: 640, fontSize: 14, marginBottom: 6 }}>
          Build the ledger from what you ticked
        </div>
        <p className="lead" style={{ margin: '0 0 10px' }}>
          Your ledger is replaced by exactly the files selected on Review — the whole
          platform recomputes: categories, transfers, recurring items, every total on
          every screen. Anything you decided by hand is put back afterwards, and you
          are told about anything that could not be.
        </p>
        <div className="row tight">
          <Chip tone="acc">{selected} file{selected === 1 ? '' : 's'} selected</Chip>
          <Chip>{count(rows)} rows</Chip>
          {staged?.superseded > 0 && <Chip tone="warn">{staged.superseded} superseded, skipped</Chip>}
          {staged?.pending > 0 && <Chip tone="warn">{staged.pending} still unread</Chip>}
        </div>
      </div>

      {!selected && (
        <Callout tone="warn">
          Nothing is ticked on Review, so there is nothing to build. Go back a step and
          select at least one file.
        </Callout>
      )}

      {staged?.processed > 0 && !done && (
        <Callout tone="acc">
          Your screens currently show a ledger of <strong>{count(staged.processed)}</strong>{' '}
          rows from the last time you built one. Nothing staged has touched it.
        </Callout>
      )}

      {running && <JobProgress job={job} title="Rebuilding your ledger" />}

      {done && result && (
        <Callout tone="pos">
          <strong>
            {count(result.transactions || 0)} transaction
            {result.transactions === 1 ? '' : 's'} across {result.accounts || 0} account
            {result.accounts === 1 ? '' : 's'} now count.
          </strong>{' '}
          {result.statements || 0} statement{result.statements === 1 ? '' : 's'}
          {result.bureau_reports ? `, ${result.bureau_reports} credit report(s)` : ''}
          {result.portfolios ? `, ${result.portfolios} portfolio(s)` : ''} were used.
          {typeof result.decisions_applied === 'number' && (
            <> {result.decisions_applied} of your own decisions were put back
              {result.decisions_orphaned
                ? `; ${result.decisions_orphaned} could not be matched to a row and are `
                  + 'kept for when it returns.' : '.'}
            </>
          )}
          {result.uncategorized > 0 && (
            <> {result.uncategorized} row{result.uncategorized === 1 ? '' : 's'} the rules
              could not place are waiting for the model under Settings — this step never
              calls it, so it never costs you money you did not ask to spend.</>
          )}
          {result.unread > 0 && (
            <> {result.unread} selected document{result.unread === 1 ? '' : 's'} could not
              be read — usually a password-protected PDF — and were skipped.</>
          )}
          {result.failed > 0 && (
            <> <strong>{result.failed} could not be rebuilt:</strong>{' '}
              {(result.failures || []).join('; ')}</>
          )}
          {' '}Every screen has been rebuilt.
        </Callout>
      )}

      <div className="row">
        <Button variant="primary" disabled={running || !selected} onClick={onRun}
          icon={done ? 'refresh' : 'play'}>
          {running ? 'Rebuilding…' : done ? 'Rebuild again' : 'Build the ledger'}
        </Button>
        {done && <Button onClick={onFinished}>Close and look</Button>}
        {!running && !done && Boolean(selected) && (
          <span className="tiny dim">This replaces your current ledger.</span>
        )}
      </div>
    </>
  );
}
