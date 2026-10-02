# GuardianAI Deployment and Operations

## Local development (Windows Command Prompt)

Install backend dependencies and run the API in one Command Prompt:

```bat
cd /d C:\Users\pritb\Desktop\guardian-ai
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements-dev.txt
set AGENT_MODE=MANUAL
set AUTH_REQUIRED=false
py -m uvicorn main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

Run the dashboard in a second Command Prompt:

```bat
cd /d C:\Users\pritb\Desktop\guardian-ai
py -m http.server 3000 --directory frontend
```

Open <http://localhost:3000/dashboard.html>. API documentation is at <http://127.0.0.1:8000/docs> and health is at <http://127.0.0.1:8000/health>.

To stop either process, press Ctrl+C. Local SQLite data is stored under `backend/data` unless `DATABASE_URL` is set.

## Persistent database and authentication

For a persistent deployment, configure `DATABASE_URL` with a PostgreSQL connection string. Alembic owns versioned migrations; app startup applies pending revisions under a PostgreSQL advisory lock. You can review/apply them separately with `alembic upgrade head`. Runtime capital, breaker, reputation, and simulated trades are checkpointed with validation artifacts. Production/Vercel startup fails unless `DATABASE_URL` is configured, so it cannot silently use ephemeral SQLite.

Production must set `AUTH_REQUIRED=true`, `REDIS_URL`, and `AUTH_API_KEYS`. API keys should contain at least 32 random characters; production stores only SHA-256 digests and permits multiple digests per role during rotation. To create a key and digest, run these commands from Command Prompt:

```bat
cd /d C:\Users\pritb\Desktop\guardian-ai\backend
py -c "import secrets; print(secrets.token_urlsafe(48))"
py -m services.auth hash-key
```

Keep the generated raw key secure for its client, and store only the printed digest in the deployment secret. Configure `AUTH_API_KEYS` as JSON, for example `{"admin":"sha256$<64-hex-digest>","operator":"sha256$<64-hex-digest>","trader":["sha256$<current-digest>","sha256$<next-digest>"]}`. Add a second digest to rotate, deploy, update clients, then remove the old digest. `trader` can submit simulated or enabled testnet intents. `operator` can also change agent mode and operate the circuit breaker. `admin` can do both and call the Kraken validation-only endpoint. Public read endpoints include health, market status, logs, and Prometheus metrics. Never put API keys in source control. The dashboard's **API KEY** button stores the raw key in the current browser session.

Use `AGENT_PRIVATE_KEY_FILE` and `TESTNET_RELAYER_PRIVATE_KEY_FILE` to point to mounted secret files. On platforms that only provide encrypted environment secrets, explicitly set `ALLOW_ENV_WALLET_KEYS=true` and keep the wallet keys in that provider's encrypted secret store. Do not commit `.env` files. Read traffic defaults to 120 requests/minute per key or client; write traffic defaults to 12/minute. Set `RATE_LIMIT_READS_PER_MINUTE` and `RATE_LIMIT_WRITES_PER_MINUTE` to tune them. Production fails closed if Redis is not configured or reachable. Set `TRUST_PROXY_HEADERS=true` only behind a trusted proxy that overwrites `X-Forwarded-For`.

Start PostgreSQL, Redis, and the API locally with Docker Compose using `docker compose up --build`. The compose API keys, Redis password, and database password are development examples; replace them before exposing the service.

## Market data and paper trading

Market tickers and one-minute candles are read from Kraken for BTC/WBTC, ETH/WETH, LINK, and UNI. The dashboard marks the feed offline or stale; trade validation and paper simulation stop when current or completed-candle data is unavailable. Paper PnL uses the latest completed candle as a historical scenario with a fixed 10 bps per-side fee estimate. It is deterministic and is not a forecast or a live fill.

Run an offline historical backtest by posting at least 22 chronological closing prices to `POST /backtest`:

```bat
curl -X POST http://127.0.0.1:8000/backtest -H "Content-Type: application/json" -d "{\"prices\":[100,101,102,103,104,105,106,107,108,109,110,111,112,113,114,115,116,117,118,119,120,121]}"
```

The strategy computes signals using data through candle *n* and executes on candle *n+1*. The response includes a buy-and-hold benchmark and model limitations. `POST /walk-forward-backtest` accepts the same data plus an optional `folds` count (2 to 10); it expands the historical training window and scores contiguous unseen windows, carrying simulated capital forward. This prototype uses a fixed strategy rather than fitting parameters, and it is not evidence of live performance.

## Testnet contract submission

Contract submission is **off by default** and the backend refuses mainnet chain ID 1. First deploy contracts to a supported testnet and register the agent wallet. Keep `AGENT_PRIVATE_KEY` (intent signer), `DEPLOYER_PRIVATE_KEY` (contract deployer), and `TESTNET_RELAYER_PRIVATE_KEY` (gas payer) in protected secret files or an encrypted secret store. The agent must be registered in the deployed `AgentIdentityRegistry`.

Configure `CHAIN_ID`, `ROUTER_ADDRESS`, `ONCHAIN_RPC_URL`, `TESTNET_SUBMISSIONS_ENABLED=true`, `TESTNET_RELAYER_PRIVATE_KEY`, and `TOKEN_METADATA_JSON`. Example metadata shape:

```json
{"11155111":{"USDC":{"address":"0x...","decimals":6,"usd_stable":true},"WETH":{"address":"0x...","decimals":18,"usd_stable":false}}}
```

Addresses must belong to the configured chain, and token-in must be configured as a USD stablecoin because trade amounts are specified in USD. The service reads the router nonce, signs matching EIP-712 data, submits via the relayer, waits for confirmation, and records the transaction hash. `RiskRouter` validates the agent registration, chain, expiry, nonce, signature, and circuit breaker. It records intents; **it does not execute token swaps**.

Deploy on Sepolia (or Base Sepolia) from the repository root after configuring the matching RPC and the deployer key (environment value or `_FILE` path):

```bat
npm ci
npm run deploy:sepolia
```

Use `npm run deploy:base-sepolia` for Base Sepolia. The deploy script refuses other chain IDs, registers the agent, and writes chain-specific addresses under `contracts/deployed-addresses-<chainId>.json`. Never test with a wallet holding funds.

The test deployment includes two worthless, clearly named `SmokeToken` contracts and stores their addresses in the deployment record. Optionally override them with `TESTNET_SMOKE_TOKEN_IN` and `TESTNET_SMOKE_TOKEN_OUT`. With the same RPC/deployed addresses and agent/relayer keys configured, first run a read-only preflight:

```bat
npm run smoke:sepolia
```

This checks chain ID, deployed bytecode, agent registration, router wiring, and EIP-712 signature validity via `eth_call`; it sends no transaction. Only after that passes, opt in to the gas-costing smoke intent:

```bat
set SMOKE_SUBMIT=true
npm run smoke:sepolia
set SMOKE_SUBMIT=
```

The script requires you to type `SUBMIT 11155111` at the prompt. For Base Sepolia, use `npm run smoke:base-sepolia` and type `SUBMIT 84532`. It records a one-base-unit intent; the router does not move the tokens. A deployment and smoke have **not** been run against a public testnet from this workspace because no RPC or wallet secrets were provided.

To submit an approved intent, send `{"submit_onchain":true}` with the normal `POST /trade-intent` body and a trader/admin API key. Do this only after deploying, registering the agent, configuring real testnet token metadata, and funding the relayer with testnet gas.

## Checks and CI

```bat
py -m pip install -r requirements-dev.txt
py -m pytest -q
npm ci
npm run contracts:check
```

GitHub Actions runs the Python suite, validates Vercel JSON, compiles/tests contracts, and builds the Docker image on pushes and pull requests. It does not deploy automatically.

## Model limitations

GuardianAI is a prototype risk gate, not a financial adviser or production execution system. The risk engine uses simplified historical volatility/VaR and a short moving-average signal; it does not model correlated portfolios, liquidity, slippage curves, funding, oracle manipulation, or all exchange failure modes. Paper PnL is a one-candle historical scenario. Backtests can overfit, omit survivorship and execution bias, and do not establish future performance. Risk controls and on-chain intent checks require independent security review before any real funds or live trading are considered.
