from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from .analytics import compute_metrics
from .congress import CongressTracker
from .config import Settings
from .mcp_bridge import analyze as analyze_with_mcp
from .db import Database
from .earnings import EarningsTracker
from .macro import MacroTracker
from .models import Candidate
from .providers import BaseBroker, ProviderError
from .sec import SecTracker


@dataclass
class TradingEngine:
    settings: Settings
    broker: BaseBroker
    db: Database

    def learning_weights(self) -> Dict[str, float]:
        raw = self.db.learning_weights()
        return {name: float(payload["weight"]) for name, payload in raw.items()}

    def _parse_timestamp(self, value: object) -> Optional[datetime]:
        if not value:
            return None
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _pdt_pause_until(self) -> Optional[datetime]:
        cooldown = max(1, int(self.settings.pdt_cooldown_hours))
        for trade in self.db.recent_trades(50):
            if trade.get("side") != "buy" or trade.get("status") != "error":
                continue
            note = str(trade.get("note") or "").lower()
            if "pattern day trading protection" not in note:
                continue
            created_at = self._parse_timestamp(trade.get("created_at"))
            if created_at is None:
                continue
            pause_until = created_at + timedelta(hours=cooldown)
            if pause_until > datetime.now(timezone.utc):
                return pause_until
        return None

    def _buying_pause_reason(self) -> str:
        pause_until = self._pdt_pause_until()
        if pause_until is None:
            return ""
        return f"buying paused until {pause_until.isoformat()} after Alpaca PDT protection rejected a prior order"

    def _stale_after_hours(self) -> Dict[str, int]:
        return {
            "congress": self.settings.congress_freshness_hours,
            "sec": self.settings.sec_freshness_hours,
            "earnings": self.settings.earnings_freshness_hours,
            "macro": self.settings.macro_freshness_hours,
        }

    def _minimum_records(self) -> Dict[str, int]:
        return {
            "congress": self.settings.congress_min_records,
            "sec": self.settings.sec_min_records,
            "earnings": self.settings.earnings_min_records,
            "macro": self.settings.macro_min_records,
        }

    def _retry_minutes(self) -> Dict[str, int]:
        return {
            "congress": self.settings.congress_retry_minutes,
            "sec": self.settings.sec_retry_minutes,
            "earnings": self.settings.earnings_retry_minutes,
            "macro": self.settings.macro_retry_minutes,
        }

    def _override_modes(self) -> Dict[str, str]:
        return {
            "congress": self.settings.congress_override_mode,
            "sec": self.settings.sec_override_mode,
            "earnings": self.settings.earnings_override_mode,
            "macro": self.settings.macro_override_mode,
        }

    def _signal_enabled(self, source: str) -> bool:
        if self._override_modes()[source] == "disabled":
            return False
        if source == "congress":
            return bool(self.settings.congress_report_urls)
        if source == "sec":
            return bool(self.settings.sec_user_agent)
        if source == "earnings":
            return bool(self.settings.alpha_vantage_api_key)
        if source == "macro":
            return True
        return False

    def _signal_health(self) -> Dict[str, dict]:
        now = datetime.now(timezone.utc)
        statuses = self.db.signal_statuses()
        health: Dict[str, dict] = {}
        for source in ("congress", "sec", "earnings", "macro"):
            item = statuses.get(
                source,
                {
                    "source": source,
                    "status": "disabled" if not self._signal_enabled(source) else "unknown",
                    "last_attempt_at": None,
                    "last_success_at": None,
                    "error_message": "",
                    "records_count": 0,
                },
            )
            stale = False
            last_success_at = item.get("last_success_at")
            if item["status"] == "ok" and last_success_at:
                last_success = datetime.fromisoformat(str(last_success_at))
                if last_success.tzinfo is None:
                    last_success = last_success.replace(tzinfo=timezone.utc)
                age_hours = (now - last_success).total_seconds() / 3600.0
                stale = age_hours > self._stale_after_hours()[source]
            minimum_records = self._minimum_records()[source]
            records_count = int(item.get("records_count", 0) or 0)
            low_confidence = item["status"] == "ok" and records_count > 0 and records_count < minimum_records
            no_data = item["status"] == "ok" and records_count == 0
            in_backoff = False
            next_retry_at = item.get("next_retry_at")
            if next_retry_at:
                next_retry = datetime.fromisoformat(str(next_retry_at))
                if next_retry.tzinfo is None:
                    next_retry = next_retry.replace(tzinfo=timezone.utc)
                in_backoff = next_retry > now
            override_mode = self._override_modes()[source]
            if override_mode == "trusted":
                stale = False
                low_confidence = False
            if override_mode == "ignore-backoff":
                in_backoff = False
            health[source] = dict(item) | {
                "enabled": self._signal_enabled(source),
                "stale": stale,
                "minimum_records": minimum_records,
                "low_confidence": low_confidence,
                "no_data": no_data,
                "in_backoff": in_backoff,
                "override_mode": override_mode,
            }
        return health

    def degraded_mode(self) -> bool:
        return any(
            item["enabled"] and (item["status"] in {"error", "backoff"} or item["stale"] or item["low_confidence"])
            for item in self._signal_health().values()
        )

    def _refresh_source(
        self,
        source: str,
        callback: Callable[[], List[dict]],
    ) -> List[dict]:
        attempted_at = datetime.now(timezone.utc).isoformat()
        if not self._signal_enabled(source):
            self.db.update_signal_status(
                source,
                "disabled",
                last_attempt_at=attempted_at,
                error_message="",
                records_count=0,
                failure_count=0,
                next_retry_at=None,
            )
            self.db.record_signal_refresh_event(source, "disabled", records_count=0, failure_count=0)
            return []
        current = self.db.signal_statuses().get(source)
        if current and current.get("next_retry_at") and self._override_modes()[source] != "ignore-backoff":
            next_retry = datetime.fromisoformat(str(current["next_retry_at"]))
            if next_retry.tzinfo is None:
                next_retry = next_retry.replace(tzinfo=timezone.utc)
            if next_retry > datetime.now(timezone.utc):
                self.db.update_signal_status(
                    source,
                    "backoff",
                    last_attempt_at=current.get("last_attempt_at"),
                    last_success_at=current.get("last_success_at"),
                    error_message=str(current.get("error_message") or ""),
                    records_count=int(current.get("records_count") or 0),
                    failure_count=int(current.get("failure_count") or 0),
                    next_retry_at=str(current["next_retry_at"]),
                )
                self.db.record_signal_refresh_event(
                    source,
                    "backoff",
                    records_count=int(current.get("records_count") or 0),
                    failure_count=int(current.get("failure_count") or 0),
                    error_message=str(current.get("error_message") or ""),
                    next_retry_at=str(current["next_retry_at"]),
                )
                return []
        try:
            records = callback()
        except Exception as exc:  # noqa: BLE001
            failure_count = int(current.get("failure_count") or 0) + 1 if current else 1
            delay_minutes = self._retry_minutes()[source] * min(8, 2 ** (failure_count - 1))
            next_retry_at = (datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)).isoformat()
            self.db.update_signal_status(
                source,
                "error",
                last_attempt_at=attempted_at,
                error_message=str(exc),
                records_count=0,
                failure_count=failure_count,
                next_retry_at=next_retry_at,
            )
            self.db.record_signal_refresh_event(
                source,
                "error",
                records_count=0,
                failure_count=failure_count,
                error_message=str(exc),
                next_retry_at=next_retry_at,
            )
            return []
        self.db.update_signal_status(
            source,
            "ok",
            last_attempt_at=attempted_at,
            last_success_at=attempted_at,
            error_message="",
            records_count=len(records),
            failure_count=0,
            next_retry_at=None,
        )
        self.db.record_signal_refresh_event(source, "ok", records_count=len(records), failure_count=0)
        return records

    def _external_decision_inputs(self, symbol: str) -> Dict[str, float]:
        return (
            self.db.congress_signal_for_symbol(symbol, self.settings.congress_signal_window_days)
            | self.db.sec_signal_for_symbol(symbol, self.settings.sec_signal_window_days)
            | self.db.earnings_signal_for_symbol(symbol, self.settings.earnings_signal_window_days)
            | self.db.macro_signal(self.settings.macro_signal_window_days)
        )

    def _external_signal_controls(self, symbol: str) -> tuple[Dict[str, float], Dict[str, str]]:
        raw = self._external_decision_inputs(symbol)
        health = self._signal_health()
        signal_usage: Dict[str, str] = {}
        effective: Dict[str, float] = dict(raw)

        source_fields = {
            "congress": [
                "congress_buy_count",
                "congress_sell_count",
                "congress_net_count",
                "days_since_congress_trade",
                "days_since_congress_filed",
            ],
            "sec": [
                "sec_form4_count",
                "sec_disclosure_count",
                "sec_offering_filing_count",
                "days_since_sec_filing",
            ],
            "earnings": [
                "days_until_earnings",
                "earnings_before_open_count",
                "earnings_after_close_count",
                "has_upcoming_earnings",
            ],
            "macro": [
                "days_until_macro_event",
                "has_near_macro_event",
                "near_cpi_count",
                "near_fomc_count",
            ],
        }
        weight_keys = {
            "congress": "congress_weight",
            "sec": "sec_weight",
            "earnings": "earnings_weight",
            "macro": "macro_weight",
        }
        configured_weights = {
            "congress": self.settings.decision_support_congress_weight,
            "sec": self.settings.decision_support_sec_weight,
            "earnings": self.settings.decision_support_earnings_weight,
            "macro": self.settings.decision_support_macro_weight,
        }

        for source, fields in source_fields.items():
            item = health[source]
            if not item["enabled"]:
                signal_usage[source] = "disabled"
                effective[weight_keys[source]] = 0.0
                for field in fields:
                    effective[field] = 0.0
                continue
            if item["override_mode"] == "trusted":
                signal_usage[source] = "trusted"
                effective[weight_keys[source]] = configured_weights[source]
                continue
            if item["status"] == "error":
                signal_usage[source] = "error"
                effective[weight_keys[source]] = 0.0
                for field in fields:
                    effective[field] = 0.0
                continue
            if item["status"] == "backoff" or item["in_backoff"]:
                signal_usage[source] = "backoff"
                effective[weight_keys[source]] = 0.0
                for field in fields:
                    effective[field] = 0.0
                continue
            if item["stale"]:
                signal_usage[source] = "stale"
                effective[weight_keys[source]] = 0.0
                for field in fields:
                    effective[field] = 0.0
                continue
            if item["low_confidence"]:
                signal_usage[source] = "low-confidence"
                effective[weight_keys[source]] = 0.0
                for field in fields:
                    effective[field] = 0.0
                continue
            if item["no_data"]:
                signal_usage[source] = "no-data"
                effective[weight_keys[source]] = 0.0
                for field in fields:
                    effective[field] = 0.0
                continue
            if configured_weights[source] <= 0:
                signal_usage[source] = "weight=0"
                effective[weight_keys[source]] = 0.0
                for field in fields:
                    effective[field] = 0.0
                continue
            signal_usage[source] = "active"
            effective[weight_keys[source]] = configured_weights[source]
        return effective, signal_usage

    def _avg_dollar_volume_from_bars(self, bars: List[dict], window: int = 20) -> float:
        recent = bars[-max(1, window):]
        if not recent:
            return 0.0
        return sum(float(bar["c"]) * float(bar["v"]) for bar in recent) / len(recent)

    def _candidate_symbol_pool(self) -> List[str]:
        raw_symbols = self.broker.universe()
        if self.settings.scan_universe or not self.settings.is_alpaca:
            return raw_symbols[: self.settings.scan_limit]

        target_pool = max(self.settings.scan_limit * 4, self.settings.candidate_limit * 6)
        batch_size = max(25, min(60, self.settings.scan_limit * 2))
        history_days = min(max(30, self.settings.lookback_days // 3), self.settings.lookback_days)
        baseline_liquidity = max(100_000.0, self.settings.min_dollar_volume * 0.5)
        ranked: List[tuple[float, str]] = []
        seen: set[str] = set()
        max_batches = 4

        for start in range(0, len(raw_symbols), batch_size):
            if start >= batch_size * max_batches:
                break
            batch = raw_symbols[start : start + batch_size]
            if not batch:
                break
            try:
                bars = self.broker.bars(batch, history_days)
            except ProviderError:
                continue
            for symbol in batch:
                if symbol in seen:
                    continue
                item = bars.get(symbol) or []
                if len(item) < 20:
                    continue
                price = float(item[-1]["c"])
                if not (self.settings.min_stock_price <= price <= self.settings.max_stock_price):
                    continue
                avg_dollar_volume = self._avg_dollar_volume_from_bars(item)
                if avg_dollar_volume < baseline_liquidity:
                    continue
                ranked.append((avg_dollar_volume, symbol))
                seen.add(symbol)
            if len(ranked) >= target_pool:
                break

        if ranked:
            ranked.sort(key=lambda item: item[0], reverse=True)
            return [symbol for _, symbol in ranked[:target_pool]]
        return raw_symbols[: self.settings.scan_limit]

    def _candidate_from_bars(self, symbol: str, bars: List[dict], buying_power: float) -> Candidate | None:
        if len(bars) < 30:
            return None
        metrics = compute_metrics(bars)
        price = metrics["latest"]
        if not (self.settings.min_stock_price <= price <= self.settings.max_stock_price):
            return None

        stop_from_atr = price - (metrics["atr"] * 1.6)
        stop_from_pct = price * (1 - self.settings.stop_loss_pct)
        stop_price = round(max(stop_from_atr, stop_from_pct, self.settings.min_stock_price * 0.5), 2)
        target_from_swing = max(metrics["swing_high20"] * 1.02, price + metrics["atr"] * 2.4)
        target_price = round(target_from_swing, 2)
        reward = max(0.01, target_price - price)
        risk = max(0.01, price - stop_price)
        reward_risk = reward / risk

        external_inputs, signal_usage = self._external_signal_controls(symbol)
        analysis_input = dict(metrics)
        analysis_input.update(
            {
                "reward_risk": reward_risk,
                "stop_price": stop_price,
                "target_price": target_price,
                "risk_amount": risk,
                "reward_amount": reward,
                "min_reward_risk": self.settings.min_reward_risk,
            }
        )
        analysis_input.update(external_inputs)
        analysis = analyze_with_mcp(analysis_input, self.settings.analyzer_mode)
        decision_support_score, decision_support_reasons = analysis["decision_support"]
        momentum_score, momentum_reasons = analysis["momentum"]
        reversion_score, reversion_reasons = analysis["reversion"]
        risk_score, risk_reasons = analysis["risk"]

        weights = self.learning_weights()
        weighted_scores = {
            "decision_support": decision_support_score,
            "momentum": momentum_score,
            "reversion": reversion_score,
            "risk": risk_score,
        }
        total_weight = sum(weights[name] for name in weighted_scores)
        final_score = sum(weighted_scores[name] * weights[name] for name in weighted_scores) / total_weight

        reasons = []
        reasons.extend(decision_support_reasons[:2])
        reasons.extend(momentum_reasons[:2])
        reasons.extend(reversion_reasons[:2])
        reasons.extend(risk_reasons[:2])

        action = "watch"
        if (
            final_score >= 60
            and reward_risk >= self.settings.min_reward_risk
            and metrics["avg_dollar_volume"] >= self.settings.min_dollar_volume
            and risk_score >= 45
            and decision_support_score >= 50
        ):
            action = "buy"
            reasons.insert(0, "decision support, liquidity, and reward/risk all cleared the bar")

        risk_budget = max(50.0, buying_power * self.settings.risk_per_trade_pct)
        position_cap = max(100.0, buying_power * self.settings.max_position_pct)
        qty_from_risk = int(risk_budget / risk)
        qty_from_value = int(position_cap / price)
        qty = max(0, min(qty_from_risk, qty_from_value))

        if qty <= 0:
            action = "watch"

        return Candidate(
            symbol=symbol,
            price=round(price, 2),
            final_score=round(final_score, 2),
            action=action,
            reasons=reasons[:5],
            stop_price=stop_price,
            target_price=target_price,
            reward_risk=round(reward_risk, 2),
            qty=qty,
            analyst_scores={
                "decision_support": round(decision_support_score, 2),
                "momentum": round(momentum_score, 2),
                "reversion": round(reversion_score, 2),
                "risk": round(risk_score, 2),
            },
            metrics={k: round(v, 4) for k, v in (metrics | external_inputs).items()},
            signal_usage=signal_usage,
        )

    def scan_market(self) -> List[Candidate]:
        try:
            account = self.broker.account()
            symbols = self._candidate_symbol_pool()
            bars = self.broker.bars(symbols, self.settings.lookback_days)
        except ProviderError:
            self.db.record_scan(self.settings.broker_mode, self.broker.name, [])
            return []
        candidates: List[Candidate] = []
        for symbol in symbols:
            item = bars.get(symbol)
            if not item:
                continue
            candidate = self._candidate_from_bars(symbol, item, account.buying_power)
            if candidate:
                candidates.append(candidate)
        candidates.sort(key=lambda x: (x.action == "buy", x.final_score, x.reward_risk), reverse=True)
        trimmed = candidates[: self.settings.candidate_limit]
        self.db.record_scan(self.settings.broker_mode, self.broker.name, [c.model_dump() for c in trimmed])
        return trimmed

    def refresh_congress_trades(self) -> List[dict]:
        def run() -> List[dict]:
            tracker = CongressTracker(self.settings, self.broker.latest_prices)
            trades = [trade.model_dump() for trade in tracker.refresh()]
            self.db.replace_congress_trades(trades)
            return trades

        return self._refresh_source("congress", run)

    def refresh_sec_filings(self) -> List[dict]:
        def run() -> List[dict]:
            tracker = SecTracker(self.settings)
            symbols = self._candidate_symbol_pool()[: self.settings.scan_limit]
            filings = [filing.__dict__ for filing in tracker.refresh(symbols)]
            grouped: Dict[str, List[dict]] = {}
            for filing in filings:
                grouped.setdefault(filing["symbol"], []).append(filing)
            for symbol in symbols:
                self.db.replace_sec_filings_for_symbol(symbol, grouped.get(symbol, []))
            return filings

        return self._refresh_source("sec", run)

    def refresh_earnings_events(self) -> List[dict]:
        def run() -> List[dict]:
            tracker = EarningsTracker(self.settings)
            symbols = self._candidate_symbol_pool()[: self.settings.scan_limit]
            events = [event.__dict__ for event in tracker.refresh(symbols)]
            grouped: Dict[str, List[dict]] = {}
            for event in events:
                grouped.setdefault(event["symbol"], []).append(event)
            for symbol in symbols:
                self.db.replace_earnings_events_for_symbol(symbol, grouped.get(symbol, []))
            return events

        return self._refresh_source("earnings", run)

    def refresh_macro_events(self) -> List[dict]:
        def run() -> List[dict]:
            tracker = MacroTracker(self.settings)
            events = [event.__dict__ for event in tracker.refresh()]
            self.db.replace_macro_events(events)
            return events

        return self._refresh_source("macro", run)

    def refresh_all_signals(self) -> Dict[str, List[dict]]:
        return {
            "congress": self.refresh_congress_trades(),
            "sec": self.refresh_sec_filings(),
            "earnings": self.refresh_earnings_events(),
            "macro": self.refresh_macro_events(),
        }

    def _held_days(self, opened_at: str) -> int:
        opened = datetime.fromisoformat(opened_at)
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - opened).days)

    def _loss_stop_price(self, entry_price: float, stored_stop_price: float) -> float:
        percent_stop = round(entry_price * (1 - self.settings.stop_loss_pct), 2)
        return max(float(stored_stop_price), percent_stop)

    def reconcile_broker_state(self) -> List[dict]:
        tracked = {item["symbol"]: item for item in self.db.all_position_meta()}
        live_positions = {p.symbol: p for p in self.broker.positions()}
        notes: List[dict] = []

        for symbol, position in live_positions.items():
            meta = tracked.get(symbol)
            if not meta:
                stop_price = round(max(position.avg_entry_price * (1 - self.settings.stop_loss_pct), self.settings.min_stock_price * 0.5), 2)
                target_price = round(position.avg_entry_price + (position.avg_entry_price - stop_price) * self.settings.min_reward_risk, 2)
                self.db.open_position_meta(symbol, position.qty, position.avg_entry_price, stop_price, target_price, {})
                self.db.record_trade(symbol, "buy", position.qty, position.avg_entry_price, "reconciled", "reconciled external position")
                notes.append({"symbol": symbol, "note": "reconciled external position"})
                continue
            if abs(float(meta["qty"]) - float(position.qty)) > 1e-9 or abs(float(meta["entry_price"]) - float(position.avg_entry_price)) > 1e-9:
                self.db.open_position_meta(
                    symbol,
                    position.qty,
                    position.avg_entry_price,
                    float(meta["stop_price"]),
                    float(meta["target_price"]),
                    meta["analysis"],
                )
                notes.append({"symbol": symbol, "note": "synced live position metadata"})

        missing_symbols = [symbol for symbol in tracked if symbol not in live_positions]
        if not missing_symbols:
            return notes

        recent_sells = self.broker.recent_filled_sell_orders(missing_symbols)
        for symbol in missing_symbols:
            order = recent_sells.get(symbol)
            if not order:
                continue
            closed = self.db.close_position_meta(symbol)
            if not closed:
                continue
            exit_price = float(order.get("filled_avg_price") or order.get("limit_price") or closed["target_price"])
            qty = float(order.get("filled_qty") or order.get("qty") or closed["qty"])
            entry = float(closed["entry_price"])
            pnl_pct = ((exit_price - entry) / entry) * 100 if entry else 0.0
            note = order.get("order_class") or order.get("client_order_id") or "broker managed exit"
            analysis = closed["analysis"]
            self.db.record_trade(symbol, "sell", qty, exit_price, order.get("status", "filled"), note, pnl_pct, analysis)
            if analysis:
                self.db.update_learning(analysis, pnl_pct)
            notes.append({"symbol": symbol, "pnl_pct": round(pnl_pct, 2), "note": note})
        return notes

    def manage_positions(self) -> List[dict]:
        broker_notes: List[dict] = []
        if self.settings.is_alpaca:
            broker_notes = self.reconcile_broker_state()
        prices = self.broker.latest_prices([p.symbol for p in self.broker.positions()])
        sold: List[dict] = list(broker_notes)
        for position in self.broker.positions():
            meta = self.db.get_position_meta(position.symbol)
            if not meta:
                continue
            if bool(meta.get("exit_pending")):
                continue
            current = prices.get(position.symbol, position.current_price)
            should_sell = False
            note = ""
            held_days = self._held_days(str(meta["opened_at"]))
            effective_stop_price = self._loss_stop_price(float(meta["entry_price"]), float(meta["stop_price"]))
            if current <= effective_stop_price:
                should_sell = True
                note = "stop hit"
            elif self.settings.max_hold_days > 0 and held_days >= self.settings.max_hold_days:
                should_sell = True
                note = "time stop"
            elif position.unrealized_pl_pct <= -(self.settings.stop_loss_pct * 100):
                should_sell = True
                note = "loss cap"
            elif position.unrealized_pl_pct <= -6:
                should_sell = True
                note = "drawdown cap"
            elif held_days >= self.settings.min_hold_days and current >= float(meta["target_price"]):
                should_sell = True
                note = "target hit"
            if should_sell:
                try:
                    if self.settings.is_alpaca and self.settings.use_broker_protective_orders:
                        self.broker.cancel_open_orders_for_symbol(position.symbol)
                    result = self.broker.sell(position.symbol, position.qty)
                except ProviderError as exc:
                    self.db.record_trade(position.symbol, "sell", position.qty, current, "error", str(exc))
                    continue
                raw_qty = result.get("qty")
                raw_price = result.get("filled_avg_price")
                status = result.get("status", "submitted")
                recorded_qty = float(raw_qty) if raw_qty not in (None, "") else float(position.qty)
                recorded_price = float(raw_price) if raw_price not in (None, "") else float(current)
                if status in {"filled"}:
                    closed = self.db.close_position_meta(position.symbol)
                    entry = float(closed["entry_price"]) if closed else position.avg_entry_price
                    pnl_pct = ((recorded_price - entry) / entry) * 100 if entry else 0.0
                    analysis = closed["analysis"] if closed else {}
                    self.db.record_trade(
                        position.symbol,
                        "sell",
                        recorded_qty,
                        recorded_price,
                        status,
                        note,
                        pnl_pct,
                        analysis,
                    )
                    if analysis:
                        self.db.update_learning(analysis, pnl_pct)
                    sold.append({"symbol": position.symbol, "pnl_pct": round(pnl_pct, 2), "note": note})
                else:
                    self.db.set_exit_pending(position.symbol, True)
                    self.db.record_trade(
                        position.symbol,
                        "sell",
                        recorded_qty,
                        recorded_price,
                        status,
                        note,
                    )
                    sold.append({"symbol": position.symbol, "note": f"{note} submitted"})
        return sold

    def buy_candidates(self, candidates: List[Candidate]) -> List[dict]:
        pause_reason = self._buying_pause_reason()
        if pause_reason:
            return []
        positions = self.broker.positions()
        existing = {p.symbol for p in positions}
        account = self.broker.account()
        bought: List[dict] = []
        open_position_limit = self.settings.max_open_positions or (len(positions) + self.settings.max_new_positions_per_run)
        slots = min(self.settings.max_new_positions_per_run, max(0, open_position_limit - len(positions)))
        cash_left = account.buying_power
        deployed_capital = sum(p.market_value for p in positions)
        capital_limit = self.settings.max_total_capital if self.settings.max_total_capital > 0 else max(account.equity, deployed_capital + cash_left)
        capital_left = max(0.0, capital_limit - deployed_capital)
        for candidate in candidates:
            if slots <= 0:
                break
            if candidate.action != "buy" or candidate.symbol in existing:
                continue
            max_affordable_qty = int(min(cash_left, capital_left) / candidate.price) if candidate.price > 0 else 0
            qty = min(candidate.qty, max_affordable_qty)
            est_cost = qty * candidate.price
            if qty <= 0 or est_cost > cash_left or est_cost > capital_left:
                continue
            try:
                result = self.broker.buy(
                    candidate.symbol,
                    qty,
                    stop_price=candidate.stop_price,
                    target_price=candidate.target_price,
                )
            except ProviderError as exc:
                self.db.record_trade(candidate.symbol, "buy", qty or candidate.qty, candidate.price, "error", str(exc), analysis=candidate.analyst_scores)
                continue
            fill_price = float(result.get("filled_avg_price") or candidate.price)
            applied_stop_price = self._loss_stop_price(fill_price, candidate.stop_price)
            filled_qty = float(result.get("qty", qty))
            self.db.record_trade(candidate.symbol, "buy", filled_qty, fill_price, result.get("status", "submitted"), "entry", analysis=candidate.analyst_scores)
            self.db.open_position_meta(candidate.symbol, filled_qty, fill_price, applied_stop_price, candidate.target_price, candidate.analyst_scores)
            bought.append({"symbol": candidate.symbol, "qty": filled_qty, "price": fill_price})
            cash_left -= est_cost
            capital_left -= est_cost
            slots -= 1
        return bought

    def trade_once(self) -> Dict[str, List[dict]]:
        self.broker.advance_market()
        sold = self.manage_positions()
        candidates = self.scan_market()
        bought = self.buy_candidates(candidates)
        payload = {"sold": sold, "bought": bought, "candidates": [c.model_dump() for c in candidates]}
        pause_reason = self._buying_pause_reason()
        if pause_reason:
            payload["buying_paused_reason"] = pause_reason
        return payload

    def trade_once_with_congress_refresh(self) -> Dict[str, List[dict]]:
        self.refresh_all_signals()
        return self.trade_once()

    def trade_once_with_signal_refresh(self) -> Dict[str, List[dict]]:
        self.refresh_all_signals()
        return self.trade_once()

    def dashboard_snapshot(self) -> dict:
        account = self.broker.account()
        return {
            "account": account.model_dump(),
            "candidates": self.db.latest_candidates(),
            "congress_trades": self.db.recent_congress_trades(self.settings.congress_trade_limit),
            "positions": [p.model_dump() for p in self.broker.positions()],
            "trades": self.db.recent_trades(25),
            "learning": self.db.learning_weights(),
            "signal_health": self._signal_health(),
            "signal_refresh_history": self.db.recent_signal_refresh_history(12),
            "degraded_mode": self.degraded_mode(),
            "buying_paused_reason": self._buying_pause_reason(),
            "mode": self.settings.broker_mode,
            "provider": self.broker.name,
        }
