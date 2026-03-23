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
from kraken_connection import get_balance, get_ticker, get_trade_history, get_closed_orders, query_orders
from market_sentiment import get_market_sentiment, should_buy as check_sentiment, classify_sentiment

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
    """Get trade history from Kraken API with full pagination."""
    try:
        # Paginate through all trades (Kraken returns max 50 per call)
        all_trades = []
        for ofs in range(0, 2000, 50):
            batch = get_trade_history(count=50, ofs=ofs)
            if not batch:
                break
            all_trades.extend(batch)
            if len(batch) < 50:
                break
        trades = all_trades

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

            # Extract base symbol from pair by stripping quote currency
            pair = trade.get("pair", "")
            symbol = pair
            for suffix in ["USD", "EUR", "USDT"]:
                if symbol.endswith(suffix) and len(symbol) > len(suffix):
                    symbol = symbol[:-len(suffix)]
                    break

            # Rename Kraken internal asset names to standard symbols
            kraken_renames = {
                "XBT": "BTC", "XBTC": "BTC", "XXBT": "BTC", "XXBTZ": "BTC",
                "XETH": "ETH", "XETHZ": "ETH",
                "XDG": "DOGE", "XXDG": "DOGE",
                "BTCZZ": "BTCZ",
            }
            symbol = kraken_renames.get(symbol, symbol)
            
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

BOT_USERREFS = {
    1: "stock_swing_bot",
    2: "btc_swing_bot",
    3: "eth_swing_bot",
    4: "sol_swing_bot",
    5: "dot_swing_bot",
    6: "doge_swing_bot",
    7: "btc_momentum_bot",
    8: "dynamic_hft_bot",
    9: "universal_bull_bot",
    11: "correlation_bot",
    12: "momentum_scanner_bot",
}

# Cache: ordertxid -> userref (populated once per run)
_order_userref_cache: dict[str, int] = {}


def _populate_userref_cache(trades: list) -> None:
    """Batch-lookup ordertxids via QueryOrders to get userref for each trade."""
    ordertxids = list({
        t.get("raw_data", {}).get("ordertxid", "")
        for t in trades
    } - {""})
    if not ordertxids:
        return

    # Kraken QueryOrders accepts up to 20 txids per call
    import time as _time
    for i in range(0, len(ordertxids), 20):
        batch = ordertxids[i:i+20]
        orders = query_orders(batch)
        for txid, info in orders.items():
            ref = int(info.get("userref") or 0)
            if ref:
                _order_userref_cache[txid] = ref
        if i + 20 < len(ordertxids):
            _time.sleep(2)  # avoid rate limits


def identify_trade_source(trade):
    """Identify which bot made the trade using Kraken userref tag."""
    raw = trade.get('raw_data', {})
    ordertxid = raw.get('ordertxid', '')
    userref = _order_userref_cache.get(ordertxid, 0)
    if userref and userref in BOT_USERREFS:
        return BOT_USERREFS[userref]
    return "Manual"

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
    open_positions = {}  # Track current open positions by symbol
    
    for symbol, symbol_trade_list in symbol_trades.items():
        # Track open positions for this symbol
        symbol_open_positions = []
        
        for trade in symbol_trade_list:
            if trade['type'] == 'buy':
                # Add to open positions
                symbol_open_positions.append({
                    'entry_price': trade['price'],
                    'volume': trade['volume'],
                    'entry_cost': trade['cost'],
                    'entry_fee': trade['fee'],
                    'entry_time': trade['timestamp'],
                    'bot_source': trade['bot_source']
                })
            elif trade['type'] == 'sell' and symbol_open_positions:
                # Match with earliest open position (FIFO)
                position = symbol_open_positions.pop(0)
                
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
                    'reason': f"{bot_source} - Realized P&L (buy: {position['entry_time'][:10]}, sell: {trade['timestamp'][:10]})",
                    'fee': buy_fee + sell_fee,
                    'cost': sell_revenue,
                    'bot_source': bot_source,
                    'status': 'closed'
                })
        
        # Store remaining open positions for this symbol
        if symbol_open_positions:
            open_positions[symbol] = symbol_open_positions
    
    # Add open positions as holdings (not losses)
    for symbol, positions in open_positions.items():
        for position in positions:
            pnl_trades.append({
                'timestamp': position['entry_time'],
                'symbol': symbol,
                'pair': f"{symbol}USD",
                'type': 'open',
                'entry_price': position['entry_price'],
                'exit_price': 0,
                'shares': position['volume'],
                'pnl_amount': 0,  # Open positions have unrealized P&L, not realized loss
                'pnl_pct': 0,
                'reason': f"{position['bot_source']} - Open position (holding)",
                'fee': position['entry_fee'],
                'cost': position['entry_cost'],
                'bot_source': position['bot_source'],
                'status': 'open',
                'unrealized_cost': position['entry_cost'] + position['entry_fee']
            })
    
    return pnl_trades, open_positions

def get_pair_format(symbol: str) -> str:
    """Get correct Kraken pair format for a base symbol (e.g. 'SOL' -> 'SOLUSD')."""
    # Skip stocks/ETFs and non-crypto pairs
    stock_etf_symbols = {"SOXS", "BTCZ", "SOXS#1"}
    if symbol in stock_etf_symbols or "#" in symbol:
        return "SKIP_STOCK_ETF"

    # Kraken uses legacy pair names for some assets
    special_pairs = {
        "BTC": "XBTUSD",
        "ETH": "ETHUSD",
        "DOGE": "XDGUSD",
        "XBT": "XBTUSD",
        "XDG": "XDGUSD",
    }
    if symbol in special_pairs:
        return special_pairs[symbol]

    # All others: assume SYMBOL + USD
    return f"{symbol}USD"

def is_stock_or_etf(symbol: str) -> bool:
    """Check if symbol is a stock/ETF (not crypto)."""
    # Remove .EQ suffix if present
    clean_symbol = symbol.replace(".EQ", "")
    
    # Stocks/ETFs are typically 1-5 letters, no crypto prefixes
    crypto_prefixes = ["XBT", "XETH", "XXBT"]
    stock_like = (
        clean_symbol.isalpha() and 
        len(clean_symbol) <= 5 and
        not any(clean_symbol.startswith(prefix) for prefix in crypto_prefixes) and
        clean_symbol not in ["BTC", "ETH", "SOL", "DOT", "ADA", "LINK", "UNI"]
    )
    
    return stock_like

def calculate_unrealized_pnl(open_positions):
    """Calculate unrealized P&L for current holdings using current prices."""
    unrealized_trades = []
    
    for symbol, positions in open_positions.items():
        # Get current price for the symbol
        try:
            # Use the new pair format logic
            pair = get_pair_format(symbol)
            
            # Skip stocks/ETFs entirely
            if pair == "SKIP_STOCK_ETF":
                print(f"  ⚠️ Skipping stock/ETF {symbol} (not available on Kraken)")
                continue
            
            ticker = get_ticker(pair)
            if ticker:
                current_price = float(ticker.get("c", [0])[0])
            else:
                # Skip assets without price data
                print(f"  ⚠️ No price data for {symbol} ({pair}), skipping...")
                continue
        except:
            current_price = 0
        
        for position in positions:
            if current_price > 0:
                # Calculate unrealized P&L
                current_value = position['volume'] * current_price
                entry_cost = position['entry_cost']
                entry_fee = position['entry_fee']
                total_cost = entry_cost + entry_fee
                
                unrealized_pnl = current_value - total_cost
                unrealized_pct = (unrealized_pnl / total_cost) * 100 if total_cost > 0 else 0
                
                unrealized_trades.append({
                    'timestamp': position['entry_time'],
                    'symbol': symbol,
                    'pair': f"{symbol}USD",
                    'type': 'unrealized',
                    'entry_price': position['entry_price'],
                    'current_price': current_price,
                    'shares': position['volume'],
                    'pnl_amount': unrealized_pnl,
                    'pnl_pct': unrealized_pct,
                    'reason': f"{position['bot_source']} - Unrealized P&L",
                    'fee': position['entry_fee'],
                    'cost': position['entry_cost'],
                    'bot_source': position['bot_source'],
                    'status': 'open',
                    'current_value': current_value
                })
    
    return unrealized_trades

def get_kraken_orders_history(period_days=None, count=100):
    """Get closed orders history from Kraken API (more detailed)."""
    try:
        # get_closed_orders returns a dict {order_id: order_data}
        orders = get_closed_orders()

        if not orders:
            print("  📝 No closed orders found in Kraken API")
            return []

        cutoff_timestamp = None
        if period_days:
            cutoff_timestamp = int((datetime.now() - timedelta(days=period_days)).timestamp())

        formatted_trades = []
        for order_id, order_data in orders.items():
            close_time = order_data.get("closetm", 0)
            if not close_time:
                continue

            if cutoff_timestamp and int(close_time) < cutoff_timestamp:
                continue

            order_time = datetime.fromtimestamp(float(close_time))

            # Pair and type live inside "descr"
            descr = order_data.get("descr", {})
            pair = descr.get("pair", "")
            order_type = descr.get("type", "")

            # Extract base symbol by stripping quote currency
            symbol = pair
            for suffix in ["USD", "EUR", "USDT"]:
                if symbol.endswith(suffix) and len(symbol) > len(suffix):
                    symbol = symbol[:-len(suffix)]
                    break
            kraken_renames = {"XBT": "BTC", "XBTC": "BTC", "XETH": "ETH", "XDG": "DOGE", "XXBT": "BTC"}
            symbol = kraken_renames.get(symbol, symbol)

            cost = float(order_data.get("cost", 0))
            fee = float(order_data.get("fee", 0))
            vol_exec = float(order_data.get("vol_exec", 0))
            price = float(order_data.get("price", 0))

            formatted_trades.append({
                "timestamp": order_time.isoformat(),
                "symbol": symbol,
                "pair": pair,
                "type": order_type,
                "price": price,
                "vol": vol_exec,
                "volume": vol_exec,
                "cost": cost,
                "fee": fee,
                "trade_id": order_id,
                "userref": int(order_data.get("userref", 0)),
                "raw_data": order_data,
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
                    line += "🔵"
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
            bar = "🔵"
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
            emoji = "🔵"
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
            emoji = "🔵"
        
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
            emoji = "🔵"
        
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

def get_total_deposits() -> tuple[float, list]:
    """Get total USD + USDC deposited from Kraken ledger. Returns (net_deposited, entries)."""
    from kraken_connection import get_ledger
    import time as _time

    all_deposits = []
    for ofs in range(0, 500, 50):
        batch = get_ledger(ltype='deposit', ofs=ofs)
        if not batch:
            break
        all_deposits.extend(batch)
        if len(batch) < 50:
            break
        _time.sleep(1)

    all_withdrawals = []
    _time.sleep(1)
    for ofs in range(0, 500, 50):
        batch = get_ledger(ltype='withdrawal', ofs=ofs)
        if not batch:
            break
        all_withdrawals.extend(batch)
        if len(batch) < 50:
            break
        _time.sleep(1)

    usd_assets = {'ZUSD', 'USD', 'USDC'}
    total_in = sum(d['amount'] - d['fee'] for d in all_deposits if d['asset'] in usd_assets)
    total_out = sum(abs(w['amount']) + w['fee'] for w in all_withdrawals if w['asset'] in usd_assets)

    return total_in - total_out, all_deposits


def get_live_portfolio() -> tuple[float, float, list]:
    """
    Get real portfolio from actual Kraken balances + live prices.
    Returns (cash_usd, total_value, holdings_list).
    holdings_list: [{"asset", "volume", "price", "value", "pair"}, ...]
    """
    SKIP = {'ZUSD', 'USD', 'KFEE', 'NFTS'}
    PAIR_MAP = {
        'DOT': 'DOTUSD', 'HYPE': 'HYPEUSD', 'XDG': 'XDGUSD', 'PEPE': 'PEPEUSD',
        'RIVER': 'RIVERUSD', 'XETH': 'ETHUSD', 'SOL': 'SOLUSD', 'XXBT': 'XBTUSD',
        'ADA': 'ADAUSD', 'AVAX': 'AVAXUSD', 'LINK': 'LINKUSD', 'SUI': 'SUIUSD',
        'TAO': 'TAOUSD', 'NEAR': 'NEARUSD', 'ICP': 'ICPUSD', 'RENDER': 'RENDERUSD',
        'HBAR': 'HBARUSD', 'TRUMP': 'TRUMPUSD', 'ZRO': 'ZROUSD', 'XPL': 'XPLUSD',
        'FARTCOIN': 'FARTCOINUSD', 'KAS': 'KASUSD', 'FET': 'FETUSD',
        'PENGU': 'PENGUUSD', 'WAR': 'WARUSD', 'PUMP': 'PUMPUSD',
        'PLUME': 'PLUMEUSD', 'NIGHT': 'NIGHTUSD', 'XCN': 'XCNUSD',
        'ASTER': 'ASTERUSD', 'ICNT': 'ICNTUSD', 'IDOS': 'IDOSUSD',
        'ALCX': 'ALCXUSD', 'SENT': 'SENTUSD', 'WIF': 'WIFUSD',
        'BABY': 'BABYUSD', 'USELESS': 'USELESSUSD', 'XZEC': 'ZECUSD',
        'DOGE': 'XDGUSD', 'ETH': 'ETHUSD', 'BTC': 'XBTUSD',
    }
    NAMES = {
        'XXBT': 'BTC', 'XETH': 'ETH', 'XDG': 'DOGE', 'XXDG': 'DOGE',
        'XZEC': 'ZEC', 'XZECZ': 'ZEC',
    }

    balances = get_balance()
    cash = float(balances.get('ZUSD', 0)) + float(balances.get('USDC', 0))

    holdings = []
    for asset, bal_str in balances.items():
        vol = float(bal_str)
        if vol <= 0 or asset in SKIP:
            continue
        pair = PAIR_MAP.get(asset)
        if not pair:
            continue
        ticker = get_ticker(pair)
        if not ticker:
            continue
        price = float(ticker.get('c', [0])[0])
        value = vol * price
        name = NAMES.get(asset, asset)
        holdings.append({
            'asset': name, 'volume': vol, 'price': price,
            'value': value, 'pair': pair,
        })

    holdings.sort(key=lambda x: -x['value'])
    total = cash + sum(h['value'] for h in holdings)
    return cash, total, holdings


def analyze_portfolio(show_chart=False, period_days=None, export_format=None):
    """Main portfolio analysis — grounded in actual balances and deposits."""
    print("📊 Portfolio Analyzer — Kraken")
    print("=" * 60)

    # ── 1. REAL PORTFOLIO VALUE ──────────────────
    print("  Fetching live balances...")
    cash, total_value, holdings = get_live_portfolio()
    holdings_value = total_value - cash

    print(f"\n  💰 PORTFOLIO VALUE: ${total_value:.2f}")
    print(f"  Cash: ${cash:.2f} | Holdings: ${holdings_value:.2f}")
    print(f"  " + "-"*50)

    for h in holdings:
        if h['value'] >= 0.50:
            print(f"  {h['asset']:12} {h['volume']:>14.6f} @ ${h['price']:<12.4f} = ${h['value']:.2f}")
    dust_value = sum(h['value'] for h in holdings if h['value'] < 0.50)
    dust_count = sum(1 for h in holdings if h['value'] < 0.50)
    if dust_count:
        print(f"  {'(dust)':12} {dust_count} positions {'':>19} = ${dust_value:.2f}")

    # ── 1b. MARKET SENTIMENT ─────────────────────
    try:
        sentiment = get_market_sentiment()
        decision = check_sentiment(sentiment)
        fg = sentiment.get("fear_greed", "?")
        fg_label = sentiment.get("fear_greed_label", "?")
        zone = classify_sentiment(fg) if isinstance(fg, int) else "?"
        mkt_chg = sentiment.get("market_cap_change_24h", 0)
        btc_dom = sentiment.get("btc_dominance", 0)
        mkt_cap = sentiment.get("total_market_cap", 0)

        # Zone color
        zone_emoji = {"EXTREME_FEAR": "🔴", "FEAR": "🟠", "NEUTRAL": "🔵",
                       "GREED": "🟢", "EXTREME_GREED": "🟢"}.get(zone, "⚪")

        print(f"\n  {zone_emoji} MARKET SENTIMENT")
        print(f"  " + "="*50)
        print(f"  Fear & Greed:     {fg} ({fg_label})")
        print(f"  Market 24h:       {mkt_chg:+.1f}%")
        print(f"  BTC Dominance:    {btc_dom:.1f}%")
        if mkt_cap > 0:
            print(f"  Total Market Cap: ${mkt_cap/1e12:.2f}T")
        buy_mode = "BLOCKED" if not decision["allow"] else f"{decision['size_multiplier']}x size"
        print(f"  Bot buy mode:     {buy_mode}")
        print(f"  Reason:           {decision['reason']}")

        # On-chain signals
        try:
            from onchain_signals import get_onchain_signals, format_onchain
            onchain = get_onchain_signals()
            funding_sig = decision.get("funding_signal", "?")
            print(f"\n  ⛓️ ON-CHAIN SIGNALS")
            print(f"  " + "="*50)
            print(f"  {format_onchain(onchain)}")
            print(f"  Funding signal:   {funding_sig}")
            for name in ["BTC", "ETH"]:
                f = onchain.get("funding", {}).get(name, {})
                ls = onchain.get("long_short", {}).get(name, {})
                oi = onchain.get("open_interest", {}).get(name, {})
                print(f"  {name}: funding {f.get('rate',0)*100:.4f}% | "
                      f"L/S {ls.get('ratio',0):.2f} | "
                      f"OI ${oi.get('current_usd',0)/1e9:.1f}B ({oi.get('change_24h',0):+.1f}%)")
        except Exception as e:
            print(f"\n  ⚠️ On-chain signals unavailable: {e}")
    except Exception as e:
        print(f"\n  ⚠️ Sentiment unavailable: {e}")

    # ── 2. DEPOSITS → REAL P&L ──────────────────
    print(f"\n  Fetching deposit history...")
    net_deposited, deposit_entries = get_total_deposits()
    real_pnl = total_value - net_deposited
    real_pnl_pct = (real_pnl / net_deposited * 100) if net_deposited > 0 else 0

    print(f"\n  💵 REAL P&L (verified against deposits)")
    print(f"  " + "="*50)
    print(f"  Total deposited:  ${net_deposited:.2f}")
    print(f"  Current value:    ${total_value:.2f}")
    if real_pnl >= 0:
        print(f"  Real P&L:         ${real_pnl:+.2f} ({real_pnl_pct:+.1f}%) 🟢")
    else:
        print(f"  Real P&L:         ${real_pnl:+.2f} ({real_pnl_pct:+.1f}%) 🔴")

    print(f"\n  Deposits:")
    for d in deposit_entries:
        dt = datetime.fromtimestamp(d['time']).strftime('%Y-%m-%d')
        net = d['amount'] - d['fee']
        print(f"    {dt} | {d['asset']:6} | ${net:.2f} (fee ${d['fee']:.2f})")

    # ── 3. TRADE HISTORY ANALYSIS ────────────────
    print(f"\n  Fetching trade history...")
    trades = get_kraken_trade_history(period_days)
    if not trades:
        print("  No trades found")
        return

    print(f"  {len(trades)} raw trades found")

    # Batch-lookup order userrefs for bot identification
    _populate_userref_cache(trades)

    # Calculate realized P&L by matching buy/sell pairs (FIFO)
    pnl_trades, open_positions = calculate_realized_pnl(trades)
    realized_trades = [t for t in pnl_trades if t['type'] == 'sell']

    if realized_trades:
        performance = analyze_performance(realized_trades)

        # Cross-check: FIFO realized P&L vs real P&L
        fifo_realized = performance['total_pnl']
        fifo_unrealized = sum(h['value'] for h in holdings) - sum(
            p['entry_cost'] + p['entry_fee']
            for positions in open_positions.values()
            for p in positions
        )

        print(f"\n  📊 TRADE PERFORMANCE (FIFO estimate)")
        print(f"  " + "="*50)
        print(f"  Completed trades: {performance['total_trades']}")
        print(f"  Win rate:         {performance['win_rate']:.1f}%")
        print(f"  FIFO realized:    ${fifo_realized:+.2f}")
        print(f"  Profit factor:    {performance['profit_factor']:.2f}")
        print(f"  Avg win:          ${performance['avg_win']:.2f}")
        print(f"  Avg loss:         ${performance['avg_loss']:.2f}")
        print(f"  Best trade:       ${performance['max_win']:.2f}")
        print(f"  Worst trade:      ${performance['max_loss']:.2f}")

        # Note if FIFO doesn't match reality
        fifo_total = fifo_realized + fifo_unrealized
        if abs(fifo_total - real_pnl) > 5:
            print(f"\n  ⚠️ FIFO estimate (${fifo_total:+.2f}) differs from real P&L (${real_pnl:+.2f})")
            print(f"     This happens when trade history doesn't cover all original buys.")

        # Bot breakdown
        bot_stats = analyze_by_bot(realized_trades)
        display_bot_breakdown(bot_stats)

        # Symbol breakdown — top winners and losers
        symbol_stats = analyze_by_symbol(realized_trades)
        if symbol_stats:
            sorted_symbols = sorted(symbol_stats.items(), key=lambda x: x[1]['total_pnl'], reverse=True)
            print(f"\n  📈 Top Performers (realized):")
            for sym, stats in sorted_symbols[:5]:
                if stats['total_pnl'] > 0:
                    print(f"    🟢 {sym:8} ${stats['total_pnl']:+8.2f} | {stats['total_trades']} trades | {stats['win_rate']:.0f}% win")
            losers = [s for s in sorted_symbols if s[1]['total_pnl'] < -0.10]
            if losers:
                print(f"\n  📉 Worst Performers (realized):")
                for sym, stats in losers[-5:]:
                    print(f"    🔴 {sym:8} ${stats['total_pnl']:+8.2f} | {stats['total_trades']} trades | {stats['win_rate']:.0f}% win")

    # ── 4. BIGGEST UNREALIZED POSITIONS ──────────
    if holdings:
        # Show biggest unrealized losses
        print(f"\n  📉 Holdings by Value:")
        print(f"  " + "-"*50)
        for h in holdings[:10]:
            print(f"    {h['asset']:8} ${h['value']:>8.2f}")

    # Monthly breakdown
    if realized_trades:
        monthly_stats = analyze_by_period(realized_trades, 'monthly')
        display_period_breakdown(monthly_stats, 'monthly')

    # Recent trades
    if realized_trades:
        print(f"\n  📋 Recent Closed Trades:")
        print(f"  " + "-"*70)
        sorted_trades = sorted(realized_trades, key=lambda x: x.get('timestamp', ''), reverse=True)
        for t in sorted_trades[:10]:
            pnl = t.get('pnl_amount', 0)
            emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "🔵"
            try:
                ts_str = datetime.fromisoformat(t.get('timestamp', '')).strftime("%m/%d %H:%M")
            except Exception:
                ts_str = "?"
            bot = t.get('bot_source', '?')
            print(f"    {emoji} [{ts_str}] {t['symbol']:8} ${pnl:+8.2f} ({t['pnl_pct']:+.1f}%) | {bot}")

    # Charts
    if show_chart and realized_trades:
        print(f"\n  📈 Cumulative P&L Chart:")
        pnl_values = [t.get('pnl_amount', 0) for t in realized_trades]
        chart = create_pnl_chart(pnl_values)
        print(chart)

    # Export
    if export_format == 'csv':
        export_to_csv(pnl_trades)

    # Telegram summary
    if realized_trades:
        performance = analyze_performance(realized_trades)
        symbol_stats = analyze_by_symbol(realized_trades)
        monthly_stats = analyze_by_period(realized_trades, 'monthly')
        send_telegram_summary(performance, symbol_stats, monthly_stats)

    print(f"\n  ✅ Done — Real P&L: ${real_pnl:+.2f} ({real_pnl_pct:+.1f}%)")

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
