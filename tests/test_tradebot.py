import os
from pathlib import Path

from fastapi.testclient import TestClient

from tradebot.config import Settings
from tradebot.dashboard import create_app
from tradebot.db import Database
from tradebot.engine import TradingEngine
from tradebot.models import AccountSnapshot, Candidate
from tradebot.providers import AlpacaBroker, BaseBroker, build_broker


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


def test_manage_positions_sells_when_stop_hit(tmp_path: Path):
    settings = make_settings(tmp_path)
    engine = TradingEngine(settings=settings, broker=build_broker(settings), db=Database(settings.db_path))

    first = engine.trade_once()
    assert first["bought"]

    symbol = first["bought"][0]["symbol"]
    meta = engine.db.get_position_meta(symbol)
    assert meta is not None

    # Raise the stored stop above the market so the next management pass must exit.
    engine.db.open_position_meta(
        symbol,
        float(meta["qty"]),
        float(meta["entry_price"]),
        float(meta["entry_price"]) * 10,
        float(meta["target_price"]),
        meta["analysis"],
    )

    sold = engine.manage_positions()
    assert sold
    assert sold[0]["symbol"] == symbol
    assert sold[0]["note"] == "stop hit"
    assert engine.broker.positions() == []


def test_settings_reads_stop_loss_from_env(tmp_path: Path):
    previous_stop_loss = os.environ.get("STOP_LOSS")
    previous_stop_loss_pct = os.environ.get("STOP_LOSS_PCT")
    os.environ["STOP_LOSS"] = "5"
    os.environ.pop("STOP_LOSS_PCT", None)
    try:
        settings = Settings(data_dir=tmp_path)
    finally:
        if previous_stop_loss is None:
            os.environ.pop("STOP_LOSS", None)
        else:
            os.environ["STOP_LOSS"] = previous_stop_loss
        if previous_stop_loss_pct is None:
            os.environ.pop("STOP_LOSS_PCT", None)
        else:
            os.environ["STOP_LOSS_PCT"] = previous_stop_loss_pct
    assert settings.stop_loss_pct == 0.05


class CaptureAlpacaBroker(AlpacaBroker):
    def __init__(self, settings: Settings) -> None:
        self.calls = []
        super().__init__(settings)

    def _request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        return {"status": "accepted"}


def test_alpaca_buy_uses_bracket_order_payload(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    settings.broker_mode = "paper"
    settings.alpaca_key_id = "key"
    settings.alpaca_secret_key = "secret"
    settings.use_broker_protective_orders = True
    settings.__post_init__()

    broker = CaptureAlpacaBroker(settings)
    broker.buy("AAPL", 3, stop_price=9.5, target_price=11.25)

    method, url, kwargs = broker.calls[-1]
    payload = kwargs["json"]
    assert method == "POST"
    assert url.endswith("/v2/orders")
    assert payload["symbol"] == "AAPL"
    assert payload["qty"] == 3
    assert payload["order_class"] == "bracket"
    assert payload["stop_loss"] == {"stop_price": 9.5}
    assert payload["take_profit"] == {"limit_price": 11.25}


class CaptureBroker(BaseBroker):
    name = "capture"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.last_buy = None

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(cash=1_000, equity=1_000, buying_power=1_000, mode=self.settings.broker_mode)

    def positions(self):
        return []

    def bars(self, symbols, days):
        return {}

    def latest_prices(self, symbols):
        return {}

    def buy(self, symbol: str, qty: int, stop_price=None, target_price=None) -> dict:
        self.last_buy = {
            "symbol": symbol,
            "qty": qty,
            "stop_price": stop_price,
            "target_price": target_price,
        }
        return {"symbol": symbol, "qty": qty, "filled_avg_price": 10.0, "status": "filled"}

    def sell(self, symbol: str, qty=None) -> dict:
        return {"symbol": symbol, "qty": qty or 0, "filled_avg_price": 10.0, "status": "filled"}


def test_buy_candidates_passes_stop_and_target_to_broker(tmp_path: Path):
    settings = make_settings(tmp_path)
    broker = CaptureBroker(settings)
    engine = TradingEngine(settings=settings, broker=broker, db=Database(settings.db_path))
    candidate = Candidate(
        symbol="AAPL",
        price=10.0,
        final_score=90.0,
        action="buy",
        stop_price=9.5,
        target_price=11.5,
        reward_risk=2.0,
        qty=2,
    )

    result = engine.buy_candidates([candidate])

    assert result
    assert broker.last_buy == {
        "symbol": "AAPL",
        "qty": 2,
        "stop_price": 9.5,
        "target_price": 11.5,
    }


class BrokerManagedExitBroker(BaseBroker):
    name = "broker-managed"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._positions = []
        self._recent = {
            "AAPL": {
                "symbol": "AAPL",
                "side": "sell",
                "status": "filled",
                "filled_avg_price": 11.0,
                "filled_qty": 2,
                "order_class": "bracket",
            }
        }

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(cash=1_000, equity=1_000, buying_power=1_000, mode=self.settings.broker_mode)

    def positions(self):
        return self._positions

    def bars(self, symbols, days):
        return {}

    def latest_prices(self, symbols):
        return {}

    def buy(self, symbol: str, qty: int, stop_price=None, target_price=None) -> dict:
        return {"symbol": symbol, "qty": qty, "filled_avg_price": 10.0, "status": "filled"}

    def sell(self, symbol: str, qty=None) -> dict:
        return {"symbol": symbol, "qty": qty or 0, "filled_avg_price": 10.0, "status": "filled"}

    def recent_filled_sell_orders(self, symbols):
        return {symbol: self._recent[symbol] for symbol in symbols if symbol in self._recent}


def test_reconcile_broker_managed_exits_updates_learning(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    settings.broker_mode = "paper"
    settings.use_broker_protective_orders = True
    settings.alpaca_key_id = "key"
    settings.alpaca_secret_key = "secret"
    settings.__post_init__()

    broker = BrokerManagedExitBroker(settings)
    db = Database(settings.db_path)
    engine = TradingEngine(settings=settings, broker=broker, db=db)
    db.open_position_meta("AAPL", 2, 10.0, 9.5, 11.0, {"momentum": 100.0, "reversion": 50.0, "risk": 75.0})

    before = engine.learning_weights()
    sold = engine.manage_positions()
    after = engine.learning_weights()

    assert sold == [{"symbol": "AAPL", "pnl_pct": 10.0, "note": "bracket"}]
    assert after["momentum"] > before["momentum"]
    assert db.get_position_meta("AAPL") is None


def test_demo_latest_price_matches_bar_close(tmp_path: Path):
    settings = make_settings(tmp_path)
    broker = build_broker(settings)
    symbol = broker.universe()[0]

    latest = broker.latest_prices([symbol])[symbol]
    bars = broker.bars([symbol], settings.lookback_days)[symbol]

    assert latest == bars[-1]["c"]


def test_learning_update_caps_single_outlier_loss(tmp_path: Path):
    db = Database(tmp_path / "tradebot.db")

    db.update_learning({"momentum": 100.0}, -50.0)
    weights = db.learning_weights()

    assert weights["momentum"]["weight"] > 0.5
