"""
Database Migration: Multi-Position Hedge Support + Fee Tracking

This migration adds support for:
1. Separate long/short position tracking
2. Last trade price/timestamp for %3 price change validation
3. Fee optimization tracking

Usage:
    python migrations/multi_position_hedge_support.py
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text
from sqlalchemy.orm import Session
from app.executor.ledger import engine
from app.utils.logging import get_logger

logger = get_logger(__name__)


def run_migration():
    """Run the multi-position hedge support migration"""
    
    migration_sql = """
    DO $$
    BEGIN
        -- Add long_position column
        IF NOT EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name = 'portfolio' 
            AND column_name = 'long_position'
        ) THEN
            ALTER TABLE portfolio 
            ADD COLUMN long_position FLOAT DEFAULT 0.0;
            RAISE NOTICE 'Column long_position added';
        END IF;
        
        -- Add long_avg_price column
        IF NOT EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name = 'portfolio' 
            AND column_name = 'long_avg_price'
        ) THEN
            ALTER TABLE portfolio 
            ADD COLUMN long_avg_price FLOAT DEFAULT NULL;
            RAISE NOTICE 'Column long_avg_price added';
        END IF;
        
        -- Add short_position column
        IF NOT EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name = 'portfolio' 
            AND column_name = 'short_position'
        ) THEN
            ALTER TABLE portfolio 
            ADD COLUMN short_position FLOAT DEFAULT 0.0;
            RAISE NOTICE 'Column short_position added';
        END IF;
        
        -- Add short_avg_price column
        IF NOT EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name = 'portfolio' 
            AND column_name = 'short_avg_price'
        ) THEN
            ALTER TABLE portfolio 
            ADD COLUMN short_avg_price FLOAT DEFAULT NULL;
            RAISE NOTICE 'Column short_avg_price added';
        END IF;
        
        -- Add net_position column
        IF NOT EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name = 'portfolio' 
            AND column_name = 'net_position'
        ) THEN
            ALTER TABLE portfolio 
            ADD COLUMN net_position FLOAT DEFAULT 0.0;
            RAISE NOTICE 'Column net_position added';
        END IF;
        
        -- Add last_trade_price column
        IF NOT EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name = 'portfolio' 
            AND column_name = 'last_trade_price'
        ) THEN
            ALTER TABLE portfolio 
            ADD COLUMN last_trade_price FLOAT DEFAULT NULL;
            RAISE NOTICE 'Column last_trade_price added';
        END IF;
        
        -- Add last_trade_timestamp column
        IF NOT EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name = 'portfolio' 
            AND column_name = 'last_trade_timestamp'
        ) THEN
            ALTER TABLE portfolio 
            ADD COLUMN last_trade_timestamp TIMESTAMP DEFAULT NULL;
            RAISE NOTICE 'Column last_trade_timestamp added';
        END IF;
        
    END $$;
    
    -- Migrate existing data from old columns to new structure
    UPDATE portfolio 
    SET 
        net_position = position,
        long_position = CASE WHEN position > 0 THEN position ELSE 0 END,
        long_avg_price = CASE WHEN position > 0 THEN average_price ELSE NULL END,
        short_position = CASE WHEN position < 0 THEN position ELSE 0 END,
        short_avg_price = CASE WHEN position < 0 THEN average_price ELSE NULL END
    WHERE net_position = 0.0;  -- Only update if not already migrated
    
    -- Populate last_trade_price and last_trade_timestamp from latest trade
    UPDATE portfolio p
    SET 
        last_trade_price = (
            SELECT t.price 
            FROM trades t 
            WHERE t.symbol = p.symbol 
            ORDER BY t.timestamp DESC 
            LIMIT 1
        ),
        last_trade_timestamp = (
            SELECT t.timestamp 
            FROM trades t 
            WHERE t.symbol = p.symbol 
            ORDER BY t.timestamp DESC 
            LIMIT 1
        )
    WHERE p.last_trade_price IS NULL;
    """
    
    try:
        with Session(engine) as session:
            logger.info("Running migration: multi_position_hedge_support")
            
            session.execute(text(migration_sql))
            session.commit()
            
            logger.info("✅ Migration completed successfully")
            logger.info("   - New columns added to 'portfolio' table:")
            logger.info("     * long_position, long_avg_price")
            logger.info("     * short_position, short_avg_price")
            logger.info("     * net_position")
            logger.info("     * last_trade_price, last_trade_timestamp")
            
            # Verify migration
            count_result = session.execute(
                text("SELECT COUNT(*) FROM portfolio")
            )
            portfolio_count = count_result.scalar()
            
            migrated_result = session.execute(
                text("SELECT COUNT(*) FROM portfolio WHERE net_position != 0 OR long_position != 0 OR short_position != 0")
            )
            migrated_count = migrated_result.scalar()
            
            logger.info(f"   - Total portfolios: {portfolio_count}")
            logger.info(f"   - Migrated portfolios: {migrated_count}")
            
            return True
            
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    logger.info("=" * 70)
    logger.info("DATABASE MIGRATION: Multi-Position Hedge Support + Fee Tracking")
    logger.info("=" * 70)
    
    success = run_migration()
    
    if success:
        logger.info("=" * 70)
        logger.info("MIGRATION SUCCESSFUL")
        logger.info("=" * 70)
        logger.info("")
        logger.info("Next steps:")
        logger.info("1. Update portfolio_sync.py to calculate long/short separately")
        logger.info("2. Update executor.py to support hedge positions")
        logger.info("3. Update GLM prompts with new portfolio structure")
        logger.info("")
        sys.exit(0)
    else:
        logger.error("=" * 70)
        logger.error("MIGRATION FAILED - Check logs above")
        logger.error("=" * 70)
        sys.exit(1)
