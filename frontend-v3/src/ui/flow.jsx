/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Cashflow Money Stream Visualizer (Sankey-style money routing)
   Visualizes how income flows into Needs, Wants, Debt and Savings.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useId } from 'react';
import { money, pct } from '../core/format';

export function MoneyStreamFlow({
  income = 250000,
  needs = 105000,
  wants = 45000,
  debt = 50000,
  savings = 50000,
  height = 220,
}) {
  const id = useId();
  const nIncome = Math.max(0, Number(income) || 0);
  const nNeeds = Math.max(0, Number(needs) || 0);
  const nWants = Math.max(0, Number(wants) || 0);
  const nDebt = Math.max(0, Number(debt) || 0);
  const nSavings = Math.max(0, Number(savings) || 0);
  const totalOut = nNeeds + nWants + nDebt + nSavings;
  const effectiveIn = Math.max(nIncome, totalOut, 1);

  const streams = [
    { label: 'Fixed Needs', value: nNeeds, color: 'var(--c1)', desc: 'Housing, bills & utilities' },
    { label: 'Discretionary Wants', value: nWants, color: 'var(--c4)', desc: 'Dining, leisure & shopping' },
    { label: 'Debt Service', value: nDebt, color: 'var(--c7)', desc: 'EMIs & loan commitments' },
    { label: 'Investments & Savings', value: nSavings, color: 'var(--c2)', desc: 'SIPs & capital build' },
  ];

  return (
    <div className="w-full flex-col gap-3" style={{ margin: '14px 0' }}>
      <div className="flex items-center justify-between text-xs text-3 font-semibold uppercase tracking-wider">
        <span>Source (Total Inflow)</span>
        <span>Allocations & Destinations</span>
      </div>

      <div style={{ position: 'relative', width: '100%', height }}>
        <svg width="100%" height={height} viewBox="0 0 600 220" preserveAspectRatio="none">
          <defs>
            {streams.map((s, idx) => (
              <linearGradient key={idx} id={`${id}-flow-${idx}`} x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.45" />
                <stop offset="100%" stopColor={s.color} stopOpacity="0.65" />
              </linearGradient>
            ))}
          </defs>

          {/* Left Inflow Bar */}
          <rect x="10" y="15" width="20" height="190" rx="6" fill="var(--accent)" />

          {/* Flow Ribbons */}
          {streams.map((s, idx) => {
            const startY = 25 + idx * 45;
            const endY = 20 + idx * 50;
            const ribbonH = Math.max(8, (s.value / effectiveIn) * 120);

            return (
              <path
                key={idx}
                d={`M 30 ${startY} C 200 ${startY}, 350 ${endY}, 520 ${endY} L 520 ${endY + ribbonH} C 350 ${endY + ribbonH}, 200 ${startY + ribbonH}, 30 ${startY + ribbonH} Z`}
                fill={`url(#${id}-flow-${idx})`}
                style={{ transition: 'all 0.5s var(--e-out)' }}
              />
            );
          })}

          {/* Right Destination Bars */}
          {streams.map((s, idx) => {
            const endY = 20 + idx * 50;
            const ribbonH = Math.max(8, (s.value / effectiveIn) * 120);
            return (
              <rect
                key={idx}
                x="520"
                y={endY}
                width="14"
                height={ribbonH}
                rx="4"
                fill={s.color}
              />
            );
          })}
        </svg>

        {/* Labels Overlay */}
        <div style={{
          position: 'absolute', left: 40, top: 15,
          background: 'var(--surface)', padding: '4px 10px', borderRadius: 'var(--r-sm)',
          border: '1px solid var(--line)', boxShadow: 'var(--shadow-sm)',
        }}>
          <div style={{ fontSize: 11, color: 'var(--text-3)', fontWeight: 600 }}>MONEY IN</div>
          <div style={{ fontSize: 15, fontWeight: 800, color: 'var(--accent)' }} className="tabular-nums">
            {money(income)}
          </div>
        </div>

        <div style={{
          position: 'absolute', right: 90, top: 0, bottom: 0,
          display: 'flex', flexDirection: 'column', justifyContent: 'space-around',
        }}>
          {streams.map((s, idx) => {
            const portion = totalOut > 0 ? (s.value / totalOut) * 100 : 0;
            return (
              <div key={idx} className="flex items-center gap-2" style={{ textAlign: 'right' }}>
                <div>
                  <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text)' }}>
                    {s.label} ({portion.toFixed(0)}%)
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-3)' }}>{money(s.value)}</div>
                </div>
                <div style={{ width: 8, height: 8, borderRadius: '50%', background: s.color }} />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
