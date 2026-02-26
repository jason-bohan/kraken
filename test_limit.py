#!/usr/bin/env python3
"""
Try limit order instead
"""
from kraken_connection import get_ticker, place_order

ticker = get_ticker("SOLUSD")
ask = float(ticker.get('a', [0])[0])  # ask price
bid = float(ticker.get('b', [0])[0])  # bid price

print(f"SOL - Ask: ${ask}, Bid: ${bid}")

# Try limit order - buy at bid (lower price)
volume = 0.01  # Very small
ok, result = place_order("SOLUSD", "buy", "limit", volume=volume, price=bid)

print("Result:", ok, result)
