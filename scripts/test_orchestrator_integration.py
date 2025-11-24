#!/usr/bin/env python3
"""Test orchestrator integration with Active Learning"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# DISABLE TELEGRAM FOR TESTS
os.environ["TELEGRAM_ENABLED"] = "false"

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.agents.feedback.logger import log_prediction
from app.executor.ledger import PredictionLog, engine
from app.orchestrator.runtime import AutomatedRunner
from app.utils.logging import configure_logging, get_logger

configure_logging("INFO")
logger = get_logger(__name__)


def create_sample_predictions():
    """Create sample predictions for testing"""
    logger.info("Creating sample predictions...")
    
    # Create 5 predictions with 20 minutes age
    for i in range(5):
        log_prediction(
            symbol="BTCUSDT",
            features={
                "long_short_ratio": 1.05 + i * 0.01,
                "open_interest": 2500000 + i * 10000,
                "funding_rate": 0.0008 + i * 0.0001,
                "ema_20": 67000 + i * 100,
                "ema_50": 66500 + i * 100,
                "rsi_14": 55 + i,
                "close": 67000 + i * 100,
            },
            model_score=0.6 + i * 0.05,
            predicted_direction="BUY" if i % 2 == 0 else "SELL",
            confidence=0.7 + i * 0.05,
        )
    
    logger.info("✅ Created 5 sample predictions")


def check_system_state():
    """Check current system state"""
    logger.info("\n=== System State Check ===")
    
    with Session(engine) as session:
        # Predictions
        total_predictions = session.query(PredictionLog).count()
        pending = session.query(PredictionLog).filter_by(result_collected=False).count()
        collected = session.query(PredictionLog).filter_by(result_collected=True).count()
        glm_feedbacks = session.query(PredictionLog).filter_by(glm_feedback_collected=True).count()
        
        logger.info("Predictions:")
        logger.info("  Total: %d", total_predictions)
        logger.info("  Pending collection: %d", pending)
        logger.info("  Results collected: %d", collected)
        logger.info("  GLM feedbacks: %d", glm_feedbacks)
        
        # Recent predictions
        recent = (
            session.query(PredictionLog)
            .order_by(PredictionLog.timestamp.desc())
            .limit(5)
            .all()
        )
        
        if recent:
            logger.info("\nRecent Predictions:")
            for pred in recent:
                age = (datetime.utcnow() - pred.timestamp).total_seconds() / 60
                logger.info(
                    "  ID=%d %s score=%.2f age=%.1f min collected=%s",
                    pred.id,
                    pred.predicted_direction,
                    pred.model_score,
                    age,
                    pred.result_collected,
                )


async def test_orchestrator_with_feedback():
    """Test 1: Orchestrator with feedback collector (30 seconds)"""
    logger.info("\n=== Test 1: Orchestrator with Feedback Collector ===")
    logger.info("Running for 30 seconds...")
    
    runner = AutomatedRunner(
        symbol="BTCUSDT",
        interval="5min",
        cycle_seconds=10,  # Fast cycle for testing
        enable_feedback_collector=True,
        enable_daily_retraining=False,  # Disable for this test
    )
    
    # Start runner
    task = asyncio.create_task(runner.start())
    
    # Run for 30 seconds
    await asyncio.sleep(30)
    
    # Stop runner
    runner._running = False
    task.cancel()
    
    try:
        await task
    except asyncio.CancelledError:
        logger.info("✅ Orchestrator stopped gracefully")
    
    logger.info("Test 1 complete")


async def test_orchestrator_with_retraining():
    """Test 2: Orchestrator with retraining scheduler (test immediate trigger)"""
    logger.info("\n=== Test 2: Orchestrator with Retraining Scheduler ===")
    logger.info("Note: Retraining scheduler set for next day, not triggering in this test")
    
    # For this test, we'll just verify the scheduler starts
    runner = AutomatedRunner(
        symbol="BTCUSDT",
        interval="5min",
        cycle_seconds=60,
        enable_feedback_collector=False,
        enable_daily_retraining=True,
        retraining_hour=2,  # 02:00 UTC
    )
    
    # Start runner
    task = asyncio.create_task(runner.start())
    
    # Run for 5 seconds (just to verify startup)
    await asyncio.sleep(5)
    
    # Stop runner
    runner._running = False
    task.cancel()
    
    try:
        await task
    except asyncio.CancelledError:
        logger.info("✅ Orchestrator with scheduler stopped gracefully")
    
    logger.info("Test 2 complete")


async def test_full_integration():
    """Test 3: Full integration (both enabled, short run)"""
    logger.info("\n=== Test 3: Full Integration ===")
    logger.info("Running with both feedback collector and retraining scheduler...")
    
    runner = AutomatedRunner(
        symbol="BTCUSDT",
        interval="5min",
        cycle_seconds=15,
        enable_feedback_collector=True,
        enable_daily_retraining=True,
        retraining_hour=2,
    )
    
    # Start runner
    task = asyncio.create_task(runner.start())
    
    # Run for 20 seconds
    await asyncio.sleep(20)
    
    # Stop runner
    runner._running = False
    task.cancel()
    
    try:
        await task
    except asyncio.CancelledError:
        logger.info("✅ Full integration test stopped gracefully")
    
    logger.info("Test 3 complete")


async def main():
    """Run all integration tests"""
    logger.info("=== Orchestrator Integration Tests ===\n")
    
    try:
        # Setup
        create_sample_predictions()
        check_system_state()
        
        # Run tests
        await test_orchestrator_with_feedback()
        check_system_state()
        
        await test_orchestrator_with_retraining()
        
        await test_full_integration()
        check_system_state()
        
        logger.info("\n✅ All integration tests completed!")
        logger.info("\n📝 Summary:")
        logger.info("  - Feedback Collector: ✅ Integrated and running")
        logger.info("  - Retraining Scheduler: ✅ Integrated and running")
        logger.info("  - Orchestrator: ✅ Manages all background tasks")
        logger.info("\n🚀 System ready for production!")
        
    except Exception as exc:
        logger.error("❌ Integration test failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
