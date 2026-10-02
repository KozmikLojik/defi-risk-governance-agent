from services import market_data


def test_stale_cached_price_is_reported_as_stale(monkeypatch):
    snapshot = {
        "symbol": "BTC", "price": 50_000.0, "completed_candle_return": 0.01,
        "historical_closes": [49_000.0, 50_000.0], "historical_returns": [0.02],
        "completed_candle_at": 100, "source": "Kraken",
        "observed_at": "2026-01-01T00:00:00+00:00", "cached_at": 100.0,
    }
    monkeypatch.setattr(market_data.time, "time", lambda: 200.0)
    result = market_data._public_snapshot(snapshot, "timeout")
    assert result["price"] == 50_000.0
    assert result["available"] is False
    assert result["stale"] is True
    assert result["error"] == "timeout"


def test_unknown_symbol_is_reported_offline():
    result = market_data.get_market_snapshot("NOT-A-PAIR")
    assert result["available"] is False
    assert result["history_available"] is False
