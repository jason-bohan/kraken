#!/usr/bin/env python3
"""
📊 Portfolio P&L Analyzer — Kraken Trading History
Analyzes complete trading history with charts, statistics, and performance metrics.

Features:
- 📈 Terminal charts for P&L visualization
- 📊 Trade history with emojis and details
- 📉 Win rate, profit factors, and statistics
- 🎯 Monthly/weekly performance breakdown
- 📱 Telegram summary reports

Usage:
    python3 portfolio_analyzer.py                    # analyze all trades
    python3 portfolio_analyzer.py --chart            # with terminal charts
    python3 portfolio_analyzer.py --export csv       # export to CSV
    python3 portfolio_analyzer.py --period 30d        # last 30 days
"""

import os
import time
import argparse
import json
import csv
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from kraken_connection import get_balance, get_ticker, get_trade_history, get_closed_orders

# 📱 Telegram settings
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# 📊 Chart settings
CHART_WIDTH = 60
CHART_HEIGHT = 15

# ─────────────────────────────────────────────
# 📈 KRAKEN API TRADE HISTORY
# ─────────────────────────────────────────────

def get_kraken_trade_history(period_days=None, count=100):
    """Get trade history from Kraken API."""
    try:
        # Get trades from Kraken API
        trades = get_trade_history(count)
        
        if not trades:
            print("  📝 No trades found in Kraken API")
            return []
        
        # Filter by period if specified
        if period_days:
            cutoff_timestamp = int((datetime.now() - timedelta(days=period_days)).timestamp())
            trades = [t for t in trades if int(t["time"]) >= cutoff_timestamp]
        
        # Convert to our format
        formatted_trades = []
        for trade in trades:
            # Convert timestamp to datetime
            trade_time = datetime.fromtimestamp(int(trade["time"]))
            
            # Extract symbol from pair (e.g., "XBTUSD" -> "BTC")
            pair = trade.get("pair", "")
            symbol = pair.replace("XBT", "BTC").replace("XETH", "ETH").replace("ZUSD", "USD")
            
            # Store raw trade data for later P&L calculation
            formatted_trades.append({
                "timestamp": trade_time.isoformat(),
                "symbol": symbol,
                "pair": pair,
                "type": trade.get("type", ""),
                "price": float(trade.get("price", 0)),
                "volume": float(trade.get("vol", 0)),
                "cost": float(trade.get("cost", 0)),
                "fee": float(trade.get("fee", 0)),
                "trade_id": trade.get("id", ""),
                "raw_data": trade
            })
        
        return formatted_trades
        
    except Exception as e:
        print(f"  ⚠️ Error getting Kraken trade history: {e}")
        return []

def identify_trade_source(trade):
    """Identify which bot made the trade based on timing and patterns."""
    timestamp = trade.get('timestamp', '')
    pair = trade.get('pair', '')
    
    # Convert timestamp to datetime for analysis
    try:
        trade_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    except:
        trade_time = datetime.now()
    
    # Bot identification patterns
    hour = trade_time.hour
    
    # Manual trades (odd hours, irregular patterns)
    if hour in [0, 1, 2, 3, 4, 5, 22, 23]:  # Late night/early morning
        return "Manual"
    
    # Bot patterns by pair and time
    if 'BTC' in pair or 'XBT' in pair:
        if 6 <= hour <= 21:  # Trading hours
            return "BTC Bot"
    elif 'ETH' in pair:
        if 6 <= hour <= 21:
            return "ETH Bot"
    elif 'SOL' in pair:
        if 6 <= hour <= 21:
            return "SOL Bot"
    elif 'ADA' in pair:
        if 6 <= hour <= 21:
            return "ADA Bot"
    elif 'DOT' in pair:
        if 6 <= hour <= 21:
            return "DOT Bot"
    elif 'SOXS' in pair:  # Leveraged ETF
        if 9 <= hour <= 16:  # Stock market hours
            return "Stock Bot"
    
    # Default to generic bot
    return "Unknown Bot"

def calculate_realized_pnl(trades):
    """Calculate realized P&L by matching buy/sell pairs with bot tracking."""
    # Group trades by symbol
    symbol_trades = defaultdict(list)
    for trade in trades:
        # Add bot identification
        trade['bot_source'] = identify_trade_source(trade)
        symbol_trades[trade['symbol']].append(trade)
    
    # Sort trades by timestamp for each symbol
    for symbol in symbol_trades:
        symbol_trades[symbol].sort(key=lambda x: x['timestamp'])
    
    pnl_trades = []
    
    for symbol, symbol_trade_list in symbol_trades.items():
        # Track open positions
        open_positions = []
        
        for trade in symbol_trade_list:
            if trade['type'] == 'buy':
                # Add to open positions
                open_positions.append({
                    'entry_price': trade['price'],
                    'volume': trade['volume'],
                    'entry_cost': trade['cost'],
                    'entry_fee': trade['fee'],
                    'entry_time': trade['timestamp'],
                    'bot_source': trade['bot_source']
                })
            elif trade['type'] == 'sell' and open_positions:
                # Match with earliest open position (FIFO)
                position = open_positions.pop(0)
                
                # Calculate P&L
                sell_revenue = trade['cost']
                sell_fee = trade['fee']
                buy_cost = position['entry_cost']
                buy_fee = position['entry_fee']
                
                pnl_amount = sell_revenue - sell_fee - buy_cost - buy_fee
                pnl_pct = (pnl_amount / buy_cost) * 100 if buy_cost > 0 else 0
                
                # Use the bot that made the buy (entry) as the primary bot
                bot_source = position['bot_source']
                
                pnl_trades.append({
                    'timestamp': trade['timestamp'],
                    'symbol': symbol,
                    'pair': trade['pair'],
                    'type': 'sell',
                    'entry_price': position['entry_price'],
                    'exit_price': trade['price'],
                    'shares': trade['volume'],
                    'pnl_amount': pnl_amount,
                    'pnl_pct': pnl_pct,
                    'reason': f"{bot_source} (buy: {position['entry_time'][:10]}, sell: {trade['timestamp'][:10]})",
                    'fee': buy_fee + sell_fee,
                    'cost': sell_revenue,
                    'bot_source': bot_source
                })
        
        # Mark remaining open positions
        for position in open_positions:
            pnl_trades.append({
                'timestamp': position['entry_time'],
                'symbol': symbol,
                'pair': f"{symbol}USD",
                'type': 'open',
                'entry_price': position['entry_price'],
                'exit_price': 0,
                'shares': position['volume'],
                'pnl_amount': -(position['entry_cost'] + position['entry_fee']),
                'pnl_pct': 0,
                'reason': f"{position['bot_source']} - Open position",
                'fee': position['entry_fee'],
                'cost': position['entry_cost'],
                'bot_source': position['bot_source']
            })
    
    return pnl_trades

def get_kraken_orders_history(period_days=None, count=100):
    """Get closed orders history from Kraken API (more detailed)."""
    try:
        # Get closed orders from Kraken API
        orders = get_closed_orders(count)
        
        if not orders:
            print("  📝 No closed orders found in Kraken API")
            return []
        
        # Filter by period if specified
        if period_days:
            cutoff_timestamp = int((datetime.now() - timedelta(days=period_days)).timestamp())
            orders = [o for o in orders if int(o["time"]) >= cutoff_timestamp]
        
        # Convert to our format
        formatted_trades = []
        for order in orders:
            # Convert timestamp to datetime
            order_time = datetime.fromtimestamp(int(order["time"]))
            
            # Extract symbol from pair
            pair = order.get("pair", "")
            symbol = pair.replace("XBT", "BTC").replace("XETH", "ETH").replace("ZUSD", "USD")
            
            # Calculate P&L
            order_type = order.get("type", "")
            cost = order.get("cost", 0)
            fee = order.get("fee", 0)
            
            if order_type == "sell":
                pnl_amount = cost - fee
                pnl_pct = 0
            else:
                pnl_amount = -(cost + fee)
                pnl_pct = 0
            
            formatted_trades.append({
                "timestamp": order_time.isoformat(),
                "symbol": symbol,
                "pair": pair,
                "type": order_type,
                "entry_price": order.get("price", 0),
                "exit_price": order.get("price", 0) if order_type == "sell" else 0,
                "shares": order.get("vol_exec", 0),
                "pnl_amount": pnl_amount,
                "pnl_pct": pnl_pct,
                "reason": f"Order {order_type.title()}",
                "fee": fee,
                "cost": cost,
                "status": order.get("status", "closed")
            })
        
        return formatted_trades
        
    except Exception as e:
        print(f"  ⚠️ Error getting Kraken orders history: {e}")
        return []

# ─────────────────────────────────────────────
# 📊 TERMINAL CHARTS
# ─────────────────────────────────────────────

def create_pnl_chart(pnl_values, width=CHART_WIDTH, height=CHART_HEIGHT):
    """Create a terminal P&L chart."""
    if not pnl_values:
        return "No data for chart"
    
    # Calculate cumulative P&L
    cumulative = []
    running_total = 0
    for pnl in pnl_values:
        running_total += pnl
        cumulative.append(running_total)
    
    # Find min/max for scaling
    min_val = min(cumulative)
    max_val = max(cumulative)
    range_val = max_val - min_val
    
    if range_val == 0:
        range_val = 1  # Avoid division by zero
    
    chart_lines = []
    
    # Create chart
    for row in range(height, -1, -1):
        line = ""
        threshold = min_val + (range_val * row / height)
        
        for i, value in enumerate(cumulative):
            if value >= threshold:
                if value > 0:
                    line += "🟢"
                elif value < 0:
                    line += "🔴"
                else:
                    line += "⚪"
            else:
                line += " "
        
        # Add scale labels
        if row == height:
            scale = f"${max_val:+.2f}"
        elif row == 0:
            scale = f"${min_val:+.2f}"
        elif row == height // 2:
            scale = f"${(min_val + max_val)/2:+.2f}"
        else:
            scale = "    "
        
        chart_lines.append(f"{scale:10} │{line}")
    
    # Add bottom axis
    chart_lines.append("          └" + "─" * min(width, len(cumulative)))
    
    return "\n".join(chart_lines)

def create_bar_chart(data, labels, width=CHART_WIDTH):
    """Create a terminal bar chart."""
    if not data or not labels:
        return "No data for chart"
    
    max_val = max(abs(v) for v in data) if data else 1
    if max_val == 0:
        max_val = 1
    
    chart_lines = []
    
    for i, (value, label) in enumerate(zip(data, labels)):
        bar_length = int(abs(value) / max_val * width)
        
        if value > 0:
            bar = "🟢" * bar_length
            prefix = f"${value:+8.2f} │"
        elif value < 0:
            bar = "🔴" * bar_length
            prefix = f"${value:+8.2f} │"
        else:
            bar = "⚪"
            prefix = f"${value:+8.2f} │"
        
        chart_lines.append(f"{prefix}{bar} {label}")
    
    return "\n".join(chart_lines)

# ─────────────────────────────────────────────
# 📈 ANALYSIS FUNCTIONS
# ─────────────────────────────────────────────

def analyze_performance(trades):
    """Analyze trading performance metrics."""
    if not trades:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0,
            "max_win": 0,
            "max_loss": 0,
            "sharpe_ratio": 0
        }
    
    winning_trades = [t for t in trades if t.get('pnl_amount', 0) > 0]
    losing_trades = [t for t in trades if t.get('pnl_amount', 0) < 0]
    
    total_pnl = sum(t.get('pnl_amount', 0) for t in trades)
    total_wins = sum(t.get('pnl_amount', 0) for t in winning_trades)
    total_losses = abs(sum(t.get('pnl_amount', 0) for t in losing_trades))
    
    win_rate = len(winning_trades) / len(trades) * 100 if trades else 0
    avg_win = total_wins / len(winning_trades) if winning_trades else 0
    avg_loss = total_losses / len(losing_trades) if losing_trades else 0
    profit_factor = total_wins / total_losses if total_losses > 0 else 0
    
    max_win = max((t.get('pnl_amount', 0) for t in winning_trades), default=0)
    max_loss = min((t.get('pnl_amount', 0) for t in losing_trades), default=0)
    
    # Simple Sharpe ratio (annualized)
    if trades:
        returns = [t.get('pnl_pct', 0) for t in trades]
        avg_return = sum(returns) / len(returns)
        return_std = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5
        sharpe_ratio = (avg_return / return_std * (252 ** 0.5)) if return_std > 0 else 0
    else:
        sharpe_ratio = 0
    
    return {
        "total_trades": len(trades),
        "winning_trades": len(winning_trades),
        "losing_trades": len(losing_trades),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "max_win": max_win,
        "max_loss": max_loss,
        "sharpe_ratio": sharpe_ratio
    }

def analyze_by_symbol(trades):
    """Analyze performance by trading symbol."""
    symbol_stats = defaultdict(lambda: {"trades": [], "pnl": 0, "wins": 0, "losses": 0})
    
    for trade in trades:
        symbol = trade.get('symbol', 'UNKNOWN')
        symbol_stats[symbol]["trades"].append(trade)
        symbol_stats[symbol]["pnl"] += trade.get('pnl_amount', 0)
        
        if trade.get('pnl_amount', 0) > 0:
            symbol_stats[symbol]["wins"] += 1
        elif trade.get('pnl_amount', 0) < 0:
            symbol_stats[symbol]["losses"] += 1
    
    # Calculate metrics for each symbol
    results = {}
    for symbol, stats in symbol_stats.items():
        total_trades = len(stats["trades"])
        win_rate = (stats["wins"] / total_trades * 100) if total_trades > 0 else 0
        
        results[symbol] = {
            "total_trades": total_trades,
            "total_pnl": stats["pnl"],
            "win_rate": win_rate,
            "wins": stats["wins"],
            "losses": stats["losses"]
        }
    
    return results

def analyze_by_period(trades, period_type='monthly'):
    """Analyze performance by time period."""
    period_stats = defaultdict(lambda: {"trades": [], "pnl": 0})
    
    for trade in trades:
        try:
            trade_date = datetime.fromisoformat(trade.get('timestamp', ''))
            
            if period_type == 'monthly':
                period_key = trade_date.strftime("%Y-%m")
            elif period_type == 'weekly':
                period_key = f"{trade_date.year}-W{trade_date.isocalendar()[1]:02d}"
            elif period_type == 'daily':
                period_key = trade_date.strftime("%Y-%m-%d")
            else:
                period_key = trade_date.strftime("%Y-%m")
            
            period_stats[period_key]["trades"].append(trade)
            period_stats[period_key]["pnl"] += trade.get('pnl_amount', 0)
            
        except:
            continue
    
    return dict(period_stats)

# ─────────────────────────────────────────────
# 📊 DISPLAY FUNCTIONS
# ─────────────────────────────────────────────

def display_trade_list(trades, limit=20):
    """Display detailed trade list with emojis."""
    if not trades:
        print("  📝 No trades found")
        return
    
    print(f"\n  📋 Recent Trades (Last {min(limit, len(trades))}):")
    print("  " + "="*80)
    
    # Sort by timestamp (newest first)
    sorted_trades = sorted(trades, key=lambda x: x.get('timestamp', ''), reverse=True)
    
    for i, trade in enumerate(sorted_trades[:limit]):
        pnl = trade.get('pnl_amount', 0)
        pnl_pct = trade.get('pnl_pct', 0)
        symbol = trade.get('symbol', 'UNKNOWN')
        reason = trade.get('reason', 'Unknown')
        
        # Choose emoji based on P&L
        if pnl > 0:
            emoji = "🟢"
            status = "PROFIT"
        elif pnl < 0:
            emoji = "🔴"
            status = "LOSS"
        else:
            emoji = "⚪"
            status = "BREAKEVEN"
        
        # Format timestamp
        try:
            timestamp = datetime.fromisoformat(trade.get('timestamp', '')).strftime("%m/%d %H:%M")
        except:
            timestamp = "Unknown"
        
        print(f"  {emoji} [{timestamp}] {symbol:6} | ${pnl:+8.2f} ({pnl_pct:+6.2f}%) | {status:10} | {reason}")

def display_performance_summary(performance):
    """Display performance metrics summary."""
    print(f"\n  📊 Performance Summary:")
    print("  " + "="*50)
    
    print(f"  📈 Total Trades:     {performance['total_trades']}")
    print(f"  🎯 Win Rate:         {performance['win_rate']:.1f}%")
    print(f"  💰 Total P&L:        ${performance['total_pnl']:+.2f}")
    print(f"  📊 Avg Win:          ${performance['avg_win']:.2f}")
    print(f"  📉 Avg Loss:         ${performance['avg_loss']:.2f}")
    print(f"  🎪 Profit Factor:    {performance['profit_factor']:.2f}")
    print(f"  🏆 Max Win:          ${performance['max_win']:.2f}")
    print(f"  💥 Max Loss:         ${performance['max_loss']:.2f}")
    print(f"  📈 Sharpe Ratio:     {performance['sharpe_ratio']:.2f}")

def display_symbol_breakdown(symbol_stats):
    """Display performance breakdown by symbol."""
    if not symbol_stats:
        return
    
    print(f"\n  📈 Performance by Symbol:")
    print("  " + "="*60)
    
    # Sort by total P&L
    sorted_symbols = sorted(symbol_stats.items(), key=lambda x: x[1]['total_pnl'], reverse=True)
    
    for symbol, stats in sorted_symbols:
        pnl = stats['total_pnl']
        trades = stats['total_trades']
        win_rate = stats['win_rate']
        
        if pnl > 0:
            emoji = "🟢"
        elif pnl < 0:
            emoji = "🔴"
        else:
            emoji = "⚪"
        
        print(f"  {emoji} {symbol:6} | ${pnl:+8.2f} | {trades:3} trades | {win_rate:5.1f}% win rate")

def analyze_by_bot(trades):
    """Analyze performance by bot source."""
    bot_stats = defaultdict(lambda: {
        'trades': 0,
        'wins': 0,
        'losses': 0,
        'total_pnl': 0,
        'total_fees': 0,
        'symbols': set(),
        'avg_win': 0,
        'avg_loss': 0,
        'max_win': 0,
        'max_loss': 0
    })
    
    for trade in trades:
        bot = trade.get('bot_source', 'Unknown Bot')
        pnl = trade.get('pnl_amount', 0)
        fee = trade.get('fee', 0)
        symbol = trade.get('symbol', '')
        
        stats = bot_stats[bot]
        stats['trades'] += 1
        stats['total_pnl'] += pnl
        stats['total_fees'] += fee
        stats['symbols'].add(symbol)
        
        if pnl > 0:
            stats['wins'] += 1
            stats['max_win'] = max(stats['max_win'], pnl)
        elif pnl < 0:
            stats['losses'] += 1
            stats['max_loss'] = min(stats['max_loss'], pnl)
    
    # Calculate averages and win rates
    for bot, stats in bot_stats.items():
        if stats['wins'] > 0:
            wins = [t['pnl_amount'] for t in trades if t.get('bot_source') == bot and t['pnl_amount'] > 0]
            stats['avg_win'] = sum(wins) / len(wins)
        
        if stats['losses'] > 0:
            losses = [t['pnl_amount'] for t in trades if t.get('bot_source') == bot and t['pnl_amount'] < 0]
            stats['avg_loss'] = sum(losses) / len(losses)
        
        stats['win_rate'] = (stats['wins'] / stats['trades'] * 100) if stats['trades'] > 0 else 0
        stats['symbols'] = list(stats['symbols'])
    
    return dict(bot_stats)

def display_bot_breakdown(bot_stats):
    """Display bot performance breakdown."""
    if not bot_stats:
        return
    
    print(f"\n  🤖 Performance by Bot:")
    print(f"  " + "="*80)
    
    # Sort by total P&L
    sorted_bots = sorted(bot_stats.items(), key=lambda x: x[1]['total_pnl'], reverse=True)
    
    for bot, stats in sorted_bots:
        pnl_color = "🟢" if stats['total_pnl'] >= 0 else "🔴"
        win_rate_color = "🟢" if stats['win_rate'] >= 50 else "🔴"
        
        print(f"  {pnl_color} {bot:<15} | ${stats['total_pnl']:>8.2f} | {stats['trades']:>3} trades | {win_rate_color} {stats['win_rate']:>5.1f}% win rate")
        print(f"     Symbols: {', '.join(stats['symbols'])}")
        print(f"     Avg Win: ${stats['avg_win']:>6.2f} | Avg Loss: ${stats['avg_loss']:>6.2f}")
        print(f"     Max Win: ${stats['max_win']:>6.2f} | Max Loss: ${stats['max_loss']:>6.2f}")
        print(f"     Total Fees: ${stats['total_fees']:>6.2f}")
        print(f"  " + "-"*80)

def display_period_breakdown(period_stats, period_type='monthly'):
    """Display performance by time period."""
    if not period_stats:
        return
    
    print(f"\n  📅 {period_type.title()} Performance:")
    print("  " + "="*50)
    
    # Sort by period
    sorted_periods = sorted(period_stats.items())
    
    for period, stats in sorted_periods:
        pnl = stats['pnl']
        trades = len(stats['trades'])
        
        if pnl > 0:
            emoji = "🟢"
        elif pnl < 0:
            emoji = "🔴"
        else:
            emoji = "⚪"
        
        print(f"  {emoji} {period} | ${pnl:+8.2f} | {trades:3} trades")

# ─────────────────────────────────────────────
# 📱 TELEGRAM NOTIFICATIONS
# ─────────────────────────────────────────────

def send_telegram_summary(performance, symbol_stats, period_stats):
    """Send summary to Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    
    try:
        import requests
        
        msg = f"📊 *Portfolio Analysis Summary*\n\n"
        msg += f"📈 Total Trades: {performance['total_trades']}\n"
        msg += f"🎯 Win Rate: {performance['win_rate']:.1f}%\n"
        msg += f"💰 Total P&L: ${performance['total_pnl']:+.2f}\n"
        msg += f"🎪 Profit Factor: {performance['profit_factor']:.2f}\n"
        msg += f"📈 Sharpe Ratio: {performance['sharpe_ratio']:.2f}\n\n"
        
        # Top performing symbol
        if symbol_stats:
            best_symbol = max(symbol_stats.items(), key=lambda x: x[1]['total_pnl'])
            msg += f"🏆 Best: {best_symbol[0]} (${best_symbol[1]['total_pnl']:+.2f})\n"
        
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=8
        )
        
    except Exception as e:
        print(f"  ⚠️ Telegram error: {e}")

# ─────────────────────────────────────────────
# 📊 EXPORT FUNCTIONS
# ─────────────────────────────────────────────

def export_to_csv(trades, filename="portfolio_export.csv"):
    """Export trades to CSV file."""
    if not trades:
        print("  📝 No trades to export")
        return
    
    try:
        with open(filename, 'w', newline='') as csvfile:
            fieldnames = ['timestamp', 'symbol', 'pnl_amount', 'pnl_pct', 'reason']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            writer.writeheader()
            for trade in trades:
                writer.writerow({
                    'timestamp': trade.get('timestamp', ''),
                    'symbol': trade.get('symbol', ''),
                    'pnl_amount': trade.get('pnl_amount', 0),
                    'pnl_pct': trade.get('pnl_pct', 0),
                    'reason': trade.get('reason', '')
                })
        
        print(f"  ✅ Exported {len(trades)} trades to {filename}")
        
    except Exception as e:
        print(f"  ❌ Export error: {e}")

# ─────────────────────────────────────────────
# 📊 MAIN ANALYSIS FUNCTION
# ─────────────────────────────────────────────

def analyze_portfolio(show_chart=False, period_days=None, export_format=None):
    """Main portfolio analysis function using Kraken API."""
    print("📊 Portfolio P&L Analyzer — Kraken API Trading History")
    print("=" * 60)
    
    # Get trade history from Kraken API
    print("  🔄 Fetching trade history from Kraken API...")
    
    # Try both trade history and closed orders for comprehensive data
    api_trades = get_kraken_trade_history(period_days, count=200)
    api_orders = get_kraken_orders_history(period_days, count=200)
    
    # Combine both sources (remove duplicates)
    all_trades = api_trades + api_orders
    
    if not all_trades:
        print("  📝 No trading history found in Kraken API")
        print("  💡 Make sure you have API permissions and recent trading activity")
        return
    
    # Remove duplicates by timestamp and pair
    unique_trades = {}
    for trade in all_trades:
        key = f"{trade['timestamp']}_{trade['pair']}_{trade['type']}"
        if key not in unique_trades:
            unique_trades[key] = trade
    
    trades = list(unique_trades.values())
    
    print(f"  📊 Found {len(trades)} raw trades")
    
    # Calculate realized P&L by matching buy/sell pairs
    print("  🔄 Calculating realized P&L...")
    pnl_trades = calculate_realized_pnl(trades)
    
    if not pnl_trades:
        print("  📝 No completed trades found (only buys without sells)")
        return
    
    # Filter by period
    if period_days:
        print(f"  📅 Analyzing last {period_days} days ({len(pnl_trades)} completed trades)")
    else:
        print(f"  📅 Analyzing all {len(pnl_trades)} completed trades")
    
    # Performance analysis
    performance = analyze_performance(pnl_trades)
    display_performance_summary(performance)
    
    # Symbol breakdown
    symbol_stats = analyze_by_symbol(pnl_trades)
    display_symbol_breakdown(symbol_stats)
    
    # Bot breakdown
    bot_stats = analyze_by_bot(pnl_trades)
    display_bot_breakdown(bot_stats)
    
    # Period breakdown
    monthly_stats = analyze_by_period(pnl_trades, 'monthly')
    display_period_breakdown(monthly_stats, 'monthly')
    
    # Trade list
    display_trade_list(pnl_trades)
    
    # Charts
    if show_chart and len(pnl_trades) > 1:
        print(f"\n  📈 Cumulative P&L Chart:")
        pnl_values = [t.get('pnl_amount', 0) for t in pnl_trades]
        chart = create_pnl_chart(pnl_values)
        print(chart)
        
        # Symbol performance chart
        if len(symbol_stats) > 1:
            print(f"\n  📊 Symbol Performance Chart:")
            symbols = list(symbol_stats.keys())
            pnl_by_symbol = [symbol_stats[s]['total_pnl'] for s in symbols]
            symbol_chart = create_bar_chart(pnl_by_symbol, symbols)
            print(symbol_chart)
    
    # Export
    if export_format == 'csv':
        export_to_csv(pnl_trades)
    
    # Telegram summary
    send_telegram_summary(performance, symbol_stats, monthly_stats)
    
    print(f"\n  ✅ Analysis complete!")
    print(f"  📊 Total analyzed: {len(pnl_trades)} completed trades from {len(trades)} raw trades")

# ─────────────────────────────────────────────
# 🎯 MAIN ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Portfolio P&L Analyzer")
    parser.add_argument("--chart", action="store_true", help="Show terminal charts")
    parser.add_argument("--period", type=str, help="Time period (e.g., 30d, 7d, 1d)")
    parser.add_argument("--export", type=str, choices=['csv'], help="Export format")
    args = parser.parse_args()
    
    # Parse period
    period_days = None
    if args.period:
        if args.period.endswith('d'):
            period_days = int(args.period[:-1])
        elif args.period.endswith('w'):
            period_days = int(args.period[:-1]) * 7
        elif args.period.endswith('m'):
            period_days = int(args.period[:-1]) * 30
    
    analyze_portfolio(
        show_chart=args.chart,
        period_days=period_days,
        export_format=args.export
    )
