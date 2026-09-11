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

/* Tone names accepted everywhere: pos | neg | warn | acc/accent/brand. */
function toneClass(prefix, tone) {
  if (tone === 'pos' || tone === 'neg' || tone === 'warn') return `${prefix}-${tone}`;
  if (tone === 'acc' || tone === 'accent' || tone === 'brand') return `${prefix}-accent`;
  return '';
}

export function Chip({ tone = '', size, children, className = '', ...rest }) {
  return (
    <span className={`chip ${toneClass('chip', tone)} ${size === 'sm' ? 'tiny' : ''} ${className}`} {...rest}>
      {children}
    </span>
  );
}

export function Badge({ tone = '', size, children, className = '', ...rest }) {
  return (
    <span className={`badge ${toneClass('badge', tone)} ${size === 'sm' ? 'tiny' : ''} ${className}`} {...rest}>
      {children}
    </span>
  );
}

const CALLOUT_ICON = {
  pos: 'check-circle', neg: 'alert', warn: 'warning', acc: 'info', info: 'info',
};

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

export function Loading({ message, label }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12, padding: '40px 0' }}>
      <Spinner />
      <span style={{ fontSize: 13.5, color: 'var(--text-2)' }}>{message ?? label ?? 'Loading…'}</span>
    </div>
  );
}

export function Spinner({ sm = false, size }) {
  const sz = size || (sm ? 14 : 20);
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
        : variant === 'link' ? 'link'
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

/* `type="money"` and `type="number"` commit a Number (or null when blank) so
   numeric API fields never receive a string; every other type commits text. */
const NUMERIC_EDIT = new Set(['money', 'number']);

export function InlineEdit({
  value, onSave, label = 'Edit value', placeholder = '', type = 'text',
  width, align = 'right', suffix = '',
}) {
  const numeric = NUMERIC_EDIT.has(type);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value ?? '');
  useEffect(() => setDraft(value ?? ''), [value]);

  const commit = async () => {
    setEditing(false);
    const next = numeric
      ? (String(draft).trim() === '' ? null : Number(draft))
      : draft;
    if (numeric && next !== null && Number.isNaN(next)) return;
    if (next !== value) await onSave?.(next);
  };

  const shown = value === null || value === undefined || value === ''
    ? (placeholder || '—')
    : (type === 'money' ? money(value) : String(value));

  if (!editing) {
    return (
      <button
        type="button"
        className="flex items-center gap-1"
        style={{
          color: 'inherit',
          textAlign: align,
          justifyContent: align === 'right' ? 'flex-end' : 'flex-start',
          width: width ? `${width}px` : undefined,
          maxWidth: '100%',
          cursor: 'pointer',
          background: 'none',
          border: 'none',
          padding: 0,
          font: 'inherit',
        }}
        onClick={() => setEditing(true)}
        title={label}
      >
        <span className={numeric ? 'tabular-nums' : ''}>{shown}{suffix && value != null && value !== '' ? suffix : ''}</span>
        <Icon name="edit" size={12} style={{ opacity: 0.4, flexShrink: 0 }} />
      </button>
    );
  }

  return (
    <input
      type={numeric ? 'number' : type}
      step={type === 'money' ? '0.01' : undefined}
      value={draft ?? ''}
      autoFocus
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') commit();
        if (e.key === 'Escape') { setDraft(value ?? ''); setEditing(false); }
      }}
      style={{
        padding: '3px 8px', fontSize: 13, borderRadius: 'var(--r-xs)',
        width: width ? `${width}px` : undefined, maxWidth: '100%',
        textAlign: align,
        border: '1px solid var(--accent)', background: 'var(--surface)', color: 'var(--text)',
      }}
    />
  );
}

/* ═══════════════════════════════════════════════════════ Inputs & Controls ═══════ */

export function Segmented({ options = [], value, onChange, ariaLabel, className = '' }) {
  return (
    <div className={`segmented-control ${className}`} role="tablist" aria-label={ariaLabel} style={{
      display: 'flex', gap: 2, padding: 3, borderRadius: 'var(--r-sm)',
      background: 'var(--surface-2)', border: '1px solid var(--line)',
      /* `inline-flex` + `flexShrink: 0` meant a long option set pushed its
         whole row past the viewport instead of scrolling inside its own box.
         `minWidth: 0` is what actually lets a flex child shrink below its
         content width, which is the precondition for the scroll to engage. */
      maxWidth: '100%', minWidth: 0, overflowX: 'auto', scrollbarWidth: 'none',
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
              flexShrink: 0, whiteSpace: 'nowrap',
              fontSize: 12.5, fontWeight: isActive ? 600 : 500,
              background: isActive ? 'var(--surface)' : 'transparent',
              color: isActive ? 'var(--accent-text)' : 'var(--text-2)',
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

/* Vertical padding and font per size. `xs` exists because six call sites
   were each writing `style={{ fontSize: 11, height: 26 }}` by hand, and a
   height set from outside cannot know what padding the component will add
   underneath it - see the clamp below for what that cost. */
const SELECT_SIZES = {
  xs: { padY: 3, padX: 8, font: 11 },
  sm: { padY: 4, padX: 8, font: 12 },
  '': { padY: 7, padX: 12, font: 13.5 },
};

export function Select({ options = [], value, onChange, size = '', placeholder, disabled = false, style = {}, ...rest }) {
  const scale = SELECT_SIZES[size] || SELECT_SIZES[''];
  let { padY } = scale;
  const font = style.fontSize ?? scale.font;

  /* A caller-supplied height wins, but it cannot be allowed to crush the
     text inside it. Everything here is `box-sizing: border-box`, so a fixed
     26px against 7px of padding top and bottom plus 1px borders leaves a
     10px content box for an 11px font - and the glyphs are clipped top and
     bottom. That is exactly what the two dropdowns on the import wizard's
     Source step were doing: measured, they needed 29.2px and were given 26.
     Rather than ignore the height or let it clip, shrink the padding to
     whatever actually fits and keep at least a hairline of it. */
  if (style.height != null && style.padding == null) {
    const box = parseFloat(style.height);
    if (Number.isFinite(box)) {
      const lineBox = Math.ceil(font * 1.2);
      padY = Math.max(1, Math.floor((box - lineBox - 2) / 2));
    }
  }

  return (
    <select
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
      disabled={disabled}
      style={{
        padding: `${padY}px ${scale.padX}px`,
        fontSize: font,
        // Pinned, not inherited. `base.css` sets `font: inherit` on every
        // select, and the `font` shorthand carries line-height with it - so
        // a select dropped into a tight flex row inherited that row's
        // leading instead of its own.
        lineHeight: 1.2,
        borderRadius: 'var(--r-sm)',
        border: '1px solid var(--line-strong)',
        background: 'var(--surface)',
        color: 'var(--text)',
        cursor: 'pointer',
        ...style,
        // After the spread: a caller's `height` is honoured, but the padding
        // computed above must not be overwritten by the shorthand it came in
        // with. Only an explicit `padding` from the caller replaces it.
        padding: style.padding ?? `${padY}px ${scale.padX}px`,
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

export function Search({ value, onChange, placeholder = 'Search…', style = {}, ...rest }) {
  /* `style` sizes the wrapper (callers pass width/flex); the input keeps its own
     chrome so an incoming style object can never strip its border and padding. */
  return (
    <div style={{
      position: 'relative', display: 'inline-flex', alignItems: 'center', width: '100%', ...style,
    }}>
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
              color: isActive ? 'var(--accent-text)' : 'var(--text-2)',
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

export function SkeletonStats({ count = 4 }) {
  return (
    <div className={count === 3 ? 'grid-3' : 'grid-4'} style={{ marginBottom: 20 }}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="stat-widget">
          <Skeleton lines={2} height={24} />
        </div>
      ))}
    </div>
  );
}

/* `max` caps how many rows render; bars are always scaled against the largest
   value present (or `total`, when the caller wants share-of-whole widths).
   Passing `onPick` makes each row a button that drills into its rows. */
export function BarList({ items = [], max, total, onPick }) {
  const shown = max ? items.slice(0, max) : items;
  const scale = Number(total) > 0
    ? Number(total)
    : shown.reduce((top, i) => Math.max(top, Math.abs(Number(i.value) || 0)), 1);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {shown.map((item, idx) => {
        const ratio = Math.min(100, (Math.abs(Number(item.value) || 0) / (scale || 1)) * 100);
        const body = (
          <>
            <div className="flex items-center justify-between text-xs" style={{ gap: 12 }}>
              <span className="font-semibold truncate" style={{ maxWidth: 240 }}>{item.label}</span>
              <span className="tabular-nums font-bold">{money(item.value)}</span>
            </div>
            <div style={{
              width: '100%', height: 6, borderRadius: 'var(--r-full)',
              background: 'var(--surface-3)', overflow: 'hidden', marginTop: 4,
            }}>
              <div style={{
                width: `${ratio}%`, height: '100%',
                background: item.color || `var(--c${(idx % 12) + 1})`,
                borderRadius: 'var(--r-full)',
              }} />
            </div>
          </>
        );

        if (!onPick) {
          return <div key={item.label || idx}>{body}</div>;
        }
        return (
          <button
            key={item.label || idx}
            type="button"
            onClick={() => onPick(item)}
            title="Show the transactions behind this"
            style={{
              display: 'block', width: '100%', textAlign: 'left', cursor: 'pointer',
              background: 'none', border: 'none', padding: 0, color: 'inherit', font: 'inherit',
            }}
          >
            {body}
          </button>
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
          background: 'none', border: 'none', color: isSorted ? 'var(--accent-text)' : 'inherit',
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

