/* Charts, hand-built in SVG.
 *
 * There is no charting library in this app, and that is a deliberate decision
 * rather than an omission. The one it replaces pulled in ~450KB of Recharts to
 * draw six chart types, none of which needed a layout engine, a virtual DOM
 * reconciler of its own, or a plugin system. What is here is about 600 lines,
 * ships in the main bundle, and does three things that library could not:
 *
 *   - It reads its colours from CSS custom properties, so a chart follows the
 *     theme without being re-rendered and without JavaScript ever learning
 *     what "dark" means.
 *   - It sizes from a ResizeObserver on its own container rather than from a
 *     window listener, so a chart inside a resizable dashboard tile is correct
 *     without the page being resized.
 *   - Its tooltip is one absolutely-positioned div driven by a single pointer
 *     handler on a transparent hit rect, rather than per-mark listeners. That
 *     is what keeps a 400-bar chart smooth.
 *
 * Everything below shares the same frame: a plot area, a y axis of "nice"
 * ticks, an x axis that thins its own labels to fit, and a hover model that
 * reports the nearest x index.
 */

import React, {
  useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState,
} from 'react';
import { compact, money } from '../core/format';

/* ── measuring ───────────────────────────────────────────────────────────── */

/** The container's own width, tracked. Height is given by the caller: a chart
    that guesses its own height fights whatever laid it out. */
export function useSize(ref) {
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    setWidth(el.clientWidth);
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect?.width;
      if (w != null) setWidth(Math.round(w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return width;
}

/* ── scales ──────────────────────────────────────────────────────────────── */

/** Axis bounds a person would have chosen: 0, 25k, 50k rather than 0, 23.7k. */
export function niceTicks(min, max, count = 4) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { lo: 0, hi: 1, ticks: [0, 1] };
  let lo = Math.min(0, min);
  let hi = Math.max(0, max);
  if (lo === hi) { hi = lo + 1; }
  const span = hi - lo;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  lo = Math.floor(lo / step) * step;
  hi = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
  return { lo, hi, ticks };
}

const linear = (lo, hi, from, to) => (v) => {
  if (hi === lo) return to;
  return from + ((v - lo) / (hi - lo)) * (to - from);
};

/* ── the shared frame ────────────────────────────────────────────────────── */

const PAD = { top: 10, right: 8, bottom: 24, left: 54 };

/**
 * Everything every cartesian chart needs: measured width, a y scale over nice
 * ticks, a band scale over the x values, and a hover index driven by one hit
 * rect. Returned as data rather than as a wrapper component so each chart can
 * lay its own marks out inside the plot.
 */
function useFrame({ data, height, valueMin, valueMax, pad = PAD, xKey }) {
  const hostRef = useRef(null);
  const width = useSize(hostRef);
  const [hover, setHover] = useState(null);

  const plotW = Math.max(0, width - pad.left - pad.right);
  const plotH = Math.max(0, height - pad.top - pad.bottom);

  const { lo, hi, ticks } = useMemo(
    () => niceTicks(valueMin, valueMax), [valueMin, valueMax],
  );
  const y = useCallback(
    (v) => pad.top + linear(lo, hi, plotH, 0)(v), [lo, hi, plotH, pad.top],
  );

  const n = data.length;
  const band = n ? plotW / n : plotW;
  const cx = useCallback((i) => pad.left + band * (i + 0.5), [band, pad.left]);

  const onMove = useCallback((e) => {
    if (!n) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left - pad.left;
    const i = Math.max(0, Math.min(n - 1, Math.floor(x / band)));
    setHover({ i, x: e.clientX - rect.left, y: e.clientY - rect.top });
  }, [band, n, pad.left]);

  const clear = useCallback(() => setHover(null), []);

  /* Thin the x labels to whatever fits, rather than rotating them. A rotated
     axis costs vertical space and is harder to read than every third label. */
  const labelStep = useMemo(() => {
    if (!n) return 1;
    const per = plotW / n;
    return Math.max(1, Math.ceil(52 / Math.max(per, 1)));
  }, [n, plotW]);

  return {
    hostRef, width, height, plotW, plotH, pad,
    lo, hi, ticks, y, cx, band, hover, onMove, clear, labelStep, xKey,
  };
}

function Axes({ f, format = compact }) {
  return (
    <g aria-hidden="true">
      {f.ticks.map((t) => (
        <g key={t}>
          <line className="grid-line" x1={f.pad.left} x2={f.pad.left + f.plotW}
            y1={f.y(t)} y2={f.y(t)} />
          <text className="axis-text num" x={f.pad.left - 8} y={f.y(t)}
            textAnchor="end" dominantBaseline="middle">
            {format(t)}
          </text>
        </g>
      ))}
    </g>
  );
}

function XLabels({ f, data, labelOf }) {
  return (
    <g aria-hidden="true">
      {data.map((d, i) => (
        i % f.labelStep === 0 ? (
          <text key={i} className="axis-text" x={f.cx(i)} y={f.pad.top + f.plotH + 15}
            textAnchor="middle">
            {labelOf(d, i)}
          </text>
        ) : null
      ))}
    </g>
  );
}

/** One tooltip implementation for every chart. Positioned inside the host so
    it never escapes a scrolling panel, and flipped when it would run off. */
function Tip({ f, rows, label }) {
  if (!f.hover || !rows.length) return null;
  const flip = f.hover.x > f.width - 160;
  return (
    <div
      className="chart-tip"
      style={{
        left: flip ? undefined : f.hover.x + 14,
        right: flip ? f.width - f.hover.x + 14 : undefined,
        top: Math.max(0, Math.min(f.hover.y - 12, f.height - 90)),
      }}
    >
      {label && <div className="chart-tip-label">{label}</div>}
      {rows.map((r) => (
        <div className="chart-tip-row" key={r.key}>
          <span className="k">
            <i className="dot" style={{ background: r.color }} />
            <span className="truncate">{r.name}</span>
          </span>
          <span className="v">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

function Cursor({ f }) {
  if (!f.hover) return null;
  return (
    <line className="cursor-line" x1={f.cx(f.hover.i)} x2={f.cx(f.hover.i)}
      y1={f.pad.top} y2={f.pad.top + f.plotH} />
  );
}

function Host({ f, children, tip, label }) {
  return (
    <div className="chart" ref={f.hostRef} style={{ height: f.height }}>
      {f.width > 0 && (
        <svg width={f.width} height={f.height} role="img" aria-label={label}>
          {children}
          <rect className="hit" x={f.pad.left} y={f.pad.top}
            width={Math.max(0, f.plotW)} height={Math.max(0, f.plotH)}
            onPointerMove={f.onMove} onPointerLeave={f.clear} />
        </svg>
      )}
      {tip}
    </div>
  );
}

/* ── bars ────────────────────────────────────────────────────────────────── */

/**
 * Grouped or stacked bars.
 *
 * `series` is `[{ key, name, color }]`; `data` is one row per x value carrying
 * those keys. `onPick` makes a bar open the rows behind it - the strip of
 * month buttons the old version needed under the axis is unnecessary once the
 * bar itself is a target.
 */
export function BarChart({
  data, series, labelOf = (d) => d.label, height = 250, stacked = false,
  format = money, axisFormat = compact, onPick, maxBar = 34, animate = true,
}) {
  const { min, max } = useMemo(() => {
    let lo = 0;
    let hi = 0;
    for (const row of data) {
      if (stacked) {
        let sum = 0;
        for (const s of series) sum += Number(row[s.key]) || 0;
        hi = Math.max(hi, sum);
        lo = Math.min(lo, sum);
      } else {
        for (const s of series) {
          const v = Number(row[s.key]) || 0;
          hi = Math.max(hi, v);
          lo = Math.min(lo, v);
        }
      }
    }
    return { min: lo, max: hi };
  }, [data, series, stacked]);

  const f = useFrame({ data, height, valueMin: min, valueMax: max });
  const zero = f.y(0);
  const inner = Math.min(maxBar * (stacked ? 1 : series.length), f.band * 0.72);
  const each = stacked ? inner : inner / Math.max(series.length, 1);

  const rows = f.hover ? series.map((s) => ({
    key: s.key, name: s.name, color: s.color,
    value: format(Number(data[f.hover.i]?.[s.key]) || 0),
  })).filter((r) => r.value !== '—') : [];

  return (
    <Host f={f} label="Bar chart"
      tip={<Tip f={f} rows={rows} label={f.hover ? labelOf(data[f.hover.i], f.hover.i) : ''} />}>
      <Axes f={f} format={axisFormat} />
      {data.map((row, i) => {
        let acc = 0;
        return (
          <g key={i}>
            {series.map((s, si) => {
              const v = Number(row[s.key]) || 0;
              if (!v) { return null; }
              let top; let h; let x;
              if (stacked) {
                const from = acc;
                acc += v;
                top = f.y(acc);
                h = Math.abs(f.y(from) - f.y(acc));
                x = f.cx(i) - each / 2;
              } else {
                top = v >= 0 ? f.y(v) : zero;
                h = Math.abs(f.y(v) - zero);
                x = f.cx(i) - inner / 2 + si * each;
              }
              return (
                <rect
                  key={s.key}
                  className={`bar-rect ${f.hover && f.hover.i !== i ? 'dim' : ''}`}
                  x={x}
                  y={top}
                  width={Math.max(1, each - (stacked ? 0 : 2))}
                  height={Math.max(h, v ? 1.5 : 0)}
                  rx={stacked ? 0 : 3}
                  fill={s.color}
                  style={animate ? {
                    transformOrigin: `0 ${zero}px`,
                    animation: `bar-grow ${320 + i * 8}ms var(--e-out) both`,
                  } : undefined}
                />
              );
            })}
            {onPick && (
              <rect
                x={f.cx(i) - f.band / 2} y={f.pad.top}
                width={f.band} height={f.plotH}
                fill="transparent" style={{ cursor: 'pointer' }}
                onClick={() => onPick(row, i)}
              >
                <title>{labelOf(row, i)}</title>
              </rect>
            )}
          </g>
        );
      })}
      <line className="grid-line" x1={f.pad.left} x2={f.pad.left + f.plotW}
        y1={zero} y2={zero} stroke="var(--line-strong)" />
      <XLabels f={f} data={data} labelOf={labelOf} />
    </Host>
  );
}

/* ── lines and areas ─────────────────────────────────────────────────────── */

function pathFor(points) {
  return points.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
}

/**
 * Lines, optionally filled, optionally with a band behind them.
 *
 * `band` is `{ lowKey, highKey, color }` and draws the range a forecast is
 * honest about - the reason the forecast screen needs no second chart type.
 */
export function LineChart({
  data, series, labelOf = (d) => d.label, height = 250, area = false, band = null,
  format = money, axisFormat = compact, zeroLine = false, dots = false, animate = true,
}) {
  const { min, max } = useMemo(() => {
    let lo = Infinity;
    let hi = -Infinity;
    for (const row of data) {
      for (const s of series) {
        const v = Number(row[s.key]);
        if (Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
      }
      if (band) {
        const l = Number(row[band.lowKey]);
        const h = Number(row[band.highKey]);
        if (Number.isFinite(l)) lo = Math.min(lo, l);
        if (Number.isFinite(h)) hi = Math.max(hi, h);
      }
    }
    if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
    return { min: lo, max: hi };
  }, [data, series, band]);

  const f = useFrame({ data, height, valueMin: min, valueMax: max });
  const gid = useId().replace(/:/g, '');

  const rows = f.hover ? series.map((s) => ({
    key: s.key, name: s.name, color: s.color,
    value: format(Number(data[f.hover.i]?.[s.key])),
  })) : [];

  return (
    <Host f={f} label="Line chart"
      tip={<Tip f={f} rows={rows} label={f.hover ? labelOf(data[f.hover.i], f.hover.i) : ''} />}>
      <defs>
        {series.map((s) => (
          <linearGradient key={s.key} id={`fill-${gid}-${s.key}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={s.color} stopOpacity=".26" />
            <stop offset="100%" stopColor={s.color} stopOpacity="0" />
          </linearGradient>
        ))}
      </defs>

      <Axes f={f} format={axisFormat} />

      {band && data.length > 1 && (
        <path
          d={`${pathFor(data.map((d, i) => [f.cx(i), f.y(Number(d[band.highKey]) || 0)]))} `
            + `${pathFor([...data].reverse().map((d, i) => [
              f.cx(data.length - 1 - i), f.y(Number(d[band.lowKey]) || 0)]))
              .replace('M', 'L')} Z`}
          fill={band.color}
          opacity=".15"
        />
      )}

      {zeroLine && (
        <line x1={f.pad.left} x2={f.pad.left + f.plotW} y1={f.y(0)} y2={f.y(0)}
          stroke="var(--neg)" strokeDasharray="3 3" opacity=".6" />
      )}

      {series.map((s) => {
        const pts = data
          .map((d, i) => [f.cx(i), f.y(Number(d[s.key]) || 0), Number.isFinite(Number(d[s.key]))])
          .filter((p) => p[2]);
        if (!pts.length) return null;
        const line = pathFor(pts);
        return (
          <g key={s.key}>
            {area && (
              <path
                d={`${line} L${pts[pts.length - 1][0].toFixed(1)} ${f.y(0)} `
                  + `L${pts[0][0].toFixed(1)} ${f.y(0)} Z`}
                fill={`url(#fill-${gid}-${s.key})`}
              />
            )}
            <path
              className="series-line" d={line} stroke={s.color}
              strokeWidth={s.width || 2.2}
              strokeDasharray={s.dashed ? '5 4' : undefined}
              style={animate ? {
                strokeDasharray: s.dashed ? '5 4' : 2000,
                strokeDashoffset: s.dashed ? 0 : 2000,
                animation: s.dashed ? undefined : 'draw 900ms var(--e-out) forwards',
              } : undefined}
            />
            {(dots || data.length <= 14) && pts.map((p, i) => (
              <circle key={i} cx={p[0]} cy={p[1]} r={f.hover?.i === i ? 4.5 : 2.8}
                fill="var(--surface)" stroke={s.color} strokeWidth="2" />
            ))}
          </g>
        );
      })}

      <Cursor f={f} />
      <XLabels f={f} data={data} labelOf={labelOf} />
    </Host>
  );
}

/**
 * Bars and a line on one plot.
 *
 * The one composite worth having: income vs outflow as bars with net as a
 * line is the single most-read chart in this app, and drawing it as two
 * charts loses the comparison that makes it worth reading.
 */
export function ComboChart({
  data, bars, line, labelOf = (d) => d.label, height = 260, onPick,
  format = money, axisFormat = compact, animate = true,
}) {
  const { min, max } = useMemo(() => {
    let lo = 0;
    let hi = 0;
    for (const row of data) {
      for (const s of [...bars, ...(line ? [line] : [])]) {
        const v = Number(row[s.key]) || 0;
        hi = Math.max(hi, v);
        lo = Math.min(lo, v);
      }
    }
    return { min: lo, max: hi };
  }, [data, bars, line]);

  const f = useFrame({ data, height, valueMin: min, valueMax: max });
  const zero = f.y(0);
  const inner = Math.min(30 * bars.length, f.band * 0.7);
  const each = inner / Math.max(bars.length, 1);

  const rows = f.hover ? [...bars, ...(line ? [line] : [])].map((s) => ({
    key: s.key, name: s.name, color: s.color,
    value: format(Number(data[f.hover.i]?.[s.key]) || 0),
  })) : [];

  const linePts = line
    ? data.map((d, i) => [f.cx(i), f.y(Number(d[line.key]) || 0)])
    : [];

  return (
    <Host f={f} label="Cashflow chart"
      tip={<Tip f={f} rows={rows} label={f.hover ? labelOf(data[f.hover.i], f.hover.i) : ''} />}>
      <Axes f={f} format={axisFormat} />
      {data.map((row, i) => (
        <g key={i}>
          {bars.map((s, si) => {
            const v = Number(row[s.key]) || 0;
            const top = v >= 0 ? f.y(v) : zero;
            const h = Math.abs(f.y(v) - zero);
            return (
              <rect
                key={s.key}
                className={`bar-rect ${f.hover && f.hover.i !== i ? 'dim' : ''}`}
                x={f.cx(i) - inner / 2 + si * each}
                y={top}
                width={Math.max(1, each - 2)}
                height={Math.max(h, v ? 1.5 : 0)}
                rx="3"
                fill={s.color}
                style={animate ? {
                  transformOrigin: `0 ${zero}px`,
                  animation: `bar-grow ${300 + i * 10}ms var(--e-out) both`,
                } : undefined}
              />
            );
          })}
          {onPick && (
            <rect x={f.cx(i) - f.band / 2} y={f.pad.top} width={f.band} height={f.plotH}
              fill="transparent" style={{ cursor: 'pointer' }} onClick={() => onPick(row, i)}>
              <title>{labelOf(row, i)}</title>
            </rect>
          )}
        </g>
      ))}
      <line className="grid-line" x1={f.pad.left} x2={f.pad.left + f.plotW}
        y1={zero} y2={zero} stroke="var(--line-strong)" />
      {line && linePts.length > 1 && (
        <path className="series-line" d={pathFor(linePts)} stroke={line.color} strokeWidth="2.2" />
      )}
      {line && data.length <= 16 && linePts.map((p, i) => (
        <circle key={i} cx={p[0]} cy={p[1]} r={f.hover?.i === i ? 4.5 : 2.6}
          fill="var(--surface)" stroke={line.color} strokeWidth="2" />
      ))}
      <XLabels f={f} data={data} labelOf={labelOf} />
    </Host>
  );
}

/* ── donut ───────────────────────────────────────────────────────────────── */

export function Donut({ data, height = 240, format = money, centerLabel }) {
  const hostRef = useRef(null);
  const width = useSize(hostRef);
  const [hover, setHover] = useState(null);

  const total = data.reduce((s, d) => s + Math.abs(d.value || 0), 0);
  const size = Math.min(width || 0, height);
  const r = size / 2 - 6;
  const inner = r * 0.62;
  const cx = (width || 0) / 2;
  const cy = height / 2;

  const arcs = useMemo(() => {
    let a0 = -Math.PI / 2;
    return data.map((d) => {
      const frac = total ? Math.abs(d.value) / total : 0;
      const a1 = a0 + frac * Math.PI * 2;
      const arc = {
        ...d,
        frac,
        d: arcPath(cx, cy, r, inner, a0, a1 - 0.006),
      };
      a0 = a1;
      return arc;
    });
  }, [data, total, cx, cy, r, inner]);

  if (!total) return <div className="tile-empty">Nothing to plot.</div>;

  const active = hover != null ? arcs[hover] : null;

  return (
    <div className="chart" ref={hostRef} style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label="Donut chart">
          {arcs.map((a, i) => (
            <path
              key={a.label}
              d={a.d}
              fill={a.color}
              opacity={hover == null || hover === i ? 1 : 0.35}
              style={{ transition: 'opacity var(--t-fast)', cursor: 'pointer' }}
              onPointerEnter={() => setHover(i)}
              onPointerLeave={() => setHover(null)}
            >
              <title>{`${a.label}: ${format(a.value)}`}</title>
            </path>
          ))}
          <text x={cx} y={cy - 4} textAnchor="middle"
            style={{ fill: 'var(--text)', fontSize: 17, fontWeight: 640 }}
            className="num">
            {format(active ? active.value : total)}
          </text>
          <text x={cx} y={cy + 14} textAnchor="middle" className="axis-text">
            {active ? active.label : (centerLabel || 'total')}
          </text>
        </svg>
      )}
    </div>
  );
}

function arcPath(cx, cy, r, ir, a0, a1) {
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const p = (rad, a) => [cx + rad * Math.cos(a), cy + rad * Math.sin(a)];
  const [x0, y0] = p(r, a0);
  const [x1, y1] = p(r, a1);
  const [x2, y2] = p(ir, a1);
  const [x3, y3] = p(ir, a0);
  return `M${x0} ${y0} A${r} ${r} 0 ${large} 1 ${x1} ${y1} `
       + `L${x2} ${y2} A${ir} ${ir} 0 ${large} 0 ${x3} ${y3} Z`;
}

/* ── sparkline ───────────────────────────────────────────────────────────── */

/** A trend, at a glance, inside a tile or a table cell. No axes on purpose:
    it answers "which way" and never "how much". */
export function Sparkline({ values, width = 88, height = 26, color = 'var(--accent)', fill = true }) {
  const gid = useId().replace(/:/g, '');
  if (!values?.length) return null;
  const lo = Math.min(...values, 0);
  const hi = Math.max(...values, 0);
  const y = (v) => height - 2 - ((v - lo) / (hi - lo || 1)) * (height - 4);
  const x = (i) => (i / Math.max(values.length - 1, 1)) * width;
  const pts = values.map((v, i) => [x(i), y(v)]);
  const line = pathFor(pts);
  return (
    <svg className="spark" width={width} height={height} aria-hidden="true">
      {fill && (
        <>
          <defs>
            <linearGradient id={`sp-${gid}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity=".28" />
              <stop offset="100%" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={`${line} L${width} ${height} L0 ${height} Z`} fill={`url(#sp-${gid})`} />
        </>
      )}
      <path d={line} fill="none" stroke={color} strokeWidth="1.6"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* ── one bar, to scale ───────────────────────────────────────────────────── */

/**
 * A single horizontal bar split into named segments.
 *
 * "Most of my income is already committed" becomes a thing you can see rather
 * than a ratio you have to interpret. Used for the shape of a month, and for
 * card utilisation.
 */
export function StackBar({ segments, total, height = 12, format = money }) {
  const sum = total || segments.reduce((s, x) => s + Math.max(0, x.value || 0), 0);
  if (!sum) return null;
  return (
    <div style={{
      display: 'flex', height, borderRadius: 'var(--r-full)', overflow: 'hidden',
      background: 'var(--surface-3)', border: '1px solid var(--line)',
    }}>
      {segments.map((s) => {
        const share = Math.max(0, s.value || 0) / sum;
        if (share <= 0) return null;
        return (
          <span
            key={s.label}
            title={`${s.label}: ${format(s.value)} (${(share * 100).toFixed(0)}%)`}
            style={{
              width: `${share * 100}%`,
              background: s.color,
              transition: 'width var(--t-slow) var(--e-out)',
            }}
          />
        );
      })}
    </div>
  );
}

/* Keyframes the charts use. Injected once, from here, so a chart carries its
   own animation rather than depending on a stylesheet knowing about it. */
const KEYFRAMES = `
@keyframes bar-grow { from { transform: scaleY(0); } to { transform: scaleY(1); } }
@keyframes draw { to { stroke-dashoffset: 0; } }
`;

let injected = false;
export function useChartKeyframes() {
  useEffect(() => {
    if (injected) return;
    injected = true;
    const style = document.createElement('style');
    style.textContent = KEYFRAMES;
    document.head.appendChild(style);
  }, []);
}

