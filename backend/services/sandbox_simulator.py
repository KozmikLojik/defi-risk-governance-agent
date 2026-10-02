"""
services/sandbox_simulator.py
GuardianAI — Stablecoin Sandbox Capital Simulator

Simulates PnL, drawdown, and mock price feeds for testnet demo.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class TradeResult:
    trade_id: str
    token_in: str
    token_out: str
    amount_usd: float
    pnl_usd: float
    pnl_pct: float
    entry_price: float
    exit_price: float
    slippage_pct: float
    capital_after: float
    timestamp: str
    market_data_source: str


class SandboxSimulator:
    """
    Simulates capital allocation and PnL for approved trades.
    Uses the latest completed Kraken candle return as an explicitly labelled
    historical scenario. This is not a prediction or a forward test.
    """

    def __init__(self, starting_capital: float = 10_000.0):
        self.starting_capital  = starting_capital
        self.current_capital   = starting_capital
        self.peak_capital      = starting_capital
        self._trade_results: list[TradeResult] = []
    def simulate_trade(
        self,
        trade_id: str,
        token_in: str,
        token_out: str,
        amount_usd: float,
        approved: bool,
        reference_price: Optional[float] = None,
        historical_return: Optional[float] = None,
        market_data_source: str = "Kraken completed candle scenario",
        leverage: float = 1.0,
    ) -> Optional[TradeResult]:
        """
        Simulate execution of a trade. Only executes if approved=True.
        Returns None if rejected.
        """
        if not approved:
            return None
        if reference_price is None or reference_price <= 0 or historical_return is None:
            return None

        if amount_usd > self.current_capital:
            amount_usd = self.current_capital * 0.95

        entry_price = reference_price
        slippage_pct = 0.001  # 10 bps per simulated round trip
        price_change = max(-0.95, min(1.0, historical_return))
        exit_price = entry_price * (1 + price_change)
        net_return = max(-1.0, price_change * leverage - slippage_pct * 2 * leverage)
        net_pnl = amount_usd * net_return

        self.current_capital += net_pnl
        if self.current_capital > self.peak_capital:
            self.peak_capital = self.current_capital

        result = TradeResult(
            trade_id=trade_id,
            token_in=token_in,
            token_out=token_out,
            amount_usd=amount_usd,
            pnl_usd=round(net_pnl, 4),
            pnl_pct=round(net_return, 6) if amount_usd > 0 else 0,
            entry_price=round(entry_price, 4),
            exit_price=round(exit_price, 4),
            slippage_pct=round(slippage_pct, 6),
            capital_after=round(self.current_capital, 4),
            timestamp=datetime.utcnow().isoformat(),
            market_data_source=market_data_source,
        )
        self._trade_results.append(result)
        return result

    def get_stats(self) -> dict:
        cap = self.current_capital
        peak = self.peak_capital
        drawdown = max(0.0, (peak - cap) / peak) if peak > 0 else 0.0
        total_pnl = cap - self.starting_capital
        returns = [r.pnl_pct for r in self._trade_results]
        return {
            "starting_capital": self.starting_capital,
            "current_capital":  round(cap, 4),
            "peak_capital":     round(peak, 4),
            "total_pnl_usd":    round(total_pnl, 4),
            "total_pnl_pct":    round(total_pnl / self.starting_capital, 6),
            "drawdown_pct":     round(drawdown, 6),
            "num_trades":       len(self._trade_results),
            "recent_returns":   returns[-20:],
        }

    def recent_trades(self, n: int = 10) -> list[dict]:
        return [
            {
                "trade_id":   r.trade_id,
                "token_in":   r.token_in,
                "token_out":  r.token_out,
                "amount_usd": r.amount_usd,
                "pnl_usd":    r.pnl_usd,
                "pnl_pct":    r.pnl_pct,
                "capital_after": r.capital_after,
                "timestamp":  r.timestamp,
                "market_data_source": r.market_data_source,
            }
            for r in self._trade_results[-n:][::-1]
        ]

    def export_state(self) -> dict:
        return {
            "starting_capital": self.starting_capital,
            "current_capital": self.current_capital,
            "peak_capital": self.peak_capital,
            "trades": [result.__dict__ for result in self._trade_results],
        }

    def restore_state(self, state: dict) -> None:
        if not state:
            return
        self.current_capital = float(state.get("current_capital", self.current_capital))
        self.peak_capital = float(state.get("peak_capital", self.peak_capital))
        self._trade_results = [TradeResult(**trade) for trade in state.get("trades", [])]
