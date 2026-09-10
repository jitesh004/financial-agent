import React, { useCallback, useRef, useState } from 'react';
import { api } from '../../core/api';
import { bytes } from '../../core/format';
import { Button, Callout, Chip, IconButton, ProgressBar } from '../../ui';
import { Icon } from '../../ui/icons';

const ACCEPT = '.pdf,.xlsx,.xls,.xlsm,.csv,.tsv,.txt,.docx';
const STAGES = ['Saving files to disk', 'Extracting text & table coordinates', 'Staging for validation review'];

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
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      const next = [...prev];
      for (const f of incoming) {
        const k = `${f.name}:${f.size}`;
        if (!seen.has(k)) {
          seen.add(k);
          next.push(f);
        }
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

    const ticker = setInterval(
      () => setStage((s) => Math.min(s + 1, STAGES.length - 1)),
      1400
    );

    try {
      const { job_id: jobId, rejected: skipped, staged } = await api.upload(files);
      if (skipped?.length) setRejected(skipped);
      if (!jobId) {
        setFiles([]);
        onComplete?.({ staged: staged || 0 });
        return;
      }

      let unanswered = 0;
      for (;;) {
        await new Promise((r) => { setTimeout(r, 900); });
        const job = await api.job(jobId).catch(() => null);
        if (!job) {
          unanswered += 1;
          if (unanswered < 5) continue;
          throw new Error(
            'The server stopped reporting on this upload. Your files were saved.'
          );
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
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)', padding: 'var(--space-5)', textAlign: 'center' }}>
        <strong className="font-semibold brand">{STAGES[stage]}…</strong>
        <ProgressBar value={((stage + 1) / STAGES.length) * 100} tall />
        <p className="tiny muted">
          {files.length} document{files.length === 1 ? '' : 's'}. Large multi-page PDF statements take the longest.
        </p>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
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
        style={{
          border: '2px dashed var(--border-subtle)',
          borderRadius: 'var(--radius-lg)',
          padding: 'var(--space-6)',
          textAlign: 'center',
          cursor: 'pointer',
          background: over ? 'var(--surface-3)' : 'var(--surface-2)',
          transition: 'all var(--t-fast)',
        }}
      >
        <div style={{ color: 'var(--brand-primary)', marginBottom: 8, display: 'inline-flex' }}>
          <Icon name="upload" size={28} />
        </div>
        <h4 className="h4" style={{ margin: '0 0 4px 0' }}>Drop statements here or browse files</h4>
        <p className="tiny muted" style={{ margin: 0 }}>
          Bank, credit card, loan and investment holdings statements (PDF, Excel, Word or CSV).
        </p>
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPT}
          hidden
          onChange={(e) => { add(Array.from(e.target.files || [])); e.target.value = ''; }}
        />
      </div>

      {error && <Callout tone="neg"><strong>Upload failed.</strong> {error}</Callout>}
      {rejected.map((r) => <Callout tone="warn" key={r}>{r}</Callout>)}

      {files.length > 0 && (
        <div style={{ padding: 'var(--space-3)', background: 'var(--surface-2)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {files.map((f, i) => (
              <div key={`${f.name}-${i}`} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                <Chip size="sm" tone="brand">{f.name.split('.').pop().toUpperCase()}</Chip>
                <span className="small truncate font-medium" style={{ flex: 1 }}>{f.name}</span>
                <span className="tiny num muted">{bytes(f.size)}</span>
                <IconButton
                  icon="x"
                  size="xs"
                  label={`Remove ${f.name}`}
                  onClick={() => setFiles((p) => p.filter((_, j) => j !== i))}
                />
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 'var(--space-2)', marginTop: 'var(--space-3)', alignItems: 'center' }}>
            <Button variant="primary" size="sm" icon="upload" onClick={send}>
              Process {files.length} Document{files.length === 1 ? '' : 's'}
            </Button>
            <Button size="sm" onClick={() => setFiles([])}>Clear</Button>
          </div>
        </div>
      )}
    </div>
  );
}
