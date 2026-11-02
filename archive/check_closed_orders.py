#!/usr/bin/env python3
"""Check closed orders on Kraken."""
from kraken_connection import get_closed_orders
import json

orders = get_closed_orders()
print("Closed Orders:")
print(json.dumps(orders, indent=2))
