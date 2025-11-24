#!/usr/bin/env python3
"""Check system status and running processes"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.orm import Session

from app.executor.ledger import PredictionLog, Trade, engine
from app.utils.logging import configure_logging, get_logger


configure_logging("INFO")
logger = get_logger(__name__)


def check_running_processes():
    """Check what's running"""
    logger.info("=== Running Processes ===")
    
    result = subprocess.run(
        ["ps", "aux"],
        capture_output=True,
        text=True
    )
    
    for line in result.stdout.split("\n"):
        if "python" in line and "trading" in line and "grep" not in line:
            logger.info(line.strip())


def check_database_stats():
    """Check database statistics"""
    logger.info("\n=== Database Statistics ===")
    
    with Session(engine) as session:
        # Predictions
        total_predictions = session.query(PredictionLog).count()
        pending = session.query(PredictionLog).filter_by(result_collected=False).count()
        collected = session.query(PredictionLog).filter_by(result_collected=True).count()
        glm_feedbacks = session.query(PredictionLog).filter_by(glm_feedback_collected=True).count()
        
        # Recent predictions
        recent = (
            session.query(PredictionLog)
            .order_by(PredictionLog.timestamp.desc())
            .limit(5)
            .all()
        )
        
        # Trades
        total_trades = session.query(Trade).count()
        
        logger.info("Predictions:")
        logger.info("  Total: %d", total_predictions)
        logger.info("  Pending collection: %d", pending)
        logger.info("  Results collected: %d", collected)
        logger.info("  GLM feedbacks: %d", glm_feedbacks)
        
        logger.info("\nTrades:")
        logger.info("  Total trades: %d", total_trades)
        
        if recent:
            logger.info("\nRecent 5 Predictions:")
            for pred in recent:
                from datetime import datetime
                age = (datetime.utcnow() - pred.timestamp).total_seconds() / 60
                logger.info(
                    "  %s score=%.2f age=%.1fm collected=%s",
                    pred.predicted_direction,
                    pred.model_score,
                    age,
                    pred.result_collected,
                )


def main():
    """Run all checks"""
    logger.info("=== System Status Check ===\n")
    
    try:
        check_running_processes()
        check_database_stats()
        
        logger.info("\n✅ Status check complete")
        
    except Exception as exc:
        logger.error("❌ Check failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
