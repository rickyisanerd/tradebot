from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import Settings, get_settings
from .db import Database
from .engine import TradingEngine
from .providers import build_broker


class TradingScheduler:
    def __init__(self, interval_seconds: int, callback: Callable[[], None]) -> None:
        self.interval_seconds = max(1, interval_seconds)
        self.callback = callback
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="tradebot-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def run_cycle(self) -> None:
        self.callback()

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            self.callback()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = Database(settings.db_path)
    broker = build_broker(settings)
    engine = TradingEngine(settings=settings, broker=broker, db=db)
    engine_lock = threading.Lock()

    def run_trade_cycle() -> None:
        with engine_lock:
            engine.trade_once()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        scheduler = None
        if settings.auto_trade_enabled:
            scheduler = TradingScheduler(settings.auto_trade_interval_minutes * 60, run_trade_cycle)
            scheduler.start()
        try:
            yield
        finally:
            if scheduler:
                scheduler.stop()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.engine = engine
    app.state.engine_lock = engine_lock
    app.state.auto_trade_enabled = settings.auto_trade_enabled
    app.state.auto_trade_interval_minutes = settings.auto_trade_interval_minutes
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        snapshot = engine.dashboard_snapshot()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                **snapshot,
                "auto_trade_enabled": app.state.auto_trade_enabled,
                "auto_trade_interval_minutes": app.state.auto_trade_interval_minutes,
                "congress_max_price": settings.congress_max_price,
            },
        )

    @app.post("/scan")
    async def scan():
        with engine_lock:
            engine.scan_market()
        return RedirectResponse(url="/", status_code=303)

    @app.post("/refresh-congress")
    async def refresh_congress():
        with engine_lock:
            engine.refresh_congress_trades()
        return RedirectResponse(url="/", status_code=303)

    @app.post("/trade-once")
    async def trade_once():
        with engine_lock:
            engine.trade_once()
        return RedirectResponse(url="/", status_code=303)

    @app.post("/refresh-signals")
    async def refresh_signals():
        with engine_lock:
            engine.refresh_all_signals()
        return RedirectResponse(url="/", status_code=303)

    @app.get("/api/status")
    async def status():
        with engine_lock:
            return {
                **engine.dashboard_snapshot(),
                "auto_trade_enabled": app.state.auto_trade_enabled,
                "auto_trade_interval_minutes": app.state.auto_trade_interval_minutes,
                "congress_max_price": settings.congress_max_price,
            }

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app


app = create_app()
