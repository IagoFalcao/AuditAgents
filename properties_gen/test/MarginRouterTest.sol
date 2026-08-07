pragma solidity ^0.8.9;

contract MarginRouter {
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
        registerTrade(
            msg.sender,
            tokens[0],
            tokens[tokens.length - 1],
            amountIn,
            amounts[amounts.length - 1]
        );

        _swapExactT4T(amounts, amountOutMin, pairs, tokens);
    }
}