"""
services/sandbox_simulator.py
GuardianAI — Stablecoin Sandbox Capital Simulator

Simulates PnL, drawdown, and mock price feeds for testnet demo.
"""

import random
import math
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


class SandboxSimulator:
    """
    Simulates capital allocation and PnL for approved trades.
    Uses randomized price movements with configurable volatility.
    """

    def __init__(self, starting_capital: float = 10_000.0, seed: int = 42):
        random.seed(seed)
        self.starting_capital  = starting_capital
        self.current_capital   = starting_capital
        self.peak_capital      = starting_capital
        self._trade_results: list[TradeResult] = []
        self._mock_prices: dict[str, float] = {
            "ETH":  1800.0,
            "BTC":  30000.0,
            "USDC": 1.0,
            "LINK": 7.50,
            "UNI":  4.20,
        }

    def get_mock_price(self, symbol: str) -> float:
        """Get current mock price with slight random drift."""
        base = self._mock_prices.get(symbol, 100.0)
        # Random walk: ±1.5% per call
        drift = random.gauss(0.0003, 0.008)
        new_price = base * (1 + drift)
        self._mock_prices[symbol] = new_price
        return round(new_price, 4)

    def simulate_trade(
        self,
        trade_id: str,
        token_in: str,
        token_out: str,
        amount_usd: float,
        approved: bool,
    ) -> Optional[TradeResult]:
        """
        Simulate execution of a trade. Only executes if approved=True.
        Returns None if rejected.
        """
        if not approved:
            return None

        if amount_usd > self.current_capital:
            amount_usd = self.current_capital * 0.95

        symbol_out = token_out.replace("USDC", "").replace("WETH", "ETH").strip() or "ETH"
        entry_price = self.get_mock_price(symbol_out)

        # Simulate slippage (0.05% - 0.5%)
        slippage_pct = random.uniform(0.0005, 0.005)

        # Simulate price move during holding (random, biased slightly negative for realism)
        price_change = random.gauss(-0.001, 0.015)
        exit_price   = entry_price * (1 + price_change)

        gross_pnl  = amount_usd * price_change
        slip_cost  = amount_usd * slippage_pct
        net_pnl    = gross_pnl - slip_cost

        self.current_capital += net_pnl
        if self.current_capital > self.peak_capital:
            self.peak_capital = self.current_capital

        result = TradeResult(
            trade_id=trade_id,
            token_in=token_in,
            token_out=token_out,
            amount_usd=amount_usd,
            pnl_usd=round(net_pnl, 4),
            pnl_pct=round(net_pnl / amount_usd, 6) if amount_usd > 0 else 0,
            entry_price=round(entry_price, 4),
            exit_price=round(exit_price, 4),
            slippage_pct=round(slippage_pct, 6),
            capital_after=round(self.current_capital, 4),
            timestamp=datetime.utcnow().isoformat(),
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
            }
            for r in self._trade_results[-n:][::-1]
        ]
