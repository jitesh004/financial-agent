/* Rendering one widget's result.
 *
 * Everything here is driven by the result's own `columns` metadata rather than
 * by the widget's saved query: the server is the authority on what came back,
 * and a widget whose query was edited but whose type was not must still render
 * whatever it actually received instead of the shape it used to have.
 */

import React, { useMemo } from 'react';
import {
  colorFor, compact, compactNumber, count, dateLabel, money, monthLabel, titleCase,
} from '../../core/format';
import { BarList, Legend, Spinner } from '../../ui';
import { BarChart, Donut, LineChart } from '../../ui/charts';

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
   empty and somebody is left guessing why. */
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

const dimensionsOf = (r) => r.columns.filter((c) => c.role === 'dimension');
const measuresOf = (r) => r.columns.filter((c) => c.role === 'measure');

/* Chart-ready rows.
 *
 * Two shapes collapse to one here. With a single grouping the series ARE the
 * measures; with two groupings and one measure the second grouping becomes the
 * series and the rows have to be widened into one row per x value first -
 * otherwise a chart draws one bar per (month, rail) pair rather than one bar
 * per month split by rail. */
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
      if (!byX.has(x)) byX.set(x, { __x: x });
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
    data: result.rows.map((r) => ({ ...r, __x: r[xKey] })),
    xType: dims[0].type,
    valueType: measures[0].type,
    series: measures.map((m, i) => ({ key: m.key, name: m.label, color: colorFor(i) })),
  };
}

const labelOf = (type) => (row) => formatValue(row.__x, type);

function Note({ children }) { return <div className="tile-empty">{children}</div>; }

export default function WidgetView({ widget, result, error, loading, animate = true }) {
  if (widget.type === 'text') {
    return (
      <div className="prose" style={{ fontSize: 13.5 }}>
        {widget.viz?.text || widget.title || 'Empty note.'}
      </div>
    );
  }
  if (loading) return <div className="tile-empty"><Spinner /></div>;
  if (error) return <Note>{error}</Note>;
  if (!result) return <Note>Not loaded.</Note>;
  if (!result.rows.length) return <Note>No rows matched.</Note>;

  switch (widget.type) {
    case 'stat': return <StatView result={result} viz={widget.viz || {}} />;
    case 'table': return <TableView result={result} />;
    case 'pivot': return <PivotView result={result} viz={widget.viz || {}} />;
    case 'hbar': return <RankedView result={result} viz={widget.viz || {}} />;
    case 'donut': return <DonutView result={result} />;
    default:
      return <Cartesian result={result} type={widget.type} viz={widget.viz || {}} animate={animate} />;
  }
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
  // A grouped result has one row per group and a tile can only show one of
  // them. Naming it is the difference between "your spending" and "your
  // spending on rent, which happens to rank first".
  const scope = dims.length
    ? `${primary.label} · ${formatValue(row[dims[0].key], dims[0].type)}`
      + (result.row_count > 1 ? ` (top of ${result.row_count})` : '')
    : primary.label;

  return (
    <div className="tile-stat">
      <div className="tile-stat-value">{formatValue(value, primary.type)}</div>
      <div className="stat-note">{scope}</div>
      {delta !== undefined && delta !== null && (
        <div className="row tight">
          <span className={`chip ${delta * tone >= 0 ? 'pos' : 'neg'}`}>
            {delta >= 0 ? '▲' : '▼'} {formatValue(Math.abs(delta), primary.type)}
          </span>
          <span className="tiny dim">vs {formatValue(previous, primary.type)} before</span>
        </div>
      )}
      {measures.length > 1 && (
        <div className="col" style={{ gap: 3, marginTop: 8 }}>
          {measures.slice(1).map((m) => (
            <div key={m.key} className="row small muted" style={{ justifyContent: 'space-between' }}>
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
  const showDelta = result.rows.some(
    (r) => Object.keys(r).some((k) => k.endsWith('__delta')));
  const firstMeasure = measuresOf(result)[0];
  return (
    <table className="tbl-compact">
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
            {showDelta && (
              <td className="right num">
                {(() => {
                  const d = firstMeasure && row[`${firstMeasure.key}__delta`];
                  if (d === null || d === undefined) return '—';
                  return (
                    <span className={d >= 0 ? 'pos' : 'neg'}>
                      {d >= 0 ? '+' : ''}{compact(d)}
                    </span>
                  );
                })()}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PivotView({ result, viz }) {
  const dims = dimensionsOf(result);
  const measures = measuresOf(result);
  const matrix = useMemo(() => {
    if (dims.length < 2 || !measures.length) return null;
    const [rowDim, colDim] = dims;
    const measure = measures[0];
    const columnValues = [];
    const rowMap = new Map();
    result.rows.forEach((row) => {
      const rk = row[rowDim.key];
      const ck = row[colDim.key];
      if (!columnValues.includes(ck)) columnValues.push(ck);
      if (!rowMap.has(rk)) rowMap.set(rk, {});
      rowMap.get(rk)[ck] = row[measure.key];
    });
    columnValues.sort();
    return { rowDim, colDim, measure, columnValues, rowMap };
  }, [result, dims, measures]);

  if (!matrix) return <Note>A pivot needs two groupings and one measure.</Note>;
  const { rowDim, colDim, measure, columnValues, rowMap } = matrix;
  const columnTotals = {};
  columnValues.forEach((c) => {
    columnTotals[c] = [...rowMap.values()].reduce((s, r) => s + (r[c] || 0), 0);
  });

  return (
    <table className="tbl-compact">
      <thead>
        <tr>
          <th>{rowDim.label}</th>
          {columnValues.map((c) => (
            <th key={c} className="right">{formatValue(c, colDim.type)}</th>
          ))}
          <th className="right">Total</th>
        </tr>
      </thead>
      <tbody>
        {[...rowMap.entries()].map(([label, cells]) => {
          const total = columnValues.reduce((s, c) => s + (cells[c] || 0), 0);
          return (
            <tr key={label}>
              <td>{formatValue(label, rowDim.type)}</td>
              {columnValues.map((c) => (
                <td key={c} className="right num">
                  {cells[c] === undefined ? '·' : formatValue(cells[c], measure.type)}
                </td>
              ))}
              <td className="right num"><strong>{formatValue(total, measure.type)}</strong></td>
            </tr>
          );
        })}
      </tbody>
      {viz.show_totals !== false && (
        <tfoot>
          <tr>
            <td>Total</td>
            {columnValues.map((c) => (
              <td key={c} className="right num">{formatValue(columnTotals[c], measure.type)}</td>
            ))}
            <td className="right num">
              {formatValue(columnValues.reduce((s, c) => s + columnTotals[c], 0), measure.type)}
            </td>
          </tr>
        </tfoot>
      )}
    </table>
  );
}

function RankedView({ result, viz }) {
  const dims = dimensionsOf(result);
  const measures = measuresOf(result);
  if (!dims.length || !measures.length) {
    return <Note>Group by one field and pick a measure.</Note>;
  }
  const items = result.rows.map((row, i) => ({
    label: String(row[dims[0].key] ?? '—'),
    value: Math.abs(row[measures[0].key] || 0),
    color: colorFor(i),
  }));
  const total = viz.show_share ? items.reduce((s, it) => s + it.value, 0) : 0;
  return (
    <BarList
      items={items}
      total={total}
      max={viz.max_bars || 14}
      format={(v) => (measures[0].type === 'money' ? compact(v) : count(v))}
    />
  );
}

function DonutView({ result }) {
  const dims = dimensionsOf(result);
  const measures = measuresOf(result);
  if (!dims.length || !measures.length) {
    return <Note>Group by one field and pick a measure.</Note>;
  }
  const data = result.rows
    .map((row, i) => ({
      label: titleCase(String(row[dims[0].key] ?? '—')),
      value: Math.abs(row[measures[0].key] || 0),
      color: colorFor(i),
    }))
    .filter((d) => d.value > 0);
  if (!data.length) return <Note>Nothing to plot in this range.</Note>;
  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ flex: 1, minHeight: 0 }}>
        <Donut data={data} height={160}
          format={measures[0].type === 'money' ? money : count} />
      </div>
      <Legend items={data.slice(0, 8)} />
    </div>
  );
}

function Cartesian({ result, type, viz, animate }) {
  const chart = toChartData(result);
  if (!chart || !chart.data.length) return <Note>Nothing to plot in this range.</Note>;
  const money$ = chart.valueType === 'money';
  const shared = {
    data: chart.data,
    series: chart.series,
    labelOf: labelOf(chart.xType),
    height: 190,
    animate,
    format: money$ ? money : count,
    axisFormat: money$ ? compact : compactNumber,
  };
  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ flex: 1, minHeight: 0 }}>
        {type === 'bar'
          ? <BarChart {...shared} stacked={Boolean(viz.stacked)} />
          : <LineChart {...shared} area={type === 'area'} />}
      </div>
      {chart.series.length > 1 && <Legend items={chart.series.map((s) => ({
        label: s.name, color: s.color,
      }))} />}
    </div>
  );
}
