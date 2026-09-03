import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../lib';
import { Callout } from '../ui';
import { FieldSelect, FilterRow, groupOptions } from './FieldControls';
import WidgetView, { TYPE_NEEDS, WIDGET_TYPES } from './WidgetView';

/* The query builder.
 *
 * Every control on this panel is generated from /api/query/schema. Nothing
 * about which fields exist, which operators apply to them or which
 * aggregations a measure allows is written down here - add a dimension to
 * analytics.query.FIELDS and it appears in these dropdowns with no change on
 * this side.
 *
 * The preview runs the real endpoint against the real ledger while you build,
 * because the alternative is saving a widget to find out what it says. */

const WIDTHS = [
  [3, 'Quarter'], [4, 'Third'], [6, 'Half'], [8, 'Two thirds'], [12, 'Full'],
];
const HEIGHTS = [[1, 'Short'], [2, 'Medium'], [3, 'Tall'], [4, 'Very tall']];

const EMPTY = {
  title: '',
  type: 'bar',
  width: 6,
  height: 3,
  query: {
    dimensions: ['month'],
    measures: [{ field: 'outflow', agg: 'sum' }],
    filters: [],
    date_range: { preset: 'inherit' },
    limit: 200,
    exclude_mirror_legs: true,
    exclude_excluded: true,
    compare: false,
  },
  viz: {},
};

export default function WidgetEditor({ schema, widget, board, onSave, onCancel, onDelete }) {
  const [draft, setDraft] = useState(() => ({
    ...EMPTY, ...widget,
    query: { ...EMPTY.query, ...(widget?.query || {}) },
    viz: { ...(widget?.viz || {}) },
  }));
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const timer = useRef(null);

  const fields = schema.fields;
  const fieldMap = useMemo(
    () => Object.fromEntries(fields.map((f) => [f.key, f])), [fields],
  );
  const groupable = useMemo(() => fields.filter((f) => f.groupable), [fields]);
  const filterable = useMemo(() => fields.filter((f) => f.filterable), [fields]);
  const measureMap = useMemo(
    () => Object.fromEntries(schema.measures.map((m) => [m.key, m])), [schema.measures],
  );

  const setQuery = (patch) => setDraft((d) => ({ ...d, query: { ...d.query, ...patch } }));
  const setViz = (patch) => setDraft((d) => ({ ...d, viz: { ...d.viz, ...patch } }));

  const querySignature = JSON.stringify(draft.query);

  useEffect(() => {
    if (draft.type === 'text') { setPreview(null); setError(null); return undefined; }
    // Debounced: every keystroke in a filter would otherwise be its own query.
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      setBusy(true);
      api.runQuery(draft.query, board)
        .then((result) => { setPreview(result); setError(null); })
        .catch((e) => { setError(e.message); setPreview(null); })
        .finally(() => setBusy(false));
    }, 320);
    return () => clearTimeout(timer.current);
  }, [querySignature, draft.type, board]);

  // ---- dimensions ----
  const dimensions = draft.query.dimensions || [];
  const setDimension = (index, key) => {
    const next = [...dimensions];
    if (key) next[index] = key; else next.splice(index, 1);
    setQuery({ dimensions: next });
  };

  // ---- measures ----
  const measures = draft.query.measures || [];
  const setMeasure = (index, patch) => {
    const next = measures.map((m, i) => (i === index ? { ...m, ...patch } : m));
    // Switching measure can strand an aggregation the new one does not offer.
    if (patch.field) {
      const allowed = measureMap[patch.field]?.aggs || ['sum'];
      if (!allowed.includes(next[index].agg)) next[index].agg = allowed[0];
    }
    setQuery({ measures: next });
  };

  const filters = draft.query.filters || [];

  const needs = TYPE_NEEDS[draft.type] || TYPE_NEEDS.table;
  const shapeWarning = (() => {
    if (draft.type === 'text') return null;
    const [minDim, maxDim] = needs.dimensions;
    const [minMeasure] = needs.measures;
    if (dimensions.length < minDim) {
      return `A ${draft.type} needs at least ${minDim} grouping${minDim > 1 ? 's' : ''}.`;
    }
    if (dimensions.length > maxDim) {
      return `A ${draft.type} uses only the first ${maxDim} grouping${maxDim > 1 ? 's' : ''}; the rest are ignored.`;
    }
    if (measures.length < minMeasure) return 'Add at least one measure.';
    return null;
  })();

  const save = () => onSave({
    ...draft,
    title: draft.title.trim() || suggestedTitle(draft, measureMap, fieldMap),
  });

  return (
    <div className="xp-overlay" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="xp-modal">
        <div className="xp-modal-head">
          <div className="xp-modal-title">{widget?.id ? 'Edit widget' : 'New widget'}</div>
          <div className="header-spacer" style={{ flex: 1 }} />
          <button className="xp-icon-btn" onClick={onCancel} aria-label="Close">✕</button>
        </div>

        <div className="xp-modal-body">
          <div className="xp-editor">
            {/* ------------- builder ------------- */}
            <div className="xp-editor-pane">
              <div className="xp-field">
                <label htmlFor="xp-title">Title</label>
                <input id="xp-title" className="xp-input" value={draft.title}
                  placeholder={suggestedTitle(draft, measureMap, fieldMap)}
                  onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))} />
              </div>

              <div className="xp-field">
                <span className="xp-legend">Show as</span>
                <div className="xp-types">
                  {WIDGET_TYPES.map((t) => (
                    <button key={t.key} type="button"
                      className={`xp-type ${draft.type === t.key ? 'active' : ''}`}
                      onClick={() => setDraft((d) => ({ ...d, type: t.key }))}>
                      <span className="xp-type-glyph">{t.glyph}</span>
                      {t.label}
                    </button>
                  ))}
                </div>
              </div>

              {draft.type === 'text' ? (
                <div className="xp-field">
                  <label htmlFor="xp-note">Note</label>
                  <textarea id="xp-note" className="xp-input" rows={6}
                    value={draft.viz.text || ''}
                    placeholder="A heading, a caveat, a reminder of what this section means."
                    onChange={(e) => setViz({ text: e.target.value })} />
                </div>
              ) : (
                <>
                  <div className="xp-field">
                    <span className="xp-legend">Group by</span>
                    <div className="xp-list">
                      {dimensions.map((key, i) => (
                        <div className="xp-row" key={`${key}-${i}`}>
                          <FieldSelect fields={groupable} value={key}
                            onChange={(next) => setDimension(i, next)} />
                          <button className="xp-icon-btn" title="Remove"
                            onClick={() => setDimension(i, null)}>✕</button>
                        </div>
                      ))}
                      {dimensions.length < 2 && (
                        <button className="btn" style={{ fontSize: 12 }}
                          onClick={() => setQuery({
                            dimensions: [...dimensions,
                              groupable.find((f) => !dimensions.includes(f.key))?.key
                              || groupable[0].key],
                          })}>
                          + Add grouping
                        </button>
                      )}
                      {dimensions.length === 0 && (
                        <div className="xp-hint">
                          No grouping: one row for the whole range, which is what a
                          Number tile wants.
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="xp-field">
                    <span className="xp-legend">Measure</span>
                    <div className="xp-list">
                      {measures.map((m, i) => (
                        <div className="xp-row" key={i}>
                          <select className="xp-select" value={m.field}
                            onChange={(e) => setMeasure(i, { field: e.target.value })}>
                            {groupOptions(schema.measures).map(([group, items]) => (
                              <optgroup label={group} key={group}>
                                {items.map((one) => (
                                  <option key={one.key} value={one.key}>{one.label}</option>
                                ))}
                              </optgroup>
                            ))}
                          </select>
                          <select className="xp-select" style={{ maxWidth: 130 }} value={m.agg}
                            onChange={(e) => setMeasure(i, { agg: e.target.value })}>
                            {(measureMap[m.field]?.aggs || ['sum']).map((agg) => (
                              <option key={agg} value={agg}>{schema.agg_labels[agg] || agg}</option>
                            ))}
                          </select>
                          <button className="xp-icon-btn" title="Remove"
                            onClick={() => setQuery({
                              measures: measures.filter((_, j) => j !== i),
                            })}>✕</button>
                        </div>
                      ))}
                      <button className="btn" style={{ fontSize: 12 }}
                        onClick={() => setQuery({
                          measures: [...measures, { field: 'outflow', agg: 'sum' }],
                        })}>
                        + Add measure
                      </button>
                    </div>
                  </div>

                  <div className="xp-field">
                    <span className="xp-legend">Filters</span>
                    <div className="xp-list">
                      {filters.map((f, i) => (
                        <FilterRow
                          key={i}
                          filter={f}
                          fields={filterable}
                          fieldMap={fieldMap}
                          opLabels={schema.op_labels}
                          options={schema.options}
                          onChange={(next) => setQuery({
                            filters: filters.map((one, j) => (j === i ? next : one)),
                          })}
                          onRemove={() => setQuery({
                            filters: filters.filter((_, j) => j !== i),
                          })}
                        />
                      ))}
                      <button className="btn" style={{ fontSize: 12 }}
                        onClick={() => setQuery({
                          filters: [...filters,
                            { field: 'category', op: 'in', value: [] }],
                        })}>
                        + Add filter
                      </button>
                    </div>
                  </div>

                  <div className="xp-field">
                    <span className="xp-legend">Date range</span>
                    <select className="xp-select" value={draft.query.date_range?.preset || 'inherit'}
                      onChange={(e) => setQuery({
                        date_range: { ...draft.query.date_range, preset: e.target.value },
                        pin_date: e.target.value !== 'inherit',
                      })}>
                      <option value="inherit">Follow the dashboard</option>
                      {schema.date_presets.map((p) => (
                        <option key={p.value} value={p.value}>{p.label}</option>
                      ))}
                    </select>
                    {draft.query.date_range?.preset === 'custom' && (
                      <div className="xp-row">
                        <input className="xp-input slim" type="date"
                          value={draft.query.date_range.start || ''}
                          onChange={(e) => setQuery({
                            date_range: { ...draft.query.date_range, start: e.target.value },
                          })} />
                        <input className="xp-input slim" type="date"
                          value={draft.query.date_range.end || ''}
                          onChange={(e) => setQuery({
                            date_range: { ...draft.query.date_range, end: e.target.value },
                          })} />
                      </div>
                    )}
                    {draft.query.date_range?.preset === 'custom_months' && (
                      <div className="xp-row">
                        <input className="xp-input slim" type="month"
                          aria-label="First month"
                          value={draft.query.date_range.start_month || ''}
                          onChange={(e) => setQuery({
                            date_range: { ...draft.query.date_range,
                              start_month: e.target.value },
                          })} />
                        <input className="xp-input slim" type="month"
                          aria-label="Last month"
                          value={draft.query.date_range.end_month || ''}
                          onChange={(e) => setQuery({
                            date_range: { ...draft.query.date_range,
                              end_month: e.target.value },
                          })} />
                      </div>
                    )}
                    {draft.query.date_range?.preset !== 'inherit' && (
                      <label className="xp-check">
                        <input type="checkbox" checked={Boolean(draft.query.pin_date)}
                          onChange={(e) => setQuery({ pin_date: e.target.checked })} />
                        Keep this range even when the dashboard&apos;s changes
                      </label>
                    )}
                  </div>

                  <div className="xp-field">
                    <span className="xp-legend">Options</span>
                    <label className="xp-check">
                      <input type="checkbox"
                        checked={draft.query.exclude_mirror_legs !== false}
                        onChange={(e) => setQuery({ exclude_mirror_legs: e.target.checked })} />
                      Drop mirror legs of internal transfers
                    </label>
                    <label className="xp-check">
                      <input type="checkbox"
                        checked={draft.query.exclude_excluded !== false}
                        onChange={(e) => setQuery({ exclude_excluded: e.target.checked })} />
                      Drop rows you excluded by hand
                    </label>
                    <label className="xp-check">
                      <input type="checkbox" checked={Boolean(draft.query.compare)}
                        onChange={(e) => setQuery({ compare: e.target.checked })} />
                      Compare with the period before
                    </label>
                    {draft.type === 'bar' || draft.type === 'area' ? (
                      <label className="xp-check">
                        <input type="checkbox" checked={Boolean(draft.viz.stacked)}
                          onChange={(e) => setViz({ stacked: e.target.checked })} />
                        Stack the series
                      </label>
                    ) : null}
                    {draft.type === 'hbar' && (
                      <label className="xp-check">
                        <input type="checkbox" checked={draft.viz.show_share !== false}
                          onChange={(e) => setViz({ show_share: e.target.checked })} />
                        Show each row&apos;s share of the total
                      </label>
                    )}
                    {draft.type === 'pivot' && (
                      <label className="xp-check">
                        <input type="checkbox" checked={draft.viz.show_totals !== false}
                          onChange={(e) => setViz({ show_totals: e.target.checked })} />
                        Show a totals row
                      </label>
                    )}
                    <div className="xp-row" style={{ marginTop: 4 }}>
                      <select className="xp-select slim" value={draft.query.sort?.[0]?.key || ''}
                        onChange={(e) => setQuery({
                          sort: e.target.value
                            ? [{ key: e.target.value, dir: draft.query.sort?.[0]?.dir || 'desc' }]
                            : [],
                        })}>
                        <option value="">Sort automatically</option>
                        {dimensions.map((key) => (
                          <option key={key} value={key}>By {fieldMap[key]?.label}</option>
                        ))}
                        {measures.map((m, i) => (
                          <option key={`m${i}`} value={`m${i}`}>
                            By {measureMap[m.field]?.label || m.field}
                          </option>
                        ))}
                      </select>
                      <select className="xp-select slim" style={{ maxWidth: 120 }}
                        value={draft.query.sort?.[0]?.dir || 'desc'}
                        disabled={!draft.query.sort?.length}
                        onChange={(e) => setQuery({
                          sort: [{ key: draft.query.sort[0].key, dir: e.target.value }],
                        })}>
                        <option value="desc">Highest first</option>
                        <option value="asc">Lowest first</option>
                      </select>
                    </div>
                    <div className="xp-row">
                      <label className="xp-hint" htmlFor="xp-limit" style={{ flex: 'none' }}>
                        Row limit
                      </label>
                      <input id="xp-limit" className="xp-input slim" type="number" min="1"
                        max={schema.max_rows} value={draft.query.limit || 200}
                        onChange={(e) => setQuery({ limit: Number(e.target.value) || 200 })} />
                    </div>
                  </div>
                </>
              )}

              <div className="xp-field">
                <span className="xp-legend">Size on the board</span>
                <div className="xp-row">
                  <select className="xp-select slim" value={draft.width}
                    onChange={(e) => setDraft((d) => ({ ...d, width: Number(e.target.value) }))}>
                    {WIDTHS.map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                  <select className="xp-select slim" value={draft.height}
                    onChange={(e) => setDraft((d) => ({ ...d, height: Number(e.target.value) }))}>
                    {HEIGHTS.map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            {/* ------------- live preview ------------- */}
            <div className="xp-editor-pane">
              <div className="xp-field">
                <span className="xp-legend">
                  Preview {busy && <span className="spinner" style={{ width: 10, height: 10 }} />}
                </span>
                {shapeWarning && <Callout tone="warn">{shapeWarning}</Callout>}
                {error && <Callout tone="warn">{error}</Callout>}
                <div className="xp-preview">
                  <div className="xp-widget-head">
                    <div className="xp-widget-title">
                      {draft.title || suggestedTitle(draft, measureMap, fieldMap)}
                    </div>
                    {preview && (
                      <div className="xp-widget-sub">
                        {preview.row_count} row{preview.row_count === 1 ? '' : 's'}
                        {preview.truncated ? ' (capped)' : ''}
                      </div>
                    )}
                  </div>
                  <div className={`xp-widget-body ${['table', 'pivot'].includes(draft.type) ? 'scroll' : ''}`}
                    style={{ minHeight: 260 }}>
                    <WidgetView widget={draft} result={preview} error={error} loading={busy && !preview} />
                  </div>
                </div>
              </div>

              {preview && (
                <div className="xp-field">
                  <div className="xp-row">
                    <button className="btn" style={{ fontSize: 12 }}
                      onClick={() => setShowSql((s) => !s)}>
                      {showSql ? 'Hide' : 'Show'} the query
                    </button>
                    <button className="btn" style={{ fontSize: 12 }}
                      onClick={() => api.exportQueryCsv(
                        draft.query, board,
                        (draft.title || 'widget').replace(/[^\w -]/g, ''),
                      )}>
                      Export CSV
                    </button>
                  </div>
                  {showSql && (
                    <>
                      <div className="xp-sql">{preview.sql}</div>
                      {preview.params?.length > 0 && (
                        <div className="xp-hint">
                          Values: {preview.params.join(' · ')}
                        </div>
                      )}
                      <div className="xp-hint">
                        Every figure above is computed by this statement in SQLite.
                        Money is summed in whole paise and divided once at the end,
                        so the totals are exact.
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="xp-modal-foot">
          {widget?.id && (
            <button className="btn danger" onClick={() => onDelete(widget)}>Delete</button>
          )}
          <div style={{ flex: 1 }} />
          <button className="btn" onClick={onCancel}>Cancel</button>
          <button className="btn primary" onClick={save}>
            {widget?.id ? 'Save changes' : 'Add to dashboard'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* A title the user did not have to write. Named widgets are the norm; making
   someone type "Money out by month" before they can save one is friction for
   no benefit. */
function suggestedTitle(draft, measureMap, fieldMap) {
  if (draft.type === 'text') return 'Note';
  const measure = draft.query.measures?.[0];
  const measureLabel = measure ? (measureMap[measure.field]?.label || measure.field) : 'Rows';
  const dimensionLabels = (draft.query.dimensions || [])
    .map((key) => fieldMap[key]?.label?.toLowerCase())
    .filter(Boolean);
  if (!dimensionLabels.length) return measureLabel;
  return `${measureLabel} by ${dimensionLabels.join(' and ')}`;
}
