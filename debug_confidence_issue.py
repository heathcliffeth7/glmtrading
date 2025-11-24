#!/usr/bin/env python3
"""Debug script to understand why trades aren't opening despite confidence > 80"""

import sqlite3
from datetime import datetime, timedelta

# Connect to database
conn = sqlite3.connect('/root/trading/portfolio.db')
c = conn.cursor()

print("=" * 80)
print("TRADING SYSTEM DEBUG - Confidence > 80 but no trades opening")
print("=" * 80)

# Check recent trades
print("\n1. RECENT TRADES (Last 10):")
print("-" * 80)
try:
    trades = c.execute("""
        SELECT action, glm_confidence, executed_at, reasoning, amount, price 
        FROM trades 
        ORDER BY executed_at DESC 
        LIMIT 10
    """).fetchall()
    
    if trades:
        for trade in trades:
            action, conf, exec_at, reason, amt, price = trade
            print(f"  {exec_at} | {action:4} | Conf: {conf:5.1f}% | Amt: {amt:8.6f} | ${price:8.2f}")
            if reason:
                print(f"    Reason: {reason[:80]}...")
    else:
        print("  NO TRADES FOUND!")
except Exception as e:
    print(f"  Error: {e}")

# Check portfolio state
print("\n2. CURRENT PORTFOLIO STATE:")
print("-" * 80)
try:
    portfolio = c.execute("SELECT * FROM portfolio").fetchone()
    if portfolio:
        print(f"  Symbol: {portfolio[0]}")
        print(f"  Position: {portfolio[1]:.6f} BTC")
        print(f"  Average Price: ${portfolio[2]:.2f}")
        print(f"  Realized PnL: ${portfolio[3]:.2f}")
        print(f"  Updated At: {portfolio[4]}")
    else:
        print("  NO PORTFOLIO DATA!")
except Exception as e:
    print(f"  Error: {e}")

# Check daily PnL
print("\n3. DAILY PNL:")
print("-" * 80)
try:
    pnl = c.execute("SELECT * FROM daily_pnl ORDER BY date DESC LIMIT 1").fetchone()
    if pnl:
        print(f"  Date: {pnl[0]}")
        print(f"  Realized PnL: ${pnl[1]:.2f}")
        print(f"  Unrealized PnL: ${pnl[2]:.2f}")
        print(f"  Total Trades: {pnl[3]}")
    else:
        print("  NO PNL DATA!")
except Exception as e:
    print(f"  Error: {e}")

# Check for HOLD decisions with high confidence
print("\n4. RECENT HOLD DECISIONS WITH HIGH CONFIDENCE:")
print("-" * 80)
try:
    holds = c.execute("""
        SELECT executed_at, glm_confidence, reasoning 
        FROM trades 
        WHERE action = 'HOLD' AND glm_confidence >= 80
        ORDER BY executed_at DESC 
        LIMIT 5
    """).fetchall()
    
    if holds:
        for hold in holds:
            exec_at, conf, reason = hold
            print(f"  {exec_at} | Conf: {conf:5.1f}% | {reason[:70]}...")
    else:
        print("  No HOLD decisions with confidence >= 80%")
except Exception as e:
    print(f"  Error: {e}")

# Check last CLOSE time
print("\n5. LAST CLOSE TIME (for cooldown check):")
print("-" * 80)
try:
    last_close = c.execute("""
        SELECT executed_at, action, glm_confidence 
        FROM trades 
        WHERE action = 'CLOSE'
        ORDER BY executed_at DESC 
        LIMIT 1
    """).fetchone()
    
    if last_close:
        exec_at, action, conf = last_close
        close_time = datetime.strptime(exec_at, '%Y-%m-%d %H:%M:%S')
        elapsed = (datetime.now() - close_time).total_seconds()
        print(f"  Last CLOSE: {exec_at}")
        print(f"  Time elapsed: {elapsed:.0f} seconds ({elapsed/60:.1f} minutes)")
        if elapsed < 900:  # 15 minutes
            print(f"  ⚠️ COOLDOWN ACTIVE! Waiting period may be blocking trades.")
    else:
        print("  No CLOSE trades found")
except Exception as e:
    print(f"  Error: {e}")

# Check for open positions
print("\n6. OPEN POSITIONS:")
print("-" * 80)
try:
    open_trades = c.execute("""
        SELECT action, amount, price, executed_at, position_id
        FROM trades 
        WHERE close_price IS NULL
        ORDER BY executed_at DESC
    """).fetchall()
    
    if open_trades:
        print(f"  Found {len(open_trades)} open position(s):")
        total_notional = 0
        for trade in open_trades:
            action, amt, price, exec_at, pos_id = trade
            notional = abs(amt) * price
            total_notional += notional
            print(f"    {exec_at} | {action:4} | {amt:8.6f} BTC @ ${price:8.2f} | Notional: ${notional:,.2f}")
        print(f"  Total Notional: ${total_notional:,.2f}")
    else:
        print("  No open positions")
except Exception as e:
    print(f"  Error: {e}")

print("\n" + "=" * 80)
print("DIAGNOSTIC CHECKLIST:")
print("=" * 80)
print("✓ Check logs: tail -100 /root/trading/runtime_debug.log | grep -E 'COOLDOWN|BLOCKED|SKIP|confidence'")
print("✓ If COOLDOWN active: wait or reduce cooldown period")
print("✓ If notional cap reached: close positions or increase cap")
print("✓ If equity issue: check portfolio sync")

conn.close()
