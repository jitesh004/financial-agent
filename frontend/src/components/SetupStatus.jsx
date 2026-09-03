import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Chip } from './ui';
import { api } from '../lib';

/* Where the workspace stands: what is done, and what is waiting on you.
 *
 * Derived fresh from stored data on every request rather than tracked as a
 * "current step" pointer. A stored pointer is a second source of truth about
 * state the database already knows, and the two drift the moment anything
 * happens out of band - a file retried from the coverage grid, a restart
 * mid-import, a decision recorded from the transactions table.
 *
 * It used to be a six-tile strip above EVERY panel. That is the wrong place
 * for it twice over: it answers a question you ask on the days you are
 * importing statements, not the ones you are reading them, and it pushed the
 * figures - the thing the page is for - a hundred and forty pixels down the
 * screen on all of them. So it lives behind one header button now, which
 * carries the only part that is urgent (how many things are waiting) and
 * opens the rest on demand.
 */

//: Which tab fixes each stage. These pointed at `file-registry` and
//: `review-queue`, which have not existed since the eighteen tabs became
//: eleven - so every one of these buttons navigated to a tab key that matched
//: no render branch and blanked the page.
const TAB_FOR = {
  sources: 'data',
  collect: 'data',
  parse: 'data',
  review: 'review',
  analyze: 'overview',
};

export function useSetupStatus() {
  const [state, setState] = useState(null);

  const load = useCallback(() => {
    api.workflow().then(setState).catch(() => setState(null));
  }, []);

  useEffect(load, [load]);

  const stages = state?.stages || [];
  return {
    state,
    stages,
    outstanding: stages.filter((s) => !s.complete),
    reload: load,
  };
}

/* The header's trigger. Shows a count when something is waiting and nothing
   but a tick when nothing is - the difference between "look at me" and "you
   are done here", which is the whole reason this is a button and not a bar. */
export function SetupButton({ status, open, onToggle }) {
  const { state, outstanding } = status;
  if (!state) return null;
  const waiting = outstanding.length;

  return (
    <button
      className={`btn setup-btn${waiting ? ' warn' : ''}`}
      aria-haspopup="dialog"
      aria-expanded={open}
      onClick={onToggle}
      title={waiting
        ? `${waiting} setup step${waiting === 1 ? '' : 's'} waiting: `
          + outstanding.map((s) => s.label).join(', ')
        : 'Setup is complete — nothing waiting on you'}
    >
      <span aria-hidden>{waiting ? '◍' : '✓'}</span>
      <span className="setup-btn-label">Setup</span>
      {waiting > 0 && <span className="chip warn nav-count">{waiting}</span>}
    </button>
  );
}

/* The panel itself. A popover rather than a modal: it is a status readout you
   glance at and dismiss, and a full-screen dialog for six lines of text asks
   for more attention than it is owed. */
export function SetupPanel({ status, onClose, onNavigate, onProfile, onImport }) {
  const box = useRef(null);
  const { state, stages, outstanding, reload } = status;

  /* Re-read on open: the panel is the one place these figures are shown, so
     it should never be showing what was true when the page loaded.

     In its OWN effect, keyed on the (stable) loader. It used to sit in the
     effect below, which is keyed on `onClose` - an inline arrow, so a new
     identity on every render. The fetch set state, the state re-rendered the
     parent, the new `onClose` re-ran the effect, and the effect fetched
     again: 84 requests to /api/workflow in three seconds, for as long as the
     panel stayed open. A side effect belongs with the thing it depends on. */
  useEffect(() => { reload(); }, [reload]);

  useEffect(() => {
    const away = (e) => {
      // The trigger is outside this box, and it toggles. Without ignoring it
      // here, mousedown closed the panel and the click that followed
      // re-opened it - so the button could open but never close.
      if (box.current?.contains(e.target)) return;
      if (e.target.closest?.('.setup-btn')) return;
      onClose();
    };
    const escape = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', away);
      document.removeEventListener('keydown', escape);
    };
  }, [onClose]);

  if (!state) return null;
  const counts = state.counts || {};

  const go = (stage) => {
    onClose();
    if (stage.id === 'profile') { onProfile?.(); return; }
    const target = TAB_FOR[stage.id];
    if (target) onNavigate?.(target);
  };

  return (
    <div className="setup-panel" role="dialog" aria-label="Setup" ref={box}>
      <div className="setup-head">
        <div>
          <strong>Setup</strong>
          {outstanding.length
            ? <Chip tone="warn">{outstanding.length} to do</Chip>
            : <Chip tone="pos">all done</Chip>}
        </div>
        <button className="btn icon" aria-label="Close" onClick={onClose}>✕</button>
      </div>

      <p className="setup-lead">
        {outstanding.length
          ? 'Each of these is optional — the app works without any of them. '
            + 'They are listed because each one makes the figures more complete.'
          : 'Nothing is waiting on you. Every statement known to this '
            + 'workspace is parsed, reviewed and counted.'}
      </p>

      <ol className="setup-stages">
        {stages.map((stage, i) => (
          <li key={stage.id} className={stage.complete ? 'done' : 'todo'}>
            <button type="button" onClick={() => go(stage)}
              title={stage.complete ? stage.detail : `Go and fix: ${stage.label}`}>
              <span className="setup-dot">{stage.complete ? '✓' : i + 1}</span>
              <span className="setup-stage-body">
                <span className="setup-stage-name">{stage.label}</span>
                <span className="setup-stage-detail">{stage.detail}</span>
              </span>
              <span className="setup-go" aria-hidden>→</span>
            </button>
          </li>
        ))}
      </ol>

      <div className="setup-foot">
        <button className="btn primary" onClick={() => { onClose(); onImport?.(); }}>
          Import statements
        </button>
        <span className="setup-counts">
          {counts.transactions ? `${counts.transactions.toLocaleString('en-IN')} rows`
            : 'nothing imported yet'}
          {counts.files ? ` · ${counts.files} file${counts.files === 1 ? '' : 's'}` : ''}
          {counts.missing_months
            ? ` · ${counts.missing_months} month${counts.missing_months === 1 ? '' : 's'} missing`
            : ''}
        </span>
      </div>
    </div>
  );
}
