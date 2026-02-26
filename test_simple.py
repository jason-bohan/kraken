#!/usr/bin/env python3
"""
Super simple test - just volume
"""
from kraken_connection import get_ticker, place_order

ticker = get_ticker("SOLUSD")
price = float(ticker.get('a', [0])[0])
print(f"SOL price: ${price}")

# Minimum is 0.02 SOL
volume = 0.02

print(f"Trying to buy {volume} SOL...")
ok, result = place_order("SOLUSD", "buy", "market", volume=volume)

print("Result:", ok, result)
