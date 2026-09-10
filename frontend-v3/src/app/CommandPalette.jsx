/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Universal Command Palette 2.0 (⌘K / /)
   Spotlight search across screens, live arithmetic calculator & quick actions.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ROUTES } from './routes';
import { useRouter } from '../core/router';
import { Icon } from '../ui/icons';
import { setTheme, readTheme } from '../core/theme';
import { switchDemo } from '../core/api';
import { useCopilot } from '../core/copilot';
import { money } from '../core/format';

export default function CommandPalette({ open, onClose, onImport, hasLedger }) {
  const { navigate } = useRouter();
  const { openCopilot } = useCopilot();
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const inputRef = useRef(null);

  useEffect(() => {
    if (open) {
      setQuery('');
      setSelected(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  /* Live Math Expression Evaluator */
  const mathResult = useMemo(() => {
    const q = query.trim();
    if (!/^[0-9+\-*/().\s^%]+$/.test(q) || !/[+\-*/^%]/.test(q)) return null;
    try {
      // Safe sanitized arithmetic
      // eslint-disable-next-line no-new-func
      const res = Function(`"use strict"; return (${q})`)();
      if (typeof res === 'number' && !Number.isNaN(res) && Number.isFinite(res)) {
        return res;
      }
    } catch {}
    return null;
  }, [query]);

  /* Quick Actions */
  const actions = useMemo(() => [
    {
      id: 'act-copilot',
      label: 'Open AI Financial Copilot',
      group: 'Actions',
      icon: 'sparkles',
      action: () => { onClose(); openCopilot(); },
    },
    {
      id: 'act-import',
      label: 'Import Bank & Card Statements',
      group: 'Actions',
      icon: 'upload',
      action: () => { onClose(); onImport?.(); },
    },
    {
      id: 'act-theme',
      label: 'Toggle Dark / Light Theme',
      group: 'Actions',
      icon: 'sun',
      action: () => {
        setTheme(readTheme() === 'dark' ? 'light' : 'dark');
        onClose();
      },
    },
    {
      id: 'act-demo',
      label: 'Toggle Demo Workspace Mode',
      group: 'Actions',
      icon: 'scales',
      action: () => {
        onClose();
        switchDemo(true);
      },
    },
  ], [onClose, onImport, openCopilot]);

  /* Filtered Items */
  const items = useMemo(() => {
    const q = query.toLowerCase().trim();
    const result = [];

    // Math result item
    if (mathResult !== null) {
      result.push({
        id: 'math',
        label: `Calculate: ${query} = ${money(mathResult)}`,
        detail: String(mathResult),
        group: 'Calculator',
        icon: 'wallet',
        action: () => {},
      });
    }

    // Matching actions
    actions.forEach((a) => {
      if (!q || a.label.toLowerCase().includes(q)) {
        result.push(a);
      }
    });

    // Matching routes
    ROUTES.filter((r) => !r.hidden).forEach((r) => {
      if (!q || r.label.toLowerCase().includes(q) || r.title.toLowerCase().includes(q) || r.blurb?.toLowerCase().includes(q)) {
        result.push({
          id: `route-${r.key}`,
          label: r.label,
          detail: r.title !== r.label ? r.title : r.blurb,
          group: 'Navigation',
          icon: r.icon,
          action: () => { onClose(); navigate(r.path); },
        });
      }
    });

    return result.slice(0, 12);
  }, [query, mathResult, actions, onClose, navigate]);

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelected((prev) => (prev + 1) % (items.length || 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelected((prev) => (prev - 1 + items.length) % (items.length || 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (items[selected]) items[selected].action();
    } else if (e.key === 'Escape') {
      onClose();
    }
  };

  if (!open) return null;

  return (
    <div className="palette-overlay" onClick={onClose}>
      <div className="palette-box" onClick={(e) => e.stopPropagation()}>
        <div className="palette-input-wrap">
          <Icon name="search" size={18} style={{ color: 'var(--text-3)' }} />
          <input
            ref={inputRef}
            type="text"
            className="palette-input"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setSelected(0); }}
            onKeyDown={onKeyDown}
            placeholder="Type a screen, command, or math expression (e.g. 50000 * 0.18)..."
          />
          <span className="badge badge-accent" style={{ fontSize: 11 }}>ESC to close</span>
        </div>

        <div className="palette-results">
          {items.length === 0 ? (
            <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-3)' }}>
              No commands or destinations match "{query}"
            </div>
          ) : (
            items.map((item, idx) => (
              <div
                key={item.id}
                className={`palette-item ${selected === idx ? 'selected' : ''}`}
                onMouseEnter={() => setSelected(idx)}
                onClick={item.action}
              >
                <div style={{
                  width: 28, height: 28, borderRadius: 'var(--r-sm)',
                  background: 'var(--surface-2)', display: 'flex', alignItems: 'center',
                  justifyContent: 'center', color: 'inherit',
                }}>
                  <Icon name={item.icon || 'chevron'} size={15} />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 13.5 }}>{item.label}</div>
                  {item.detail && (
                    <div style={{ fontSize: 12, color: 'var(--text-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {item.detail}
                    </div>
                  )}
                </div>
                <span style={{ fontSize: 11, color: 'var(--text-3)', fontWeight: 600, textTransform: 'uppercase' }}>
                  {item.group}
                </span>
              </div>
            ))
          )}
        </div>

        <div className="palette-foot">
          <span>Navigate with <kbd style={{ padding: '1px 5px', background: 'var(--surface-3)', borderRadius: 3 }}>↑</kbd> <kbd style={{ padding: '1px 5px', background: 'var(--surface-3)', borderRadius: 3 }}>↓</kbd></span>
          <span>Select with <kbd style={{ padding: '1px 5px', background: 'var(--surface-3)', borderRadius: 3 }}>↵ Enter</kbd></span>
        </div>
      </div>
    </div>
  );
}
