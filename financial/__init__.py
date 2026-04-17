"""Financial Domain: account balances, obligations, and projected liquidity.

Hard constraints:
- All Tier-1 obligations must be met
- No future liquidity breach allowed

Event sourced; projection defines truth.
"""
