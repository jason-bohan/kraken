#!/usr/bin/env python3
"""Check what trading pairs are available on Kraken"""

from kraken_connection import get_ticker

def check_available_pairs():
    """Check common trading pairs to see what's available."""
    
    # Test different categories
    test_pairs = {
        "Crypto": ["BTCUSD", "ETHUSD", "SOLUSD", "ADAUSD", "DOTUSD"],
        "Stock Formats": ["AAPLUSD", "TSLAUSD", "MSFTUSD", "AAPL/USD", "TSLA/USD"],
        "USDT Pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "Other": ["XBTUSD", "XXBTZUSD"]  # Alternative BTC pairs
    }
    
    print("🔍 Checking available trading pairs on Kraken...")
    print("=" * 60)
    
    available = []
    unavailable = []
    
    for category, pairs in test_pairs.items():
        print(f"\n📊 {category}:")
        for pair in pairs:
            try:
                result = get_ticker(pair)
                if result and result != {}:
                    price = result.get("c", [0])[0] if result.get("c") else "N/A"
                    print(f"  ✅ {pair}: ${price}")
                    available.append(pair)
                else:
                    print(f"  ❌ {pair}: No data")
                    unavailable.append(pair)
            except Exception as e:
                print(f"  ❌ {pair}: {str(e)[:50]}...")
                unavailable.append(pair)
    
    print("\n" + "=" * 60)
    print(f"📈 Summary: {len(available)} available, {len(unavailable)} unavailable")
    
    if available:
        print(f"\n✅ Available pairs ({len(available)}):")
        for pair in available:
            print(f"   {pair}")
    
    if unavailable:
        print(f"\n❌ Unavailable pairs ({len(unavailable)}):")
        for pair in unavailable[:10]:  # Show first 10
            print(f"   {pair}")
        if len(unavailable) > 10:
            print(f"   ... and {len(unavailable) - 10} more")

if __name__ == "__main__":
    check_available_pairs()
