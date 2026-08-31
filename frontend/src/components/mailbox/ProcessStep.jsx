import React from 'react';
import JobProgress from '../JobProgress';
import { Callout, Chip } from '../ui';

/* Step 6: the only step that changes what any tab shows.
 *
 * Everything before this reads documents into a staging area. This rebuilds
 * the ledger from the files ticked on Review - all of them, every time, not
 * just the new ones. That is deliberate: the selection IS the ledger, so a
 * rebuild that added to the previous one could never remove anything, and
 * unticking a file would be a button that does nothing.
 *
 * Re-parsing is what is incremental. Rebuilding is not, and does not need to
 * be: the parse results are already stored, so this is reading rows out of
 * SQLite rather than PDFs off a disk.
 */

export default function ProcessStep({ staged, job, stage, onRun, onFinished }) {
  const running = stage === 'processing';
  const done = stage === 'done' && job?.kind === 'stage_process'
    && job.status === 'complete';
  const result = done ? (job.result || {}) : null;

  const rows = staged?.rows || 0;
  const selected = staged?.selected || 0;
  const nothing = !selected;

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <div>
        <div style={{ fontWeight: 640, fontSize: 14, marginBottom: 6 }}>
          Build the ledger from what you ticked
        </div>
        <p style={{ color: 'var(--text-2)', fontSize: 13, margin: '0 0 10px' }}>
          Your ledger is replaced by exactly the files selected on Review — the
          whole platform recomputes: categories, transfers, recurring items,
          every total on every tab. Anything you decided by hand is put back
          afterwards, and you are told about anything that could not be.
        </p>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Chip tone="accent">{selected} file{selected === 1 ? '' : 's'} selected</Chip>
          <Chip>{rows.toLocaleString('en-IN')} rows</Chip>
          {staged?.superseded > 0 && (
            <Chip tone="warn">{staged.superseded} superseded, skipped</Chip>
          )}
          {staged?.pending > 0 && (
            <Chip tone="warn">{staged.pending} still unread</Chip>
          )}
        </div>
      </div>

      {nothing && (
        <Callout tone="warn">
          Nothing is ticked on Review, so there is nothing to build. Go back a
          step and select at least one file.
        </Callout>
      )}

      {staged?.processed > 0 && !done && (
        <Callout>
          Your tabs currently show a ledger of{' '}
          <strong>{staged.processed.toLocaleString('en-IN')}</strong> rows from
          the last time you processed. Nothing staged has touched it.
        </Callout>
      )}

      {running && (
        <JobProgress job={job} title="Rebuilding your ledger" />
      )}

      {done && result && (
        <Callout tone="pos">
          <strong>
            {(result.transactions || 0).toLocaleString('en-IN')} transaction
            {result.transactions === 1 ? '' : 's'} across{' '}
            {result.accounts || 0} account{result.accounts === 1 ? '' : 's'} now count.
          </strong>
          {' '}
          {result.statements || 0} statement{result.statements === 1 ? '' : 's'}
          {result.bureau_reports ? `, ${result.bureau_reports} credit report(s)` : ''}
          {result.portfolios ? `, ${result.portfolios} portfolio(s)` : ''}
          {' '}were used.
          {typeof result.decisions_applied === 'number' && (
            <> {result.decisions_applied} of your own decisions were put back
              {result.decisions_orphaned
                ? `; ${result.decisions_orphaned} could not be matched to a row
                   and are kept for when it returns.`
                : '.'}
            </>
          )}
          {result.uncategorized > 0 && (
            <> {result.uncategorized} row
              {result.uncategorized === 1 ? '' : 's'} the rules could not place
              are waiting for the model under Settings — this step never calls
              it, so it never costs you money you did not ask to spend.</>
          )}
          {result.unread > 0 && (
            <> {result.unread} selected document
              {result.unread === 1 ? '' : 's'} could not be read — usually a
              password-protected PDF — and were skipped.</>
          )}
          {result.failed > 0 && (
            <> <strong>{result.failed} could not be rebuilt:</strong>{' '}
              {(result.failures || []).join('; ')}</>
          )}
          {' '}Every tab has been rebuilt.
        </Callout>
      )}

      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <button className="btn primary" disabled={running || nothing}
          onClick={onRun}>
          {running ? 'Rebuilding…' : done ? 'Rebuild again' : 'Process data'}
        </button>
        {done && (
          <button className="btn" onClick={onFinished}>Close and look</button>
        )}
        {!running && !done && !nothing && (
          <span className="xp-hint" style={{ textTransform: 'none' }}>
            This replaces your current ledger.
          </span>
        )}
      </div>
    </div>
  );
}
