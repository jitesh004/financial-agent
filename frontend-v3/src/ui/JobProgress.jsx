/* ────────────────────────────────────────────────────────────────────────────
   Live Progress & Execution Step Tracker for Background Jobs
   ──────────────────────────────────────────────────────────────────────── */

import React, { useEffect, useRef } from 'react';
import { duration } from '../core/format';
import { Button, Chip, ProgressBar, Spinner } from './index';

const TONE = { done: 'pos', skipped: 'warn', failed: 'neg', active: 'acc', pending: '' };

export default function JobProgress({ job, title, onCancel, trace = true,
                                      traceHeight = 180 }) {
  const traceRef = useRef(null);

  useEffect(() => {
    const el = traceRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [job?.item_count, job?.items?.length]);

  if (!job) return null;

  const counts = job.counts || {};
  const running = job.status === 'running' || job.status === 'queued';

  return (
    <div className="flex-col gap-2 w-full">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {running && <Spinner sm />}
          <strong style={{ fontSize: 13.5 }}>{title || job.phase || 'Processing'}</strong>
        </div>
        <span className="mono tabular-nums text-3 text-xs">
          {job.total > 0 ? `${job.current || 0} / ${job.total}` : ''}
        </span>
      </div>

      <ProgressBar
        value={job.percent || 0}
        tall
        tone={job.status === 'failed' ? 'neg' : job.status === 'complete' ? 'pos' : 'accent'}
      />

      <div className="flex items-center justify-between text-xs text-3">
        <div className="flex items-center gap-2">
          <span className="tabular-nums font-semibold">{job.percent || 0}%</span>
          <span>{job.phase}</span>
          <span className="tabular-nums">elapsed {duration(job.elapsed)}</span>
          {job.eta_seconds != null && (
            <span className="tabular-nums">~{duration(job.eta_seconds)} left</span>
          )}
        </div>
        {running && onCancel && (
          <Button size="xs" variant="ghost" onClick={onCancel}>Cancel</Button>
        )}
      </div>

      {Object.keys(counts).length > 0 && (
        <div className="flex items-center gap-2">
          {Object.entries(counts).map(([status, n]) => (
            <Chip key={status} tone={TONE[status]}>{n} {status}</Chip>
          ))}
        </div>
      )}

      {job.message && <div className="text-sm text-2">{job.message}</div>}

      {trace && job.items?.length > 0 && (
        <div className="trace-box" ref={traceRef}
          style={{ maxHeight: traceHeight, overflowY: 'auto' }}>
          {job.items.map((item, i) => (
            <div className="trace-row flex items-center gap-2 py-1 text-xs" key={i}>
              <span className="beacon-live" style={{
                background: item.status === 'done' ? 'var(--pos)'
                  : item.status === 'failed' ? 'var(--neg)'
                    : item.status === 'skipped' ? 'var(--warn)' : 'var(--text-3)',
              }} />
              <span className="font-semibold text-1">{item.name}</span>
              {item.cached && <Chip>cached</Chip>}
              <span className="text-3 truncate">{item.detail}</span>
            </div>
          ))}
        </div>
      )}

      {job.warnings?.length > 0 && (
        <details className="text-xs text-warn">
          <summary style={{ cursor: 'pointer' }}>
            {job.warnings.length} warning{job.warnings.length === 1 ? '' : 's'}
          </summary>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            {job.warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}
