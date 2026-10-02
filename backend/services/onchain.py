"""Opt-in, testnet-only RiskRouter submission through a funded relayer."""

from __future__ import annotations

import os

from eth_account import Account
from web3 import Web3
from services.secrets import get_secret

ROUTER_ABI = [{
    "type": "function", "name": "submitTradeIntent", "stateMutability": "nonpayable",
    "inputs": [
        {"name": "intent", "type": "tuple", "components": [
            {"name": "agent", "type": "address"},
            {"name": "tokenIn", "type": "address"},
            {"name": "tokenOut", "type": "address"},
            {"name": "amountIn", "type": "uint256"},
            {"name": "maxSlippageBps", "type": "uint256"},
            {"name": "deadline", "type": "uint256"},
            {"name": "riskArtifactHash", "type": "bytes32"},
            {"name": "nonce", "type": "uint256"},
        ]},
        {"name": "signature", "type": "bytes"},
    ],
    "outputs": [{"name": "intentHash", "type": "bytes32"}],
}]
NONCE_ABI = [{"type": "function", "name": "agentNonces", "stateMutability": "view",
             "inputs": [{"name": "", "type": "address"}],
             "outputs": [{"name": "", "type": "uint256"}]}]
TESTNET_CHAIN_IDS = {int(value) for value in os.getenv(
    "TESTNET_CHAIN_IDS", "11155111,84532,421614,80002,11155420,31337"
).split(",") if value.strip()}


def _client() -> tuple[Web3, int, str]:
    if os.getenv("TESTNET_SUBMISSIONS_ENABLED", "false").lower() != "true":
        raise RuntimeError("Testnet submissions are disabled")
    rpc = os.getenv("ONCHAIN_RPC_URL")
    router_address = os.getenv("ROUTER_ADDRESS")
    chain_id = int(os.getenv("CHAIN_ID", "31337"))
    if not rpc or not router_address:
        raise RuntimeError("ONCHAIN_RPC_URL and ROUTER_ADDRESS are required")
    if chain_id not in TESTNET_CHAIN_IDS or chain_id == 1:
        raise RuntimeError(f"Chain {chain_id} is not in the configured testnet allowlist")
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 12}))
    if not w3.is_connected():
        raise RuntimeError("Could not connect to the configured RPC")
    if w3.eth.chain_id != chain_id:
        raise RuntimeError(f"RPC chain ID {w3.eth.chain_id} does not match configured chain {chain_id}")
    if not Web3.is_address(router_address):
        raise RuntimeError("ROUTER_ADDRESS is not a valid EVM address")
    return w3, chain_id, Web3.to_checksum_address(router_address)


def get_agent_nonce(agent_address: str) -> int:
    w3, _, router_address = _client()
    contract = w3.eth.contract(address=router_address, abi=NONCE_ABI)
    return int(contract.functions.agentNonces(Web3.to_checksum_address(agent_address)).call())


def submit_signed_intent(intent: dict, signature: str) -> dict:
    w3, chain_id, router_address = _client()
    if int(intent["domain"]["chainId"]) != chain_id:
        raise RuntimeError("Signed intent chain does not match the connected testnet")
    relayer_key = get_secret("TESTNET_RELAYER_PRIVATE_KEY", required=True)
    if not relayer_key:
        raise RuntimeError("TESTNET_RELAYER_PRIVATE_KEY is required for testnet submission")
    relayer = Account.from_key(relayer_key)
    contract = w3.eth.contract(address=router_address, abi=ROUTER_ABI)
    values = (
        Web3.to_checksum_address(intent["agent"]),
        Web3.to_checksum_address(intent["tokenIn"]),
        Web3.to_checksum_address(intent["tokenOut"]),
        int(intent["amountIn"]),
        int(intent["maxSlippageBps"]),
        int(intent["deadline"]),
        bytes.fromhex(intent["riskArtifactHash"][2:]),
        int(intent["nonce"]),
    )
    nonce = w3.eth.get_transaction_count(relayer.address)
    function = contract.functions.submitTradeIntent(values, bytes.fromhex(signature.removeprefix("0x")))
    gas = function.estimate_gas({"from": relayer.address})
    tx = function.build_transaction({
        "from": relayer.address,
        "nonce": nonce,
        "chainId": chain_id,
        "gas": int(gas * 1.2),
        "gasPrice": w3.eth.gas_price,
    })
    signed_tx = relayer.sign_transaction(tx)
    raw = getattr(signed_tx, "raw_transaction", getattr(signed_tx, "rawTransaction", None))
    tx_hash = w3.eth.send_raw_transaction(raw)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status != 1:
        raise RuntimeError(f"RiskRouter transaction reverted: {tx_hash.hex()}")
    return {"chain_id": chain_id, "transaction_hash": tx_hash.hex(),
            "block_number": receipt.blockNumber, "gas_used": receipt.gasUsed}
