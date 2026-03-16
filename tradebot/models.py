from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class Candidate(BaseModel):
    symbol: str
    price: float
    final_score: float
    action: str
    reasons: List[str] = Field(default_factory=list)
    stop_price: float
    target_price: float
    reward_risk: float
    qty: int = 0
    analyst_scores: Dict[str, float] = Field(default_factory=dict)
    metrics: Dict[str, float] = Field(default_factory=dict)


class AccountSnapshot(BaseModel):
    cash: float
    equity: float
    buying_power: float
    mode: str


class PositionSnapshot(BaseModel):
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pl_pct: float


class TradeEvent(BaseModel):
    symbol: str
    side: str
    qty: float
    price: float
    status: str
    note: str = ""
    pnl_pct: Optional[float] = None
    analysis: Dict[str, float] = Field(default_factory=dict)
