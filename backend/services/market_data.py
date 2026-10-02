"""Cached Kraken ticker and completed-candle data for paper trading."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger("market_data")
BASE_URL = "https://api.kraken.com/0/public"
PAIRS = {"BTC": "XBTUSD", "WBTC": "XBTUSD", "ETH": "ETHUSD", "WETH": "ETHUSD",
         "LINK": "LINKUSD", "UNI": "UNIUSD"}
CACHE_TTL_SEC = 10
STALE_AFTER_SEC = 60
_cache: dict[str, dict] = {}
_lock = threading.Lock()


def get_market_snapshot(symbol: str) -> dict:
    key = symbol.upper()
    pair = PAIRS.get(key)
    if not pair:
        return _unavailable(key, f"No Kraken USD market is configured for {key}")

    with _lock:
        cached = _cache.get(key)
        if cached and time.time() - cached["cached_at"] < CACHE_TTL_SEC:
            return _public_snapshot(cached, None)

        try:
            ticker_response = requests.get(f"{BASE_URL}/Ticker", params={"pair": pair}, timeout=8)
            ticker_response.raise_for_status()
            ticker = ticker_response.json()
            if ticker.get("error") or not ticker.get("result"):
                raise ValueError("Kraken ticker response was empty or contained errors")
            market = next(iter(ticker["result"].values()))
            price = float(market["c"][0])
            if price <= 0:
                raise ValueError("Kraken returned an invalid price")

            candle_return = None
            historical_closes: list[float] = []
            historical_returns: list[float] = []
            completed_candle_at = None
            candle_error = None
            try:
                candles_response = requests.get(
                    f"{BASE_URL}/OHLC", params={"pair": pair, "interval": 1, "since": 0}, timeout=8
                )
                candles_response.raise_for_status()
                candles = candles_response.json()
                if candles.get("error"):
                    raise ValueError("Kraken OHLC response contained errors")
                rows = next((value for key_, value in candles.get("result", {}).items() if key_ != "last"), [])
                # Exclude the current, unfinished candle from a completed-candle return.
                completed = rows[:-1]
                historical_closes = [float(row[4]) for row in completed if float(row[4]) > 0]
                historical_returns = [b / a - 1 for a, b in zip(historical_closes, historical_closes[1:])]
                if len(completed) >= 2 and historical_closes:
                    previous, latest = historical_closes[-2:]
                    candle_return = latest / previous - 1 if previous > 0 else None
                    completed_candle_at = int(completed[-1][0])
            except Exception as exc:
                candle_error = str(exc)
                logger.warning("Kraken candle request failed for %s: %s", key, exc)

            snapshot = {
                "symbol": key,
                "price": price,
                "completed_candle_return": candle_return,
                "historical_closes": historical_closes[-100:],
                "historical_returns": historical_returns[-99:],
                "completed_candle_at": completed_candle_at,
                "source": "Kraken",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "cached_at": time.time(),
                "error": candle_error,
            }
            _cache[key] = snapshot
            return _public_snapshot(snapshot, candle_error)
        except Exception as exc:
            logger.warning("Kraken ticker request failed for %s: %s", key, exc)
            if cached:
                return _public_snapshot(cached, str(exc))
            return _unavailable(key, str(exc))


def _public_snapshot(snapshot: dict, error: str | None) -> dict:
    age = max(0, time.time() - snapshot["cached_at"])
    return {
        "symbol": snapshot["symbol"],
        "price": snapshot["price"],
        "completed_candle_return": snapshot["completed_candle_return"],
        "historical_closes": snapshot.get("historical_closes", []),
        "historical_returns": snapshot.get("historical_returns", []),
        "completed_candle_at": snapshot.get("completed_candle_at"),
        "history_available": snapshot.get("completed_candle_return") is not None,
        "history_error": error if snapshot.get("completed_candle_return") is None else None,
        "source": snapshot["source"],
        "observed_at": snapshot["observed_at"],
        "age_sec": round(age, 2),
        "available": age <= STALE_AFTER_SEC,
        "stale": age > STALE_AFTER_SEC,
        "error": error,
    }


def _unavailable(symbol: str, error: str) -> dict:
    return {"symbol": symbol, "price": None, "completed_candle_return": None,
            "historical_closes": [], "historical_returns": [], "completed_candle_at": None,
            "history_available": False,
            "history_error": error,
            "source": "Kraken", "observed_at": None, "age_sec": None,
            "available": False, "stale": False, "error": error}
