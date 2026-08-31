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

export function Stat({ label, value, note, tone, precise = false }) {
  const isNumber = typeof value === 'number';
  return (
    <div className="card stat">
      <div className="stat-label">{label}</div>
      <div className={`stat-value num ${tone || ''}`}>
        {isNumber ? money(value, precise) : value}
      </div>
      {note && <div className="stat-note">{note}</div>}
    </div>
  );
}

export function Chip({ children, tone = '', className = '', style, ...rest }) {
  return <span className={`chip ${tone} ${className}`} style={style} {...rest}>{children}</span>;
}

export function Empty({ title, children }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
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
export function BarList({ items, total, colorKey = 'color', max = 12, format = compact }) {
  const shown = items.slice(0, max);
  const peak = Math.max(...shown.map((i) => i.value), 1);

  return (
    <div>
      {shown.map((item, i) => (
        <div className="catrow" key={item.label}>
          <div className="catrow-label" title={titleCase(item.label)}>
            {titleCase(item.label)}
          </div>
          <div className="catrow-track">
            <div
              className="catrow-fill"
              style={{
                width: `${Math.max(2, (item.value / peak) * 100)}%`,
                background: item[colorKey],
              }}
            />
          </div>
          <div className="catrow-value num">{format(item.value)}</div>
          <div className="catrow-pct num">
            {total ? `${((item.value / total) * 100).toFixed(0)}%` : ''}
          </div>
        </div>
      ))}
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
