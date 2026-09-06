/* Explore: dashboards you build.
 *
 * A board is a set of saved QUERIES. Nothing here caches a figure - opening a
 * board re-runs every widget against the live ledger, so a correction made in
 * Review shows up the next time this is opened rather than whenever somebody
 * remembers to rebuild something.
 *
 * The board's date range and filters are sent alongside each query and merged
 * server-side. That is what makes one control at the top re-cut twelve widgets
 * without rewriting any of them.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { useRouteParam } from '../core/router';
import { usePrefs } from '../core/prefs';
import { useToast } from '../core/toast';
import { read, write } from '../core/storage';
import {
  Button, Callout, Empty, Icon, IconButton, Loading, Menu, MenuItem, Modal, Select, Spinner,
} from '../ui';
import { FilterRow } from './explore/fields';
import WidgetEditor from './explore/WidgetEditor';
import WidgetView from './explore/WidgetView';
import { useChartKeyframes } from '../ui/charts';

const ROW_UNIT = 118;
const LAST_BOARD = 'prism-explore-board';

/* Discrete widths rather than a free drag: twelve columns only divide cleanly
   so many ways, and a tile left at 7 columns leaves a gap nothing fits. */
const WIDTH_STEPS = [3, 4, 6, 8, 12];
const stepWidth = (current, dir) => {
  const i = WIDTH_STEPS.indexOf(current);
  const from = i === -1 ? WIDTH_STEPS.indexOf(6) : i;
  return WIDTH_STEPS[Math.min(WIDTH_STEPS.length - 1, Math.max(0, from + dir))];
};

export default function Explore() {
  useChartKeyframes();
  const toast = useToast();
  const [prefs] = usePrefs();
  const [boardId, setBoardId] = useRouteParam('board', '');

  const schemaQ = useQuery('query-schema', () => api.querySchema());
  const boardsQ = useQuery('boards', () => api.boards());
  const templatesQ = useQuery('board-templates', () => api.boardTemplates());

  const [board, setBoard] = useState(null);
  const [results, setResults] = useState({});
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  const [editing, setEditing] = useState(null);
  const [picker, setPicker] = useState(false);
  const [renaming, setRenaming] = useState(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [dragging, setDragging] = useState(null);
  const [dropTarget, setDropTarget] = useState(null);
  const importRef = useRef(null);

  const boards = boardsQ.data || [];
  const schema = schemaQ.data;

  /* Which board to open: the URL first, then the one you were last on, then
     the default, then whatever exists. */
  useEffect(() => {
    if (!boards.length || boardId) return;
    const remembered = read(LAST_BOARD, null);
    const pick = boards.find((b) => b.id === remembered)
      || boards.find((b) => b.is_default) || boards[0];
    if (pick) setBoardId(pick.id);
  }, [boards, boardId, setBoardId]);

  const loadBoard = useCallback(async (id) => {
    if (!id) { setBoard(null); setResults({}); return; }
    setRunning(true);
    try {
      const loaded = await api.board(id);
      setBoard(loaded);
      const { results: ran } = await api.runBoard(id, loaded.filters);
      setResults(ran);
      setError(null);
    } catch (e) { setError(e.message); }
    finally { setRunning(false); }
  }, []);

  useEffect(() => {
    if (boardId) write(LAST_BOARD, boardId);
    loadBoard(boardId);
  }, [boardId, loadBoard]);

  /* Re-runs every widget with a changed board filter, without re-fetching the
     board itself - the definitions have not moved, only the window over them. */
  const rerun = useCallback(async (filters) => {
    if (!board) return;
    setRunning(true);
    try {
      const { results: ran } = await api.runBoard(board.id, filters);
      setResults(ran);
    } catch (e) { setError(e.message); }
    finally { setRunning(false); }
  }, [board]);

  const setBoardFilters = async (filters) => {
    setBoard((b) => ({ ...b, filters }));
    await api.updateBoard(board.id, { filters });
    rerun(filters);
  };

  const saveWidget = async (draft) => {
    const payload = {
      title: draft.title, type: draft.type, query: draft.query, viz: draft.viz,
      width: draft.width, height: draft.height,
    };
    if (draft.id) await api.updateWidget(board.id, draft.id, payload);
    else await api.createWidget(board.id, payload);
    setEditing(null);
    await loadBoard(board.id);
    boardsQ.refetch();
  };

  const resizeWidget = async (widget, patch) => {
    // Applied locally first: waiting for a round trip to see a tile change
    // width makes resizing feel broken even when it works.
    setBoard((b) => ({
      ...b, widgets: b.widgets.map((w) => (w.id === widget.id ? { ...w, ...patch } : w)),
    }));
    await api.updateWidget(board.id, widget.id, patch);
  };

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

  const createBoard = async (templateKey) => {
    const { id } = await api.createBoard({ template: templateKey });
    setPicker(false);
    await boardsQ.refetch();
    setBoardId(id);
  };

  if (schemaQ.loading || boardsQ.loading) return <Loading label="Reading your dashboards…" />;
  if (schemaQ.error) return <Callout tone="warn">{schemaQ.error.message}</Callout>;

  const templates = templatesQ.data || [];

  if (!boards.length) {
    return (
      <>
        <Empty title="No dashboards yet" icon="compass">
          Build one from a starting point, or start from a blank canvas and add your own
          widgets. Every widget is a saved question, re-answered from the ledger each time
          you open it.
        </Empty>
        <div style={{ maxWidth: 560, margin: '0 auto' }}>
          <div className="col">
            {templates.map((t) => (
              <button className="card" key={t.key} onClick={() => createBoard(t.key)}
                style={{ textAlign: 'left', cursor: 'pointer', padding: 14, border: 0 }}>
                <div style={{ fontWeight: 620 }}>{t.name}</div>
                <div className="small muted">
                  {t.description}{t.widget_count > 0 && ` · ${t.widget_count} widgets`}
                </div>
              </button>
            ))}
          </div>
        </div>
      </>
    );
  }

  const filters = board?.filters || {};
  const activeFilters = (filters.filters || []).length;

  return (
    <>
      {/* ---- board bar ---- */}
      <div className="row">
        <div className="seg">
          {boards.map((b) => (
            <button
              key={b.id}
              type="button"
              className={`seg-btn ${b.id === boardId ? 'active' : ''}`}
              onClick={() => setBoardId(b.id)}
            >
              {b.name}
              {b.is_default && <span style={{ opacity: 0.5, marginLeft: 5 }}>★</span>}
            </button>
          ))}
          <button type="button" className="seg-btn" onClick={() => setPicker(true)}>+ New</button>
        </div>

        {board && (
          <>
            <Select
              value={filters.date_range?.preset || 'all'}
              onChange={(v) => setBoardFilters({
                ...filters, date_range: { ...filters.date_range, preset: v },
              })}
              options={schema.date_presets.map((p) => [p.value, p.label])}
              aria-label="Date range"
            />
            {filters.date_range?.preset === 'custom' && (
              <>
                <input type="date" value={filters.date_range.start || ''}
                  onChange={(e) => setBoardFilters({
                    ...filters, date_range: { ...filters.date_range, start: e.target.value },
                  })} />
                <input type="date" value={filters.date_range.end || ''}
                  onChange={(e) => setBoardFilters({
                    ...filters, date_range: { ...filters.date_range, end: e.target.value },
                  })} />
              </>
            )}
            {/* Months rather than dates, which is how every preset is
                resolved: whole ACCOUNTING months, so a salary paid on the 1st
                counts in the month it is pay for. */}
            {filters.date_range?.preset === 'custom_months' && (
              <>
                <input type="month" aria-label="First month"
                  value={filters.date_range.start_month || ''}
                  onChange={(e) => setBoardFilters({
                    ...filters,
                    date_range: { ...filters.date_range, start_month: e.target.value },
                  })} />
                <input type="month" aria-label="Last month"
                  value={filters.date_range.end_month || ''}
                  onChange={(e) => setBoardFilters({
                    ...filters,
                    date_range: { ...filters.date_range, end_month: e.target.value },
                  })} />
              </>
            )}
            <Button size="sm" icon="filter" onClick={() => setFiltersOpen(true)}>
              Filters{activeFilters ? ` (${activeFilters})` : ''}
            </Button>
            <IconButton icon="refresh" label="Re-run every widget" size="sm"
              disabled={running} onClick={() => rerun(filters)} />
            {running && <Spinner sm />}

            <span className="spacer" />

            <Button size="sm" icon="plus" onClick={() => setEditing({})}>Widget</Button>
            <Menu
              width={230}
              trigger={<Button size="sm" icon="sliders">Board</Button>}
            >
              {(close) => (
                <>
                  <MenuItem icon="edit" onClick={() => { close(); setRenaming(board.name); }}>
                    Rename
                  </MenuItem>
                  <MenuItem icon="copy" onClick={async () => {
                    close();
                    const { id } = await api.duplicateBoard(board.id);
                    await boardsQ.refetch();
                    setBoardId(id);
                  }}>
                    Duplicate
                  </MenuItem>
                  <MenuItem icon="target" onClick={async () => {
                    close();
                    await api.updateBoard(board.id, { is_default: true });
                    boardsQ.refetch();
                  }}>
                    Make it open first
                  </MenuItem>
                  <div className="pop-sep" />
                  <MenuItem icon="download"
                    onClick={() => { close(); api.exportBoard(board.id, board.name); }}>
                    Export as JSON
                  </MenuItem>
                  <MenuItem icon="upload"
                    onClick={() => { close(); importRef.current?.click(); }}>
                    Import from JSON
                  </MenuItem>
                  <div className="pop-sep" />
                  <MenuItem icon="trash" onClick={async () => {
                    close();
                    await api.deleteBoard(board.id);
                    const remaining = await api.boards();
                    boardsQ.refetch();
                    setBoardId(remaining[0]?.id || '');
                  }}>
                    Delete this board
                  </MenuItem>
                </>
              )}
            </Menu>
            <input ref={importRef} type="file" accept="application/json" hidden
              onChange={async (e) => {
                const [file] = e.target.files || [];
                e.target.value = '';
                if (!file) return;
                try {
                  const { id } = await api.importBoard(JSON.parse(await file.text()));
                  await boardsQ.refetch();
                  setBoardId(id);
                } catch (err) { toast.fail('That file could not be imported', err.message); }
              }} />
          </>
        )}
      </div>

      {error && <Callout tone="warn">{error}</Callout>}

      {board && (
        <div className="board">
          {board.widgets.map((widget) => {
            const result = results[widget.id];
            /* A ranked list or a block of text can be longer than the tile it is
               in; a stat, a donut and the cartesian charts all size themselves
               to the box. Only the first two sit flush. */
            const flush = ['table', 'pivot'].includes(widget.type);
            const scrolls = flush || ['hbar', 'text'].includes(widget.type);
            return (
              <div
                key={widget.id}
                className={`tile ${dragging === widget.id ? 'dragging' : ''}`
                  + `${dropTarget === widget.id ? ' drop' : ''}`}
                style={{
                  gridColumn: `span ${Math.min(12, widget.width || 6)}`,
                  height: (widget.height || 2) * ROW_UNIT,
                }}
                onDragOver={(e) => { e.preventDefault(); setDropTarget(widget.id); }}
                onDragLeave={() => setDropTarget((t) => (t === widget.id ? null : t))}
                onDrop={() => onDrop(widget)}
              >
                <div className="tile-head">
                  <span
                    className="tile-drag" draggable title="Drag to reorder"
                    onDragStart={() => setDragging(widget.id)}
                    onDragEnd={() => { setDragging(null); setDropTarget(null); }}
                  >
                    <Icon name="grip" size={13} />
                  </span>
                  <div className="tile-title" title={widget.title}>{widget.title}</div>
                  <div className="tile-tools">
                    <IconButton icon="chevron-left" label="Narrower" size="xs" className="ghost"
                      disabled={(widget.width || 6) <= WIDTH_STEPS[0]}
                      onClick={() => resizeWidget(widget, {
                        width: stepWidth(widget.width || 6, -1),
                      })} />
                    <IconButton icon="chevron" label="Wider" size="xs" className="ghost"
                      disabled={(widget.width || 6) >= 12}
                      onClick={() => resizeWidget(widget, {
                        width: stepWidth(widget.width || 6, +1),
                      })} />
                    <IconButton icon="minus" label="Shorter" size="xs" className="ghost"
                      disabled={(widget.height || 2) <= 1}
                      onClick={() => resizeWidget(widget, { height: (widget.height || 2) - 1 })} />
                    <IconButton icon="plus" label="Taller" size="xs" className="ghost"
                      disabled={(widget.height || 2) >= 6}
                      onClick={() => resizeWidget(widget, { height: (widget.height || 2) + 1 })} />
                    <IconButton icon="copy" label="Duplicate" size="xs" className="ghost"
                      onClick={async () => {
                        await api.createWidget(board.id, {
                          title: `${widget.title} (copy)`, type: widget.type,
                          query: widget.query, viz: widget.viz,
                          width: widget.width, height: widget.height,
                        });
                        loadBoard(board.id);
                      }} />
                    <IconButton icon="edit" label="Edit" size="xs" className="ghost"
                      onClick={() => setEditing(widget)} />
                  </div>
                </div>
                <div className={`tile-body ${scrolls ? 'scroll' : ''} ${flush ? 'flush' : ''}`}>
                  <WidgetView
                    widget={widget}
                    result={result && !result.error ? result : null}
                    error={result?.error}
                    loading={running && !result}
                    animate={prefs.animate}
                  />
                </div>
              </div>
            );
          })}

          <button className="tile" style={{
            gridColumn: 'span 4', minHeight: 120, cursor: 'pointer',
            alignItems: 'center', justifyContent: 'center', color: 'var(--text-3)',
            borderStyle: 'dashed',
          }} onClick={() => setEditing({})}>
            <Icon name="plus" size={18} /> Add a widget
          </button>
        </div>
      )}

      {editing && (
        <WidgetEditor
          schema={schema}
          widget={editing.id ? editing : null}
          board={filters}
          onSave={saveWidget}
          onCancel={() => setEditing(null)}
          onDelete={async (w) => {
            await api.deleteWidget(board.id, w.id);
            setEditing(null);
            await loadBoard(board.id);
            boardsQ.refetch();
          }}
        />
      )}

      {picker && (
        <Modal size="sm" title="New dashboard" onClose={() => setPicker(false)}>
          <div className="col">
            {templates.map((t) => (
              <button className="card" key={t.key} onClick={() => createBoard(t.key)}
                style={{ textAlign: 'left', cursor: 'pointer', padding: 14, border: 0 }}>
                <div style={{ fontWeight: 620 }}>{t.name}</div>
                <div className="small muted">
                  {t.description}{t.widget_count > 0 && ` · ${t.widget_count} widgets`}
                </div>
              </button>
            ))}
          </div>
        </Modal>
      )}

      {renaming !== null && (
        <Modal
          size="sm" title="Rename dashboard" onClose={() => setRenaming(null)}
          footer={(
            <>
              <span className="spacer" />
              <Button onClick={() => setRenaming(null)}>Cancel</Button>
              <Button variant="primary" onClick={async () => {
                await api.updateBoard(board.id, { name: renaming });
                setBoard((b) => ({ ...b, name: renaming }));
                setRenaming(null);
                boardsQ.refetch();
              }}>Save</Button>
            </>
          )}
        >
          <input value={renaming} onChange={(e) => setRenaming(e.target.value)} autoFocus />
        </Modal>
      )}

      {filtersOpen && (
        <Modal
          size="md" title="Dashboard filters" onClose={() => setFiltersOpen(false)}
          footer={(
            <>
              <span className="tiny dim" style={{ flex: 1 }}>
                Applied on top of every widget&apos;s own filters.
              </span>
              <Button variant="primary" onClick={() => setFiltersOpen(false)}>Done</Button>
            </>
          )}
        >
          <div className="col">
            {(filters.filters || []).map((f, i) => (
              <FilterRow
                key={i}
                filter={f}
                fields={schema.fields.filter((field) => field.filterable)}
                fieldMap={Object.fromEntries(schema.fields.map((field) => [field.key, field]))}
                opLabels={schema.op_labels}
                options={schema.options}
                onChange={(next) => setBoardFilters({
                  ...filters, filters: filters.filters.map((one, j) => (j === i ? next : one)),
                })}
                onRemove={() => setBoardFilters({
                  ...filters, filters: filters.filters.filter((_, j) => j !== i),
                })}
              />
            ))}
            <Button icon="plus" onClick={() => setBoardFilters({
              ...filters,
              filters: [...(filters.filters || []), { field: 'account_id', op: 'in', value: [] }],
            })}>
              Add filter
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
