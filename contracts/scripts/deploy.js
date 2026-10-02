const hre = require("hardhat");
const fs = require("fs");
const path = require("path");
const { secret } = require("./secrets");

async function main() {
  const network = await hre.ethers.provider.getNetwork();
  const chainId = Number(network.chainId);
  if (![11155111, 84532].includes(chainId)) {
    throw new Error(`Refusing deployment to chain ${chainId}; use Sepolia or Base Sepolia only.`);
  }
  const agentKey = secret("AGENT_PRIVATE_KEY");
  if (!agentKey) throw new Error("Set AGENT_PRIVATE_KEY or AGENT_PRIVATE_KEY_FILE before deployment.");
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying with:", deployer.address);

  const Registry = await hre.ethers.getContractFactory("AgentIdentityRegistry");
  const registry = await Registry.deploy();
  await registry.waitForDeployment();
  console.log("AgentIdentityRegistry:", await registry.getAddress());

  const Reputation = await hre.ethers.getContractFactory("ReputationRegistry");
  const reputation = await Reputation.deploy(deployer.address);
  await reputation.waitForDeployment();
  console.log("ReputationRegistry:", await reputation.getAddress());

  const Router = await hre.ethers.getContractFactory("RiskRouter");
  const router = await Router.deploy(
    await registry.getAddress(),
    await reputation.getAddress()
  );
  await router.waitForDeployment();
  console.log("RiskRouter:", await router.getAddress());

  const SmokeToken = await hre.ethers.getContractFactory("SmokeToken");
  const smokeTokenIn = await SmokeToken.deploy("GuardianAI Smoke USD", "gUSD", 6);
  await smokeTokenIn.waitForDeployment();
  const smokeTokenOut = await SmokeToken.deploy("GuardianAI Smoke Asset", "gAST", 18);
  await smokeTokenOut.waitForDeployment();

  const addresses = {
    chainId: Number((await hre.ethers.provider.getNetwork()).chainId),
    deployer: deployer.address,
    AgentIdentityRegistry: await registry.getAddress(),
    ReputationRegistry: await reputation.getAddress(),
    RiskRouter: await router.getAddress(),
    SmokeTokenIn: await smokeTokenIn.getAddress(),
    SmokeTokenOut: await smokeTokenOut.getAddress(),
  };
  if (agentKey) {
    const agent = new hre.ethers.Wallet(agentKey);
    const typedChainId = BigInt(addresses.chainId);
    const domain = { name: "GuardianAI", version: "1", chainId,
      verifyingContract: addresses.AgentIdentityRegistry };
    const types = { BindWallet: [
      { name: "wallet", type: "address" }, { name: "agentId", type: "uint256" },
      { name: "chainId", type: "uint256" }, { name: "nonce", type: "uint256" },
    ] };
    const agentId = await registry.nextAgentId();
    const bindNonce = await registry.nonces(agent.address);
    const signature = await agent.signTypedData(domain, types, {
      wallet: agent.address, agentId, chainId: typedChainId, nonce: bindNonce,
    });
    await (await registry.registerAgent(`guardian-ai-${chainId}`, "https://example.invalid/guardian-ai.json",
      agent.address, signature)).wait();
    addresses.agent = agent.address;
    console.log("Registered agent:", agent.address);
  }
  const output = path.join(__dirname, "..", `deployed-addresses-${chainId}.json`);
  fs.writeFileSync(output, JSON.stringify(addresses, null, 2) + "\n", { mode: 0o600 });
  console.log("Deployment record:", output);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
