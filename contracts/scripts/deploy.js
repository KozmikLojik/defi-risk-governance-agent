const hre = require("hardhat");

async function main() {
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
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});