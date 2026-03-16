from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import Settings, get_settings
from .db import Database
from .engine import TradingEngine
from .providers import build_broker


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = Database(settings.db_path)
    broker = build_broker(settings)
    engine = TradingEngine(settings=settings, broker=broker, db=db)

    app = FastAPI(title=settings.app_name)
    app.state.engine = engine
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        snapshot = engine.dashboard_snapshot()
        return templates.TemplateResponse(request, "index.html", {**snapshot})

    @app.post("/scan")
    async def scan():
        engine.scan_market()
        return RedirectResponse(url="/", status_code=303)

    @app.post("/trade-once")
    async def trade_once():
        engine.trade_once()
        return RedirectResponse(url="/", status_code=303)

    @app.get("/api/status")
    async def status():
        return engine.dashboard_snapshot()

    return app


app = create_app()
