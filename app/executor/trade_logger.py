"""
Trade Logger Module

Handles logging trade information to file for analysis and audit.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from app.utils.logging import get_logger


logger = get_logger(__name__)

# Trade log file configuration
TRADE_LOG_DIR = Path("/root/trading/logs")
TRADE_LOG_FILE = TRADE_LOG_DIR / "trades.log"


def _ensure_log_dir() -> None:
    """Create log directory if it doesn't exist."""
    TRADE_LOG_DIR.mkdir(parents=True, exist_ok=True)


def log_trade_to_file(
    symbol: str,
    action: str,
    entry_price: float,
    close_price: float = None,
    pnl: float = None,
    reasoning: str = None,
    amount: float = None,
    leverage: float = None,
    stop_loss: float = None,
    take_profit: float = None,
    entry_reasoning: str = None,
    invalidation_condition: str = None,
) -> None:
    """
    Log trade information to file with formatted output.

    Args:
        symbol: Trading symbol (e.g., "BTCUSDT")
        action: Trade action ("BUY", "SELL", "CLOSE_LONG", "CLOSE_SHORT", etc.)
        entry_price: Entry price of the position
        close_price: Close price (if position is being closed)
        pnl: Profit/Loss amount (if closing)
        reasoning: Exit reasoning (if closing)
        amount: Position amount
        leverage: Leverage used
        stop_loss: Stop loss price
        take_profit: Take profit price
        entry_reasoning: Original entry reasoning
        invalidation_condition: Condition that would invalidate the trade
    """
    try:
        _ensure_log_dir()

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        separator = "=" * 65

        # Determine position type
        if "CLOSE" in action:
            position_type = action.replace("CLOSE_", "")
        else:
            position_type = "LONG" if action == "BUY" else "SHORT"

        with open(TRADE_LOG_FILE, "a", encoding="utf-8") as f:
            if close_price and pnl is not None:
                # === CLOSE FORMAT ===
                # Calculate PnL percentage
                pnl_pct = ((close_price - entry_price) / entry_price * 100) if entry_price else 0
                if position_type == "SHORT":
                    pnl_pct = -pnl_pct
                pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                pnl_pct_str = f"+{pnl_pct:.2f}%" if pnl_pct >= 0 else f"{pnl_pct:.2f}%"

                f.write(f"{separator}\n")
                f.write(f"[{timestamp}] {symbol} | {position_type}\n")
                f.write(f"{separator}\n")
                f.write(f"Entry:     ${entry_price:,.2f}\n")
                f.write(f"Close:     ${close_price:,.2f}\n")
                if amount and leverage:
                    f.write(f"Amount:    {amount:.4f} | Leverage: {leverage:.0f}x\n")
                f.write(f"PnL:       {pnl_str} ({pnl_pct_str})\n")
                f.write("\n")

                # Exit Plan info
                if stop_loss:
                    f.write(f"Stop Loss:   ${stop_loss:,.2f}\n")
                if take_profit:
                    f.write(f"Take Profit: ${take_profit:,.2f}\n")
                if invalidation_condition:
                    f.write(f"Invalidation: {invalidation_condition}\n")
                if stop_loss or take_profit or invalidation_condition:
                    f.write("\n")

                # Entry Reason
                if entry_reasoning:
                    clean_entry = entry_reasoning.replace("\n", " ").strip()
                    if len(clean_entry) > 500:
                        clean_entry = clean_entry[:500] + "..."
                    f.write(f"Entry Reason:\n  {clean_entry}\n\n")

                # Exit Reason
                if reasoning:
                    clean_exit = reasoning.replace("\n", " ").strip()
                    if len(clean_exit) > 500:
                        clean_exit = clean_exit[:500] + "..."
                    f.write(f"Exit Reason:\n  {clean_exit}\n")

                f.write(f"{separator}\n\n")

            else:
                # === OPEN FORMAT ===
                f.write(f"{separator}\n")
                f.write(f"[{timestamp}] {symbol} | {position_type} | OPENED\n")
                f.write(f"{separator}\n")
                f.write(f"Entry:     ${entry_price:,.2f}\n")
                if amount and leverage:
                    f.write(f"Amount:    {amount:.4f} | Leverage: {leverage:.0f}x\n")
                f.write("\n")

                # Exit Plan info
                if stop_loss:
                    f.write(f"Stop Loss:   ${stop_loss:,.2f}\n")
                if take_profit:
                    f.write(f"Take Profit: ${take_profit:,.2f}\n")
                if invalidation_condition:
                    f.write(f"Invalidation: {invalidation_condition}\n")
                if stop_loss or take_profit or invalidation_condition:
                    f.write("\n")

                # Entry Reason
                if reasoning:
                    clean_reasoning = reasoning.replace("\n", " ").strip()
                    if len(clean_reasoning) > 500:
                        clean_reasoning = clean_reasoning[:500] + "..."
                    f.write(f"Entry Reason:\n  {clean_reasoning}\n")

                f.write(f"{separator}\n\n")

        logger.info("Trade logged: %s %s @ %.2f", symbol, action, entry_price)

    except Exception as e:
        logger.error("Failed to log trade to file: %s", e)
