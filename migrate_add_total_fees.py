#!/usr/bin/env python3
"""
Simple database migration: Add total_fees column to daily_pnl table
Uses direct SQLite connection to avoid import issues
"""
import sqlite3
import os

def migrate_database():
    """Add total_fees column to daily_pnl table"""
    
    db_path = os.path.join(os.path.dirname(__file__), 'portfolio.db')
    
    if not os.path.exists(db_path):
        print(f"❌ Database not found at: {db_path}")
        return False
    
    print(f"📂 Database path: {db_path}")
    
    try:
        # Connect to database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if column already exists
        cursor.execute("PRAGMA table_info(daily_pnl)")
        columns = cursor.fetchall()
        column_names = [col[1] for col in columns]
        
        print(f"📊 Current columns in daily_pnl: {column_names}")
        
        if 'total_fees' in column_names:
            print("✅ Column 'total_fees' already exists")
            conn.close()
            return True
        
        # Add the column
        print("➕ Adding 'total_fees' column...")
        cursor.execute("ALTER TABLE daily_pnl ADD COLUMN total_fees REAL DEFAULT 0.0")
        conn.commit()
        
        # Verify
        cursor.execute("PRAGMA table_info(daily_pnl)")
        columns = cursor.fetchall()
        column_names = [col[1] for col in columns]
        
        if 'total_fees' in column_names:
            print("✅ Successfully added 'total_fees' column")
            print(f"📊 Updated columns: {column_names}")
            conn.close()
            return True
        else:
            print("❌ Failed to add column")
            conn.close()
            return False
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    print("=" * 70)
    print("DATABASE MIGRATION: Add total_fees to daily_pnl table")
    print("=" * 70)
    success = migrate_database()
    print("=" * 70)
    if success:
        print("✅ Migration completed successfully!")
    else:
        print("❌ Migration failed!")
    print("=" * 70)
