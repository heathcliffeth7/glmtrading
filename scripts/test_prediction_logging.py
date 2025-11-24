#!/usr/bin/env python3
"""Test prediction logging system"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# DISABLE TELEGRAM FOR TESTS
os.environ["TELEGRAM_ENABLED"] = "false"

from datetime import datetime

from sqlalchemy.orm import Session

from app.agents.derivatives import DerivativesAgent
from app.executor.ledger import (
    PredictionLog,
    engine,
    get_pending_predictions,
    update_prediction_result,
)
from app.utils.logging import configure_logging, get_logger

configure_logging("INFO")
logger = get_logger(__name__)


def test_direct_logging():
    """Test 1: Direkt prediction logging"""
    logger.info("=== Test 1: Direct Prediction Logging ===")
    
    from app.agents.feedback.logger import log_prediction
    
    test_features = {
        "long_short_ratio": 1.05,
        "open_interest": 1234567.89,
        "funding_rate": 0.0001,
        "ema_20": 95000.0,
        "ema_50": 94500.0,
        "rsi_14": 55.0,
        "close": 95123.45,
    }
    
    prediction_id = log_prediction(
        symbol="BTCUSDT",
        features=test_features,
        model_score=0.65,
        predicted_direction="BUY",
        confidence=0.8,
    )
    
    if prediction_id > 0:
        logger.info("✅ Prediction logged successfully: id=%d", prediction_id)
        
        # Verify in database
        with Session(engine) as session:
            pred = session.query(PredictionLog).filter_by(id=prediction_id).first()
            if pred:
                logger.info("✅ Prediction verified in DB:")
                logger.info("   - Symbol: %s", pred.symbol)
                logger.info("   - Direction: %s", pred.predicted_direction)
                logger.info("   - Score: %.4f", pred.model_score)
                logger.info("   - Features: LSR=%.4f OI=%.2f FR=%.6f", 
                           pred.long_short_ratio, pred.open_interest, pred.funding_rate)
            else:
                logger.error("❌ Prediction not found in DB")
    else:
        logger.error("❌ Failed to log prediction")


def test_agent_integration():
    """Test 2: DerivativesAgent integration"""
    logger.info("\n=== Test 2: DerivativesAgent Integration ===")
    
    agent = DerivativesAgent(symbol="BTCUSDT")
    
    try:
        signal = agent.generate_signal()
        logger.info("✅ Agent signal generated: %s (confidence=%.2f)", 
                   signal.direction, signal.confidence)
        
        # Check if prediction was logged
        with Session(engine) as session:
            recent_predictions = (
                session.query(PredictionLog)
                .filter_by(symbol="BTCUSDT")
                .order_by(PredictionLog.timestamp.desc())
                .limit(1)
                .all()
            )
            
            if recent_predictions:
                pred = recent_predictions[0]
                logger.info("✅ Latest prediction found:")
                logger.info("   - Direction: %s", pred.predicted_direction)
                logger.info("   - Timestamp: %s", pred.timestamp)
                logger.info("   - Score: %.4f", pred.model_score)
            else:
                logger.warning("⚠️  No predictions found (model might be missing)")
                
    except Exception as exc:
        logger.error("❌ Agent test failed: %s", exc, exc_info=True)


def test_pending_predictions():
    """Test 3: Query pending predictions"""
    logger.info("\n=== Test 3: Query Pending Predictions ===")
    
    with Session(engine) as session:
        # Get predictions older than 0 minutes (all uncollected)
        pending = get_pending_predictions(session, minutes_after=0, limit=10)
        
        logger.info("Found %d pending predictions (not collected yet)", len(pending))
        
        for pred in pending[:5]:  # Show first 5
            age_minutes = (datetime.utcnow() - pred.timestamp).total_seconds() / 60
            logger.info("   - ID=%d %s @ %.2f (age: %.1f min)", 
                       pred.id, pred.predicted_direction, pred.model_score, age_minutes)


def test_result_update():
    """Test 4: Update prediction result (simulated)"""
    logger.info("\n=== Test 4: Update Prediction Result ===")
    
    with Session(engine) as session:
        # Get any uncollected prediction
        pending = get_pending_predictions(session, minutes_after=0, limit=1)
        
        if not pending:
            logger.warning("⚠️  No pending predictions to update")
            return
        
        pred = pending[0]
        logger.info("Updating prediction ID=%d", pred.id)
        
        # Simulate: Price went up 1.5%
        update_prediction_result(
            session,
            prediction_id=pred.id,
            actual_price_change_pct=1.5,
            actual_direction="BUY",
            minutes_after=15,
        )
        session.commit()
        
        # Verify
        updated = session.query(PredictionLog).filter_by(id=pred.id).first()
        if updated and updated.result_collected:
            logger.info("✅ Result updated successfully:")
            logger.info("   - Predicted: %s (score=%.4f)", 
                       updated.predicted_direction, updated.model_score)
            logger.info("   - Actual: %s (change=%.2f%%)", 
                       updated.actual_direction, updated.actual_price_change_pct)
            logger.info("   - Correct: %s", 
                       updated.predicted_direction == updated.actual_direction)
        else:
            logger.error("❌ Failed to update result")


def test_database_stats():
    """Test 5: Database statistics"""
    logger.info("\n=== Test 5: Database Statistics ===")
    
    with Session(engine) as session:
        total_predictions = session.query(PredictionLog).count()
        collected = session.query(PredictionLog).filter_by(result_collected=True).count()
        pending = total_predictions - collected
        
        logger.info("Total predictions: %d", total_predictions)
        logger.info("Results collected: %d", collected)
        logger.info("Pending collection: %d", pending)
        
        if total_predictions > 0:
            logger.info("Collection rate: %.1f%%", (collected / total_predictions) * 100)


def main():
    """Run all tests"""
    logger.info("Starting Prediction Logging Tests\n")
    
    try:
        test_direct_logging()
        test_agent_integration()
        test_pending_predictions()
        test_result_update()
        test_database_stats()
        
        logger.info("\n✅ All tests completed!")
        
    except Exception as exc:
        logger.error("❌ Test suite failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
