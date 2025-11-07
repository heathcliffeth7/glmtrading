"""
Outcome Tracking Worker

Automatically tracks signal outcomes by:
1. Querying signals without outcomes
2. Fetching current price and PnL
3. Calculating outcome success
4. Updating signal with outcome data

Runs periodically to update 1h, 4h, and 24h outcomes.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.utils.influx import _ensure_client, _query_api, query_price_at_time
from app.utils.logging import get_logger
from app.utils.signal_logger import get_signal_logger

logger = get_logger(__name__)


class OutcomeTracker:
    """
    Track signal outcomes automatically

    Usage:
        tracker = OutcomeTracker()

        # Track 1h outcomes
        await tracker.track_outcomes(timeframe='1h')

        # Run continuous tracking
        await tracker.run_continuous()
    """

    def __init__(self, bucket: str = None):
        from app.config.settings import get_settings

        settings = get_settings()
        self.bucket = bucket or settings.influx.bucket
        self.signal_logger = get_signal_logger()

    async def track_outcomes(
        self,
        timeframe: str = "1h",
        symbol: str = "BTCUSDT",
    ) -> int:
        """
        Track outcomes for signals in specified timeframe

        Args:
            timeframe: '1h', '4h', or '24h'
            symbol: Trading symbol

        Returns:
            Number of signals updated
        """
        _ensure_client()

        # Calculate time window based on timeframe
        hours_map = {"1h": 1, "4h": 4, "24h": 24}
        hours = hours_map.get(timeframe, 1)

        # Query signals without outcomes for this timeframe
        # Look for signals from (hours + 1) to (hours + 24) ago
        start_time = datetime.utcnow() - timedelta(hours=hours + 24)
        end_time = datetime.utcnow() - timedelta(hours=hours)

        query = f"""
from(bucket: "{self.bucket}")
  |> range(start: {start_time.strftime('%Y-%m-%dT%H:%M:%SZ')}, 
           stop: {end_time.strftime('%Y-%m-%dT%H:%M:%SZ')})
  |> filter(fn: (r) => r["_measurement"] == "trading_signals")
  |> filter(fn: (r) => r["symbol"] == "{symbol}")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => not exists r["outcome_price_{timeframe}"])
  |> keep(columns: ["_time", "action", "current_price", "position", "amount", "leverage"])
"""

        try:
            tables = _query_api.query(query)

            if not tables or not tables[0].records:
                logger.debug("No signals to track for %s timeframe", timeframe)
                return 0

            updated_count = 0

            for record in tables[0].records:
                signal_time = record.get_time()
                action = record.values.get("action", "HOLD")
                entry_price = record.values.get("current_price", 0)
                position = record.values.get("position", 0)
                amount = record.values.get("amount", 0)
                leverage = record.values.get("leverage", 1)

                # Calculate outcome time
                outcome_time = signal_time + timedelta(hours=hours)

                # Fetch price at outcome time
                outcome_price = query_price_at_time(
                    symbol=symbol,
                    target_time=outcome_time,
                    interval="1m",
                    window_minutes=30,
                )

                if outcome_price is None:
                    logger.warning("Could not fetch outcome price for signal at %s", signal_time)
                    continue

                # Calculate PnL
                outcome_pnl = self._calculate_pnl(
                    action=action,
                    entry_price=entry_price,
                    exit_price=outcome_price,
                    amount=amount,
                    leverage=leverage,
                )

                # Determine success
                outcome_success = outcome_pnl > 0

                # Update signal with outcome
                self.signal_logger.update_outcome(
                    symbol=symbol,
                    timestamp=signal_time,
                    **{
                        f"outcome_price_{timeframe}": outcome_price,
                        f"outcome_pnl_{timeframe}": outcome_pnl,
                        "outcome_success": outcome_success,
                    },
                )

                updated_count += 1

                logger.info(
                    "📊 Outcome tracked (%s): %s @ %s → %.2f (PnL: $%.2f, Success: %s)",
                    timeframe,
                    action,
                    signal_time.strftime("%Y-%m-%d %H:%M"),
                    outcome_price,
                    outcome_pnl,
                    "✅" if outcome_success else "❌",
                )

            return updated_count

        except Exception as e:
            logger.error("Failed to track outcomes for %s: %s", timeframe, e, exc_info=True)
            return 0

    def _calculate_pnl(
        self,
        action: str,
        entry_price: float,
        exit_price: float,
        amount: float,
        leverage: float,
    ) -> float:
        """
        Calculate PnL for a signal

        Args:
            action: BUY, SELL, or HOLD
            entry_price: Entry price
            exit_price: Exit price
            amount: Position size
            leverage: Leverage used

        Returns:
            PnL in USD
        """
        if action == "HOLD" or amount == 0:
            return 0.0

        # Calculate price change percentage
        if action == "BUY":
            price_change_pct = (exit_price - entry_price) / entry_price
        elif action == "SELL":
            price_change_pct = (entry_price - exit_price) / entry_price
        else:
            return 0.0

        # Calculate PnL with leverage
        position_value = amount * entry_price
        pnl = position_value * price_change_pct * leverage

        return pnl

    async def run_continuous(
        self,
        check_interval_minutes: int = 15,
        symbol: str = "BTCUSDT",
    ) -> None:
        """
        Run continuous outcome tracking

        Checks for signals to update every check_interval_minutes.

        Args:
            check_interval_minutes: Minutes between checks
            symbol: Trading symbol
        """
        logger.info(
            "Starting continuous outcome tracking: check_interval=%dm, symbol=%s",
            check_interval_minutes,
            symbol,
        )

        while True:
            try:
                # Track outcomes for all timeframes
                for timeframe in ["1h", "4h", "24h"]:
                    updated = await self.track_outcomes(timeframe, symbol)
                    if updated > 0:
                        logger.info("Updated %d signals for %s timeframe", updated, timeframe)

            except Exception as e:
                logger.error("Error in continuous outcome tracking: %s", e, exc_info=True)

            # Wait before next check
            await asyncio.sleep(check_interval_minutes * 60)

    def get_pending_outcomes(
        self,
        symbol: str = "BTCUSDT",
    ) -> Dict[str, int]:
        """
        Get count of signals pending outcome tracking

        Args:
            symbol: Trading symbol

        Returns:
            Dict with pending counts per timeframe
        """
        _ensure_client()

        pending = {}

        for timeframe in ["1h", "4h", "24h"]:
            hours_map = {"1h": 1, "4h": 4, "24h": 24}
            hours = hours_map[timeframe]

            start_time = datetime.utcnow() - timedelta(hours=hours + 24)
            end_time = datetime.utcnow() - timedelta(hours=hours)

            query = f"""
from(bucket: "{self.bucket}")
  |> range(start: {start_time.strftime('%Y-%m-%dT%H:%M:%SZ')}, 
           stop: {end_time.strftime('%Y-%m-%dT%H:%M:%SZ')})
  |> filter(fn: (r) => r["_measurement"] == "trading_signals")
  |> filter(fn: (r) => r["symbol"] == "{symbol}")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => not exists r["outcome_price_{timeframe}"])
  |> count()
"""

            try:
                tables = _query_api.query(query)
                if tables and tables[0].records:
                    pending[timeframe] = tables[0].records[0].get_value()
                else:
                    pending[timeframe] = 0
            except Exception as e:
                logger.error("Failed to get pending count for %s: %s", timeframe, e)
                pending[timeframe] = 0

        return pending


async def main():
    """CLI entry point for outcome tracking"""
    import argparse

    parser = argparse.ArgumentParser(description="Outcome Tracking Worker")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading symbol")
    parser.add_argument("--timeframe", choices=["1h", "4h", "24h"], help="Track specific timeframe")
    parser.add_argument("--continuous", action="store_true", help="Run continuous tracking")
    parser.add_argument("--interval", type=int, default=15, help="Check interval in minutes")
    parser.add_argument("--pending", action="store_true", help="Show pending outcomes")

    args = parser.parse_args()

    tracker = OutcomeTracker()

    if args.pending:
        pending = tracker.get_pending_outcomes(args.symbol)
        print("\nPending Outcome Tracking:")
        for tf, count in pending.items():
            print(f"  {tf}: {count} signals")

    elif args.continuous:
        await tracker.run_continuous(
            check_interval_minutes=args.interval,
            symbol=args.symbol,
        )

    elif args.timeframe:
        updated = await tracker.track_outcomes(args.timeframe, args.symbol)
        print(f"\nUpdated {updated} signals for {args.timeframe} timeframe")

    else:
        # Track all timeframes once
        for tf in ["1h", "4h", "24h"]:
            updated = await tracker.track_outcomes(tf, args.symbol)
            print(f"Updated {updated} signals for {tf} timeframe")


if __name__ == "__main__":
    asyncio.run(main())
