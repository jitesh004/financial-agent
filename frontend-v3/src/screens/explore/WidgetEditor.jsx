import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../core/api';
import { Button, Callout, Field, IconButton, Modal, Spinner } from '../../ui';
import { FieldSelect, FilterRow, groupOptions } from './fields';
import WidgetView, { TYPE_NEEDS, WIDGET_TYPES } from './WidgetView';

const WIDTHS = [[3, 'Quarter (3)'], [4, 'Third (4)'], [6, 'Half (6)'], [8, 'Two-Thirds (8)'], [12, 'Full Width (12)']];
const HEIGHTS = [[1, 'Compact (1)'], [2, 'Standard (2)'], [3, 'Tall (3)'], [4, 'Expanded (4)']];

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
    ...EMPTY,
    ...widget,
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
    () => Object.fromEntries(schema.measures.map((m) => [m.key, m])),
    [schema.measures]
  );

  const setQuery = (patch) => setDraft((d) => ({ ...d, query: { ...d.query, ...patch } }));
  const setViz = (patch) => setDraft((d) => ({ ...d, viz: { ...d.viz, ...patch } }));

  const signature = JSON.stringify(draft.query);

  useEffect(() => {
    if (draft.type === 'text') {
      setPreview(null);
      setError(null);
      return undefined;
    }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      setBusy(true);
      api.runQuery(draft.query, board)
        .then((result) => { setPreview(result); setError(null); })
        .catch((e) => { setError(e.message); setPreview(null); })
        .finally(() => setBusy(false));
    }, 300);
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
      return `A ${draft.type} requires at least ${minDim} grouping dimension${minDim > 1 ? 's' : ''}.`;
    }
    if (dimensions.length > maxDim) {
      return `A ${draft.type} uses only the first ${maxDim} grouping; extra dimensions will be disregarded.`;
    }
    if (measures.length < minMeasure) return 'Specify at least one metric measure.';
    return null;
  })();

  const suggested = suggestTitle(draft, measureMap, fieldMap);

  return (
    <Modal
      size="lg"
      title={widget?.id ? 'Configure Custom Widget' : 'Add Analytics Widget'}
      onClose={onCancel}
      footer={(
        <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', alignItems: 'center' }}>
          <div>
            {widget?.id && (
              <Button variant="danger" size="sm" onClick={() => onDelete(widget)}>
                Delete Widget
              </Button>
            )}
          </div>
          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
            <Button size="sm" onClick={onCancel}>Cancel</Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => onSave({ ...draft, title: draft.title.trim() || suggested })}
            >
              {widget?.id ? 'Save Configuration' : 'Mount to Dashboard'}
            </Button>
          </div>
        </div>
      )}
    >
      <div className="grid-2">
        {/* Left: Query Configuration */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)', maxHeight: 540, overflowY: 'auto', paddingRight: 4 }}>
          <Field label="Widget Title">
            <input
              className="input"
              value={draft.title}
              placeholder={suggested}
              onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
            />
          </Field>

          <div>
            <div className="small font-medium muted" style={{ marginBottom: 6 }}>Visualization Format</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {WIDGET_TYPES.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  className={`btn btn-sm ${draft.type === t.key ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() => setDraft((d) => ({ ...d, type: t.key }))}
                >
                  <span aria-hidden="true" style={{ marginRight: 4 }}>{t.glyph}</span> {t.label}
                </button>
              ))}
            </div>
          </div>

          {draft.type === 'text' ? (
            <Field label="Audit Memo Text">
              <textarea
                className="input"
                rows={6}
                value={draft.viz?.content || ''}
                placeholder="Markdown narrative, notes, or analytical rationale…"
                onChange={(e) => setViz({ content: e.target.value })}
              />
            </Field>
          ) : (
            <>
              <div>
                <div className="small font-medium muted" style={{ marginBottom: 6 }}>Grouping Dimensions</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {dimensions.map((key, i) => (
                    <div key={`${key}-${i}`} style={{ display: 'flex', gap: 6 }}>
                      <FieldSelect
                        fields={groupable}
                        value={key}
                        style={{ flex: 1 }}
                        onChange={(next) => setDimension(i, next)}
                      />
                      <IconButton icon="x" label="Remove dimension" size="sm" onClick={() => setDimension(i, null)} />
                    </div>
                  ))}
                  {dimensions.length < 2 && (
                    <Button
                      size="sm"
                      onClick={() => setQuery({
                        dimensions: [
                          ...dimensions,
                          groupable.find((f) => !dimensions.includes(f.key))?.key || groupable[0].key,
                        ],
                      })}
                    >
                      + Add Dimension
                    </Button>
                  )}
                </div>
              </div>

              <div>
                <div className="small font-medium muted" style={{ marginBottom: 6 }}>Measures & Aggregations</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {measures.map((m, i) => (
                    <div key={i} style={{ display: 'flex', gap: 6 }}>
                      <select
                        className="select"
                        value={m.field}
                        style={{ flex: 1 }}
                        onChange={(e) => setMeasure(i, { field: e.target.value })}
                      >
                        {groupOptions(schema.measures).map(([group, items]) => (
                          <optgroup label={group} key={group}>
                            {items.map((one) => (
                              <option key={one.key} value={one.key}>{one.label}</option>
                            ))}
                          </optgroup>
                        ))}
                      </select>
                      <select
                        className="select"
                        value={m.agg}
                        style={{ maxWidth: 120 }}
                        onChange={(e) => setMeasure(i, { agg: e.target.value })}
                      >
                        {(measureMap[m.field]?.aggs || ['sum']).map((agg) => (
                          <option key={agg} value={agg}>{schema.agg_labels[agg] || agg}</option>
                        ))}
                      </select>
                      <IconButton icon="x" label="Remove measure" size="sm" onClick={() => setQuery({ measures: measures.filter((_, j) => j !== i) })} />
                    </div>
                  ))}
                  <Button
                    size="sm"
                    onClick={() => setQuery({
                      measures: [...measures, { field: 'outflow', agg: 'sum' }],
                    })}
                  >
                    + Add Measure
                  </Button>
                </div>
              </div>

              <div>
                <div className="small font-medium muted" style={{ marginBottom: 6 }}>Filters</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
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
                  <Button
                    size="sm"
                    onClick={() => setQuery({
                      filters: [...filters, { field: 'category', op: 'in', value: [] }],
                    })}
                  >
                    + Add Filter
                  </Button>
                </div>
              </div>
            </>
          )}

          {/* Sizing Grid */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-3)', paddingTop: 'var(--space-2)', borderTop: '1px solid var(--border-subtle)' }}>
            <div>
              <div className="tiny muted font-medium" style={{ marginBottom: 4 }}>Tile Width</div>
              <select
                className="select"
                value={draft.width}
                onChange={(e) => setDraft((d) => ({ ...d, width: Number(e.target.value) }))}
              >
                {WIDTHS.map(([w, label]) => (
                  <option key={w} value={w}>{label}</option>
                ))}
              </select>
            </div>
            <div>
              <div className="tiny muted font-medium" style={{ marginBottom: 4 }}>Tile Height</div>
              <select
                className="select"
                value={draft.height}
                onChange={(e) => setDraft((d) => ({ ...d, height: Number(e.target.value) }))}
              >
                {HEIGHTS.map(([h, label]) => (
                  <option key={h} value={h}>{label}</option>
                ))}
              </select>
            </div>
          </div>
        </div>

        {/* Right: Real-time Live Query Preview */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          <div className="small font-semibold muted">Real-Time Ledger Query Preview</div>

          {shapeWarning && <Callout tone="warn">{shapeWarning}</Callout>}
          {error && <Callout tone="neg">{error}</Callout>}

          <div
            style={{
              height: 320,
              background: 'var(--surface-2)',
              borderRadius: 'var(--radius-lg)',
              border: '1px solid var(--border-subtle)',
              overflow: 'hidden',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--border-subtle)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span className="font-semibold small">{draft.title || suggested}</span>
              {preview && (
                <span className="tiny muted">
                  {preview.row_count} row{preview.row_count === 1 ? '' : 's'}
                  {preview.truncated ? ' (capped)' : ''}
                </span>
              )}
            </div>

            <div style={{ flex: 1, minHeight: 0 }}>
              <WidgetView
                widget={draft}
                result={preview}
                error={error}
                running={busy && !preview}
              />
            </div>
          </div>

          {preview && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
              <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
                <Button size="sm" onClick={() => setShowSql((s) => !s)}>
                  {showSql ? 'Hide SQL' : 'Inspect SQL Statement'}
                </Button>
                <Button
                  size="sm"
                  onClick={() => api.exportQueryCsv(
                    draft.query,
                    board,
                    (draft.title || 'widget').replace(/[^\w -]/g, '')
                  )}
                >
                  Export CSV
                </Button>
              </div>

              {showSql && (
                <div style={{ padding: 'var(--space-3)', background: 'var(--surface-3)', borderRadius: 'var(--radius-md)', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                  <pre style={{ margin: 0, whiteSpace: 'pre-wrap', color: 'var(--brand-primary)' }}>{preview.sql}</pre>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}

function suggestTitle(draft, measureMap, fieldMap) {
  if (draft.type === 'text') return 'Audit Note';
  const measure = draft.query.measures?.[0];
  const measureLabel = measure ? (measureMap[measure.field]?.label || measure.field) : 'Transactions';
  const dims = (draft.query.dimensions || [])
    .map((key) => fieldMap[key]?.label?.toLowerCase()).filter(Boolean);
  if (!dims.length) return measureLabel;
  return `${measureLabel} by ${dims.join(' and ')}`;
}
