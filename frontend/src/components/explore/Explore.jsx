import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../lib';
import { Callout, Empty } from '../ui';
import { FilterRow } from './FieldControls';
import WidgetEditor from './WidgetEditor';
import WidgetView from './WidgetView';
import { scopedKey } from '../../userStorage';

/* Explore: dashboards the user builds.
 *
 * A board is a set of saved QUERIES. Nothing here caches a figure - opening a
 * board re-runs every widget against the live ledger, so a correction made in
 * the Review tab shows up the next time this one is opened rather than
 * whenever someone remembers to rebuild something.
 *
 * The board's date range and filters are sent alongside each query and merged
 * server-side. That is what makes one control at the top re-cut twelve widgets
 * without rewriting any of them. */

const ROW_UNIT = 120;
/* Scoped per account - this holds a dashboard id, and one from
   somebody else's account matches nothing here. */
const LAST_BOARD_KEY = 'fa-explore-board';

/* Discrete widths rather than a free drag: twelve columns only divide cleanly
   so many ways, and a tile left at 7 columns leaves a gap nothing fits. */
const WIDTH_STEPS = [3, 4, 6, 8, 12];

function stepWidth(current, direction) {
  const index = WIDTH_STEPS.indexOf(current);
  const from = index === -1 ? WIDTH_STEPS.indexOf(6) : index;
  return WIDTH_STEPS[Math.min(WIDTH_STEPS.length - 1, Math.max(0, from + direction))];
}

function Modal({ title, children, onClose, footer, narrow = true }) {
  return (
    <div className="xp-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`xp-modal ${narrow ? 'narrow' : ''}`}>
        <div className="xp-modal-head">
          <div className="xp-modal-title">{title}</div>
          <div style={{ flex: 1 }} />
          <button className="xp-icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="xp-modal-body">{children}</div>
        {footer && <div className="xp-modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export default function Explore() {
  const [schema, setSchema] = useState(null);
  const [boards, setBoards] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [board, setBoard] = useState(null);
  const [results, setResults] = useState({});
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  const [editing, setEditing] = useState(null);      // widget being edited, or {} for new
  const [picker, setPicker] = useState(false);       // template picker
  const [templates, setTemplates] = useState([]);
  const [renaming, setRenaming] = useState(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [dragging, setDragging] = useState(null);
  const [dropTarget, setDropTarget] = useState(null);
  const importRef = useRef(null);

  // ---- initial load -------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.querySchema(), api.boards(), api.boardTemplates()])
      .then(([loadedSchema, loadedBoards, loadedTemplates]) => {
        if (cancelled) return;
        setSchema(loadedSchema);
        setBoards(loadedBoards);
        setTemplates(loadedTemplates);
        const remembered = localStorage.getItem(scopedKey(LAST_BOARD_KEY));
        const pick = loadedBoards.find((b) => b.id === remembered)
          || loadedBoards.find((b) => b.is_default)
          || loadedBoards[0];
        setActiveId(pick?.id || null);
      })
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, []);

  // ---- board + results ----------------------------------------------------
  const loadBoard = useCallback(async (id) => {
    if (!id) { setBoard(null); setResults({}); return; }
    setRunning(true);
    try {
      const loaded = await api.board(id);
      setBoard(loaded);
      const { results: ran } = await api.runBoard(id, loaded.filters);
      setResults(ran);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }, []);

  useEffect(() => {
    if (activeId) localStorage.setItem(scopedKey(LAST_BOARD_KEY), activeId);
    loadBoard(activeId);
  }, [activeId, loadBoard]);

  /* Re-runs every widget with a changed board filter, without re-fetching the
     board itself - the definitions have not moved, only the window over them. */
  const rerun = useCallback(async (filters) => {
    if (!board) return;
    setRunning(true);
    try {
      const { results: ran } = await api.runBoard(board.id, filters);
      setResults(ran);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }, [board]);

  const setBoardFilters = async (filters) => {
    setBoard((b) => ({ ...b, filters }));
    await api.updateBoard(board.id, { filters });
    rerun(filters);
  };

  const refreshBoards = async () => setBoards(await api.boards());

  // ---- widget CRUD --------------------------------------------------------
  const saveWidget = async (draft) => {
    const payload = {
      title: draft.title, type: draft.type, query: draft.query, viz: draft.viz,
      width: draft.width, height: draft.height,
    };
    if (draft.id) {
      await api.updateWidget(board.id, draft.id, payload);
    } else {
      await api.createWidget(board.id, payload);
    }
    setEditing(null);
    await loadBoard(board.id);
    await refreshBoards();
  };

  const removeWidget = async (widget) => {
    await api.deleteWidget(board.id, widget.id);
    setEditing(null);
    await loadBoard(board.id);
    await refreshBoards();
  };

  const duplicateWidget = async (widget) => {
    await api.createWidget(board.id, {
      title: `${widget.title} (copy)`, type: widget.type, query: widget.query,
      viz: widget.viz, width: widget.width, height: widget.height,
    });
    await loadBoard(board.id);
  };

  const resizeWidget = async (widget, patch) => {
    // Applied locally first: waiting for a round trip to see a tile change
    // width makes resizing feel broken even when it works.
    setBoard((b) => ({
      ...b,
      widgets: b.widgets.map((w) => (w.id === widget.id ? { ...w, ...patch } : w)),
    }));
    await api.updateWidget(board.id, widget.id, patch);
  };

  // ---- drag to reorder ----------------------------------------------------
  const onDrop = async (target) => {
    if (!dragging || dragging === target.id) { setDragging(null); setDropTarget(null); return; }
    const order = board.widgets.map((w) => w.id);
    const from = order.indexOf(dragging);
    const to = order.indexOf(target.id);
    order.splice(to, 0, ...order.splice(from, 1));

    const reordered = order.map((id) => board.widgets.find((w) => w.id === id));
    setBoard((b) => ({ ...b, widgets: reordered }));
    setDragging(null);
    setDropTarget(null);
    await api.saveLayout(board.id, reordered.map((w, i) => ({
      id: w.id, position: i, width: w.width, height: w.height,
    })));
  };

  // ---- board CRUD ---------------------------------------------------------
  const createBoard = async (templateKey) => {
    const { id } = await api.createBoard({ template: templateKey });
    setPicker(false);
    await refreshBoards();
    setActiveId(id);
  };

  const removeBoard = async () => {
    await api.deleteBoard(board.id);
    const remaining = await api.boards();
    setBoards(remaining);
    setActiveId(remaining[0]?.id || null);
  };

  const importBoard = async (file) => {
    try {
      const { id } = await api.importBoard(JSON.parse(await file.text()));
      await refreshBoards();
      setActiveId(id);
    } catch (e) {
      setError(`Could not import that file: ${e.message}`);
    }
  };

  // ---- render -------------------------------------------------------------
  if (loading) {
    return (
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', padding: 40 }}>
        <div className="spinner" /> Loading…
      </div>
    );
  }

  if (error && !schema) return <Callout tone="warn">{error}</Callout>;

  if (!boards.length) {
    return (
      <>
        <Empty title="No dashboards yet">
          Build one from a starting point, or start from a blank canvas and add
          your own widgets. Every widget is a saved question, re-answered from
          the ledger each time you open it.
        </Empty>
        <div style={{ maxWidth: 520, margin: '0 auto' }}>
          <div className="xp-templates">
            {templates.map((t) => (
              <button className="xp-template" key={t.key} onClick={() => createBoard(t.key)}>
                <div className="xp-template-name">{t.name}</div>
                <div className="xp-template-desc">
                  {t.description}
                  {t.widget_count > 0 && ` · ${t.widget_count} widgets`}
                </div>
              </button>
            ))}
          </div>
        </div>
      </>
    );
  }

  const boardFilters = board?.filters || {};
  const activeFilterCount = (boardFilters.filters || []).length;

  return (
    <>
      <div className="xp-bar">
        <div className="xp-boards">
          {boards.map((b) => (
            <button key={b.id}
              className={`xp-board-tab ${b.id === activeId ? 'active' : ''}`}
              onClick={() => setActiveId(b.id)}>
              {b.name}
              {b.is_default && <span style={{ opacity: .5, marginLeft: 5 }}>★</span>}
            </button>
          ))}
          <button className="xp-board-tab" onClick={() => setPicker(true)}>+ New</button>
        </div>

        {board && (
          <>
            <div className="xp-sep" />
            <select className="xp-select slim"
              value={boardFilters.date_range?.preset || 'all'}
              onChange={(e) => setBoardFilters({
                ...boardFilters,
                date_range: { ...boardFilters.date_range, preset: e.target.value },
              })}>
              {schema.date_presets.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
            {boardFilters.date_range?.preset === 'custom' && (
              <>
                <input className="xp-input slim" type="date"
                  value={boardFilters.date_range.start || ''}
                  onChange={(e) => setBoardFilters({
                    ...boardFilters,
                    date_range: { ...boardFilters.date_range, start: e.target.value },
                  })} />
                <input className="xp-input slim" type="date"
                  value={boardFilters.date_range.end || ''}
                  onChange={(e) => setBoardFilters({
                    ...boardFilters,
                    date_range: { ...boardFilters.date_range, end: e.target.value },
                  })} />
              </>
            )}
            <button className="btn" style={{ fontSize: 12 }} onClick={() => setFiltersOpen(true)}>
              Filters{activeFilterCount ? ` (${activeFilterCount})` : ''}
            </button>
            <button className="btn icon" title="Re-run every widget"
              onClick={() => rerun(boardFilters)} disabled={running}>
              {running ? <span className="spinner" style={{ width: 12, height: 12 }} /> : '↻'}
            </button>

            <div className="xp-sep" />
            <button className="btn" style={{ fontSize: 12 }}
              onClick={() => setEditing({})}>+ Widget</button>
            <select className="xp-select slim" value="" style={{ maxWidth: 110 }}
              onChange={async (e) => {
                const action = e.target.value;
                e.target.value = '';
                if (action === 'rename') setRenaming(board.name);
                if (action === 'duplicate') {
                  const { id } = await api.duplicateBoard(board.id);
                  await refreshBoards();
                  setActiveId(id);
                }
                if (action === 'default') {
                  await api.updateBoard(board.id, { is_default: true });
                  await refreshBoards();
                }
                if (action === 'export') api.exportBoard(board.id, board.name);
                if (action === 'import') importRef.current?.click();
                if (action === 'delete') removeBoard();
              }}>
              <option value="">Board…</option>
              <option value="rename">Rename</option>
              <option value="duplicate">Duplicate</option>
              <option value="default">Make it open first</option>
              <option value="export">Export as JSON</option>
              <option value="import">Import from JSON</option>
              <option value="delete">Delete this board</option>
            </select>
            <input ref={importRef} type="file" accept="application/json" hidden
              onChange={(e) => {
                const [file] = e.target.files || [];
                e.target.value = '';
                if (file) importBoard(file);
              }} />
          </>
        )}
      </div>

      {error && <Callout tone="warn" style={{ marginBottom: 12 }}>{error}</Callout>}

      {board && (
        <div className="xp-grid">
          {board.widgets.map((widget) => {
            const result = results[widget.id];
            const scrolls = ['table', 'pivot'].includes(widget.type);
            return (
              <div
                key={widget.id}
                className={`xp-widget ${dragging === widget.id ? 'dragging' : ''}`
                  + `${dropTarget === widget.id ? ' drop-target' : ''}`}
                style={{
                  gridColumn: `span ${Math.min(12, widget.width || 6)}`,
                  height: (widget.height || 2) * ROW_UNIT,
                }}
                onDragOver={(e) => { e.preventDefault(); setDropTarget(widget.id); }}
                onDragLeave={() => setDropTarget((t) => (t === widget.id ? null : t))}
                onDrop={() => onDrop(widget)}
              >
                <div className="xp-widget-head">
                  <span className="xp-drag" draggable title="Drag to reorder"
                    onDragStart={() => setDragging(widget.id)}
                    onDragEnd={() => { setDragging(null); setDropTarget(null); }}>⠿</span>
                  <div className="xp-widget-title" title={widget.title}>{widget.title}</div>
                  <div className="xp-actions">
                    <button className="xp-icon-btn" title="Narrower"
                      disabled={(widget.width || 6) <= WIDTH_STEPS[0]}
                      onClick={() => resizeWidget(widget, {
                        width: stepWidth(widget.width || 6, -1),
                      })}>‹</button>
                    <button className="xp-icon-btn" title="Wider"
                      disabled={(widget.width || 6) >= 12}
                      onClick={() => resizeWidget(widget, {
                        width: stepWidth(widget.width || 6, +1),
                      })}>›</button>
                    <button className="xp-icon-btn" title="Shorter"
                      disabled={(widget.height || 2) <= 1}
                      onClick={() => resizeWidget(widget, { height: (widget.height || 2) - 1 })}
                    >–</button>
                    <button className="xp-icon-btn" title="Taller"
                      disabled={(widget.height || 2) >= 6}
                      onClick={() => resizeWidget(widget, { height: (widget.height || 2) + 1 })}
                    >+</button>
                    <button className="xp-icon-btn" title="Duplicate"
                      onClick={() => duplicateWidget(widget)}>⧉</button>
                    <button className="xp-icon-btn" title="Edit"
                      onClick={() => setEditing(widget)}>✎</button>
                  </div>
                </div>
                <div className={`xp-widget-body ${scrolls ? 'scroll' : ''}`}>
                  <WidgetView widget={widget} result={result && !result.error ? result : null}
                    error={result?.error} loading={running && !result} />
                </div>
              </div>
            );
          })}

          <button className="xp-add" style={{ gridColumn: 'span 4' }}
            onClick={() => setEditing({})}>
            + Add a widget
          </button>
        </div>
      )}

      {editing && (
        <WidgetEditor
          schema={schema}
          widget={editing.id ? editing : null}
          board={boardFilters}
          onSave={saveWidget}
          onCancel={() => setEditing(null)}
          onDelete={removeWidget}
        />
      )}

      {picker && (
        <Modal title="New dashboard" onClose={() => setPicker(false)}>
          <div className="xp-templates">
            {templates.map((t) => (
              <button className="xp-template" key={t.key} onClick={() => createBoard(t.key)}>
                <div className="xp-template-name">{t.name}</div>
                <div className="xp-template-desc">
                  {t.description}
                  {t.widget_count > 0 && ` · ${t.widget_count} widgets`}
                </div>
              </button>
            ))}
          </div>
        </Modal>
      )}

      {renaming !== null && (
        <Modal title="Rename dashboard" onClose={() => setRenaming(null)}
          footer={(
            <>
              <div style={{ flex: 1 }} />
              <button className="btn" onClick={() => setRenaming(null)}>Cancel</button>
              <button className="btn primary" onClick={async () => {
                await api.updateBoard(board.id, { name: renaming });
                setBoard((b) => ({ ...b, name: renaming }));
                setRenaming(null);
                refreshBoards();
              }}>Save</button>
            </>
          )}>
          <input className="xp-input" style={{ width: '100%' }} value={renaming}
            onChange={(e) => setRenaming(e.target.value)} />
        </Modal>
      )}

      {filtersOpen && (
        <Modal title="Dashboard filters" onClose={() => setFiltersOpen(false)}
          footer={(
            <>
              <div className="xp-hint" style={{ flex: 1 }}>
                Applied on top of every widget&apos;s own filters.
              </div>
              <button className="btn primary" onClick={() => setFiltersOpen(false)}>Done</button>
            </>
          )}>
          <div className="xp-list">
            {(boardFilters.filters || []).map((f, i) => (
              <FilterRow
                key={i}
                filter={f}
                fields={schema.fields.filter((field) => field.filterable)}
                fieldMap={Object.fromEntries(schema.fields.map((field) => [field.key, field]))}
                opLabels={schema.op_labels}
                options={schema.options}
                onChange={(next) => setBoardFilters({
                  ...boardFilters,
                  filters: boardFilters.filters.map((one, j) => (j === i ? next : one)),
                })}
                onRemove={() => setBoardFilters({
                  ...boardFilters,
                  filters: boardFilters.filters.filter((_, j) => j !== i),
                })}
              />
            ))}
            <button className="btn" onClick={() => setBoardFilters({
              ...boardFilters,
              filters: [...(boardFilters.filters || []),
                { field: 'account_id', op: 'in', value: [] }],
            })}>
              + Add filter
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
