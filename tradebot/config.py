from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

from .universe import DEFAULT_UNIVERSE

load_dotenv()


def _env_ratio(*names: str, default: float) -> float:
    for name in names:
        raw = os.getenv(name)
        if raw is None or not raw.strip():
            continue
        value = float(raw)
        return value / 100.0 if value > 1 else value
    return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


@dataclass
class Settings:
    app_name: str = "TradeBot MCP"
    broker_mode: str = field(default_factory=lambda: os.getenv("BROKER_MODE", "demo").lower())
    alpaca_key_id: str = field(default_factory=lambda: os.getenv("ALPACA_KEY_ID", ""))
    alpaca_secret_key: str = field(default_factory=lambda: os.getenv("ALPACA_SECRET_KEY", ""))
    stop_loss_pct: float = field(default_factory=lambda: _env_ratio("STOP_LOSS_PCT", "STOP_LOSS", default=0.12))
    use_broker_protective_orders: bool = field(default_factory=lambda: _env_bool("USE_BROKER_PROTECTIVE_ORDERS", True))
    max_stock_price: float = field(default_factory=lambda: float(os.getenv("MAX_STOCK_PRICE", "20")))
    min_stock_price: float = field(default_factory=lambda: float(os.getenv("MIN_STOCK_PRICE", "2")))
    scan_limit: int = field(default_factory=lambda: int(os.getenv("SCAN_LIMIT", "40")))
    candidate_limit: int = field(default_factory=lambda: int(os.getenv("CANDIDATE_LIMIT", "12")))
    max_new_positions_per_run: int = field(default_factory=lambda: int(os.getenv("MAX_NEW_POSITIONS_PER_RUN", "3")))
    risk_per_trade_pct: float = field(default_factory=lambda: float(os.getenv("RISK_PER_TRADE_PCT", "0.01")))
    max_position_pct: float = field(default_factory=lambda: float(os.getenv("MAX_POSITION_PCT", "0.10")))
    min_reward_risk: float = field(default_factory=lambda: float(os.getenv("MIN_REWARD_RISK", "1.8")))
    min_dollar_volume: float = field(default_factory=lambda: float(os.getenv("MIN_DOLLAR_VOLUME", "1000000")))
    lookback_days: int = field(default_factory=lambda: int(os.getenv("LOOKBACK_DAYS", "80")))
    dashboard_host: str = field(default_factory=lambda: os.getenv("DASHBOARD_HOST", "127.0.0.1"))
    dashboard_port: int = field(default_factory=lambda: int(os.getenv("DASHBOARD_PORT", "8008")))
    analyzer_mode: str = field(default_factory=lambda: os.getenv("ANALYZER_MODE", "embedded").lower())
    starting_cash: float = field(default_factory=lambda: float(os.getenv("STARTING_CASH", "100000")))
    demo_seed: int = field(default_factory=lambda: int(os.getenv("DEMO_SEED", "42")))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", Path.cwd() / "data")))
    db_path: Path = field(init=False)
    demo_state_path: Path = field(init=False)
    scan_universe: List[str] = field(init=False)

    def __post_init__(self) -> None:
        raw_universe = os.getenv("SCAN_UNIVERSE", "")
        if raw_universe.strip():
            self.scan_universe = [x.strip().upper() for x in raw_universe.split(",") if x.strip()]
        else:
            self.scan_universe = DEFAULT_UNIVERSE[: self.scan_limit]
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "tradebot.db"
        self.demo_state_path = self.data_dir / "demo_broker.json"

    @property
    def is_demo(self) -> bool:
        return self.broker_mode == "demo"

    @property
    def is_alpaca(self) -> bool:
        return self.broker_mode in {"paper", "live"}

    @property
    def trading_base_url(self) -> str:
        if self.broker_mode == "paper":
            return "https://paper-api.alpaca.markets"
        return "https://api.alpaca.markets"

    @property
    def data_base_url(self) -> str:
        return "https://data.alpaca.markets"

    def validate_for_broker(self) -> None:
        if self.is_alpaca and (not self.alpaca_key_id or not self.alpaca_secret_key):
            raise ValueError("ALPACA_KEY_ID and ALPACA_SECRET_KEY are required for paper/live mode.")


def get_settings() -> Settings:
    return Settings()
