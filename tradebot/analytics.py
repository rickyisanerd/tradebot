from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Dict, List, Tuple


def sma(values: List[float], period: int) -> float:
    if len(values) < period:
        return mean(values) if values else 0.0
    return mean(values[-period:])


def rsi(values: List[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    gains = []
    losses = []
    for prev, cur in zip(values[-period - 1 : -1], values[-period:]):
        delta = cur - prev
        if delta >= 0:
            gains.append(delta)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(delta))
    avg_gain = mean(gains) if gains else 0.0
    avg_loss = mean(losses) if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return max(0.01, mean([h - l for h, l in zip(highs, lows)]))
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return max(0.01, mean(trs[-period:]))


def compute_metrics(bars: List[dict]) -> Dict[str, float]:
    closes = [float(b["c"]) for b in bars]
    highs = [float(b["h"]) for b in bars]
    lows = [float(b["l"]) for b in bars]
    volumes = [float(b["v"]) for b in bars]
    returns = []
    for prev, cur in zip(closes[:-1], closes[1:]):
        if prev:
            returns.append((cur - prev) / prev)
    latest = closes[-1]
    momentum_5 = ((latest / closes[-6]) - 1) * 100 if len(closes) >= 6 else 0.0
    momentum_20 = ((latest / closes[-21]) - 1) * 100 if len(closes) >= 21 else momentum_5
    vol20 = (pstdev(returns[-20:]) * math.sqrt(20) * 100) if len(returns) >= 2 else 0.0
    atr_value = atr(highs, lows, closes, 14)
    atr_pct = (atr_value / latest) * 100 if latest else 0.0
    avg_dollar_volume = mean([c * v for c, v in zip(closes[-20:], volumes[-20:])]) if closes else 0.0
    metrics = {
        "latest": latest,
        "sma10": sma(closes, 10),
        "sma20": sma(closes, 20),
        "sma50": sma(closes, 50),
        "rsi14": rsi(closes, 14),
        "momentum5": momentum_5,
        "momentum20": momentum_20,
        "volatility20": vol20,
        "atr": atr_value,
        "atr_pct": atr_pct,
        "avg_dollar_volume": avg_dollar_volume,
        "swing_high20": max(closes[-20:]) if len(closes) >= 20 else max(closes),
        "swing_low20": min(closes[-20:]) if len(closes) >= 20 else min(closes),
    }
    return metrics


def analyze_momentum(metrics: Dict[str, float]) -> Tuple[float, List[str]]:
    score = 40.0
    reasons: List[str] = []
    if metrics["latest"] > metrics["sma20"] > metrics["sma50"]:
        score += 25
        reasons.append("price is above the 20 and 50 day trend")
    if 1.0 <= metrics["momentum20"] <= 20.0:
        score += 20
        reasons.append("20 day momentum is positive without looking too manic")
    if 48 <= metrics["rsi14"] <= 68:
        score += 15
        reasons.append("RSI is in the healthy trend zone")
    if metrics["momentum5"] < -3:
        score -= 10
        reasons.append("recent pullback is sharper than comfy")
    return max(0.0, min(100.0, score)), reasons


def analyze_reversion(metrics: Dict[str, float]) -> Tuple[float, List[str]]:
    score = 35.0
    reasons: List[str] = []
    if metrics["latest"] > metrics["sma50"]:
        score += 20
        reasons.append("longer trend is still up")
    if metrics["latest"] < metrics["sma10"] and metrics["latest"] > metrics["sma20"] * 0.95:
        score += 20
        reasons.append("price pulled back without fully falling through the floor")
    if 38 <= metrics["rsi14"] <= 55:
        score += 15
        reasons.append("RSI suggests a bounce setup rather than exhaustion")
    if metrics["momentum20"] < -8:
        score -= 15
        reasons.append("medium trend is too soggy")
    return max(0.0, min(100.0, score)), reasons


def analyze_risk(metrics: Dict[str, float]) -> Tuple[float, List[str]]:
    score = 50.0
    reasons: List[str] = []
    if metrics["avg_dollar_volume"] >= 2_000_000:
        score += 20
        reasons.append("liquidity is decent")
    elif metrics["avg_dollar_volume"] < 1_000_000:
        score -= 20
        reasons.append("liquidity is a little swampy")
    if metrics["atr_pct"] <= 5:
        score += 15
        reasons.append("ATR percent is tame")
    else:
        score -= min(20, (metrics["atr_pct"] - 5) * 2)
        reasons.append("ATR percent says this one can buck like a caffeinated mule")
    if metrics["volatility20"] <= 35:
        score += 10
    else:
        score -= min(20, (metrics["volatility20"] - 35) * 0.5)
    return max(0.0, min(100.0, score)), reasons
