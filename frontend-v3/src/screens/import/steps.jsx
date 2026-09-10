import React, { useMemo, useState } from 'react';
import { api } from '../../core/api';
import { bytes, count, money } from '../../core/format';
import {
  Button, Callout, Chip, ConfirmButton, Empty, IconButton, PromptButton, Select, Badge,
} from '../../ui';
import JobProgress from '../../ui/JobProgress';
import Uploader from './Uploader';
import { rowKey } from './useImport';

const CAPS = [250, 500, 1000, 2500, 5000];

export const CATEGORY_TONE = {
  bank: 'brand', card: 'pos', loan: 'warn', bureau: 'neg', broker: 'brand', unknown: '',
};

function Group({ children, className = '' }) {
  return (
    <div
      className={className}
      style={{
        padding: '12px 16px',
        borderRadius: 'var(--radius-md)',
        background: 'var(--surface-2)',
        border: '1px solid var(--border-subtle)',
      }}
    >
      {children}
    </div>
  );
}

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

  const uploader = (
    <Group>
      <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 2 }}>
        Files from this computer
      </div>
      <div className="small muted" style={{ marginBottom: 8 }}>
        {mailboxReady
          ? 'Anything Gmail does not carry. They are read on the next step with every other source.'
          : 'Bank, card, loan and investment statements you already have. They are read on the next step.'}
        {staged.upload?.staged > 0 && ` ${staged.upload.staged} staged so far.`}
      </div>
      <Uploader compact onComplete={onUploaded} />
    </Group>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <p className="lead" style={{ margin: 0 }}>
        {mailboxReady
          ? 'Select financial sources to scan and configure lookback depths. Each source is scanned in isolated stages.'
          : 'Add the statements you have. Everything works identically whether documents arrive via Gmail or local upload.'}
      </p>

      {!mailboxReady && uploader}

      {intents.map((one) => {
        const on = chosen.has(one.key) && mailboxReady;
        const s = settingsFor(one.key);
        const c = staged[one.key];
        return (
          <Group key={one.key}>
            <label
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 'var(--space-2)',
                cursor: mailboxReady ? 'pointer' : 'default',
                opacity: mailboxReady ? 1 : 0.55,
              }}
            >
              <input
                type="checkbox"
                checked={on}
                disabled={!mailboxReady}
                onChange={() => onToggle(one.key)}
                style={{ marginTop: 3, accentColor: 'var(--brand-primary)' }}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontWeight: 600, fontSize: 13 }}>
                  {one.label}
                  {c?.staged > 0 && (
                    <Badge tone="pos" size="sm" style={{ marginLeft: 8 }}>
                      {c.staged} staged
                    </Badge>
                  )}
                </span>
                <span className="tiny muted">{one.description}</span>
              </div>
            </label>

            {on && (
              <div
                style={{
                  display: 'flex',
                  gap: 'var(--space-3)',
                  alignItems: 'center',
                  marginTop: 10,
                  paddingTop: 10,
                  borderTop: '1px solid var(--border-subtle)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span className="tiny muted">Look back:</span>
                  <Select
                    value={s.months}
                    onChange={(v) => onSetting(one.key, { months: v === '' ? null : Number(v) })}
                    options={periodsFor(periods, s.months).map((p) => [p.months ?? '', p.label])}
                    style={{ fontSize: 11, height: 26 }}
                  />
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span className="tiny muted">Max emails:</span>
                  <Select
                    value={s.maxMessages}
                    onChange={(v) => onSetting(one.key, { maxMessages: Number(v) })}
                    options={CAPS.map((cap) => [cap, `${cap} emails`])}
                    style={{ fontSize: 11, height: 26 }}
                  />
                </div>
              </div>
            )}
          </Group>
        );
      })}

      {mailboxReady && uploader}
    </div>
  );
}

function ScanSection({ source, jobId, running, onScan, onForget }) {
  const [open, setOpen] = useState(false);
  const { data: job } = useQuery(
    jobId ? `scan-section:${jobId}` : null,
    () => api.job(jobId),
    { enabled: Boolean(jobId), refetchInterval: 1200 }
  );

  const done = job && !job.active && job.status === 'complete';
  const found = done ? job.result?.attachments?.length ?? job.result?.alerts?.length : null;

  return (
    <Group>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--space-2)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <strong style={{ fontSize: 13 }}>{source.label}</strong>
          {done && found != null && <Badge tone="pos" size="sm">{found} found</Badge>}
          {source.staged > 0 && <Badge size="sm">{source.staged} staged</Badge>}
          {job?.active && <Badge tone="brand" size="sm">Scanning…</Badge>}
        </div>

        <div style={{ display: 'flex', gap: 6 }}>
          {source.staged > 0 && (
            <ConfirmButton
              size="sm"
              disabled={running}
              question={`Forget ${source.staged} staged files?`}
              confirmLabel="Forget"
              onConfirm={() => onForget(source.key)}
            >
              Forget
            </ConfirmButton>
          )}
          <Button size="sm" disabled={running} onClick={() => onScan(source.key)}>
            {job ? 'Re-scan' : 'Scan'}
          </Button>
        </div>
      </div>

      {job?.active && <div style={{ marginTop: 8 }}><JobProgress job={job} trace={false} /></div>}
      {done && <div className="tiny muted" style={{ marginTop: 6 }}>{job.message}</div>}
      {job?.status === 'failed' && (
        <Callout tone="neg" style={{ marginTop: 8 }}>
          {job.errors?.join('; ') || 'Scan failed.'}
        </Callout>
      )}
    </Group>
  );
}

export function ScanStep({ intents, chosen, sections, sourceJobs, busy, onScan, onForget }) {
  const known = Object.fromEntries((sections || []).map((s) => [s.key, s]));
  const picked = (intents || []).filter((one) => chosen.has(one.key));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <p className="lead" style={{ margin: 0 }}>
        Scanning checks your mailbox and catalogs matching statement attachments. Nothing is downloaded yet.
      </p>
      {!picked.length && (
        <Callout tone="warn">
          No sources selected. Go back to <strong>Source</strong> and pick at least one.
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
    </div>
  );
}

function FileRows({ rows, selected, onToggle }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, 10);

  return (
    <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
      {shown.map((row) => {
        const key = rowKey(row);
        const on = selected.has(key);
        return (
          <label
            key={key}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '6px 8px',
              borderRadius: 'var(--radius-sm)',
              background: on ? 'var(--surface-3)' : 'transparent',
              cursor: 'pointer',
            }}
          >
            <input
              type="checkbox"
              checked={on}
              onChange={() => onToggle(row)}
              style={{ accentColor: 'var(--brand-primary)' }}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="small font-medium truncate">{row.filename}</div>
              <div className="tiny muted">
                {row.institution || row.sender_name}
                {row.date_iso ? ` · ${row.date_iso.slice(0, 10)}` : ''}
                {row.cached ? ' · Cached' : ''}
              </div>
            </div>
            <span className="tiny num muted nowrap">{row.size ? bytes(row.size) : ''}</span>
          </label>
        );
      })}
      {rows.length > 10 && (
        <Button size="xs" onClick={() => setAll((v) => !v)}>
          {all ? 'Show Fewer' : `Show All ${rows.length} Attachments`}
        </Button>
      )}
    </div>
  );
}

export function ChooseStep({
  intents, chosen, sections, sourceResults, rows, selected, onToggle, onToggleMany,
}) {
  const all = rows || [];
  const staged = Object.fromEntries((sections || []).map((s) => [s.key, s]));
  const picked = intents.filter((one) => chosen.has(one.key));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <p className="lead" style={{ margin: 0 }}>
        Review attachments discovered by the scan. Check what to download and read into staging.
      </p>

      {picked.map((one) => {
        const sourceRows = all.filter((r) => (r.intent || 'statement') === one.key);
        const mine = sourceRows.filter((r) => selected.has(rowKey(r))).length;
        const allSelected = sourceRows.length > 0 && mine === sourceRows.length;

        return (
          <Group key={one.key}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <strong style={{ fontSize: 13 }}>{one.label}</strong>
                {sourceRows.length > 0 && (
                  <Badge tone={mine ? 'brand' : undefined} size="sm">
                    {mine} of {sourceRows.length} chosen
                  </Badge>
                )}
                {staged[one.key]?.staged > 0 && (
                  <Badge tone="pos" size="sm">{staged[one.key].staged} staged</Badge>
                )}
              </div>

              {sourceRows.length > 0 && (
                <Button size="sm" onClick={() => onToggleMany(sourceRows, !allSelected)}>
                  {allSelected ? 'Clear' : 'Select All'}
                </Button>
              )}
            </div>

            {sourceRows.length > 0 && (
              <FileRows rows={sourceRows} selected={selected} onToggle={onToggle} />
            )}
          </Group>
        );
      })}
    </div>
  );
}

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

  const run = async (key) => {
    setRunning(key);
    const id = await onParse(key);
    if (!id) { setRunning(null); onRefresh?.(); }
  };

  React.useEffect(() => {
    if (running && job && !job.active) { setRunning(null); onRefresh?.(); }
  }, [job, running, onRefresh]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <p className="lead" style={{ margin: 0 }}>
        Parsing converts raw PDFs and spreadsheets into staged transaction records.
        <strong> Zero changes to your active ledger</strong> until verified on the next step.
      </p>

      {rows.map((s) => {
        const outstanding = s.pending + s.failed;
        return (
          <Group key={s.key}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <strong style={{ fontSize: 13 }}>{s.label}</strong>
                <Badge size="sm">{s.staged} staged</Badge>
                {s.parsed > 0 && <Badge tone="pos" size="sm">{s.parsed} parsed</Badge>}
                {s.failed > 0 && <Badge tone="neg" size="sm">{s.failed} locked/failed</Badge>}
              </div>

              <Button
                size="sm"
                disabled={busy || Boolean(running) || !outstanding}
                onClick={() => run(s.key)}
              >
                {running === s.key ? 'Parsing…' : outstanding ? `Read ${outstanding}` : 'All Read'}
              </Button>
            </div>

            {running === s.key && job?.active && (
              <div style={{ marginTop: 8 }}><JobProgress job={job} trace /></div>
            )}
          </Group>
        );
      })}
    </div>
  );
}
