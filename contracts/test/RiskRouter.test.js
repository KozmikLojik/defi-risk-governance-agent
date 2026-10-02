const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("RiskRouter", function () {
  let deployer, agentWallet, registry, reputation, router, domain;
  const bindTypes = {
    BindWallet: [
      { name: "wallet", type: "address" },
      { name: "agentId", type: "uint256" },
      { name: "chainId", type: "uint256" },
      { name: "nonce", type: "uint256" },
    ],
  };
  const intentTypes = {
    TradeIntent: [
      { name: "agent", type: "address" },
      { name: "tokenIn", type: "address" },
      { name: "tokenOut", type: "address" },
      { name: "amountIn", type: "uint256" },
      { name: "maxSlippageBps", type: "uint256" },
      { name: "deadline", type: "uint256" },
      { name: "riskArtifactHash", type: "bytes32" },
      { name: "nonce", type: "uint256" },
    ],
  };

  async function registerAgent() {
    const network = await ethers.provider.getNetwork();
    const identityDomain = {
      name: "GuardianAI", version: "1", chainId: network.chainId,
      verifyingContract: await registry.getAddress(),
    };
    const bind = { wallet: agentWallet.address, agentId: 1, chainId: network.chainId, nonce: 0 };
    const signature = await agentWallet.signTypedData(identityDomain, bindTypes, bind);
    await registry.registerAgent("test-agent", "https://example.invalid/agent.json", agentWallet.address, signature);
  }

  async function signedIntent(nonce = 0) {
    const network = await ethers.provider.getNetwork();
    const intent = {
      agent: agentWallet.address,
      tokenIn: "0x0000000000000000000000000000000000001001",
      tokenOut: "0x0000000000000000000000000000000000002001",
      amountIn: 500_000_000,
      maxSlippageBps: 50,
      deadline: Math.floor(Date.now() / 1000) + 300,
      riskArtifactHash: ethers.keccak256(ethers.toUtf8Bytes("risk-artifact")),
      nonce,
    };
    const signature = await agentWallet.signTypedData(domain, intentTypes, intent);
    return { intent, signature };
  }

  beforeEach(async function () {
    [deployer] = await ethers.getSigners();
    agentWallet = ethers.Wallet.createRandom();
    const Registry = await ethers.getContractFactory("AgentIdentityRegistry");
    registry = await Registry.deploy();
    const Reputation = await ethers.getContractFactory("ReputationRegistry");
    reputation = await Reputation.deploy(deployer.address);
    const Router = await ethers.getContractFactory("RiskRouter");
    router = await Router.deploy(await registry.getAddress(), await reputation.getAddress());
    const network = await ethers.provider.getNetwork();
    domain = {
      name: "GuardianAI RiskRouter", version: "1", chainId: network.chainId,
      verifyingContract: await router.getAddress(),
    };
    await registerAgent();
  });

  it("accepts a valid EIP-712 intent from an active registered agent once", async function () {
    const { intent, signature } = await signedIntent();
    await expect(router.submitTradeIntent(intent, signature)).to.emit(router, "TradeIntentReceived");
    expect(await router.agentNonces(agentWallet.address)).to.equal(1);
    await expect(router.submitTradeIntent(intent, signature)).to.be.revertedWith("Intent already processed");
  });

  it("rejects inactive agents", async function () {
    await registry.deactivateAgent(1);
    const { intent, signature } = await signedIntent();
    await expect(router.submitTradeIntent(intent, signature)).to.be.revertedWith("Agent is inactive");
  });

  it("rejects a signature created for a different router domain", async function () {
    const { intent } = await signedIntent();
    const badSignature = await agentWallet.signTypedData(
      { ...domain, verifyingContract: deployer.address }, intentTypes, intent,
    );
    await expect(router.submitTradeIntent(intent, badSignature)).to.be.revertedWith("Invalid agent signature");
  });
});

describe("SmokeToken", function () {
  it("mints worthless test tokens with explicit decimals for testnet smoke checks", async function () {
    const [owner] = await ethers.getSigners();
    const Token = await ethers.getContractFactory("SmokeToken");
    const token = await Token.deploy("Smoke USD", "sUSD", 6);
    expect(await token.decimals()).to.equal(6);
    expect(await token.balanceOf(owner.address)).to.equal(1_000_000n * 10n ** 6n);
  });
});
