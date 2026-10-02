"""
main.py — GuardianAI FastAPI Backend
"""

import os
import logging
import asyncio
import secrets
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, confloat
from eth_account import Account

from services.risk_engine import RiskEngine, RiskConfig
from services.trade_validator import (TradeValidator, fetch_recent_artifacts, load_runtime_state,
                                      save_runtime_state, update_artifact_chain_status)
from services.reputation_engine import ReputationEngine
from services.sandbox_simulator import SandboxSimulator
from services.kraken_service import place_order
from services.market_data import get_market_snapshot
from services.auth import require_role, api_key_identity
from services.observability import (configure_logging, request_id_var, record_request,
                                    render_metrics, start_timer)
from services.onchain import get_agent_nonce, submit_signed_intent
from services.backtest import run_backtest, run_walk_forward_backtest
from services.secrets import get_secret
from services.rate_limiter import RateLimiter, create_rate_limiter

configure_logging()
logger = logging.getLogger("main")

# ─────────────────────────────────────────────
# Globals
# ─────────────────────────────────────────────

risk_engine = None
validator = None
reputation = None
simulator = None
rate_limiter: RateLimiter | None = None

STARTING_CAPITAL = float(os.getenv("STARTING_CAPITAL", "10000"))
_PRODUCTION = bool(os.getenv("VERCEL") or os.getenv("ENVIRONMENT", "").lower() == "production")
AGENT_PRIVATE_KEY = get_secret("AGENT_PRIVATE_KEY", required=_PRODUCTION)
if AGENT_PRIVATE_KEY:
    try:
        Account.from_key(AGENT_PRIVATE_KEY)
    except ValueError:
        logger.warning("AGENT_PRIVATE_KEY is invalid; using an ephemeral development key")
        AGENT_PRIVATE_KEY = None
if os.getenv("VERCEL") and not AGENT_PRIVATE_KEY:
    raise RuntimeError("Configure AGENT_PRIVATE_KEY_FILE or explicitly allow encrypted platform wallet-key environment variables")
if _PRODUCTION and not AGENT_PRIVATE_KEY:
    raise RuntimeError("Production requires a stable AGENT_PRIVATE_KEY")
AGENT_PRIVATE_KEY = AGENT_PRIVATE_KEY or ("0x" + secrets.token_hex(32))
AGENT_ID = int(os.getenv("AGENT_ID", "1"))
CHAIN_ID = int(os.getenv("CHAIN_ID", "31337"))
ROUTER_ADDR = os.getenv("ROUTER_ADDRESS", "0x0000000000000000000000000000000000000001")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global risk_engine, validator, reputation, simulator, AGENT_MODE, last_evaluated_candle, rate_limiter

    rate_limiter = await create_rate_limiter()

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
    saved_state = load_runtime_state()
    risk_engine.restore_state(saved_state.get("risk", {}))
    simulator.restore_state(saved_state.get("simulator", {}))
    reputation.restore_state(saved_state.get("reputation", {}))
    if saved_state.get("agent_mode") in {"AUTO", "MANUAL"}:
        AGENT_MODE = saved_state["agent_mode"]
    last_evaluated_candle = saved_state.get("last_evaluated_candle")

    task = asyncio.create_task(agent_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if rate_limiter and rate_limiter.redis:
            await rate_limiter.redis.aclose()


app = FastAPI(lifespan=lifespan)


@app.exception_handler(Exception)
async def handle_unexpected_error(request, exc):
    logger.exception("unhandled_request_error path=%s", request.url.path)
    return JSONResponse(status_code=500, content={
        "detail": "Unexpected server error",
        "request_id": request_id_var.get(),
    })

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", "*").split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def strip_vercel_api_prefix(request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))[:128]
    token = request_id_var.set(request_id)
    started = start_timer()
    # Vercel serves this app beneath /api; locally it is mounted at /.
    path = request.scope["path"]
    if path == "/api" or path.startswith("/api/"):
        request.scope["path"] = path[4:] or "/"
        request.scope["raw_path"] = request.scope["path"].encode()
    try:
        path = request.scope["path"]
        is_write = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        bucket = "write" if is_write else "read"
        env_name = "RATE_LIMIT_WRITES_PER_MINUTE" if is_write else "RATE_LIMIT_READS_PER_MINUTE"
        limit = int(os.getenv(env_name, "12" if is_write else "120"))
        identity = api_key_identity(request.headers.get("X-API-Key"))
        if not identity:
            client_host = request.client.host if request.client else "unknown"
            if os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true":
                identity = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip() or client_host
            else:
                identity = client_host
        try:
            allowed, retry_after = (await rate_limiter.check(identity, bucket, limit)
                                    if rate_limiter else (True, 0))
        except Exception:
            logger.exception("rate_limit_store_error path=%s", path)
            response = JSONResponse(status_code=503, content={
                "detail": "Request limit service is temporarily unavailable",
                "request_id": request_id,
            }, headers={"Retry-After": "5"})
        else:
            if allowed:
                response = await call_next(request)
            else:
                response = JSONResponse(status_code=429, content={
                    "detail": "Request limit reached; retry after the current one-minute window",
                    "request_id": request_id,
                }, headers={"Retry-After": str(retry_after)})
        response.headers["X-Request-ID"] = request_id
        route = getattr(request.scope.get("route"), "path", request.url.path)
        elapsed = start_timer() - started
        record_request(request.method, route, response.status_code, elapsed)
        logger.info("http_request method=%s route=%s status=%s duration_ms=%.1f",
                    request.method, route, response.status_code, elapsed * 1000)
        return response
    finally:
        request_id_var.reset(token)

# Autonomous agent settings
AGENT_MODE = os.getenv("AGENT_MODE", "MANUAL" if os.getenv("VERCEL") else "AUTO").upper()
AGENT_INTERVAL_SEC = int(os.getenv("AGENT_INTERVAL_SEC", "15"))
btc_price_history: list[float] = []
last_evaluated_candle: Optional[int] = None

# ─────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────

class TradeIntentRequest(BaseModel):
    token_in: str = Field(min_length=1, max_length=42, pattern=r"^[A-Za-z0-9._-]+$")
    token_out: str = Field(min_length=1, max_length=42, pattern=r"^[A-Za-z0-9._-]+$")
    amount_in_usd: float = Field(gt=0, le=1_000_000_000, allow_inf_nan=False)
    leverage: float = Field(default=1.0, ge=1.0, le=100, allow_inf_nan=False)
    max_slippage_bps: int = Field(default=50, ge=0, le=1000)
    asset_returns: Optional[List[confloat(allow_inf_nan=False, ge=-0.99, le=10)]] = Field(default=None, max_length=500)
    asset_prices: Optional[List[confloat(gt=0, allow_inf_nan=False, le=1_000_000_000)]] = Field(default=None, max_length=500)
    submit_onchain: bool = False


class BacktestRequest(BaseModel):
    prices: List[confloat(gt=0, allow_inf_nan=False, le=1_000_000_000)] = Field(min_length=22, max_length=5000)
    starting_capital: float = Field(default=10_000, gt=0, le=1_000_000_000, allow_inf_nan=False)
    fee_bps: float = Field(default=20, ge=0, le=1000, allow_inf_nan=False)
    folds: int = Field(default=5, ge=2, le=10)


class CircuitBreakerAction(BaseModel):
    action: str = Field(min_length=1, max_length=10)


class AgentModeRequest(BaseModel):
    mode: str = Field(min_length=1, max_length=10)


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@app.get("/price")
async def get_price():
    snapshot = await asyncio.to_thread(get_market_snapshot, "BTC")
    return {"btc_price": snapshot["price"] or 0, **snapshot}


@app.get("/market-status/{symbol}")
async def market_status(symbol: str):
    snapshot = await asyncio.to_thread(get_market_snapshot, symbol)
    if not snapshot["available"] or snapshot["stale"]:
        snapshot["status"] = "stale" if snapshot["stale"] else "offline"
    elif not snapshot["history_available"]:
        snapshot["status"] = "history_unavailable"
    else:
        snapshot["status"] = "live"
    return snapshot


@app.post("/backtest")
async def backtest(request: BacktestRequest):
    try:
        return run_backtest([float(price) for price in request.prices],
                            request.starting_capital, request.fee_bps)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/walk-forward-backtest")
async def walk_forward_backtest(request: BacktestRequest):
    try:
        return run_walk_forward_backtest(
            [float(price) for price in request.prices], request.starting_capital,
            request.fee_bps, request.folds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/execute-trade")
async def execute_trade(role: str = Depends(require_role("trader"))):
    market = await asyncio.to_thread(get_market_snapshot, "BTC")
    price = market["price"] or 0
    if price <= 0 or market["stale"] or not market["available"] or not market["history_available"]:
        raise HTTPException(status_code=503, detail="BTC price feed is unavailable or stale")
    amount = _determine_position_size()
    artifact = validator.validate_and_process("USDC", "BTC", amount, 1.0, 50,
                                               asset_returns=market.get("historical_returns"),
                                               asset_prices=market.get("historical_closes"))
    result = simulator.simulate_trade(artifact.trade_id, "USDC", "BTC", amount,
                                      artifact.decision == "APPROVE", reference_price=price,
                                      historical_return=market.get("completed_candle_return"),
                                      market_data_source="Kraken completed 1-minute candle scenario")
    stats = simulator.get_stats()
    if result:
        risk_engine.update_capital(stats["current_capital"], result.pnl_pct)
        reputation.record_approved(pnl_pct=result.pnl_pct)
    else:
        reputation.record_rejected()
    reputation.update_drawdown(stats["drawdown_pct"])
    _persist_runtime_state()
    return {
        "price": price,
        "decision": artifact.decision,
        "artifact": artifact.to_dict(),
        "simulation": result.__dict__ if result else None,
        "status": "simulated",
    }


@app.post("/real-trade")
async def real_trade(role: str = Depends(require_role("admin"))):
    # Kraken is deliberately called in validate-only mode (validate=True).
    # This endpoint never submits a live order.
    result = place_order()
    return result


@app.get("/health")
async def health():
    return {"status": "ok", "agent": validator.agent_address, "chain_id": CHAIN_ID}


@app.get("/metrics", include_in_schema=False)
async def metrics():
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


@app.get("/risk-status")
async def get_risk_status():
    return {
        "risk_engine": risk_engine.get_status(),
        "sandbox": simulator.get_stats(),
        "config": {
            "max_position_pct": risk_engine.config.max_position_pct,
            "daily_loss_limit_pct": risk_engine.config.daily_loss_limit_pct,
            "max_drawdown_pct": risk_engine.config.max_drawdown_pct,
            "leverage_cap": risk_engine.config.leverage_cap,
            "max_volatility_pct": risk_engine.config.max_volatility_pct,
            "var_limit_pct": risk_engine.config.var_limit_pct,
        }
    }


@app.get("/reputation")
async def get_reputation():
    return reputation.to_dict()


@app.get("/logs")
async def get_logs(limit: int = Query(default=50, ge=1, le=100)):
    artifacts = fetch_recent_artifacts(limit=limit)
    return {
        "total": len(artifacts),
        "approved": sum(a["decision"] == "APPROVE" for a in artifacts),
        "rejected": sum(a["decision"] == "REJECT" for a in artifacts),
        "artifacts": artifacts,
    }


@app.post("/trade-intent")
async def submit_trade_intent(req: TradeIntentRequest, role: str = Depends(require_role("trader"))):
    market = await asyncio.to_thread(get_market_snapshot, req.token_out)
    if market["stale"] or not market["available"] or not market["history_available"]:
        raise HTTPException(status_code=503, detail="Market price/candle data is unavailable or stale; trade was not simulated")
    historical_returns = market.get("historical_returns") or []
    intent_nonce = None
    if req.submit_onchain:
        try:
            intent_nonce = await asyncio.to_thread(get_agent_nonce, validator.agent_address)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Testnet router unavailable: {exc}") from exc
    artifact = validator.validate_and_process(
        token_in=req.token_in,
        token_out=req.token_out,
        amount_in_usd=req.amount_in_usd,
        leverage=req.leverage,
        max_slippage_bps=req.max_slippage_bps,
        asset_returns=historical_returns,
        asset_prices=market.get("historical_closes") or req.asset_prices,
        intent_nonce=intent_nonce,
    )

    approved = artifact.decision == "APPROVE"

    sim_result = simulator.simulate_trade(
        trade_id=artifact.trade_id,
        token_in=req.token_in,
        token_out=req.token_out,
        amount_usd=req.amount_in_usd,
        approved=approved,
        reference_price=market["price"],
        historical_return=market.get("completed_candle_return", 0.0),
        market_data_source="Kraken completed 1-minute candle scenario",
        leverage=req.leverage,
    )

    sim_stats = simulator.get_stats()

    pnl_pct = sim_result.pnl_pct if sim_result else None

    risk_engine.update_capital(
        sim_stats["current_capital"],
        price_return=pnl_pct,
    )

    if approved:
        reputation.record_approved(pnl_pct=pnl_pct)
    else:
        reputation.record_rejected()

    reputation.update_drawdown(sim_stats["drawdown_pct"])
    _persist_runtime_state()

    onchain_result = {"status": "not_requested"}
    if req.submit_onchain and approved:
        try:
            onchain_result = await asyncio.to_thread(
                submit_signed_intent, artifact.intent, artifact.signature
            )
            onchain_result["status"] = "submitted"
            update_artifact_chain_status(artifact.trade_id, "submitted", onchain_result["transaction_hash"])
        except Exception as exc:
            logger.exception("Testnet submission failed for trade %s", artifact.trade_id)
            onchain_result = {"status": "failed", "error": str(exc)}
            update_artifact_chain_status(artifact.trade_id, "failed")
    elif req.submit_onchain:
        onchain_result = {"status": "risk_rejected"}
        update_artifact_chain_status(artifact.trade_id, "risk_rejected")

    return {
        "artifact": artifact.to_dict(),
        "simulation": sim_result.__dict__ if sim_result else None,
        "capital_now": round(risk_engine.current_capital, 4),
        "onchain": onchain_result,
    }


def _determine_position_size() -> float:
    return max(0.0, min(risk_engine.current_capital * 0.02,
                        risk_engine.current_capital * risk_engine.config.max_position_pct))


def _build_signal_payload(prices: list[float], returns: list[float]) -> dict:
    return {
        "momentum": risk_engine._momentum_signal(prices, returns),
        "rsi": round(risk_engine._rsi(returns), 2),
        "volatility": round(risk_engine._rolling_volatility(returns), 6),
        "price_slope": round(risk_engine._price_slope(prices), 6),
    }


async def evaluate_market_cycle():
    global btc_price_history, last_evaluated_candle
    market = await asyncio.to_thread(get_market_snapshot, "BTC")
    price = market["price"] or 0
    if price <= 0 or market["stale"] or not market["available"] or not market["history_available"]:
        logger.warning("Agent market eval: failed to fetch BTC price")
        return

    btc_price_history = market.get("historical_closes", [])[-100:]
    returns = market.get("historical_returns", [])[-99:]
    candle_at = market.get("completed_candle_at")
    is_new_candle = candle_at is not None and candle_at != last_evaluated_candle
    last_evaluated_candle = candle_at

    signal = _build_signal_payload(btc_price_history, returns)

    # Auto decision logic
    if AGENT_MODE == "AUTO" and is_new_candle and signal["momentum"] == "BUY" and not risk_engine.circuit_breaker_active:
        amount = _determine_position_size()

        artifact = validator.validate_and_process(
            token_in="USDC",
            token_out="BTC",
            amount_in_usd=amount,
            leverage=1.0,
            max_slippage_bps=50,
            asset_returns=returns,
            asset_prices=btc_price_history,
        )

        approved = artifact.decision == "APPROVE"
        sim_result = simulator.simulate_trade(
            trade_id=artifact.trade_id,
            token_in="USDC",
            token_out="BTC",
            amount_usd=amount,
            approved=approved,
            reference_price=price,
            historical_return=market.get("completed_candle_return"),
            market_data_source="Kraken completed 1-minute candle scenario",
        )

        sim_stats = simulator.get_stats()
        pnl_pct = sim_result.pnl_pct if sim_result else None

        risk_engine.update_capital(sim_stats["current_capital"], price_return=pnl_pct)

        if approved:
            reputation.record_approved(pnl_pct=pnl_pct)
        else:
            reputation.record_rejected()

        reputation.update_drawdown(sim_stats["drawdown_pct"])
        _persist_runtime_state()

        logger.info(f"Agent AUTO trade {artifact.trade_id} decision={artifact.decision} amount={amount}")

    return {
        "mode": AGENT_MODE,
        "btc_price": price,
        "signal": signal,
        "capital": risk_engine.current_capital,
    }


async def agent_loop():
    while True:
        if AGENT_MODE == "AUTO":
            try:
                await evaluate_market_cycle()
            except Exception:
                logger.exception("Agent loop error")
        await asyncio.sleep(max(1, AGENT_INTERVAL_SEC))


def _persist_runtime_state() -> None:
    if all((risk_engine, simulator, reputation)):
        save_runtime_state({
            "risk": risk_engine.export_state(),
            "simulator": simulator.export_state(),
            "reputation": reputation.export_state(),
            "agent_mode": AGENT_MODE,
            "last_evaluated_candle": last_evaluated_candle,
        })


@app.post("/agent-mode")
async def set_agent_mode(request: AgentModeRequest, role: str = Depends(require_role("operator"))):
    global AGENT_MODE
    mode = request.mode.upper()
    if mode not in ["AUTO", "MANUAL"]:
        raise HTTPException(status_code=400, detail="Invalid mode")
    AGENT_MODE = mode
    _persist_runtime_state()
    return {"agent_mode": AGENT_MODE}


@app.post("/circuit-breaker")
async def control_circuit_breaker(request: CircuitBreakerAction, role: str = Depends(require_role("operator"))):
    action = request.action.strip().lower()
    if action == "trip":
        risk_engine.circuit_breaker_active = True
        _persist_runtime_state()
        return {"status": "TRIPPED", "message": "All trading halted."}
    if action == "reset":
        risk_engine.reset_circuit_breaker()
        _persist_runtime_state()
        return {"status": "RESET", "message": "Circuit breaker reset."}
    raise HTTPException(status_code=400, detail="action must be 'trip' or 'reset'")


@app.post("/agent-cycle")
async def run_agent_cycle(role: str = Depends(require_role("trader"))):
    result = await evaluate_market_cycle()
    if result is None:
        raise HTTPException(status_code=503, detail="Market data is unavailable")
    return result


@app.get("/agent-status")
async def get_agent_status():
    return {
        "mode": AGENT_MODE,
        "interval_sec": AGENT_INTERVAL_SEC,
        "current_capital": risk_engine.current_capital,
        "circuit_breaker": risk_engine.circuit_breaker_active,
        "price_history_len": len(btc_price_history),
        "market": await asyncio.to_thread(get_market_snapshot, "BTC"),
    }
