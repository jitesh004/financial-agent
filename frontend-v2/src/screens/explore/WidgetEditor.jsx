/* The query builder.
 *
 * Every control on this panel is generated from /api/query/schema. Nothing
 * about which fields exist, which operators apply to them or which
 * aggregations a measure allows is written down here - add a dimension on the
 * server and it appears in these dropdowns with no change on this side.
 *
 * The preview runs the real endpoint against the real ledger while you build,
 * because the alternative is saving a widget to find out what it says.
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../core/api';
import { Button, Callout, Field, IconButton, Modal, Spinner } from '../../ui';
import { FieldSelect, FilterRow, groupOptions } from './fields';
import WidgetView, { TYPE_NEEDS, WIDGET_TYPES } from './WidgetView';

const WIDTHS = [[3, 'Quarter'], [4, 'Third'], [6, 'Half'], [8, 'Two thirds'], [12, 'Full']];
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

  const { fields } = schema;
  const fieldMap = useMemo(() => Object.fromEntries(fields.map((f) => [f.key, f])), [fields]);
  const groupable = useMemo(() => fields.filter((f) => f.groupable), [fields]);
  const filterable = useMemo(() => fields.filter((f) => f.filterable), [fields]);
  const measureMap = useMemo(
    () => Object.fromEntries(schema.measures.map((m) => [m.key, m])), [schema.measures]);

  const setQuery = (patch) => setDraft((d) => ({ ...d, query: { ...d.query, ...patch } }));
  const setViz = (patch) => setDraft((d) => ({ ...d, viz: { ...d.viz, ...patch } }));

  const signature = JSON.stringify(draft.query);

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
  }, [signature, draft.type, board]);

  const dimensions = draft.query.dimensions || [];
  const measures = draft.query.measures || [];
  const filters = draft.query.filters || [];

  const setDimension = (index, key) => {
    const next = [...dimensions];
    if (key) next[index] = key; else next.splice(index, 1);
    setQuery({ dimensions: next });
  };

  const setMeasure = (index, patch) => {
    const next = measures.map((m, i) => (i === index ? { ...m, ...patch } : m));
    // Switching measure can strand an aggregation the new one does not offer.
    if (patch.field) {
      const allowed = measureMap[patch.field]?.aggs || ['sum'];
      if (!allowed.includes(next[index].agg)) next[index].agg = allowed[0];
    }
    setQuery({ measures: next });
  };

  const needs = TYPE_NEEDS[draft.type] || TYPE_NEEDS.table;
  const shapeWarning = (() => {
    if (draft.type === 'text') return null;
    const [minDim, maxDim] = needs.dimensions;
    const [minMeasure] = needs.measures;
    if (dimensions.length < minDim) {
      return `A ${draft.type} needs at least ${minDim} grouping${minDim > 1 ? 's' : ''}.`;
    }
    if (dimensions.length > maxDim) {
      return `A ${draft.type} uses only the first ${maxDim} grouping`
        + `${maxDim > 1 ? 's' : ''}; the rest are ignored.`;
    }
    if (measures.length < minMeasure) return 'Add at least one measure.';
    return null;
  })();

  const suggested = suggestTitle(draft, measureMap, fieldMap);

  return (
    <Modal
      size="lg"
      title={widget?.id ? 'Edit widget' : 'New widget'}
      onClose={onCancel}
      footer={(
        <>
          {widget?.id && (
            <Button variant="danger" onClick={() => onDelete(widget)}>Delete</Button>
          )}
          <span className="spacer" />
          <Button onClick={onCancel}>Cancel</Button>
          <Button variant="primary"
            onClick={() => onSave({ ...draft, title: draft.title.trim() || suggested })}>
            {widget?.id ? 'Save changes' : 'Add to dashboard'}
          </Button>
        </>
      )}
    >
      <div className="split">
        {/* ---- builder ---- */}
        <div className="col">
          <Field label="Title">
            <input value={draft.title} placeholder={suggested}
              onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))} />
          </Field>

          <div className="field">
            <span className="field-label">Show as</span>
            <div className="row tight">
              {WIDGET_TYPES.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  className={`tog ${draft.type === t.key ? 'on' : ''}`}
                  onClick={() => setDraft((d) => ({ ...d, type: t.key }))}
                >
                  <span aria-hidden="true">{t.glyph}</span> {t.label}
                </button>
              ))}
            </div>
          </div>

          {draft.type === 'text' ? (
            <Field label="Note">
              <textarea rows={6} value={draft.viz.text || ''}
                placeholder="A heading, a caveat, a reminder of what this section means."
                onChange={(e) => setViz({ text: e.target.value })} />
            </Field>
          ) : (
            <>
              <div className="field">
                <span className="field-label">Group by</span>
                <div className="col" style={{ gap: 6 }}>
                  {dimensions.map((key, i) => (
                    <div className="row" key={`${key}-${i}`}>
                      <FieldSelect fields={groupable} value={key} style={{ flex: 1 }}
                        onChange={(next) => setDimension(i, next)} />
                      <IconButton icon="x" label="Remove" size="sm" className="ghost"
                        onClick={() => setDimension(i, null)} />
                    </div>
                  ))}
                  {dimensions.length < 2 && (
                    <Button size="sm" icon="plus" onClick={() => setQuery({
                      dimensions: [...dimensions,
                        groupable.find((f) => !dimensions.includes(f.key))?.key
                        || groupable[0].key],
                    })}>
                      Add grouping
                    </Button>
                  )}
                  {dimensions.length === 0 && (
                    <div className="tiny dim">
                      No grouping: one row for the whole range, which is what a Number
                      tile wants.
                    </div>
                  )}
                </div>
              </div>

              <div className="field">
                <span className="field-label">Measure</span>
                <div className="col" style={{ gap: 6 }}>
                  {measures.map((m, i) => (
                    <div className="row" key={i}>
                      <select value={m.field} style={{ flex: 1 }}
                        onChange={(e) => setMeasure(i, { field: e.target.value })}>
                        {groupOptions(schema.measures).map(([group, items]) => (
                          <optgroup label={group} key={group}>
                            {items.map((one) => (
                              <option key={one.key} value={one.key}>{one.label}</option>
                            ))}
                          </optgroup>
                        ))}
                      </select>
                      <select value={m.agg} style={{ maxWidth: 140 }}
                        onChange={(e) => setMeasure(i, { agg: e.target.value })}>
                        {(measureMap[m.field]?.aggs || ['sum']).map((agg) => (
                          <option key={agg} value={agg}>{schema.agg_labels[agg] || agg}</option>
                        ))}
                      </select>
                      <IconButton icon="x" label="Remove" size="sm" className="ghost"
                        onClick={() => setQuery({ measures: measures.filter((_, j) => j !== i) })} />
                    </div>
                  ))}
                  <Button size="sm" icon="plus" onClick={() => setQuery({
                    measures: [...measures, { field: 'outflow', agg: 'sum' }],
                  })}>
                    Add measure
                  </Button>
                </div>
              </div>

              <div className="field">
                <span className="field-label">Filters</span>
                <div className="col" style={{ gap: 8 }}>
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
                      onRemove={() => setQuery({ filters: filters.filter((_, j) => j !== i) })}
                    />
                  ))}
                  <Button size="sm" icon="plus" onClick={() => setQuery({
                    filters: [...filters, { field: 'category', op: 'in', value: [] }],
                  })}>
                    Add filter
                  </Button>
                </div>
              </div>

              <div className="field">
                <span className="field-label">Date range</span>
                <select
                  value={draft.query.date_range?.preset || 'inherit'}
                  onChange={(e) => setQuery({
                    date_range: { ...draft.query.date_range, preset: e.target.value },
                    pin_date: e.target.value !== 'inherit',
                  })}
                >
                  <option value="inherit">Follow the dashboard</option>
                  {schema.date_presets.map((p) => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
                {draft.query.date_range?.preset === 'custom' && (
                  <div className="row">
                    <input type="date" value={draft.query.date_range.start || ''}
                      onChange={(e) => setQuery({
                        date_range: { ...draft.query.date_range, start: e.target.value },
                      })} />
                    <input type="date" value={draft.query.date_range.end || ''}
                      onChange={(e) => setQuery({
                        date_range: { ...draft.query.date_range, end: e.target.value },
                      })} />
                  </div>
                )}
                {draft.query.date_range?.preset === 'custom_months' && (
                  <div className="row">
                    <input type="month" aria-label="First month"
                      value={draft.query.date_range.start_month || ''}
                      onChange={(e) => setQuery({
                        date_range: { ...draft.query.date_range, start_month: e.target.value },
                      })} />
                    <input type="month" aria-label="Last month"
                      value={draft.query.date_range.end_month || ''}
                      onChange={(e) => setQuery({
                        date_range: { ...draft.query.date_range, end_month: e.target.value },
                      })} />
                  </div>
                )}
                {draft.query.date_range?.preset !== 'inherit' && (
                  <label className="check" style={{ marginTop: 6 }}>
                    <input type="checkbox" checked={Boolean(draft.query.pin_date)}
                      onChange={(e) => setQuery({ pin_date: e.target.checked })} />
                    Keep this range even when the dashboard&apos;s changes
                  </label>
                )}
              </div>

              <div className="field">
                <span className="field-label">Options</span>
                <div className="col" style={{ gap: 6 }}>
                  <label className="check">
                    <input type="checkbox" checked={draft.query.exclude_mirror_legs !== false}
                      onChange={(e) => setQuery({ exclude_mirror_legs: e.target.checked })} />
                    Drop mirror legs of internal transfers
                  </label>
                  <label className="check">
                    <input type="checkbox" checked={draft.query.exclude_excluded !== false}
                      onChange={(e) => setQuery({ exclude_excluded: e.target.checked })} />
                    Drop rows you excluded by hand
                  </label>
                  <label className="check">
                    <input type="checkbox" checked={Boolean(draft.query.compare)}
                      onChange={(e) => setQuery({ compare: e.target.checked })} />
                    Compare with the period before
                  </label>
                  {(draft.type === 'bar' || draft.type === 'area') && (
                    <label className="check">
                      <input type="checkbox" checked={Boolean(draft.viz.stacked)}
                        onChange={(e) => setViz({ stacked: e.target.checked })} />
                      Stack the series
                    </label>
                  )}
                  {draft.type === 'hbar' && (
                    <label className="check">
                      <input type="checkbox" checked={draft.viz.show_share !== false}
                        onChange={(e) => setViz({ show_share: e.target.checked })} />
                      Show each row&apos;s share of the total
                    </label>
                  )}
                  {draft.type === 'pivot' && (
                    <label className="check">
                      <input type="checkbox" checked={draft.viz.show_totals !== false}
                        onChange={(e) => setViz({ show_totals: e.target.checked })} />
                      Show a totals row
                    </label>
                  )}

                  <div className="row" style={{ marginTop: 4 }}>
                    <select value={draft.query.sort?.[0]?.key || ''} style={{ flex: 1 }}
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
                    <select value={draft.query.sort?.[0]?.dir || 'desc'}
                      disabled={!draft.query.sort?.length} style={{ maxWidth: 140 }}
                      onChange={(e) => setQuery({
                        sort: [{ key: draft.query.sort[0].key, dir: e.target.value }],
                      })}>
                      <option value="desc">Highest first</option>
                      <option value="asc">Lowest first</option>
                    </select>
                  </div>
                  <div className="row">
                    <span className="tiny dim">Row limit</span>
                    <input type="number" min="1" max={schema.max_rows}
                      value={draft.query.limit || 200} style={{ width: 110 }}
                      onChange={(e) => setQuery({ limit: Number(e.target.value) || 200 })} />
                  </div>
                </div>
              </div>
            </>
          )}

          <div className="field">
            <span className="field-label">Size on the board</span>
            <div className="row">
              <select value={draft.width} style={{ flex: 1 }}
                onChange={(e) => setDraft((d) => ({ ...d, width: Number(e.target.value) }))}>
                {WIDTHS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
              <select value={draft.height} style={{ flex: 1 }}
                onChange={(e) => setDraft((d) => ({ ...d, height: Number(e.target.value) }))}>
                {HEIGHTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
          </div>
        </div>

        {/* ---- live preview ---- */}
        <div className="col">
          <div className="row">
            <span className="field-label">Preview</span>
            {busy && <Spinner sm />}
          </div>
          {shapeWarning && <Callout tone="warn">{shapeWarning}</Callout>}
          {error && <Callout tone="warn">{error}</Callout>}

          <div className="tile" style={{ height: 320 }}>
            <div className="tile-head">
              <div className="tile-title">{draft.title || suggested}</div>
              {preview && (
                <span className="tiny dim">
                  {preview.row_count} row{preview.row_count === 1 ? '' : 's'}
                  {preview.truncated ? ' (capped)' : ''}
                </span>
              )}
            </div>
            <div className={`tile-body
              ${['table', 'pivot', 'hbar', 'text'].includes(draft.type) ? 'scroll' : ''}
              ${['table', 'pivot'].includes(draft.type) ? 'flush' : ''}`}>
              <WidgetView widget={draft} result={preview} error={error}
                loading={busy && !preview} />
            </div>
          </div>

          {preview && (
            <div className="col">
              <div className="row">
                <Button size="sm" onClick={() => setShowSql((s) => !s)}>
                  {showSql ? 'Hide' : 'Show'} the query
                </Button>
                <Button size="sm" icon="download" onClick={() => api.exportQueryCsv(
                  draft.query, board, (draft.title || 'widget').replace(/[^\w -]/g, ''))}>
                  Export CSV
                </Button>
              </div>
              {showSql && (
                <>
                  <pre className="code-block">{preview.sql}</pre>
                  {preview.params?.length > 0 && (
                    <div className="tiny dim">Values: {preview.params.join(' · ')}</div>
                  )}
                  <div className="tiny dim">
                    Every figure above is computed by this statement in the database. Money
                    is summed in whole paise and divided once at the end, so the totals are
                    exact.
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}

/* A title nobody had to write. Named widgets are the norm; making somebody
   type "Money out by month" before they can save one is friction for no
   benefit. */
function suggestTitle(draft, measureMap, fieldMap) {
  if (draft.type === 'text') return 'Note';
  const measure = draft.query.measures?.[0];
  const measureLabel = measure ? (measureMap[measure.field]?.label || measure.field) : 'Rows';
  const dims = (draft.query.dimensions || [])
    .map((key) => fieldMap[key]?.label?.toLowerCase()).filter(Boolean);
  if (!dims.length) return measureLabel;
  return `${measureLabel} by ${dims.join(' and ')}`;
}

