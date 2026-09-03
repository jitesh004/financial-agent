/* Which theme the app is in, for every screen rather than just the app.
 *
 * This used to live inside App.jsx, in a useEffect that set `data-theme` on
 * <html>. App.jsx renders only once you are signed in AND set up, so the two
 * screens before it - the sign-in door and the setup wizard - rendered with no
 * `data-theme` at all and fell back to :root, which is the light palette. The
 * app was dark, and the first two screens anybody sees were white.
 *
 * Holding it here fixes that by construction: the attribute is written at
 * import time, before React renders anything, so there is no screen the theme
 * can be missing from and no first-paint flash of the wrong one.
 *
 * The theme is deliberately NOT scoped per account (see userStorage.js): dark
 * mode belongs to the screen and the person looking at it, not to the account
 * they happen to be signed into - and it has to be readable before anyone is
 * signed in at all.
 */

import { useCallback, useEffect, useState } from 'react';

const KEY = 'fa-theme';
const EVENT = 'fa-theme-changed';

/* Kept in step with the inline script in index.html, which runs this same
   decision before the bundle loads so the first paint is already right. Two
   copies is the cost of not flashing white; they are eight lines apart in
   behaviour and both say so. */
export function readTheme() {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored === 'dark' || stored === 'light') return stored;
  } catch { /* private mode: fall through to the system answer */ }
  // Dark unless the system asks for light. `prefers-color-scheme: dark` is
  // false both for "light" and for "no preference", so asking that question
  // the other way round is what makes dark the default rather than the
  // consolation prize.
  return window.matchMedia?.('(prefers-color-scheme: light)').matches
    ? 'light' : 'dark';
}

export function applyTheme(theme) {
  const root = document.documentElement;
  root.setAttribute('data-theme', theme);
  // The browser's own chrome on mobile - the address bar - is painted from
  // this, and a light bar over a dark page reads as a rendering fault.
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    meta.setAttribute('content', theme === 'dark' ? '#0e1117' : '#f6f7f9');
  }
}

export function setTheme(theme) {
  const next = theme === 'dark' ? 'dark' : 'light';
  applyTheme(next);
  try { localStorage.setItem(KEY, next); } catch { /* private mode */ }
  // So a toggle in the header and one on the sign-in screen never disagree.
  window.dispatchEvent(new CustomEvent(EVENT, { detail: next }));
  return next;
}

/* Applied on import, which is the point: main.jsx imports this before it
   renders, so no screen is ever painted without a theme. */
applyTheme(readTheme());

export function useTheme() {
  const [theme, setLocal] = useState(readTheme);

  useEffect(() => {
    const onChange = (e) => setLocal(e.detail);
    window.addEventListener(EVENT, onChange);
    return () => window.removeEventListener(EVENT, onChange);
  }, []);

  /* Someone who has never chosen follows their system. Once they have chosen,
     they have chosen - flipping the OS to light must not undo it. */
  useEffect(() => {
    const query = window.matchMedia?.('(prefers-color-scheme: light)');
    if (!query?.addEventListener) return undefined;
    const onSystem = () => {
      let stored = null;
      try { stored = localStorage.getItem(KEY); } catch { /* private mode */ }
      if (!stored) {
        applyTheme(readTheme());
        setLocal(readTheme());
      }
    };
    query.addEventListener('change', onSystem);
    return () => query.removeEventListener('change', onSystem);
  }, []);

  const toggle = useCallback(
    () => setLocal(setTheme(readTheme() === 'dark' ? 'light' : 'dark')), []);

  return [theme, toggle];
}
