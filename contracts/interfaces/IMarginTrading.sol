// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.0;

/// @notice Interface implemented by the cross/isolated margin trading contracts.
interface IMarginTrading {
    function registerDeposit(address trader, address token, uint256 depositAmount)
        external returns (uint256 extinguishableDebt);
    function registerBorrow(address trader, address borrowToken, uint256 borrowAmount) external;
    function registerWithdrawal(address trader, address withdrawToken, uint256 withdrawAmount) external;
    function registerOvercollateralizedBorrow(
        address trader, address depositToken, uint256 depositAmount,
        address borrowToken, uint256 withdrawAmount
    ) external;
    function registerTradeAndBorrow(
        address trader, address tokenFrom, address tokenTo,
        uint256 inAmount, uint256 outAmount
    ) external returns (uint256 extinguishableDebt, uint256 borrowAmount);
    function registerLiquidation(address trader) external;
    function getHoldingAmounts(address trader)
        external view returns (address[] memory holdingTokens, uint256[] memory holdingAmounts);
    function getBorrowAmounts(address trader)
        external view returns (address[] memory borrowTokens, uint256[] memory borrowAmounts);
}
