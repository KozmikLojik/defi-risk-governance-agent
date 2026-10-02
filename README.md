# GuardianAI

GuardianAI is a risk-gated trading prototype with a FastAPI API, single-page dashboard, EIP-712 signed trade intents, a Solidity `RiskRouter`, and paper-trading/backtesting tools.

## Local run

Windows Command Prompt:

```bat
cd /d C:\Users\pritb\Desktop\guardian-ai
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements-dev.txt
set AGENT_MODE=MANUAL
set AUTH_REQUIRED=false
py -m uvicorn main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

In a second Command Prompt:

```bat
cd /d C:\Users\pritb\Desktop\guardian-ai
py -m http.server 3000 --directory frontend
```

Dashboard: <http://localhost:3000/dashboard.html> · API docs: <http://127.0.0.1:8000/docs>

For a persistent PostgreSQL-backed local stack, use `docker compose up --build`. Read [DEPLOYMENT.md](./DEPLOYMENT.md) before enabling auth or testnet transactions.

## Included

- Risk checks for position sizing, daily loss, drawdown, leverage, volatility, VaR, and circuit-breaker state.
- Persisted validation artifacts and runtime checkpoints; SQLite locally or PostgreSQL through `DATABASE_URL`.
- Role-based API keys for trader, operator, and admin actions.
- Kraken ticker/candle status with offline and stale-data indicators.
- Deterministic historical-candle paper scenarios and an offline next-candle backtest endpoint.
- Expanding-window walk-forward backtests that report contiguous out-of-sample folds.
- Opt-in, allowlisted testnet intent submission through the Solidity router. Mainnet submission is refused; the router records an intent and does not swap tokens.
- SHA-256 API-key digests with rotation, Redis-backed production rate limiting, and mounted wallet-secret files.
- Versioned Alembic migrations, including safe upgrades from the earlier SQLite schema.
- JSON request logs, request IDs, and Prometheus metrics at `/metrics`.
- Pytest, Hardhat integration tests, Docker build, and CI workflow.

See [DEPLOYMENT.md](./DEPLOYMENT.md) for environment variables, testnet setup, API-key roles, checks, and risk-model limitations. See [contract security review status](./contracts/SECURITY_REVIEW.md) before any execution feature is considered.
