/* Per-browser display preferences.
 *
 * Deliberately localStorage rather than the database. These are worthless to
 * anyone else, differ legitimately between a laptop and a phone, and a round
 * trip to persist "compact rows" would be absurd. Anything that changes what
 * the ledger MEANS - a category, a correction - goes to the server instead,
 * where it survives a re-parse.
 */

import { useCallback, useEffect, useState } from 'react';
import { read, write } from './storage';

const KEY = 'prism-prefs';
const EVENT = 'prism-prefs-changed';

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
    hint: 'How many transactions to load at a time. The table is virtualised, '
      + 'so a larger page costs a request rather than a frame rate.',
    type: 'select',
    options: [['100', '100'], ['250', '250'], ['500', '500'], ['1000', '1000']],
    fallback: '250',
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
    hint: 'Rule, learned, model, or your own correction.',
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
  {
    key: 'animate',
    label: 'Animate charts',
    hint: 'Bars and lines draw themselves on first paint. Off is instant.',
    type: 'toggle',
    fallback: true,
  },
];

const DEFAULTS = Object.fromEntries(PREFS.map((p) => [p.key, p.fallback]));

export function readPrefs() {
  return { ...DEFAULTS, ...(read(KEY, {}) || {}) };
}

export function usePrefs() {
  const [prefs, setPrefs] = useState(readPrefs);

  const setPref = useCallback((key, value) => {
    setPrefs((prev) => {
      const next = { ...prev, [key]: value };
      write(KEY, next);
      // So every other mounted component picks the change up without a reload.
      window.dispatchEvent(new CustomEvent(EVENT, { detail: next }));
      return next;
    });
  }, []);

  useEffect(() => {
    const onChange = (e) => setPrefs(e.detail);
    window.addEventListener(EVENT, onChange);
    return () => window.removeEventListener(EVENT, onChange);
  }, []);

  return [prefs, setPref];
}
