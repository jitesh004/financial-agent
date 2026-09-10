/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Financial Gauges & Donut Rings
   Health Score gauge, Liquidity Runway dial & interactive Donut breakdowns.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useId } from 'react';
import { compact, money, pct } from '../core/format';

/* ═══════════════════════════════════════════════════════ 1. Health Score Gauge ═══════ */

export function HealthScoreGauge({ score = 82, size = 160, grade = 'Strong', note = 'Well diversified' }) {
  const radius = 64;
  const circumference = 2 * Math.PI * radius;
  const numericScore = Number.isFinite(Number(score)) ? Number(score) : 50;
  const progress = Math.max(0, Math.min(100, Math.round(numericScore)));
  const strokeDashoffset = circumference - (progress / 100) * circumference;
  const id = useId();

  const color = progress >= 80 ? 'var(--pos)'
    : progress >= 60 ? 'var(--accent)'
      : progress >= 40 ? 'var(--warn)'
        : 'var(--neg)';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center' }}>
      <div style={{ position: 'relative', width: size, height: size }}>
        <svg width={size} height={size} viewBox="0 0 160 160" style={{ transform: 'rotate(-90deg)' }}>
          <defs>
            <linearGradient id={`${id}-grad`} x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="var(--accent)" />
              <stop offset="100%" stopColor={color} />
            </linearGradient>
          </defs>
          <circle
            cx="80"
            cy="80"
            r={radius}
            fill="none"
            stroke="var(--surface-3)"
            strokeWidth="12"
          />
          <circle
            cx="80"
            cy="80"
            r={radius}
            fill="none"
            stroke={`url(#${id}-grad)`}
            strokeWidth="12"
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
            strokeLinecap="round"
            style={{ transition: 'stroke-dashoffset 0.8s var(--e-out)' }}
          />
        </svg>

        <div style={{
          position: 'absolute', inset: 0,
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        }}>
          <span style={{
            fontFamily: 'var(--font-display)', fontSize: 32, fontWeight: 800,
            lineHeight: 1, letterSpacing: '-0.03em', color: 'var(--text)',
          }}>
            {progress}
          </span>
          <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', color, marginTop: 4 }}>
            {grade}
          </span>
        </div>
      </div>
      {note && <div style={{ fontSize: 12.5, color: 'var(--text-3)', marginTop: 8 }}>{note}</div>}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════ 2. Liquidity Runway Dial ═══════ */

export function RunwayDial({ months = 6.4, burnMonthly = 85000, liquidTotal = 544000 }) {
  const safeMonths = Number.isFinite(Number(months)) ? Math.max(0, Number(months)) : 0;
  const isHealthy = safeMonths >= 6;
  const isFair = safeMonths >= 3;
  const statusColor = isHealthy ? 'var(--pos)' : isFair ? 'var(--warn)' : 'var(--neg)';
  const statusLabel = isHealthy ? 'Safe Runway' : isFair ? 'Moderate Runway' : 'Low Cushion';

  return (
    <div className="stat-widget" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div className="flex items-center justify-between">
        <span className="stat-label">Liquidity Runway</span>
        <span className="badge" style={{
          background: isHealthy ? 'var(--pos-soft)' : isFair ? 'var(--warn-soft)' : 'var(--neg-soft)',
          color: statusColor,
          borderColor: statusColor,
        }}>
          {statusLabel}
        </span>
      </div>

      <div className="flex items-baseline gap-2">
        <span style={{ fontFamily: 'var(--font-display)', fontSize: 32, fontWeight: 800, color: statusColor }}>
          {safeMonths.toFixed(1)}
        </span>
        <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-2)' }}>months</span>
      </div>

      <div style={{
        width: '100%', height: 6, borderRadius: 'var(--r-full)',
        background: 'var(--surface-3)', overflow: 'hidden', margin: '4px 0',
      }}>
        <div style={{
          width: `${Math.min(100, (safeMonths / 12) * 100)}%`,
          height: '100%', background: statusColor,
          borderRadius: 'var(--r-full)', transition: 'width 0.6s var(--e-out)',
        }} />
      </div>

      <div className="flex items-center justify-between text-xs text-3" style={{ marginTop: 2 }}>
        <span>Liquid: <strong>{money(liquidTotal)}</strong></span>
        <span>Burn: <strong>{money(burnMonthly)}/mo</strong></span>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════ 3. Donut Breakdown ═══════ */

export function DonutChart({ data = [], size = 180, thickness = 26, centerLabel = '', centerValue = '' }) {
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const total = data.reduce((acc, d) => acc + Math.abs(Number(d.value) || 0), 0) || 1;

  let currentOffset = 0;
  const slices = data.map((d, i) => {
    const val = Math.abs(Number(d.value) || 0);
    const strokeDasharray = `${(val / total) * circumference} ${circumference}`;
    const offset = currentOffset;
    currentOffset -= (val / total) * circumference;
    return {
      ...d,
      color: d.color || `var(--c${(i % 12) + 1})`,
      strokeDasharray,
      offset,
    };
  });

  return (
    <div style={{ position: 'relative', width: size, height: size, margin: '0 auto' }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ transform: 'rotate(-90deg)' }}>
        {slices.map((s, i) => (
          <circle
            key={i}
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={s.color}
            strokeWidth={thickness}
            strokeDasharray={s.strokeDasharray}
            strokeDashoffset={s.offset}
            strokeLinecap="round"
          />
        ))}
      </svg>
      {(centerLabel || centerValue) && (
        <div style={{
          position: 'absolute', inset: 0,
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          textAlign: 'center',
        }}>
          {centerValue && (
            <span className="tabular-nums" style={{ fontFamily: 'var(--font-display)', fontSize: 17, fontWeight: 800 }}>
              {centerValue}
            </span>
          )}
          {centerLabel && (
            <span style={{ fontSize: 11, color: 'var(--text-3)', fontWeight: 600, textTransform: 'uppercase' }}>
              {centerLabel}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
