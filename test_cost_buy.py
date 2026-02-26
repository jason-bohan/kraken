#!/usr/bin/env python3
"""
Test buy with cost - spend exactly $1 from your USD balance
"""
from kraken_connection import place_order, get_balance

bal = get_balance()
usd = float(bal.get('ZUSD', 0))
print(f"USD Balance: ${usd}")

# Buy $1 worth of SOL using 'cost' parameter
ok, result = place_order("SOLUSD", "buy", "market", cost=1.00)

print("Result:", ok, result)

if ok:
    print("✅ $1 TEST BUY SUCCESSFUL!")
else:
    print("❌ Failed:", result)
