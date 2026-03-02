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
import sqlite3
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from eth_account import Account
from eth_account.messages import encode_typed_data

from services.risk_engine import RiskEngine, RiskDecision

logger = logging.getLogger("trade_validator")

DB_PATH = Path("data/guardianai.db")


# ────────────────────────────────────────────────────────────
#  EIP-712 Domain + Types
# ────────────────────────────────────────────────────────────

GUARDIAN_DOMAIN = {
    "name": "GuardianAI RiskRouter",
    "version": "1",
    # chainId and verifyingContract set at runtime
}

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
        self.signature = signature
        self.timestamp = datetime.utcnow().isoformat()
        self.hash_ref = self._compute_hash()

    def _compute_hash(self) -> str:
        payload = json.dumps({
            "trade_id": self.trade_id,
            "agent": self.agent_address,
            "decision": self.decision,
            "risk_score": self.risk_score,
            "timestamp": self.timestamp,
        }, sort_keys=True).encode()
        return "0x" + hashlib.sha256(payload).hexdigest()

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
            "signature":         self.signature,
            "timestamp":         self.timestamp,
            "hash_ref":          self.hash_ref,
        }


# ────────────────────────────────────────────────────────────
#  Database Init
# ────────────────────────────────────────────────────────────

def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_artifacts (
            trade_id         TEXT PRIMARY KEY,
            agent_address    TEXT,
            token_in         TEXT,
            token_out        TEXT,
            amount_in_usd    REAL,
            leverage         REAL,
            risk_score       REAL,
            decision         TEXT,
            violations_json  TEXT,
            var_pct          REAL,
            volatility_pct   REAL,
            position_size_pct REAL,
            circuit_breaker  INTEGER,
            signature        TEXT,
            timestamp        TEXT,
            hash_ref         TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_artifact(artifact: ValidationArtifact):
    conn = sqlite3.connect(DB_PATH)
    d = artifact.to_dict()
    conn.execute("""
        INSERT OR REPLACE INTO trade_artifacts VALUES (
            :trade_id, :agent_address, :token_in, :token_out,
            :amount_in_usd, :leverage, :risk_score, :decision,
            :violations_json, :var_pct, :volatility_pct,
            :position_size_pct, :circuit_breaker, :signature,
            :timestamp, :hash_ref
        )
    """, {
        **d,
        "violations_json": json.dumps(d["violations"]),
        "circuit_breaker": int(d["circuit_breaker"]),
    })
    conn.commit()
    conn.close()


def fetch_recent_artifacts(limit: int = 50) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM trade_artifacts ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    results = []
    for row in rows:
        d = dict(row)
        d["violations"] = json.loads(d.pop("violations_json", "[]"))
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
    ) -> ValidationArtifact:
        """
        Main entry point. Validates a trade intent against all risk rules.
        If approved, creates EIP-712 signed intent. Emits artifact in all cases.
        """
        trade_id = str(uuid.uuid4())
        logger.info(f"Validating trade {trade_id}: {token_in}→{token_out} ${amount_in_usd:.2f} {leverage}x")

        # 1. Run risk assessment
        assessment = self.risk_engine.validate_trade_intent(
            trade_value_usd=amount_in_usd,
            leverage=leverage,
            asset_returns=asset_returns,
        )

        signature = None

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
                )
                logger.info(f"Trade {trade_id} APPROVED and signed")
            except Exception as e:
                logger.error(f"Signing failed for {trade_id}: {e}")

        else:
            rules = [v.rule for v in assessment.violations]
            logger.warning(f"Trade {trade_id} REJECTED — rules: {rules}")

        # 3. Emit validation artifact
        artifact = ValidationArtifact(
            trade_id=trade_id,
            agent_address=self.agent_address,
            token_in=token_in,
            token_out=token_out,
            amount_in_usd=amount_in_usd,
            leverage=leverage,
            risk_score=assessment.risk_score,
            decision=assessment.decision.value,
            violations=[{"rule": v.rule, "detail": v.detail, "severity": v.severity}
                        for v in assessment.violations],
            var_pct=assessment.var_pct,
            volatility_pct=assessment.volatility_pct,
            position_size_pct=assessment.position_size_pct,
            circuit_breaker=assessment.circuit_breaker_active,
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
    ) -> str:
        """Signs an EIP-712 TradeIntent."""
        import time

        # Use risk artifact hash as bytes32
        artifact_hash_hex = hashlib.sha256(
            f"{trade_id}:{risk_score}".encode()
        ).hexdigest()
        artifact_hash_bytes = bytes.fromhex(artifact_hash_hex)

        nonce = self._nonces.get(self.agent_address, 0)
        self._nonces[self.agent_address] = nonce + 1

        domain = {
            **GUARDIAN_DOMAIN,
            "chainId": self.chain_id,
            "verifyingContract": self.router_address,
        }

        message = {
            "agent":            self.agent_address,
            "tokenIn":          token_in,
            "tokenOut":         token_out,
            "amountIn":         int(amount_in_usd * 1_000_000),  # USDC 6 decimals
            "maxSlippageBps":   max_slippage_bps,
            "deadline":         int(time.time()) + 300,  # 5 min
            "riskArtifactHash": artifact_hash_bytes,
            "nonce":            nonce,
        }

        structured_data = {
            "types": TRADE_INTENT_TYPES,
            "domain": domain,
            "primaryType": "TradeIntent",
            "message": message,
        }

        signed = self.account.sign_typed_data(
            domain_data=domain,
            message_types={"TradeIntent": TRADE_INTENT_TYPES["TradeIntent"]},
            message_data=message,
        )
        return signed.signature.hex()
