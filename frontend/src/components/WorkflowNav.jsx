import React, { useCallback, useEffect } from 'react';
import { Chip } from './ui';
import { api } from '../lib';

/* Where the workspace stands, derived fresh on every request.
 *
 * Deliberately not a wizard. Every stage stays reachable at all times - this
 * only reports what is done and what is blocking the next thing. The backend
 * computes it from stored data rather than tracking a "current step", because
 * a stored pointer is a second source of truth about state the database
 * already knows, and the two drift the moment anything happens out of band. */

//: Remembered per browser. Someone who has collapsed this once has said it is
//: not what they come to the app for, and re-expanding it on every reload
//: would be the app disagreeing every morning.
const STORAGE_KEY = 'fa-workflow-open';

export default function WorkflowNav({ onNavigate }) {
  const [state, setState] = React.useState(null);
  const [open, setOpen] = React.useState(() => {
    try { return localStorage.getItem(STORAGE_KEY) !== 'closed'; } catch { return true; }
  });

  const toggle = () => setOpen((v) => {
    const next = !v;
    try { localStorage.setItem(STORAGE_KEY, next ? 'open' : 'closed'); } catch { /* private mode */ }
    return next;
  });

  const load = useCallback(() => {
    api.workflow().then(setState).catch(() => setState(null));
  }, []);

  useEffect(load, [load]);

  if (!state) return null;

  // Where each stage lives, so a blocked stage is one click from being fixed.
  const TAB_FOR = {
    sources: 'file-registry',
    collect: 'file-registry',
    parse: 'file-registry',
    review: 'review-queue',
    analyze: 'overview',
  };

  const outstanding = state.stages.filter((s) => !s.complete);

  //: Collapsed, this is one line that still answers the only question the bar
  //: exists to answer - is anything waiting on me - and gives it back its
  //: full height when the answer matters.
  if (!open) {
    return (
      <button type="button" onClick={toggle} className="card"
        style={{
          display: 'flex', gap: 8, alignItems: 'center', width: '100%',
          padding: '7px 12px', marginBottom: 16, textAlign: 'left',
          font: 'inherit', color: 'var(--text)', cursor: 'pointer',
        }}>
        <span style={{ color: 'var(--text-3)', fontSize: 12 }}>▸</span>
        <strong style={{ fontSize: 12.5 }}>Setup</strong>
        {outstanding.length
          ? <Chip tone="warn">{outstanding.length} to do</Chip>
          : <Chip tone="pos">all done</Chip>}
        <span className="xp-hint" style={{ textTransform: 'none' }}>
          {outstanding.length
            ? outstanding.map((s) => s.label).join(', ')
            : 'nothing waiting on you'}
        </span>
      </button>
    );
  }

  return (
    <div
      className="card"
      style={{
        display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'stretch',
        padding: 12, marginBottom: 16,
      }}
    >
      <button type="button" onClick={toggle} title="Hide these steps"
        style={{
          flex: '0 0 auto', alignSelf: 'flex-start', padding: '2px 8px',
          borderRadius: 'var(--radius)', border: '1px solid var(--surface-2)',
          background: 'transparent', color: 'var(--text-3)', font: 'inherit',
          fontSize: 12, cursor: 'pointer',
        }}>
        ▾
      </button>
      {state.stages.map((s, i) => {
        const target = TAB_FOR[s.id];
        const clickable = Boolean(target && onNavigate);
        return (
          <button
            key={s.id}
            type="button"
            onClick={clickable ? () => onNavigate(target) : undefined}
            disabled={!clickable}
            title={clickable ? `Go to ${s.label}` : s.detail}
            style={{
              flex: '1 1 140px', textAlign: 'left', padding: '10px 12px',
              borderRadius: 'var(--radius)', border: '1px solid var(--surface-2)',
              background: s.complete ? 'var(--positive-soft)' : 'var(--surface-2)',
              cursor: clickable ? 'pointer' : 'default',
              color: 'var(--text)', font: 'inherit',
            }}
          >
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <span style={{ color: 'var(--text-3)', fontSize: 12 }}>{i + 1}</span>
              <strong style={{ fontSize: 13 }}>{s.label}</strong>
              {s.complete
                ? <Chip tone="pos">done</Chip>
                : <Chip tone="warn">todo</Chip>}
            </div>
            <div style={{ color: 'var(--text-2)', fontSize: 12, marginTop: 4 }}>
              {s.detail}
            </div>
          </button>
        );
      })}
    </div>
  );
}
