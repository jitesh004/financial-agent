/* ────────────────────────────────────────────────────────────────────────────
   Toast Notifications System
   ──────────────────────────────────────────────────────────────────────── */

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
    fail: (title, detail) => push({ tone: 'neg', title, detail, ttl: 8000 }),
  }), [push, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts-container" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast-card toast-${t.tone}`}>
            <div className="toast-icon-wrap">
              <Icon
                name={t.tone === 'neg' ? 'alert' : t.tone === 'warn' ? 'warning' : 'check-circle'}
                size={16}
              />
            </div>
            <div className="toast-body">
              <div className="toast-title">{t.title}</div>
              {t.detail && <div className="toast-detail">{t.detail}</div>}
            </div>
            <button className="toast-close-btn" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
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
  return value || {
    push: () => {}, dismiss: () => {}, ok: () => {}, info: () => {},
    warn: () => {}, fail: () => {},
  };
}
