# GuardianAI 🛡️
### Trustless Risk Enforcement Agent for Autonomous Trading

> Built on ERC-8004 · EIP-712 · FastAPI · Solidity

---

## What Is It?

GuardianAI is an AI-powered risk governance agent that sits **between trade intent and execution**.

It does **NOT** try to trade aggressively. It enforces capital protection rules using:
- **ERC-8004** identity and reputation registries
- **EIP-712** cryptographic signing of approved intents only
- **7 risk rules** evaluated before every trade: position size, daily loss, drawdown, volatility, VaR, leverage, circuit breaker
- **Validation artifacts** emitted for every decision — transparent and auditable

## Architecture

```
Trade Intent → Risk Engine (7 rules) → APPROVE/REJECT
                    ↓                        ↓
               EIP-712 Sign           Log Artifact
                    ↓                        ↓
              Risk Router            Reputation Engine
              (on-chain)            (ERC-8004 registry)
```

## Risk Algorithms
| Algorithm | Use |
|---|---|
| Fixed Fractional Position Sizing | Max position per trade |
| Historical VaR (95%, parametric) | Tail risk limit |
| Rolling Std Dev | Volatility filter |
| Peak-to-Trough | Max drawdown tracking |
| Annualised Sharpe | Reputation scoring |

## Quick Start

```bash
# Backend
cd backend && pip install -r requirements.txt
uvicorn main:app --reload

# Frontend
open frontend/dashboard.html
```

See [DEPLOYMENT.md](./DEPLOYMENT.md) for full setup including contracts.

## API
| Endpoint | Method | Description |
|---|---|---|
| `/trade-intent` | POST | Submit and validate a trade |
| `/risk-status` | GET | Current risk engine state |
| `/reputation` | GET | Agent reputation metrics |
| `/logs` | GET | Last 50 validation artifacts |
| `/circuit-breaker` | POST | Trip or reset the breaker |

## License
MIT
