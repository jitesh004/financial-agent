import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../core/api';
import { useQuery } from '../core/store';
import { useRouteParam } from '../core/router';
import { useToast } from '../core/toast';
import { read, write } from '../core/storage';
import {
  Button, Callout, Empty, Loading, PromptButton, GlassCard, Badge,
} from '../ui';
import { Icon } from '../ui/icons';
import WidgetEditor from './explore/WidgetEditor';
import WidgetView from './explore/WidgetView';

const LAST_BOARD = 'prism-explore-board';
const WIDTH_STEPS = [3, 4, 6, 8, 12];

const stepWidth = (current, dir) => {
  const i = WIDTH_STEPS.indexOf(current);
  const from = i === -1 ? WIDTH_STEPS.indexOf(6) : i;
  return WIDTH_STEPS[Math.min(WIDTH_STEPS.length - 1, Math.max(0, from + dir))];
};

export default function Explore() {
  const toast = useToast();
  const [boardId, setBoardId] = useRouteParam('board', '');

  const schemaQ = useQuery('query-schema', () => api.querySchema());
  const boardsQ = useQuery('boards', () => api.boards());
  const templatesQ = useQuery('board-templates', () => api.boardTemplates());

  const [board, setBoard] = useState(null);
  const [results, setResults] = useState({});
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  const [editing, setEditing] = useState(null);

  const boards = boardsQ.data || [];
  const schema = schemaQ.data;

  useEffect(() => {
    if (!boards.length || boardId) return;
    const remembered = read(LAST_BOARD, null);
    const pick = boards.find((b) => b.id === remembered) || boards.find((b) => b.is_default) || boards[0];
    if (pick) setBoardId(pick.id);
  }, [boards, boardId, setBoardId]);

  const loadBoard = useCallback(async (id) => {
    if (!id) {
      setBoard(null);
      setResults({});
      return;
    }
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
    if (boardId) write(LAST_BOARD, boardId);
    loadBoard(boardId);
  }, [boardId, loadBoard]);

  const rerun = useCallback(async (filters) => {
    if (!boardId) return;
    setRunning(true);
    try {
      const { results: ran } = await api.runBoard(boardId, filters);
      setResults(ran);
    } catch (e) {
      toast.fail('Query execution failed', e.message);
    } finally {
      setRunning(false);
    }
  }, [boardId, toast]);

  const reload = useCallback(async () => {
    if (!boardId) return null;
    const fresh = await api.board(boardId);
    setBoard(fresh);
    return fresh;
  }, [boardId]);

  const handleCreateBoard = async () => {
    try {
      const created = await api.createBoard({ name: 'New dashboard', widgets: [] });
      boardsQ.refetch();
      setBoardId(created.id);
      toast.ok('Created new dashboard');
    } catch (e) {
      toast.fail('Creation failed', e.message);
    }
  };

  const handleRenameBoard = async (name) => {
    if (!board || !name.trim()) return;
    try {
      await api.updateBoard(board.id, { ...board, name: name.trim() });
      await reload();
      boardsQ.refetch();
      toast.ok('Dashboard renamed');
    } catch (e) {
      toast.fail('Rename failed', e.message);
    }
  };

  const handleDeleteBoard = async () => {
    if (!board) return;
    try {
      await api.deleteBoard(board.id);
      boardsQ.refetch();
      setBoardId('');
      toast.ok('Dashboard deleted');
    } catch (e) {
      toast.fail('Delete failed', e.message);
    }
  };

  const handleSaveWidget = async (widgetData) => {
    if (!board) return;
    try {
      if (widgetData.id) await api.updateWidget(board.id, widgetData.id, widgetData);
      else await api.createWidget(board.id, widgetData);
      const fresh = await reload();
      setEditing(null);
      await rerun(fresh?.filters || board.filters);
      toast.ok(widgetData.id ? 'Widget updated' : 'Widget added');
    } catch (e) {
      toast.fail('Could not save widget', e.message);
    }
  };

  const handleDeleteWidget = async (widget) => {
    if (!board || !widget?.id) { setEditing(null); return; }
    try {
      await api.deleteWidget(board.id, widget.id);
      await reload();
      setEditing(null);
      toast.ok('Widget removed');
    } catch (e) {
      toast.fail('Could not remove widget', e.message);
    }
  };

  const updateWidgetSize = async (widget, widthDelta, heightDelta) => {
    if (!board) return;
    const next = {
      ...widget,
      width: widthDelta !== 0 ? stepWidth(widget.width || 6, widthDelta) : (widget.width || 6),
      height: heightDelta !== 0
        ? Math.max(1, Math.min(4, (widget.height || 2) + heightDelta))
        : (widget.height || 2),
    };
    /* Paint the new size straight away; the request only confirms it. */
    setBoard((prev) => (prev ? {
      ...prev,
      widgets: (prev.widgets || []).map((w) => (w.id === widget.id ? next : w)),
    } : prev));
    try {
      await api.updateWidget(board.id, widget.id, next);
    } catch (e) {
      await reload();
      toast.fail('Could not resize widget', e.message);
    }
  };

  if (schemaQ.loading || boardsQ.loading) {
    return <Loading message="Loading custom analytics engine…" />;
  }

  return (
    <div className="explore-screen page-enter" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>
      {/* Top Header & Dashboard Bar */}
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <h1 className="h1" style={{ margin: 0 }}>Custom Analytics & Exploration</h1>
            <Badge tone="brand" size="sm">Query Studio</Badge>
          </div>
          <p className="lead" style={{ marginTop: 'var(--space-2)' }}>
            Construct modular multi-dimensional dashboards with custom SQL-backed aggregations and real-time ledger filters.
          </p>
        </div>

        {/* Dashboard Actions */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 'var(--space-2)',
          flexWrap: 'wrap', maxWidth: '100%',
        }}>
          {boards.length > 0 && (
            <select
              className="select"
              aria-label="Dashboard"
              value={boardId}
              onChange={(e) => setBoardId(e.target.value)}
              style={{ minWidth: 200 }}
            >
              {boards.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name || 'Untitled dashboard'}{b.is_default ? ' (default)' : ''}
                </option>
              ))}
            </select>
          )}

          {board && (
            <PromptButton
              size="sm"
              initial={board.name || ''}
              placeholder="Dashboard name"
              submitLabel="Rename"
              onSubmit={handleRenameBoard}
            >
              Rename
            </PromptButton>
          )}

          <Button size="sm" onClick={handleCreateBoard}>
            + New Dashboard
          </Button>

          {board && (
            <Button
              size="sm"
              variant="primary"
              onClick={() => setEditing({})}
            >
              + Add Widget
            </Button>
          )}

          {board && !board.is_default && (
            <Button size="sm" variant="danger" onClick={handleDeleteBoard}>
              Delete
            </Button>
          )}
        </div>
      </div>

      {error && <Callout tone="neg">{error}</Callout>}

      {!boards.length && (
        <Empty title="No custom dashboards configured" icon="briefcase">
          Click &ldquo;New Dashboard&rdquo; above to start building tailor-made visual telemetry for your ledger.
        </Empty>
      )}

      {/* Widgets 12-Column Responsive Grid */}
      {board && (
        <div className="explore-grid">
          {!(board.widgets || []).length ? (
            <div style={{ gridColumn: 'span 12' }}>
              <Empty
                title="This dashboard has no widgets yet"
                icon="plus"
                action={(
                  <Button variant="primary" onClick={() => setEditing({})}>
                    Add First Widget
                  </Button>
                )}
              >
                Assemble cards, charts, ranked lists, or data tables powered by database queries.
              </Empty>
            </div>
          ) : (
            (board.widgets || []).map((w) => {
              const res = results[w.id];
              const colSpan = Math.min(12, Math.max(3, w.width || 6));
              /* A one-row stat tile is shorter than its own label + figure, so
                 those grow to fit; charts and tables need a definite height. */
              const flexible = w.type === 'stat' || w.type === 'text';
              const rowHeight = Math.max(flexible ? 132 : 0, (w.height || 2) * 120);

              return (
                <GlassCard
                  key={w.id}
                  style={{
                    gridColumn: `span ${colSpan}`,
                    ...(flexible ? { minHeight: rowHeight } : { height: rowHeight }),
                    display: 'flex',
                    flexDirection: 'column',
                    padding: 0,
                    overflow: 'hidden',
                  }}
                >
                  {/* Widget Card Header */}
                  <div
                    style={{
                      padding: '10px 14px',
                      borderBottom: '1px solid var(--border-subtle)',
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      background: 'var(--surface-2)',
                    }}
                  >
                    <span className="font-semibold small truncate" style={{ maxWidth: '60%' }}>
                      {w.title}
                    </span>

                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      <button
                        className="btn btn-ghost btn-sm"
                        style={{ padding: '2px 6px', fontSize: 11 }}
                        title="Shrink width"
                        onClick={() => updateWidgetSize(w, -1, 0)}
                      >
                        ◄
                      </button>
                      <button
                        className="btn btn-ghost btn-sm"
                        style={{ padding: '2px 6px', fontSize: 11 }}
                        title="Expand width"
                        onClick={() => updateWidgetSize(w, 1, 0)}
                      >
                        ►
                      </button>
                      <button
                        className="btn btn-ghost btn-sm"
                        style={{ padding: '2px 6px', fontSize: 11 }}
                        title="Configure widget"
                        onClick={() => setEditing(w)}
                      >
                        <Icon name="sparkles" size={14} />
                      </button>
                    </div>
                  </div>

                  {/* Widget Viewport */}
                  <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
                    <WidgetView widget={w} result={res} running={running} />
                  </div>
                </GlassCard>
              );
            })
          )}
        </div>
      )}

      {/* Widget Editor Modal */}
      {editing && schema && (
        <WidgetEditor
          schema={schema}
          widget={editing}
          board={board}
          onSave={handleSaveWidget}
          onCancel={() => setEditing(null)}
          onDelete={handleDeleteWidget}
        />
      )}
    </div>
  );
}
