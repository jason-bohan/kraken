#!/usr/bin/env python3
"""Test script to find correct stock pair names on Kraken"""

from kraken_connection import get_ticker

def test_stock_pairs():
    """Test various stock pair formats to find the correct one."""
    
    # Common formats for stock pairs
    test_pairs = [
        # Direct USD pairs
        'AAPLUSD', 'TSLAUSD', 'MSFTUSD', 'GOOGLUSD', 'NVDAUSD',
        # With slashes
        'AAPL/USD', 'TSLA/USD', 'MSFT/USD', 'GOOGL/USD', 'NVDA/USD',
        # USDT pairs
        'AAPLUSDT', 'TSLAUSDT', 'MSFTUSDT', 'GOOGLUSDT', 'NVDAUSDT',
        # Different base currencies
        'XBTUSD', 'ETHUSD', 'SOLUSD',  # Test crypto pairs work
        # Stock with different quote currencies
        'AAPLUSDC', 'TSLAUSDC',
    ]
    
    print("🔍 Testing stock pair formats on Kraken...")
    print("=" * 60)
    
    working_pairs = []
    failed_pairs = []
    
    for pair in test_pairs:
        try:
            result = get_ticker(pair)
            if result and result != {}:
                price = result.get("c", [0])[0] if result.get("c") else "N/A"
                print(f"✅ {pair}: ${price}")
                working_pairs.append(pair)
            else:
                print(f"❌ {pair}: No data")
                failed_pairs.append(pair)
        except Exception as e:
            print(f"❌ {pair}: {e}")
            failed_pairs.append(pair)
    
    print("\n" + "=" * 60)
    print(f"📊 Results: {len(working_pairs)} working, {len(failed_pairs)} failed")
    
    if working_pairs:
        print("\n✅ Working pairs:")
        for pair in working_pairs:
            print(f"   {pair}")
    
    if failed_pairs:
        print("\n❌ Failed pairs:")
        for pair in failed_pairs:
            print(f"   {pair}")

if __name__ == "__main__":
    test_stock_pairs()
