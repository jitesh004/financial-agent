import { useCallback, useEffect, useState } from 'react';
import { readScoped, writeScoped } from './userStorage';

/* Per-browser display preferences.
 *
 * Deliberately localStorage rather than the database. These are worthless to
 * anyone else, differ legitimately between a laptop and a phone, and a round
 * trip to persist "compact rows" would be absurd. Anything that changes what
 * the ledger MEANS - a category, a correction - goes to the server instead,
 * where it survives a re-parse. */

const KEY = 'fa-prefs';

export const PREFS = [
  {
    key: 'density',
    label: 'Row density',
    hint: 'Compact fits roughly half as much vertical space per row.',
    type: 'select',
    options: [['comfortable', 'Comfortable'], ['compact', 'Compact']],
    fallback: 'comfortable',
  },
  {
    key: 'pageSize',
    label: 'Rows per page',
    hint: 'How many transactions to load at a time.',
    type: 'select',
    options: [['50', '50'], ['100', '100'], ['250', '250'], ['500', '500']],
    fallback: '100',
  },
  {
    key: 'showBalance',
    label: 'Show running balance',
    hint: 'Only some statements carry one, so the column is often empty.',
    type: 'toggle',
    fallback: true,
  },
  {
    key: 'showRole',
    label: 'Show what each row counts as',
    hint: 'Income, spending, transfer, money back — the accounting side.',
    type: 'toggle',
    fallback: true,
  },
  {
    key: 'showSource',
    label: 'Show how a category was decided',
    hint: 'Rule, learned, AI, or your own correction.',
    type: 'toggle',
    fallback: true,
  },
  {
    key: 'hideExcluded',
    label: 'Hide excluded transactions',
    hint: 'Rows you have taken out of every total.',
    type: 'toggle',
    fallback: false,
  },
];

const DEFAULTS = Object.fromEntries(PREFS.map((p) => [p.key, p.fallback]));

export function readPrefs() {
  try {
    return { ...DEFAULTS, ...readScoped(KEY, {}) };
  } catch {
    // A private window, cleared site data, or storage disabled entirely.
    return { ...DEFAULTS };
  }
}

export function usePrefs() {
  const [prefs, setPrefs] = useState(readPrefs);

  const setPref = useCallback((key, value) => {
    setPrefs((prev) => {
      const next = { ...prev, [key]: value };
      try {
        writeScoped(KEY, next);
      } catch { /* not worth failing the interaction over */ }
      // So other mounted components pick the change up without a reload.
      window.dispatchEvent(new CustomEvent('fa-prefs-changed', { detail: next }));
      return next;
    });
  }, []);

  useEffect(() => {
    const onChange = (e) => setPrefs(e.detail);
    window.addEventListener('fa-prefs-changed', onChange);
    return () => window.removeEventListener('fa-prefs-changed', onChange);
  }, []);

  return [prefs, setPref];
}

/* CSV, built here so every table exports the same way.
 *
 * Quoting is not optional: transaction descriptions routinely contain commas
 * and quotes, and an unescaped one silently shifts every later column. */
export function toCsv(rows, columns) {
  const esc = (v) => {
    const s = v === null || v === undefined ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const head = columns.map(([, label]) => esc(label)).join(',');
  const body = rows.map(
    (r) => columns.map(([key]) => esc(
      typeof key === 'function' ? key(r) : r[key])).join(','));
  return [head, ...body].join('\n');
}

export function downloadCsv(filename, csv) {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
