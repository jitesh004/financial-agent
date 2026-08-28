import React, { useCallback, useEffect, useState } from 'react';
import { Callout, Card, Chip, Stat } from './ui';
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
          <div key={a.scope} className="file-row">
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
            <div style={{ textAlign: 'right' }}>
              <button
                className={`btn ${a.destructive ? 'danger' : ''}`}
                disabled={busy}
                onClick={() => run(a)}
              >
                {a.label}
              </button>
            </div>

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
          <div key={s.name} className="file-row">
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="truncate file-name">{s.name}</div>
              <div style={{ color: 'var(--text-3)', fontSize: 12 }}>
                {s.created_at} · {formatBytes(s.size_bytes)}
              </div>
            </div>
            <button className="btn" disabled={busy} onClick={() => restore(s.name)}>
              Restore
            </button>
          </div>
        ))}
      </Card>
    </div>
  );
}
