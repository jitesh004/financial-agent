/* The six steps of an import.
 *
 * The order is the argument: what to look for, look for it, choose what to
 * fetch, read it, judge what was read, and only then let any of it count.
 * Nothing before the last step changes a single figure anywhere in the app,
 * and each step says so, because "I imported it and my numbers went wrong" is
 * the failure this whole flow exists to make impossible.
 */

import React, { useMemo, useState } from 'react';
import { api } from '../../core/api';
import { bytes, count, money } from '../../core/format';
import {
  Button, Callout, Chip, ConfirmButton, Empty, IconButton, PromptButton, Select,
} from '../../ui';
import JobProgress from '../../ui/JobProgress';
import Uploader from './Uploader';
import { rowKey } from './useImport';

const CAPS = [250, 500, 1000, 2500, 5000];

/* Sender category -> chip tone. The categories are the backend's, so all of
   them must be here: a missing key renders an undefined tone. */
export const CATEGORY_TONE = {
  bank: 'acc', card: 'pos', loan: 'warn', bureau: 'neg', broker: '', unknown: '',
};

function Group({ children, className = '' }) {
  return <div className={`card sunken ${className}`} style={{ padding: '10px 12px' }}>{children}</div>;
}

/* ═══════════════════════════════════════════════════════ 1. source ══════ */

/* The windows the server offers, plus whatever this source is actually set to.
   A <select> cannot display a value that is not among its options - it
   silently shows the first one instead, which is how a 2-month cap came to
   read "1 month". The list itself comes from the server, which is the only
   place it is decided. */
function periodsFor(periods, months) {
  const list = periods?.length ? periods : [{ label: 'Everything', months: null }];
  if (months == null || list.some((p) => p.months === months)) return list;
  return [...list, { label: `${months} month${months === 1 ? '' : 's'}`, months }]
    .sort((a, b) => (a.months ?? 1e9) - (b.months ?? 1e9));
}

export function SourceStep({
  intents, periods, chosen, onToggle, settingsFor, onSetting, sections, onUploaded,
  mailboxReady = true,
}) {
  const staged = Object.fromEntries((sections || []).map((s) => [s.key, s]));

  /* Files first when there is no mailbox, because then they are the only way
     in - and a page that opens on four tick boxes that cannot do anything
     reads as a dead end. */
  const uploader = (
    <Group>
      <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 2 }}>
        Files from this computer
      </div>
      <div className="small muted" style={{ marginBottom: 8 }}>
        {mailboxReady
          ? 'Anything Gmail does not carry. They are read on the next step with every '
            + 'other source.'
          : 'Bank, card, loan and investment statements you already have. They are read '
            + 'on the next step, exactly like anything a mailbox scan finds.'}
        {staged.upload?.staged > 0 && ` ${staged.upload.staged} staged so far.`}
      </div>
      <Uploader compact onComplete={onUploaded} />
    </Group>
  );

  return (
    <>
      <p className="lead" style={{ margin: 0 }}>
        {mailboxReady
          ? 'Tick the sources to scan and set how far back each should look. One shared '
            + 'window was wrong for every source at once — a holdings statement is a '
            + 'photograph of one date, a bank statement is money still to be accounted '
            + 'for, and alerts are noise a statement supersedes. They are scanned one '
            + 'after another, and any one can be re-scanned on its own later.'
          : 'Add the statements you have. Everything below works the same way whether a '
            + 'document came from a mailbox or from your own disk.'}
      </p>

      {!mailboxReady && uploader}

      {intents.map((one) => {
        const on = chosen.has(one.key) && mailboxReady;
        const s = settingsFor(one.key);
        const c = staged[one.key];
        return (
          <Group key={one.key}>
            <label className="row"
              style={{ cursor: mailboxReady ? 'pointer' : 'default', alignItems: 'flex-start',
                opacity: mailboxReady ? 1 : 0.55 }}>
              <input type="checkbox" checked={on} disabled={!mailboxReady}
                onChange={() => onToggle(one.key)}
                style={{ marginTop: 3, accentColor: 'var(--accent)' }} />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontWeight: 600, fontSize: 13 }}>
                  {one.label}
                </span>
                <span className="small muted" style={{ display: 'block' }}>
                  {one.description}
                </span>
              </span>
              {c?.staged > 0 && <Chip>{c.staged} staged</Chip>}
            </label>

            {on && (
              <div className="row" style={{ margin: '10px 0 0 26px', alignItems: 'flex-end' }}>
                <label className="field" style={{ maxWidth: 190 }}>
                  <span className="field-label">Look back</span>
                  <Select
                    value={s.months ?? ''}
                    onChange={(v) => onSetting(one.key, { months: v ? Number(v) : null })}
                    options={periodsFor(periods, s.months).map((p) => [p.months ?? '', p.label])}
                  />
                </label>
                <label className="field" style={{ maxWidth: 150 }}>
                  <span className="field-label">Max emails to read</span>
                  <Select
                    value={String(s.maxMessages)}
                    onChange={(v) => onSetting(one.key, { maxMessages: Number(v) })}
                    options={CAPS.map((n) => [String(n), String(n)])}
                  />
                </label>
                {one.max_months != null && (
                  <span className="tiny dim" style={{ maxWidth: 340 }}>
                    Starts at {one.max_months} months because these are unreconciled and
                    earn their place by being fresher than the statement covering them.
                    Widen it if you want to.
                  </span>
                )}
              </div>
            )}
          </Group>
        );
      })}

      {mailboxReady && uploader}

      {mailboxReady && chosen.size === 0 && (
        <Callout tone="warn">
          Nothing is ticked, so a scan has nothing to look for. Pick at least one
          source — or just add files above.
        </Callout>
      )}
    </>
  );
}

/* ═══════════════════════════════════════════════════════ 2. scanning ════ */

/* Sequential rather than parallel, which is the honest way round: Gmail rate
   limits, and four scans racing produce four progress bars that all crawl.
   More importantly each source keeps its OWN job, so re-scanning alerts in
   November does not disturb the statement scan run in August. */
function ScanSection({ source, jobId, onScan, onForget, running }) {
  const [job, setJob] = useState(null);
  const [open, setOpen] = useState(false);

  React.useEffect(() => {
    if (!jobId) { setJob(null); return undefined; }
    let live = true;
    let timer = null;
    const poll = async () => {
      const current = await api.job(jobId).catch(() => null);
      if (!live) return;
      setJob(current);
      // A finished job never changes again, so stop asking.
      if (current?.active) timer = setTimeout(poll, 900);
    };
    poll();
    return () => { live = false; clearTimeout(timer); };
  }, [jobId]);

  const result = job?.result;
  const found = result?.attachments?.length ?? result?.alerts?.length ?? null;
  const done = job && !job.active && job.status === 'complete';

  /* What the scan read and did NOT use, and why. A scan reporting "40 found"
     out of 425 read has made 385 decisions nobody can see, and "no account
     here ends 4345" is a fact about the ledger worth knowing. */
  const refused = [
    ...(result?.excluded || []).map((e) => e.reason),
    ...(result?.alerts || []).filter((a) => a.status !== 'imported')
      .map((a) => a.reason || a.status),
  ];
  const reasons = refused.reduce((acc, why) => {
    const k = why || 'no reason given';
    acc.set(k, (acc.get(k) || 0) + 1);
    return acc;
  }, new Map());

  return (
    <Group>
      <div className="row">
        <IconButton icon={open ? 'chevron-down' : 'chevron'} size="xs" className="ghost"
          label={open ? 'Collapse' : 'Expand'} onClick={() => setOpen((v) => !v)} />
        <strong style={{ fontSize: 13 }}>{source.label}</strong>
        {done && found != null && <Chip tone="pos">{found} found</Chip>}
        {source.staged > 0 && (
          <Chip title="Everything this source has ever staged, not just this scan">
            {source.staged} kept
          </Chip>
        )}
        {done && refused.length > 0 && <Chip tone="warn">{refused.length} not used</Chip>}
        {job?.active && <Chip tone="acc">scanning</Chip>}
        <span className="spacer" />
        {source.staged > 0 && (
          <ConfirmButton
            size="sm" disabled={running}
            title="Remove what this source has staged. Your ledger is untouched."
            question={`Forget ${source.staged} document(s)?`}
            confirmLabel="Forget"
            onConfirm={() => onForget(source.key)}
          >
            Forget {source.staged}
          </ConfirmButton>
        )}
        <Button size="sm" disabled={running} onClick={() => onScan(source.key)}>
          {job ? 'Re-scan' : 'Scan'}
        </Button>
      </div>

      {job?.active && <div style={{ marginTop: 8 }}><JobProgress job={job} trace={false} /></div>}
      {done && <div className="small muted" style={{ marginTop: 6 }}>{job.message}</div>}
      {job?.status === 'failed' && (
        <Callout tone="neg" style={{ marginTop: 8 }}>
          {job.errors?.join('; ') || 'That scan failed.'}
        </Callout>
      )}

      {open && reasons.size > 0 && (
        <div style={{ marginTop: 8 }}>
          <div className="tiny" style={{ fontWeight: 620, marginBottom: 4 }}>Not used</div>
          {[...reasons.entries()].sort((a, b) => b[1] - a[1]).map(([why, n]) => (
            <div className="tiny dim" key={why}><strong>{n}</strong> — {why}</div>
          ))}
        </div>
      )}
    </Group>
  );
}

export function ScanStep({ intents, chosen, sections, sourceJobs, busy, onScan, onForget }) {
  const known = Object.fromEntries((sections || []).map((s) => [s.key, s]));
  const picked = (intents || []).filter((one) => chosen.has(one.key));

  return (
    <>
      <p className="lead" style={{ margin: 0 }}>
        A scan reads your mailbox and lists what it found. Nothing is downloaded and
        nothing is read here — that is the next two steps.
      </p>
      {!picked.length && (
        <Callout tone="warn">
          No sources are ticked. Go back to <strong>Source</strong> and pick at least
          one — or add files from your computer there.
        </Callout>
      )}
      {picked.map((one) => (
        <ScanSection
          key={one.key}
          source={{ ...one, staged: known[one.key]?.staged || 0 }}
          jobId={sourceJobs?.[one.key]}
          running={busy}
          onScan={onScan}
          onForget={onForget}
        />
      ))}
    </>
  );
}

/* ═══════════════════════════════════════════════════════ 3. choose ══════ */

function FileRows({ rows, selected, onToggle }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, 10);
  return (
    <div style={{ marginTop: 8 }}>
      {shown.map((row) => {
        const key = rowKey(row);
        const on = selected.has(key);
        return (
          <label
            key={key}
            className="row"
            style={{ padding: '5px 6px', opacity: on ? 1 : 0.55, cursor: 'pointer' }}
          >
            <input type="checkbox" checked={on} onChange={() => onToggle(row)}
              style={{ accentColor: 'var(--accent)' }} />
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ display: 'block', fontSize: 12.5, overflowWrap: 'anywhere' }}>
                {row.filename}
              </span>
              <span className="tiny dim">
                {row.institution || row.sender_name}
                {row.date_iso ? ` · ${row.date_iso.slice(0, 10)}` : ''}
                {row.cached ? ' · already downloaded' : ''}
                {row.password_ready === false ? ' · no password for this one' : ''}
              </span>
            </span>
            <span className="tiny dim nowrap">{row.size ? bytes(row.size) : ''}</span>
          </label>
        );
      })}
      {rows.length > 10 && (
        <Button size="xs" style={{ marginTop: 6 }} onClick={() => setAll((v) => !v)}>
          {all ? 'Show fewer' : `Show all ${rows.length}`}
        </Button>
      )}
    </div>
  );
}

/* Every alert the scan read and did not use, as the transactions they would
   have been. A count answers "how many"; only the rows answer "which". An
   alert refused for naming a card you hold no statements for is a real payment
   missing from your ledger. */
function RefusedAlerts({ alerts }) {
  const [open, setOpen] = useState(false);
  if (!alerts.length) return null;
  const shown = open ? alerts.slice(0, 40) : alerts.slice(0, 6);
  return (
    <div style={{ marginTop: 10 }}>
      <div className="tiny" style={{ fontWeight: 620, marginBottom: 4 }}>
        Not used ({alerts.length})
      </div>
      <div className="tbl-wrap">
        <table className="tbl-compact">
          <thead>
            <tr><th>Date</th><th>Merchant</th><th>Account</th>
              <th className="right">Amount</th><th>Why not</th></tr>
          </thead>
          <tbody>
            {shown.map((a, i) => (
              <tr key={`${a.message_id}-${i}`}>
                <td className="nowrap">{a.date_iso ? a.date_iso.slice(0, 10) : '—'}</td>
                <td style={{ maxWidth: 200, overflowWrap: 'anywhere' }}>
                  {a.merchant || a.subject || '—'}
                </td>
                <td className="nowrap">
                  {a.institution || a.sender_name || '—'}
                  {a.account_suffix ? ` …${a.account_suffix}` : ''}
                </td>
                <td className="right num nowrap"
                  style={{ color: a.direction === 'credit' ? 'var(--pos)' : undefined }}>
                  {a.amount ? `${a.direction === 'credit' ? '+' : '−'}${money(a.amount)}` : '—'}
                </td>
                <td className="dim">{a.reason || a.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {alerts.length > 6 && (
        <Button size="xs" style={{ marginTop: 6 }} onClick={() => setOpen((v) => !v)}>
          {open ? 'Show fewer' : `Show ${Math.min(alerts.length, 40)} of ${alerts.length}`}
        </Button>
      )}
    </div>
  );
}

function ChooseSection({ source, result, rows, staged, selected, onToggle, onToggleMany }) {
  const [open, setOpen] = useState(false);
  const mine = rows.filter((r) => selected.has(rowKey(r))).length;
  const all = rows.length > 0 && mine === rows.length;

  const refusedAlerts = useMemo(() => {
    const list = (result?.alerts || []).filter((a) => a.status !== 'imported');
    /* Refusals worth reading first: an alert refused for naming a card you
       hold no statements for is a real payment missing from the ledger; one
       refused as "not a completed transaction" is a marketing email. Unsorted,
       the marketing came first and the money was below the fold. */
    const weight = (a) => {
      if (a.amount && a.date_iso && a.account_suffix) return 0;
      if (a.amount && a.date_iso) return 1;
      if (a.amount) return 2;
      return 3;
    };
    return [...list].sort((a, b) => weight(a) - weight(b)
      || String(b.date_iso || '').localeCompare(String(a.date_iso || '')));
  }, [result]);

  const excluded = result?.excluded || [];
  const refusedCount = refusedAlerts.length + excluded.length;

  return (
    <Group>
      <div className="row">
        <IconButton icon={open ? 'chevron-down' : 'chevron'} size="xs" className="ghost"
          label={open ? 'Collapse' : 'Expand'} onClick={() => setOpen((v) => !v)} />
        <strong style={{ fontSize: 13 }}>{source.label}</strong>
        {rows.length > 0 && (
          <Chip tone={mine ? 'acc' : ''}>{mine} of {rows.length} chosen</Chip>
        )}
        {staged > 0 && <Chip tone="pos">{staged} staged</Chip>}
        {refusedCount > 0 && <Chip tone="warn">{refusedCount} not used</Chip>}
        {!result && <Chip>not scanned</Chip>}
        <span className="spacer" />
        {rows.length > 0 && (
          <Button size="sm" onClick={() => onToggleMany(rows, !all)}>
            {all ? 'Clear' : 'Select all'}
          </Button>
        )}
      </div>

      {open && (
        <>
          {!result && (
            <div className="small muted" style={{ marginTop: 6 }}>
              Not scanned in this session — anything staged earlier is still on Review.
            </div>
          )}
          {result && !rows.length && (
            <div className="small muted" style={{ marginTop: 6 }}>
              That scan found nothing to choose from.
            </div>
          )}
          {rows.length > 0 && (
            <FileRows rows={rows} selected={selected} onToggle={onToggle} />
          )}
          <RefusedAlerts alerts={refusedAlerts} />
          {excluded.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div className="tiny" style={{ fontWeight: 620, marginBottom: 4 }}>
                Emails not used ({excluded.length})
              </div>
              {[...excluded.reduce((m, e) => {
                const k = e.reason || 'no reason given';
                m.set(k, (m.get(k) || 0) + 1);
                return m;
              }, new Map()).entries()]
                .sort((a, b) => b[1] - a[1])
                .map(([why, n]) => (
                  <div className="tiny dim" key={why}><strong>{n}</strong> — {why}</div>
                ))}
            </div>
          )}
        </>
      )}
    </Group>
  );
}

export function ChooseStep({
  intents, chosen, sections, sourceResults, rows, selected, onToggle, onToggleMany,
  ignoredSenders, ignoredCount, onIgnore, onClearIgnored,
}) {
  const [search, setSearch] = useState('');
  const [hidden, setHidden] = useState(() => new Set());

  const all = rows || [];
  const needle = search.trim().toLowerCase();
  /* Filtering hides rows; it never unticks them. A row already chosen and then
     filtered out of view stays chosen - the alternative is a filter that
     silently changes what is about to be imported. */
  const visible = all.filter((r) => {
    if (hidden.has(r.category || 'unknown')) return false;
    if (!needle) return true;
    return `${r.filename} ${r.sender} ${r.subject}`.toLowerCase().includes(needle);
  });
  const hiddenCount = all.length - visible.length;

  const byIntent = visible.reduce((acc, r) => {
    const k = r.intent || 'statement';
    (acc[k] = acc[k] || []).push(r);
    return acc;
  }, {});
  const staged = Object.fromEntries((sections || []).map((s) => [s.key, s]));
  const picked = intents.filter((one) => chosen.has(one.key));

  const types = Object.entries(all.reduce((acc, r) => {
    const c = r.category || 'unknown';
    acc[c] = (acc[c] || 0) + 1;
    return acc;
  }, {})).sort((a, b) => b[1] - a[1]);

  if (!picked.length) {
    return (
      <Empty title="Nothing to choose from" icon="search">
        No sources are ticked. Go back to Source and pick at least one.
      </Empty>
    );
  }

  return (
    <>
      <p className="lead" style={{ margin: 0 }}>
        What each scan found. Open a section to see the files and what was refused.
        Tick what to download and read — nothing is read, and nothing counts, until you
        say so.
      </p>

      <Group>
        <div className="row">
          <input type="search" placeholder="Search filename, sender or subject…"
            value={search} onChange={(e) => setSearch(e.target.value)}
            style={{ flex: 1, minWidth: 200 }} />
          <PromptButton
            size="sm"
            title="Never offer this sender again, in this or any future scan"
            placeholder="Sender to ignore, e.g. bajajfinserv"
            submitLabel="Ignore"
            onSubmit={onIgnore}
          >
            Ignore a sender
          </PromptButton>
        </div>

        {types.length > 1 && (
          <div className="row tight" style={{ marginTop: 9 }}>
            <span className="tiny dim">Type:</span>
            {types.map(([cat, n]) => {
              const off = hidden.has(cat);
              return (
                <button
                  key={cat}
                  className={`chip ${off ? '' : CATEGORY_TONE[cat] || 'acc'}`}
                  style={{ cursor: 'pointer', opacity: off ? 0.45 : 1, border: 0 }}
                  title={off ? `Show ${cat} again` : `Hide every ${cat} document`}
                  onClick={() => setHidden((p) => {
                    const next = new Set(p);
                    if (next.has(cat)) next.delete(cat); else next.add(cat);
                    return next;
                  })}
                >
                  {off ? '✕ ' : '✓ '}{cat} ({n})
                </button>
              );
            })}
          </div>
        )}

        {ignoredSenders?.length > 0 && (
          <div className="row tight" style={{ marginTop: 9 }}>
            <span className="tiny dim">
              Never offered{ignoredCount ? ` · ${ignoredCount} skipped this scan` : ''}:
            </span>
            {ignoredSenders.map((f) => <Chip key={f} tone="warn">{f}</Chip>)}
            <Button size="xs" onClick={onClearIgnored}>Clear</Button>
          </div>
        )}
      </Group>

      {hiddenCount > 0 && (
        <Callout tone="warn">
          {hiddenCount} document{hiddenCount === 1 ? '' : 's'} hidden by the filters
          above. Hiding does not untick anything — what you have already chosen is
          still chosen.
        </Callout>
      )}

      {picked.map((one) => (
        <ChooseSection
          key={one.key}
          source={one}
          result={sourceResults?.[one.key]}
          rows={byIntent[one.key] || []}
          staged={staged[one.key]?.staged || 0}
          selected={selected}
          onToggle={onToggle}
          onToggleMany={onToggleMany}
        />
      ))}

      <Group>
        <div className="row">
          <strong style={{ fontSize: 13 }}>Files from this computer</strong>
          {staged.upload?.staged > 0
            ? <Chip tone="pos">{staged.upload.staged} staged</Chip>
            : <Chip>none added</Chip>}
        </div>
        <div className="small muted" style={{ marginTop: 6 }}>
          Uploaded files skip this step — they are already here, so there is nothing to
          download. They are read on <strong>Read</strong> and judged on{' '}
          <strong>Review</strong> with everything else.
        </div>
      </Group>

      <Callout tone="acc">
        Alerts carry their amount in the email body, so there is no file to fetch: the
        ones that were understood go straight to staging when their scan runs, and
        appear on Review.
      </Callout>
    </>
  );
}

/* ═══════════════════════════════════════════════════════ 4. read ════════ */

/* Reading is incremental by content hash - a file already read is never read
   again - so a section whose documents are all read has nothing to do and says
   so, rather than offering a button that would do nothing. */
export function ReadStep({
  intents, chosen, sections, chosenCounts, onParse, onRefresh, busy, job,
}) {
  const [running, setRunning] = useState(null);

  const known = Object.fromEntries((sections || []).map((x) => [x.key, x]));
  const order = [
    ...(intents || []).filter((one) => chosen?.has(one.key)),
    { key: 'upload', label: 'Files from this computer' },
  ];
  const rows = order.map((one) => ({
    key: one.key,
    label: known[one.key]?.label || one.label,
    staged: 0, parsed: 0, pending: 0, failed: 0, rows: 0,
    ...(known[one.key] || {}),
    chosen: chosenCounts?.[one.key] || 0,
  }));
  const anyStaged = rows.some((s) => s.staged > 0);
  const anyPending = rows.some((s) => s.pending > 0 || s.failed > 0);

  const run = async (key) => {
    setRunning(key);
    const id = await onParse(key);
    if (!id) { setRunning(null); onRefresh?.(); }
  };

  React.useEffect(() => {
    if (running && job && !job.active) { setRunning(null); onRefresh?.(); }
  }, [job, running, onRefresh]);

  return (
    <>
      <p className="lead" style={{ margin: 0 }}>
        Reading turns each document into rows held in staging.{' '}
        <strong>None of it reaches your ledger here</strong> — that happens on the last
        step, and only for what you tick on Review. A document already read is never
        read twice.
      </p>

      {!anyStaged && (
        <Callout tone="warn">
          Nothing is staged yet. Scan a source or add files on <strong>Source</strong>{' '}
          first.
        </Callout>
      )}

      {rows.map((s) => {
        const outstanding = s.pending + s.failed;
        return (
          <Group key={s.key}>
            <div className="row">
              <strong style={{ fontSize: 13 }}>{s.label}</strong>
              <Chip>{s.staged} staged</Chip>
              {s.parsed > 0 && <Chip tone="pos">{s.parsed} read</Chip>}
              {s.pending > 0 && <Chip tone="warn">{s.pending} unread</Chip>}
              {s.failed > 0 && <Chip tone="neg">{s.failed} could not be read</Chip>}
              <span className="spacer" />
              <Button size="sm" disabled={busy || Boolean(running) || !outstanding}
                onClick={() => run(s.key)}>
                {running === s.key ? 'Reading…'
                  : outstanding ? `Read ${outstanding}`
                    : s.staged ? 'All read' : 'Nothing to read'}
              </Button>
            </div>

            {running === s.key && job?.active && (
              <div style={{ marginTop: 8 }}><JobProgress job={job} trace /></div>
            )}

            {s.staged === 0 && (
              <div className="small muted" style={{ marginTop: 6 }}>
                {s.chosen > 0 ? (
                  <>
                    <strong>{s.chosen} chosen but not fetched yet.</strong> Go back to{' '}
                    <strong>Choose</strong> and press <strong>Download &amp; read</strong> —
                    choosing marks what you want; that button goes and gets it.
                  </>
                ) : (
                  <>
                    Nothing staged from this source yet — scan it on{' '}
                    <strong>Scanning</strong>, then pick its files on{' '}
                    <strong>Choose</strong>.
                  </>
                )}
              </div>
            )}

            {s.rows > 0 && (
              <div className="small muted" style={{ marginTop: 6 }}>
                {count(s.rows)} rows read, waiting on Review.
              </div>
            )}
            {s.failed > 0 && (
              <div className="small muted" style={{ marginTop: 4 }}>
                Usually a password-protected PDF. Add the password under Your details
                and read again — nothing already read is read twice.
              </div>
            )}
          </Group>
        );
      })}

      {anyStaged && !anyPending && (
        <Callout tone="pos">
          Everything staged has been read. What it produced is on <strong>Review</strong>,
          and still counts for nothing until you build the ledger.
        </Callout>
      )}
    </>
  );
}
