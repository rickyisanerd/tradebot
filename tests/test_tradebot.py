from pathlib import Path

from fastapi.testclient import TestClient

from tradebot.config import Settings
from tradebot.dashboard import create_app
from tradebot.db import Database
from tradebot.engine import TradingEngine
from tradebot.providers import build_broker


def make_settings(tmp_path: Path) -> Settings:
    settings = Settings(data_dir=tmp_path)
    settings.broker_mode = "demo"
    settings.__post_init__()
    return settings


def test_scan_and_trade_once(tmp_path: Path):
    settings = make_settings(tmp_path)
    engine = TradingEngine(settings=settings, broker=build_broker(settings), db=Database(settings.db_path))
    candidates = engine.scan_market()
    assert candidates
    result = engine.trade_once()
    assert "candidates" in result
    snapshot = engine.dashboard_snapshot()
    assert "account" in snapshot
    assert isinstance(snapshot["positions"], list)


def test_dashboard_renders(tmp_path: Path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "TradeBot MCP Dashboard" in response.text
