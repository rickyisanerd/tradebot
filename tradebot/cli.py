from __future__ import annotations

import argparse
import json

import uvicorn

from .config import get_settings
from .db import Database
from .engine import TradingEngine
from .providers import build_broker


def build_engine() -> TradingEngine:
    settings = get_settings()
    db = Database(settings.db_path)
    broker = build_broker(settings)
    return TradingEngine(settings=settings, broker=broker, db=db)


def main() -> int:
    parser = argparse.ArgumentParser(description="TradeBot MCP")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="Run a single market scan")
    sub.add_parser("trade-once", help="Advance the market one step, manage open positions, and buy new candidates")
    sub.add_parser("refresh-signals", help="Refresh all cached external decision-support signals")
    sub.add_parser("refresh-congress", help="Refresh cached congressional PTR trades from configured official report URLs")
    sub.add_parser("refresh-sec", help="Refresh cached SEC filing signals for the current scan universe")
    sub.add_parser("refresh-earnings", help="Refresh cached earnings events for the current scan universe")
    sub.add_parser("refresh-macro", help="Refresh cached CPI and FOMC calendar events")
    sub.add_parser("status", help="Print dashboard snapshot as JSON")
    sub.add_parser("dashboard", help="Run the FastAPI dashboard")
    args = parser.parse_args()

    engine = build_engine()
    settings = engine.settings

    if args.command == "scan":
        candidates = [c.model_dump() for c in engine.scan_market()]
        print(json.dumps(candidates, indent=2))
        return 0
    if args.command == "trade-once":
        result = engine.trade_once()
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "refresh-signals":
        result = engine.refresh_all_signals()
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "refresh-congress":
        result = engine.refresh_congress_trades()
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "refresh-sec":
        result = engine.refresh_sec_filings()
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "refresh-earnings":
        result = engine.refresh_earnings_events()
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "refresh-macro":
        result = engine.refresh_macro_events()
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "status":
        print(json.dumps(engine.dashboard_snapshot(), indent=2))
        return 0
    if args.command == "dashboard":
        uvicorn.run("tradebot.dashboard:app", host=settings.dashboard_host, port=settings.dashboard_port, reload=False)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
