import React, { useMemo } from 'react';
import {
  colorFor, compact, compactNumber, count, dateLabel, money, monthLabel, titleCase,
} from '../../core/format';
import { BarList, Legend, Spinner } from '../../ui';
import { ComboChart, Donut, GlowAreaChart, StackedBarChart } from '../../ui/charts';

export const WIDGET_TYPES = [
  { key: 'stat', label: 'Telemetry Metric', glyph: '123' },
  { key: 'table', label: 'Data Table', glyph: '▤' },
  { key: 'bar', label: 'Bar Distribution', glyph: '▮' },
  { key: 'line', label: 'Trend Line', glyph: '╱' },
  { key: 'area', label: 'Glow Area', glyph: '◣' },
  { key: 'hbar', label: 'Ranked List', glyph: '≡' },
  { key: 'donut', label: 'Donut Share', glyph: '◔' },
  { key: 'pivot', label: 'Cross Pivot', glyph: '⊞' },
  { key: 'text', label: 'Audit Memo', glyph: '¶' },
];

export const TYPE_NEEDS = {
  stat: { dimensions: [0, 1], measures: [1, 9] },
  table: { dimensions: [0, 2], measures: [0, 9] },
  bar: { dimensions: [1, 2], measures: [1, 9] },
  line: { dimensions: [1, 2], measures: [1, 9] },
  area: { dimensions: [1, 2], measures: [1, 9] },
  hbar: { dimensions: [1, 1], measures: [1, 1] },
  donut: { dimensions: [1, 1], measures: [1, 1] },
  pivot: { dimensions: [2, 2], measures: [1, 1] },
  text: { dimensions: [0, 0], measures: [0, 0] },
};

export function formatValue(value, type) {
  if (value === null || value === undefined || value === '') return '—';
  if (type === 'money') return money(value);
  if (type === 'number') {
    return Number.isInteger(value) ? count(value)
      : Number(value).toLocaleString('en-IN', { maximumFractionDigits: 2 });
  }
  if (type === 'month') return monthLabel(value);
  if (type === 'date') return dateLabel(value);
  if (type === 'bool') return value ? 'Yes' : 'No';
  return titleCase(String(value));
}

const dimensionsOf = (r) => (r?.columns || []).filter((c) => c.role === 'dimension');
const measuresOf = (r) => (r?.columns || []).filter((c) => c.role === 'measure');

function toChartData(result) {
  const dims = dimensionsOf(result);
  const measures = measuresOf(result);
  if (!dims.length || !measures.length) return null;
  const xKey = dims[0].key;
  const split = dims[1] || null;

  if (split && measures.length === 1) {
    const measureKey = measures[0].key;
    const byX = new Map();
    const seen = [];
    result.rows.forEach((row) => {
      const x = row[xKey];
      if (!byX.has(x)) byX.set(x, { label: formatValue(x, dims[0].type), __x: x });
      const name = String(row[split.key] ?? '—');
      byX.get(x)[name] = row[measureKey];
      if (!seen.includes(name)) seen.push(name);
    });
    return {
      data: [...byX.values()],
      xType: dims[0].type,
      valueType: measures[0].type,
      series: seen.map((name, i) => ({ key: name, name: titleCase(name), color: colorFor(i) })),
    };
  }

  return {
    data: result.rows.map((r) => ({ ...r, label: formatValue(r[xKey], dims[0].type), __x: r[xKey] })),
    xType: dims[0].type,
    valueType: measures[0].type,
    series: measures.map((m, i) => ({ key: m.key, name: m.label, color: colorFor(i) })),
  };
}

export default function WidgetView({ widget, result, running, error }) {
  if (widget.type === 'text') {
    return (
      <div style={{ padding: 'var(--space-4)', fontSize: 'var(--text-sm)', lineHeight: 1.6, color: 'var(--text-2)' }}>
        {widget.viz?.content || widget.title || 'Blank narrative block'}
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 'var(--space-4)', color: 'var(--state-neg)', fontSize: 'var(--text-sm)' }}>
        {error}
      </div>
    );
  }

  if (!result && running) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', padding: 'var(--space-6)' }}>
        <Spinner size="md" />
      </div>
    );
  }

  if (!result || !result.rows?.length) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', padding: 'var(--space-4)', color: 'var(--text-3)', fontSize: 'var(--text-sm)' }}>
        Zero records match query filters
      </div>
    );
  }

  const dims = dimensionsOf(result);
  const measures = measuresOf(result);

  if (widget.type === 'stat') {
    const firstRow = result.rows[0] || {};
    const primaryMeasure = measures[0];
    const val = primaryMeasure ? firstRow[primaryMeasure.key] : null;

    return (
      <div style={{ padding: 'var(--space-5)', display: 'flex', flexDirection: 'column', justifyContent: 'center', height: '100%' }}>
        <div className="stat-label">{widget.title || primaryMeasure?.label || 'Total'}</div>
        <div className="stat-value" style={{ fontSize: 'var(--text-3xl)', color: 'var(--brand-primary)' }}>
          {formatValue(val, primaryMeasure?.type)}
        </div>
        {dims[0] && (
          <div className="tiny muted" style={{ marginTop: 4 }}>
            {formatValue(firstRow[dims[0].key], dims[0].type)}
          </div>
        )}
      </div>
    );
  }

  if (widget.type === 'hbar') {
    const dKey = dims[0]?.key;
    const mKey = measures[0]?.key;
    const items = result.rows.map((r, i) => ({
      label: formatValue(r[dKey], dims[0]?.type),
      value: Number(r[mKey]) || 0,
      color: colorFor(i),
    }));

    return (
      <div style={{ padding: 'var(--space-4)', height: '100%', overflowY: 'auto' }}>
        <BarList items={items} max={10} />
      </div>
    );
  }

  if (widget.type === 'donut') {
    const dKey = dims[0]?.key;
    const mKey = measures[0]?.key;
    const items = result.rows.map((r, i) => ({
      label: formatValue(r[dKey], dims[0]?.type),
      value: Number(r[mKey]) || 0,
      color: colorFor(i),
    }));

    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 'var(--space-3)', height: '100%' }}>
        <Donut data={items.slice(0, 8)} size={180} strokeWidth={24} />
        <Legend items={items.slice(0, 6)} />
      </div>
    );
  }

  if (widget.type === 'bar' || widget.type === 'line' || widget.type === 'area') {
    const chart = toChartData(result);
    if (!chart) return null;

    return (
      <div style={{ padding: 'var(--space-3)', height: '100%', width: '100%' }}>
        {widget.type === 'area' ? (
          <GlowAreaChart
            data={chart.data}
            height={220}
            dataKey={chart.series[0]?.key}
            color={chart.series[0]?.color || 'var(--brand-primary)'}
          />
        ) : (
          <ComboChart
            data={chart.data}
            height={220}
            bars={widget.type === 'bar' ? chart.series : []}
            lines={widget.type === 'line' ? chart.series : []}
          />
        )}
      </div>
    );
  }

  // Default: Data Table
  return (
    <div style={{ maxHeight: '100%', overflowY: 'auto' }}>
      <table className="terminal-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            {result.columns.map((c) => (
              <th key={c.key} style={{ textAlign: c.role === 'measure' ? 'right' : 'left' }}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, i) => (
            <tr key={i} className="terminal-row">
              {result.columns.map((c) => (
                <td
                  key={c.key}
                  className={c.role === 'measure' ? 'num nowrap font-medium' : 'nowrap'}
                  style={{ textAlign: c.role === 'measure' ? 'right' : 'left' }}
                >
                  {formatValue(row[c.key], c.type)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
