#!/usr/bin/env python3
"""
Migration: add close_time column to trades (if not exists)
"""
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.executor.ledger import engine


def main() -> None:
    with Session(engine) as session:
        try:
            session.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name='trades' AND column_name='close_time'
                    ) THEN
                        ALTER TABLE trades ADD COLUMN close_time TIMESTAMP NULL;
                    END IF;
                END $$;
            """))
            session.commit()
            print("✅ close_time column ensured")
        except Exception as exc:
            session.rollback()
            print(f"❌ Migration failed: {exc}")


if __name__ == "__main__":
    main()
