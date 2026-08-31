import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../lib';
import { Callout, Card, Chip } from './ui';

const ACCEPTED = '.pdf,.xlsx,.xls,.xlsm,.csv,.tsv,.txt,.docx';

/* What the upload is actually doing now: saving and reading. It used to end
   with "Analysing" and "Building your dashboard", which stopped being true
   the moment uploads started going to staging - nothing is analysed until the
   wizard's last step. */
const STAGES = ['Saving your files', 'Reading them', 'Staging for review'];

/* `compact` also changes WHO decides when these are read: the wizard does,
   from its Scanning step, via the `onFilesChange` callback and the `submit`
   handle below. */
export default function Upload({ onComplete, compact = false, onFilesChange }) {
  const [files, setFiles] = useState([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState(0);
  const [error, setError] = useState(null);
  const [rejected, setRejected] = useState([]);
  const inputRef = useRef(null);

  const addFiles = useCallback((incoming) => {
    setError(null);
    setFiles((prev) => {
      // De-dupe by name+size so dropping the same batch twice doesn't queue
      // every statement a second time.
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      const next = [...prev];
      for (const f of incoming) {
        const key = `${f.name}:${f.size}`;
        if (!seen.has(key)) { seen.add(key); next.push(f); }
      }
      return next;
    });
  }, []);

  const onDrop = (e) => {
    e.preventDefault();
    setOver(false);
    addFiles(Array.from(e.dataTransfer.files || []));
  };

  const remove = (index) => setFiles((prev) => prev.filter((_, i) => i !== index));

  async function analyze() {
    if (!files.length) return;
    setBusy(true);
    setError(null);
    setRejected([]);
    setStage(0);

    // The backend reports coarse status only, so the stage list advances on a
    // timer purely as a progress affordance. It is never presented as a
    // precise percentage, because that would be a fabricated number.
    const ticker = setInterval(
      () => setStage((s) => Math.min(s + 1, STAGES.length - 1)),
      1400,
    );

    try {
      const { job_id: jobId, rejected: skipped, staged } = await api.upload(files);
      if (skipped?.length) setRejected(skipped);

      /* An upload is staged and read; it is not analysed here any more. What
         it produced is reviewed with everything else and reaches the ledger
         only when the wizard's last step is run - so this waits for the READ
         to finish and then hands over, rather than waiting for figures that
         are deliberately not being computed yet. */
      if (!jobId) {
        clearInterval(ticker);
        onComplete({ staged: staged || 0 });
        return;
      }
      for (;;) {
        await new Promise((r) => setTimeout(r, 900));
        const job = await api.job(jobId).catch(() => null);
        if (!job) break;
        if (!job.active) {
          clearInterval(ticker);
          if (job.status === 'failed') {
            throw new Error(job.errors?.join('; ') || 'Reading the files failed.');
          }
          onComplete({ staged: staged || 0, ...(job.result || {}) }, job);
          return;
        }
      }
    } catch (err) {
      setError(err.message);
    } finally {
      clearInterval(ticker);
      setBusy(false);
    }
  }

  // Tell the wizard what is queued, so its Scanning step can offer to read it.
  useEffect(() => { onFilesChange?.(files, analyze); }, [files]);

  if (busy) {
    return (
      <Card>
        <div style={{ padding: '28px 8px', textAlign: 'center' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10 }}>
            <div className="spinner" />
            <strong>{STAGES[stage]}…</strong>
          </div>
          <div className="bar" style={{ margin: '20px auto 8px', maxWidth: 420 }}>
            <span style={{ width: `${((stage + 1) / STAGES.length) * 100}%` }} />
          </div>
          <p style={{ color: 'var(--text-3)', fontSize: 13 }}>
            Analyzing {files.length} file{files.length === 1 ? '' : 's'}. Large PDF
            statements take the longest.
          </p>
        </div>
      </Card>
    );
  }

  return (
    <div className="grid" style={{ gap: 14 }}>
      <div
        className={`dropzone ${over ? 'over' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
      >
        <h3 style={compact ? { fontSize: 14, margin: 0 } : undefined}>
          Drop your statements here
        </h3>
        <p style={compact ? { fontSize: 12.5, margin: '4px 0 0' } : undefined}>
          Bank, credit card, loan and investment statements — PDF, Excel, Word or CSV.
        </p>
        {!compact && (
          <p style={{ marginTop: 10, color: 'var(--text-3)', fontSize: 12.5 }}>
            Upload every account for the same period. Statements that reference each
            other let the analyzer cancel out transfers instead of counting them as spending.
          </p>
        )}
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED}
          style={{ display: 'none' }}
          onChange={(e) => { addFiles(Array.from(e.target.files || [])); e.target.value = ''; }}
        />
      </div>

      {error && <Callout tone="neg"><strong>Upload failed.</strong> {error}</Callout>}
      {rejected.map((r) => <Callout tone="warn" key={r}>{r}</Callout>)}

      {files.length > 0 && (
        <Card title={`${files.length} file${files.length === 1 ? '' : 's'} ready`}>
          {files.map((f, i) => (
            <div className="file-row" key={`${f.name}-${i}`}>
              <Chip tone="accent">{f.name.split('.').pop().toUpperCase()}</Chip>
              <span className="file-name">{f.name}</span>
              <span className="num" style={{ color: 'var(--text-3)', fontSize: 12 }}>
                {(f.size / 1024).toFixed(0)} KB
              </span>
              <button className="btn icon" onClick={() => remove(i)} aria-label={`Remove ${f.name}`}>✕</button>
            </div>
          ))}
          <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
            {/* In the wizard, adding files is choosing a source - the reading
                happens on the Scanning step with every other source, so there
                is nothing to press here. Outside it, this is still the whole
                interaction and needs its own button. */}
            {!compact && (
              <button className="btn primary" onClick={analyze}>
                Analyze {files.length} statement{files.length === 1 ? '' : 's'}
              </button>
            )}
            <button className="btn" onClick={() => setFiles([])}>Clear</button>
          </div>
        </Card>
      )}
    </div>
  );
}
