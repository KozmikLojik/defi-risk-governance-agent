// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "@openzeppelin/contracts/utils/cryptography/EIP712.sol";

/**
 * @title AgentIdentityRegistry (ERC-8004 Mock)
 * @notice Registers AI trading agent identities as ERC-721 tokens
 *         with EIP-712 wallet binding and metadata linking.
 */
contract AgentIdentityRegistry is ERC721, EIP712, Ownable {
    using ECDSA for bytes32;

    uint256 private _tokenIdCounter;

    struct AgentIdentity {
        string handle;          // Human-readable agent name
        string metadataURI;     // IPFS/HTTPS URI to agent JSON
        address boundWallet;    // Wallet bound via EIP-712
        uint256 chainId;        // Chain binding
        uint256 registeredAt;
        bool active;
    }

    // tokenId => identity
    mapping(uint256 => AgentIdentity) public identities;
    // wallet => tokenId (one agent per wallet)
    mapping(address => uint256) public walletToAgent;
    // handle uniqueness
    mapping(string => bool) public handleTaken;

    bytes32 private constant BIND_TYPEHASH = keccak256(
        "BindWallet(address wallet,uint256 agentId,uint256 chainId,uint256 nonce)"
    );

    mapping(address => uint256) public nonces;

    event AgentRegistered(uint256 indexed tokenId, string handle, address boundWallet);
    event AgentDeactivated(uint256 indexed tokenId);

    constructor() ERC721("GuardianAI Agent", "GAIA") EIP712("GuardianAI", "1") Ownable(msg.sender) {}

    /**
     * @notice Register a new agent identity
     * @param handle Unique agent handle (e.g., "guardian-alpha-01")
     * @param metadataURI Link to agent JSON metadata
     * @param wallet Wallet to bind to this agent
     * @param signature EIP-712 signed binding proof
     */
    function registerAgent(
        string calldata handle,
        string calldata metadataURI,
        address wallet,
        bytes calldata signature
    ) external returns (uint256 tokenId) {
        require(!handleTaken[handle], "Handle already taken");
        require(walletToAgent[wallet] == 0, "Wallet already bound to an agent");

        // Verify EIP-712 signature from the wallet being bound
        uint256 nonce = nonces[wallet];
        bytes32 structHash = keccak256(
            abi.encode(BIND_TYPEHASH, wallet, _tokenIdCounter + 1, block.chainid, nonce)
        );
        bytes32 digest = _hashTypedDataV4(structHash);
        address signer = digest.recover(signature);
        require(signer == wallet, "Invalid wallet binding signature");

        nonces[wallet]++;
        _tokenIdCounter++;
        tokenId = _tokenIdCounter;

        _safeMint(msg.sender, tokenId);

        identities[tokenId] = AgentIdentity({
            handle: handle,
            metadataURI: metadataURI,
            boundWallet: wallet,
            chainId: block.chainid,
            registeredAt: block.timestamp,
            active: true
        });

        walletToAgent[wallet] = tokenId;
        handleTaken[handle] = true;

        emit AgentRegistered(tokenId, handle, wallet);
    }

    function deactivateAgent(uint256 tokenId) external {
        require(ownerOf(tokenId) == msg.sender, "Not owner");
        identities[tokenId].active = false;
        emit AgentDeactivated(tokenId);
    }

    function tokenURI(uint256 tokenId) public view override returns (string memory) {
        _requireOwned(tokenId);
        return identities[tokenId].metadataURI;
    }

    function getAgentByWallet(address wallet) external view returns (AgentIdentity memory) {
        uint256 tokenId = walletToAgent[wallet];
        require(tokenId != 0, "No agent for this wallet");
        return identities[tokenId];
    }

    function domainSeparator() external view returns (bytes32) {
        return _domainSeparatorV4();
    }
}

/**
 * @title ReputationRegistry
 * @notice Stores on-chain reputation scores for GuardianAI agents.
 *         Updated by the authorized backend oracle.
 */
contract ReputationRegistry is Ownable {
    address public oracle; // Backend signer authorized to update

    struct ReputationRecord {
        uint256 agentId;
        uint32  safeTrades;
        uint32  rejectedTrades;
        int32   drawdownBps;      // basis points (e.g., -500 = -5%)
        int32   sharpeBps;        // sharpe * 100 (e.g., 150 = 1.50)
        uint32  reputationScore;  // 0-10000 (bps)
        uint256 lastUpdated;
    }

    mapping(uint256 => ReputationRecord) public records;

    event ReputationUpdated(uint256 indexed agentId, uint32 score, uint256 timestamp);

    constructor(address _oracle) Ownable(msg.sender) {
        oracle = _oracle;
    }

    modifier onlyOracle() {
        require(msg.sender == oracle, "Not authorized oracle");
        _;
    }

    function updateReputation(
        uint256 agentId,
        uint32 safeTrades,
        uint32 rejectedTrades,
        int32 drawdownBps,
        int32 sharpeBps,
        uint32 reputationScore
    ) external onlyOracle {
        records[agentId] = ReputationRecord({
            agentId: agentId,
            safeTrades: safeTrades,
            rejectedTrades: rejectedTrades,
            drawdownBps: drawdownBps,
            sharpeBps: sharpeBps,
            reputationScore: reputationScore,
            lastUpdated: block.timestamp
        });

        emit ReputationUpdated(agentId, reputationScore, block.timestamp);
    }

    function getReputation(uint256 agentId) external view returns (ReputationRecord memory) {
        return records[agentId];
    }

    function setOracle(address _oracle) external onlyOwner {
        oracle = _oracle;
    }
}

/**
 * @title RiskRouter
 * @notice Receives validated, signed TradeIntents from the GuardianAI backend.
 *         Records validation hashes on-chain for auditability.
 *         Does NOT execute trades — it is a trust anchor.
 */
contract RiskRouter is EIP712, Ownable {
    using ECDSA for bytes32;

    address public agentRegistry;
    address public reputationRegistry;
    bool public circuitBreakerTripped;

    struct TradeIntent {
        address agent;       // Bound wallet of the agent
        address tokenIn;
        address tokenOut;
        uint256 amountIn;
        uint256 maxSlippageBps;  // e.g. 50 = 0.5%
        uint256 deadline;
        bytes32 riskArtifactHash;  // keccak256 of off-chain validation artifact
        uint256 nonce;
    }

    bytes32 private constant TRADE_INTENT_TYPEHASH = keccak256(
        "TradeIntent(address agent,address tokenIn,address tokenOut,uint256 amountIn,uint256 maxSlippageBps,uint256 deadline,bytes32 riskArtifactHash,uint256 nonce)"
    );

    mapping(address => uint256) public agentNonces;
    mapping(bytes32 => bool) public processedIntents;

    event TradeIntentReceived(
        bytes32 indexed intentHash,
        address indexed agent,
        bytes32 riskArtifactHash,
        uint256 timestamp
    );
    event CircuitBreakerTripped(address triggeredBy, uint256 timestamp);
    event CircuitBreakerReset(address resetBy, uint256 timestamp);

    constructor(address _agentRegistry, address _reputationRegistry)
        EIP712("GuardianAI RiskRouter", "1")
        Ownable(msg.sender)
    {
        agentRegistry = _agentRegistry;
        reputationRegistry = _reputationRegistry;
    }

    /**
     * @notice Submit a validated trade intent with EIP-712 signature
     * @param intent The trade intent struct
     * @param signature EIP-712 signature from the agent's bound wallet
     */
    function submitTradeIntent(
        TradeIntent calldata intent,
        bytes calldata signature
    ) external returns (bytes32 intentHash) {
        require(!circuitBreakerTripped, "Circuit breaker is active");
        require(intent.deadline >= block.timestamp, "Intent expired");

        bytes32 structHash = keccak256(abi.encode(
            TRADE_INTENT_TYPEHASH,
            intent.agent,
            intent.tokenIn,
            intent.tokenOut,
            intent.amountIn,
            intent.maxSlippageBps,
            intent.deadline,
            intent.riskArtifactHash,
            intent.nonce
        ));

        intentHash = _hashTypedDataV4(structHash);
        require(!processedIntents[intentHash], "Intent already processed");

        // Verify signature is from the agent's bound wallet
        address signer = intentHash.recover(signature);
        require(signer == intent.agent, "Invalid agent signature");

        // Verify nonce
        require(intent.nonce == agentNonces[intent.agent], "Invalid nonce");
        agentNonces[intent.agent]++;

        processedIntents[intentHash] = true;

        emit TradeIntentReceived(intentHash, intent.agent, intent.riskArtifactHash, block.timestamp);
    }

    function tripCircuitBreaker() external onlyOwner {
        circuitBreakerTripped = true;
        emit CircuitBreakerTripped(msg.sender, block.timestamp);
    }

    function resetCircuitBreaker() external onlyOwner {
        circuitBreakerTripped = false;
        emit CircuitBreakerReset(msg.sender, block.timestamp);
    }

    function getIntentTypehash() external pure returns (bytes32) {
        return TRADE_INTENT_TYPEHASH;
    }
}
