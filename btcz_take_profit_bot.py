#!/usr/bin/env python3
"""
💰 BTCZ Take Profit Bot — Stock Exchange
Manages BTCZ (2X Inverse Bitcoin ETF) positions with take profit and stop loss.

Strategy:
- TAKE PROFIT: Sell BTCZ when Bitcoin drops (BTCZ rises)
- STOP LOSS: Sell BTCZ when Bitcoin rises too much (BTCZ drops)
- DAILY TARGET: 10-15% profit on inverse ETF
- RISK MANAGEMENT: 8-10% stop loss to protect capital
- BTC CORRELATION: BTCZ moves opposite to Bitcoin

Perfect for:
- Managing existing BTCZ positions
- Automated profit taking on inverse ETF
- Risk management on leveraged ETFs
- Bitcoin inverse exposure without crypto exchange

Usage:
    python3 btcz_take_profit_bot.py --monitor       # Monitor BTCZ position
    python3 btcz_take_profit_bot.py --set-tp 6.00  # Set take profit at $6.00
    python3 btcz_take_profit_bot.py --set-sl 5.00  # Set stop loss at $5.00

Requires stock exchange API integration for your specific broker.
"""

import os
import time
import argparse
from datetime import datetime, timedelta

# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────
SYMBOL         = "BTCZ"       # BTCZ ETF symbol
CURRENT_PRICE   = 5.49          # Current BTCZ price from your interface
YOUR_BALANCE    = 1.0           # Your BTCZ balance
CURRENT_VALUE   = 19.38         # Current USD value

# Trading parameters
TAKE_PROFIT_PCT = 0.10          # 10% profit target
STOP_LOSS_PCT   = 0.08          # 8% stop loss
CHECK_INTERVAL   = 60             # seconds between checks
MIN_PROFIT_PCT  = 0.05          # Minimum 5% profit before considering sell

# Target prices
TAKE_PROFIT_PRICE = CURRENT_PRICE * (1 + TAKE_PROFIT_PCT)  # $6.04
STOP_LOSS_PRICE   = CURRENT_PRICE * (1 - STOP_LOSS_PCT)     # $5.05

# Telegram settings (optional)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────────
# TELEGRAM NOTIFICATIONS
# ─────────────────────────────────────────────
def tg(msg: str):
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

# ─────────────────────────────────────────────
# MOCK API FUNCTIONS (Replace with real broker API)
# ─────────────────────────────────────────────
def get_btcz_price() -> float:
    """Get current BTCZ price (replace with real API call)."""
    # This would be your broker's API call
    # For now, return the current price you showed me
    return CURRENT_PRICE

def get_btc_price() -> float:
    """Get current Bitcoin price (replace with real API call)."""
    # This would be your broker's API call
    # For now, use approximate BTC price
    return 69167.7

def place_btcz_sell_order(price: float, quantity: float, order_type: str) -> dict:
    """Place BTCZ sell order (replace with real broker API call)."""
    # This would be your broker's API call
    # For now, simulate the order
    order = {
        "success": True,
        "order_id": f"BTCZ_{int(time.time())}",
        "symbol": SYMBOL,
        "type": "sell",
        "order_type": order_type,
        "price": price,
        "quantity": quantity,
        "status": "placed"
    }
    
    print(f"  📊 Placing {order_type} sell order:")
    print(f"      💰 Price: ${price:.2f}")
    print(f"      📊 Quantity: {quantity} {SYMBOL}")
    print(f"      💵 Total Value: ${price * quantity:.2f}")
    
    return order

def check_open_orders() -> list:
    """Check existing open orders (replace with real broker API call)."""
    # This would be your broker's API call
    # For now, return empty (no open orders)
    return []

# ─────────────────────────────────────────────
# TRADING LOGIC
# ─────────────────────────────────────────────
def calculate_profit_loss(entry_price: float, current_price: float) -> dict:
    """Calculate profit/loss percentage."""
    if entry_price <= 0:
        return {"pct": 0, "amount": 0, "type": "none"}
    
    pct_change = (current_price - entry_price) / entry_price
    amount_change = (current_price - entry_price) * YOUR_BALANCE
    
    return {
        "pct": pct_change,
        "amount": amount_change,
        "type": "profit" if pct_change > 0 else "loss" if pct_change < 0 else "breakeven"
    }

def check_triggers() -> dict:
    """Check if take profit or stop loss should trigger."""
    current_price = get_btcz_price()
    btc_price = get_btc_price()
    
    # Calculate current P&L
    pnl = calculate_profit_loss(CURRENT_PRICE, current_price)
    
    # Check triggers
    take_profit_triggered = current_price >= TAKE_PROFIT_PRICE
    stop_loss_triggered = current_price <= STOP_LOSS_PRICE
    
    # Additional logic: Check if Bitcoin is dropping (good for BTCZ)
    btc_drop_5pct = btc_price < 69167.7 * 0.95  # BTC dropped 5%
    btc_drop_10pct = btc_price < 69167.7 * 0.90  # BTC dropped 10%
    
    return {
        "current_price": current_price,
        "btc_price": btc_price,
        "pnl": pnl,
        "take_profit_triggered": take_profit_triggered,
        "stop_loss_triggered": stop_loss_triggered,
        "btc_drop_5pct": btc_drop_5pct,
        "btc_drop_10pct": btc_drop_10pct,
        "recommendation": get_recommendation(current_price, btc_price, pnl)
    }

def get_recommendation(btcz_price: float, btc_price: float, pnl: dict) -> str:
    """Get trading recommendation based on current conditions."""
    
    # If already profitable enough
    if pnl["pct"] >= MIN_PROFIT_PCT:
        return "TAKE_PROFIT"
    
    # If Bitcoin is dropping significantly (good for BTCZ)
    if btc_price < 69167.7 * 0.90:  # BTC dropped 10%
        return "TAKE_PROFIT_BTC_DROP"
    
    # If losing too much
    if pnl["pct"] <= -STOP_LOSS_PCT:
        return "STOP_LOSS"
    
    # If Bitcoin is rising (bad for BTCZ)
    if btc_price > 69167.7 * 1.05:  # BTC rose 5%
        return "CONSIDER_STOP_LOSS"
    
    return "HOLD"

# ─────────────────────────────────────────────
# MAIN FUNCTIONS
# ─────────────────────────────────────────────
def monitor_mode():
    """Monitor BTCZ position and manage take profit/stop loss."""
    print("💰 BTCZ Take Profit Bot — Monitoring")
    print("=" * 50)
    print(f"  📊 Position: {YOUR_BALANCE} {SYMBOL}")
    print(f"  💰 Current Price: ${CURRENT_PRICE:.2f}")
    print(f"  💵 Current Value: ${CURRENT_VALUE:.2f}")
    print(f"  🎯 Take Profit: ${TAKE_PROFIT_PRICE:.2f} (+{TAKE_PROFIT_PCT*100:.1f}%)")
    print(f"  🛡️ Stop Loss: ${STOP_LOSS_PRICE:.2f} (-{STOP_LOSS_PCT*100:.1f}%)")
    print(f"  ⏱️  Checking every {CHECK_INTERVAL}s")
    print("=" * 50)
    
    tg(f"💰 *BTCZ Take Profit Bot started* - Monitoring {YOUR_BALANCE} {SYMBOL}")
    
    position_open = True
    entry_price = CURRENT_PRICE
    
    try:
        while position_open:
            # Check current conditions
            analysis = check_triggers()
            current_price = analysis["current_price"]
            btc_price = analysis["btc_price"]
            pnl = analysis["pnl"]
            recommendation = analysis["recommendation"]
            
            timestamp = datetime.now().strftime('%H:%M:%S')
            print(f"  [{timestamp}] 📊 {SYMBOL}: ${current_price:.2f} | BTC: ${btc_price:,.0f}")
            print(f"  📊 P&L: {pnl['pct']*100:+.2f}% (${pnl['amount']:+.2f}) | {recommendation}")
            
            # Check for sell triggers
            if analysis["take_profit_triggered"]:
                print(f"\n  🎯 TAKE PROFIT TRIGGERED!")
                print(f"  💰 Price reached: ${current_price:.2f}")
                print(f"  📊 Selling {YOUR_BALANCE} {SYMBOL} at market price")
                
                # Place sell order
                order = place_btcz_sell_order(current_price, YOUR_BALANCE, "market")
                if order["success"]:
                    print(f"  ✅ Take profit order placed: {order['order_id']}")
                    tg(f"🎯 *BTCZ Take Profit Executed*\n\n💰 Price: ${current_price:.2f}\n📊 P&L: {pnl['pct']*100:+.2f}%\n💵 Profit: ${pnl['amount']:+.2f}")
                    position_open = False
                    break
            
            elif analysis["stop_loss_triggered"]:
                print(f"\n  🛡️ STOP LOSS TRIGGERED!")
                print(f"  💰 Price reached: ${current_price:.2f}")
                print(f"  📊 Selling {YOUR_BALANCE} {SYMBOL} at market price")
                
                # Place sell order
                order = place_btcz_sell_order(current_price, YOUR_BALANCE, "market")
                if order["success"]:
                    print(f"  ✅ Stop loss order placed: {order['order_id']}")
                    tg(f"🛡️ *BTCZ Stop Loss Executed*\n\n💰 Price: ${current_price:.2f}\n📊 P&L: {pnl['pct']*100:+.2f}%\n💵 Loss: ${pnl['amount']:+.2f}")
                    position_open = False
                    break
            
            elif recommendation == "TAKE_PROFIT_BTC_DROP":
                print(f"\n  📉 BITCOIN DROPPING - TAKE PROFIT!")
                print(f"  💰 BTC: ${btc_price:,.0f} (dropped significantly)")
                print(f"  📊 BTCZ should rise - take profits now")
                
                # Place sell order
                order = place_btcz_sell_order(current_price, YOUR_BALANCE, "market")
                if order["success"]:
                    print(f"  ✅ BTC drop take profit placed: {order['order_id']}")
                    tg(f"📉 *BTCZ Take Profit (BTC Drop)*\n\n💰 BTC: ${btc_price:,.0f}\n💰 {SYMBOL}: ${current_price:.2f}\n📊 P&L: {pnl['pct']*100:+.2f}%")
                    position_open = False
                    break
            
            elif recommendation == "CONSIDER_STOP_LOSS":
                print(f"  ⚠️ Bitcoin rising - consider stop loss")
                print(f"  💰 BTC: ${btc_price:,.0f} (rising)")
                print(f"  📊 {SYMBOL} may drop further")
            
            # Show progress
            if position_open:
                print(f"  📊 Position: {YOUR_BALANCE} {SYMBOL} @ ${entry_price:.2f}")
                print(f"  🎯 Target: ${TAKE_PROFIT_PRICE:.2f} | 🛡️ Stop: ${STOP_LOSS_PRICE:.2f}")
                print(f"  ──────────────────────────────────")
            
            time.sleep(CHECK_INTERVAL)
            
    except KeyboardInterrupt:
        print(f"\n  🛑 BTCZ Take Profit Bot stopped by user")
        tg(f"🛑 *BTCZ Take Profit Bot stopped*")
    except Exception as e:
        print(f"\n  ❌ BTCZ Take Profit Bot error: {e}")
        tg(f"❌ *BTCZ Take Profit Bot error*: {e}")

def set_take_profit(price: float):
    """Set take profit order at specific price."""
    print(f"💰 Setting take profit for {SYMBOL} at ${price:.2f}")
    
    # Place take profit order
    order = place_btcz_sell_order(price, YOUR_BALANCE, "take_profit_limit")
    
    if order["success"]:
        print(f"  ✅ Take profit order placed: {order['order_id']}")
        print(f"  🎯 Will sell {YOUR_BALANCE} {SYMBOL} if price reaches ${price:.2f}")
        
        # Send notification
        msg = f"🎯 *BTCZ Take Profit Set*\n\n"
        msg += f"💰 Price: ${price:.2f}\n"
        msg += f"📊 Quantity: {YOUR_BALANCE} {SYMBOL}\n"
        msg += f"💵 Value: ${price * YOUR_BALANCE:.2f}\n"
        msg += f"🤖 BTCZ Take Profit Bot"
        tg(msg)
    else:
        print(f"  ❌ Failed to place take profit order")

def set_stop_loss(price: float):
    """Set stop loss order at specific price."""
    print(f"🛡️ Setting stop loss for {SYMBOL} at ${price:.2f}")
    
    # Place stop loss order
    order = place_btcz_sell_order(price, YOUR_BALANCE, "stop_loss_limit")
    
    if order["success"]:
        print(f"  ✅ Stop loss order placed: {order['order_id']}")
        print(f"  🛡️ Will sell {YOUR_BALANCE} {SYMBOL} if price drops to ${price:.2f}")
        
        # Send notification
        msg = f"🛡️ *BTCZ Stop Loss Set*\n\n"
        msg += f"💰 Price: ${price:.2f}\n"
        msg += f"📊 Quantity: {YOUR_BALANCE} {SYMBOL}\n"
        msg += f"💵 Value: ${price * YOUR_BALANCE:.2f}\n"
        msg += f"🤖 BTCZ Take Profit Bot"
        tg(msg)
    else:
        print(f"  ❌ Failed to place stop loss order")

def show_status():
    """Show current BTCZ position status."""
    current_price = get_btcz_price()
    btc_price = get_btc_price()
    pnl = calculate_profit_loss(CURRENT_PRICE, current_price)
    
    print("💰 BTCZ Position Status")
    print("=" * 40)
    print(f"  📊 Symbol: {SYMBOL}")
    print(f"  💰 Balance: {YOUR_BALANCE} {SYMBOL}")
    print(f"  💵 Entry Price: ${CURRENT_PRICE:.2f}")
    print(f"  💰 Current Price: ${current_price:.2f}")
    print(f"  📊 Current Value: ${current_price * YOUR_BALANCE:.2f}")
    print(f"  📈 P&L: {pnl['pct']*100:+.2f}% (${pnl['amount']:+.2f})")
    print(f"  🪙 BTC Price: ${btc_price:,.0f}")
    print(f"  🎯 Take Profit: ${TAKE_PROFIT_PRICE:.2f}")
    print(f"  🛡️ Stop Loss: ${STOP_LOSS_PRICE:.2f}")
    print(f"  📊 Recommendation: {get_recommendation(current_price, btc_price, pnl)}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="BTCZ Take Profit Bot")
    parser.add_argument("--monitor", action="store_true", help="Monitor BTCZ position")
    parser.add_argument("--set-tp", type=float, help="Set take profit at specific price")
    parser.add_argument("--set-sl", type=float, help="Set stop loss at specific price")
    parser.add_argument("--status", action="store_true", help="Show current status")
    args = parser.parse_args()
    
    if args.monitor:
        monitor_mode()
    elif args.set_tp:
        set_take_profit(args.set_tp)
    elif args.set_sl:
        set_stop_loss(args.set_sl)
    elif args.status:
        show_status()
    else:
        print("💰 BTCZ Take Profit Bot")
        print("Usage:")
        print("  python3 btcz_take_profit_bot.py --monitor     # Monitor position")
        print("  python3 btcz_take_profit_bot.py --set-tp 6.00 # Set take profit at $6.00")
        print("  python3 btcz_take_profit_bot.py --set-sl 5.00 # Set stop loss at $5.00")
        print("  python3 btcz_take_profit_bot.py --status     # Show current status")
        print("\nCurrent Position:")
        print(f"  📊 {YOUR_BALANCE} {SYMBOL} @ ${CURRENT_PRICE:.2f} = ${CURRENT_VALUE:.2f}")
        print(f"  🎯 Suggested TP: ${TAKE_PROFIT_PRICE:.2f} (+{TAKE_PROFIT_PCT*100:.1f}%)")
        print(f"  🛡️ Suggested SL: ${STOP_LOSS_PRICE:.2f} (-{STOP_LOSS_PCT*100:.1f}%)")

if __name__ == "__main__":
    main()
