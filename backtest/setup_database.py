#!/usr/bin/env python3
"""
Backtest Database Setup Script
Bu script PostgreSQL'de backtest sonuçları için gerekli tabloları oluşturur
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Boolean, ForeignKey, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base = declarative_base()

class BacktestResult(Base):
    """Backtest sonuçlarını saklar"""
    __tablename__ = 'backtest_results'

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_name = Column(String(100), nullable=False)
    scenario_name = Column(String(100), nullable=True)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    timeframe = Column(String(10), nullable=False)
    initial_capital = Column(Float, nullable=False)
    final_capital = Column(Float, nullable=False)

    # Performance Metrics
    total_return = Column(Float, nullable=False)  # percentage
    cagr = Column(Float, nullable=True)  # Compound Annual Growth Rate
    benchmark_return = Column(Float, nullable=True)
    excess_return = Column(Float, nullable=True)

    # Risk Metrics
    sharpe_ratio = Column(Float, nullable=True)
    sortino_ratio = Column(Float, nullable=True)
    calmar_ratio = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    max_drawdown_duration = Column(Integer, nullable=True)  # days
    var_95 = Column(Float, nullable=True)  # Value at Risk 95%
    cvar_95 = Column(Float, nullable=True)  # Conditional VaR 95%

    # Trade Metrics
    num_trades = Column(Integer, nullable=False)
    win_rate = Column(Float, nullable=True)  # percentage
    profit_factor = Column(Float, nullable=True)
    avg_win = Column(Float, nullable=True)
    avg_loss = Column(Float, nullable=True)
    largest_win = Column(Float, nullable=True)
    largest_loss = Column(Float, nullable=True)
    avg_trade_duration = Column(Float, nullable=True)  # hours

    # Monthly Stats (JSON format)
    monthly_returns = Column(Text, nullable=True)  # JSON string

    # Parameters used
    parameters = Column(Text, nullable=True)  # JSON string

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    execution_time_seconds = Column(Float, nullable=True)
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)

    # Relationships
    trades = relationship("BacktestTrade", back_populates="backtest", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<BacktestResult(id={self.id}, run_name='{self.run_name}', total_return={self.total_return:.2f}%)>"

class BacktestTrade(Base):
    """Her trade'in detaylarını saklar"""
    __tablename__ = 'backtest_trades'

    id = Column(Integer, primary_key=True, autoincrement=True)
    backtest_id = Column(Integer, ForeignKey('backtest_results.id'), nullable=False)

    timestamp = Column(DateTime, nullable=False)
    trade_id = Column(String(50), nullable=True)  # External trade ID if exists

    # Trade Details
    action = Column(String(10), nullable=False)  # BUY, SELL, CLOSE
    symbol = Column(String(20), default='BTCUSDT')
    price = Column(Float, nullable=False)
    amount = Column(Float, nullable=False)  # BTC amount
    value = Column(Float, nullable=False)  # USDT value

    # PnL
    pnl = Column(Float, nullable=True)  # Realized PnL for this trade
    cumulative_pnl = Column(Float, nullable=True)
    portfolio_value_before = Column(Float, nullable=True)
    portfolio_value_after = Column(Float, nullable=True)

    # Signal Details
    signal_confidence = Column(Float, nullable=True)
    glm_reasoning = Column(Text, nullable=True)
    signal_components = Column(Text, nullable=True)  # JSON string

    # Risk Metrics at Trade Time
    equity = Column(Float, nullable=True)
    used_margin = Column(Float, nullable=True)
    free_equity = Column(Float, nullable=True)
    position_size_percent = Column(Float, nullable=True)

    # Metadata
    commission = Column(Float, default=0.0)
    slippage = Column(Float, default=0.0)

    # Relationships
    backtest = relationship("BacktestResult", back_populates="trades")

    def __repr__(self):
        return f"<BacktestTrade(id={self.id}, action={self.action}, price={self.price:.2f}, pnl={self.pnl:.2f})>"

class BacktestMetric(Base):
    """Rolling metrics ve diğer hesaplamalar için"""
    __tablename__ = 'backtest_metrics'

    id = Column(Integer, primary_key=True, autoincrement=True)
    backtest_id = Column(Integer, ForeignKey('backtest_results.id'), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    metric_type = Column(String(50), nullable=False)  # 'rolling_sharpe', 'daily_return', etc.

    # Values
    value = Column(Float, nullable=False)
    additional_data = Column(Text, nullable=True)  # JSON string for extra data

    # Indexes for performance
    __table_args__ = (
        Index('idx_backtest_metric_type_time', 'backtest_id', 'metric_type', 'timestamp'),
    )

class BenchmarkResult(Base):
    """Benchmark stratejilerinin sonuçları"""
    __tablename__ = 'benchmark_results'

    id = Column(Integer, primary_key=True, autoincrement=True)
    backtest_id = Column(Integer, ForeignKey('backtest_results.id'), nullable=False)
    benchmark_name = Column(String(100), nullable=False)

    # Metrics
    total_return = Column(Float, nullable=False)
    sharpe_ratio = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)

    def __repr__(self):
        return f"<BenchmarkResult(benchmark_name='{self.benchmark_name}', return={self.total_return:.2f}%)>"

def create_tables(database_url):
    """Tabloları oluştur"""
    try:
        engine = create_engine(database_url)
        Base.metadata.create_all(engine)
        logger.info("✅ Tablolar başarıyla oluşturuldu")
        return True
    except Exception as e:
        logger.error(f"❌ Tablo oluşturma hatası: {e}")
        return False

def drop_tables(database_url):
    """Tabloları sil (dikkatli kullanın!)"""
    try:
        engine = create_engine(database_url)
        Base.metadata.drop_all(engine)
        logger.warning("⚠️ Tüm tablolar silindi")
        return True
    except Exception as e:
        logger.error(f"❌ Tablo silme hatası: {e}")
        return False

def get_session(database_url):
    """Database session oluştur"""
    engine = create_engine(database_url)
    Session = sessionmaker(bind=engine)
    return Session()

def verify_tables(database_url):
    """Tabloların varlığını kontrol et"""
    try:
        session = get_session(database_url)

        # Check if tables exist by querying them
        from sqlalchemy import inspect
        inspector = inspect(session.get_bind())
        table_names = inspector.get_table_names()

        expected_tables = [
            'backtest_results',
            'backtest_trades',
            'backtest_metrics',
            'benchmark_results'
        ]

        missing_tables = [t for t in expected_tables if t not in table_names]

        if missing_tables:
            logger.error(f"❌ Eksik tablolar: {missing_tables}")
            return False

        logger.info("✅ Tüm tablolar mevcut")

        # Show table counts
        for table in expected_tables:
            count = session.execute(f"SELECT COUNT(*) FROM {table}").scalar()
            logger.info(f"   {table}: {count} kayıt")

        return True
    except Exception as e:
        logger.error(f"❌ Tablo kontrol hatası: {e}")
        return False

def main():
    """Ana fonksiyon"""
    import argparse
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description='Backtest Database Setup')
    parser.add_argument('--action', choices=['create', 'drop', 'verify'], default='create',
                        help='Action to perform')
    parser.add_argument('--database-url', default=os.getenv('DATABASE_URL'),
                        help='Database URL')

    args = parser.parse_args()

    if not args.database_url:
        logger.error("❌ DATABASE_URL environment variable not set")
        sys.exit(1)

    logger.info(f"Database URL: {args.database_url}")

    if args.action == 'create':
        create_tables(args.database_url)
        verify_tables(args.database_url)
    elif args.action == 'drop':
        confirm = input("⚠️ Are you sure you want to DROP ALL TABLES? (yes/no): ")
        if confirm.lower() == 'yes':
            drop_tables(args.database_url)
        else:
            logger.info("Operation cancelled")
    elif args.action == 'verify':
        verify_tables(args.database_url)

if __name__ == '__main__':
    main()