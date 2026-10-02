"""
services/trade_validator.py
GuardianAI — Trade Intent Validator

Handles:
  - EIP-712 TradeIntent struct construction
  - Risk engine pre-validation
  - Artifact emission (SQLite)
  - Signing approved intents
  - Forwarding to Risk Router (simulated)
"""

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from eth_account import Account
from sqlalchemy import create_engine, text

from services.risk_engine import RiskEngine, RiskDecision

logger = logging.getLogger("trade_validator")

DATABASE_URL = os.getenv("DATABASE_URL")
if (os.getenv("VERCEL") or os.getenv("ENVIRONMENT", "").lower() == "production") and not DATABASE_URL:
    raise RuntimeError("Production requires DATABASE_URL pointing to persistent PostgreSQL storage")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://"):]

if DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
else:
    DB_PATH = Path(os.getenv("GUARDIAN_DB_PATH", Path(__file__).resolve().parents[1] / "data" / "guardianai.db"))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{DB_PATH.as_posix()}", connect_args={"check_same_thread": False})


# ────────────────────────────────────────────────────────────
#  EIP-712 Domain + Types
# ────────────────────────────────────────────────────────────

GUARDIAN_DOMAIN = {
    "name": "GuardianAI RiskRouter",
    "version": "1",
    # chainId and verifyingContract set at runtime
}

CHAIN_TOKEN_METADATA = {
    # Deliberate local-only placeholders. The router records intents but does not
    # transfer these tokens; public chains must provide real token metadata.
    31337: {
        "USDC": {"address": "0x0000000000000000000000000000000000001001", "decimals": 6, "usd_stable": True},
        "USDT": {"address": "0x0000000000000000000000000000000000001002", "decimals": 6, "usd_stable": True},
        "DAI": {"address": "0x0000000000000000000000000000000000001003", "decimals": 18, "usd_stable": True},
        "WETH": {"address": "0x0000000000000000000000000000000000002001", "decimals": 18, "usd_stable": False},
        "WBTC": {"address": "0x0000000000000000000000000000000000002002", "decimals": 8, "usd_stable": False},
        "BTC": {"address": "0x0000000000000000000000000000000000002002", "decimals": 8, "usd_stable": False},
        "LINK": {"address": "0x0000000000000000000000000000000000002003", "decimals": 18, "usd_stable": False},
        "UNI": {"address": "0x0000000000000000000000000000000000002004", "decimals": 18, "usd_stable": False},
    },
    1: {
        "USDC": {"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "decimals": 6, "usd_stable": True},
        "USDT": {"address": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6, "usd_stable": True},
        "DAI": {"address": "0x6B175474E89094C44Da98b954EedeAC495271d0F", "decimals": 18, "usd_stable": True},
        "WETH": {"address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "decimals": 18, "usd_stable": False},
        "WBTC": {"address": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", "decimals": 8, "usd_stable": False},
        "BTC": {"address": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", "decimals": 8, "usd_stable": False},
        "LINK": {"address": "0x514910771AF9Ca656af840dff83E8264EcF986CA", "decimals": 18, "usd_stable": False},
        "UNI": {"address": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", "decimals": 18, "usd_stable": False},
    }
}
if os.getenv("TOKEN_METADATA_JSON"):
    for chain, tokens in json.loads(os.environ["TOKEN_METADATA_JSON"]).items():
        CHAIN_TOKEN_METADATA.setdefault(int(chain), {}).update(tokens)
if os.getenv("TOKEN_ADDRESS_MAP"):
    # Backwards-compatible mapping for the configured chain.
    configured_chain = int(os.getenv("CHAIN_ID", "31337"))
    CHAIN_TOKEN_METADATA.setdefault(configured_chain, {}).update({
        symbol: {"address": address, "decimals": 6 if symbol.upper() in {"USDC", "USDT"} else 18,
                 "usd_stable": symbol.upper() in {"USDC", "USDT", "DAI"}}
        for symbol, address in json.loads(os.environ["TOKEN_ADDRESS_MAP"]).items()
    })

TRADE_INTENT_TYPES = {
    "EIP712Domain": [
        {"name": "name",    "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "TradeIntent": [
        {"name": "agent",            "type": "address"},
        {"name": "tokenIn",          "type": "address"},
        {"name": "tokenOut",         "type": "address"},
        {"name": "amountIn",         "type": "uint256"},
        {"name": "maxSlippageBps",   "type": "uint256"},
        {"name": "deadline",         "type": "uint256"},
        {"name": "riskArtifactHash", "type": "bytes32"},
        {"name": "nonce",            "type": "uint256"},
    ],
}


# ────────────────────────────────────────────────────────────
#  Validation Artifact
# ────────────────────────────────────────────────────────────

class ValidationArtifact:
    def __init__(
        self,
        trade_id: str,
        agent_address: str,
        token_in: str,
        token_out: str,
        amount_in_usd: float,
        leverage: float,
        risk_score: float,
        decision: str,
        violations: list[dict],
        var_pct: float,
        volatility_pct: float,
        position_size_pct: float,
        circuit_breaker: bool,
        signal: Optional[dict] = None,
        risk_checks: Optional[list] = None,
        intent: Optional[dict] = None,
        signature: Optional[str] = None,
    ):
        self.trade_id = trade_id
        self.agent_address = agent_address
        self.token_in = token_in
        self.token_out = token_out
        self.amount_in_usd = amount_in_usd
        self.leverage = leverage
        self.risk_score = risk_score
        self.decision = decision
        self.violations = violations
        self.var_pct = var_pct
        self.volatility_pct = volatility_pct
        self.position_size_pct = position_size_pct
        self.circuit_breaker = circuit_breaker
        self.signal = signal or {}
        self.risk_checks = risk_checks or []
        self.intent = intent or {}
        self.signature = signature
        self.timestamp = datetime.utcnow().isoformat()
        self.hash_ref = self._compute_hash()

    def _compute_hash(self) -> str:
        return "0x" + hashlib.sha256(f"{self.trade_id}:{self.risk_score}".encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "trade_id":          self.trade_id,
            "agent_address":     self.agent_address,
            "token_in":          self.token_in,
            "token_out":         self.token_out,
            "amount_in_usd":     self.amount_in_usd,
            "leverage":          self.leverage,
            "risk_score":        self.risk_score,
            "decision":          self.decision,
            "violations":        self.violations,
            "var_pct":           self.var_pct,
            "volatility_pct":    self.volatility_pct,
            "position_size_pct": self.position_size_pct,
            "circuit_breaker":   self.circuit_breaker,
            "signal":            self.signal,
            "risk_checks":       self.risk_checks,
            "intent":            self.intent,
            "signature":         self.signature,
            "timestamp":         self.timestamp,
            "hash_ref":          self.hash_ref,
        }


# ────────────────────────────────────────────────────────────
#  Database Init
# ────────────────────────────────────────────────────────────

def init_db():
    from services.database_migrations import apply_migrations
    apply_migrations(engine)


def load_runtime_state() -> dict:
    init_db()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT state_json FROM runtime_state WHERE state_key='app'" )).first()
    return json.loads(row[0]) if row else {}


def save_runtime_state(state: dict) -> None:
    init_db()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO runtime_state (state_key, state_json) VALUES ('app', :state_json)
            ON CONFLICT (state_key) DO UPDATE SET state_json=excluded.state_json
        """), {"state_json": json.dumps(state)})


def update_artifact_chain_status(trade_id: str, status: str, tx_hash: str = "") -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE trade_artifacts SET onchain_status=:status, onchain_tx_hash=:tx_hash
            WHERE trade_id=:trade_id
        """), {"status": status, "tx_hash": tx_hash, "trade_id": trade_id})


def save_artifact(artifact: ValidationArtifact):
    d = artifact.to_dict()
    with engine.begin() as conn:
        conn.execute(text("""
        INSERT INTO trade_artifacts (
            trade_id, agent_address, token_in, token_out, amount_in_usd, leverage,
            risk_score, decision, violations_json, var_pct, volatility_pct,
            position_size_pct, circuit_breaker, signal_json, risk_checks_json,
            intent_json, signature, timestamp, hash_ref
            , onchain_status, onchain_tx_hash
        ) VALUES (
            :trade_id, :agent_address, :token_in, :token_out,
            :amount_in_usd, :leverage, :risk_score, :decision,
            :violations_json, :var_pct, :volatility_pct,
            :position_size_pct, :circuit_breaker, :signal_json,
            :risk_checks_json, :intent_json, :signature,
            :timestamp, :hash_ref, 'not_requested', ''
        )
        ON CONFLICT (trade_id) DO UPDATE SET
            signature=excluded.signature,
            intent_json=excluded.intent_json,
            timestamp=excluded.timestamp,
            hash_ref=excluded.hash_ref
    """), {
            **d,
            "violations_json": json.dumps(d["violations"]),
            "circuit_breaker": int(d["circuit_breaker"]),
            "signal_json": json.dumps(d.get("signal", {})),
            "risk_checks_json": json.dumps(d.get("risk_checks", [])),
            "intent_json": json.dumps(d.get("intent", {})),
        })


def fetch_recent_artifacts(limit: int = 50) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT * FROM trade_artifacts ORDER BY timestamp DESC LIMIT :limit"
        ), {"limit": limit}).mappings().all()
    results = []
    for row in rows:
        d = dict(row)
        d["violations"] = json.loads(d.pop("violations_json", "[]"))
        d["signal"] = json.loads(d.pop("signal_json", None) or "{}")
        d["risk_checks"] = json.loads(d.pop("risk_checks_json", None) or "[]")
        d["intent"] = json.loads(d.pop("intent_json", None) or "{}")
        d["onchain_status"] = d.get("onchain_status") or "not_requested"
        d["onchain_tx_hash"] = d.get("onchain_tx_hash") or ""
        d["circuit_breaker"] = bool(d["circuit_breaker"])
        results.append(d)
    return results


# ────────────────────────────────────────────────────────────
#  Trade Validator
# ────────────────────────────────────────────────────────────

class TradeValidator:
    def __init__(
        self,
        risk_engine: RiskEngine,
        agent_private_key: str,
        chain_id: int = 31337,
        router_address: str = "0x0000000000000000000000000000000000000001",
        agent_nonces: Optional[dict] = None,
    ):
        self.risk_engine    = risk_engine
        self.account        = Account.from_key(agent_private_key)
        self.agent_address  = self.account.address
        self.chain_id       = chain_id
        self.router_address = router_address
        self._nonces: dict[str, int] = agent_nonces or {}
        init_db()

    def validate_and_process(
        self,
        token_in: str,
        token_out: str,
        amount_in_usd: float,
        leverage: float = 1.0,
        max_slippage_bps: int = 50,
        asset_returns: Optional[list[float]] = None,
        asset_prices: Optional[list[float]] = None,
        intent_nonce: Optional[int] = None,
    ) -> ValidationArtifact:
        """
        Main entry point. Validates a trade intent against all risk rules.
        If approved, creates EIP-712 signed intent. Emits artifact in all cases.
        """
        trade_id = str(uuid.uuid4())
        self._last_signed_intent = {}
        logger.info(f"Validating trade {trade_id}: {token_in}→{token_out} ${amount_in_usd:.2f} {leverage}x")

        # 1. Run risk assessment
        assessment = self.risk_engine.validate_trade_intent(
            trade_value_usd=amount_in_usd,
            leverage=leverage,
            asset_returns=asset_returns,
            asset_prices=asset_prices,
        )

        signature = None
        decision = assessment.decision.value
        signing_failed = False
        violations = [{"rule": v.rule, "detail": v.detail, "severity": v.severity}
                      for v in assessment.violations]

        if assessment.decision == RiskDecision.APPROVE:
            # 2. Build + sign EIP-712 TradeIntent
            try:
                signature = self._sign_trade_intent(
                    trade_id=trade_id,
                    token_in=token_in,
                    token_out=token_out,
                    amount_in_usd=amount_in_usd,
                    max_slippage_bps=max_slippage_bps,
                    risk_score=assessment.risk_score,
                    intent_nonce=intent_nonce,
                )
                logger.info(f"Trade {trade_id} APPROVED and signed")
            except Exception as e:
                logger.exception("Signing failed for %s", trade_id)
                decision = RiskDecision.REJECT.value
                signing_failed = True
                violations.append({
                    "rule": "SIGNING_FAILED",
                    "detail": "Could not create a valid EIP-712 signature for this token pair.",
                    "severity": "HARD",
                })

        else:
            rules = [v.rule for v in assessment.violations]
            logger.warning(f"Trade {trade_id} REJECTED — rules: {rules}")

        # 3. Build structured proof + artifact
        risk_checks = [
            {
                "rule": v.rule,
                "passed": v.severity != "HARD",
                "value": v.detail,
                "severity": v.severity,
            }
            for v in assessment.violations
        ]
        if signing_failed:
            risk_checks.append({"rule": "SIGNING_FAILED", "passed": False,
                                "value": "No valid intent signature was produced.", "severity": "HARD"})

        signal = {
            "momentum": assessment.momentum_signal,
            "rsi": assessment.rsi,
            "price_slope": assessment.price_slope,
            "volatility": assessment.volatility_pct,
            "var_pct": assessment.var_pct,
            "risk_score": assessment.risk_score,
        }

        artifact = ValidationArtifact(
            trade_id=trade_id,
            agent_address=self.agent_address,
            token_in=token_in,
            token_out=token_out,
            amount_in_usd=amount_in_usd,
            leverage=leverage,
            risk_score=assessment.risk_score,
            decision=decision,
            violations=violations,
            var_pct=assessment.var_pct,
            volatility_pct=assessment.volatility_pct,
            position_size_pct=assessment.position_size_pct,
            circuit_breaker=assessment.circuit_breaker_active,
            signal=signal,
            risk_checks=risk_checks,
            intent=getattr(self, "_last_signed_intent", {}),
            signature=signature,
        )

        # 4. Persist
        save_artifact(artifact)
        return artifact

    def _sign_trade_intent(
        self,
        trade_id: str,
        token_in: str,
        token_out: str,
        amount_in_usd: float,
        max_slippage_bps: int,
        risk_score: float,
        intent_nonce: Optional[int] = None,
    ) -> str:
        """Signs an EIP-712 TradeIntent."""
        import time

        # Use risk artifact hash as bytes32
        artifact_hash_hex = hashlib.sha256(
            f"{trade_id}:{risk_score}".encode()
        ).hexdigest()
        artifact_hash_bytes = bytes.fromhex(artifact_hash_hex)

        nonce = intent_nonce if intent_nonce is not None else self._nonces.get(self.agent_address, 0)
        self._nonces[self.agent_address] = nonce + 1

        domain = {
            **GUARDIAN_DOMAIN,
            "chainId": self.chain_id,
            "verifyingContract": self.router_address,
        }

        message = {
            "agent":            self.agent_address,
            "tokenIn":          _resolve_token_metadata(token_in, self.chain_id, require_usd_stable=True)["address"],
            "tokenOut":         _resolve_token_metadata(token_out, self.chain_id)["address"],
            "amountIn":         int(amount_in_usd * 10 ** _resolve_token_metadata(token_in, self.chain_id)["decimals"]),
            "maxSlippageBps":   max_slippage_bps,
            "deadline":         int(time.time()) + 300,  # 5 min
            "riskArtifactHash": artifact_hash_bytes,
            "nonce":            nonce,
        }

        signed = self.account.sign_typed_data(
            domain_data=domain,
            message_types={"TradeIntent": TRADE_INTENT_TYPES["TradeIntent"]},
            message_data=message,
        )
        self._last_signed_intent = {
            **message,
            "domain": domain,
            "tokenIn": message["tokenIn"],
            "tokenOut": message["tokenOut"],
            "amountIn": str(message["amountIn"]),
            "maxSlippageBps": str(message["maxSlippageBps"]),
            "deadline": str(message["deadline"]),
            "nonce": str(message["nonce"]),
            "riskArtifactHash": "0x" + artifact_hash_hex,
        }
        return signed.signature.hex()


def _resolve_token_metadata(token: str, chain_id: int, require_usd_stable: bool = False) -> dict:
    """Resolve symbols using metadata for this chain; explicit addresses require decimals."""
    from web3 import Web3

    metadata = CHAIN_TOKEN_METADATA.get(chain_id, {}).get(token.upper())
    if metadata:
        address = metadata["address"]
    elif Web3.is_address(token):
        raise ValueError("For explicit token addresses, configure TOKEN_METADATA_JSON with decimals")
    else:
        raise ValueError(f"No address/decimal metadata configured for {token} on chain {chain_id}")
    if not Web3.is_address(address):
        raise ValueError(f"Unknown token symbol or invalid token address: {token}")
    if not 0 <= int(metadata["decimals"]) <= 36:
        raise ValueError(f"Invalid decimals for token {token}")
    if require_usd_stable and not metadata.get("usd_stable", False):
        raise ValueError(f"Trade input {token} must be configured as a USD stablecoin")
    return {**metadata, "address": Web3.to_checksum_address(address)}
