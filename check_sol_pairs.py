#!/usr/bin/env python3
"""Check available SOL pairs on Kraken."""
from kraken_connection import get_asset_pairs

pairs = get_asset_pairs()
print("SOL pairs found:")
for name, info in pairs.items():
    if 'SOL' in name.upper():
        print(f"  {name}: base={info.get('base')}, quote={info.get('quote')}, wsname={info.get('wsname')}")
