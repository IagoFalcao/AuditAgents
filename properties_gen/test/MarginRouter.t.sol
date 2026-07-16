pragma solidity ^0.8.0;

import '@uniswap/v2-core/contracts/interfaces/IUniswapV2Factory.sol';
import '../libraries/UniswapStyleLib.sol';
import './RoleAware.sol';
import './Fund.sol';
import '../interfaces/IMarginTrading.sol';
import './Lending.sol';
import './Admin.sol';
import './IncentivizedHolder.sol';

/// @title Top level transaction controller
contract MarginRouter is RoleAware, IncentivizedHolder, Ownable {
    /// @notice wrapped ETH ERC20 contract
    address public immutable WETH;
    uint256 public constant mswapFeesPer10k = 10;

    /// emitted when a trader depoits on cross margin
    event CrossDeposit(address trader, address depositToken, uint256 depositAmount);
    /// emitted whenever a trade happens
    event CrossTrade(address trader, address inToken, uint256 inTokenAmount, uint256 inTokenBorrow, address outToken, uint256 outTokenAmount, uint256 outTokenExtinguish);
    /// emitted when a trader withdraws funds
    event CrossWithdraw(address trader, address withdrawToken, uint256 withdrawAmount);
    /// emitted upon sucessfully borrowing
    event CrossBorrow(address trader, address borrowToken, uint256 borrowAmount);

    modifier ensure(uint256 deadline) {
        require(deadline >= block.timestamp, "Trade has expired");
        _;
    }

    constructor(address _WETH, address _roles) RoleAware(_roles) {
        WETH = _WETH;
    }

    /// @notice traders call this to deposit funds on cross margin
    function crossDeposit(address depositToken, uint256 depositAmount)
        external
    {
        Fund(fund()).depositFor(msg.sender, depositToken, depositAmount);

        uint256 extinguishAmount =
            IMarginTrading(marginTrading()).registerDeposit(
                msg.sender,
                depositToken,
                depositAmount
            );
        if (extinguishAmount > 0) {
            Lending(lending()).payOff(depositToken, extinguishAmount);
            withdrawClaim(msg.sender, depositToken, extinguishAmount);
        }
        emit CrossDeposit(msg.sender, depositToken, depositAmount);
    }

    /// @notice deposit wrapped ehtereum into cross margin account
    function crossDepositETH() external payable {
        Fund(fund()).depositToWETH{value: msg.value}();
        uint256 extinguishAmount =
            IMarginTrading(marginTrading()).registerDeposit(
                msg.sender,
                WETH,
                msg.value
            );
        if (extinguishAmount > 0) {
            Lending(lending()).payOff(WETH, extinguishAmount);
            withdrawClaim(msg.sender, WETH, extinguishAmount);
        }
        emit CrossDeposit(msg.sender, WETH, msg.value);
    }

    /// @notice make swaps on AMM using protocol funds, only for authorized contracts
    function authorizedSwapExactT4T(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata pairs,
        address[] calldata tokens
    ) external returns (uint256[] memory amounts) {
        require(isAuthorizedFundTrader(msg.sender), "Calling contract is not authorized to trade with protocl funds");
        amounts = UniswapStyleLib.getAmountsOut(amountIn, pairs, tokens);
        _swapExactT4T(amounts, amountOutMin, pairs, tokens);
    }

    /// @notice entry point for swapping tokens held in cross margin account
    function crossSwapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata pairs,
        address[] calldata tokens,
        uint256 deadline
    ) external ensure(deadline) returns (uint256[] memory amounts) {
        // calc fees
        uint256 fees = takeFeesFromInput(amountIn);

        // swap
        amounts = UniswapStyleLib.getAmountsOut(amountIn - fees, pairs, tokens);

        // checks that trader is within allowed lending bounds
        registerTrade(msg.sender, tokens[0], tokens[tokens.length - 1], amountIn, amounts[amounts.length - 1]);

        _swapExactT4T(amounts, amountOutMin, pairs, tokens);
    }
}
