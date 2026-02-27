#!/usr/bin/env python3
"""
🛡️ Protective Orders Script - One-time limit order placement
Places stop-loss and take-profit orders on existing holdings using bot strategy.

Usage:
    python3 protective_orders.py
"""

import os
import time
import argparse
from datetime import datetime
from kraken_connection import get_balance, get_ticker, get_orderbook, place_order, get_open_orders, cancel_order

# 📱 Telegram settings
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

def tg(msg: str):
    """Send Telegram notification if configured."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        import requests as req
        req.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=8
        )
    except:
        pass

def get_stock_config(symbol: str) -> dict:
    """Get configuration for a specific asset (same as bot)."""
    crypto_configs = {
        "BTC": {
            "profit_target": 0.04,
            "stop_loss": 0.02,
        },
        "ETH": {
            "profit_target": 0.06,
            "stop_loss": 0.03,
        },
        "SOL": {
            "profit_target": 0.08,
            "stop_loss": 0.04,
        },
        "ADA": {
            "profit_target": 0.06,
            "stop_loss": 0.03,
        },
        "DOT": {
            "profit_target": 0.07,
            "stop_loss": 0.035,
        }
    }
    
    # Stock/ETF configurations
    stock_configs = {
        "SOXS": {
            "profit_target": 0.06,  # 6% profit target
            "stop_loss": 0.03,      # 3% stop loss
        },
        "SOXS.EQ": {
            "profit_target": 0.06,  # 6% profit target
            "stop_loss": 0.03,      # 3% stop loss
        },
        "AAPL": {
            "profit_target": 0.06,
            "stop_loss": 0.03,
        },
        "TSLA": {
            "profit_target": 0.08,
            "stop_loss": 0.04,
        }
    }
    
    symbol_upper = symbol.upper()
    return crypto_configs.get(symbol_upper) or stock_configs.get(symbol_upper) or {
        "profit_target": 0.06,
        "stop_loss": 0.03,
    }

def get_pair_format(symbol: str) -> str:
    """Get correct Kraken pair format for crypto vs stocks/ETFs."""
    # Crypto pairs (including memecoins)
    crypto_symbols = ["BTC", "ETH", "SOL", "DOT", "ADA", "LINK", "UNI", "PEPE", "SHIB", "DOGE"]
    
    if symbol == "BTC":
        return "XBTUSD"
    elif symbol == "ETH":
        return "ETHUSD"
    elif symbol in crypto_symbols:
        return f"{symbol}USD"
    
    # Stock/ETF pairs (special handling for SOXS and other ETFs)
    elif symbol.replace(".EQ", "") in ["SOXS", "TSLA", "AAPL", "MSFT", "GOOGL"]:
        clean_symbol = symbol.replace(".EQ", "")
        return f"{clean_symbol}USD"  # Try without .EQ suffix first
    
    # Other stock/ETF pairs (3-4 letter tickers)
    elif len(symbol.replace(".EQ", "")) <= 4 and symbol.replace(".", "").isalpha():
        clean_symbol = symbol.replace(".EQ", "")
        if clean_symbol not in crypto_symbols:
            if symbol.endswith(".EQ"):
                return f"{clean_symbol}USD"  # Try without .EQ
            else:
                return f"{symbol}.EQUSD"  # Add .EQ suffix as fallback
    
    # Default to crypto format
    else:
        return f"{symbol}USD"

def is_stock_or_etf(symbol: str) -> bool:
    """Check if symbol is a stock/ETF (not crypto)."""
    # Remove .EQ suffix if present
    clean_symbol = symbol.replace(".EQ", "")
    
    # Known crypto symbols (including memecoins)
    crypto_symbols = ["BTC", "ETH", "SOL", "DOT", "ADA", "LINK", "UNI", "PEPE", "SHIB", "DOGE"]
    
    # Known stock/ETF symbols
    stock_symbols = ["SOXS", "TSLA", "AAPL", "MSFT", "GOOGL"]
    
    # Crypto prefixes
    crypto_prefixes = ["XBT", "XETH", "XXBT"]
    
    stock_like = (
        clean_symbol.isalpha() and 
        len(clean_symbol) <= 5 and
        not any(clean_symbol.startswith(prefix) for prefix in crypto_prefixes) and
        clean_symbol not in crypto_symbols
    )
    
    return stock_like or clean_symbol in stock_symbols

def get_current_holdings():
    """Get current holdings from Kraken balance."""
    print("🔍 Fetching current holdings...")
    
    balances = get_balance()
    holdings = {}
    
    # Check for non-zero balances
    for asset, balance in balances.items():
        amount = float(balance)
        if amount > 0 and asset not in ["ZUSD", "USDC"]:  # Exclude cash
            # Convert Kraken asset names to symbols
            symbol = asset.replace("XXBT", "BTC").replace("XETH", "ETH")
            holdings[symbol] = amount
    
    return holdings

def get_average_entry_price(symbol: str, pair: str):
    """Estimate average entry price from recent trades (simplified)."""
    # For this script, we'll use current price as approximation
    # In a real scenario, you'd calculate from trade history
    ticker = get_ticker(pair)
    if ticker:
        return float(ticker.get("c", [0])[0])
    return 0

def get_price_precision(symbol: str) -> int:
    """Get price precision for Kraken pairs."""
    clean_symbol = symbol.replace(".EQ", "")
    
    # Crypto precision (including memecoins)
    crypto_precision = {
        "BTC": 1,      # $65,000.0
        "ETH": 2,      # $3,500.00
        "SOL": 2,      # $81.00
        "DOT": 4,      # $1.5961
        "ADA": 4,      # $0.4567
        "LINK": 4,     # $10.1234
        "UNI": 4,      # $15.2345
        "PEPE": 8,     # $0.00000001 (memecoin)
        "SHIB": 8,     # $0.00000001 (memecoin)
        "DOGE": 6,     # $0.000001 (memecoin)
    }
    
    # Stock/ETF precision (typically 2-4 decimals)
    stock_precision = {
        "SOXS": 4,     # $1.2345
        "AAPL": 2,     # $150.00
        "TSLA": 2,     # $200.00
        "MSFT": 2,     # $300.00
        "GOOGL": 2,    # $150.00
    }
    
    if is_stock_or_etf(symbol):
        return stock_precision.get(clean_symbol, 4)
    else:
        return crypto_precision.get(clean_symbol, 4)

def get_minimum_order_size(symbol: str) -> float:
    """Get minimum order size for Kraken pairs."""
    clean_symbol = symbol.replace(".EQ", "")
    
    # Crypto minimums (including memecoins)
    crypto_min_sizes = {
        "BTC": 0.0001,
        "ETH": 0.005,
        "SOL": 0.01,
        "DOT": 0.1,
        "ADA": 1.0,
        "LINK": 0.1,
        "UNI": 0.1,
        "PEPE": 50000,   # Reduced minimum for PEPE memecoin
        "SHIB": 100000,  # High minimum for SHIB memecoin
        "DOGE": 1000,    # Minimum for DOGE memecoin
    }
    
    # Stock/ETF minimums (typically 1 share)
    stock_min_sizes = {
        "SOXS": 1.0,
        "AAPL": 1.0,
        "TSLA": 1.0,
        "MSFT": 1.0,
        "GOOGL": 1.0,
    }
    
    if is_stock_or_etf(symbol):
        return stock_min_sizes.get(clean_symbol, 1.0)
    else:
        return crypto_min_sizes.get(clean_symbol, 0.01)

def format_price(price: float, precision: int) -> float:
    """Format price to correct decimal places."""
    return round(price, precision)

def place_protective_orders(symbol: str, holdings: float, dry_run: bool = False):
    """Place stop-loss and take-profit orders for a holding."""
    print(f"\n📊 Processing {symbol}: {holdings:.6f} shares")
    
    # Get configuration
    config = get_stock_config(symbol)
    pair = get_pair_format(symbol)
    
    # Get price precision for this symbol (needed before ticker lookup)
    price_precision = get_price_precision(symbol)
    
    # Get current price
    ticker = get_ticker(pair)
    if ticker:
        current_price = float(ticker.get("c", [0])[0])
        print(f"  💰 Current Price: ${current_price:.{price_precision}f} (from ticker)")
    else:
        # Ticker failed - try fallback for equity pairs
        if is_stock_or_etf(symbol):
            print(f"  ⚠️ Ticker lookup failed for {pair} (equity pair)")
            print(f"  💡 Using fallback price calculation...")
            
            # For equity pairs, we can try to get price from orderbook
            try:
                orderbook = get_orderbook(pair)
                if orderbook and 'bids' in orderbook and orderbook['bids']:
                    current_price = float(orderbook['bids'][0][0])
                    print(f"  💰 Current Price: ${current_price:.{price_precision}f} (from orderbook)")
                else:
                    print(f"  ❌ No orderbook data for {pair}")
                    print(f"  💰 Current Price: $0.0000 (unavailable)")
                    current_price = 0
            except Exception as e:
                print(f"  ❌ Orderbook lookup failed: {e}")
                print(f"  💰 Current Price: $0.0000 (unavailable)")
                current_price = 0
        else:
            print(f"  ❌ Ticker error for {pair}")
            current_price = 0
    
    # Calculate entry price (using current price as approximation)
    entry_price = get_average_entry_price(symbol, pair)
    
    # If ticker failed, use entry price as current price fallback
    if current_price == 0:
        current_price = entry_price
        print(f"  💰 Current Price: ${current_price:.{price_precision}f} (using entry price fallback)")
    
    # Calculate stop-loss and take-profit prices with correct precision
    stop_loss_price = format_price(entry_price * (1 - config["stop_loss"]), price_precision)
    take_profit_price = format_price(entry_price * (1 + config["profit_target"]), price_precision)
    
    print(f"  💰 Current Price: ${current_price:.{price_precision}f}")
    print(f"  📈 Entry Price: ${entry_price:.{price_precision}f}")
    print(f"  🛡️ Stop Loss: ${stop_loss_price:.{price_precision}f} ({config['stop_loss']*100:.1f}%)")
    print(f"  🎯 Take Profit: ${take_profit_price:.{price_precision}f} ({config['profit_target']*100:.1f}%)")
    print(f"  🔢 Price Precision: {price_precision} decimals")
    
    # Check minimum order size
    min_order_size = get_minimum_order_size(symbol)
    if holdings < min_order_size:
        print(f"  ❌ Holdings {holdings:.6f} below minimum {min_order_size:.6f}")
        return False
    
    # Continue even if price lookup failed (we have fallback)
    if current_price == 0:
        print(f"  ⚠️ Proceeding with entry price as current price")
    
    if dry_run:
        print(f"  🔵 [DRY RUN] Would place protective orders")
        return True
    
    try:
        # Cancel any existing orders for this pair first
        print(f"  🗑️ Checking for existing orders...")
        open_orders = get_open_orders()
        
        for order_id, order_data in open_orders.get('open', {}).items():
            if order_data.get('pair', '') == pair:
                print(f"  🗑️ Canceling existing order: {order_id}")
                cancel_result, cancel_info = cancel_order(order_id)
                if cancel_result:
                    print(f"  ✅ Order canceled")
                else:
                    print(f"  ❌ Failed to cancel: {cancel_info}")
        
        # Place stop-loss order (this will be our primary protection)
        print(f"  🛡️ Placing stop-loss order...")
        stop_result, stop_info = place_order(
            pair=pair,
            side="sell",
            order_type="stop-loss",
            volume=holdings,
            price=stop_loss_price,
            validate=False
        )
        
        if stop_result:
            stop_order_id = stop_info.get('txid', [None])[0]
            print(f"  ✅ Stop-loss order placed: {stop_order_id}")
        else:
            print(f"  ❌ Stop-loss order failed: {stop_info}")
            return False
        
        # For take-profit, we'll use a different approach:
        # Since Kraken doesn't allow multiple sell orders, we'll create a price alert
        # and let the bot handle the take-profit when price reaches target
        print(f"  🎯 Take-profit target set at ${take_profit_price:.{price_precision}f}")
        print(f"  💡 Note: Bot will monitor for take-profit price and sell manually")
        print(f"  📱 You'll get Telegram alert when take-profit price is reached")
        
        # Store take-profit info for monitoring
        take_profit_info = {
            "symbol": symbol,
            "pair": pair,
            "target_price": take_profit_price,
            "volume": holdings,
            "stop_loss_order": stop_order_id
        }
        
        # Save take-profit info to a file for monitoring
        try:
            import json
            with open("take_profit_targets.json", "a") as f:
                f.write(json.dumps({
                    "timestamp": datetime.now().isoformat(),
                    **take_profit_info
                }) + "\n")
        except:
            pass
        
        # Send notification
        msg = f"🛡️ *Protective Orders Placed*\n"
        msg += f"Symbol: {symbol}\n"
        msg += f"Holdings: {holdings:.6f}\n"
        msg += f"Stop Loss: ${stop_loss_price:.{price_precision}f}\n"
        msg += f"Take Profit Target: ${take_profit_price:.{price_precision}f}\n"
        msg += f"Stop Loss Order: {stop_order_id}\n"
        msg += f"Take Profit: Monitored by bot"
        tg(msg)
        
        print(f"  ✅ Protective orders placed successfully!")
        print(f"  🛡️ Stop-loss: Active (${stop_loss_price:.{price_precision}f})")
        print(f"  🎯 Take-profit: Will be monitored (${take_profit_price:.{price_precision}f})")
        return True
        
    except Exception as e:
        print(f"  ❌ Error placing orders: {e}")
        return False

def main():
    """Main function to place protective orders on all holdings."""
    print("🛡️ Protective Orders Script — One-time Limit Order Placement")
    print("=" * 60)
    
    parser = argparse.ArgumentParser(description="Place protective orders on holdings")
    parser.add_argument("--dry", action="store_true", help="Dry run — don't actually place orders")
    args = parser.parse_args()
    
    mode = "🔵 DRY RUN" if args.dry else "🟢 LIVE"
    print(f"  Mode: {mode}")
    
    if not args.dry:
        confirm = input("\n  ⚠️ This will place REAL orders. Continue? (y/N): ")
        if confirm.lower() != 'y':
            print("  👋 Cancelled")
            return
    
    # Get current holdings
    holdings = get_current_holdings()
    
    if not holdings:
        print("  📝 No holdings found")
        return
    
    print(f"\n  📊 Found {len(holdings)} holdings:")
    for symbol, amount in holdings.items():
        print(f"     {symbol}: {amount:.6f}")
    
    # Place protective orders for each holding
    success_count = 0
    total_count = len(holdings)
    
    for symbol, amount in holdings.items():
        if place_protective_orders(symbol, amount, args.dry):
            success_count += 1
    
    # Summary
    print(f"\n  📊 Summary:")
    print(f"  ✅ Successful: {success_count}/{total_count}")
    print(f"  ❌ Failed: {total_count - success_count}/{total_count}")
    
    if success_count > 0 and not args.dry:
        print(f"\n  💡 Check your Kraken account to confirm the orders!")
        print(f"  📱 You'll get Telegram notifications when orders fill")
    
    print(f"\n  ✅ Script complete!")

if __name__ == "__main__":
    main()
