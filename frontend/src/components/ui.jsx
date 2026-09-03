import React from 'react';
import { compact, money, titleCase } from '../lib';

export function Card({ title, sub, children, className = '', ...rest }) {
  return (
    <div className={`card ${className}`} {...rest}>
      {(title || sub) && (
        <div className="card-head">
          {title && <div className="card-title">{title}</div>}
          {sub && <div className="card-sub">{sub}</div>}
        </div>
      )}
      {children}
    </div>
  );
}

/* A headline figure. `onDrill` makes the figure itself open the rows behind
   it - the question anyone asks of a number they did not expect. */
export function Stat({ label, value, note, tone, precise = false, onDrill,
  drillTitle }) {
  const isNumber = typeof value === 'number';
  const shown = isNumber ? money(value, precise) : value;
  return (
    <div className={`card stat${onDrill ? ' stat-drill' : ''}`}>
      <div className="stat-label">{label}</div>
      <div className={`stat-value num ${tone || ''}`}>
        {onDrill ? (
          <button type="button" className="drill-link" onClick={onDrill}
            title={drillTitle || 'Show the transactions behind this'}>
            {shown}
          </button>
        ) : shown}
      </div>
      {note && <div className="stat-note">{note}</div>}
    </div>
  );
}

export function Chip({ children, tone = '', className = '', style, ...rest }) {
  return <span className={`chip ${tone} ${className}`} style={style} {...rest}>{children}</span>;
}

export function Empty({ title, children, action }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action && <div style={{ marginTop: 14 }}>{action}</div>}
    </div>
  );
}

export function Callout({ tone = '', children, className = '', style, ...rest }) {
  return <div className={`callout ${tone} ${className}`} style={style} {...rest}>{children}</div>;
}

/* Recharts' default tooltip cannot format Indian currency, so every chart
   shares this one instead. */
export function ChartTooltip({ active, payload, label, formatter = money }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tooltip">
      <div className="tooltip-label">{label}</div>
      {payload.filter((p) => p.value != null).map((p) => (
        <div className="tooltip-row" key={p.dataKey}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span className="dot" style={{ background: p.color || p.stroke || p.fill }} />
            {p.name}
          </span>
          <span>{formatter(p.value)}</span>
        </div>
      ))}
    </div>
  );
}

export const axisProps = {
  tick: { fontSize: 11, fill: 'var(--text-3)' },
  axisLine: { stroke: 'var(--border)' },
  tickLine: false,
};

export const moneyAxis = { ...axisProps, tickFormatter: compact, width: 58 };

/* Horizontal bar list. Preferred over a pie for category breakdowns: humans
   compare bar lengths far more accurately than pie-slice angles, and a pie
   with fifteen categories is unreadable at any size. */
/* `format` exists because this list is no longer only used for money. An
   Explore widget can rank by transaction count, and the default currency
   formatter turned 654 transactions into "₹654". */
/* `onPick` makes each bar open the transactions behind it. A breakdown whose
   rows cannot be opened answers "how much" and refuses to answer "on what",
   which is the next question every single time. */
export function BarList({ items, total, colorKey = 'color', max = 12,
  format = compact, onPick }) {
  const shown = items.slice(0, max);
  const peak = Math.max(...shown.map((i) => i.value), 1);

  return (
    <div>
      {shown.map((item) => {
        const row = (
          <div className="catrow">
            <div className="catrow-label" title={titleCase(item.label)}>
              {titleCase(item.label)}
            </div>
            <div className="catrow-track">
              <div
                className="catrow-fill"
                style={{
                  width: `${Math.max(2, (Math.abs(item.value) / peak) * 100)}%`,
                  background: item[colorKey],
                }}
              />
            </div>
            <div className="catrow-value num">{format(item.value)}</div>
            <div className="catrow-pct num">
              {total ? `${((item.value / total) * 100).toFixed(0)}%` : ''}
            </div>
          </div>
        );
        if (!onPick) return <React.Fragment key={item.label}>{row}</React.Fragment>;
        return (
          <button type="button" className="drill-row" key={item.label}
            onClick={() => onPick(item)}
            title={`Show the ${titleCase(item.label)} transactions`}>
            {row}
          </button>
        );
      })}
    </div>
  );
}

export function ThemeToggle({ theme, onToggle }) {
  return (
    <button className="btn icon" onClick={onToggle} title="Toggle theme" aria-label="Toggle theme">
      {theme === 'dark' ? '☀' : '☾'}
    </button>
  );
}

/* Confirmation that does not depend on the browser granting us a dialog.
 *
 * `window.confirm` is suppressed in embedded and app-hosted browsers: it
 * returns false immediately without ever showing anything, so a handler
 * written as `if (!window.confirm(...)) return;` becomes a button that does
 * nothing at all, silently, with no error to find. Ten controls in this app
 * were dead that way - Start over, Forget, Clear parsed ledger, Stop tracking
 * - and the failure looks exactly like a bug in the thing being confirmed.
 *
 * Asking in the page costs one extra click and always works.
 */
export function ConfirmButton({
  children, onConfirm, confirmLabel = 'Confirm', question,
  className = 'btn', style, disabled, title, timeout = 8000,
}) {
  const [armed, setArmed] = React.useState(false);
  const timer = React.useRef(null);

  React.useEffect(() => () => clearTimeout(timer.current), []);

  const arm = () => {
    setArmed(true);
    // Disarms itself: a button left sitting in "are you sure" is a trap for
    // whoever comes back to the tab later.
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setArmed(false), timeout);
  };

  const go = async () => {
    clearTimeout(timer.current);
    setArmed(false);
    await onConfirm?.();
  };

  if (!armed) {
    return (
      <button type="button" className={className} style={style}
        disabled={disabled} title={title} onClick={arm}>
        {children}
      </button>
    );
  }

  return (
    <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
      {question && (
        <span className="xp-hint" style={{ textTransform: 'none' }}>{question}</span>
      )}
      <button type="button" className={`${className} primary`} style={style}
        disabled={disabled} onClick={go}>
        {confirmLabel}
      </button>
      <button type="button" className={className} style={style}
        onClick={() => { clearTimeout(timer.current); setArmed(false); }}>
        Cancel
      </button>
    </span>
  );
}

/* The same problem for `window.prompt`, which is suppressed identically and
   returns null - so "add a note" quietly did nothing. */
export function PromptButton({
  children, onSubmit, placeholder = '', initial = '',
  className = 'btn', style, disabled, title, submitLabel = 'Save',
}) {
  const [open, setOpen] = React.useState(false);
  const [value, setValue] = React.useState(initial);

  React.useEffect(() => { if (open) setValue(initial); }, [open, initial]);

  if (!open) {
    return (
      <button type="button" className={className} style={style}
        disabled={disabled} title={title} onClick={() => setOpen(true)}>
        {children}
      </button>
    );
  }

  const submit = async () => {
    setOpen(false);
    await onSubmit?.(value);
  };

  return (
    <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
      <input type="text" value={value} autoFocus placeholder={placeholder}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') submit();
          if (e.key === 'Escape') setOpen(false);
        }}
        style={{ minWidth: 180, fontSize: 12 }} />
      <button type="button" className={`${className} primary`} onClick={submit}>
        {submitLabel}
      </button>
      <button type="button" className={className} onClick={() => setOpen(false)}>
        Cancel
      </button>
    </span>
  );
}
