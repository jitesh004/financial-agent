import React from 'react';

/* The header's way in, and the only place an import in progress is visible
   from anywhere in the app.
 *
 * It shows live state rather than a static label, because the whole point of
 * moving job state to the server is that work continues after you navigate
 * away - and work you cannot see is work you assume has stopped. */

const LABEL = {
  scan: 'Scanning',
  download: 'Downloading',
  process: 'Parsing',
};

export default function MailboxButton({ mailbox, onOpen }) {
  const { job, busy, stage } = mailbox;

  const interrupted = stage === 'interrupted';
  const showCount = busy && job?.total > 0;

  return (
    <button
      className={`btn mailbox-btn${busy ? ' busy' : ''}${interrupted ? ' warn' : ''}`}
      onClick={onOpen}
      title={busy
        ? `${LABEL[job?.kind] || 'Working'} — ${job?.current || 0} of ${job?.total || 0}`
        : interrupted
          ? 'An import stopped when the server restarted'
          : 'Scan your mailbox for statements'}
    >
      {busy ? <span className="spinner" style={{ width: 12, height: 12 }} />
        : interrupted ? <span aria-hidden>!</span>
          : <span aria-hidden>✉</span>}
      <span>{busy ? LABEL[job?.kind] || 'Working' : 'Imports'}</span>
      {showCount && (
        <span className="chip accent" style={{ padding: '0 6px' }}>
          {job.current}/{job.total}
        </span>
      )}
      {/* A thread of the progress bar, thin enough to live inside a button.
          Reading a percentage from the header beats opening a modal to learn
          that nothing has moved. */}
      {busy && job?.percent > 0 && (
        <span className="mailbox-progress" aria-hidden>
          <span style={{ width: `${job.percent}%` }} />
        </span>
      )}
    </button>
  );
}
