import React, { useEffect, useState } from 'react';
import { api } from '../../lib';
import JobProgress from '../JobProgress';
import { Callout, Chip } from '../ui';

/* Step 4: read the staged documents, one source at a time.
 *
 * Reading is incremental by content hash - a file already read is never read
 * again - so a section whose documents are all read has nothing to do, and
 * says so rather than offering a button that would do nothing.
 *
 * Nothing here reaches the ledger. That is the next step but one.
 */

export default function ParseSections({
  intents, chosen, sections, chosenCounts, onParse, onRefresh, busy,
}) {
  const [jobId, setJobId] = useState(null);
  const [job, setJob] = useState(null);
  const [running, setRunning] = useState(null);

  useEffect(() => {
    if (!jobId) return undefined;
    let live = true;
    let timer = null;
    const poll = async () => {
      const current = await api.job(jobId).catch(() => null);
      if (!live) return;
      setJob(current);
      if (current?.active) {
        timer = setTimeout(poll, 900);
      } else {
        setRunning(null);
        onRefresh?.();
      }
    };
    poll();
    return () => { live = false; clearTimeout(timer); };
  }, [jobId, onRefresh]);

  const run = async (key) => {
    setRunning(key);
    const id = await onParse(key);
    if (id) setJobId(id);
    else { setRunning(null); onRefresh?.(); }
  };

  /* One section per source, whatever it holds - the same set Scanning and
     Choose show, plus uploads.
   *
     This used to list only sources with something staged, so Investments and
     Transaction alerts simply were not on the screen when they had nothing.
     A step that silently drops a section reads as broken: the question it
     leaves you with is "where did it go", and the answer - "it has nothing
     yet" - is one line that the section can perfectly well say itself. */
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

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <p style={{ color: 'var(--text-2)', fontSize: 13, margin: 0 }}>
        Reading turns each document into rows held in staging.{' '}
        <strong>None of it reaches your ledger here</strong> — that happens on
        the last step, and only for what you tick on Review. A document already
        read is never read twice.
      </p>

      {!anyStaged && (
        <Callout tone="warn">
          Nothing is staged yet. Scan a source or add files on{' '}
          <strong>Source</strong> first.
        </Callout>
      )}

      {rows.map((section) => {
        const outstanding = section.pending + section.failed;
        const isRunning = running === section.key;
        return (
          <div key={section.key} className="file-group" style={{ padding: '10px 12px' }}>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <strong style={{ fontSize: 13 }}>{section.label}</strong>
              <Chip>{section.staged} staged</Chip>
              {section.parsed > 0 && <Chip tone="pos">{section.parsed} read</Chip>}
              {section.pending > 0 && <Chip tone="warn">{section.pending} unread</Chip>}
              {section.failed > 0 && <Chip tone="neg">{section.failed} could not be read</Chip>}
              <div style={{ flex: 1 }} />
              <button className="btn" disabled={busy || Boolean(running) || !outstanding}
                style={{ padding: '3px 10px', fontSize: 12 }}
                onClick={() => run(section.key)}>
                {isRunning ? 'Reading…'
                  : outstanding ? `Read ${outstanding}`
                    : section.staged ? 'All read' : 'Nothing to read'}
              </button>
            </div>

            {isRunning && job?.active && (
              <div style={{ marginTop: 8 }}>
                <JobProgress job={job} title="" />
              </div>
            )}

            {section.staged === 0 && (
              <div className="xp-hint" style={{ textTransform: 'none', marginTop: 6 }}>
                {section.chosen > 0
                  /* Ticking on Choose selects; it does not fetch. Without
                     saying so, a source with 108 files chosen and 0 staged
                     looks like the wizard lost them. */
                  ? <>
                      <strong>{section.chosen} chosen but not fetched yet.</strong>{' '}
                      Go back to <strong>Choose</strong> and press{' '}
                      <strong>Download &amp; read</strong> — choosing marks
                      what you want; that button goes and gets it.
                    </>
                  : <>
                      Nothing staged from this source yet — scan it on{' '}
                      <strong>Scanning</strong>, then pick its files on{' '}
                      <strong>Choose</strong>.
                    </>}
              </div>
            )}

            {section.rows > 0 && (
              <div className="xp-hint" style={{ textTransform: 'none', marginTop: 6 }}>
                {section.rows.toLocaleString('en-IN')} rows read, waiting on Review.
              </div>
            )}
            {section.failed > 0 && (
              <div className="xp-hint" style={{ textTransform: 'none', marginTop: 4 }}>
                Usually a password-protected PDF. Add the password under
                Profile and read again — nothing already read is read twice.
              </div>
            )}
          </div>
        );
      })}

      {anyStaged && !anyPending && (
        <Callout tone="pos">
          Everything staged has been read. What it produced is on{' '}
          <strong>Review</strong>, and still counts for nothing until you
          process it.
        </Callout>
      )}
    </div>
  );
}
