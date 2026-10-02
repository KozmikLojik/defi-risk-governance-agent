import pytest

from services.backtest import run_backtest, run_walk_forward_backtest


def test_backtest_executes_after_signal_and_is_repeatable():
    prices = [100 + index * 0.5 for index in range(80)]
    first = run_backtest(prices)
    second = run_backtest(prices)
    assert first == second
    assert first["trade_count"] > 0
    assert first["ending_capital"] < first["starting_capital"] * 1.1
    assert first["limitations"]


def test_backtest_rejects_too_short_or_invalid_prices():
    with pytest.raises(ValueError):
        run_backtest([1.0] * 21)
    with pytest.raises(ValueError):
        run_backtest([1.0] * 21 + [0.0])


def test_walk_forward_uses_contiguous_out_of_sample_windows():
    prices = [100 + index * 0.2 + (index % 7) * 0.1 for index in range(100)]
    result = run_walk_forward_backtest(prices, folds=4)
    assert result["fold_count"] == 4
    assert result["folds"][0]["train_candles"] == 50
    for left, right in zip(result["folds"], result["folds"][1:]):
        assert left["test_end_index_exclusive"] == right["test_start_index"]
    assert result["out_of_sample_return_pct"] is not None
    assert "do not predict future performance" in result["limitations"][-1]


def test_backtest_rejects_non_finite_prices_and_unusable_walk_forward():
    with pytest.raises(ValueError):
        run_backtest([float("nan")] + [100.0] * 21)
    with pytest.raises(ValueError):
        run_walk_forward_backtest([100.0] * 43, folds=3)
