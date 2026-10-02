# Contract security review status

## Current status

No independent third-party audit has been completed. `RiskRouter` verifies and records signed intents; it does not hold tokens, call a DEX, or execute swaps. The testnet `SmokeToken` contracts are worthless fixtures for smoke tests, not production assets.

Do not represent these contracts as audited and do not use them to custody or trade real assets. The local Hardhat tests are functional checks, not a security audit.

## Gate before adding swap execution

Commission an independent audit of the deployed identity, intent, and execution contracts and their exact compiler/dependency configuration. A swap executor should be a separate contract with a narrowly defined router and token allowlist, minimum-output and deadline enforcement, exact approvals, safe ERC-20 handling, reentrancy protection, atomic revert behavior, and a tested emergency stop. Fuzz/invariant tests should cover unauthorized callers, replayed signatures, malicious tokens/routers, approval cleanup, circuit-breaker behavior, and assets held before and after every path.

The audit should cover both the executor and how the backend constructs/signs intents. Fix and retest all findings, publish the audit and deployed bytecode verification, then repeat testnet smoke and upgrade/rollback checks before considering any mainnet use.
