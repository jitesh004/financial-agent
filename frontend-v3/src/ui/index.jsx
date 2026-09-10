/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Comprehensive UI Design System Components
   ──────────────────────────────────────────────────────────────────────── */

import React, {
  useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState,
} from 'react';
import { createPortal } from 'react-dom';
import { Icon } from './icons';
export { Icon };

import { compact, money, titleCase } from '../core/format';

/* ═══════════════════════════════════════════════════════ Surfaces ═══════ */

export function Card({ title, sub, subtitle, tools, children, className = '', pad = true, glowing = false, ...rest }) {
  const renderedSub = sub ?? subtitle;
  const glowClass = glowing ? 'card-glow' : '';
  return (
    <div className={`card ${glowClass} ${className}`} {...rest}>
      {(title || renderedSub || tools) && (
        <div className="card-head">
          <div style={{ flex: 1, minWidth: 0 }}>
            {title && <div className="card-title">{title}</div>}
            {renderedSub && <div className="card-sub">{renderedSub}</div>}
          </div>
          {tools && <div className="flex items-center gap-2">{tools}</div>}
        </div>
      )}
      {pad ? <div className="card-body">{children}</div> : children}
    </div>
  );
}

export function GlassCard({ title, sub, subtitle, tools, children, className = '', pad = true, glowing = false, ...rest }) {
  const renderedSub = sub ?? subtitle;
  const glowClass = glowing ? 'glass-card-glow' : '';
  return (
    <div className={`glass-card ${glowClass} ${className}`} {...rest}>
      {(title || renderedSub || tools) && (
        <div className="card-head">
          <div style={{ flex: 1, minWidth: 0 }}>
            {title && <div className="card-title">{title}</div>}
            {renderedSub && <div className="card-sub">{renderedSub}</div>}
          </div>
          {tools && <div className="flex items-center gap-2">{tools}</div>}
        </div>
      )}
      {pad ? <div className="card-body">{children}</div> : children}
    </div>
  );
}

export function Section({ title, note, subtitle, children, actions }) {
  const renderedNote = note ?? subtitle;
  return (
    <div style={{ margin: '24px 0 14px' }}>
      <div className="flex items-center justify-between" style={{ marginBottom: 12 }}>
        <div>
          <h2 style={{ fontSize: 16, fontWeight: 700 }}>{title}</h2>
          {renderedNote && <div style={{ fontSize: 12.5, color: 'var(--text-3)', marginTop: 2 }}>{renderedNote}</div>}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {children}
    </div>
  );
}

export function Stat({
  label, value, note, sub, tone, precise = false, onDrill, drillTitle, hint, small, icon, delta,
}) {
  const shown = typeof value === 'number' ? money(value, precise) : (value ?? '—');
  const subNote = note ?? sub;
  const toneClass = tone === 'pos' ? 'pos' : tone === 'neg' ? 'neg' : tone === 'warn' ? 'warn' : tone === 'brand' || tone === 'accent' ? 'brand' : '';

  return (
    <div className="stat-widget">
      <div className="stat-header">
        <span className="stat-label">
          {label}
          {hint && <Hint text={hint} />}
        </span>
        {icon && (
          <div className="stat-icon">
            <Icon name={icon} size={16} />
          </div>
        )}
      </div>
      <div className={`stat-value ${toneClass} ${small ? 'text-lg' : ''}`}>
        {onDrill ? (
          <button
            type="button"
            className="tabular-nums"
            onClick={onDrill}
            style={{ color: 'inherit', textAlign: 'left', cursor: 'pointer' }}
            title={drillTitle || 'Show the transactions behind this'}
          >
            {shown}
          </button>
        ) : (
          <span className="tabular-nums">{shown}</span>
        )}
      </div>
      {(subNote || delta != null) && (
        <div className="stat-footer">
          {delta != null && (
            <span className={`stat-delta ${delta >= 0 ? 'pos' : 'neg'}`}>
              <Icon name={delta >= 0 ? 'arrow-up' : 'arrow-down'} size={11} />
              {Math.abs(delta)}%
            </span>
          )}
          {subNote && <span>{subNote}</span>}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════ Badges & Chips ═══════ */

export function Chip({ tone = '', children, className = '', ...rest }) {
  const t = tone === 'pos' ? 'chip-pos' : tone === 'neg' ? 'chip-neg' : tone === 'warn' ? 'chip-warn' : tone === 'acc' ? 'chip-accent' : '';
  return <span className={`chip ${t} ${className}`} {...rest}>{children}</span>;
}

export function Badge({ tone = '', children, className = '', ...rest }) {
  const t = tone === 'pos' ? 'badge-pos' : tone === 'neg' ? 'badge-neg' : tone === 'warn' ? 'badge-warn' : tone === 'acc' ? 'badge-accent' : '';
  return <span className={`badge ${t} ${className}`} {...rest}>{children}</span>;
}

const CALLOUT_ICON = { pos: 'check-circle', neg: 'alert', warn: 'warning', acc: 'info' };

export function Callout({ tone = '', children, icon, className = '', ...rest }) {
  const name = icon ?? CALLOUT_ICON[tone] ?? 'info';
  const toneBg = tone === 'neg' ? 'var(--neg-soft)' : tone === 'warn' ? 'var(--warn-soft)' : tone === 'pos' ? 'var(--pos-soft)' : 'var(--accent-soft)';
  const toneColor = tone === 'neg' ? 'var(--neg)' : tone === 'warn' ? 'var(--warn)' : tone === 'pos' ? 'var(--pos)' : 'var(--accent)';
  const toneBorder = tone === 'neg' ? 'var(--neg-border)' : tone === 'warn' ? 'var(--warn-border)' : tone === 'pos' ? 'var(--pos-border)' : 'var(--accent-border)';

  return (
    <div
      className={`callout ${className}`}
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 12, padding: '12px 16px',
        borderRadius: 'var(--r)', background: toneBg, color: toneColor,
        border: `1px solid ${toneBorder}`, fontSize: 13.5, margin: '10px 0',
      }}
      {...rest}
    >
      <Icon name={name} size={17} style={{ flexShrink: 0, marginTop: 2 }} />
      <div style={{ flex: 1, color: 'var(--text)' }}>{children}</div>
    </div>
  );
}

export function Empty({ title, icon = 'inbox', children, action }) {
  return (
    <div style={{ textAlign: 'center', padding: '48px 24px', margin: '20px 0' }}>
      <div style={{
        width: 48, height: 48, borderRadius: 'var(--r-lg)',
        background: 'var(--surface-2)', border: '1px solid var(--line)',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        color: 'var(--text-3)', marginBottom: 14,
      }}>
        <Icon name={icon} size={22} />
      </div>
      {title && <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>{title}</h3>}
      {children && <div style={{ color: 'var(--text-2)', maxWidth: 440, margin: '0 auto 16px', fontSize: 13.5 }}>{children}</div>}
      {action && <div>{action}</div>}
    </div>
  );
}

export function Loading({ message = 'Loading...' }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12, padding: '40px 0' }}>
      <Spinner />
      <span style={{ fontSize: 13.5, color: 'var(--text-2)' }}>{message}</span>
    </div>
  );
}

export function Spinner({ sm = false }) {
  const sz = sm ? 14 : 20;
  return (
    <div style={{
      width: sz, height: sz, borderRadius: '50%',
      border: '2px solid var(--line-strong)',
      borderTopColor: 'var(--accent)',
      animation: 'spin 0.8s linear infinite',
      display: 'inline-block',
    }} />
  );
}

/* ═══════════════════════════════════════════════════════ Buttons ═══════ */

export function Button({
  children, variant = 'secondary', size = '', icon, busy = false, disabled = false, className = '', ...rest
}) {
  const vClass = variant === 'primary' ? 'btn-primary'
    : variant === 'ghost' ? 'btn-ghost'
      : variant === 'danger' ? 'btn-danger'
        : 'btn-secondary';
  const sClass = size === 'sm' || size === 'xs' ? 'btn-sm' : size === 'lg' ? 'btn-lg' : '';

  return (
    <button
      type="button"
      className={`btn ${vClass} ${sClass} ${className}`}
      disabled={disabled || busy}
      {...rest}
    >
      {busy ? <Spinner sm /> : icon ? <Icon name={icon} size={15} /> : null}
      {children}
    </button>
  );
}

export function IconButton({ icon, label, onClick, size = 'sm', className = '', ...rest }) {
  return (
    <button
      type="button"
      className={`btn btn-ghost ${size === 'sm' ? 'btn-sm' : ''} ${className}`}
      onClick={onClick}
      title={label}
      aria-label={label}
      style={{ padding: 6, borderRadius: 'var(--r-sm)' }}
      {...rest}
    >
      <Icon name={icon} size={16} />
    </button>
  );
}

export function ConfirmButton({
  children, onConfirm, confirmLabel = 'Confirm', question,
  variant = '', size = '', icon, timeout = 8000, ...rest
}) {
  const [armed, setArmed] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  if (!armed) {
    return (
      <Button variant={variant} size={size} icon={icon} {...rest}
        onClick={() => {
          setArmed(true);
          clearTimeout(timer.current);
          timer.current = setTimeout(() => setArmed(false), timeout);
        }}>
        {children}
      </Button>
    );
  }

  return (
    <span className="flex items-center gap-2" style={{ display: 'inline-flex' }}>
      {question && <span style={{ fontSize: 11.5, color: 'var(--text-3)' }}>{question}</span>}
      <Button variant="danger" size={size} disabled={rest.disabled}
        onClick={async () => {
          clearTimeout(timer.current);
          setArmed(false);
          await onConfirm?.();
        }}>
        {confirmLabel}
      </Button>
      <Button size={size} variant="ghost" onClick={() => { clearTimeout(timer.current); setArmed(false); }}>
        Cancel
      </Button>
    </span>
  );
}

export function PromptButton({
  children, onSubmit, placeholder = '', initial = '', submitLabel = 'Save',
  variant = '', size = '', icon, ...rest
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(initial);
  useEffect(() => { if (open) setValue(initial); }, [open, initial]);

  if (!open) {
    return (
      <Button variant={variant} size={size} icon={icon} {...rest} onClick={() => setOpen(true)}>
        {children}
      </Button>
    );
  }

  const submit = async () => { setOpen(false); await onSubmit?.(value); };

  return (
    <span className="flex items-center gap-2" style={{ display: 'inline-flex' }}>
      <input
        type="text" value={value} autoFocus placeholder={placeholder}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') submit();
          if (e.key === 'Escape') setOpen(false);
        }}
        style={{
          minWidth: 180, padding: '5px 10px', fontSize: 13,
          borderRadius: 'var(--r-sm)', border: '1px solid var(--line-strong)',
          background: 'var(--surface)', color: 'var(--text)',
        }}
      />
      <Button variant="primary" size={size} onClick={submit}>{submitLabel}</Button>
      <Button size={size} variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
    </span>
  );
}

export function InlineEdit({
  value, onSave, label = 'Edit value', placeholder = '', type = 'text', size = '',
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);

  const commit = async () => {
    setEditing(false);
    if (draft !== value) await onSave?.(draft);
  };

  if (!editing) {
    return (
      <button
        type="button"
        className="flex items-center gap-1.5"
        style={{ color: 'inherit', textAlign: 'left', cursor: 'pointer', background: 'none', border: 'none' }}
        onClick={() => setEditing(true)}
        title={label}
      >
        <span>{value || placeholder || '—'}</span>
        <Icon name="edit" size={12} style={{ opacity: 0.5 }} />
      </button>
    );
  }

  return (
    <input
      type={type}
      value={draft ?? ''}
      autoFocus
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') commit();
        if (e.key === 'Escape') { setDraft(value); setEditing(false); }
      }}
      style={{
        padding: '3px 8px', fontSize: 13, borderRadius: 'var(--r-xs)',
        border: '1px solid var(--accent)', background: 'var(--surface)', color: 'var(--text)',
      }}
    />
  );
}

/* ═══════════════════════════════════════════════════════ Inputs & Controls ═══════ */

export function Segmented({ options = [], value, onChange, ariaLabel, className = '' }) {
  return (
    <div className={`segmented-control ${className}`} role="tablist" aria-label={ariaLabel} style={{
      display: 'inline-flex', gap: 2, padding: 3, borderRadius: 'var(--r-sm)',
      background: 'var(--surface-2)', border: '1px solid var(--line)',
      maxWidth: '100%', overflowX: 'auto', flexShrink: 0, scrollbarWidth: 'none',
    }}>
      {options.map(([v, label, title]) => {
        const isActive = value === v;
        return (
          <button
            key={v}
            type="button"
            role="tab"
            aria-selected={isActive}
            title={title}
            onClick={() => onChange(v)}
            style={{
              padding: '4px 10px', borderRadius: 'var(--r-xs)',
              fontSize: 12.5, fontWeight: isActive ? 600 : 500,
              background: isActive ? 'var(--surface)' : 'transparent',
              color: isActive ? 'var(--accent)' : 'var(--text-2)',
              border: 'none', cursor: 'pointer',
              boxShadow: isActive ? 'var(--shadow-sm)' : 'none',
              transition: 'all var(--t-fast)',
            }}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

export function Toggle({ on, children, ...rest }) {
  return (
    <button
      type="button"
      className={`btn ${on ? 'btn-primary' : 'btn-secondary'} btn-sm`}
      aria-pressed={on}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Select({ options = [], value, onChange, size = '', placeholder, disabled = false, style = {}, ...rest }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
      disabled={disabled}
      style={{
        padding: size === 'sm' ? '4px 8px' : '7px 12px',
        fontSize: size === 'sm' ? 12 : 13.5,
        borderRadius: 'var(--r-sm)',
        border: '1px solid var(--line-strong)',
        background: 'var(--surface)',
        color: 'var(--text)',
        cursor: 'pointer',
        ...style,
      }}
      {...rest}
    >
      {placeholder && <option value="">{placeholder}</option>}
      {options.map(([val, lbl]) => (
        <option key={val} value={val}>{lbl}</option>
      ))}
    </select>
  );
}

export function Field({ label, hint, error, children, id: given }) {
  const auto = useId();
  const id = given || auto;
  const child = React.isValidElement(children)
    ? React.cloneElement(children, { id, 'aria-describedby': hint ? `${id}-hint` : undefined })
    : children;
  return (
    <label className="field" htmlFor={id} style={{ display: 'flex', flexDirection: 'column', gap: 6, margin: '8px 0' }}>
      {label && <span className="field-label" style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>{label}</span>}
      {child}
      {hint && <span className="field-hint" id={`${id}-hint`} style={{ fontSize: 12, color: 'var(--text-3)' }}>{hint}</span>}
      {error && <span className="field-hint" style={{ fontSize: 12, color: 'var(--neg)' }}>{error}</span>}
    </label>
  );
}

export function Search({ value, onChange, placeholder = 'Search…', ...rest }) {
  return (
    <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', width: '100%' }}>
      <span style={{ position: 'absolute', left: 10, color: 'var(--text-3)', pointerEvents: 'none', display: 'flex' }}>
        <Icon name="search" size={14} />
      </span>
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          width: '100%', padding: '7px 12px 7px 32px', fontSize: 13,
          borderRadius: 'var(--r-sm)', border: '1px solid var(--line-strong)',
          background: 'var(--surface)', color: 'var(--text)', outline: 'none',
        }}
        {...rest}
      />
    </div>
  );
}

export function Switch({ checked, onChange, label, disabled = false }) {
  return (
    <label style={{ display: 'inline-flex', alignItems: 'center', gap: 10, cursor: disabled ? 'not-allowed' : 'pointer', userSelect: 'none' }}>
      <input
        type="checkbox"
        className="sr-only"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange?.(e.target.checked)}
      />
      <div style={{
        width: 38, height: 22, borderRadius: 'var(--r-full)',
        background: checked ? 'var(--accent)' : 'var(--line-strong)',
        position: 'relative', transition: 'background var(--t-fast)',
        opacity: disabled ? 0.5 : 1,
      }}>
        <div style={{
          width: 16, height: 16, borderRadius: '50%', background: '#ffffff',
          position: 'absolute', top: 3, left: checked ? 19 : 3,
          transition: 'left var(--t-fast) var(--e-out)',
        }} />
      </div>
      {label && <span style={{ fontSize: 13.5, color: 'var(--text)' }}>{label}</span>}
    </label>
  );
}

export function Tabs({ tabs = [], active, onChange }) {
  return (
    <div style={{
      display: 'inline-flex', gap: 4, padding: 4, borderRadius: 'var(--r)',
      background: 'var(--surface-2)', border: '1px solid var(--line)',
    }}>
      {tabs.map(([key, label, count]) => {
        const isActive = active === key;
        return (
          <button
            key={key}
            type="button"
            onClick={() => onChange(key)}
            style={{
              padding: '6px 14px', borderRadius: 'var(--r-sm)',
              fontSize: 13, fontWeight: isActive ? 600 : 500,
              background: isActive ? 'var(--surface)' : 'transparent',
              color: isActive ? 'var(--accent)' : 'var(--text-2)',
              border: 'none', cursor: 'pointer', display: 'inline-flex',
              alignItems: 'center', gap: 6,
              boxShadow: isActive ? 'var(--shadow-sm)' : 'none',
              transition: 'all var(--t-fast)',
            }}
          >
            <span>{label}</span>
            {count != null && (
              <span style={{
                fontSize: 11, padding: '1px 6px', borderRadius: 'var(--r-full)',
                background: isActive ? 'var(--accent-soft)' : 'var(--surface-3)',
                color: isActive ? 'var(--accent)' : 'var(--text-3)',
              }}>
                {count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export function ProgressBar({ value = 0, tall = false, tone = '' }) {
  const clamp = Math.max(0, Math.min(100, Number(value) || 0));
  const fillBg = tone === 'pos' ? 'var(--pos)'
    : tone === 'neg' ? 'var(--neg)'
      : tone === 'warn' ? 'var(--warn)'
        : 'var(--accent)';

  return (
    <div style={{
      width: '100%', height: tall ? 8 : 5, borderRadius: 'var(--r-full)',
      background: 'var(--surface-3)', overflow: 'hidden',
    }}>
      <div style={{
        width: `${clamp}%`, height: '100%', background: fillBg,
        borderRadius: 'var(--r-full)', transition: 'width var(--t-slow) var(--e-out)',
      }} />
    </div>
  );
}

export function Hint({ text }) {
  return (
    <span style={{ display: 'inline-flex', marginLeft: 5, color: 'var(--text-3)', verticalAlign: 'middle', cursor: 'help' }} title={text}>
      <Icon name="question" size={13} />
    </span>
  );
}

/* ═══════════════════════════════════════════════════════ Dialogs & Modals ═══════ */

export function Modal({
  open, onClose, title, sub, subtitle, tools, footer, children, width, size = 'md',
}) {
  useEffect(() => {
    const onEsc = (e) => { if (e.key === 'Escape') onClose?.(); };
    if (open) window.addEventListener('keydown', onEsc);
    return () => window.removeEventListener('keydown', onEsc);
  }, [open, onClose]);

  if (!open) return null;

  const maxWidth = width || (size === 'sm' ? 440 : size === 'lg' ? 760 : size === 'xl' ? 960 : 540);
  const subText = sub || subtitle;

  return createPortal(
    <div
      className="drawer-backdrop"
      onClick={onClose}
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20, zIndex: 1000,
      }}
    >
      <div
        className="card"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '100%', maxWidth, maxHeight: '90vh',
          display: 'flex', flexDirection: 'column',
          boxShadow: 'var(--shadow-3)', animation: 'scaleIn var(--t-fast) var(--e-out)',
          overflow: 'hidden',
        }}
      >
        <div className="card-head" style={{ borderBottom: '1px solid var(--line)', padding: '14px 18px' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h3 className="card-title" style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>{title}</h3>
            {subText && <div className="card-sub" style={{ fontSize: 12.5, color: 'var(--text-3)', marginTop: 2 }}>{subText}</div>}
          </div>
          <div className="flex items-center gap-2">
            {tools}
            <IconButton icon="x" label="Close" onClick={onClose} />
          </div>
        </div>
        <div className="card-body" style={{ overflowY: 'auto', flex: 1, padding: '16px 20px' }}>
          {children}
        </div>
        {footer && (
          <div
            className="flex items-center justify-end gap-2"
            style={{
              padding: '12px 18px', borderTop: '1px solid var(--line)', background: 'var(--surface-2)',
            }}
          >
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}


export function Sheet({ open, onClose, title, subtitle, tools, children, width = 640 }) {
  useEffect(() => {
    const onEsc = (e) => { if (e.key === 'Escape') onClose?.(); };
    if (open) window.addEventListener('keydown', onEsc);
    return () => window.removeEventListener('keydown', onEsc);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="drawer-backdrop" onClick={onClose}>
      <div
        className="drawer-panel"
        style={{ maxWidth: width }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="drawer-head">
          <div>
            <h3 style={{ fontSize: 17, fontWeight: 700 }}>{title}</h3>
            {subtitle && <div style={{ fontSize: 13, color: 'var(--text-3)', marginTop: 2 }}>{subtitle}</div>}
          </div>
          <div className="flex items-center gap-2">
            {tools}
            <IconButton icon="x" label="Close" onClick={onClose} />
          </div>
        </div>
        <div className="drawer-body">
          {children}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ═══════════════════════════════════════════════════════ Table & Skeleton ═══════ */

export function Table({ columns = [], rows = [], keyField = 'id', compact = false, onRowClick }) {
  return (
    <div className="table-wrapper">
      <table className={`terminal-table ${compact ? 'compact' : ''}`}>
        <thead>
          <tr>
            {columns.map(([key, label, width, align]) => (
              <th key={key} style={{ width, textAlign: align || 'left' }}>{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row[keyField] || JSON.stringify(row)}
              onClick={() => onRowClick?.(row)}
              style={{ cursor: onRowClick ? 'pointer' : 'default' }}
            >
              {columns.map(([key, , , align]) => (
                <td key={key} style={{ textAlign: align || 'left' }}>
                  {typeof key === 'function' ? key(row) : row[key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Skeleton({ lines = 4, height = 18 }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="skeleton-shimmer"
          style={{
            height,
            borderRadius: 'var(--r-xs)',
            width: i === lines - 1 ? '60%' : '100%',
          }}
        />
      ))}
    </div>
  );
}

export function SkeletonStats() {
  return (
    <div className="grid-4" style={{ marginBottom: 20 }}>
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="stat-widget">
          <Skeleton lines={2} height={24} />
        </div>
      ))}
    </div>
  );
}

export function BarList({ items = [], max }) {
  const topVal = max || Math.max(...items.map((i) => Math.abs(Number(i.value) || 0)), 1);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {items.map((item, idx) => {
        const ratio = Math.min(100, (Math.abs(Number(item.value) || 0) / topVal) * 100);
        return (
          <div key={item.label || idx} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div className="flex items-center justify-between text-xs">
              <span className="font-semibold truncate" style={{ maxWidth: 220 }}>{item.label}</span>
              <span className="tabular-nums font-bold">{money(item.value)}</span>
            </div>
            <div style={{
              width: '100%', height: 6, borderRadius: 'var(--r-full)',
              background: 'var(--surface-3)', overflow: 'hidden',
            }}>
              <div style={{
                width: `${ratio}%`, height: '100%',
                background: item.color || `var(--c${(idx % 12) + 1})`,
                borderRadius: 'var(--r-full)',
              }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function Legend({ items = [] }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, fontSize: 12, margin: '8px 0' }}>
      {items.map((item, idx) => (
        <div key={item.label || idx} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{
            width: 9, height: 9, borderRadius: '50%',
            background: item.color || `var(--c${(idx % 12) + 1})`,
            flexShrink: 0,
          }} />
          <span style={{ color: 'var(--text-2)' }}>{item.label}</span>
          {item.value && <span className="tabular-nums font-semibold" style={{ color: 'var(--text)' }}>{item.value}</span>}
        </div>
      ))}
    </div>
  );
}

export function SortHeader({ label, field, sort, onSort, align = 'left' }) {
  const isSorted = sort?.key === field;
  const isAsc = isSorted && sort?.dir === 'asc';
  return (
    <th style={{ textAlign: align }}>
      <button
        type="button"
        onClick={() => onSort?.(field)}
        style={{
          background: 'none', border: 'none', color: isSorted ? 'var(--accent)' : 'inherit',
          cursor: 'pointer', font: 'inherit', fontWeight: isSorted ? 700 : 600,
          display: 'inline-flex', alignItems: 'center', gap: 4,
          padding: 0,
        }}
      >
        <span>{label}</span>
        {isSorted && (
          <Icon name={isAsc ? 'arrow-up' : 'arrow-down'} size={12} />
        )}
      </button>
    </th>
  );
}

export function useSorted(items = [], initial = { key: '', dir: 'asc' }) {
  const [sortState, setSortState] = useState(initial);
  const by = useCallback((key) => {
    setSortState((prev) => ({
      key,
      dir: prev.key === key && prev.dir === 'asc' ? 'desc' : 'asc',
    }));
  }, []);
  const sorted = useMemo(() => {
    if (!sortState.key) return items;
    return [...(items || [])].sort((a, b) => {
      const va = a?.[sortState.key];
      const vb = b?.[sortState.key];
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === 'number' && typeof vb === 'number') {
        return sortState.dir === 'asc' ? va - vb : vb - va;
      }
      const sa = String(va).toLowerCase();
      const sb = String(vb).toLowerCase();
      return sortState.dir === 'asc' ? sa.localeCompare(sb) : sb.localeCompare(sa);
    });
  }, [items, sortState]);
  return { sorted, sort: sortState, by };
}

