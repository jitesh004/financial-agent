import React, { useEffect, useState } from 'react';
import { api } from '../../lib';
import JobProgress from '../JobProgress';
import { Callout, Chip } from '../ui';

/* Step 2: one scan per source, run in turn, each with its own Retry.
 *
 * Sequential rather than parallel, which is the honest way round: Gmail rate
 * limits, and four scans racing produce four progress bars that all crawl.
 * More importantly each source keeps its OWN job, so re-scanning alerts in
 * November does not disturb the statement scan you ran in August - the reason
 * to want a per-section Retry at all.
 */

function Section({ source, jobId, onScan, running }) {
  const [job, setJob] = useState(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
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

  /* What the scan read and did NOT use, and why. A scan that reports "40
     found" out of 425 read has made 385 decisions the user cannot see, and
     "no account here ends 4345" is a fact about their ledger worth knowing -
     not a silent skip. */
  const refused = [
    ...(result?.excluded || []).map((e) => e.reason),
    ...(result?.alerts || [])
      .filter((a) => a.status !== 'imported')
      .map((a) => a.reason || a.status),
  ];
  const ignored = result?.ignored_by_rule || 0;
  const reasons = refused.reduce((acc, why) => {
    acc.set(why || 'no reason given', (acc.get(why || 'no reason given') || 0) + 1);
    return acc;
  }, new Map());

  return (
    <div className="file-group" style={{ padding: '10px 12px' }}>
      <div style={{
        display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
      }}>
        <button className="btn" onClick={() => setOpen((v) => !v)}
          style={{ padding: '1px 7px', fontSize: 11, lineHeight: 1.6 }}
          aria-label={open ? 'Collapse' : 'Expand'}>
          {open ? '▾' : '▸'}
        </button>
        <strong style={{ fontSize: 13 }}>{source.label}</strong>
        {done && found != null && <Chip tone="pos">{found} found</Chip>}
        {source.staged > 0 && <Chip>{source.staged} staged</Chip>}
        {done && refused.length > 0 && (
          <Chip tone="warn">{refused.length} not used</Chip>
        )}
        {job?.active && (
          <Chip tone="accent">
            <span className="spinner" style={{ width: 9, height: 9, marginRight: 5 }} />
            scanning
          </Chip>
        )}
        <div style={{ flex: 1 }} />
        <button className="btn" disabled={running}
          style={{ padding: '3px 10px', fontSize: 12 }}
          onClick={() => onScan(source.key)}>
          {job ? 'Re-scan' : 'Scan'}
        </button>
      </div>

      {job?.active && (
        <div style={{ marginTop: 8 }}>
          <JobProgress job={job} title="" />
        </div>
      )}

      {done && (
        <div className="xp-hint" style={{ textTransform: 'none', marginTop: 6 }}>
          {job.message}
        </div>
      )}

      {job && job.status === 'failed' && (
        <Callout tone="neg" style={{ marginTop: 8 }}>
          {job.errors?.join('; ') || 'That scan failed.'}
        </Callout>
      )}

      {open && (
        <>
          {!job && (
            <div className="xp-hint" style={{ textTransform: 'none', marginTop: 6 }}>
              Not scanned yet in this session.
            </div>
          )}
          {done && (refused.length > 0 || ignored > 0) && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 4 }}>
                Not used ({refused.length}
                {ignored ? `, plus ${ignored} from senders you ignore` : ''})
              </div>
              {[...reasons.entries()].sort((a, b) => b[1] - a[1]).map(([why, n]) => (
                <div key={why} className="xp-hint"
                  style={{ textTransform: 'none', padding: '2px 0' }}>
                  <strong>{n}</strong> — {why}
                </div>
              ))}
              <div className="xp-hint" style={{ textTransform: 'none', marginTop: 4 }}>
                The rows behind these are on <strong>Choose</strong>.
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function ScanSections({
  intents, chosen, sections, sourceJobs, onScan, busy, pendingUploads,
}) {
  const staged = Object.fromEntries((sections || []).map((s) => [s.key, s]));
  const picked = intents.filter((one) => chosen.has(one.key));
  const [queue, setQueue] = useState(false);

  /* Scan every ticked source, one after another. */
  const scanAll = async () => {
    setQueue(true);
    try {
      for (const one of picked) {
        // eslint-disable-next-line no-await-in-loop
        const id = await onScan(one.key);
        if (!id) continue;
        // eslint-disable-next-line no-await-in-loop
        await new Promise((resolve) => {
          const wait = async () => {
            const current = await api.job(id).catch(() => null);
            if (!current || !current.active) resolve();
            else setTimeout(wait, 900);
          };
          wait();
        });
      }
    } finally { setQueue(false); }
  };

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <p style={{ color: 'var(--text-2)', fontSize: 13, margin: 0, flex: 1 }}>
          Each source is scanned on its own, one after another. Re-scan any one
          of them without touching the others — this is where you come back for
          new alerts.
        </p>
        <button className="btn primary" disabled={queue || busy || !picked.length}
          onClick={scanAll}>
          {queue ? 'Scanning…' : `Scan ${picked.length} source${picked.length === 1 ? '' : 's'}`}
        </button>
      </div>

      {!picked.length && (
        <Callout tone="warn">
          No sources are ticked. Go back to <strong>Source</strong> and pick at
          least one.
        </Callout>
      )}

      {picked.map((one) => (
        <Section key={one.key}
          source={{ ...one, staged: staged[one.key]?.staged || 0 }}
          jobId={sourceJobs?.[one.key]}
          running={queue}
          onScan={onScan} />
      ))}

      {/* Files from this computer are a source like any other, so they are
          read here rather than behind their own button on the previous step -
          which was the one place in the wizard where an action ran the moment
          you picked something. */}
      <div className="file-group" style={{ padding: '10px 12px' }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <strong style={{ fontSize: 13 }}>Files from this computer</strong>
          {pendingUploads?.count > 0 && (
            <Chip tone="warn">{pendingUploads.count} waiting</Chip>
          )}
          {staged.upload?.staged > 0 && (
            <Chip tone="pos">{staged.upload.staged} staged</Chip>
          )}
          <div style={{ flex: 1 }} />
          <button className="btn" disabled={queue || busy || !pendingUploads?.count}
            style={{ padding: '3px 10px', fontSize: 12 }}
            onClick={() => pendingUploads.submit?.()}>
            {pendingUploads?.count
              ? `Read ${pendingUploads.count} file${pendingUploads.count === 1 ? '' : 's'}`
              : 'Nothing waiting'}
          </button>
        </div>
        <div className="xp-hint" style={{ textTransform: 'none', marginTop: 6 }}>
          {pendingUploads?.count
            ? 'Added on Source and not read yet.'
            : 'Add files on the Source step; they are read from here.'}
        </div>
      </div>
    </div>
  );
}
