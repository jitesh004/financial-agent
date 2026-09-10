/* ────────────────────────────────────────────────────────────────────────────
   Prism v3 Cashflow Money Stream Visualizer (Sankey-style money routing)
   Visualizes how income flows into Needs, Wants, Debt, Savings and whatever
   is left unclassified.
   ──────────────────────────────────────────────────────────────────────── */

import React, { useId, useRef } from 'react';
import { money } from '../core/format';
import { useSize } from './charts';

/* Everything is drawn in real pixels against the measured container width, so
   the SVG keeps a 1:1 aspect (no `preserveAspectRatio="none"` stretching) and
   the labels live in a gutter the ribbons stop short of, never on top. */
const SOURCE_X = 4;
const SOURCE_W = 18;
const NODE_W = 12;
const LABEL_W = 210;
const GAP = 8;
const LABEL_MIN_WIDTH = 620;

export function MoneyStreamFlow({
  income = 0,
  needs = 0,
  wants = 0,
  debt = 0,
  savings = 0,
  other = 0,
  height = 220,
}) {
  const id = useId();
  const hostRef = useRef(null);
  const width = useSize(hostRef);

  const nIncome = Math.max(0, Number(income) || 0);
  const streams = [
    { label: 'Fixed Needs', value: Math.max(0, Number(needs) || 0), color: 'var(--c1)' },
    { label: 'Discretionary Wants', value: Math.max(0, Number(wants) || 0), color: 'var(--c4)' },
    { label: 'Debt Service', value: Math.max(0, Number(debt) || 0), color: 'var(--c7)' },
    { label: 'Investments & Savings', value: Math.max(0, Number(savings) || 0), color: 'var(--c2)' },
    /* Spending the grouping rules could not place still left the account, so
       it belongs on the chart — dropping it made the percentages lie. */
    { label: 'Other & Unclassified', value: Math.max(0, Number(other) || 0), color: 'var(--c12)' },
  ].filter((s, i) => s.value > 0 || i < 4);

  const totalOut = streams.reduce((sum, s) => sum + s.value, 0);

  const plotTop = 8;
  const plotH = Math.max(60, height - plotTop * 2);
  const minBand = 5;

  /* Destination side: stacked with gaps so each pillar is separable. */
  const usable = plotH - GAP * Math.max(0, streams.length - 1);
  let cursor = plotTop;
  const bands = streams.map((s) => {
    const share = totalOut > 0 ? s.value / totalOut : 1 / streams.length;
    const h = Math.max(minBand, share * usable);
    const band = { ...s, share, ty: cursor, h };
    cursor += h + GAP;
    return band;
  });

  /* If min-heights pushed the stack past the box, shrink it back to fit. */
  const drawn = cursor - GAP - plotTop;
  if (drawn > plotH) {
    const k = plotH / drawn;
    let y = plotTop;
    for (const b of bands) {
      b.h *= k;
      b.ty = y;
      y += b.h + GAP * k;
    }
  }

  /* Source side: the same bands packed with no gaps, so the ribbons fan out of
     one solid inflow bar the way a Sankey should read. */
  const sourceH = bands.reduce((sum, b) => sum + b.h, 0);
  const sourceTop = plotTop + Math.max(0, (plotH - sourceH) / 2);
  let sy = sourceTop;
  for (const b of bands) {
    b.sy = sy;
    sy += b.h;
  }

  const labelW = width >= LABEL_MIN_WIDTH ? LABEL_W : 0;
  const nodeX = Math.max(SOURCE_X + SOURCE_W + 80, width - labelW - NODE_W - 8);

  return (
    <div className="w-full flex-col gap-3" style={{ margin: '14px 0' }}>
      <div className="flex items-center justify-between flex-wrap gap-2 text-xs text-3 font-semibold uppercase tracking-wider">
        <span>
          Source · Total inflow
          <strong className="tabular-nums" style={{ color: 'var(--accent-text)', marginLeft: 8 }}>
            {money(nIncome)}
          </strong>
        </span>
        <span>
          Allocations · Total outflow
          <strong className="tabular-nums" style={{ color: 'var(--text)', marginLeft: 8 }}>
            {money(totalOut)}
          </strong>
        </span>
      </div>

      <div ref={hostRef} style={{ width: '100%' }}>
        {width > 0 && (
          <svg width={width} height={height} role="img" aria-label="Income routed to spending pillars">
            <defs>
              {bands.map((s, idx) => (
                <linearGradient key={idx} id={`${id}-flow-${idx}`} x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.55" />
                  <stop offset="100%" stopColor={s.color} stopOpacity="0.8" />
                </linearGradient>
              ))}
            </defs>

            {/* Inflow bar, spanning exactly the ribbons it feeds */}
            <rect
              x={SOURCE_X}
              y={sourceTop}
              width={SOURCE_W}
              height={Math.max(2, sourceH)}
              rx={5}
              fill="var(--accent)"
            />

            {bands.map((s, idx) => {
              const x0 = SOURCE_X + SOURCE_W;
              const c1 = x0 + (nodeX - x0) * 0.45;
              const c2 = x0 + (nodeX - x0) * 0.55;
              return (
                <path
                  key={idx}
                  d={`M ${x0} ${s.sy} C ${c1} ${s.sy}, ${c2} ${s.ty}, ${nodeX} ${s.ty}`
                    + ` L ${nodeX} ${s.ty + s.h}`
                    + ` C ${c2} ${s.ty + s.h}, ${c1} ${s.sy + s.h}, ${x0} ${s.sy + s.h} Z`}
                  fill={`url(#${id}-flow-${idx})`}
                >
                  <title>{`${s.label}: ${money(s.value)} (${Math.round(s.share * 100)}% of outflow)`}</title>
                </path>
              );
            })}

            {bands.map((s, idx) => (
              <rect
                key={idx}
                x={nodeX}
                y={s.ty}
                width={NODE_W}
                height={Math.max(2, s.h)}
                rx={3}
                fill={s.color}
              />
            ))}

            {labelW > 0 && bands.map((s, idx) => {
              const mid = s.ty + s.h / 2;
              const twoLine = s.h >= 26;
              return (
                <g key={idx}>
                  <text
                    x={nodeX + NODE_W + 10}
                    y={twoLine ? mid - 2 : mid + 4}
                    fill="var(--text)"
                    fontSize={12.5}
                    fontWeight={700}
                  >
                    {s.label} ({Math.round(s.share * 100)}%)
                  </text>
                  {twoLine && (
                    <text
                      x={nodeX + NODE_W + 10}
                      y={mid + 13}
                      fill="var(--text-3)"
                      fontSize={11}
                      fontFamily="var(--font-mono)"
                    >
                      {money(s.value)}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        )}
      </div>

      {/* Narrow viewports read the allocations as a legend under the ribbons */}
      {width > 0 && width < LABEL_MIN_WIDTH && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
          {bands.map((s, idx) => (
            <div key={idx} className="flex items-center gap-2" style={{ fontSize: 12 }}>
              <span style={{ width: 9, height: 9, borderRadius: '50%', background: s.color, flexShrink: 0 }} />
              <span style={{ fontWeight: 600 }}>{s.label} ({Math.round(s.share * 100)}%)</span>
              <span className="tabular-nums text-3">{money(s.value)}</span>
            </div>
          ))}
        </div>
      )}

      {/* Percentages are of outflow, so say so rather than leaving "52% of what?" */}
      <div className="tiny muted">
        Shares are of total outflow{nIncome > totalOut
          ? `; ${money(nIncome - totalOut)} of inflow stayed as balance`
          : nIncome > 0 && totalOut > nIncome
            ? `; outflow exceeded inflow by ${money(totalOut - nIncome)}`
            : ''}.
      </div>
    </div>
  );
}
