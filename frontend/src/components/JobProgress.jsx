import React, { useEffect, useRef } from 'react';
import { formatDuration } from '../lib';
import { Chip } from './ui';

const STATUS_TONE = {
  done: 'pos', skipped: 'warn', failed: 'neg', active: 'accent', pending: '',
};

/* Live progress for a running job.

   The bar is driven by real completed-item counts from the server, never by a
   timer - a bar that advances on its own while work is stuck is worse than no
   bar at all. The per-file trace below it is what makes a 200-file run
   debuggable instead of a black box. */
export default function JobProgress({ job, title, onCancel, showTrace = true }) {
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
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        {running && <div className="spinner" />}
        <strong style={{ fontSize: 14 }}>{title || job.phase}</strong>
        <span style={{ marginLeft: 'auto', fontSize: 12.5, color: 'var(--text-3)' }} className="num">
          {job.total > 0 ? `${job.current} / ${job.total}` : ''}
        </span>
      </div>

      <div className="bar" style={{ height: 8 }}>
        <span style={{
          width: `${job.percent}%`,
          background: job.status === 'failed' ? 'var(--negative)'
            : job.status === 'complete' ? 'var(--positive)' : 'var(--accent)',
        }} />
      </div>

      <div style={{
        display: 'flex', gap: 12, marginTop: 7, fontSize: 12,
        color: 'var(--text-3)', flexWrap: 'wrap', alignItems: 'center',
      }}>
        <span className="num">{job.percent}%</span>
        <span>{job.phase}</span>
        <span className="num">elapsed {formatDuration(job.elapsed)}</span>
        {job.eta_seconds != null && (
          <span className="num">~{formatDuration(job.eta_seconds)} left</span>
        )}

        {Object.entries(counts).map(([status, n]) => (
          <Chip key={status} tone={STATUS_TONE[status]}>{n} {status}</Chip>
        ))}

        {running && onCancel && (
          <button className="btn" style={{ marginLeft: 'auto', padding: '3px 10px', fontSize: 12 }}
            onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>

      {job.message && (
        <div style={{ marginTop: 8, fontSize: 13, color: 'var(--text-2)' }}>{job.message}</div>
      )}

      {showTrace && job.items?.length > 0 && (
        <div ref={traceRef} className="scroll-y"
          style={{
            marginTop: 10, maxHeight: 220, border: '1px solid var(--border)',
            borderRadius: 8, background: 'var(--surface-2)',
            fontFamily: 'var(--mono)', fontSize: 11.5,
          }}>
          {job.items.map((item, i) => (
            <div key={i} style={{
              display: 'flex', gap: 8, alignItems: 'center',
              padding: '3px 10px',
              borderBottom: '1px solid var(--border)',
            }}>
              <span className="dot" style={{
                background: item.status === 'done' ? 'var(--positive)'
                  : item.status === 'failed' ? 'var(--negative)'
                  : item.status === 'skipped' ? 'var(--warn)' : 'var(--text-3)',
              }} />
              <span style={{
                flex: 1, overflow: 'hidden', textOverflow: 'ellipsis',
                whiteSpace: 'nowrap', color: 'var(--text-2)',
              }}>
                {item.name}
              </span>
              {item.cached && <Chip>cached</Chip>}
              <span style={{ color: 'var(--text-3)', whiteSpace: 'nowrap' }}>
                {item.detail}
              </span>
            </div>
          ))}
        </div>
      )}

      {job.warnings?.length > 0 && (
        <details style={{ marginTop: 8, fontSize: 12, color: 'var(--text-3)' }}>
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
