/* ────────────────────────────────────────────────────────────────────────────
   Browser display preferences (density, pageSize, toggles)
   ──────────────────────────────────────────────────────────────────────── */

import { useCallback, useEffect, useState } from 'react';
import { read, write } from './storage';

const KEY = 'prism-v3-prefs';
const EVENT = 'prism-v3-prefs-changed';

export const PREFS = [
  {
    key: 'density',
    label: 'Row density',
    hint: 'Controls spacing in the transaction terminal.',
    type: 'select',
    options: [['spacious', 'Spacious'], ['comfortable', 'Comfortable'], ['compact', 'Ultra-Dense']],
    fallback: 'comfortable',
  },
  {
    key: 'pageSize',
    label: 'Rows per page',
    hint: 'How many transactions to load in the table view.',
    type: 'select',
    options: [['100', '100'], ['250', '250'], ['500', '500'], ['1000', '1000']],
    fallback: '250',
  },
  {
    key: 'showBalance',
    label: 'Show running balance',
    hint: 'Display statement running balance when available.',
    type: 'toggle',
    fallback: true,
  },
  {
    key: 'showRole',
    label: 'Show flow role',
    hint: 'Income, spending, transfer, refund.',
    type: 'toggle',
    fallback: true,
  },
  {
    key: 'showSource',
    label: 'Show categorization reason',
    hint: 'Rule, learned merchant, LLM, or manual override.',
    type: 'toggle',
    fallback: true,
  },
  {
    key: 'hideExcluded',
    label: 'Hide excluded transactions',
    hint: 'Filter out rows manually flagged as excluded.',
    type: 'toggle',
    fallback: false,
  },
  {
    key: 'animate',
    label: 'Animate charts & metrics',
    hint: 'Smooth micro-animations and chart draw transitions.',
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
