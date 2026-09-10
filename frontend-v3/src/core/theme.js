/* ────────────────────────────────────────────────────────────────────────────
   Theme Manager: Obsidian Dark & Alabaster Light
   ──────────────────────────────────────────────────────────────────────── */

import { useCallback, useEffect, useState } from 'react';

const KEY = 'prism-v3-theme';
const EVENT = 'prism-v3-theme-changed';

export function readTheme() {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored === 'dark' || stored === 'light') return stored;
  } catch {}
  if (typeof window !== 'undefined' && window.matchMedia) {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  return 'dark';
}

export function applyTheme(theme) {
  if (typeof document !== 'undefined') {
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', theme === 'dark' ? '#090b10' : '#f8fafc');
  }
}

export function setTheme(theme) {
  const resolved = theme === 'light' ? 'light' : 'dark';
  try {
    localStorage.setItem(KEY, resolved);
  } catch {}
  applyTheme(resolved);
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(EVENT, { detail: resolved }));
  }
  return resolved;
}

if (typeof window !== 'undefined') {
  applyTheme(readTheme());
}

export function useTheme() {
  const [theme, setLocal] = useState(readTheme);

  useEffect(() => {
    const onChange = (e) => setLocal(e.detail);
    window.addEventListener(EVENT, onChange);
    return () => window.removeEventListener(EVENT, onChange);
  }, []);

  const toggle = useCallback(
    () => setLocal(setTheme(readTheme() === 'dark' ? 'light' : 'dark')), []);

  return [theme, toggle];
}
