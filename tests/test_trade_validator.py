from sqlalchemy import create_engine, text
from eth_account import Account
from eth_account.messages import encode_typed_data

from services import trade_validator
from services.risk_engine import RiskEngine


def test_btc_symbol_resolves_to_chain_wrapped_btc():
    btc = trade_validator._resolve_token_metadata("BTC", 1)
    wbtc = trade_validator._resolve_token_metadata("WBTC", 1)
    assert btc == wbtc


def test_signed_intent_recovers_agent_and_persists(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'trades.db').as_posix()}")
    monkeypatch.setattr(trade_validator, "engine", engine)
    risk = RiskEngine(); risk.initialize(10_000)
    account = Account.from_key("0x" + "11" * 32)
    validator = trade_validator.TradeValidator(risk, account.key.hex(), 31337,
                                               "0x0000000000000000000000000000000000000001")
    artifact = validator.validate_and_process("USDC", "WETH", 500)
    assert artifact.decision == "APPROVE"
    intent = artifact.intent
    message = {key: intent[key] for key in (
        "agent", "tokenIn", "tokenOut", "amountIn", "maxSlippageBps",
        "deadline", "riskArtifactHash", "nonce")}
    message["amountIn"] = int(message["amountIn"])
    message["maxSlippageBps"] = int(message["maxSlippageBps"])
    message["deadline"] = int(message["deadline"])
    message["nonce"] = int(message["nonce"])
    message["riskArtifactHash"] = bytes.fromhex(message["riskArtifactHash"][2:])
    signable = encode_typed_data(domain_data=intent["domain"],
                                 message_types={"TradeIntent": trade_validator.TRADE_INTENT_TYPES["TradeIntent"]},
                                 message_data=message)
    assert Account.recover_message(signable, signature=artifact.signature) == account.address
    saved = trade_validator.fetch_recent_artifacts()
    assert saved[0]["intent"]["riskArtifactHash"] == artifact.hash_ref
    assert saved[0]["decision"] == "APPROVE"
    engine.dispose()


def test_migrates_legacy_sqlite_schema(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE trade_artifacts (
            trade_id TEXT PRIMARY KEY, agent_address TEXT, token_in TEXT, token_out TEXT,
            amount_in_usd REAL, leverage REAL, risk_score REAL, decision TEXT,
            violations_json TEXT, var_pct REAL, volatility_pct REAL,
            position_size_pct REAL, circuit_breaker INTEGER, signature TEXT,
            timestamp TEXT, hash_ref TEXT)"""))
    monkeypatch.setattr(trade_validator, "engine", engine)
    trade_validator.init_db()
    from sqlalchemy import inspect
    columns = {item["name"] for item in inspect(engine).get_columns("trade_artifacts")}
    assert {"signal_json", "risk_checks_json", "intent_json", "onchain_status"} <= columns
    engine.dispose()


def test_runtime_state_survives_database_reconnect(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    first = create_engine(f"sqlite:///{path.as_posix()}")
    monkeypatch.setattr(trade_validator, "engine", first)
    trade_validator.save_runtime_state({"capital": 9123.45, "mode": "MANUAL"})
    first.dispose()

    reopened = create_engine(f"sqlite:///{path.as_posix()}")
    monkeypatch.setattr(trade_validator, "engine", reopened)
    assert trade_validator.load_runtime_state() == {"capital": 9123.45, "mode": "MANUAL"}
    reopened.dispose()
