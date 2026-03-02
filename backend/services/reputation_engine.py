"""
services/reputation_engine.py
GuardianAI — Reputation Engine

Tracks:
  - Safe trades (approved + completed)
  - Rejected risky trades
  - Drawdown %
  - Sharpe ratio (simulated)
  - Overall reputation score 0–100

Optionally publishes to on-chain ReputationRegistry.
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from services.risk_engine import _sharpe_ratio

logger = logging.getLogger("reputation_engine")


@dataclass
class ReputationMetrics:
    agent_id: int
    agent_address: str
    safe_trades: int
    rejected_trades: int
    total_trades: int
    approval_rate: float         # safe / total
    drawdown_pct: float
    sharpe_ratio: float
    reputation_score: float      # 0–100
    grade: str                   # AAA / AA / A / B / C / D
    last_updated: str


def _grade(score: float) -> str:
    if score >= 90: return "AAA"
    if score >= 75: return "AA"
    if score >= 60: return "A"
    if score >= 45: return "B"
    if score >= 30: return "C"
    return "D"


class ReputationEngine:
    def __init__(self, agent_id: int, agent_address: str):
        self.agent_id      = agent_id
        self.agent_address = agent_address

        self._safe_trades:     int   = 0
        self._rejected_trades: int   = 0
        self._returns:         list[float] = []
        self._drawdown_pct:    float = 0.0

    # ------------------------------------------------------------------ #

    def record_approved(self, pnl_pct: Optional[float] = None):
        """Record a completed safe (approved) trade."""
        self._safe_trades += 1
        if pnl_pct is not None:
            self._returns.append(pnl_pct)

    def record_rejected(self):
        """Record a trade that was blocked by risk rules."""
        self._rejected_trades += 1

    def update_drawdown(self, drawdown_pct: float):
        """Update the current max drawdown from portfolio state."""
        self._drawdown_pct = drawdown_pct

    def get_metrics(self) -> ReputationMetrics:
        total      = self._safe_trades + self._rejected_trades
        approval   = (self._safe_trades / total) if total > 0 else 0.0
        sharpe     = _sharpe_ratio(self._returns)
        score      = self._compute_score(approval, self._drawdown_pct, sharpe, total)

        return ReputationMetrics(
            agent_id=self.agent_id,
            agent_address=self.agent_address,
            safe_trades=self._safe_trades,
            rejected_trades=self._rejected_trades,
            total_trades=total,
            approval_rate=round(approval, 4),
            drawdown_pct=round(self._drawdown_pct, 6),
            sharpe_ratio=round(sharpe, 4),
            reputation_score=round(score, 2),
            grade=_grade(score),
            last_updated=datetime.utcnow().isoformat(),
        )

    def to_dict(self) -> dict:
        m = self.get_metrics()
        return {
            "agent_id":         m.agent_id,
            "agent_address":    m.agent_address,
            "safe_trades":      m.safe_trades,
            "rejected_trades":  m.rejected_trades,
            "total_trades":     m.total_trades,
            "approval_rate":    m.approval_rate,
            "drawdown_pct":     m.drawdown_pct,
            "sharpe_ratio":     m.sharpe_ratio,
            "reputation_score": m.reputation_score,
            "grade":            m.grade,
            "last_updated":     m.last_updated,
        }

    # ------------------------------------------------------------------ #

    def _compute_score(
        self,
        approval_rate: float,
        drawdown_pct: float,
        sharpe: float,
        total_trades: int,
    ) -> float:
        """
        Reputation score components (out of 100):
          - Approval rate:   40 pts  (high = good, agent enforces risk rules)
          - Drawdown:        30 pts  (lower = better)
          - Sharpe ratio:    20 pts  (higher = better)
          - Activity bonus:  10 pts  (more trades = more proven)
        """
        # 40 pts: approval rate (paradox: high rejection = higher score)
        # Agents that ENFORCE rules are safer → reward high rejection of risky trades
        # But also need to actually approve reasonable trades
        # Sweet spot: 70-90% approval rate
        if approval_rate >= 0.70:
            approval_score = 40.0
        elif approval_rate >= 0.50:
            approval_score = 30.0
        elif approval_rate >= 0.30:
            approval_score = 15.0
        else:
            approval_score = 5.0

        # 30 pts: drawdown (0% = 30pts, 15%+ = 0pts)
        drawdown_score = max(0.0, 30.0 * (1 - drawdown_pct / 0.15))

        # 20 pts: Sharpe ratio (capped at 3.0)
        sharpe_clamped = max(0.0, min(sharpe, 3.0))
        sharpe_score   = (sharpe_clamped / 3.0) * 20.0

        # 10 pts: activity (logarithmic)
        if total_trades == 0:
            activity_score = 0.0
        else:
            activity_score = min(10.0, math.log10(total_trades + 1) * 5.0)

        return approval_score + drawdown_score + sharpe_score + activity_score
