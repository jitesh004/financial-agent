import React, { useCallback, useEffect, useState } from 'react';
import { Callout, Card, Chip, ConfirmButton, Stat } from './ui';
import { api, titleCase } from '../lib';
import { PREFS, usePrefs } from '../prefs';
import LlmSettings from './LlmSettings';

/* Categories you invented, and how the app should look and behave.
 *
 * Display preferences live in localStorage rather than the database: they are
 * per-browser, worthless to anyone else, and a round trip to save "compact
 * rows" would be silly. Categories are the opposite - they are a decision,
 * they change what the ledger means, and they belong with the rest of the
 * tier-0 data that survives a re-parse. */

export default function Settings({ onLedgerChanged }) {
  const [categories, setCategories] = useState([]);
  const [customCategories, setCustomCategories] = useState(new Set());
  const [name, setName] = useState('');
  const [error, setError] = useState(null);
  const [note, setNote] = useState(null);
  const [prefs, setPref] = usePrefs();

  const load = useCallback(() => {
    Promise.all([api.categories(), api.health()])
      .then(([cats]) => setCategories(cats || []))
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
    // Asked for directly rather than assumed from position: this used to take
    // the first 30 entries of /api/categories as "built-in", a count that
    // happened to match Category's own built-in list today but would have
    // silently misclassified everything the moment that list ever changed.
    api.request('/api/categories/custom').then((custom) => {
      setCustomCategories(new Set(custom || []));
    }).catch(() => {});
  }, [load]);

  async function add(e) {
    e.preventDefault();
    const clean = name.trim().toLowerCase().replace(/\s+/g, '_');
    if (!clean) return;
    try {
      await api.addCategory(clean);
      setName('');
      setNote(`Added "${titleCase(clean)}". It is now available everywhere a
               category can be chosen.`);
      load();
    } catch (e2) {
      setError(e2.message);
    }
  }

  async function remove(cat) {
    try {
      await api.deleteCategory(cat);
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  const custom = categories.filter((c) => customCategories.has(c));
  const builtinCount = categories.length - customCategories.size;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 className="section-title" style={{ marginBottom: 4 }}>Settings</h2>
        <p style={{ color: 'var(--text-2)', margin: 0 }}>
          Categories are stored with your other decisions and survive a
          re-parse. Display options are per-browser.
        </p>
      </div>

      <LlmSettings onLedgerChanged={onLedgerChanged} />

      {error && <Callout tone="neg">{error}</Callout>}
      {note && <Callout tone="pos">{note}</Callout>}

      <div className="grid cols-2">
        <Stat label="Categories available" value={String(categories.length)} />
        <Stat label="Added by you" value={String(custom.length)} />
      </div>

      <Card title="Your categories" sub="Available anywhere a category is chosen">
        <form onSubmit={add} style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
          <input
            value={name}
            placeholder="e.g. pets, gifts_received, side_project"
            onChange={(e) => setName(e.target.value)}
            style={{ flex: 1 }}
          />
          <button className="btn primary" type="submit" disabled={!name.trim()}>
            Add category
          </button>
        </form>

        {!custom.length && (
          <div style={{ color: 'var(--text-3)' }}>
            None yet. The {builtinCount || categories.length} built-in categories
            cover most things; add your own when they do not.
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {custom.map((c) => (
            <span key={c} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <Chip tone="accent">{titleCase(c)}</Chip>
              <ConfirmButton
                className="btn icon"
                title={`Remove ${titleCase(c)}`}
                question={`Remove "${titleCase(c)}"? Transactions using it become uncategorized.`}
                confirmLabel="Remove"
                onConfirm={() => remove(c)}
              >
                ×
              </ConfirmButton>
            </span>
          ))}
        </div>
      </Card>

      <Card title="Display" sub="Remembered in this browser only">
        {PREFS.map((p) => (
          <div key={p.key} className="file-row">
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600 }}>{p.label}</div>
              <div style={{ color: 'var(--text-2)', fontSize: 13 }}>{p.hint}</div>
            </div>
            {p.type === 'toggle' ? (
              <button
                className={`btn ${prefs[p.key] ? 'primary' : ''}`}
                onClick={() => setPref(p.key, !prefs[p.key])}
              >
                {prefs[p.key] ? 'On' : 'Off'}
              </button>
            ) : (
              <select
                value={prefs[p.key]}
                onChange={(e) => setPref(p.key, e.target.value)}
              >
                {p.options.map(([v, label]) => (
                  <option key={v} value={v}>{label}</option>
                ))}
              </select>
            )}
          </div>
        ))}
      </Card>

      <Card title="Built-in categories" sub={`${builtinCount} always available`}>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {categories.filter((c) => !customCategories.has(c)).map((c) => (
            <Chip key={c}>{titleCase(c)}</Chip>
          ))}
        </div>
      </Card>
    </div>
  );
}
