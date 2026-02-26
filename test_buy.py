#!/usr/bin/env python3
"""
Test buy - spend exactly $1 to test the API
"""
from kraken_connection import place_order, get_balance

bal = get_balance()
print("Balance:", bal)

# Try to buy $1 worth of SOL (cheapest way to test)
# SOL is ~$70, so 0.014 SOL = ~$1
ok, result = place_order("SOLUSD", "buy", "market", 0.015)

print("Result:", ok, result)
