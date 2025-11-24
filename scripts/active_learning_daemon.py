#!/usr/bin/env python3
"""
Active Learning Daemon - Continuously monitors and retrains model
Runs in background as systemd service
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import time
import asyncio
from datetime import datetime, timedelta
import sqlite3

from app.executor.ledger import engine
from app.utils.logging import get_logger
from scripts.retrain_derivatives_active_learning import (
    fetch_active_learning_data,
    create_labels_from_actuals,
    train_model,
    save_model,
)

logger = get_logger(__name__)


class ActiveLearningDaemon:
    def __init__(
        self,
        check_interval_minutes: int = 30,
        min_new_predictions: int = 10,
        min_total_predictions: int = 20,
    ):
        self.check_interval = check_interval_minutes * 60  # Convert to seconds
        self.min_new_predictions = min_new_predictions
        self.min_total_predictions = min_total_predictions
        self.last_retrain_time = None
        self.last_prediction_count = 0
        
    def get_prediction_count(self) -> int:
        """Get count of predictions with actual results"""
        conn = sqlite3.connect('portfolio.db')
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM prediction_logs WHERE result_collected=1')
        count = c.fetchone()[0]
        conn.close()
        return count
    
    def should_retrain(self) -> tuple[bool, str]:
        """
        Check if model should be retrained
        
        Returns:
            (should_retrain, reason)
        """
        current_count = self.get_prediction_count()
        
        # 1. First time check (no previous retrain)
        if self.last_retrain_time is None:
            if current_count >= self.min_total_predictions:
                return True, f"Initial training with {current_count} predictions"
            else:
                return False, f"Waiting for minimum predictions ({current_count}/{self.min_total_predictions})"
        
        # 2. Check if enough new predictions accumulated
        new_predictions = current_count - self.last_prediction_count
        if new_predictions >= self.min_new_predictions:
            return True, f"New predictions accumulated: {new_predictions} (threshold: {self.min_new_predictions})"
        
        # 3. Check if enough time passed (minimum 1 hour since last retrain)
        time_since_retrain = datetime.utcnow() - self.last_retrain_time
        if time_since_retrain < timedelta(hours=1):
            return False, f"Too soon since last retrain ({time_since_retrain.total_seconds()/60:.1f} min ago)"
        
        # 4. Daily retrain at 02:00 UTC (if not done recently)
        now = datetime.utcnow()
        if now.hour == 2 and time_since_retrain > timedelta(hours=12):
            return True, f"Daily scheduled retrain (02:00 UTC)"
        
        return False, f"No retrain needed (new: {new_predictions}/{self.min_new_predictions})"
    
    def retrain(self):
        """Execute model retraining"""
        try:
            logger.info("="*60)
            logger.info("Starting Active Learning Retrain...")
            logger.info("="*60)
            
            # 1. Fetch data
            df = fetch_active_learning_data(min_samples=self.min_total_predictions)
            if df is None:
                logger.warning("Not enough data for retraining")
                return False
            
            # 2. Create labels
            df_labeled = create_labels_from_actuals(df)
            if df_labeled.empty:
                logger.error("No labeled data after processing")
                return False
            
            # 3. Train model
            model = train_model(df_labeled)
            if model is None:
                logger.error("Training failed")
                return False
            
            # 4. Save model
            save_model(model, "models/derivatives.joblib")
            
            logger.info("="*60)
            logger.info("✅ Active Learning Retrain Complete!")
            logger.info("="*60)
            
            # Update state
            self.last_retrain_time = datetime.utcnow()
            self.last_prediction_count = self.get_prediction_count()
            
            return True
            
        except Exception as e:
            logger.error(f"Retrain failed with error: {e}", exc_info=True)
            return False
    
    async def run_forever(self):
        """Main daemon loop"""
        logger.info("="*60)
        logger.info("Active Learning Daemon Started")
        logger.info("="*60)
        logger.info(f"Configuration:")
        logger.info(f"  Check interval: {self.check_interval/60:.0f} minutes")
        logger.info(f"  Min new predictions: {self.min_new_predictions}")
        logger.info(f"  Min total predictions: {self.min_total_predictions}")
        logger.info("="*60)
        
        while True:
            try:
                # Check if retrain is needed
                should_retrain, reason = self.should_retrain()
                
                current_count = self.get_prediction_count()
                logger.info(
                    f"Status check | predictions={current_count} | should_retrain={should_retrain} | reason={reason}"
                )
                
                if should_retrain:
                    logger.info("🔄 Triggering model retrain...")
                    success = self.retrain()
                    if success:
                        logger.info("✅ Retrain successful")
                    else:
                        logger.warning("⚠️  Retrain failed or skipped")
                else:
                    logger.debug(f"Skipping retrain: {reason}")
                
                # Wait for next check
                logger.info(f"Next check in {self.check_interval/60:.0f} minutes...")
                await asyncio.sleep(self.check_interval)
                
            except KeyboardInterrupt:
                logger.info("Received interrupt signal, shutting down...")
                break
            except Exception as e:
                logger.error(f"Daemon error: {e}", exc_info=True)
                logger.info("Waiting 5 minutes before retry...")
                await asyncio.sleep(300)  # Wait 5 min on error


async def main():
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description='Active Learning Daemon')
    parser.add_argument(
        '--check-interval',
        type=int,
        default=30,
        help='Minutes between checks (default: 30)'
    )
    parser.add_argument(
        '--min-new-predictions',
        type=int,
        default=10,
        help='Minimum new predictions to trigger retrain (default: 10)'
    )
    parser.add_argument(
        '--min-total-predictions',
        type=int,
        default=20,
        help='Minimum total predictions for initial training (default: 20)'
    )
    
    args = parser.parse_args()
    
    # Create and run daemon
    daemon = ActiveLearningDaemon(
        check_interval_minutes=args.check_interval,
        min_new_predictions=args.min_new_predictions,
        min_total_predictions=args.min_total_predictions,
    )
    
    await daemon.run_forever()


if __name__ == '__main__':
    asyncio.run(main())
