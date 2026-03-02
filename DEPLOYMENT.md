# GuardianAI — Deployment Guide & Demo Script

## Prerequisites
- Python 3.11+
- Node.js 18+
- Git

---

## 1. Clone & Setup

```bash
git clone https://github.com/YOUR_ORG/guardian-ai
cd guardian-ai
```

---

## 2. Smart Contracts (Hardhat)

```bash
cd contracts
npm init -y
npm install --save-dev hardhat @nomicfoundation/hardhat-toolbox @openzeppelin/contracts dotenv

# Compile
npx hardhat compile

# Deploy local (Hardhat node)
npx hardhat node           # Terminal 1 — keep running
npx hardhat run scripts/deploy.js --network hardhat  # Terminal 2

# Deploy Sepolia testnet
cp .env.example .env
# Fill in SEPOLIA_RPC_URL, PRIVATE_KEY, ETHERSCAN_API_KEY
npx hardhat run scripts/deploy.js --network sepolia

# Verify contract (optional)
npx hardhat verify --network sepolia <CONTRACT_ADDRESS>
```

**deployed-addresses.json** is auto-generated after deploy. Copy it to `backend/`.

---

## 3. Backend (FastAPI)

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure .env
cat > .env << 'EOF'
STARTING_CAPITAL=10000
AGENT_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
AGENT_ID=1
CHAIN_ID=31337
ROUTER_ADDRESS=0x...   # from deployed-addresses.json
MAX_POSITION_PCT=0.10
DAILY_LOSS_LIMIT_PCT=0.02
MAX_DRAWDOWN_PCT=0.15
LEVERAGE_CAP=3.0
MAX_VOL_PCT=0.05
VAR_LIMIT_PCT=0.03
EOF

# Run
uvicorn main:app --reload --port 8000
```

**API Docs:** http://localhost:8000/docs

---

## 4. Frontend Dashboard

```bash
# Simple: just open the HTML file
open frontend/dashboard.html

# Or serve it
cd frontend && python -m http.server 3000
# Visit http://localhost:3000/dashboard.html
```

---

## 5. Quick Test via cURL

```bash
# Submit safe trade
curl -X POST http://localhost:8000/trade-intent \
  -H "Content-Type: application/json" \
  -d '{"token_in":"USDC","token_out":"WETH","amount_in_usd":500,"leverage":1.0}'

# Submit risky trade (too large)
curl -X POST http://localhost:8000/trade-intent \
  -H "Content-Type: application/json" \
  -d '{"token_in":"USDC","token_out":"WETH","amount_in_usd":9999,"leverage":5.0}'

# Get risk status
curl http://localhost:8000/risk-status

# Get reputation
curl http://localhost:8000/reputation

# Get validation logs
curl http://localhost:8000/logs

# Trip circuit breaker
curl -X POST http://localhost:8000/circuit-breaker \
  -H "Content-Type: application/json" \
  -d '{"action":"trip"}'
```

---

## 6. Testnet Deployment (Base Sepolia)

```bash
cd contracts
npx hardhat run scripts/deploy.js --network base-sepolia
# Update backend .env with new addresses and CHAIN_ID=84532
```

---

## 2-Minute Demo Script

```
[0:00] INTRO
"GuardianAI is an AI-powered risk governance agent built on ERC-8004.
 It enforces capital protection rules BEFORE any trade executes.
 Every decision is transparent, cryptographically signed, and auditable."

[0:20] SHOW DASHBOARD
- Point to capital: $10,000 stablecoin sandbox
- Point to reputation grade: AAA (no trades yet)
- Explain the 7 risk rules visible in the panel

[0:35] SUBMIT A SAFE TRADE
- Enter: USDC→WETH, $500, 1x leverage
- Click VALIDATE & SUBMIT INTENT
- Show: risk score ~15, APPROVE badge, EIP-712 signature
- Capital updates, reputation records safe trade

[0:55] SUBMIT A RISKY TRADE
- Enter: USDC→WETH, $9,500, 5x leverage (HIGH VOL preset)
- Show: risk score ~85, REJECT badge
- Show violations panel: MAX_POSITION_SIZE + LEVERAGE_CAP + VOLATILITY_FILTER
- "The agent blocked this trade. No funds at risk."

[1:15] TRIP CIRCUIT BREAKER
- Click TRIP BREAKER
- Show red banner: ALL TRADING HALTED
- Try to submit trade → blocked
- Show circuit breaker in rules panel

[1:30] SHOW ON-CHAIN ARTIFACTS
- Show hash_ref in validation artifact
- "This hash is submitted to our RiskRouter contract on Sepolia"
- "The entire audit trail is verifiable on-chain"

[1:45] SHOW REPUTATION
- Grade, Sharpe ratio, drawdown %, safe vs rejected count
- "ERC-8004 reputation registry — tamper-proof trust score"

[1:55] CLOSE
"GuardianAI: capital preservation, discipline, and verifiable trust.
 Not a trading bot. A risk guardian."
```

---

## Folder Structure

```
guardian-ai/
├── contracts/
│   ├── GuardianAI.sol          # ERC-8004 Identity, Reputation, RiskRouter
│   ├── hardhat.config.js
│   ├── scripts/deploy.js
│   └── deployed-addresses.json
├── backend/
│   ├── main.py                 # FastAPI app
│   ├── requirements.txt
│   └── services/
│       ├── risk_engine.py      # All risk algorithms
│       ├── trade_validator.py  # EIP-712 + artifact emission
│       ├── reputation_engine.py
│       └── sandbox_simulator.py
├── frontend/
│   └── dashboard.html          # Single-file React-free dashboard
├── docs/
│   └── architecture.svg
└── DEPLOYMENT.md
```
