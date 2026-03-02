"""
main.py — GuardianAI FastAPI Backend
"""

import os
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from services.risk_engine import RiskEngine, RiskConfig
from services.trade_validator import TradeValidator, fetch_recent_artifacts
from services.reputation_engine import ReputationEngine
from services.sandbox_simulator import SandboxSimulator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s — %(message)s")
logger = logging.getLogger("main")

# ─────────────────────────────────────────────────────────────
#  Globals (initialized on startup)
# ─────────────────────────────────────────────────────────────

risk_engine:    RiskEngine       = None
validator:      TradeValidator   = None
reputation:     ReputationEngine = None
simulator:      SandboxSimulator = None

STARTING_CAPITAL = float(os.getenv("STARTING_CAPITAL", "10000"))
AGENT_PRIVATE_KEY = os.getenv(
    "AGENT_PRIVATE_KEY",
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # Hardhat account #0 — DEMO ONLY
)
AGENT_ID   = int(os.getenv("AGENT_ID", "1"))
CHAIN_ID   = int(os.getenv("CHAIN_ID", "31337"))
ROUTER_ADDR = os.getenv("ROUTER_ADDRESS", "0x0000000000000000000000000000000000000001")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global risk_engine, validator, reputation, simulator

    logger.info("Starting GuardianAI...")

    # Build risk config (can be overridden via env)
    config = RiskConfig(
        max_position_pct=float(os.getenv("MAX_POSITION_PCT", "0.10")),
        daily_loss_limit_pct=float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.02")),
        max_drawdown_pct=float(os.getenv("MAX_DRAWDOWN_PCT", "0.15")),
        leverage_cap=float(os.getenv("LEVERAGE_CAP", "3.0")),
        max_volatility_pct=float(os.getenv("MAX_VOL_PCT", "0.05")),
        var_limit_pct=float(os.getenv("VAR_LIMIT_PCT", "0.03")),
    )

    risk_engine = RiskEngine(config)
    risk_engine.initialize(STARTING_CAPITAL)

    validator = TradeValidator(
        risk_engine=risk_engine,
        agent_private_key=AGENT_PRIVATE_KEY,
        chain_id=CHAIN_ID,
        router_address=ROUTER_ADDR,
    )

    reputation = ReputationEngine(
        agent_id=AGENT_ID,
        agent_address=validator.agent_address,
    )

    simulator = SandboxSimulator(starting_capital=STARTING_CAPITAL)

    logger.info(f"GuardianAI agent: {validator.agent_address}")
    logger.info(f"Capital: ${STARTING_CAPITAL:,.2f}")
    yield
    logger.info("GuardianAI shutting down.")


app = FastAPI(
    title="GuardianAI Risk API",
    description="Trustless Risk Enforcement Agent for Autonomous Trading",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────
#  Request / Response Models
# ─────────────────────────────────────────────────────────────

class TradeIntentRequest(BaseModel):
    token_in:        str   = Field(..., example="USDC")
    token_out:       str   = Field(..., example="WETH")
    amount_in_usd:   float = Field(..., gt=0, example=500.0)
    leverage:        float = Field(1.0, ge=1.0, example=1.0)
    max_slippage_bps: int  = Field(50, ge=0, le=1000, example=50)
    asset_returns:   Optional[list[float]] = Field(
        None,
        description="Optional list of recent daily returns for the asset"
    )


class CircuitBreakerAction(BaseModel):
    action: str  # "trip" | "reset"


# ─────────────────────────────────────────────────────────────
#  Endpoints
# ─────────────────────────────────────────────────────────────

@app.post("/trade-intent", tags=["Trading"])
async def submit_trade_intent(req: TradeIntentRequest):
    """
    Submit a trade intent. The risk engine validates it before any signing.
    Returns a validation artifact with decision, risk score, and all metrics.
    """
    artifact = validator.validate_and_process(
        token_in=req.token_in,
        token_out=req.token_out,
        amount_in_usd=req.amount_in_usd,
        leverage=req.leverage,
        max_slippage_bps=req.max_slippage_bps,
        asset_returns=req.asset_returns,
    )

    approved = artifact.decision == "APPROVE"

    # Simulate execution in sandbox
    sim_result = simulator.simulate_trade(
        trade_id=artifact.trade_id,
        token_in=req.token_in,
        token_out=req.token_out,
        amount_usd=req.amount_in_usd,
        approved=approved,
    )

    # Update risk engine capital from simulator
    sim_stats = simulator.get_stats()
    pnl_pct   = sim_result.pnl_pct if sim_result else None
    risk_engine.update_capital(
        sim_stats["current_capital"],
        price_return=pnl_pct,
    )

    # Update reputation
    if approved:
        reputation.record_approved(pnl_pct=pnl_pct)
    else:
        reputation.record_rejected()

    reputation.update_drawdown(sim_stats["drawdown_pct"])

    return {
        "artifact":    artifact.to_dict(),
        "simulation":  sim_result.__dict__ if sim_result else None,
        "capital_now": round(risk_engine.current_capital, 4),
    }


@app.get("/risk-status", tags=["Risk"])
async def get_risk_status():
    """Returns current risk engine state and all computed metrics."""
    status = risk_engine.get_status()
    sim    = simulator.get_stats()
    return {
        "risk_engine": status,
        "sandbox":     sim,
        "config": {
            "max_position_pct":      risk_engine.config.max_position_pct,
            "daily_loss_limit_pct":  risk_engine.config.daily_loss_limit_pct,
            "max_drawdown_pct":      risk_engine.config.max_drawdown_pct,
            "leverage_cap":          risk_engine.config.leverage_cap,
            "max_volatility_pct":    risk_engine.config.max_volatility_pct,
            "var_limit_pct":         risk_engine.config.var_limit_pct,
        }
    }


@app.get("/reputation", tags=["Reputation"])
async def get_reputation():
    """Returns agent reputation metrics and grade."""
    return reputation.to_dict()


@app.get("/logs", tags=["Logs"])
async def get_logs(limit: int = 20):
    """Returns recent trade validation artifacts (approved + rejected)."""
    artifacts = fetch_recent_artifacts(limit=limit)
    approved  = sum(1 for a in artifacts if a["decision"] == "APPROVE")
    rejected  = sum(1 for a in artifacts if a["decision"] == "REJECT")
    return {
        "total":    len(artifacts),
        "approved": approved,
        "rejected": rejected,
        "artifacts": artifacts,
    }


@app.post("/circuit-breaker", tags=["Risk"])
async def control_circuit_breaker(action: CircuitBreakerAction):
    """Manually trip or reset the circuit breaker."""
    if action.action == "trip":
        risk_engine.circuit_breaker_active = True
        return {"status": "TRIPPED", "message": "Circuit breaker activated. All trading halted."}
    elif action.action == "reset":
        risk_engine.reset_circuit_breaker()
        return {"status": "RESET", "message": "Circuit breaker reset. Trading resumed."}
    else:
        raise HTTPException(status_code=400, detail="action must be 'trip' or 'reset'")


@app.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "agent": validator.agent_address, "chain_id": CHAIN_ID}
