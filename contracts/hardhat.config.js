require('@nomicfoundation/hardhat-toolbox');
require('dotenv').config();
const { secret } = require('./scripts/secrets');
const deployerKey = secret('DEPLOYER_PRIVATE_KEY');

const networks = { hardhat: { chainId: 31337 } };
const args = process.argv;
const networkFlag = args.indexOf('--network');
const selectedNetwork = networkFlag >= 0 ? args[networkFlag + 1] :
  args.find((arg) => arg.startsWith('--network='))?.split('=', 2)[1];
if (selectedNetwork === 'sepolia' && (!process.env.SEPOLIA_RPC_URL || !deployerKey)) {
  throw new Error('Sepolia is not configured. Copy .env.example to .env and set SEPOLIA_RPC_URL and DEPLOYER_PRIVATE_KEY_FILE.');
}
if (selectedNetwork === 'baseSepolia' && (!process.env.BASE_SEPOLIA_RPC_URL || !deployerKey)) {
  throw new Error('Base Sepolia is not configured. Copy .env.example to .env and set BASE_SEPOLIA_RPC_URL and DEPLOYER_PRIVATE_KEY_FILE.');
}
if (process.env.SEPOLIA_RPC_URL && deployerKey) {
  networks.sepolia = { url: process.env.SEPOLIA_RPC_URL, chainId: 11155111,
    accounts: [deployerKey] };
}
if (process.env.BASE_SEPOLIA_RPC_URL && deployerKey) {
  networks.baseSepolia = { url: process.env.BASE_SEPOLIA_RPC_URL, chainId: 84532,
    accounts: [deployerKey] };
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
