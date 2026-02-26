#!/usr/bin/env python3
"""
PEPE Test — Buy a tiny amount to test the API
"""
from kraken_connection import place_order, get_balance

# Check balance
bal = get_balance()
print("Balance:", bal)

# Buy $0.10 worth of PEPE (100000 units)
# PEPE is very cheap, so 100000 = ~$0.07
ok, result = place_order("PEPEUSD", "buy", "market", 100000)

print("Result:", ok, result)

if ok:
    print("✅ PEPE bought!")
else:
    print("❌ Failed:", result)
