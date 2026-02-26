#!/usr/bin/env python3
"""
Test buy with cost + volume - send both
"""
from kraken_connection import place_order, get_balance, get_ticker

bal = get_balance()
usd = float(bal.get('ZUSD', 0))
print(f"USD Balance: ${usd}")

# Get SOL price
ticker = get_ticker("SOLUSD")
if ticker:
    price = float(ticker.get('a', [0])[0])
    print(f"SOL price: ${price}")
    
    # Calculate volume for $1
    volume = 1.0 / price
    print(f"Volume for $1: {volume}")
    
    # Buy $1 worth of SOL with BOTH cost and volume
    ok, result = place_order("SOLUSD", "buy", "market", volume=volume, cost=1.00)
    print("Result:", ok, result)
else:
    print("Could not get SOL price")
