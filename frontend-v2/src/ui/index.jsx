/* The design system.
 *
 * Every control the app uses, in one module, so a button is invented once. The
 * rules live in styles/app.css; what is here is the markup, the accessibility,
 * and the handful of behaviours that are genuinely part of a component rather
 * than of a screen - a dialog's focus trap, a popover's outside-click, a
 * confirmation that does not depend on the browser granting us `confirm()`.
 */

import React, {
  useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState,
} from 'react';
import { createPortal } from 'react-dom';
import { Icon } from './icons';
import { compact, money, titleCase } from '../core/format';

/* ═══════════════════════════════════════════════════════ surfaces ═══════ */

export function Card({ title, sub, tools, children, className = '', pad = true, ...rest }) {
  return (
    <div className={`card ${className}`} {...rest}>
      {(title || sub || tools) && (
        <div className="card-head">
          <div className="grow">
            {title && <div className="card-title">{title}</div>}
            {sub && <div className="card-sub">{sub}</div>}
          </div>
          {tools && <div className="card-tools">{tools}</div>}
        </div>
      )}
      {pad ? <div className="card-body">{children}</div> : children}
    </div>
  );
}

export function Section({ title, note, children, actions }) {
  return (
    <>
      <div className="section">
        <span className="section-title">{title}</span>
        {note && <span className="section-note">{note}</span>}
        {actions && <span className="section-actions">{actions}</span>}
      </div>
      {children}
    </>
  );
}

/**
 * A headline figure.
 *
 * `onDrill` makes the figure itself open the rows behind it - which is the
 * question anybody asks of a number they did not expect, and the reason it is
 * a button rather than a div with a click handler.
 */
export function Stat({
  label, value, note, tone, precise = false, onDrill, drillTitle, hint, small,
}) {
  const shown = typeof value === 'number' ? money(value, precise) : value;
  const toneClass = tone === 'pos' ? 'pos-tone' : tone === 'neg' ? 'neg-tone'
    : tone === 'warn' ? 'warn-tone' : tone === 'accent' ? 'acc-tone' : '';
  const valueClass = tone === 'pos' ? 'pos' : tone === 'neg' ? 'neg' : '';
  return (
    <div className={`stat ${toneClass}`}>
      <div className="stat-label">
        {label}
        {hint && <Hint text={hint} />}
      </div>
      <div className={`stat-value ${small ? 'sm' : ''} ${valueClass}`}>
        {onDrill ? (
          <button type="button" className="drill" onClick={onDrill}
            title={drillTitle || 'Show the transactions behind this'}>
            {shown}
          </button>
        ) : shown}
      </div>
      {note && <div className="stat-note">{note}</div>}
    </div>
  );
}

export function Chip({ tone = '', children, className = '', ...rest }) {
  return <span className={`chip ${tone} ${className}`} {...rest}>{children}</span>;
}

const CALLOUT_ICON = { pos: 'check-circle', neg: 'alert', warn: 'warning', acc: 'info' };

export function Callout({ tone = '', children, icon, className = '', ...rest }) {
  const name = icon ?? CALLOUT_ICON[tone];
  return (
    <div className={`callout ${tone} ${className}`} {...rest}>
      {name && <Icon name={name} size={15} />}
      <div>{children}</div>
    </div>
  );
}

export function Empty({ title, icon = 'inbox', children, action }) {
  return (
    <div className="empty">
      <span className="empty-mark"><Icon name={icon} size={19} /></span>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action && <div style={{ marginTop: 8 }}>{action}</div>}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════ controls ══════ */

/* Forwards its ref, because `Menu` anchors its popover to whatever opened it.
   Without this the ref is silently dropped and the popover has no rect to
   position against - it sits off-screen at -9999 and looks like a dead
   button. */
export const Button = React.forwardRef(function Button({
  children, variant = '', size = '', icon, iconRight, className = '', busy, ...rest
}, ref) {
  return (
    <button
      ref={ref}
      type="button"
      className={`btn ${variant} ${size} ${className}`.trim()}
      {...rest}
      disabled={rest.disabled || busy}
    >
      {busy ? <span className="spinner sm" /> : icon && <Icon name={icon} size={14} />}
      {children}
      {iconRight && <Icon name={iconRight} size={14} />}
    </button>
  );
});

export const IconButton = React.forwardRef(function IconButton(
  { icon, label, size = '', className = '', ...rest }, ref,
) {
  return (
    <button ref={ref} type="button" className={`btn icon ${size} ${className}`.trim()}
      aria-label={label} title={rest.title || label} {...rest}>
      <Icon name={icon} size={size === 'xs' ? 12 : 14} />
    </button>
  );
});

/** One choice from a few. `options` is `[[value, label, title?], …]`. */
export function Segmented({ options, value, onChange, ariaLabel, className = '' }) {
  return (
    <div className={`seg ${className}`} role="tablist" aria-label={ariaLabel}>
      {options.map(([v, label, title]) => (
        <button
          key={v}
          type="button"
          role="tab"
          aria-selected={value === v}
          title={title}
          className={`seg-btn ${value === v ? 'active' : ''}`}
          onClick={() => onChange(v)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({ on, children, ...rest }) {
  return (
    <button type="button" className={`tog ${on ? 'on' : ''}`}
      aria-pressed={on} {...rest}>
      {children}
    </button>
  );
}

export function Switch({ checked, onChange, label, disabled }) {
  return (
    <button
      type="button"
      role="switch"
      className="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    />
  );
}

export function Field({ label, hint, error, children, id: given }) {
  const auto = useId();
  const id = given || auto;
  const child = React.isValidElement(children)
    ? React.cloneElement(children, { id, 'aria-describedby': hint ? `${id}-hint` : undefined })
    : children;
  return (
    <label className="field" htmlFor={id}>
      <span className="field-label">{label}</span>
      {child}
      {hint && <span className="field-hint" id={`${id}-hint`}>{hint}</span>}
      {error && <span className="field-hint" style={{ color: 'var(--neg)' }}>{error}</span>}
    </label>
  );
}

export function Select({ options, value, onChange, placeholder, ...rest }) {
  return (
    <select value={value ?? ''} onChange={(e) => onChange(e.target.value)} {...rest}>
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((o) => {
        const [v, label] = Array.isArray(o) ? o : [o, titleCase(o)];
        return <option key={v} value={v}>{label}</option>;
      })}
    </select>
  );
}

export function Search({ value, onChange, placeholder = 'Search…', ...rest }) {
  return (
    <input
      type="search"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      {...rest}
    />
  );
}

/**
 * Confirmation that does not depend on the browser granting us a dialog.
 *
 * `window.confirm` is suppressed in embedded and app-hosted browsers: it
 * returns false immediately without showing anything, so a handler written as
 * `if (!confirm(...)) return;` becomes a button that silently does nothing,
 * with no error to find. Asking in the page costs one extra click and always
 * works.
 */
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
          // Disarms itself: a button left sitting in "are you sure" is a trap
          // for whoever comes back to the tab later.
          clearTimeout(timer.current);
          timer.current = setTimeout(() => setArmed(false), timeout);
        }}>
        {children}
      </Button>
    );
  }

  return (
    <span className="row tight" style={{ display: 'inline-flex' }}>
      {question && <span className="tiny dim">{question}</span>}
      <Button variant="danger solid" size={size} disabled={rest.disabled}
        onClick={async () => {
          clearTimeout(timer.current);
          setArmed(false);
          await onConfirm?.();
        }}>
        {confirmLabel}
      </Button>
      <Button size={size} onClick={() => { clearTimeout(timer.current); setArmed(false); }}>
        Cancel
      </Button>
    </span>
  );
}

/** The same problem for `window.prompt`, which is suppressed identically. */
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
    <span className="row tight" style={{ display: 'inline-flex' }}>
      <input
        type="text" value={value} autoFocus placeholder={placeholder}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') submit();
          if (e.key === 'Escape') setOpen(false);
        }}
        style={{ minWidth: 190 }}
      />
      <Button variant="primary" size={size} onClick={submit}>{submitLabel}</Button>
      <Button size={size} onClick={() => setOpen(false)}>Cancel</Button>
    </span>
  );
}

/**
 * A value corrected in place.
 *
 * Committed on blur and on Enter, abandoned on Escape - never on every
 * keystroke: a partial number is a real number to the server, and saving "40"
 * on the way to "400000" would briefly make the figure wrong and, worse,
 * re-derive things from it.
 */
export function InlineEdit({
  value, onSave, type = 'text', suffix = '', placeholder = '—', width = 110,
  align = 'right', title = 'Click to edit',
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  const commit = () => {
    setEditing(false);
    const next = draft.trim();
    const before = value == null ? '' : String(value);
    if (next === before) return;
    onSave(next === '' ? null : next);
  };

  if (editing) {
    return (
      <input
        autoFocus
        type={type === 'date' ? 'date' : 'text'}
        inputMode={type === 'number' || type === 'money' ? 'decimal' : undefined}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit();
          if (e.key === 'Escape') setEditing(false);
        }}
        style={{ width, textAlign: align, height: 26, fontSize: 13 }}
      />
    );
  }

  const empty = value == null || value === '';
  const shown = empty ? placeholder
    : type === 'money' ? money(Number(value))
      : type === 'date' ? new Date(value).toLocaleDateString('en-IN',
        { day: '2-digit', month: 'short', year: '2-digit' })
        : `${value}${suffix}`;

  return (
    <button
      type="button"
      className={`inline-edit ${empty ? 'empty' : ''}`}
      title={title}
      style={{ width, textAlign: align }}
      onClick={() => { setDraft(value == null ? '' : String(value)); setEditing(true); }}
    >
      {shown}
    </button>
  );
}

/* ═══════════════════════════════════════════════════════ feedback ══════ */

export function Spinner({ sm }) { return <span className={`spinner ${sm ? 'sm' : ''}`} />; }

export function Loading({ label = 'Loading…', pad = 30 }) {
  return (
    <div className="row" style={{ padding: pad, color: 'var(--text-2)' }}>
      <Spinner /> {label}
    </div>
  );
}

/** Skeletons, so a first load has the SHAPE of the answer rather than a hole. */
export function Skeleton({ lines = 3, className = '' }) {
  return (
    <div className={className}>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skel skel-line" style={{ width: `${92 - i * 13}%` }} />
      ))}
    </div>
  );
}

export function SkeletonStats({ n = 4 }) {
  return (
    <div className="grid cols-4">
      {Array.from({ length: n }, (_, i) => <div key={i} className="skel skel-card" />)}
    </div>
  );
}

export function ProgressBar({ value, tone = '', tall }) {
  return (
    <div className={`bar ${tall ? 'tall' : ''}`}>
      <span style={{
        width: `${Math.max(0, Math.min(100, value || 0))}%`,
        background: tone === 'neg' ? 'var(--neg)' : tone === 'pos' ? 'var(--pos)' : 'var(--accent)',
      }} />
    </div>
  );
}

/** A small "why?" affordance beside a label. Native title, deliberately - it
    is the one tooltip that works on every platform and costs nothing. */
export function Hint({ text }) {
  return (
    <span title={text} style={{ display: 'inline-flex', color: 'var(--text-3)', cursor: 'help' }}>
      <Icon name="info" size={12} />
    </span>
  );
}

/* ═══════════════════════════════════════════════════════ overlays ══════ */

/* Focus trap + Escape + scroll lock, shared by every dialog and sheet. Written
   once because getting it wrong is how a modal becomes unusable by keyboard,
   and because three copies of it drift. */
function useDialog(onClose, ref) {
  useEffect(() => {
    const previous = document.activeElement;
    const lock = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const onKey = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose?.(); return; }
      if (e.key !== 'Tab' || !ref.current) return;
      const focusable = ref.current.querySelectorAll(
        'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),'
        + 'textarea:not([disabled]),[tabindex]:not([tabindex="-1"])');
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey, true);

    // Focus the panel itself rather than its first control: focusing an input
    // pops a mobile keyboard over a dialog somebody has not read yet.
    const t = setTimeout(() => ref.current?.focus?.(), 0);

    return () => {
      clearTimeout(t);
      document.removeEventListener('keydown', onKey, true);
      document.body.style.overflow = lock;
      if (previous instanceof HTMLElement) previous.focus?.();
    };
  }, [onClose, ref]);
}

export function Modal({
  title, sub, children, onClose, footer, size = '', tools, bodyClass = '',
}) {
  const ref = useRef(null);
  useDialog(onClose, ref);
  return createPortal(
    <div className="scrim center"
      onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
      <div className={`modal ${size}`} role="dialog" aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
        tabIndex={-1} ref={ref}>
        <div className="modal-head">
          <div className="grow" style={{ minWidth: 0 }}>
            <div className="modal-title">{title}</div>
            {sub && <div className="modal-sub">{sub}</div>}
          </div>
          {tools}
          <IconButton icon="x" label="Close" className="ghost" onClick={onClose} />
        </div>
        <div className={`modal-body ${bodyClass}`}>{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

/** A side sheet: "more about the thing you are already looking at", rather
    than a new context. Used for the drill-down. */
export function Sheet({ title, sub, note, children, onClose, strip, tools }) {
  const ref = useRef(null);
  useDialog(onClose, ref);
  return createPortal(
    <div className="scrim right"
      onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
      <aside className="sheet" role="dialog" aria-modal="true"
        aria-label={typeof title === 'string' ? title : 'Details'}
        tabIndex={-1} ref={ref}>
        <header className="sheet-head">
          <div className="grow" style={{ minWidth: 0, flex: 1 }}>
            <div className="modal-title">{title}</div>
            {sub && <div className="modal-sub">{sub}</div>}
            {note && <div className="small muted" style={{ marginTop: 6 }}>{note}</div>}
          </div>
          {tools}
          <IconButton icon="x" label="Close" className="ghost" onClick={onClose} />
        </header>
        {strip && <div className="sheet-strip">{strip}</div>}
        <div className="sheet-body">{children}</div>
      </aside>
    </div>,
    document.body,
  );
}

/**
 * A popover anchored to whatever opened it.
 *
 * Positioned after mount from the trigger's own rect and flipped when it would
 * leave the viewport, which is the whole reason this is not just an absolutely
 * positioned div: a menu that opens off the bottom of a laptop screen is a
 * menu with items nobody can reach.
 */
export function Popover({ anchorRef, onClose, children, align = 'end', width }) {
  const ref = useRef(null);
  const [pos, setPos] = useState(null);

  useLayoutEffect(() => {
    const a = anchorRef.current;
    const el = ref.current;
    if (!a || !el) return;
    const r = a.getBoundingClientRect();
    const box = el.getBoundingClientRect();
    const margin = 8;
    let left = align === 'end' ? r.right - box.width : r.left;
    left = Math.max(margin, Math.min(left, window.innerWidth - box.width - margin));
    let top = r.bottom + 6;
    if (top + box.height > window.innerHeight - margin) {
      top = Math.max(margin, r.top - box.height - 6);
    }
    setPos({ left, top });
  }, [anchorRef, align]);

  useEffect(() => {
    const away = (e) => {
      if (ref.current?.contains(e.target)) return;
      if (anchorRef.current?.contains(e.target)) return;
      onClose();
    };
    const esc = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', esc);
    window.addEventListener('resize', onClose);
    return () => {
      document.removeEventListener('mousedown', away);
      document.removeEventListener('keydown', esc);
      window.removeEventListener('resize', onClose);
    };
  }, [anchorRef, onClose]);

  return createPortal(
    <div
      className="pop"
      ref={ref}
      role="menu"
      style={{
        position: 'fixed',
        left: pos?.left ?? -9999,
        top: pos?.top ?? -9999,
        width,
        visibility: pos ? 'visible' : 'hidden',
      }}
    >
      {children}
    </div>,
    document.body,
  );
}

/** A popover with its own trigger, for the common case. */
export function Menu({ trigger, children, align = 'end', width }) {
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  const close = useCallback(() => setOpen(false), []);
  return (
    <>
      {React.cloneElement(trigger, {
        ref: anchor,
        onClick: () => setOpen((v) => !v),
        'aria-haspopup': 'menu',
        'aria-expanded': open,
      })}
      {open && (
        <Popover anchorRef={anchor} onClose={close} align={align} width={width}>
          {typeof children === 'function' ? children(close) : children}
        </Popover>
      )}
    </>
  );
}

export function MenuItem({ icon, children, onClick, ...rest }) {
  return (
    <button type="button" className="pop-item" role="menuitem" onClick={onClick} {...rest}>
      {icon && <Icon name={icon} size={14} />}
      {children}
    </button>
  );
}

/* ═══════════════════════════════════════════════════════ data bits ═════ */

/**
 * A horizontal bar list.
 *
 * Preferred over a pie for a category breakdown: people compare bar lengths
 * far more accurately than pie-slice angles, and a pie with fifteen slices is
 * unreadable at any size.
 *
 * The percentage column has to agree with the bars beside it, so the
 * denominator is the OUTGOING side of the same list - every positive item,
 * whether or not it made the visible slice. Taking the caller's net total
 * instead mixed a net denominator with gross numerators, and in a month with
 * real refunds the visible bars read 97%, 44%, 31%, 8%… adding to 194% of a
 * total printed directly above them.
 */
export function BarList({ items, total, max = 12, format = compact, onPick, colorKey = 'color' }) {
  const outgoing = useMemo(() => items.filter((i) => i.value > 0), [items]);
  const shown = outgoing.slice(0, max);
  const peak = Math.max(...shown.map((i) => i.value), 1);
  const basis = outgoing.reduce((s, i) => s + i.value, 0) || total;

  if (!shown.length) {
    return <div className="small dim" style={{ padding: '10px 2px' }}>Nothing to show here.</div>;
  }

  return (
    <div className="barlist">
      {shown.map((item, i) => {
        const inner = (
          <>
            <span className="barrow-label" title={titleCase(item.label)}>
              {titleCase(item.label)}
            </span>
            <span className="barrow-track">
              <span
                className="barrow-fill"
                style={{
                  width: `${Math.max(2, (item.value / peak) * 100)}%`,
                  background: item[colorKey] || `var(--c${(i % 12) + 1})`,
                }}
              />
            </span>
            <span className="barrow-value">{format(item.value)}</span>
            <span className="barrow-pct">
              {basis ? `${((item.value / basis) * 100).toFixed(0)}%` : ''}
            </span>
          </>
        );
        if (!onPick) return <div className="barrow" key={item.label}>{inner}</div>;
        return (
          <button type="button" className="barrow" key={item.label}
            onClick={() => onPick(item)}
            title={`Show the ${titleCase(item.label)} transactions`}>
            {inner}
          </button>
        );
      })}
    </div>
  );
}

export function Legend({ items }) {
  return (
    <div className="legend">
      {items.map((it) => (
        <span className="legend-item" key={it.label}>
          <i className="swatch" style={{ background: it.color }} />
          {it.label}
          {it.value != null && <strong className="num">&nbsp;{it.value}</strong>}
        </span>
      ))}
    </div>
  );
}

/** A sortable table header cell. Ascending on a new column, flipping on the
    current one - anything else means you cannot tell which way it is sorted
    without reading the arrow. */
export function SortHeader({ label, field, sort, onSort, align, title }) {
  const on = sort.key === field;
  return (
    <th className={align === 'right' ? 'right' : ''} title={title}
      aria-sort={on ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type="button" className={`sortable ${on ? 'on' : ''}`} onClick={() => onSort(field)}>
        {label}
        <Icon className="caret" name={on && sort.dir === 'desc' ? 'chevron-down' : 'chevron-up'}
          size={11} />
      </button>
    </th>
  );
}

/** Sorting that works on a column of mixed types. Nulls always sink, whichever
    way the sort runs: a blank credit limit is not "the smallest limit", it is
    a limit nobody has recorded. */
export function useSorted(rows, initial = { key: 'label', dir: 'asc' }) {
  const [sort, setSort] = useState(initial);
  const sorted = useMemo(() => {
    const { key, dir } = sort;
    const sign = dir === 'desc' ? -1 : 1;
    return [...rows].sort((a, b) => {
      const x = a[key];
      const y = b[key];
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      if (typeof x === 'number' && typeof y === 'number') return (x - y) * sign;
      return String(x).localeCompare(String(y), undefined,
        { numeric: true, sensitivity: 'base' }) * sign;
    });
  }, [rows, sort]);
  const by = (key) => setSort((p) => ({
    key, dir: p.key === key && p.dir === 'asc' ? 'desc' : 'asc',
  }));
  return { sorted, sort, by };
}

export function Table({ children, scrollY, className = '', maxHeight }) {
  return (
    <div className={`tbl-wrap ${scrollY ? 'scroll-y' : ''} ${className}`}
      style={maxHeight ? { maxHeight, overflowY: 'auto' } : undefined}>
      <table>{children}</table>
    </div>
  );
}

/** Something is loading, over content that is already there. */
export function Refreshing({ on, label = 'Refreshing…' }) {
  if (!on) return null;
  return (
    <span className="row tight tiny dim" style={{ display: 'inline-flex' }}>
      <Spinner sm /> {label}
    </span>
  );
}

export { Icon };
