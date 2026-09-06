/* Transient confirmations, in one corner.
 *
 * The app this replaces reported the result of every action as a banner inside
 * the panel that did it - which pushed the content down, stayed until the next
 * navigation, and could not report anything that finished while you were
 * looking somewhere else. A toast is the right shape for "that worked": it
 * costs no layout, it leaves on its own, and it is the same in every screen.
 *
 * Errors that a person has to act on are NOT toasts. Those stay beside the
 * control that failed, where the retry is.
 */

import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react';
import { Icon } from '../ui/icons';

const ToastContext = createContext(null);

let seq = 0;

export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const timers = useRef(new Map());

  const dismiss = useCallback((id) => {
    setItems((prev) => prev.filter((t) => t.id !== id));
    clearTimeout(timers.current.get(id));
    timers.current.delete(id);
  }, []);

  const push = useCallback((toast) => {
    const id = ++seq;
    const item = { id, tone: 'pos', ttl: 4200, ...toast };
    setItems((prev) => [...prev.slice(-3), item]);
    if (item.ttl) {
      timers.current.set(id, setTimeout(() => dismiss(id), item.ttl));
    }
    return id;
  }, [dismiss]);

  useEffect(() => {
    const map = timers.current;
    return () => map.forEach((t) => clearTimeout(t));
  }, []);

  const value = useMemo(() => ({
    push,
    dismiss,
    ok: (title, detail) => push({ tone: 'pos', title, detail }),
    info: (title, detail) => push({ tone: 'acc', title, detail }),
    warn: (title, detail) => push({ tone: 'warn', title, detail, ttl: 6500 }),
    // Long-lived rather than sticky: an error worth blocking on belongs beside
    // the control, and an error worth mentioning should still go away.
    fail: (title, detail) => push({ tone: 'neg', title, detail, ttl: 8000 }),
  }), [push, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`}>
            <Icon
              name={t.tone === 'neg' ? 'alert' : t.tone === 'warn' ? 'warning' : 'check-circle'}
              size={16}
            />
            <div className="toast-body">
              <div className="toast-title">{t.title}</div>
              {t.detail && <div className="toast-detail">{t.detail}</div>}
            </div>
            <button className="btn ghost icon xs" aria-label="Dismiss"
              onClick={() => dismiss(t.id)}>
              <Icon name="x" size={13} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const value = useContext(ToastContext);
  // Deliberately not throwing: a component rendered outside the provider
  // should lose its confirmations, not fail to render at all.
  return value || {
    push: () => {}, dismiss: () => {}, ok: () => {}, info: () => {},
    warn: () => {}, fail: () => {},
  };
}
