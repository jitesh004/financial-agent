/* Live progress for a running job.
 *
 * The bar is driven by real completed-item counts from the server, never by a
 * timer - a bar that advances on its own while work is stuck is worse than no
 * bar at all. The per-file trace below it is what makes a 200-file run
 * debuggable rather than a black box.
 */

import React, { useEffect, useRef } from 'react';
import { duration } from '../core/format';
import { Button, Chip, ProgressBar, Spinner } from './index';

const TONE = { done: 'pos', skipped: 'warn', failed: 'neg', active: 'acc', pending: '' };

export default function JobProgress({ job, title, onCancel, trace = true }) {
  const traceRef = useRef(null);

  // Follow the tail as items stream in, so the newest file is always visible.
  useEffect(() => {
    const el = traceRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [job?.item_count]);

  if (!job) return null;

  const counts = job.counts || {};
  const running = job.status === 'running' || job.status === 'queued';

  return (
    <div className="col" style={{ gap: 8 }}>
      <div className="row">
        {running && <Spinner sm />}
        <strong style={{ fontSize: 13.5 }}>{title || job.phase}</strong>
        <span className="spacer" />
        <span className="tiny dim num">{job.total > 0 ? `${job.current} / ${job.total}` : ''}</span>
      </div>

      <ProgressBar
        value={job.percent}
        tall
        tone={job.status === 'failed' ? 'neg' : job.status === 'complete' ? 'pos' : ''}
      />

      <div className="row tight tiny dim">
        <span className="num">{job.percent}%</span>
        <span>{job.phase}</span>
        <span className="num">elapsed {duration(job.elapsed)}</span>
        {job.eta_seconds != null && <span className="num">~{duration(job.eta_seconds)} left</span>}
        {Object.entries(counts).map(([status, n]) => (
          <Chip key={status} tone={TONE[status]}>{n} {status}</Chip>
        ))}
        {running && onCancel && (
          <>
            <span className="spacer" />
            <Button size="xs" onClick={onCancel}>Cancel</Button>
          </>
        )}
      </div>

      {job.message && <div className="small muted">{job.message}</div>}

      {trace && job.items?.length > 0 && (
        <div className="trace" ref={traceRef}>
          {job.items.map((item, i) => (
            <div className="trace-row" key={i}>
              <span className="dot" style={{
                background: item.status === 'done' ? 'var(--pos)'
                  : item.status === 'failed' ? 'var(--neg)'
                    : item.status === 'skipped' ? 'var(--warn)' : 'var(--text-3)',
              }} />
              <span className="trace-name">{item.name}</span>
              {item.cached && <Chip>cached</Chip>}
              <span className="trace-detail">{item.detail}</span>
            </div>
          ))}
        </div>
      )}

      {job.warnings?.length > 0 && (
        <details className="tiny dim">
          <summary style={{ cursor: 'pointer' }}>
            {job.warnings.length} warning{job.warnings.length === 1 ? '' : 's'}
          </summary>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            {job.warnings.map((w, i) => <li key={i} style={{ listStyle: 'disc' }}>{w}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}
