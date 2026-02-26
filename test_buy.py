#!/usr/bin/env python3
"""
Test buy - spend exactly $1 to test the API
"""
from kraken_connection import place_order, get_balance, get_ticker, get_min_order_info

bal = get_balance()
print("Balance:", bal)

# Get minimum order requirements for SOLUSD
pair = "SOLUSD"
min_info = get_min_order_info(pair)
print(f"Minimum order info for {pair}: {min_info}")

# Get current SOL price
ticker = get_ticker(pair)
if ticker and min_info:
    current_price = float(ticker.get("c", [0])[0])
    min_volume = min_info['ordermin']
    min_cost = min_info['costmin']
    
    # Use the larger of minimum volume or minimum cost
    if min_cost > 0:
        volume_from_cost = min_cost / current_price
        final_volume = max(min_volume, volume_from_cost)
        final_cost = final_volume * current_price
    else:
        final_volume = min_volume
        final_cost = final_volume * current_price
    
    print(f"SOL price: ${current_price}")
    print(f"Final order: {final_volume} SOL = ${final_cost:.2f}")
    
    # Place order
    ok, result = place_order(pair, "buy", "market", cost=final_cost, volume=final_volume)
else:
    print("Could not get SOL price or minimum order info")
    ok, result = False, {"error": "No market data"}

print("Result:", ok, result)
