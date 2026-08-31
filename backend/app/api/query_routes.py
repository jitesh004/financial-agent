"""The Explore tab's API: ad-hoc queries and the dashboards built from them.

Two responsibilities, deliberately kept apart:

  * `/api/query` runs one query description against the ledger. It is
    stateless - nothing is stored, and the same body always produces the same
    numbers from the same data.
  * `/api/dashboards` stores *queries*, never results. A saved board is a set
    of questions; every figure on it is recomputed on open, so a board can
    never drift out of step with a correction made in the Review tab.

A board carries its own date range and filters. Those are merged into each
widget's query at request time by `_apply_board` rather than being written
into the widget, so changing the board's range does not rewrite twelve saved
widgets - and a widget can opt out with `pin_date`.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ..analytics import query as q
from ..db.database import get_db
from ..db import repository as repo
from . import dashboard_templates as templates

router = APIRouter(prefix="/api", tags=["explore"])


# --------------------------------------------------------------------------
# Ad-hoc queries
# --------------------------------------------------------------------------

@router.get("/query/schema")
def query_schema() -> dict[str, Any]:
    """Every field, measure, operator and option the builder can offer.

    The frontend renders itself from this. A dimension added to
    analytics.query.FIELDS shows up in the UI with no frontend change.
    """
    return q.schema(get_db())


def _apply_board(spec: dict[str, Any], board: dict[str, Any] | None) -> dict[str, Any]:
    """Overlay a board's date range and filters onto one widget's query.

    The board's range wins unless the widget pinned its own - a "last year vs
    this year" tile has to keep its range when the board is switched to this
    month, or it stops meaning anything. Board filters always AND with the
    widget's: they narrow, never widen.
    """
    if not board:
        return spec
    merged = dict(spec)
    board_range = board.get("date_range")
    if board_range and board_range.get("preset") not in (None, "inherit"):
        if not spec.get("pin_date"):
            merged["date_range"] = board_range
    board_filters = board.get("filters") or []
    if board_filters:
        merged["filters"] = [*(spec.get("filters") or []), *board_filters]
    return merged


@router.post("/query")
def run_query(payload: dict[str, Any]) -> dict[str, Any]:
    spec = payload.get("query") or payload
    try:
        return q.run_query(get_db(), _apply_board(spec, payload.get("board")))
    except q.QueryError as exc:
        # 400, not 500: the query named something that does not exist, which
        # is a fixable mistake in the widget rather than a server fault.
        raise HTTPException(400, str(exc)) from exc


@router.post("/query/export")
def export_query(payload: dict[str, Any]) -> Response:
    """The same query as CSV, with the row limit lifted to the engine's cap."""
    spec = dict(payload.get("query") or payload)
    spec["limit"] = q.MAX_ROWS
    try:
        result = q.run_query(get_db(), _apply_board(spec, payload.get("board")))
    except q.QueryError as exc:
        raise HTTPException(400, str(exc)) from exc

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([c["label"] for c in result["columns"]])
    for row in result["rows"]:
        writer.writerow([row.get(c["key"]) for c in result["columns"]])

    name = (payload.get("filename") or "export").replace('"', "")
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


# --------------------------------------------------------------------------
# Dashboards
#
# Literal paths are declared BEFORE /dashboards/{dashboard_id}. FastAPI
# matches in declaration order, so with the parameterised route first a
# request to /dashboards/templates resolves as a dashboard whose id is the
# literal string "templates" and answers 404 - the same trap that made
# /api/transactions/bulk unreachable.
# --------------------------------------------------------------------------

@router.get("/dashboards/templates")
def list_templates() -> list[dict[str, Any]]:
    return templates.catalogue()


@router.post("/dashboards/import")
def import_dashboard(payload: dict[str, Any]) -> dict[str, Any]:
    """Recreate a board from an exported JSON document."""
    board = payload.get("dashboard") or payload
    name = board.get("name") or "Imported dashboard"
    widgets = board.get("widgets") or []
    if not isinstance(widgets, list):
        raise HTTPException(400, "'widgets' must be a list.")
    dashboard_id = repo.create_dashboard(
        get_db(), name, board.get("description", ""),
        filters=board.get("filters") or {}, widgets=widgets)
    return {"status": "ok", "id": dashboard_id}


@router.get("/dashboards")
def list_dashboards() -> list[dict[str, Any]]:
    return repo.list_dashboards(get_db())


@router.post("/dashboards")
def create_dashboard(payload: dict[str, Any]) -> dict[str, Any]:
    name = (payload.get("name") or "").strip() or "Untitled dashboard"
    template_key = payload.get("template")
    widgets = payload.get("widgets")
    board_filters = payload.get("filters") or {}

    if template_key:
        template = templates.get(template_key)
        if template is None:
            raise HTTPException(404, f"No template named '{template_key}'.")
        widgets = template["widgets"]
        board_filters = template.get("filters", {})
        if not payload.get("name"):
            name = template["name"]

    dashboard_id = repo.create_dashboard(
        get_db(), name, payload.get("description", ""),
        filters=board_filters, widgets=widgets or [])
    return {"status": "ok", "id": dashboard_id}


@router.get("/dashboards/{dashboard_id}")
def read_dashboard(dashboard_id: str) -> dict[str, Any]:
    board = repo.get_dashboard(get_db(), dashboard_id)
    if board is None:
        raise HTTPException(404, "No such dashboard.")
    return board


@router.put("/dashboards/{dashboard_id}")
def update_dashboard(dashboard_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not repo.update_dashboard(get_db(), dashboard_id, **payload):
        raise HTTPException(404, "No such dashboard.")
    return {"status": "ok"}


@router.delete("/dashboards/{dashboard_id}")
def delete_dashboard(dashboard_id: str) -> dict[str, Any]:
    if not repo.delete_dashboard(get_db(), dashboard_id):
        raise HTTPException(404, "No such dashboard.")
    return {"status": "ok"}


@router.post("/dashboards/{dashboard_id}/duplicate")
def duplicate_dashboard(dashboard_id: str, payload: dict[str, Any] | None = None
                        ) -> dict[str, Any]:
    db = get_db()
    board = repo.get_dashboard(db, dashboard_id)
    if board is None:
        raise HTTPException(404, "No such dashboard.")
    name = ((payload or {}).get("name") or f"{board['name']} (copy)")
    new_id = repo.create_dashboard(db, name, board["description"],
                                   filters=board["filters"],
                                   widgets=board["widgets"])
    return {"status": "ok", "id": new_id}


@router.get("/dashboards/{dashboard_id}/export")
def export_dashboard(dashboard_id: str) -> Response:
    board = repo.get_dashboard(get_db(), dashboard_id)
    if board is None:
        raise HTTPException(404, "No such dashboard.")
    # Ids are stripped: an exported board is a recipe, and carrying the
    # original ids into an import would invite two boards sharing them.
    document = {
        "name": board["name"],
        "description": board["description"],
        "filters": board["filters"],
        "widgets": [
            {k: w[k] for k in ("title", "type", "query", "viz", "position",
                               "width", "height")}
            for w in board["widgets"]
        ],
    }
    filename = (board["name"] or "dashboard").replace('"', "")
    return Response(
        content=json.dumps(document, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}.json"'},
    )


@router.post("/dashboards/{dashboard_id}/run")
def run_dashboard(dashboard_id: str, payload: dict[str, Any] | None = None
                  ) -> dict[str, Any]:
    """Every widget's query in one round trip.

    A twelve-tile board would otherwise open with twelve requests. Each widget
    is caught individually: one malformed query must show its own error in its
    own tile, not blank the whole board.
    """
    db = get_db()
    board = repo.get_dashboard(db, dashboard_id)
    if board is None:
        raise HTTPException(404, "No such dashboard.")

    board_filters = (payload or {}).get("board", board["filters"])
    results: dict[str, Any] = {}
    for widget in board["widgets"]:
        if widget["type"] == "text":
            continue
        try:
            results[widget["id"]] = q.run_query(
                db, _apply_board(widget["query"], board_filters))
        except q.QueryError as exc:
            results[widget["id"]] = {"error": str(exc)}
        except Exception as exc:  # a bad saved query must not 500 the board
            results[widget["id"]] = {"error": f"{type(exc).__name__}: {exc}"}
    return {"results": results}


@router.post("/dashboards/{dashboard_id}/widgets")
def create_widget(dashboard_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    widget_id = repo.create_widget(get_db(), dashboard_id, payload)
    if widget_id is None:
        raise HTTPException(404, "No such dashboard.")
    return {"status": "ok", "id": widget_id}


@router.put("/dashboards/{dashboard_id}/layout")
def save_layout(dashboard_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    updated = repo.save_layout(get_db(), dashboard_id, payload.get("layout") or [])
    return {"status": "ok", "updated": updated}


@router.put("/dashboards/{dashboard_id}/widgets/{widget_id}")
def update_widget(dashboard_id: str, widget_id: str,
                  payload: dict[str, Any]) -> dict[str, Any]:
    if not repo.update_widget(get_db(), widget_id, payload):
        raise HTTPException(404, "No such widget.")
    return {"status": "ok"}


@router.delete("/dashboards/{dashboard_id}/widgets/{widget_id}")
def delete_widget(dashboard_id: str, widget_id: str) -> dict[str, Any]:
    if not repo.delete_widget(get_db(), widget_id):
        raise HTTPException(404, "No such widget.")
    return {"status": "ok"}
