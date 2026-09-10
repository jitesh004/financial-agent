/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Next-Gen SVG Chart Engine
   Radiant gradient fills, smooth bezier curves, interactive crosshairs & zero external bloat.
   ──────────────────────────────────────────────────────────────────────── */

import React, {
  useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState,
} from 'react';
import { compact, money, pct } from '../core/format';

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

export function Fill({ children, min = 120 }) {
  const ref = useRef(null);
  const [height, setHeight] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    setHeight(el.clientHeight);
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver((e) => {
      const h = e[0]?.contentRect?.height;
      if (h != null) setHeight(Math.round(h));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return (
    <div ref={ref} style={{ height: '100%', minHeight: min }}>
      {height > 0 ? children(Math.max(min, height)) : null}
    </div>
  );
}

export function niceTicks(min, max, count = 4) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { lo: 0, hi: 1, ticks: [0, 1] };
  let lo = Math.min(0, min);
  let hi = Math.max(0, max);
  if (lo === hi) { hi = lo + 1; }
  const span = hi - lo;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
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

export function useChartKeyframes() {}

const PAD = { top: 16, right: 12, bottom: 28, left: 60 };

function useFrame({ data, height, valueMin, valueMax, pad = PAD }) {
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

  const labelStep = useMemo(() => {
    if (!n) return 1;
    const per = plotW / n;
    return Math.max(1, Math.ceil(56 / Math.max(per, 1)));
  }, [n, plotW]);

  return {
    hostRef, width, height, plotW, plotH, pad,
    lo, hi, ticks, y, cx, band, hover, onMove, clear, labelStep,
  };
}

function GridAndAxes({ f, format = compact }) {
  const { pad, plotW, plotH, ticks, y, labelStep, width } = f;
  return (
    <g aria-hidden="true">
      {ticks.map((val) => {
        const yPos = y(val);
        return (
          <g key={val}>
            <line
              x1={pad.left}
              x2={pad.left + plotW}
              y1={yPos}
              y2={yPos}
              stroke="var(--line)"
              strokeDasharray="3 3"
            />
            <text
              x={pad.left - 10}
              y={yPos + 4}
              textAnchor="end"
              fill="var(--text-3)"
              fontSize={11}
              fontFamily="var(--font-mono)"
            >
              {format(val)}
            </text>
          </g>
        );
      })}
    </g>
  );
}

/* ═══════════════════════════════════════════════════════ 1. Combo Chart ═══════ */

export function ComboChart({
  data = [],
  height = 280,
  bars = [],
  lines = [],
  xKey = 'label',
  formatY = compact,
}) {
  let min = 0;
  let max = 0;
  data.forEach((d) => {
    bars.forEach((b) => {
      const v = Number(d[b.key] || 0);
      if (v < min) min = v;
      if (v > max) max = v;
    });
    lines.forEach((l) => {
      const v = Number(d[l.key] || 0);
      if (v < min) min = v;
      if (v > max) max = v;
    });
  });

  const f = useFrame({ data, height, valueMin: min, valueMax: max });
  const gradId = useId();

  const barCount = bars.length;
  const barWidth = Math.max(4, Math.min(22, (f.band * 0.75) / barCount));

  return (
    <div ref={f.hostRef} style={{ position: 'relative', width: '100%', height }}>
      {f.width > 0 && (
        <svg
          width={f.width}
          height={height}
          onMouseMove={f.onMove}
          onMouseLeave={f.clear}
          style={{ overflow: 'visible' }}
        >
          <defs>
            <linearGradient id={`${gradId}-glow`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.25" />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.0" />
            </linearGradient>
            <filter id={`${gradId}-shadow`} x="-20%" y="-20%" width="140%" height="140%">
              <feDropShadow dx="0" dy="4" stdDeviation="3" floodColor="var(--accent-glow)" />
            </filter>
          </defs>

          <GridAndAxes f={f} format={formatY} />

          {/* Bars */}
          {data.map((d, i) => {
            const groupX = f.pad.left + i * f.band + (f.band - barCount * barWidth) / 2;
            return (
              <g key={i}>
                {bars.map((b, bi) => {
                  const val = Number(d[b.key] || 0);
                  const yZero = f.y(0);
                  const yVal = f.y(val);
                  const barH = Math.abs(yVal - yZero);
                  const barY = Math.min(yVal, yZero);
                  const isHovered = f.hover?.i === i;

                  return (
                    <rect
                      key={b.key}
                      x={groupX + bi * barWidth + 1}
                      y={barY}
                      width={Math.max(2, barWidth - 2)}
                      height={Math.max(1, barH)}
                      fill={b.color || `var(--c${bi + 1})`}
                      rx={3}
                      opacity={isHovered ? 1 : 0.85}
                      style={{ transition: 'opacity 0.15s, y 0.2s, height 0.2s' }}
                    />
                  );
                })}
              </g>
            );
          })}

          {/* Lines */}
          {lines.map((l) => {
            const points = data.map((d, i) => [f.cx(i), f.y(Number(d[l.key] || 0))]);
            if (points.length < 2) return null;

            let pathD = `M ${points[0][0]} ${points[0][1]}`;
            for (let i = 1; i < points.length; i += 1) {
              const prev = points[i - 1];
              const curr = points[i];
              const midX = (prev[0] + curr[0]) / 2;
              pathD += ` C ${midX} ${prev[1]}, ${midX} ${curr[1]}, ${curr[0]} ${curr[1]}`;
            }

            return (
              <g key={l.key}>
                <path
                  d={pathD}
                  fill="none"
                  stroke={l.color || 'var(--accent)'}
                  strokeWidth={2.5}
                  strokeLinecap="round"
                  filter={`url(#${gradId}-shadow)`}
                />
                {points.map(([px, py], pi) => (
                  <circle
                    key={pi}
                    cx={px}
                    cy={py}
                    r={f.hover?.i === pi ? 5 : 3}
                    fill="var(--surface)"
                    stroke={l.color || 'var(--accent)'}
                    strokeWidth={2}
                  />
                ))}
              </g>
            );
          })}

          {/* X Axis Labels */}
          {data.map((d, i) => {
            if (i % f.labelStep !== 0) return null;
            return (
              <text
                key={i}
                x={f.cx(i)}
                y={f.pad.top + f.plotH + 20}
                textAnchor="middle"
                fill="var(--text-3)"
                fontSize={11.5}
                fontFamily="var(--font-sans)"
              >
                {d[xKey]}
              </text>
            );
          })}

          {/* Crosshair Cursor */}
          {f.hover && (
            <line
              x1={f.cx(f.hover.i)}
              x2={f.cx(f.hover.i)}
              y1={f.pad.top}
              y2={f.pad.top + f.plotH}
              stroke="var(--accent)"
              strokeDasharray="4 4"
              strokeWidth={1.5}
            />
          )}
        </svg>
      )}

      {/* Floating Tooltip */}
      {f.hover && data[f.hover.i] && (
        <div
          className="glass-card"
          style={{
            position: 'absolute',
            left: Math.min(f.width - 160, Math.max(10, f.cx(f.hover.i) - 75)),
            top: 10,
            padding: '8px 12px',
            fontSize: 12,
            boxShadow: 'var(--shadow-2)',
            pointerEvents: 'none',
            zIndex: 10,
          }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>{data[f.hover.i][xKey]}</div>
          {bars.map((b) => (
            <div key={b.key} className="flex items-center justify-between gap-3">
              <span style={{ color: b.color }}>{b.label || b.key}:</span>
              <span className="tabular-nums font-semibold">{money(data[f.hover.i][b.key])}</span>
            </div>
          ))}
          {lines.map((l) => (
            <div key={l.key} className="flex items-center justify-between gap-3">
              <span style={{ color: l.color }}>{l.label || l.key}:</span>
              <span className="tabular-nums font-semibold">{money(data[f.hover.i][l.key])}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════ 2. Glow Area Chart ═══════ */

export function GlowAreaChart({
  data = [],
  height = 240,
  dataKey = 'value',
  xKey = 'label',
  color = 'var(--accent)',
  formatY = compact,
}) {
  let min = 0;
  let max = 0;
  data.forEach((d) => {
    const v = Number(d[dataKey] || 0);
    if (v < min) min = v;
    if (v > max) max = v;
  });

  const f = useFrame({ data, height, valueMin: min, valueMax: max });
  const gradId = useId();

  const points = data.map((d, i) => [f.cx(i), f.y(Number(d[dataKey] || 0))]);
  let lineD = '';
  let areaD = '';

  if (points.length >= 2) {
    lineD = `M ${points[0][0]} ${points[0][1]}`;
    for (let i = 1; i < points.length; i += 1) {
      const prev = points[i - 1];
      const curr = points[i];
      const midX = (prev[0] + curr[0]) / 2;
      lineD += ` C ${midX} ${prev[1]}, ${midX} ${curr[1]}, ${curr[0]} ${curr[1]}`;
    }
    const zeroY = f.y(0);
    areaD = `${lineD} L ${points[points.length - 1][0]} ${zeroY} L ${points[0][0]} ${zeroY} Z`;
  }

  return (
    <div ref={f.hostRef} style={{ position: 'relative', width: '100%', height }}>
      {f.width > 0 && (
        <svg
          width={f.width}
          height={height}
          onMouseMove={f.onMove}
          onMouseLeave={f.clear}
          style={{ overflow: 'visible' }}
        >
          <defs>
            <linearGradient id={`${gradId}-grad`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.32" />
              <stop offset="100%" stopColor={color} stopOpacity="0.0" />
            </linearGradient>
          </defs>

          <GridAndAxes f={f} format={formatY} />

          {areaD && <path d={areaD} fill={`url(#${gradId}-grad)`} />}
          {lineD && <path d={lineD} fill="none" stroke={color} strokeWidth={2.5} strokeLinecap="round" />}

          {data.map((d, i) => {
            if (i % f.labelStep !== 0) return null;
            return (
              <text
                key={i}
                x={f.cx(i)}
                y={f.pad.top + f.plotH + 20}
                textAnchor="middle"
                fill="var(--text-3)"
                fontSize={11.5}
              >
                {d[xKey]}
              </text>
            );
          })}

          {f.hover && points[f.hover.i] && (
            <circle
              cx={points[f.hover.i][0]}
              cy={points[f.hover.i][1]}
              r={5}
              fill="var(--surface)"
              stroke={color}
              strokeWidth={3}
            />
          )}
        </svg>
      )}

      {f.hover && data[f.hover.i] && (
        <div
          className="glass-card"
          style={{
            position: 'absolute',
            left: Math.min(f.width - 140, Math.max(10, f.cx(f.hover.i) - 60)),
            top: 10,
            padding: '6px 12px',
            fontSize: 12,
            pointerEvents: 'none',
          }}
        >
          <div style={{ fontWeight: 600 }}>{data[f.hover.i][xKey]}</div>
          <div className="tabular-nums font-bold" style={{ color }}>
            {money(data[f.hover.i][dataKey])}
          </div>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════ 3. Stacked Bar Chart ═══════ */

export function StackedBarChart({
  data = [],
  height = 260,
  series = [],
  xKey = 'label',
  formatY = compact,
}) {
  let max = 0;
  data.forEach((d) => {
    let sum = 0;
    series.forEach((s) => { sum += Math.abs(Number(d[s.key]) || 0); });
    if (sum > max) max = sum;
  });

  const f = useFrame({ data, height, valueMin: 0, valueMax: max });
  const barWidth = Math.max(6, Math.min(32, f.band * 0.65));

  return (
    <div ref={f.hostRef} style={{ position: 'relative', width: '100%', height }}>
      {f.width > 0 && (
        <svg
          width={f.width}
          height={height}
          onMouseMove={f.onMove}
          onMouseLeave={f.clear}
        >
          <GridAndAxes f={f} format={formatY} />

          {data.map((d, i) => {
            const x = f.cx(i) - barWidth / 2;
            let currentY = f.y(0);
            return (
              <g key={i}>
                {series.map((s, si) => {
                  const val = Math.abs(Number(d[s.key]) || 0);
                  const h = Math.abs(f.y(val) - f.y(0));
                  const y = currentY - h;
                  currentY = y;
                  return (
                    <rect
                      key={s.key}
                      x={x}
                      y={y}
                      width={barWidth}
                      height={Math.max(0, h)}
                      fill={s.color || `var(--c${si + 1})`}
                      rx={si === series.length - 1 ? 3 : 0}
                    />
                  );
                })}
              </g>
            );
          })}

          {data.map((d, i) => {
            if (i % f.labelStep !== 0) return null;
            return (
              <text
                key={i}
                x={f.cx(i)}
                y={f.pad.top + f.plotH + 20}
                textAnchor="middle"
                fill="var(--text-3)"
                fontSize={11.5}
              >
                {d[xKey]}
              </text>
            );
          })}
        </svg>
      )}

      {f.hover && data[f.hover.i] && (
        <div
          className="glass-card"
          style={{
            position: 'absolute',
            left: Math.min(f.width - 160, Math.max(10, f.cx(f.hover.i) - 75)),
            top: 10,
            padding: '8px 12px',
            fontSize: 12,
            pointerEvents: 'none',
          }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4 }}>{data[f.hover.i][xKey]}</div>
          {series.map((s) => (
            <div key={s.key} className="flex items-center justify-between gap-3">
              <span style={{ color: s.color }}>{s.label || s.key}:</span>
              <span className="tabular-nums font-semibold">{money(data[f.hover.i][s.key])}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════ 4. Sparkline ═══════ */

export function Sparkline({ data = [], width = 80, height = 24, color = 'var(--accent)' }) {
  if (!data.length) return null;
  const values = data.map((v) => (typeof v === 'object' ? Number(v.value || 0) : Number(v || 0)));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;

  const points = values.map((v, i) => {
    const x = (i / (values.length - 1 || 1)) * (width - 4) + 2;
    const y = height - 4 - ((v - min) / span) * (height - 8);
    return `${x},${y}`;
  }).join(' ');

  return (
    <svg width={width} height={height} style={{ overflow: 'visible', verticalAlign: 'middle' }}>
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/* ═══════════════════════════════════════════════════════ 5. Confidence Band Forecast ═══════ */

export function ConfidenceBandChart({
  data = [],
  height = 280,
  xKey = 'label',
  expectedKey = 'expected',
  lowKey = 'low',
  highKey = 'high',
}) {
  let min = 0;
  let max = 0;
  data.forEach((d) => {
    const lo = Number(d[lowKey] || 0);
    const hi = Number(d[highKey] || 0);
    if (lo < min) min = lo;
    if (hi > max) max = hi;
  });

  const f = useFrame({ data, height, valueMin: min, valueMax: max });
  const gradId = useId();

  const expPoints = data.map((d, i) => [f.cx(i), f.y(Number(d[expectedKey] || 0))]);
  const loPoints = data.map((d, i) => [f.cx(i), f.y(Number(d[lowKey] || 0))]);
  const hiPoints = data.map((d, i) => [f.cx(i), f.y(Number(d[highKey] || 0))]);

  let bandPath = '';
  if (hiPoints.length && loPoints.length) {
    bandPath = `M ${hiPoints[0][0]} ${hiPoints[0][1]}`;
    hiPoints.forEach(([x, y]) => { bandPath += ` L ${x} ${y}`; });
    for (let i = loPoints.length - 1; i >= 0; i -= 1) {
      bandPath += ` L ${loPoints[i][0]} ${loPoints[i][1]}`;
    }
    bandPath += ' Z';
  }

  let lineD = '';
  if (expPoints.length >= 2) {
    lineD = `M ${expPoints[0][0]} ${expPoints[0][1]}`;
    expPoints.forEach(([x, y]) => { lineD += ` L ${x} ${y}`; });
  }

  return (
    <div ref={f.hostRef} style={{ position: 'relative', width: '100%', height }}>
      {f.width > 0 && (
        <svg width={f.width} height={height} onMouseMove={f.onMove} onMouseLeave={f.clear}>
          <defs>
            <linearGradient id={`${gradId}-cone`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.2" />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.05" />
            </linearGradient>
          </defs>

          <GridAndAxes f={f} format={compact} />

          {bandPath && <path d={bandPath} fill={`url(#${gradId}-cone)`} />}
          {lineD && <path d={lineD} fill="none" stroke="var(--accent)" strokeWidth={2.5} />}

          {data.map((d, i) => {
            if (i % f.labelStep !== 0) return null;
            return (
              <text
                key={i}
                x={f.cx(i)}
                y={f.pad.top + f.plotH + 20}
                textAnchor="middle"
                fill="var(--text-3)"
                fontSize={11.5}
              >
                {d[xKey]}
              </text>
            );
          })}
        </svg>
      )}

      {f.hover && data[f.hover.i] && (
        <div
          className="glass-card"
          style={{
            position: 'absolute',
            left: Math.min(f.width - 160, Math.max(10, f.cx(f.hover.i) - 75)),
            top: 10,
            padding: '8px 12px',
            fontSize: 12,
            pointerEvents: 'none',
          }}
        >
          <div style={{ fontWeight: 700 }}>{data[f.hover.i][xKey]}</div>
          <div className="flex justify-between gap-3 text-accent font-semibold">
            <span>Expected:</span>
            <span className="tabular-nums">{money(data[f.hover.i][expectedKey])}</span>
          </div>
          <div className="flex justify-between gap-3 text-3">
            <span>Range:</span>
            <span className="tabular-nums">
              {money(data[f.hover.i][lowKey])} – {money(data[f.hover.i][highKey])}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export const BarChart = GlowAreaChart;
export const LineChart = GlowAreaChart;
export { DonutChart as Donut } from './gauges';
