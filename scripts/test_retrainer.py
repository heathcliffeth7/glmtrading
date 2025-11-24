#!/usr/bin/env python3
"""Test active learning retrainer"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# DISABLE TELEGRAM FOR TESTS
os.environ["TELEGRAM_ENABLED"] = "false"

import random
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.agents.feedback.retrainer import ActiveLearningRetrainer
from app.executor.ledger import PredictionLog, engine
from app.utils.logging import configure_logging, get_logger

configure_logging("INFO")
logger = get_logger(__name__)


def create_realistic_feedback_data(count: int = 100):
    """Create realistic feedback data for testing"""
    logger.info("=== Creating %d Realistic Feedback Samples ===", count)
    
    with Session(engine) as session:
        for i in range(count):
            # Random features with realistic values
            long_short_ratio = random.uniform(0.8, 1.2)
            open_interest = random.uniform(2000000, 3000000)
            funding_rate = random.uniform(-0.001, 0.001)
            ema_20 = random.uniform(65000, 70000)
            ema_50 = random.uniform(64000, 69000)
            rsi_14 = random.uniform(30, 70)
            close_price = random.uniform(66000, 68000)
            
            # Model prediction (biased towards certain conditions)
            # High LSR → BUY prediction (but not always correct)
            if long_short_ratio > 1.1 and funding_rate > 0.0005:
                model_score = random.uniform(0.65, 0.85)
                predicted_direction = "BUY"
            elif long_short_ratio < 0.9 and funding_rate < -0.0005:
                model_score = random.uniform(0.15, 0.35)
                predicted_direction = "SELL"
            else:
                model_score = random.uniform(0.4, 0.6)
                predicted_direction = "HOLD"
            
            confidence = abs(model_score - 0.5) * 2
            
            # Actual result (with some randomness + realistic correlation)
            # High funding rate → often leads to reversal (model might be wrong)
            if funding_rate > 0.0008:
                # Strong funding rate → price often drops (short squeeze avoided)
                actual_change = random.uniform(-2.0, -0.5)
            elif funding_rate < -0.0008:
                # Negative funding → price often rises
                actual_change = random.uniform(0.5, 2.0)
            elif long_short_ratio > 1.15:
                # Very high LSR → reversal risk
                actual_change = random.uniform(-1.5, 0.5)
            elif rsi_14 > 70:
                # Overbought → likely correction
                actual_change = random.uniform(-1.0, 0.0)
            elif rsi_14 < 30:
                # Oversold → likely bounce
                actual_change = random.uniform(0.0, 1.0)
            else:
                # Normal conditions
                actual_change = random.uniform(-1.0, 1.0)
            
            actual_direction = "BUY" if actual_change > 0.5 else "SELL" if actual_change < -0.5 else "HOLD"
            
            # GLM feedback (simulated intelligent feedback)
            # GLM is "smarter" - detects when model missed important signals
            glm_feedback_collected = random.random() < 0.7  # 70% have GLM feedback
            
            if glm_feedback_collected:
                # GLM correctly identifies model errors
                model_correct = (predicted_direction == actual_direction)
                
                if not model_correct:
                    # GLM flags this as error
                    glm_correct = False
                    
                    # GLM identifies important feature
                    if abs(funding_rate) > 0.0005:
                        glm_important_feature = "funding_rate"
                    elif abs(long_short_ratio - 1.0) > 0.15:
                        glm_important_feature = "long_short_ratio"
                    elif rsi_14 > 70 or rsi_14 < 30:
                        glm_important_feature = "rsi_14"
                    else:
                        glm_important_feature = "ema_20"
                    
                    glm_reasoning = f"Model missed {glm_important_feature} signal"
                else:
                    # GLM agrees with model
                    glm_correct = True
                    glm_important_feature = "funding_rate"  # Most common
                    glm_reasoning = "Model correctly identified market condition"
            else:
                glm_correct = None
                glm_important_feature = None
                glm_reasoning = None
            
            # Create prediction log
            prediction = PredictionLog(
                symbol="BTCUSDT",
                timestamp=datetime.utcnow() - timedelta(days=random.randint(1, 7)),
                long_short_ratio=long_short_ratio,
                open_interest=open_interest,
                funding_rate=funding_rate,
                ema_20=ema_20,
                ema_50=ema_50,
                rsi_14=rsi_14,
                close_price=close_price,
                model_score=model_score,
                predicted_direction=predicted_direction,
                confidence=confidence,
                result_collected=True,
                minutes_after=15,
                actual_price_change_pct=actual_change,
                actual_direction=actual_direction,
                glm_feedback_collected=glm_feedback_collected,
                glm_correct=glm_correct,
                glm_important_feature=glm_important_feature,
                glm_reasoning=glm_reasoning,
            )
            
            session.add(prediction)
        
        session.commit()
        logger.info("✅ Created %d realistic feedback samples", count)


def test_retrainer_report():
    """Test 1: Generate retraining report"""
    logger.info("\n=== Test 1: Retraining Report ===")
    
    retrainer = ActiveLearningRetrainer()
    report = retrainer.generate_report(days=7)
    
    logger.info("Retraining Report:")
    logger.info("  Total Samples: %d", report.get("total_samples", 0))
    logger.info("  GLM Feedback Rate: %.1f%%", report.get("glm_feedback_rate", 0) * 100)
    logger.info("  Model Accuracy: %.1f%%", report.get("model_accuracy", 0) * 100)
    logger.info("  GLM Agreement Rate: %.1f%%", report.get("glm_agreement_rate", 0) * 100)
    logger.info("  GLM Flagged Errors: %d", report.get("glm_flagged_errors", 0))
    logger.info("  Ready for Retrain: %s", report.get("ready_for_retrain", False))
    
    if report.get("important_features"):
        logger.info("  Top Important Features:")
        for feature, count in report["important_features"].items():
            logger.info("    - %s: %d mentions", feature, count)


def test_retrainer_dry_run():
    """Test 2: Dry run retraining"""
    logger.info("\n=== Test 2: Dry Run Retraining ===")
    
    retrainer = ActiveLearningRetrainer(min_samples=50)
    result = retrainer.retrain(days=7, dry_run=True)
    
    logger.info("Dry Run Result:")
    logger.info("  Success: %s", result.get("success", False))
    logger.info("  Samples Used: %d", result.get("samples_used", 0))
    logger.info("  Error-Weighted Samples: %d", result.get("error_weighted_samples", 0))
    
    if "old_accuracy" in result:
        logger.info("  Old Accuracy: %.2f%%", result["old_accuracy"] * 100)
        logger.info("  New Accuracy: %.2f%%", result["new_accuracy"] * 100)
        logger.info("  Improvement: %+.2f%%", result["improvement"] * 100)


def test_actual_retraining():
    """Test 3: Actual retraining (save model)"""
    logger.info("\n=== Test 3: Actual Retraining ===")
    
    # Backup existing model
    model_path = Path("models/derivatives.joblib")
    backup_path = Path("models/derivatives.backup.joblib")
    
    if model_path.exists():
        import shutil
        shutil.copy2(model_path, backup_path)
        logger.info("Backed up existing model to %s", backup_path)
    
    retrainer = ActiveLearningRetrainer(min_samples=50)
    result = retrainer.retrain(days=7, dry_run=False)
    
    logger.info("Actual Retraining Result:")
    logger.info("  Success: %s", result.get("success", False))
    logger.info("  Samples Used: %d", result.get("samples_used", 0))
    
    if result.get("success"):
        logger.info("  ✅ New model saved!")
        logger.info("  Old Accuracy: %.2f%%", result["old_accuracy"] * 100)
        logger.info("  New Accuracy: %.2f%%", result["new_accuracy"] * 100)
        logger.info("  Improvement: %+.2f%%", result["improvement"] * 100)
        
        # Check if model file exists
        if model_path.exists():
            logger.info("  ✅ Model file verified: %s", model_path)
        
        # Check archive
        archive_dir = Path("models/archive")
        if archive_dir.exists():
            archives = list(archive_dir.glob("derivatives_*.joblib"))
            logger.info("  ✅ Archived models: %d", len(archives))
    else:
        logger.warning("  ⚠️  Retraining failed or model not improved")


def test_load_and_predict():
    """Test 4: Load retrained model and make prediction"""
    logger.info("\n=== Test 4: Load and Predict with Retrained Model ===")
    
    model_path = Path("models/derivatives.joblib")
    
    if not model_path.exists():
        logger.warning("No model found at %s", model_path)
        return
    
    import joblib
    model = joblib.load(model_path)
    
    # Test prediction
    test_features = [[
        1.05,      # long_short_ratio
        2500000,   # open_interest
        0.0008,    # funding_rate
        67000,     # ema_20
        66500,     # ema_50
        55,        # rsi_14
    ]]
    
    prediction = model.predict(test_features)
    proba = model.predict_proba(test_features)
    
    logger.info("Test Prediction:")
    logger.info("  Features: LSR=1.05 OI=2.5M FR=0.0008 EMA20=67k RSI=55")
    logger.info("  Prediction: %s", "BUY" if prediction[0] == 1 else "NOT_BUY")
    logger.info("  Probability: BUY=%.2f%% NOT_BUY=%.2f%%", 
               proba[0][1] * 100, proba[0][0] * 100)
    logger.info("  ✅ Model loaded and working!")


def main():
    """Run all tests"""
    logger.info("=== Active Learning Retrainer Tests ===\n")
    
    try:
        # Create test data
        create_realistic_feedback_data(count=100)
        
        # Run tests
        test_retrainer_report()
        test_retrainer_dry_run()
        test_actual_retraining()
        test_load_and_predict()
        
        logger.info("\n✅ All retrainer tests completed!")
        
    except Exception as exc:
        logger.error("❌ Test failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
