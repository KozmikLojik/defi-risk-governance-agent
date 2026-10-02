const fs = require('fs');
const path = require('path');
const readline = require('readline/promises');
const { stdin, stdout } = require('process');
const hre = require('hardhat');
const { secret } = require('./secrets');

const AGENT_ABI = [
  'function walletToAgent(address) view returns (uint256)',
  'function identities(uint256) view returns (string,string,address,uint256,uint256,bool)',
];

async function main() {
  const network = await hre.ethers.provider.getNetwork();
  const chainId = Number(network.chainId);
  if (![11155111, 84532].includes(chainId)) {
    throw new Error(`Smoke test accepts Sepolia or Base Sepolia only; connected chain is ${chainId}.`);
  }
  const recordPath = path.join(__dirname, '..', `deployed-addresses-${chainId}.json`);
  if (!fs.existsSync(recordPath)) throw new Error(`Missing deployment record: ${recordPath}`);
  const deployed = JSON.parse(fs.readFileSync(recordPath, 'utf8'));
  if (deployed.chainId !== chainId) throw new Error('Deployment record chain ID does not match RPC.');

  const agentKey = secret('AGENT_PRIVATE_KEY');
  const relayerKey = secret('TESTNET_RELAYER_PRIVATE_KEY');
  if (!agentKey || !relayerKey) throw new Error('Set agent and testnet relayer keys (or their _FILE paths).');
  const agent = new hre.ethers.Wallet(agentKey, hre.ethers.provider);
  const relayer = new hre.ethers.Wallet(relayerKey, hre.ethers.provider);
  const registry = new hre.ethers.Contract(deployed.AgentIdentityRegistry, AGENT_ABI, hre.ethers.provider);
  const routerArtifact = await hre.artifacts.readArtifact('RiskRouter');
  const router = new hre.ethers.Contract(deployed.RiskRouter, routerArtifact.abi, hre.ethers.provider);
  for (const [label, address] of Object.entries({
    AgentIdentityRegistry: deployed.AgentIdentityRegistry,
    ReputationRegistry: deployed.ReputationRegistry,
    RiskRouter: deployed.RiskRouter,
  })) {
    if ((await hre.ethers.provider.getCode(address)) === '0x') throw new Error(`${label} has no deployed bytecode.`);
  }
  const agentId = await registry.walletToAgent(agent.address);
  if (agentId === 0n) throw new Error(`Agent ${agent.address} is not registered in this deployment.`);
  const identity = await registry.identities(agentId);
  if (!identity[5] || identity[2].toLowerCase() !== agent.address.toLowerCase() || identity[3] !== BigInt(chainId)) {
    throw new Error('Registered agent is inactive or does not match the configured wallet/chain.');
  }
  if ((await router.agentRegistry()).toLowerCase() !== deployed.AgentIdentityRegistry.toLowerCase()) {
    throw new Error('RiskRouter agent registry does not match the deployment record.');
  }

  const tokenIn = process.env.TESTNET_SMOKE_TOKEN_IN || deployed.SmokeTokenIn;
  const tokenOut = process.env.TESTNET_SMOKE_TOKEN_OUT || deployed.SmokeTokenOut;
  if (!tokenIn || !tokenOut || !hre.ethers.isAddress(tokenIn) || !hre.ethers.isAddress(tokenOut)) {
    throw new Error('Set TESTNET_SMOKE_TOKEN_IN and TESTNET_SMOKE_TOKEN_OUT to deployed test tokens.');
  }
  if (tokenIn.toLowerCase() === tokenOut.toLowerCase()) throw new Error('Smoke token addresses must differ.');
  for (const [label, address] of [['token-in', tokenIn], ['token-out', tokenOut]]) {
    if ((await hre.ethers.provider.getCode(address)) === '0x') throw new Error(`${label} address has no deployed test-token code.`);
  }

  const nonce = await router.agentNonces(agent.address);
  const intent = {
    agent: agent.address,
    tokenIn,
    tokenOut,
    amountIn: 1n,
    maxSlippageBps: 1000n,
    deadline: BigInt(Math.floor(Date.now() / 1000) + 300),
    riskArtifactHash: hre.ethers.keccak256(hre.ethers.toUtf8Bytes(`guardian-smoke-${Date.now()}`)),
    nonce,
  };
  const domain = {
    name: 'GuardianAI RiskRouter', version: '1', chainId,
    verifyingContract: deployed.RiskRouter,
  };
  const types = { TradeIntent: [
    { name: 'agent', type: 'address' }, { name: 'tokenIn', type: 'address' },
    { name: 'tokenOut', type: 'address' }, { name: 'amountIn', type: 'uint256' },
    { name: 'maxSlippageBps', type: 'uint256' }, { name: 'deadline', type: 'uint256' },
    { name: 'riskArtifactHash', type: 'bytes32' }, { name: 'nonce', type: 'uint256' },
  ] };
  const signature = await agent.signTypedData(domain, types, intent);
  const connectedRouter = router.connect(relayer);
  await connectedRouter.submitTradeIntent.staticCall(intent, signature);
  console.log(`Preflight passed on chain ${chainId}; router signature and registration validate.`);
  console.log(`Agent ${agent.address}; relayer ${relayer.address}; nonce ${nonce}.`);

  if (process.env.SMOKE_SUBMIT !== 'true') {
    console.log('No transaction was sent. Set SMOKE_SUBMIT=true and rerun to enable the confirmed smoke intent.');
    return;
  }
  const prompt = readline.createInterface({ input: stdin, output: stdout });
  const confirmation = await prompt.question(`This submits a 1-base-unit audit intent and spends testnet gas. Type SUBMIT ${chainId} to continue: `);
  prompt.close();
  if (confirmation !== `SUBMIT ${chainId}`) throw new Error('Confirmation did not match; no transaction was sent.');
  const transaction = await connectedRouter.submitTradeIntent(intent, signature);
  const receipt = await transaction.wait();
  if (receipt.status !== 1) throw new Error(`Smoke transaction reverted: ${transaction.hash}`);
  console.log(JSON.stringify({ chainId, transactionHash: transaction.hash, blockNumber: receipt.blockNumber,
    gasUsed: receipt.gasUsed.toString(), nonce: nonce.toString() }, null, 2));
}

main().catch((error) => {
  console.error(error.message || error);
  process.exitCode = 1;
});
