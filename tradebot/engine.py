from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List

from .analytics import compute_metrics
from .congress import CongressTracker
from .config import Settings
from .mcp_bridge import analyze as analyze_with_mcp
from .db import Database
from .models import Candidate
from .providers import BaseBroker, ProviderError


@dataclass
class TradingEngine:
    settings: Settings
    broker: BaseBroker
    db: Database

    def learning_weights(self) -> Dict[str, float]:
        raw = self.db.learning_weights()
        return {name: float(payload["weight"]) for name, payload in raw.items()}

    def _candidate_from_bars(self, symbol: str, bars: List[dict], buying_power: float) -> Candidate | None:
        if len(bars) < 30:
            return None
        metrics = compute_metrics(bars)
        price = metrics["latest"]
        if not (self.settings.min_stock_price <= price <= self.settings.max_stock_price):
            return None

        analysis = analyze_with_mcp(metrics, self.settings.analyzer_mode)
        momentum_score, momentum_reasons = analysis["momentum"]
        reversion_score, reversion_reasons = analysis["reversion"]
        risk_score, risk_reasons = analysis["risk"]

        weights = self.learning_weights()
        total_weight = weights["momentum"] + weights["reversion"] + weights["risk"]
        final_score = (
            momentum_score * weights["momentum"]
            + reversion_score * weights["reversion"]
            + risk_score * weights["risk"]
        ) / total_weight

        stop_from_atr = price - (metrics["atr"] * 1.6)
        stop_from_pct = price * (1 - self.settings.stop_loss_pct)
        stop_price = round(max(stop_from_atr, stop_from_pct, self.settings.min_stock_price * 0.5), 2)
        target_from_swing = max(metrics["swing_high20"] * 1.02, price + metrics["atr"] * 2.4)
        target_price = round(target_from_swing, 2)
        reward = max(0.01, target_price - price)
        risk = max(0.01, price - stop_price)
        reward_risk = reward / risk

        reasons = []
        reasons.extend(momentum_reasons[:2])
        reasons.extend(reversion_reasons[:2])
        reasons.extend(risk_reasons[:2])

        action = "watch"
        if (
            final_score >= 60
            and reward_risk >= self.settings.min_reward_risk
            and metrics["avg_dollar_volume"] >= self.settings.min_dollar_volume
            and risk_score >= 45
        ):
            action = "buy"
            reasons.insert(0, "score, liquidity, and reward/risk all cleared the bar")

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
                "momentum": round(momentum_score, 2),
                "reversion": round(reversion_score, 2),
                "risk": round(risk_score, 2),
            },
            metrics={k: round(v, 4) for k, v in metrics.items()},
        )

    def scan_market(self) -> List[Candidate]:
        account = self.broker.account()
        symbols = self.broker.universe()[: self.settings.scan_limit]
        bars = self.broker.bars(symbols, self.settings.lookback_days)
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
        tracker = CongressTracker(self.settings, self.broker.latest_prices)
        trades = [trade.model_dump() for trade in tracker.refresh()]
        self.db.replace_congress_trades(trades)
        return trades

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
        return {"sold": sold, "bought": bought, "candidates": [c.model_dump() for c in candidates]}

    def trade_once_with_congress_refresh(self) -> Dict[str, List[dict]]:
        self.refresh_congress_trades()
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
            "mode": self.settings.broker_mode,
            "provider": self.broker.name,
        }
