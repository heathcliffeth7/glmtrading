"""
Database Migration: Add exit_plan_history to trades table

Run this script to add the exit_plan_history column for dynamic exit plan management.

Usage:
    python migrations/run_migration.py
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
    """Run the exit_plan_history migration"""
    
    migration_sql = """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name = 'trades' 
            AND column_name = 'exit_plan_history'
        ) THEN
            ALTER TABLE trades 
            ADD COLUMN exit_plan_history JSON DEFAULT NULL;
            
            RAISE NOTICE 'Column exit_plan_history added successfully';
        ELSE
            RAISE NOTICE 'Column exit_plan_history already exists, skipping';
        END IF;
    END $$;
    
    UPDATE trades 
    SET exit_plan_history = '{"updates": []}'::json
    WHERE exit_plan_history IS NULL 
      AND close_price IS NULL;
    """
    
    try:
        with Session(engine) as session:
            logger.info("Running migration: add_exit_plan_history")
            
            session.execute(text(migration_sql))
            session.commit()
            
            logger.info("✅ Migration completed successfully")
            logger.info("   - Column 'exit_plan_history' added to 'trades' table")
            logger.info("   - Existing open positions initialized with empty history")
            
            count_result = session.execute(
                text("SELECT COUNT(*) FROM trades WHERE close_price IS NULL")
            )
            open_positions = count_result.scalar()
            
            logger.info(f"   - {open_positions} open positions found")
            
            return True
            
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("DATABASE MIGRATION: Add exit_plan_history column")
    logger.info("=" * 60)
    
    success = run_migration()
    
    if success:
        logger.info("=" * 60)
        logger.info("MIGRATION SUCCESSFUL")
        logger.info("=" * 60)
        sys.exit(0)
    else:
        logger.error("=" * 60)
        logger.error("MIGRATION FAILED - Check logs above")
        logger.error("=" * 60)
        sys.exit(1)
