#!/usr/bin/env python3
"""
Test sell - sell 0.01 SOL from your existing balance
"""
from kraken_connection import place_order, get_balance

bal = get_balance()
print("Balance:", bal)

# You have 0.289 SOL - let's sell 0.01 of it (~$0.70)
ok, result = place_order("SOLUSD", "sell", "market", 0.01)

print("Result:", ok, result)

if ok:
    print("✅ Test sell successful!")
else:
    print("❌ Failed:", result)