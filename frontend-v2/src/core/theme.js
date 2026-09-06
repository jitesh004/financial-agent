/* Which theme the app is in, for every screen rather than just the app.
 *
 * Held here, not in a component, because the two screens before the app - the
 * sign-in door and the setup wizard - render before any shell exists. A theme
 * owned by the shell leaves those two with no `data-theme` at all, which is to
 * say the light palette, under a dark app.
 *
 * Applied at import time, before React renders anything, so there is no screen
 * the theme can be missing from and no first paint of the wrong one. The same
 * decision is written a second time, inline in index.html, so it lands before
 * this module is even downloaded; the two must agree.
 *
 * Deliberately NOT scoped per account (see storage.js): dark mode belongs to
 * the screen and the person looking at it, not to the account they happen to
 * be signed into - and it has to be readable before anyone is signed in.
 */

import { useCallback, useEffect, useState } from 'react';

const KEY = 'prism-theme';
const EVENT = 'prism-theme-changed';

export function readTheme() {
  try {
    const stored = JSON.parse(localStorage.getItem(KEY) ?? 'null')
      ?? localStorage.getItem(KEY);
    if (stored === 'dark' || stored === 'light') return stored;
  } catch {
    // The inline script in index.html writes a bare string, not JSON, so a
    // value written by it fails to parse here. Read it raw before giving up.
    try {
      const raw = localStorage.getItem(KEY);
      if (raw === 'dark' || raw === 'light') return raw;
    } catch { /* private mode */ }
  }
  // Dark unless the system asks for light. `prefers-color-scheme: dark` is
  // false both for "light" and for "no preference", so asking the question the
  // other way round is what makes dark the default rather than the
  // consolation prize.
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  // The browser's own chrome on mobile is painted from this, and a light
  // address bar over a dark page reads as a rendering fault.
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', theme === 'dark' ? '#0a0c11' : '#f7f8fa');
}

export function setTheme(theme) {
  const next = theme === 'dark' ? 'dark' : 'light';
  applyTheme(next);
  // Written as a bare string so the inline boot script in index.html - which
  // cannot afford a JSON.parse guard - reads the same value.
  try { localStorage.setItem(KEY, next); } catch { /* private mode */ }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: next }));
  return next;
}

applyTheme(readTheme());

export function useTheme() {
  const [theme, setLocal] = useState(readTheme);

  useEffect(() => {
    const onChange = (e) => setLocal(e.detail);
    window.addEventListener(EVENT, onChange);
    return () => window.removeEventListener(EVENT, onChange);
  }, []);

  /* Somebody who has never chosen follows their system. Once they have chosen,
     they have chosen - flipping the OS to light must not undo it. */
  useEffect(() => {
    const q = window.matchMedia?.('(prefers-color-scheme: light)');
    if (!q?.addEventListener) return undefined;
    const onSystem = () => {
      let stored = null;
      try { stored = localStorage.getItem(KEY); } catch { /* private mode */ }
      if (!stored) { applyTheme(readTheme()); setLocal(readTheme()); }
    };
    q.addEventListener('change', onSystem);
    return () => q.removeEventListener('change', onSystem);
  }, []);

  const toggle = useCallback(
    () => setLocal(setTheme(readTheme() === 'dark' ? 'light' : 'dark')), []);

  return [theme, toggle];
}
