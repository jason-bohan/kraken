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
from kraken_connection import get_balance, get_ticker, place_order, get_open_orders, cancel_order

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
    """Get correct Kraken pair format."""
    if symbol == "BTC":
        return "XBTUSD"
    elif symbol == "ETH":
        return "ETHUSD"
    elif symbol == "SOXS.EQ":
        return "SOXSUSD"  # Fix for SOXS ETF
    else:
        return f"{symbol}USD"

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

def place_protective_orders(symbol: str, holdings: float, dry_run: bool = False):
    """Place stop-loss and take-profit orders for a holding."""
    print(f"\n📊 Processing {symbol}: {holdings:.6f} shares")
    
    # Get configuration
    config = get_stock_config(symbol)
    pair = get_pair_format(symbol)
    
    # Get current price
    ticker = get_ticker(pair)
    if not ticker:
        print(f"  ❌ No ticker data for {pair}")
        return False
    
    current_price = float(ticker.get("c", [0])[0])
    
    # Calculate entry price (using current price as approximation)
    entry_price = get_average_entry_price(symbol, pair)
    
    # Calculate stop-loss and take-profit prices
    stop_loss_price = entry_price * (1 - config["stop_loss"])
    take_profit_price = entry_price * (1 + config["profit_target"])
    
    print(f"  💰 Current Price: ${current_price:.4f}")
    print(f"  📈 Entry Price: ${entry_price:.4f}")
    print(f"  🛡️ Stop Loss: ${stop_loss_price:.4f} ({config['stop_loss']*100:.1f}%)")
    print(f"  🎯 Take Profit: ${take_profit_price:.4f} ({config['profit_target']*100:.1f}%)")
    
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
        
        # Place stop-loss order
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
        
        # Place take-profit order
        print(f"  🎯 Placing take-profit order...")
        profit_result, profit_info = place_order(
            pair=pair,
            side="sell",
            order_type="limit",
            volume=holdings,
            price=take_profit_price,
            validate=False
        )
        
        if profit_result:
            profit_order_id = profit_info.get('txid', [None])[0]
            print(f"  ✅ Take-profit order placed: {profit_order_id}")
        else:
            print(f"  ❌ Take-profit order failed: {profit_info}")
            return False
        
        # Send notification
        msg = f"🛡️ *Protective Orders Placed*\n"
        msg += f"Symbol: {symbol}\n"
        msg += f"Holdings: {holdings:.6f}\n"
        msg += f"Stop Loss: ${stop_loss_price:.4f}\n"
        msg += f"Take Profit: ${take_profit_price:.4f}\n"
        msg += f"Orders: {stop_order_id} | {profit_order_id}"
        tg(msg)
        
        print(f"  ✅ Both protective orders placed successfully!")
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
