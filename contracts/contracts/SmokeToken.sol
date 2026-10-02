// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @dev Worthless testnet-only token used to validate smoke-intent addresses.
contract SmokeToken is ERC20 {
    uint8 private immutable _tokenDecimals;

    constructor(string memory name_, string memory symbol_, uint8 decimals_) ERC20(name_, symbol_) {
        require(decimals_ <= 36, "Decimals too high");
        _tokenDecimals = decimals_;
        _mint(msg.sender, 1_000_000 * (10 ** uint256(decimals_)));
    }

    function decimals() public view override returns (uint8) {
        return _tokenDecimals;
    }
}
