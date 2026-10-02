from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import main
from services import auth, trade_validator


def test_trade_api_auth_roles_and_market_error(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    monkeypatch.setattr(trade_validator, "engine", engine)
    monkeypatch.setattr(auth, "AUTH_REQUIRED", True)
    trader_key = "trader-secret-for-testing-0123456789"
    operator_key = "operator-secret-for-testing-0123456789"
    monkeypatch.setattr(auth, "API_KEYS", {
        "trader": (auth.hash_api_key(trader_key).split("$", 1)[1],),
        "operator": (auth.hash_api_key(operator_key).split("$", 1)[1],),
    })
    monkeypatch.setattr(main, "AGENT_MODE", "MANUAL")
    monkeypatch.setattr(main, "get_market_snapshot", lambda symbol: {
        "symbol": symbol, "price": 2_000.0, "completed_candle_return": 0.001,
        "historical_closes": [100 + index for index in range(30)],
        "historical_returns": [0.001] * 29, "completed_candle_at": 1,
        "available": True, "stale": False, "history_available": True,
        "source": "fixture", "observed_at": "2026-01-01T00:00:00Z", "age_sec": 0,
        "error": None,
    })
    monkeypatch.setattr(main, "get_agent_nonce", lambda address: 0)
    monkeypatch.setattr(main, "submit_signed_intent", lambda intent, signature: {
        "chain_id": 31337, "transaction_hash": "0xabc", "block_number": 12, "gas_used": 50_000,
    })
    with TestClient(main.app) as client:
        assert client.post("/trade-intent", json={"token_in": "USDC", "token_out": "WETH",
                                                  "amount_in_usd": 500}).status_code == 401
        forbidden = client.post("/circuit-breaker", headers={"X-API-Key": trader_key},
                                json={"action": "trip"})
        assert forbidden.status_code == 403
        response = client.post("/trade-intent", headers={"X-API-Key": trader_key},
                               json={"token_in": "USDC", "token_out": "WETH", "amount_in_usd": 500})
        assert response.status_code == 200, response.text
        assert response.json()["simulation"]["market_data_source"].startswith("Kraken")
        chain_response = client.post("/trade-intent", headers={"X-API-Key": trader_key},
                                     json={"token_in": "USDC", "token_out": "WETH",
                                           "amount_in_usd": 500, "submit_onchain": True})
        assert chain_response.status_code == 200
        assert chain_response.json()["onchain"]["status"] == "submitted"
        breaker = client.post("/circuit-breaker", headers={"X-API-Key": operator_key},
                              json={"action": "trip"})
        assert breaker.status_code == 200
        assert client.get("/metrics").status_code == 200
        assert client.get("/health").headers.get("x-request-id")
    engine.dispose()


def test_api_rate_limit_returns_retry_after(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_READS_PER_MINUTE", "1")
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200
        limited = client.get("/health")
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) >= 1


def test_walk_forward_backtest_endpoint():
    with TestClient(main.app) as client:
        response = client.post("/walk-forward-backtest", json={
            "prices": [100 + index * 0.1 for index in range(80)], "folds": 4,
        })
        assert response.status_code == 200, response.text
        assert response.json()["fold_count"] == 4
