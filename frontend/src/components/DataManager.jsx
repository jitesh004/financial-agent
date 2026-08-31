import React, { useCallback, useEffect, useState } from 'react';
import { Callout, Card, Chip, ConfirmButton, Stat } from './ui';
import { api, formatBytes, money } from '../lib';

/* The seven clearing actions, in place of one unlabelled Reset.
 *
 * The old button deleted the ledger, the file registry AND every uploaded
 * file, so somebody clearing a bad parse lost the only copy of the statement
 * that produced it. These are ordered by what it costs to get the data back -
 * seconds, CPU, network, money, or nothing at all because a person typed it -
 * and each says what it keeps as loudly as what it removes. */

const TONE = {
  derived: 'pos', parsed_data: 'pos', files: 'warn',
  ai_inferences: 'warn', decisions: 'warn', everything: 'neg',
};

export default function DataManager() {
  const [inv, setInv] = useState(null);
  const [error, setError] = useState(null);
  const [note, setNote] = useState(null);
  const [pending, setPending] = useState(null);   // scope awaiting confirmation
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [expandedPreview, setExpandedPreview] = useState(null);
  const [previewData, setPreviewData] = useState(null);
  const [rebuildMonths, setRebuildMonths] = useState(0);

  async function loadPreview(scope) {
    if (expandedPreview === scope) {
      setExpandedPreview(null);
      return;
    }
    setExpandedPreview(scope);
    setPreviewData(null);
    try {
      const data = await api.previewData(scope);
      setPreviewData(data);
    } catch(e) {
      setPreviewData({ error: [{ error: e.message || "Failed to load preview. Please restart the backend." }] });
    }
  }

  const load = useCallback(() => {
    api.inventory().then(setInv).catch((e) => setError(e.message));
  }, []);

  useEffect(load, [load]);

  async function run(action) {
    if (action.confirm_phrase && typed !== action.confirm_phrase) {
      setPending(action.scope);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await api.clearData(action.scope, action.confirm_phrase || undefined);
      const removed = Object.entries(res.removed || {})
        .map(([t, n]) => `${n} ${t.replace(/_/g, ' ')}`).join(', ');
      setNote(
        `${action.label} done. ${removed ? `Removed ${removed}.` : 'Nothing to remove.'}`
        + ` A snapshot was saved first (${res.snapshot}), so this is undoable.`,
      );
      setPending(null);
      setTyped('');
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function restore(name) {
    setBusy(true);
    try {
      await api.restoreSnapshot(name);
      setNote(`Restored ${name}. The state before this restore was snapshotted too.`);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function deleteSnapshot(name) {
    setBusy(true);
    try {
      await api.deleteSnapshot(name);
      setNote(`Deleted snapshot ${name}.`);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function reanalyzeAll() {
    setBusy(true);
    try {
      const res = await api.reanalyze(rebuildMonths || null);
      setNote(`Rebuild started (Run ID: ${res.run_id}). Rebuilding ${res.file_count} file(s). Refresh in a couple of minutes.`);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (!inv) return <div className="spinner" style={{ margin: 40 }} />;

  const c = inv.counts || {};
  const f = inv.files || {};

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 className="section-title" style={{ marginBottom: 4 }}>Your data</h2>
        <p style={{ color: 'var(--text-2)', margin: 0 }}>
          Every action here takes a snapshot first, so none of them is final.
        </p>
      </div>

      {error && <Callout tone="neg">{error}</Callout>}
      {note && <Callout tone="pos">{note}</Callout>}

      <div className="grid cols-4">
        {/* Counts as strings - Stat formats any number as currency. */}
        <Stat label="Transactions" value={(c.transactions ?? 0).toLocaleString()} />
        <Stat label="Accounts" value={String(c.accounts ?? 0)} />
        <Stat label="Statement files" value={String(f.count ?? 0)}
              note={formatBytes(f.bytes || 0)} />
        <Stat label="Your decisions" value={String(c.user_overrides ?? 0)}
              note="cannot be regenerated" />
      </div>

      {(f.uploaded_count > 0 || f.gmail_cached_count > 0) && (
        <Callout tone={f.uploaded_count ? 'warn' : 'pos'}>
          {f.gmail_cached_count || 0} file(s) came from Gmail and could be
          downloaded again.{' '}
          {f.uploaded_count
            ? `${f.uploaded_count} were uploaded by hand and exist nowhere else — `
              + 'clearing files destroys those permanently.'
            : 'Nothing was uploaded by hand.'}
        </Callout>
      )}

      <Card title="Clearing actions" sub="Ordered by what it costs to get the data back">
        {(inv.actions || []).map((a) => (
          <div key={a.scope} className="file-row" style={{ flexWrap: 'wrap' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, display: 'flex', gap: 8, alignItems: 'center' }}>
                {a.label}
                {a.destructive && <Chip tone="neg">destructive</Chip>}
              </div>
              <div style={{ color: 'var(--text-2)', fontSize: 13, marginTop: 2 }}>
                {a.description}
              </div>
              <div style={{ marginTop: 6, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {(a.preserves || []).map((p) => (
                  <Chip key={p} tone="pos">keeps {p}</Chip>
                ))}
              </div>
            </div>
            <div style={{ textAlign: 'right', display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-end' }}>
              <button
                className={`btn ${a.destructive ? 'danger' : ''}`}
                disabled={busy}
                onClick={() => run(a)}
              >
                {a.label}
              </button>
              {['ai_inferences', 'parsed_data', 'decisions', 'files'].includes(a.scope) && (
                  <button className="btn" style={{ fontSize: 12, padding: '2px 8px' }} onClick={() => loadPreview(a.scope)}>
                    {expandedPreview === a.scope ? 'Hide preview' : 'Preview data'}
                  </button>
              )}
            </div>

            {expandedPreview === a.scope && (
              <div style={{ flexBasis: '100%', marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--surface-2)' }}>
                {previewData ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 24, paddingBottom: 12 }}>
                    {Object.entries(previewData).map(([tableName, rows]) => (
                      <div key={tableName}>
                        <div style={{ marginBottom: 8, fontWeight: 600, color: 'var(--text-1)', textTransform: 'capitalize' }}>
                          {tableName.replace(/_/g, ' ')} <span style={{ color: 'var(--text-3)', fontWeight: 'normal' }}>({rows.length}{rows.length === 500 ? '+' : ''} rows)</span>
                        </div>
                        {rows.length === 0 ? (
                          <div style={{ color: 'var(--text-3)', fontSize: 13, fontStyle: 'italic' }}>Table is empty.</div>
                        ) : (
                          <div className="table-wrap scroll-y" style={{ maxHeight: 400 }}>
                            <table>
                              <thead>
                                <tr>
                                  {Object.keys(rows[0]).map(k => (
                                    <th key={k} style={{ textTransform: 'capitalize' }}>{k.replace(/_/g, ' ')}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {rows.map((r, i) => (
                                  <tr key={i}>
                                    {Object.values(r).map((v, j) => (
                                      <td key={j} style={{ whiteSpace: 'nowrap', maxWidth: 250, overflow: 'hidden', textOverflow: 'ellipsis' }} title={String(v)}>
                                        {v === null ? <span style={{ color: 'var(--text-3)' }}>null</span> : String(v)}
                                      </td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="spinner" style={{ fontSize: 13, color: 'var(--text-3)' }}> Loading preview...</div>
                )}
              </div>
            )}
                        {pending === a.scope && (
              <div style={{
                flexBasis: '100%', marginTop: 12, paddingTop: 12,
                borderTop: '1px solid var(--surface-2)',
              }}
              >
                <Callout tone="neg">
                  This removes {(a.clears || []).join(', ')}. Type{' '}
                  <strong>{a.confirm_phrase}</strong> to confirm.
                </Callout>
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <input
                    autoFocus
                    value={typed}
                    placeholder={a.confirm_phrase}
                    onChange={(e) => setTyped(e.target.value)}
                    style={{ flex: 1 }}
                  />
                  <button
                    className="btn danger"
                    disabled={typed !== a.confirm_phrase || busy}
                    onClick={() => run(a)}
                  >
                    Confirm
                  </button>
                  <button
                    className="btn"
                    onClick={() => { setPending(null); setTyped(''); }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
      </Card>

      <Card title="System Actions" sub="Run full system rebuilds">
        <div className="file-row" style={{ flexWrap: 'wrap', gap: 12 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600 }}>Rebuild ledger from statements</div>
            <div style={{ color: 'var(--text-2)', fontSize: 13, marginTop: 2 }}>
              Drop all parsed data and re-run existing statement files through the full pipeline. Use after a parsing rule change.
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
              <label style={{ fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Scope
              </label>
              <select
                value={rebuildMonths}
                onChange={(e) => setRebuildMonths(Number(e.target.value))}
                disabled={busy}
                style={{
                  padding: '5px 8px', fontSize: 13, borderRadius: 6,
                  border: '1px solid var(--border)', background: 'var(--surface-2)',
                  color: 'var(--text-1)', cursor: 'pointer',
                }}
              >
                <option value={0}>All time</option>
                <option value={3}>Last 3 months</option>
                <option value={6}>Last 6 months</option>
                <option value={12}>Last 12 months</option>
                <option value={24}>Last 24 months</option>
              </select>
            </div>
            <ConfirmButton className="btn primary" disabled={busy}
              style={{ alignSelf: 'flex-end' }}
              question={`Wipe all parsed data and rebuild ${rebuildMonths === 0
                ? 'every statement' : `the last ${rebuildMonths} months`} from
                the files on disk? It runs in the background.`}
              confirmLabel="Start rebuild"
              onConfirm={reanalyzeAll}>
              Start Rebuild
            </ConfirmButton>
          </div>
        </div>
      </Card>
      
      <Card
        title="Snapshots"
        sub="Taken automatically before anything destructive"
      >
        {!(inv.snapshots || []).length && (
          <div style={{ color: 'var(--text-3)' }}>
            No snapshots yet — one is written the first time you clear anything.
          </div>
        )}
        {(inv.snapshots || []).map((s) => (
          <div key={s.name} className="file-row" style={{ flexWrap: 'wrap' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="truncate file-name">{s.name}</div>
              <div style={{ color: 'var(--text-3)', fontSize: 12 }}>
                {s.created_at} · {formatBytes(s.size_bytes)}
              </div>
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button className="btn" disabled={busy} onClick={() => restore(s.name)}>
                Restore
              </button>
              <ConfirmButton className="btn danger" disabled={busy}
                question={`Permanently delete snapshot ${s.name}?`}
                confirmLabel="Delete"
                onConfirm={() => deleteSnapshot(s.name)}>
                Delete
              </ConfirmButton>
            </div>
          </div>
        ))}
      </Card>
    </div>
  );
}
