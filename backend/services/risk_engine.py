"""
services/risk_engine.py
GuardianAI — Risk Governance Engine

Implements:
  1. Max Position Size (fixed fractional model)
  2. Daily Loss Limit
  3. Max Drawdown Threshold
  4. Volatility Filter (moving average of returns std)
  5. Leverage Cap
  6. Circuit Breaker
  7. Historical Value-at-Risk (parametric simplified)
  8. Sharpe Ratio approximation
"""

import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional
from enum import Enum

logger = logging.getLogger("risk_engine")


class RiskDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT  = "REJECT"


@dataclass
class RiskConfig:
    # --- Position / Capital Rules ---
    max_position_pct: float    = 0.10   # Max 10% of capital per trade
    daily_loss_limit_pct: float = 0.02  # Max 2% daily loss
    max_drawdown_pct: float    = 0.15   # Max 15% drawdown from peak
    leverage_cap: float        = 3.0    # Max 3x leverage

    # --- Volatility Filter ---
    volatility_window: int     = 20     # Rolling window for vol calculation
    max_volatility_pct: float  = 0.05   # Reject if annualised vol > 5% daily

    # --- VaR ---
    var_confidence: float      = 0.95   # 95% confidence VaR
    var_limit_pct: float       = 0.03   # Reject if VaR > 3% of capital

    # --- Circuit Breaker ---
    circuit_breaker_loss_pct: float = 0.05  # Trip breaker at 5% daily loss


@dataclass
class RiskViolation:
    rule: str
    detail: str
    severity: str  # "HARD" | "WARN"


@dataclass
class RiskAssessment:
    decision: RiskDecision
    risk_score: float          # 0.0 (safest) – 100.0 (most dangerous)
    violations: list[RiskViolation]
    var_pct: float
    volatility_pct: float
    position_size_pct: float
    recommended_size_pct: float
    circuit_breaker_active: bool
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class RiskEngine:
    """
    Stateful risk governance engine.
    Call validate_trade_intent() for every proposed trade.
    """

    def __init__(self, config: Optional[RiskConfig] = None):
        self.config = config or RiskConfig()
        self.circuit_breaker_active: bool = False

        # Portfolio state
        self.capital_peak: float  = 0.0
        self.current_capital: float = 0.0
        self.daily_start_capital: float = 0.0
        self._last_day: date = date.today()

        # Price return history for vol + VaR
        self._returns: list[float] = []

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def initialize(self, starting_capital: float):
        self.current_capital    = starting_capital
        self.capital_peak       = starting_capital
        self.daily_start_capital = starting_capital
        self._last_day = date.today()
        logger.info(f"RiskEngine initialized with capital={starting_capital:.2f}")

    def update_capital(self, new_capital: float, price_return: Optional[float] = None):
        """Call after each trade settlement to keep state current."""
        today = date.today()
        if today != self._last_day:
            self.daily_start_capital = new_capital
            self._last_day = today

        self.current_capital = new_capital
        if new_capital > self.capital_peak:
            self.capital_peak = new_capital

        if price_return is not None:
            self._returns.append(price_return)
            # Keep window bounded
            if len(self._returns) > 500:
                self._returns = self._returns[-500:]

        # Auto-trip circuit breaker on severe daily loss
        daily_loss_pct = self._daily_loss_pct()
        if daily_loss_pct > self.config.circuit_breaker_loss_pct:
            if not self.circuit_breaker_active:
                self.circuit_breaker_active = True
                logger.warning(f"Circuit breaker TRIPPED — daily loss {daily_loss_pct:.2%}")

    def reset_circuit_breaker(self):
        self.circuit_breaker_active = False
        logger.info("Circuit breaker RESET by operator")

    def validate_trade_intent(
        self,
        trade_value_usd: float,
        leverage: float = 1.0,
        asset_returns: Optional[list[float]] = None,
    ) -> RiskAssessment:
        """
        Main validation entry point.
        
        Args:
            trade_value_usd: Notional value of the trade in USD
            leverage: Leverage applied (1.0 = no leverage)
            asset_returns: Optional list of recent returns for this asset
        
        Returns:
            RiskAssessment with decision, score, and all rule outputs
        """
        violations: list[RiskViolation] = []
        cap = self.current_capital if self.current_capital > 0 else 1.0

        # --- Pre-compute metrics ---
        position_size_pct  = trade_value_usd / cap
        recommended_pct    = self._fixed_fractional_size()
        daily_loss_pct     = self._daily_loss_pct()
        drawdown_pct       = self._drawdown_pct()

        returns_to_use = asset_returns or self._returns
        volatility_pct = self._rolling_volatility(returns_to_use)
        var_pct        = self._historical_var(returns_to_use)

        # --- Rule 1: Circuit Breaker ---
        if self.circuit_breaker_active:
            violations.append(RiskViolation(
                rule="CIRCUIT_BREAKER",
                detail="Circuit breaker is active. All trading halted.",
                severity="HARD"
            ))

        # --- Rule 2: Max Position Size ---
        if position_size_pct > self.config.max_position_pct:
            violations.append(RiskViolation(
                rule="MAX_POSITION_SIZE",
                detail=f"Position {position_size_pct:.2%} exceeds limit {self.config.max_position_pct:.2%}",
                severity="HARD"
            ))

        # --- Rule 3: Daily Loss Limit ---
        if daily_loss_pct > self.config.daily_loss_limit_pct:
            violations.append(RiskViolation(
                rule="DAILY_LOSS_LIMIT",
                detail=f"Daily loss {daily_loss_pct:.2%} exceeds limit {self.config.daily_loss_limit_pct:.2%}",
                severity="HARD"
            ))

        # --- Rule 4: Max Drawdown ---
        if drawdown_pct > self.config.max_drawdown_pct:
            violations.append(RiskViolation(
                rule="MAX_DRAWDOWN",
                detail=f"Drawdown {drawdown_pct:.2%} exceeds max {self.config.max_drawdown_pct:.2%}",
                severity="HARD"
            ))

        # --- Rule 5: Leverage Cap ---
        if leverage > self.config.leverage_cap:
            violations.append(RiskViolation(
                rule="LEVERAGE_CAP",
                detail=f"Leverage {leverage:.1f}x exceeds cap {self.config.leverage_cap:.1f}x",
                severity="HARD"
            ))

        # --- Rule 6: Volatility Filter ---
        if len(returns_to_use) >= 5 and volatility_pct > self.config.max_volatility_pct:
            violations.append(RiskViolation(
                rule="VOLATILITY_FILTER",
                detail=f"Asset volatility {volatility_pct:.2%} exceeds threshold {self.config.max_volatility_pct:.2%}",
                severity="HARD"
            ))

        # --- Rule 7: VaR Check ---
        if len(returns_to_use) >= 5 and var_pct > self.config.var_limit_pct:
            violations.append(RiskViolation(
                rule="VAR_LIMIT",
                detail=f"95% VaR {var_pct:.2%} exceeds limit {self.config.var_limit_pct:.2%}",
                severity="HARD"
            ))

        # --- Compute Risk Score ---
        risk_score = self._compute_risk_score(
            position_size_pct, daily_loss_pct, drawdown_pct,
            volatility_pct, var_pct, leverage
        )

        # Decision: REJECT if any HARD violation
        hard_violations = [v for v in violations if v.severity == "HARD"]
        decision = RiskDecision.REJECT if hard_violations else RiskDecision.APPROVE

        return RiskAssessment(
            decision=decision,
            risk_score=round(risk_score, 2),
            violations=violations,
            var_pct=round(var_pct, 6),
            volatility_pct=round(volatility_pct, 6),
            position_size_pct=round(position_size_pct, 6),
            recommended_size_pct=round(recommended_pct, 6),
            circuit_breaker_active=self.circuit_breaker_active,
        )

    def compute_sharpe(self) -> float:
        """Approximate Sharpe ratio from stored returns (annualised, Rf=0)."""
        return _sharpe_ratio(self._returns)

    def get_status(self) -> dict:
        return {
            "current_capital": self.current_capital,
            "capital_peak": self.capital_peak,
            "daily_loss_pct": round(self._daily_loss_pct(), 6),
            "drawdown_pct": round(self._drawdown_pct(), 6),
            "circuit_breaker_active": self.circuit_breaker_active,
            "volatility_pct": round(self._rolling_volatility(self._returns), 6),
            "var_95_pct": round(self._historical_var(self._returns), 6),
            "sharpe_ratio": round(self.compute_sharpe(), 4),
            "num_returns_tracked": len(self._returns),
        }

    # ------------------------------------------------------------------ #
    #  Private Helpers
    # ------------------------------------------------------------------ #

    def _daily_loss_pct(self) -> float:
        if self.daily_start_capital <= 0:
            return 0.0
        loss = self.daily_start_capital - self.current_capital
        return max(0.0, loss / self.daily_start_capital)

    def _drawdown_pct(self) -> float:
        if self.capital_peak <= 0:
            return 0.0
        loss = self.capital_peak - self.current_capital
        return max(0.0, loss / self.capital_peak)

    def _rolling_volatility(self, returns: list[float]) -> float:
        """Daily std dev over the last `volatility_window` returns."""
        window = self.config.volatility_window
        r = returns[-window:] if len(returns) >= window else returns
        if len(r) < 2:
            return 0.0
        return _std(r)

    def _historical_var(self, returns: list[float]) -> float:
        """
        Parametric VaR (95%) using mean and std of returns.
        VaR = -(mean - z * std)  where z=1.645 for 95%
        Returns positive number representing potential loss %.
        """
        if len(returns) < 5:
            return 0.0
        mu  = sum(returns) / len(returns)
        std = _std(returns)
        z   = 1.645  # 95th percentile one-tailed
        var = -(mu - z * std)
        return max(0.0, var)

    def _fixed_fractional_size(self, risk_per_trade: float = 0.01) -> float:
        """
        Fixed fractional position sizing.
        Default: risk 1% of capital per trade (conservative).
        Returns fraction of capital to commit.
        """
        return min(risk_per_trade, self.config.max_position_pct)

    def _compute_risk_score(
        self,
        pos_pct: float,
        daily_loss: float,
        drawdown: float,
        vol: float,
        var: float,
        leverage: float,
    ) -> float:
        """
        Weighted risk score 0–100.
        Higher = riskier.
        """
        scores = [
            min(100, (pos_pct   / self.config.max_position_pct)     * 30),
            min(100, (daily_loss / max(self.config.daily_loss_limit_pct, 1e-9)) * 25),
            min(100, (drawdown   / max(self.config.max_drawdown_pct, 1e-9))     * 20),
            min(100, (vol        / max(self.config.max_volatility_pct, 1e-9))   * 15),
            min(100, (leverage   / self.config.leverage_cap)                    * 10),
        ]
        return sum(scores) / len(scores)


# ------------------------------------------------------------------ #
#  Pure utility functions
# ------------------------------------------------------------------ #

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0

def _variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return sum((x - m) ** 2 for x in values) / (len(values) - 1)

def _std(values: list[float]) -> float:
    return math.sqrt(_variance(values))

def _sharpe_ratio(returns: list[float], periods_per_year: int = 252) -> float:
    """Annualised Sharpe (Rf = 0)."""
    if len(returns) < 5:
        return 0.0
    m   = _mean(returns)
    std = _std(returns)
    if std == 0:
        return 0.0
    return (m / std) * math.sqrt(periods_per_year)
