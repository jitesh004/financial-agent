/* Files from this computer.
 *
 * A file you have is another source of the same documents, so it goes through
 * the same staging and the same Review as anything found in a mailbox. This
 * component only queues and uploads; nothing here reaches the ledger.
 */

import React, { useCallback, useRef, useState } from 'react';
import { api } from '../../core/api';
import { bytes } from '../../core/format';
import { Button, Callout, Chip, Icon, IconButton, ProgressBar } from '../../ui';

const ACCEPT = '.pdf,.xlsx,.xls,.xlsm,.csv,.tsv,.txt,.docx';

/* What the upload is actually doing. It used to end with "Analysing" and
   "Building your dashboard", which stopped being true the moment uploads
   started going to staging: nothing is analysed until the last step. */
const STAGES = ['Saving your files', 'Reading them', 'Staging for review'];

export default function Uploader({ onComplete, compact = false }) {
  const [files, setFiles] = useState([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState(0);
  const [error, setError] = useState(null);
  const [rejected, setRejected] = useState([]);
  const input = useRef(null);

  const add = useCallback((incoming) => {
    setError(null);
    setFiles((prev) => {
      // De-duped by name+size, so dropping the same batch twice does not queue
      // every statement a second time.
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      const next = [...prev];
      for (const f of incoming) {
        const k = `${f.name}:${f.size}`;
        if (!seen.has(k)) { seen.add(k); next.push(f); }
      }
      return next;
    });
  }, []);

  async function send() {
    if (!files.length) return;
    setBusy(true);
    setError(null);
    setRejected([]);
    setStage(0);

    // The backend reports coarse status only, so the stage list advances on a
    // timer purely as an affordance. It is never presented as a percentage,
    // because that would be a fabricated number.
    const ticker = setInterval(
      () => setStage((s) => Math.min(s + 1, STAGES.length - 1)), 1400);

    try {
      const { job_id: jobId, rejected: skipped, staged } = await api.upload(files);
      if (skipped?.length) setRejected(skipped);
      if (!jobId) {
        setFiles([]);
        onComplete?.({ staged: staged || 0 });
        return;
      }
      /* A job the server cannot answer for is not a reason to stop quietly.
         This used to `break` on the first unreadable poll, which left the
         files still queued, no message on screen and nothing having happened -
         indistinguishable, from the outside, from an upload that worked. A few
         retries cover a restart or a dropped connection; after that it is
         reported. */
      let unanswered = 0;
      for (;;) {
        // eslint-disable-next-line no-await-in-loop
        await new Promise((r) => { setTimeout(r, 900); });
        // eslint-disable-next-line no-await-in-loop
        const job = await api.job(jobId).catch(() => null);
        if (!job) {
          unanswered += 1;
          if (unanswered < 5) continue;
          throw new Error(
            'The server stopped reporting on this upload. Your files were saved — '
            + 'the Data screen lists every file and what happened to it.');
        }
        unanswered = 0;
        if (!job.active) {
          if (job.status === 'failed') {
            throw new Error(job.errors?.join('; ') || 'Reading the files failed.');
          }
          setFiles([]);
          onComplete?.({ staged: staged || 0, ...(job.result || {}) }, job);
          return;
        }
      }
    } catch (e) {
      setError(e.message);
    } finally {
      clearInterval(ticker);
      setBusy(false);
    }
  }

  if (busy) {
    return (
      <div className="col" style={{ padding: '18px 4px', textAlign: 'center' }}>
        <strong>{STAGES[stage]}…</strong>
        <ProgressBar value={((stage + 1) / STAGES.length) * 100} tall />
        <p className="small dim">
          {files.length} file{files.length === 1 ? '' : 's'}. Large PDF statements take
          the longest.
        </p>
      </div>
    );
  }

  return (
    <div className="col">
      <div
        className={`dropzone ${over ? 'over' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          add(Array.from(e.dataTransfer.files || []));
        }}
        onClick={() => input.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
      >
        <Icon name="upload" size={20} />
        <h3>Drop your statements here</h3>
        <p>
          Bank, credit card, loan and investment statements — PDF, Excel, Word or CSV.
          {!compact && ' Detected by content, not by the file extension.'}
        </p>
        <input
          ref={input} type="file" multiple accept={ACCEPT} hidden
          onChange={(e) => { add(Array.from(e.target.files || [])); e.target.value = ''; }}
        />
      </div>

      {error && <Callout tone="neg"><strong>Upload failed.</strong> {error}</Callout>}
      {rejected.map((r) => <Callout tone="warn" key={r}>{r}</Callout>)}

      {files.length > 0 && (
        <div className="card sunken" style={{ padding: 10 }}>
          <div className="col" style={{ gap: 4 }}>
            {files.map((f, i) => (
              <div className="row" key={`${f.name}-${i}`}>
                <Chip tone="acc">{f.name.split('.').pop().toUpperCase()}</Chip>
                <span className="truncate" style={{ flex: 1 }}>{f.name}</span>
                <span className="tiny dim num">{bytes(f.size)}</span>
                <IconButton icon="x" size="xs" className="ghost"
                  label={`Remove ${f.name}`}
                  onClick={() => setFiles((p) => p.filter((_, j) => j !== i))} />
              </div>
            ))}
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <Button variant="primary" size="sm" icon="upload" onClick={send}>
              Add {files.length} file{files.length === 1 ? '' : 's'}
            </Button>
            <Button size="sm" onClick={() => setFiles([])}>Clear</Button>
            <span className="tiny dim">
              They are read on the next step, with every other source.
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
