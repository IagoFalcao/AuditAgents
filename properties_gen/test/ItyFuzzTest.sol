
//SPDX-License-Identifier: UNLICENSED

import './MarginRouter.sol';
import './UniswapStyleLib.sol';
import '@openzeppelin/contracts/utils/math/SafeMath.sol';

contract ItyFuzzTest is MarginRouter, UniswapStyleLib {
    using SafeMath for uint256;

    uint256 public constant MIN_FEE = 1;
    uint256 public constant MAX_FEE = 10000;
    uint256 public constant MAX_AMOUNT_IN = 10000000000000000000000000000000