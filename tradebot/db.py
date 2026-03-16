from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init_db(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    broker_mode TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    candidates_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trade_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty REAL NOT NULL,
                    price REAL NOT NULL,
                    status TEXT NOT NULL,
                    note TEXT DEFAULT '',
                    pnl_pct REAL,
                    analysis_json TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS position_meta (
                    symbol TEXT PRIMARY KEY,
                    opened_at TEXT NOT NULL,
                    qty REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    stop_price REAL NOT NULL,
                    target_price REAL NOT NULL,
                    analysis_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS learning (
                    strategy TEXT PRIMARY KEY,
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,
                    total_return REAL NOT NULL DEFAULT 0,
                    weight REAL NOT NULL DEFAULT 1.0,
                    updated_at TEXT NOT NULL
                );
                """
            )
            for strategy in ("momentum", "reversion", "risk"):
                con.execute(
                    """
                    INSERT INTO learning(strategy, wins, losses, total_return, weight, updated_at)
                    VALUES (?, 0, 0, 0, 1.0, ?)
                    ON CONFLICT(strategy) DO NOTHING
                    """,
                    (strategy, utc_now()),
                )

    def record_scan(self, broker_mode: str, provider: str, candidates: List[Dict[str, Any]]) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO scans(created_at, broker_mode, provider, candidates_json) VALUES (?, ?, ?, ?)",
                (utc_now(), broker_mode, provider, json.dumps(candidates)),
            )

    def latest_candidates(self) -> List[Dict[str, Any]]:
        with self.connect() as con:
            row = con.execute(
                "SELECT candidates_json FROM scans ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return json.loads(row[0]) if row else []

    def recent_trades(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT created_at, symbol, side, qty, price, status, note, pnl_pct, analysis_json "
                "FROM trade_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) | {"analysis": json.loads(r["analysis_json"] or "{}")} for r in rows]

    def record_trade(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        status: str,
        note: str = "",
        pnl_pct: Optional[float] = None,
        analysis: Optional[Dict[str, float]] = None,
    ) -> None:
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO trade_events(created_at, symbol, side, qty, price, status, note, pnl_pct, analysis_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (utc_now(), symbol, side, qty, price, status, note, pnl_pct, json.dumps(analysis or {})),
            )

    def open_position_meta(
        self,
        symbol: str,
        qty: float,
        entry_price: float,
        stop_price: float,
        target_price: float,
        analysis: Dict[str, float],
    ) -> None:
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO position_meta(symbol, opened_at, qty, entry_price, stop_price, target_price, analysis_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    opened_at=excluded.opened_at,
                    qty=excluded.qty,
                    entry_price=excluded.entry_price,
                    stop_price=excluded.stop_price,
                    target_price=excluded.target_price,
                    analysis_json=excluded.analysis_json
                """,
                (symbol, utc_now(), qty, entry_price, stop_price, target_price, json.dumps(analysis)),
            )

    def get_position_meta(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self.connect() as con:
            row = con.execute("SELECT * FROM position_meta WHERE symbol = ?", (symbol,)).fetchone()
            if not row:
                return None
            payload = dict(row)
            payload["analysis"] = json.loads(payload.pop("analysis_json"))
            return payload

    def all_position_meta(self) -> List[Dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM position_meta ORDER BY opened_at DESC").fetchall()
            items = []
            for row in rows:
                payload = dict(row)
                payload["analysis"] = json.loads(payload.pop("analysis_json"))
                items.append(payload)
            return items

    def close_position_meta(self, symbol: str) -> Optional[Dict[str, Any]]:
        existing = self.get_position_meta(symbol)
        if not existing:
            return None
        with self.connect() as con:
            con.execute("DELETE FROM position_meta WHERE symbol = ?", (symbol,))
        return existing

    def learning_weights(self) -> Dict[str, Dict[str, float]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM learning ORDER BY strategy").fetchall()
            return {row["strategy"]: dict(row) for row in rows}

    def update_learning(self, analysis: Dict[str, float], pnl_pct: float) -> None:
        with self.connect() as con:
            for strategy, score in analysis.items():
                row = con.execute(
                    "SELECT wins, losses, total_return FROM learning WHERE strategy = ?",
                    (strategy,),
                ).fetchone()
                if not row:
                    continue
                wins = row["wins"] + (1 if pnl_pct > 0 else 0)
                losses = row["losses"] + (1 if pnl_pct <= 0 else 0)
                total_return = row["total_return"] + float(pnl_pct) * (float(score) / 100.0)
                weight = 1.0 + (wins - losses) * 0.03 + total_return * 0.05
                weight = max(0.5, min(1.8, weight))
                con.execute(
                    """
                    UPDATE learning
                    SET wins = ?, losses = ?, total_return = ?, weight = ?, updated_at = ?
                    WHERE strategy = ?
                    """,
                    (wins, losses, total_return, weight, utc_now(), strategy),
                )
