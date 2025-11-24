#!/usr/bin/env python3
"""
Remove TwelveData columns from prediction_logs table

This script removes the following TwelveData-specific columns:
- rsi_twelvedata
- macd_twelvedata
- macd_signal_twelvedata
- macd_hist_twelvedata
- atr_twelvedata
- stoch_k
- stoch_d
- bb_upper
- bb_middle
- bb_lower
- ema_twelvedata
- sma_twelvedata
- vwap_twelvedata
- willr
- cci
- mfi
- obv
- sar

This migration cleans up the database after TwelveData integration removal.
"""

import logging
from sqlalchemy import text
from app.executor.ledger import engine

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def migrate_remove_twelvedata_columns():
    """
    Remove TwelveData columns from prediction_logs table
    """
    # List of TwelveData columns to remove
    twelvedata_columns = [
        'rsi_twelvedata',
        'macd_twelvedata',
        'macd_signal_twelvedata',
        'macd_hist_twelvedata',
        'atr_twelvedata',
        'stoch_k',
        'stoch_d',
        'bb_upper',
        'bb_middle',
        'bb_lower',
        'ema_twelvedata',
        'sma_twelvedata',
        'vwap_twelvedata',
        'willr',
        'cci',
        'mfi',
        'obv',
        'sar'
    ]

    try:
        with engine.connect() as conn:
            # Start transaction
            trans = conn.begin()

            logger.info("Starting TwelveData column removal migration...")

            # Check if prediction_logs table exists
            result = conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'prediction_logs'
                );
            """))

            table_exists = result.scalar()
            if not table_exists:
                logger.warning("prediction_logs table does not exist, skipping migration")
                trans.commit()
                return

            # Get existing columns in prediction_logs table
            result = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'prediction_logs';
            """))

            existing_columns = [row[0] for row in result.fetchall()]
            logger.info(f"Found {len(existing_columns)} columns in prediction_logs table")

            # Remove columns that exist
            removed_count = 0
            for column in twelvedata_columns:
                if column in existing_columns:
                    logger.info(f"Removing column: {column}")
                    try:
                        conn.execute(text(f"ALTER TABLE prediction_logs DROP COLUMN {column};"))
                        removed_count += 1
                        logger.info(f"✅ Dropped column: {column}")
                    except Exception as e:
                        logger.error(f"❌ Failed to drop column {column}: {e}")
                        # Continue with other columns
                else:
                    logger.info(f"Column {column} does not exist, skipping")

            # Commit transaction
            trans.commit()

            logger.info(f"✅ Migration completed successfully!")
            logger.info(f"   Removed {removed_count} TwelveData columns from prediction_logs table")
            logger.info("   Database cleanup completed")

    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        raise

if __name__ == "__main__":
    migrate_remove_twelvedata_columns()