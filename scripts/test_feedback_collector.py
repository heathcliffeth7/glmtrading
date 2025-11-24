#!/usr/bin/env python3
"""Test feedback collector service"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# DISABLE TELEGRAM FOR TESTS
os.environ["TELEGRAM_ENABLED"] = "false"

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.agents.feedback.collector import FeedbackCollector
from app.agents.feedback.logger import log_prediction
from app.executor.ledger import PredictionLog, engine
from app.utils.logging import configure_logging, get_logger

configure_logging("INFO")
logger = get_logger(__name__)


def create_test_predictions():
    """Create test predictions with various timestamps"""
    logger.info("=== Creating Test Predictions ===")
    
    # Create predictions at different times
    test_cases = [
        {
            "time_ago_minutes": 20,  # Old enough to collect
            "features": {
                "long_short_ratio": 1.15,
                "open_interest": 2500000.0,
                "funding_rate": 0.0008,
                "ema_20": 67000.0,
                "ema_50": 66500.0,
                "rsi_14": 65.0,
                "close": 67123.45,
            },
            "score": 0.75,
            "direction": "BUY",
            "confidence": 0.85,
        },
        {
            "time_ago_minutes": 18,
            "features": {
                "long_short_ratio": 0.85,
                "open_interest": 2400000.0,
                "funding_rate": -0.0005,
                "ema_20": 67100.0,
                "ema_50": 67200.0,
                "rsi_14": 45.0,
                "close": 67050.0,
            },
            "score": 0.35,
            "direction": "SELL",
            "confidence": 0.70,
        },
        {
            "time_ago_minutes": 5,  # Too recent to collect
            "features": {
                "long_short_ratio": 1.0,
                "open_interest": 2450000.0,
                "funding_rate": 0.0001,
                "ema_20": 67200.0,
                "ema_50": 67150.0,
                "rsi_14": 52.0,
                "close": 67200.0,
            },
            "score": 0.55,
            "direction": "HOLD",
            "confidence": 0.50,
        },
    ]
    
    created_ids = []
    
    for i, test_case in enumerate(test_cases, 1):
        # Create prediction with backdated timestamp
        with Session(engine) as session:
            prediction = PredictionLog(
                symbol="BTCUSDT",
                timestamp=datetime.utcnow() - timedelta(minutes=test_case["time_ago_minutes"]),
                long_short_ratio=test_case["features"]["long_short_ratio"],
                open_interest=test_case["features"]["open_interest"],
                funding_rate=test_case["features"]["funding_rate"],
                ema_20=test_case["features"]["ema_20"],
                ema_50=test_case["features"]["ema_50"],
                rsi_14=test_case["features"]["rsi_14"],
                close_price=test_case["features"]["close"],
                model_score=test_case["score"],
                predicted_direction=test_case["direction"],
                confidence=test_case["confidence"],
            )
            session.add(prediction)
            session.commit()
            session.refresh(prediction)
            
            created_ids.append(prediction.id)
            logger.info(
                "Created test prediction %d: id=%d direction=%s age=%d min",
                i,
                prediction.id,
                test_case["direction"],
                test_case["time_ago_minutes"],
            )
    
    return created_ids


def check_collection_results(prediction_ids: list[int]):
    """Check if results were collected"""
    logger.info("\n=== Checking Collection Results ===")
    
    with Session(engine) as session:
        for pred_id in prediction_ids:
            pred = session.query(PredictionLog).filter_by(id=pred_id).first()
            
            if not pred:
                logger.error("Prediction %d not found", pred_id)
                continue
            
            age_minutes = (datetime.utcnow() - pred.timestamp).total_seconds() / 60
            
            logger.info("Prediction %d (age: %.1f min):", pred_id, age_minutes)
            logger.info("  - Predicted: %s (score=%.4f)", pred.predicted_direction, pred.model_score)
            
            if pred.result_collected:
                logger.info("  ✅ Result collected:")
                logger.info("     - Actual: %s (change=%.2f%%)", 
                           pred.actual_direction, pred.actual_price_change_pct)
                logger.info("     - Correct: %s", 
                           pred.predicted_direction == pred.actual_direction)
                
                if pred.glm_feedback_collected:
                    logger.info("  ✅ GLM feedback:")
                    logger.info("     - GLM says correct: %s", pred.glm_correct)
                    logger.info("     - Important feature: %s", pred.glm_important_feature)
                    logger.info("     - Reasoning: %s", pred.glm_reasoning[:100])
                else:
                    logger.info("  ⏳ GLM feedback not collected yet")
            else:
                logger.info("  ⏳ Result not collected yet (too recent)")


async def test_collector_single_cycle():
    """Test single collection cycle"""
    logger.info("\n=== Test: Single Collector Cycle ===")
    
    # Create collector (disable GLM for faster test)
    collector = FeedbackCollector(
        check_interval_minutes=5,
        result_after_minutes=15,
        batch_size=20,
        enable_glm_feedback=False,  # Disable for speed
    )
    
    # Run single cycle
    await collector._collect_cycle()
    
    logger.info("✅ Single cycle completed")


async def test_collector_with_glm():
    """Test collector with GLM feedback"""
    logger.info("\n=== Test: Collector with GLM Feedback ===")
    
    # Create collector with GLM enabled
    collector = FeedbackCollector(
        check_interval_minutes=5,
        result_after_minutes=15,
        batch_size=5,
        enable_glm_feedback=True,
    )
    
    # Run single cycle
    await collector._collect_cycle()
    
    logger.info("✅ GLM feedback cycle completed")


async def test_collector_loop():
    """Test collector continuous loop (run for 30 seconds)"""
    logger.info("\n=== Test: Collector Loop (30 seconds) ===")
    
    collector = FeedbackCollector(
        check_interval_minutes=0.5,  # Check every 30 seconds
        result_after_minutes=15,
        batch_size=20,
        enable_glm_feedback=True,
    )
    
    # Start collector
    task = asyncio.create_task(collector.start())
    
    # Run for 30 seconds
    await asyncio.sleep(30)
    
    # Stop collector
    collector.stop()
    task.cancel()
    
    try:
        await task
    except asyncio.CancelledError:
        logger.info("✅ Collector loop stopped gracefully")


async def main():
    """Run all tests"""
    logger.info("=== Feedback Collector Tests ===\n")
    
    try:
        # 1. Create test data
        prediction_ids = create_test_predictions()
        
        # 2. Test single cycle (no GLM)
        await test_collector_single_cycle()
        
        # 3. Check results
        check_collection_results(prediction_ids)
        
        # 4. Test with GLM (if you want to test GLM integration)
        # Uncomment below to test GLM feedback
        # await test_collector_with_glm()
        # check_collection_results(prediction_ids)
        
        # 5. Test continuous loop (optional)
        # await test_collector_loop()
        
        logger.info("\n✅ All tests completed!")
        
    except Exception as exc:
        logger.error("❌ Test failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
