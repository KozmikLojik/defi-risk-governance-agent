"""Candle-by-candle momentum backtest with next-candle execution (no look-ahead)."""

from dataclasses import asdict, dataclass
import math

from services.risk_engine import RiskConfig, RiskEngine, _sharpe_ratio


@dataclass
class BacktestTrade:
    signal_index: int
    entry_index: int
    entry_price: float
    exit_price: float
    return_pct: float
    capital_after: float


def run_backtest(prices: list[float], starting_capital: float = 10_000,
                 fee_bps: float = 20, config: RiskConfig | None = None,
                 evaluation_start_index: int = 20) -> dict:
    if len(prices) < 22:
        raise ValueError("At least 22 chronological prices are required")
    if not math.isfinite(starting_capital) or not math.isfinite(fee_bps) or starting_capital <= 0 or fee_bps < 0 or fee_bps > 1000:
        raise ValueError("Starting capital must be positive and fee_bps must be between 0 and 1000")
    if any(not math.isfinite(price) or price <= 0 for price in prices):
        raise ValueError("Prices must be finite and positive")
    if not 20 <= evaluation_start_index < len(prices) - 1:
        raise ValueError("evaluation_start_index must leave at least one next-candle execution")

    engine = RiskEngine(config)
    engine.initialize(starting_capital)
    capital = starting_capital
    equity = [capital]
    returns = [b / a - 1 for a, b in zip(prices, prices[1:])]
    trades: list[BacktestTrade] = []

    # Signal uses data through candle i; execution occurs during candle i+1.
    for signal_index in range(20, len(prices) - 1):
        if signal_index < evaluation_start_index:
            equity.append(capital)
            continue
        history = prices[:signal_index + 1]
        past_returns = returns[:signal_index]
        assessment = engine.validate_trade_intent(
            trade_value_usd=capital * min(0.01, engine.config.max_position_pct),
            leverage=1.0,
            asset_returns=past_returns,
            asset_prices=history,
        )
        if assessment.decision.value == "APPROVE" and assessment.momentum_signal == "BUY":
            entry = prices[signal_index]
            exit_price = prices[signal_index + 1]
            gross_return = exit_price / entry - 1
            net_return = gross_return - (fee_bps / 10_000)
            allocation = capital * min(0.01, engine.config.max_position_pct)
            trade_pnl_pct = allocation * net_return / capital
            capital = max(0.0, capital * (1 + trade_pnl_pct))
            trades.append(BacktestTrade(signal_index, signal_index + 1, entry,
                                        exit_price, net_return, capital))
            engine.update_capital(capital, price_return=trade_pnl_pct)
        equity.append(capital)

    peak = starting_capital
    max_drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    benchmark_return = prices[-1] / prices[0] - 1
    return {
        "starting_capital": starting_capital,
        "ending_capital": round(capital, 4),
        "strategy_return_pct": round(capital / starting_capital - 1, 6),
        "buy_and_hold_return_pct": round(benchmark_return, 6),
        "max_drawdown_pct": round(max_drawdown, 6),
        "trade_count": len(trades),
        "win_rate": round(sum(trade.return_pct > 0 for trade in trades) / len(trades), 4) if trades else 0.0,
        "sharpe_ratio": round(_sharpe_ratio([trade.return_pct for trade in trades]), 4),
        "fee_bps": fee_bps,
        "trades": [asdict(trade) for trade in trades],
        "limitations": [
            "Signals use historical candles and execute on the following candle.",
            "Fees are fixed; spread, market impact, funding, and exchange outages are not modeled.",
            "Results are historical and do not predict future performance.",
        ],
    }


def run_walk_forward_backtest(prices: list[float], starting_capital: float = 10_000,
                              fee_bps: float = 20, folds: int = 5,
                              config: RiskConfig | None = None) -> dict:
    """Expand the training history; score each contiguous, unseen test window."""
    if not 2 <= folds <= 10:
        raise ValueError("folds must be between 2 and 10")
    if len(prices) < 44:
        raise ValueError("Walk-forward evaluation requires at least 44 chronological prices")
    train_size = max(21, len(prices) // 2)
    test_count = len(prices) - train_size
    if test_count < folds:
        raise ValueError("Not enough unseen candles for the requested number of folds")

    capital = starting_capital
    fold_results = []
    all_trades = []
    benchmark_growth = 1.0
    worst_drawdown = 0.0
    boundaries = [train_size + (test_count * index // folds) for index in range(folds + 1)]
    for fold_index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        if end - start < 1:
            continue
        fold = run_backtest(
            prices[:end], starting_capital=capital, fee_bps=fee_bps, config=config,
            evaluation_start_index=start - 1,
        )
        capital = fold["ending_capital"]
        benchmark = prices[end - 1] / prices[start - 1] - 1
        benchmark_growth *= 1 + benchmark
        worst_drawdown = max(worst_drawdown, fold["max_drawdown_pct"])
        all_trades.extend(fold["trades"])
        fold_results.append({
            "fold": fold_index,
            "train_candles": start,
            "test_start_index": start,
            "test_end_index_exclusive": end,
            "test_candles": end - start,
            "strategy_return_pct": fold["strategy_return_pct"],
            "buy_and_hold_return_pct": round(benchmark, 6),
            "max_drawdown_pct": fold["max_drawdown_pct"],
            "trade_count": fold["trade_count"],
        })

    returns = [trade["return_pct"] for trade in all_trades]
    return {
        "method": "expanding-window walk-forward; each test window is evaluated once",
        "starting_capital": starting_capital,
        "ending_capital": round(capital, 4),
        "out_of_sample_return_pct": round(capital / starting_capital - 1, 6),
        "out_of_sample_buy_and_hold_return_pct": round(benchmark_growth - 1, 6),
        "max_fold_drawdown_pct": round(worst_drawdown, 6),
        "fold_count": len(fold_results),
        "trade_count": len(all_trades),
        "win_rate": round(sum(value > 0 for value in returns) / len(returns), 4) if returns else 0.0,
        "sharpe_ratio": round(_sharpe_ratio(returns), 4),
        "fee_bps": fee_bps,
        "folds": fold_results,
        "limitations": [
            "The current strategy is fixed; this evaluation does not tune or select parameters.",
            "Each fold starts from compounded prior out-of-sample capital, but each is a simplified independent simulation.",
            "Fees are fixed and spread, market impact, funding, liquidity, survivorship, and exchange outages are omitted.",
            "Historical and out-of-sample results do not predict future performance.",
        ],
    }
