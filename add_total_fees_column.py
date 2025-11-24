#!/usr/bin/env python3
"""
Database migration script: Add total_fees column to daily_pnl table
"""
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text
from app.executor.ledger import engine, Session

def add_total_fees_column():
    """Add total_fees column to daily_pnl table if it doesn't exist"""
    
    with Session(engine) as session:
        try:
            # Check if column already exists
            result = session.execute(text(
                "SELECT COUNT(*) FROM pragma_table_info('daily_pnl') WHERE name='total_fees'"
            ))
            column_exists = result.scalar() > 0
            
            if column_exists:
                print("✅ Column 'total_fees' already exists in daily_pnl table")
                return
            
            # Add the column
            print("Adding 'total_fees' column to daily_pnl table...")
            session.execute(text(
                "ALTER TABLE daily_pnl ADD COLUMN total_fees REAL DEFAULT 0.0"
            ))
            session.commit()
            print("✅ Successfully added 'total_fees' column to daily_pnl table")
            
            # Verify the column was added
            result = session.execute(text(
                "SELECT COUNT(*) FROM pragma_table_info('daily_pnl') WHERE name='total_fees'"
            ))
            if result.scalar() > 0:
                print("✅ Verification successful: Column exists")
            else:
                print("❌ Verification failed: Column not found")
                
        except Exception as e:
            print(f"❌ Error during migration: {e}")
            session.rollback()
            raise

if __name__ == "__main__":
    print("=" * 60)
    print("Database Migration: Add total_fees column to daily_pnl")
    print("=" * 60)
    add_total_fees_column()
    print("=" * 60)
    print("Migration completed!")
    print("=" * 60)
