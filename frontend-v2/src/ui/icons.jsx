/* The icon set.
 *
 * Inline paths in one file rather than a package. Two reasons, and the second
 * is the one that matters: an icon library is 60-300KB for the twenty glyphs
 * an app actually uses, and - for an app whose whole claim is that your
 * statements reach nothing else - a runtime that fetches sprites is one more
 * thing to have to say something about.
 *
 * Every glyph is drawn on a 24-unit grid with a 1.75 stroke and round joins,
 * so they sit together at any size without one looking heavier than the next.
 * They inherit `currentColor`, which is what lets the same glyph read as
 * muted in a rail and as accent in a selected one.
 */

import React from 'react';

const P = {
  /* navigation */
  overview: 'M3 13h6V3H3zM13 21h8V11h-8zM13 7h8V3h-8zM3 21h6v-4H3z',
  sparkles: 'M12 3l1.9 4.8L19 9.5l-5.1 1.7L12 16l-1.9-4.8L5 9.5l5.1-1.7zM18.5 15l.9 2.3 2.1.7-2.1.7-.9 2.3-.9-2.3-2.1-.7 2.1-.7z',
  wallet: 'M3 8a2 2 0 012-2h13a2 2 0 012 2M3 8v9a2 2 0 002 2h14a2 2 0 002-2v-2M3 8v0M16 12h5v3h-5a1.5 1.5 0 010-3z',
  calendar: 'M8 3v3M16 3v3M4 9h16M5 5h14a1 1 0 011 1v13a1 1 0 01-1 1H5a1 1 0 01-1-1V6a1 1 0 011-1z',
  repeat: 'M17 2l4 4-4 4M3 11V9a4 4 0 014-4h14M7 22l-4-4 4-4M21 13v2a4 4 0 01-4 4H3',
  trending: 'M3 17l6-6 4 4 8-8M21 7h-5M21 7v5',
  scales: 'M12 3v18M6 7l-3 7h6zM18 7l-3 7h6zM4 7h16M8 21h8',
  credit: 'M3 7a2 2 0 012-2h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2zM3 10h18M7 15h3',
  gauge: 'M12 14l4-4M20.6 17a9 9 0 10-17.2 0',
  briefcase: 'M9 6V4.5A1.5 1.5 0 0110.5 3h3A1.5 1.5 0 0115 4.5V6M3 10a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2zM3 12h18',
  rows: 'M3 6h18M3 12h18M3 18h18',
  inbox: 'M4 4h16a1 1 0 011 1v14a1 1 0 01-1 1H4a1 1 0 01-1-1V5a1 1 0 011-1zM3 14h5l1.5 2.5h5L16 14h5',
  compass: 'M12 21a9 9 0 100-18 9 9 0 000 18zM15.5 8.5l-2 5-5 2 2-5z',
  database: 'M12 8c4.4 0 8-1.1 8-2.5S16.4 3 12 3 4 4.1 4 5.5 7.6 8 12 8zM4 5.5v13C4 19.9 7.6 21 12 21s8-1.1 8-2.5v-13M4 12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5',
  book: 'M4 5a2 2 0 012-2h13v16H6a2 2 0 00-2 2zM4 19a2 2 0 002-2h13',
  settings: 'M12 15a3 3 0 100-6 3 3 0 000 6z M19.4 15a1.6 1.6 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.6 1.6 0 00-1.8-.3 1.6 1.6 0 00-1 1.5V21a2 2 0 11-4 0v-.1A1.6 1.6 0 008.9 19a1.6 1.6 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.6 1.6 0 00.3-1.8 1.6 1.6 0 00-1.5-1H3a2 2 0 110-4h.1A1.6 1.6 0 004.6 8.9a1.6 1.6 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.6 1.6 0 001.8.3H9a1.6 1.6 0 001-1.5V3a2 2 0 114 0v.1a1.6 1.6 0 001 1.5 1.6 1.6 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.6 1.6 0 00-.3 1.8V9a1.6 1.6 0 001.5 1H21a2 2 0 110 4h-.1a1.6 1.6 0 00-1.5 1z',
  shield: 'M12 21s8-4 8-10V5.5L12 3 4 5.5V11c0 6 8 10 8 10z',
  users: 'M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2M9 11a4 4 0 100-8 4 4 0 000 8zM22 21v-2a4 4 0 00-3-3.9M16 3.1a4 4 0 010 7.8',
  hand: 'M8 13V5a1.5 1.5 0 013 0v6M11 11V4a1.5 1.5 0 013 0v7M14 11V6a1.5 1.5 0 013 0v8M17 10.5a1.5 1.5 0 013 0V15a6 6 0 01-6 6h-1a7 7 0 01-7-7v-3.5a1.5 1.5 0 013 0',

  /* actions */
  search: 'M11 19a8 8 0 100-16 8 8 0 000 16zM21 21l-4.3-4.3',
  x: 'M18 6L6 18M6 6l12 12',
  plus: 'M12 5v14M5 12h14',
  minus: 'M5 12h14',
  check: 'M20 6L9 17l-5-5',
  'check-circle': 'M12 21a9 9 0 100-18 9 9 0 000 18zM8.5 12l2.5 2.5 4.5-5',
  alert: 'M12 21a9 9 0 100-18 9 9 0 000 18zM12 8v5M12 16.5v.01',
  warning: 'M10.3 3.9L1.9 18a2 2 0 001.7 3h16.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0zM12 9v4M12 17v.01',
  info: 'M12 21a9 9 0 100-18 9 9 0 000 18zM12 16v-4.5M12 8v.01',
  chevron: 'M9 18l6-6-6-6',
  'chevron-down': 'M6 9l6 6 6-6',
  'chevron-up': 'M18 15l-6-6-6 6',
  'chevron-left': 'M15 18l-6-6 6-6',
  'arrow-right': 'M5 12h14M13 5l7 7-7 7',
  'arrow-left': 'M19 12H5M11 19l-7-7 7-7',
  'arrow-up': 'M12 19V5M5 12l7-7 7 7',
  'arrow-down': 'M12 5v14M19 12l-7 7-7-7',
  refresh: 'M3 12a9 9 0 0115.5-6.2L21 8M21 3v5h-5M21 12a9 9 0 01-15.5 6.2L3 16M3 21v-5h5',
  download: 'M12 3v12M7 11l5 5 5-5M4 20h16',
  upload: 'M12 17V5M7 9l5-5 5 5M4 20h16',
  external: 'M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5',
  trash: 'M4 7h16M9 7V5a1 1 0 011-1h4a1 1 0 011 1v2M6 7l1 13a1 1 0 001 1h8a1 1 0 001-1l1-13M10 11v6M14 11v6',
  edit: 'M4 20h4l10.5-10.5a2.1 2.1 0 10-3-3L5 17v3zM13.5 6.5l3 3',
  copy: 'M9 9h10a1 1 0 011 1v10a1 1 0 01-1 1H9a1 1 0 01-1-1V10a1 1 0 011-1zM5 15H4a1 1 0 01-1-1V4a1 1 0 011-1h10a1 1 0 011 1v1',
  filter: 'M3 5h18l-7 8v6l-4 2v-8z',
  sliders: 'M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4',
  menu: 'M3 6h18M3 12h18M3 18h18',
  sun: 'M12 17a5 5 0 100-10 5 5 0 000 10zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  moon: 'M20 14.5A8.5 8.5 0 019.5 4a8.5 8.5 0 1010.5 10.5z',
  eye: 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7zM12 15a3 3 0 100-6 3 3 0 000 6z',
  'eye-off': 'M10.6 6.2A9.9 9.9 0 0112 6c6.4 0 10 6 10 6a17 17 0 01-3.2 3.9M6.6 6.7A17 17 0 002 12s3.6 7 10 7a9.8 9.8 0 004.3-1M3 3l18 18M9.9 9.9a3 3 0 004.2 4.2',
  lock: 'M6 11h12a1 1 0 011 1v8a1 1 0 01-1 1H6a1 1 0 01-1-1v-8a1 1 0 011-1zM8 11V7a4 4 0 118 0v4',
  key: 'M15.5 3a5.5 5.5 0 00-5.2 7.3L3 17.6V21h3.4l1-1v-2h2v-2h2l1.3-1.3A5.5 5.5 0 1015.5 3zM17 8h.01',
  mail: 'M4 5h16a1 1 0 011 1v12a1 1 0 01-1 1H4a1 1 0 01-1-1V6a1 1 0 011-1zM3 7l9 6 9-6',
  file: 'M14 3H7a1 1 0 00-1 1v16a1 1 0 001 1h10a1 1 0 001-1V7zM14 3v4h4',
  folder: 'M3 7a1 1 0 011-1h5l2 2h9a1 1 0 011 1v10a1 1 0 01-1 1H4a1 1 0 01-1-1z',
  clock: 'M12 21a9 9 0 100-18 9 9 0 000 18zM12 7v5l3 2',
  play: 'M7 4l12 8-12 8z',
  pause: 'M8 4h3v16H8zM13 4h3v16h-3z',
  bolt: 'M13 2L4 14h7l-1 8 9-12h-7z',
  target: 'M12 21a9 9 0 100-18 9 9 0 000 18zM12 17a5 5 0 100-10 5 5 0 000 10zM12 13a1 1 0 100-2 1 1 0 000 2z',
  link: 'M10 13a5 5 0 007.5.5l3-3a5 5 0 00-7-7l-1.7 1.7M14 11a5 5 0 00-7.5-.5l-3 3a5 5 0 007 7l1.7-1.7',
  grip: 'M9 5h.01M9 12h.01M9 19h.01M15 5h.01M15 12h.01M15 19h.01',
  panel: 'M4 4h16a1 1 0 011 1v14a1 1 0 01-1 1H4a1 1 0 01-1-1V5a1 1 0 011-1zM9 4v16',
  logout: 'M15 17l5-5-5-5M20 12H9M12 3H5a1 1 0 00-1 1v16a1 1 0 001 1h7',
  user: 'M12 12a4.5 4.5 0 100-9 4.5 4.5 0 000 9zM4 21a8 8 0 0116 0',
  question: 'M12 21a9 9 0 100-18 9 9 0 000 18zM9.5 9.5a2.5 2.5 0 114 2c-.9.7-1.5 1.2-1.5 2.5M12 17.5v.01',
  split: 'M6 3v6a4 4 0 004 4h8M18 3v6a4 4 0 01-4 4H6M15 18l3-3-3-3M15 6l3-3-3-3',
  slash: 'M12 21a9 9 0 100-18 9 9 0 000 18zM5.6 5.6l12.8 12.8',
  'corner-down': 'M15 10l5 5-5 5M20 15H9a5 5 0 01-5-5V4',
};

export function Icon({ name, size = 16, strokeWidth = 1.75, className = '', ...rest }) {
  const d = P[name];
  if (!d) return null;
  return (
    <svg
      className={`ico ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      <path d={d} />
    </svg>
  );
}

/* The product mark, as a component. Used in the rail and on both gate screens,
   so a change lands in all three. */
export function Logo({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <defs>
        <linearGradient id="prism-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--c1)" />
          <stop offset="1" stopColor="var(--c2)" />
        </linearGradient>
      </defs>
      <path d="M16 4 L29 27 H3 Z" stroke="url(#prism-mark)" strokeWidth="2.4"
        strokeLinejoin="round" fill="none" />
      <path d="M2 16 H11" stroke="var(--text-3)" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M20 15 H30" stroke="var(--c1)" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M21 19.5 H30" stroke="var(--c2)" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M22 24 H30" stroke="var(--c3)" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

/* Google's mark, inline. An external image would be a request to a third party
   made before anybody has agreed to anything. */
export function GoogleMark({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#FFC107" d="M43.6 20.1H42V20H24v8h11.3C33.7 32.7 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 13 4 4 13 4 24s9 20 20 20 20-9 20-20c0-1.3-.1-2.6-.4-3.9z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.3 0-9.7-3.3-11.3-8l-6.5 5C9.6 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.1H42V20H24v8h11.3c-.8 2.2-2.2 4.1-4.1 5.6l6.2 5.2C36.9 40.2 44 35 44 24c0-1.3-.1-2.6-.4-3.9z" />
    </svg>
  );
}
