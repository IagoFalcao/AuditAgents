// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.0;

/// @notice Minimal wrapped-native-token interface required by the contracts in contracts/3.
interface IWETH {
    function deposit() external payable;
    function withdraw(uint256 amount) external;
}
