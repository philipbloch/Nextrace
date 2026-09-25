"""FastAPI dashboard for local AI traces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nextrace.context import default_db_path
from nextrace.storage import SQLiteStore

STATIC_DIR = Path(__file__).parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"
ASSETS_DIR = STATIC_DIR / "assets"


def create_app(db_path: str | Path | None = None) -> Any:
    """Create the dashboard ASGI app."""
    try:
        from fastapi import FastAPI, HTTPException, Query
        from fastapi.responses import FileResponse, HTMLResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:
        raise RuntimeError(
            "Dashboard dependencies are missing. Install them with: "
            'python -m pip install -e ".[dashboard]"'
        ) from exc

    store = SQLiteStore(db_path or default_db_path())
    app = FastAPI(title="Nextrace", version="0.1.0")

    if ASSETS_DIR.exists():
        app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="dashboard-assets")

    @app.get("/")
    def index() -> Any:
        if INDEX_FILE.exists():
            return FileResponse(INDEX_FILE)
        return HTMLResponse(
            """
            <!doctype html>
            <html lang="en">
              <head><title>Nextrace</title></head>
              <body>
                <h1>Nextrace dashboard UI has not been built.</h1>
                <p>Run <code>cd dashboard-ui && npm install && npm run build</code>.</p>
              </body>
            </html>
            """,
            status_code=503,
        )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "db_path": str(store.path)}

    @app.get("/api/summary")
    def summary(
        application: str | None = None,
        since: float | None = Query(default=None, ge=0),
        until: float | None = Query(default=None, ge=0),
    ) -> dict[str, Any]:
        return store.summary(application=application, since=since, until=until)

    @app.get("/api/connections")
    def connections() -> list[dict[str, Any]]:
        return store.list_connections()

    @app.get("/api/traces")
    def traces(
        limit: int = Query(default=100, ge=1, le=500),
        application: str | None = None,
        status: str | None = Query(default=None, pattern="^(ok|error)$"),
        since: float | None = Query(default=None, ge=0),
        until: float | None = Query(default=None, ge=0),
    ) -> list[dict[str, Any]]:
        return store.list_traces(
            limit=limit,
            application=application,
            status=status,
            since=since,
            until=until,
        )

    @app.get("/api/traces/{trace_id}")
    def trace_detail(trace_id: str) -> dict[str, Any]:
        trace = store.get_trace(trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="Trace not found")
        return trace

    return app
