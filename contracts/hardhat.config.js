require('@nomicfoundation/hardhat-toolbox');
require('dotenv').config();
const { secret } = require('./scripts/secrets');

const networks = { hardhat: { chainId: 31337 } };
const args = process.argv;
const networkFlag = args.indexOf('--network');
const selectedNetwork = networkFlag >= 0 ? args[networkFlag + 1] :
  args.find((arg) => arg.startsWith('--network='))?.split('=', 2)[1];
const deployerKey = ['sepolia', 'baseSepolia'].includes(selectedNetwork) ?
  secret('DEPLOYER_PRIVATE_KEY') : undefined;

function configuredPrivateKey(name) {
  if (!deployerKey) return undefined;
  const normalized = deployerKey.trim();
  if (!/^(0x)?[0-9a-fA-F]{64}$/.test(normalized)) {
    throw new Error(`${name} must contain exactly 32 bytes (64 hexadecimal characters), optionally prefixed with 0x. Check that the key file contains the private key itself, not its file path or a placeholder.`);
  }
  return normalized.startsWith('0x') ? normalized : `0x${normalized}`;
}

// Do not let unused testnet credentials break local compilation or tests.
// Only load and validate credentials for the explicitly selected testnet.
if (selectedNetwork === 'sepolia') {
  if (!process.env.SEPOLIA_RPC_URL || !deployerKey) {
    throw new Error('Sepolia is not configured. Set SEPOLIA_RPC_URL and DEPLOYER_PRIVATE_KEY_FILE (or DEPLOYER_PRIVATE_KEY) in the project-root .env file.');
  }
  networks.sepolia = { url: process.env.SEPOLIA_RPC_URL, chainId: 11155111,
    accounts: [configuredPrivateKey('DEPLOYER_PRIVATE_KEY')] };
}
if (selectedNetwork === 'baseSepolia') {
  if (!process.env.BASE_SEPOLIA_RPC_URL || !deployerKey) {
    throw new Error('Base Sepolia is not configured. Set BASE_SEPOLIA_RPC_URL and DEPLOYER_PRIVATE_KEY_FILE (or DEPLOYER_PRIVATE_KEY) in the project-root .env file.');
  }
  networks.baseSepolia = { url: process.env.BASE_SEPOLIA_RPC_URL, chainId: 84532,
    accounts: [configuredPrivateKey('DEPLOYER_PRIVATE_KEY')] };
}

module.exports = {
  solidity: {
    version: '0.8.25',
    settings: {
      evmVersion: 'cancun'
    }
  },
  networks,
  paths: {
    sources: require('path').resolve(__dirname, './contracts'),
    tests: require('path').resolve(__dirname, './test'),
    cache: require('path').resolve(__dirname, './cache'),
    artifacts: require('path').resolve(__dirname, './artifacts')
  }
};
