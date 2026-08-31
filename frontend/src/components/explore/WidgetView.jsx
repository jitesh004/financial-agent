import React, { useMemo } from 'react';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  colorFor, compact, dateLabel, money, monthLabel, titleCase,
} from '../../lib';
import { axisProps, BarList, ChartTooltip, moneyAxis } from '../ui';

/* Rendering one widget's result.
 *
 * Everything here is driven by the result's own `columns` metadata rather than
 * by the widget's saved query: the server is the authority on what came back,
 * and a widget whose query was edited but whose type was not must still render
 * whatever it actually received instead of the shape it used to have. */

export const WIDGET_TYPES = [
  { key: 'stat', label: 'Number', glyph: '123' },
  { key: 'table', label: 'Table', glyph: '▤' },
  { key: 'bar', label: 'Bars', glyph: '▮' },
  { key: 'line', label: 'Line', glyph: '╱' },
  { key: 'area', label: 'Area', glyph: '◣' },
  { key: 'hbar', label: 'Ranked', glyph: '≡' },
  { key: 'donut', label: 'Donut', glyph: '◔' },
  { key: 'pivot', label: 'Pivot', glyph: '⊞' },
  { key: 'text', label: 'Note', glyph: '¶' },
];

/* Which types need what, so the editor can warn before the chart comes back
   empty and the user is left guessing why. */
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
    return Number.isInteger(value) ? value.toLocaleString('en-IN')
      : Number(value).toLocaleString('en-IN', { maximumFractionDigits: 2 });
  }
  if (type === 'month') return monthLabel(value);
  if (type === 'date') return dateLabel(value);
  if (type === 'bool') return value ? 'Yes' : 'No';
  return titleCase(String(value));
}

const dimensionsOf = (result) => result.columns.filter((c) => c.role === 'dimension');
const measuresOf = (result) => result.columns.filter((c) => c.role === 'measure');

/* Chart-ready rows.
 *
 * Two shapes collapse to one here. With a single grouping the series ARE the
 * measures; with two groupings and one measure the second grouping becomes the
 * series and the rows have to be widened into one row per x value first -
 * otherwise recharts draws one bar per (month, rail) pair rather than one bar
 * per month split by rail. */
function toChartData(result, viz = {}) {
  const dims = dimensionsOf(result);
  const measures = measuresOf(result);
  if (!dims.length || !measures.length) return null;

  const xKey = dims[0].key;
  const splitBy = dims[1] && (viz.series_from === dims[1].key || dims.length > 1)
    ? dims[1] : null;

  if (splitBy && measures.length === 1) {
    const measureKey = measures[0].key;
    const byX = new Map();
    const seen = [];
    result.rows.forEach((row) => {
      const x = row[xKey];
      if (!byX.has(x)) byX.set(x, { [xKey]: x });
      const name = String(row[splitBy.key] ?? '—');
      byX.get(x)[name] = row[measureKey];
      if (!seen.includes(name)) seen.push(name);
    });
    return {
      data: [...byX.values()],
      xKey,
      xType: dims[0].type,
      valueType: measures[0].type,
      series: seen.map((name, i) => ({
        key: name, name: titleCase(name), color: colorFor(i),
      })),
    };
  }

  return {
    data: result.rows,
    xKey,
    xType: dims[0].type,
    valueType: measures[0].type,
    series: measures.map((m, i) => ({
      key: m.key, name: m.label, color: colorFor(i),
    })),
  };
}

function xTickFormatter(type) {
  if (type === 'month') return monthLabel;
  if (type === 'date') return dateLabel;
  return (v) => titleCase(String(v ?? ''));
}

function Note({ children }) {
  return <div className="xp-empty">{children}</div>;
}

function StatView({ result, viz }) {
  const measures = measuresOf(result);
  const dims = dimensionsOf(result);
  const row = result.rows[0];
  if (!row || !measures.length) return <Note>No data in this range.</Note>;

  const primary = measures[0];
  const value = row[primary.key];
  const delta = row[`${primary.key}__delta`];
  const previous = row[`${primary.key}__prev`];
  const tone = viz.invert_tone ? -1 : 1;
  // A grouped result has one row per group and the tile can only show one of
  // them. Naming it is the difference between "your spending" and "your
  // spending on rent, which happens to rank first".
  const scope = dims.length
    ? `${primary.label} · ${formatValue(row[dims[0].key], dims[0].type)}`
      + (result.row_count > 1 ? ` (top of ${result.row_count})` : '')
    : primary.label;

  return (
    <div className="xp-stat">
      <div className="xp-stat-value">{formatValue(value, primary.type)}</div>
      <div className="stat-note">{scope}</div>
      {delta !== undefined && delta !== null && (
        <div className="xp-stat-delta">
          <span className={`chip ${delta * tone >= 0 ? 'pos' : 'neg'}`}>
            {delta >= 0 ? '▲' : '▼'} {formatValue(Math.abs(delta), primary.type)}
          </span>
          <span style={{ color: 'var(--text-3)' }}>
            vs {formatValue(previous, primary.type)} before
          </span>
        </div>
      )}
      {measures.length > 1 && (
        <div style={{ marginTop: 10, display: 'grid', gap: 4 }}>
          {measures.slice(1).map((m) => (
            <div key={m.key} style={{ display: 'flex', justifyContent: 'space-between',
              fontSize: 12, color: 'var(--text-2)' }}>
              <span>{m.label}</span>
              <span className="num">{formatValue(row[m.key], m.type)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TableView({ result }) {
  const showDelta = result.rows.some((r) => Object.keys(r).some((k) => k.endsWith('__delta')));
  return (
    <table>
      <thead>
        <tr>
          {result.columns.map((c) => (
            <th key={c.key} className={c.role === 'measure' ? 'right' : ''}>{c.label}</th>
          ))}
          {showDelta && <th className="right">Change</th>}
        </tr>
      </thead>
      <tbody>
        {result.rows.map((row, i) => (
          <tr key={i}>
            {result.columns.map((c) => (
              <td key={c.key} className={c.role === 'measure' ? 'right num' : 'truncate'}>
                {formatValue(row[c.key], c.type)}
              </td>
            ))}
            {showDelta && (() => {
              const first = measuresOf(result)[0];
              const delta = first && row[`${first.key}__delta`];
              return (
                <td className="right num">
                  {delta === null || delta === undefined ? '—' : (
                    <span className={delta >= 0 ? 'pos' : 'neg'}>
                      {delta >= 0 ? '+' : ''}{compact(delta)}
                    </span>
                  )}
                </td>
              );
            })()}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PivotView({ result, viz = {} }) {
  const dims = dimensionsOf(result);
  const measures = measuresOf(result);
  const matrix = useMemo(() => {
    if (dims.length < 2 || !measures.length) return null;
    const [rowDim, colDim] = dims;
    const measure = measures[0];
    const columnValues = [];
    const rowMap = new Map();
    result.rows.forEach((row) => {
      const rowKey = row[rowDim.key];
      const colKey = row[colDim.key];
      if (!columnValues.includes(colKey)) columnValues.push(colKey);
      if (!rowMap.has(rowKey)) rowMap.set(rowKey, {});
      rowMap.get(rowKey)[colKey] = row[measure.key];
    });
    columnValues.sort();
    return { rowDim, colDim, measure, columnValues, rowMap };
  }, [result, dims, measures]);

  if (!matrix) return <Note>A pivot needs two groupings and one measure.</Note>;
  const { rowDim, colDim, measure, columnValues, rowMap } = matrix;
  const columnTotals = {};
  columnValues.forEach((c) => {
    columnTotals[c] = [...rowMap.values()].reduce((sum, r) => sum + (r[c] || 0), 0);
  });

  return (
    <table className="xp-pivot">
      <thead>
        <tr>
          <th>{rowDim.label}</th>
          {columnValues.map((c) => (
            <th key={c}>{formatValue(c, colDim.type)}</th>
          ))}
          <th>Total</th>
        </tr>
      </thead>
      <tbody>
        {[...rowMap.entries()].map(([label, cells]) => {
          const total = columnValues.reduce((sum, c) => sum + (cells[c] || 0), 0);
          return (
            <tr key={label}>
              <th>{formatValue(label, rowDim.type)}</th>
              {columnValues.map((c) => (
                <td key={c}>
                  {cells[c] === undefined ? '·' : formatValue(cells[c], measure.type)}
                </td>
              ))}
              <td><strong>{formatValue(total, measure.type)}</strong></td>
            </tr>
          );
        })}
      </tbody>
      {viz.show_totals !== false && (
        <tfoot>
          <tr>
            <th>Total</th>
            {columnValues.map((c) => (
              <td key={c}>{formatValue(columnTotals[c], measure.type)}</td>
            ))}
            <td>
              {formatValue(
                columnValues.reduce((sum, c) => sum + columnTotals[c], 0), measure.type,
              )}
            </td>
          </tr>
        </tfoot>
      )}
    </table>
  );
}

function RankedView({ result, viz = {} }) {
  const dims = dimensionsOf(result);
  const measures = measuresOf(result);
  if (!dims.length || !measures.length) return <Note>Group by one field and pick a measure.</Note>;

  const items = result.rows.map((row, i) => ({
    label: String(row[dims[0].key] ?? '—'),
    value: Math.abs(row[measures[0].key] || 0),
    color: colorFor(i),
  }));
  const total = viz.show_share
    ? items.reduce((sum, item) => sum + item.value, 0) : 0;
  return (
    <BarList items={items} total={total} max={viz.max_bars || 14}
      format={(value) => (measures[0].type === 'money' ? compact(value)
        : value.toLocaleString('en-IN'))} />
  );
}

function DonutView({ result }) {
  const dims = dimensionsOf(result);
  const measures = measuresOf(result);
  if (!dims.length || !measures.length) return <Note>Group by one field and pick a measure.</Note>;

  const data = result.rows
    .map((row) => ({
      name: titleCase(String(row[dims[0].key] ?? '—')),
      value: Math.abs(row[measures[0].key] || 0),
    }))
    .filter((d) => d.value > 0);
  if (!data.length) return <Note>Nothing to plot in this range.</Note>;

  return (
    <ResponsiveContainer width="100%" height="100%">
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" innerRadius="52%"
          outerRadius="78%" paddingAngle={1} stroke="none">
          {data.map((entry, i) => <Cell key={entry.name} fill={colorFor(i)} />)}
        </Pie>
        <Tooltip content={<ChartTooltip />} />
        <Legend wrapperStyle={{ fontSize: 11 }} iconType="circle" iconSize={7} />
      </PieChart>
    </ResponsiveContainer>
  );
}

function CartesianView({ result, type, viz = {} }) {
  const chart = toChartData(result, viz);
  if (!chart || !chart.data.length) return <Note>Nothing to plot in this range.</Note>;

  const Chart = type === 'line' ? LineChart : type === 'area' ? AreaChart : BarChart;
  const tickFormat = xTickFormatter(chart.xType);
  const stack = viz.stacked ? 'a' : undefined;
  const yAxis = chart.valueType === 'money' ? moneyAxis
    : { ...axisProps, width: 46, tickFormatter: (v) => compact(v).replace('₹', '') };

  return (
    <ResponsiveContainer width="100%" height="100%">
      <Chart data={chart.data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--border)" vertical={false} />
        <XAxis dataKey={chart.xKey} tickFormatter={tickFormat} {...axisProps}
          interval="preserveStartEnd" minTickGap={16} />
        <YAxis {...yAxis} />
        <Tooltip content={<ChartTooltip
          formatter={chart.valueType === 'money' ? money : ((v) => v?.toLocaleString('en-IN'))} />} />
        {chart.series.length > 1 && (
          <Legend wrapperStyle={{ fontSize: 11 }} iconType="circle" iconSize={7} />
        )}
        {chart.series.map((s) => (
          type === 'line' ? (
            <Line key={s.key} type="monotone" dataKey={s.key} name={s.name}
              stroke={s.color} strokeWidth={2} dot={false} />
          ) : type === 'area' ? (
            <Area key={s.key} type="monotone" dataKey={s.key} name={s.name}
              stroke={s.color} fill={s.color} fillOpacity={0.18} strokeWidth={2}
              stackId={stack} />
          ) : (
            <Bar key={s.key} dataKey={s.key} name={s.name} fill={s.color}
              radius={[3, 3, 0, 0]} stackId={stack} maxBarSize={44} />
          )
        ))}
      </Chart>
    </ResponsiveContainer>
  );
}

export default function WidgetView({ widget, result, error, loading }) {
  if (widget.type === 'text') {
    return <div className="xp-note">{widget.viz?.text || widget.title || 'Empty note.'}</div>;
  }
  if (loading) {
    return (
      <div className="xp-empty">
        <div className="spinner" />
      </div>
    );
  }
  if (error) return <div className="xp-empty">{error}</div>;
  if (!result) return <div className="xp-empty">Not loaded.</div>;
  if (!result.rows.length) return <Note>No rows matched.</Note>;

  switch (widget.type) {
    case 'stat': return <StatView result={result} viz={widget.viz || {}} />;
    case 'table': return <TableView result={result} />;
    case 'pivot': return <PivotView result={result} viz={widget.viz || {}} />;
    case 'hbar': return <RankedView result={result} viz={widget.viz || {}} />;
    case 'donut': return <DonutView result={result} />;
    default:
      return <CartesianView result={result} type={widget.type} viz={widget.viz || {}} />;
  }
}
