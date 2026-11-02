#!/usr/bin/env python3
"""Check open orders on Kraken."""
from kraken_connection import get_open_orders
import json

orders = get_open_orders()
print("Open Orders:")
print(json.dumps(orders, indent=2))
