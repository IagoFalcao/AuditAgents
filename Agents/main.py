import os, json
from Agents.Attacker import Attacker

def main():
    contract_filepath = '/Contracts/VulnerableLending.sol'
    fn_source_code = """
        function borrow(uint256 amount) external {
        uint256 price = oracle.getPrice();
        require(collateral[msg.sender] * price >= amount * 2);
        debt[msg.sender] += amount;
        }"""
    attacker = Attacker('borrow',fn_source_code)
    #prompts = attacker.load_prompts()
    #print(json.dumps(prompts,indent = 4))
    #print(json.dumps(attacker.__dict__,indent=4))
    vul_found = attacker.query()
    print(vul_found)

if __name__ == "__main__":
    main()