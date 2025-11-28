"""
FastAPI Dependencies - Database Session Injection

CRITICAL: Reuses existing SQLAlchemy engine to share connection pool
with the trading system.
"""
from typing import Generator

from sqlalchemy.orm import Session, sessionmaker

# Import existing engine from ledger module
from app.executor.ledger import engine

# Create session factory bound to existing engine
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    Dependency for FastAPI routes.
    Yields a database session from the shared connection pool.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
