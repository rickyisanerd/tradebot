from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .analytics import compute_metrics
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

        stop_price = round(max(price - (metrics["atr"] * 1.6), price * 0.88, self.settings.min_stock_price * 0.5), 2)
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

    def manage_positions(self) -> List[dict]:
        prices = self.broker.latest_prices([p.symbol for p in self.broker.positions()])
        sold: List[dict] = []
        for position in self.broker.positions():
            meta = self.db.get_position_meta(position.symbol)
            if not meta:
                continue
            current = prices.get(position.symbol, position.current_price)
            should_sell = False
            note = ""
            if current <= float(meta["stop_price"]):
                should_sell = True
                note = "stop hit"
            elif current >= float(meta["target_price"]):
                should_sell = True
                note = "target hit"
            elif position.unrealized_pl_pct <= -6:
                should_sell = True
                note = "drawdown cap"
            if should_sell:
                try:
                    result = self.broker.sell(position.symbol, position.qty)
                except ProviderError as exc:
                    self.db.record_trade(position.symbol, "sell", position.qty, current, "error", str(exc))
                    continue
                closed = self.db.close_position_meta(position.symbol)
                entry = float(closed["entry_price"]) if closed else position.avg_entry_price
                pnl_pct = ((current - entry) / entry) * 100 if entry else 0.0
                analysis = closed["analysis"] if closed else {}
                self.db.record_trade(position.symbol, "sell", float(result.get("qty", position.qty)), float(result.get("filled_avg_price", current)), result.get("status", "submitted"), note, pnl_pct, analysis)
                if analysis:
                    self.db.update_learning(analysis, pnl_pct)
                sold.append({"symbol": position.symbol, "pnl_pct": round(pnl_pct, 2), "note": note})
        return sold

    def buy_candidates(self, candidates: List[Candidate]) -> List[dict]:
        existing = {p.symbol for p in self.broker.positions()}
        account = self.broker.account()
        bought: List[dict] = []
        slots = self.settings.max_new_positions_per_run
        cash_left = account.buying_power
        for candidate in candidates:
            if slots <= 0:
                break
            if candidate.action != "buy" or candidate.symbol in existing:
                continue
            est_cost = candidate.qty * candidate.price
            if candidate.qty <= 0 or est_cost > cash_left:
                continue
            try:
                result = self.broker.buy(candidate.symbol, candidate.qty)
            except ProviderError as exc:
                self.db.record_trade(candidate.symbol, "buy", candidate.qty, candidate.price, "error", str(exc), analysis=candidate.analyst_scores)
                continue
            fill_price = float(result.get("filled_avg_price") or candidate.price)
            self.db.record_trade(candidate.symbol, "buy", float(result.get("qty", candidate.qty)), fill_price, result.get("status", "submitted"), "entry", analysis=candidate.analyst_scores)
            self.db.open_position_meta(candidate.symbol, float(result.get("qty", candidate.qty)), fill_price, candidate.stop_price, candidate.target_price, candidate.analyst_scores)
            bought.append({"symbol": candidate.symbol, "qty": candidate.qty, "price": fill_price})
            cash_left -= est_cost
            slots -= 1
        return bought

    def trade_once(self) -> Dict[str, List[dict]]:
        self.broker.advance_market()
        sold = self.manage_positions()
        candidates = self.scan_market()
        bought = self.buy_candidates(candidates)
        return {"sold": sold, "bought": bought, "candidates": [c.model_dump() for c in candidates]}

    def dashboard_snapshot(self) -> dict:
        account = self.broker.account()
        return {
            "account": account.model_dump(),
            "candidates": self.db.latest_candidates(),
            "positions": [p.model_dump() for p in self.broker.positions()],
            "trades": self.db.recent_trades(25),
            "learning": self.db.learning_weights(),
            "mode": self.settings.broker_mode,
            "provider": self.broker.name,
        }
