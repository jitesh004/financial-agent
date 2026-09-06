/* ⌘K.
 *
 * Twenty screens is more than a person holds in their head, and the answer is
 * not a bigger menu - it is being able to type where you want to go. The
 * palette carries three kinds of thing:
 *
 *   - every screen, so navigation never needs the mouse;
 *   - the actions that are otherwise buried a screen deep - import, theme,
 *     rebuild, sign out;
 *   - the periods, because "last 3 months" is a thing people set far more
 *     often than they set anything else in this app.
 *
 * Matching is subsequence-based rather than substring: "cr" finds "Credit
 * report", "impst" finds "Import statements". Ranking prefers a prefix hit on
 * the label, which is what makes the first result the obvious one.
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useRoute } from '../core/router';
import { usePeriod } from '../core/period';
import { useTheme } from '../core/theme';
import { useAuth } from '../core/auth';
import { ROUTES } from './routes';
import { Icon } from '../ui/icons';

/* Score a candidate against the query. Returns null for no match. Lower is
   better, so a straight sort puts the best first. */
function score(text, q) {
  const t = text.toLowerCase();
  if (!q) return 0;
  const i = t.indexOf(q);
  if (i === 0) return 0;
  if (i > 0) return 1 + i * 0.01;
  // Subsequence: every character of the query in order, anywhere.
  let at = 0;
  let gaps = 0;
  for (const ch of q) {
    const found = t.indexOf(ch, at);
    if (found === -1) return null;
    gaps += found - at;
    at = found + 1;
  }
  return 6 + gaps * 0.01;
}

export default function CommandPalette({ open, onClose, onImport, hasLedger }) {
  const { navigate } = useRoute();
  const { setPeriod, quickPresets } = usePeriod();
  const [theme, toggleTheme] = useTheme();
  const { isAdmin, signOut } = useAuth();
  const [q, setQ] = useState('');
  const [cursor, setCursor] = useState(0);
  const listRef = useRef(null);

  const items = useMemo(() => {
    const out = [];
    for (const r of ROUTES) {
      if (r.adminOnly && !isAdmin) continue;
      if (r.needsLedger && !hasLedger) continue;
      out.push({
        id: `go:${r.key}`,
        group: 'Go to',
        label: r.label,
        hint: r.blurb,
        icon: r.icon,
        run: () => navigate(r.path),
      });
    }
    out.push(
      {
        id: 'act:import',
        group: 'Do',
        label: 'Import statements',
        hint: 'Scan your mailbox, or add files',
        icon: 'upload',
        run: onImport,
      },
      {
        id: 'act:theme',
        group: 'Do',
        label: theme === 'dark' ? 'Switch to the light theme' : 'Switch to the dark theme',
        icon: theme === 'dark' ? 'sun' : 'moon',
        run: toggleTheme,
      },
      {
        id: 'act:profile',
        group: 'Do',
        label: 'Your details',
        hint: 'What opens your password-protected statements',
        icon: 'user',
        run: () => navigate('/profile'),
      },
      {
        id: 'act:signout',
        group: 'Do',
        label: 'Sign out',
        icon: 'logout',
        run: signOut,
      },
    );
    for (const p of quickPresets) {
      out.push({
        id: `period:${p.value}`,
        group: 'Period',
        label: p.label,
        hint: p.resolved_label,
        icon: 'calendar',
        run: () => setPeriod({ preset: p.value }),
      });
    }
    return out;
  }, [isAdmin, hasLedger, navigate, onImport, theme, toggleTheme, signOut,
      quickPresets, setPeriod]);

  const matches = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return items;
    return items
      .map((it) => {
        const s = Math.min(
          score(it.label, needle) ?? Infinity,
          (score(it.hint || '', needle) ?? Infinity) + 3,
        );
        return Number.isFinite(s) ? { ...it, s } : null;
      })
      .filter(Boolean)
      .sort((a, b) => a.s - b.s)
      .slice(0, 24);
  }, [items, q]);

  useEffect(() => { setCursor(0); }, [q]);
  useEffect(() => { if (open) setQ(''); }, [open]);

  // Keep the highlighted row in view when arrowing past the fold.
  useEffect(() => {
    listRef.current?.querySelector('.cmd-item.on')
      ?.scrollIntoView({ block: 'nearest' });
  }, [cursor]);

  if (!open) return null;

  const pick = (item) => { onClose(); item.run(); };

  const onKey = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((c) => Math.min(c + 1, matches.length - 1)); }
    if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
    if (e.key === 'Enter' && matches[cursor]) { e.preventDefault(); pick(matches[cursor]); }
    if (e.key === 'Escape') { e.preventDefault(); onClose(); }
  };

  let lastGroup = null;

  return (
    <div className="scrim top" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="cmd" role="dialog" aria-modal="true" aria-label="Command palette">
        <div className="cmd-input-row">
          <Icon name="search" size={17} />
          <input
            className="cmd-input"
            autoFocus
            value={q}
            placeholder="Go to a screen, run something, set a period…"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKey}
            aria-label="Search commands"
          />
          <kbd>esc</kbd>
        </div>
        <div className="cmd-list" ref={listRef} role="listbox">
          {!matches.length && (
            <div style={{ padding: '26px 12px', textAlign: 'center' }} className="dim small">
              Nothing matches “{q}”.
            </div>
          )}
          {matches.map((item, i) => {
            const header = item.group !== lastGroup ? item.group : null;
            lastGroup = item.group;
            return (
              <React.Fragment key={item.id}>
                {header && <div className="cmd-group">{header}</div>}
                <button
                  type="button"
                  role="option"
                  aria-selected={i === cursor}
                  className={`cmd-item ${i === cursor ? 'on' : ''}`}
                  onMouseEnter={() => setCursor(i)}
                  onClick={() => pick(item)}
                >
                  <Icon name={item.icon} size={15} />
                  <span className="truncate">{item.label}</span>
                  {item.hint && <span className="hint truncate">{item.hint}</span>}
                </button>
              </React.Fragment>
            );
          })}
        </div>
        <div className="cmd-foot">
          <span><kbd>↑</kbd><kbd>↓</kbd> move</span>
          <span><kbd>↵</kbd> open</span>
          <span><kbd>esc</kbd> close</span>
        </div>
      </div>
    </div>
  );
}
