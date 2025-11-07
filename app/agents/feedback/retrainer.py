"""Active Learning Retrainer - GLM feedback'leri ile model'i yeniden eğit"""
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sqlalchemy.orm import Session

from app.executor.ledger import PredictionLog, engine
from app.utils.logging import get_logger
from app.utils.telegram import format_markdown, telegram_client


logger = get_logger(__name__)


class ActiveLearningRetrainer:
    """
    GLM feedback'lerini kullanarak derivatives model'ini yeniden eğit
    
    Workflow:
    1. Son N günün feedback'lerini topla (glm_feedback_collected=True)
    2. Training data hazırla (features + actual labels)
    3. GLM'in "yanlış" bulduğu örneklere 3x ağırlık ver
    4. Yeni model eğit
    5. Eski vs yeni model karşılaştır
    6. Daha iyiyse kaydet + arşivle
    7. Telegram bildirimi
    """
    
    def __init__(
        self,
        model_path: str = "models/derivatives.joblib",
        min_samples: int = 50,
        error_weight_multiplier: float = 3.0,
    ):
        self.model_path = Path(model_path)
        self.min_samples = min_samples
        self.error_weight = error_weight_multiplier
    
    def retrain(self, days: int = 7, dry_run: bool = False) -> dict:
        """
        Retrain model with feedback data
        
        Args:
            days: Number of days of feedback to use
            dry_run: If True, don't save model (just evaluate)
        
        Returns:
            {
                "success": bool,
                "old_accuracy": float,
                "new_accuracy": float,
                "improvement": float,
                "samples_used": int,
                "error_weighted_samples": int,
            }
        """
        logger.info("=== Active Learning Retraining Started ===")
        logger.info("Parameters: days=%d dry_run=%s min_samples=%d", days, dry_run, self.min_samples)
        
        # 1. Load feedback data
        feedbacks = self._load_feedbacks(days)
        
        if len(feedbacks) < self.min_samples:
            logger.warning(
                "Insufficient feedback data: %d samples (minimum %d required)",
                len(feedbacks),
                self.min_samples,
            )
            return {
                "success": False,
                "error": f"Insufficient data: {len(feedbacks)} < {self.min_samples}",
                "samples_used": len(feedbacks),
            }
        
        logger.info("Loaded %d feedback samples from last %d days", len(feedbacks), days)
        
        # 2. Prepare training data
        X, y, sample_weights = self._prepare_training_data(feedbacks)
        
        error_weighted_count = sum(1 for w in sample_weights if w > 1.0)
        logger.info(
            "Training data: %d samples, %d error-weighted (%.1f%%)",
            len(X),
            error_weighted_count,
            (error_weighted_count / len(X)) * 100,
        )
        
        # 3. Load old model (if exists)
        old_model = None
        old_accuracy = 0.0
        
        if self.model_path.exists():
            try:
                old_model = joblib.load(self.model_path)
                old_accuracy = self._evaluate_model(old_model, feedbacks)
                logger.info("Old model accuracy: %.2f%%", old_accuracy * 100)
            except Exception as exc:
                logger.warning("Could not load old model: %s", exc)
        else:
            logger.info("No existing model found, training from scratch")
        
        # 4. Train new model
        new_model = self._train_model(X, y, sample_weights)
        new_accuracy = self._evaluate_model(new_model, feedbacks)
        
        logger.info("New model accuracy: %.2f%%", new_accuracy * 100)
        
        # 5. Compare and decide
        improvement = new_accuracy - old_accuracy
        logger.info("Improvement: %+.2f%%", improvement * 100)
        
        result = {
            "success": False,
            "old_accuracy": old_accuracy,
            "new_accuracy": new_accuracy,
            "improvement": improvement,
            "samples_used": len(feedbacks),
            "error_weighted_samples": error_weighted_count,
        }
        
        # 6. Save if better (or first model)
        if new_accuracy > old_accuracy or old_model is None:
            if not dry_run:
                self._save_model(new_model, old_model)
                result["success"] = True
                logger.info("✅ New model saved (improvement: +%.2f%%)", improvement * 100)
                
                # 7. Telegram notification
                self._notify_retrain_success(result, feedbacks)
            else:
                logger.info("✅ Dry run: Would save model (improvement: +%.2f%%)", improvement * 100)
                result["success"] = True
        else:
            logger.warning("❌ New model not better than old, keeping old model")
            result["success"] = False
        
        logger.info("=== Active Learning Retraining Complete ===")
        return result
    
    def _load_feedbacks(self, days: int) -> list[PredictionLog]:
        """Load feedback data from last N days"""
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        with Session(engine) as session:
            feedbacks = (
                session.query(PredictionLog)
                .filter(
                    PredictionLog.result_collected == True,  # noqa: E712
                    PredictionLog.timestamp >= cutoff_date,
                )
                .order_by(PredictionLog.timestamp.desc())
                .all()
            )
        
        logger.info("Loaded %d feedbacks (result_collected=True) from last %d days", len(feedbacks), days)
        
        # Log statistics
        if feedbacks:
            glm_collected = sum(1 for f in feedbacks if f.glm_feedback_collected)
            correct = sum(1 for f in feedbacks if f.predicted_direction == f.actual_direction)
            
            logger.info("  - GLM feedback collected: %d (%.1f%%)", 
                       glm_collected, (glm_collected / len(feedbacks)) * 100)
            logger.info("  - Model correct: %d (%.1f%%)", 
                       correct, (correct / len(feedbacks)) * 100)
        
        return feedbacks
    
    def _prepare_training_data(self, feedbacks: list[PredictionLog]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Prepare training data with sample weights
        
        Returns:
            X: Feature matrix (N, 16) - with Twelve Data indicators
            y: Labels (N,) - binary: 1=BUY, 0=NOT_BUY
            sample_weights: Weights (N,) - higher for GLM-flagged errors
        """
        X, y, weights = [], [], []
        
        for feedback in feedbacks:
            # Features - All indicators
            X.append([
                # Futures
                feedback.long_short_ratio,
                feedback.open_interest,
                feedback.funding_rate,
                # Spot
                feedback.ema_20,
                feedback.ema_50,
                feedback.rsi_14,
                # Twelve Data
                feedback.rsi_twelvedata or 50.0,
                feedback.macd_twelvedata or 0.0,
                feedback.macd_signal_twelvedata or 0.0,
                feedback.macd_hist_twelvedata or 0.0,
                feedback.atr_twelvedata or 0.0,
                feedback.stoch_k or 50.0,
                feedback.stoch_d or 50.0,
                feedback.bb_upper or 0.0,
                feedback.bb_middle or 0.0,
                feedback.bb_lower or 0.0,
            ])
            
            # Label (actual result)
            # Binary classification: BUY (1) vs NOT-BUY (0)
            y.append(1 if feedback.actual_direction == "BUY" else 0)
            
            # Weight
            # 1. GLM'in "yanlış" dediği örneklere yüksek ağırlık
            if feedback.glm_feedback_collected and feedback.glm_correct is False:
                weights.append(self.error_weight)
            # 2. Model'in yanlış tahmin ettiği örneklere orta ağırlık
            elif feedback.predicted_direction != feedback.actual_direction:
                weights.append(2.0)
            # 3. Doğru tahminlere normal ağırlık
            else:
                weights.append(1.0)
        
        return np.array(X), np.array(y), np.array(weights)
    
    def _train_model(self, X: np.ndarray, y: np.ndarray, sample_weights: np.ndarray) -> RandomForestClassifier:
        """Train new model with weighted samples"""
        logger.info("Training new model with %d samples and %d features", len(X), X.shape[1])
        
        model = RandomForestClassifier(
            n_estimators=200,  # Daha fazla tree (16 feature için)
            max_depth=15,      # Daha derin tree'ler
            min_samples_split=10,
            min_samples_leaf=3,
            random_state=42,
            class_weight='balanced',  # Class imbalance için
            n_jobs=-1,  # Paralel training
        )
        
        # Train with sample weights
        model.fit(X, y, sample_weight=sample_weights)
        
        # Log feature importance
        feature_names = [
            # Futures
            "long_short_ratio",
            "open_interest",
            "funding_rate",
            # Spot
            "ema_20",
            "ema_50",
            "rsi_14",
            # Twelve Data
            "rsi_twelvedata",
            "macd_twelvedata",
            "macd_signal_twelvedata",
            "macd_hist_twelvedata",
            "atr_twelvedata",
            "stoch_k",
            "stoch_d",
            "bb_upper",
            "bb_middle",
            "bb_lower",
        ]
        
        importances = model.feature_importances_
        logger.info("Feature importances:")
        for name, importance in sorted(zip(feature_names, importances), key=lambda x: x[1], reverse=True):
            logger.info("  - %s: %.3f", name, importance)
        
        return model
    
    def _evaluate_model(self, model: RandomForestClassifier, feedbacks: list[PredictionLog]) -> float:
        """Evaluate model accuracy on feedback data"""
        if not feedbacks:
            return 0.0
        
        X = np.array([[
            # Futures
            f.long_short_ratio,
            f.open_interest,
            f.funding_rate,
            # Spot
            f.ema_20,
            f.ema_50,
            f.rsi_14,
            # Twelve Data
            f.rsi_twelvedata or 50.0,
            f.macd_twelvedata or 0.0,
            f.macd_signal_twelvedata or 0.0,
            f.macd_hist_twelvedata or 0.0,
            f.atr_twelvedata or 0.0,
            f.stoch_k or 50.0,
            f.stoch_d or 50.0,
            f.bb_upper or 0.0,
            f.bb_middle or 0.0,
            f.bb_lower or 0.0,
        ] for f in feedbacks])
        
        y_true = np.array([1 if f.actual_direction == "BUY" else 0 for f in feedbacks])
        
        try:
            y_pred = model.predict(X)
            accuracy = accuracy_score(y_true, y_pred)
            return accuracy
        except Exception as exc:
            logger.error("Model evaluation failed: %s", exc)
            return 0.0
    
    def _save_model(self, new_model: RandomForestClassifier, old_model: Optional[RandomForestClassifier]) -> None:
        """Save new model and archive old one"""
        # Create archive directory
        archive_dir = self.model_path.parent / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        
        # Archive old model (if exists)
        if old_model is not None and self.model_path.exists():
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            archive_path = archive_dir / f"derivatives_{timestamp}.joblib"
            
            try:
                shutil.copy2(self.model_path, archive_path)
                logger.info("Old model archived to: %s", archive_path)
            except Exception as exc:
                logger.error("Failed to archive old model: %s", exc)
        
        # Save new model
        try:
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(new_model, self.model_path)
            logger.info("New model saved to: %s", self.model_path)
        except Exception as exc:
            logger.error("Failed to save new model: %s", exc)
            raise
    
    def _notify_retrain_success(self, result: dict, feedbacks: list[PredictionLog]) -> None:
        """Send Telegram notification about retraining"""
        if not telegram_client.enabled():
            return
        
        try:
            glm_feedbacks = sum(1 for f in feedbacks if f.glm_feedback_collected)
            glm_errors = sum(1 for f in feedbacks if f.glm_feedback_collected and f.glm_correct is False)
            
            message = "\n".join([
                "*🔄 Model Retrained (Active Learning)*",
                "",
                "*📊 Training Data*",
                f"Total Samples: {result['samples_used']}",
                f"Error-Weighted: {result['error_weighted_samples']} ({(result['error_weighted_samples']/result['samples_used'])*100:.1f}%)",
                f"GLM Feedbacks: {glm_feedbacks}",
                f"GLM Flagged Errors: {glm_errors}",
                "",
                "*🎯 Performance*",
                f"Old Accuracy: {result['old_accuracy']*100:.2f}%",
                f"New Accuracy: {result['new_accuracy']*100:.2f}%",
                f"{'🟢' if result['improvement'] > 0 else '🔴'} Improvement: {result['improvement']*100:+.2f}%",
                "",
                "*✅ Status*",
                "New model saved and deployed",
                "Old model archived",
                "",
                f"🕒 {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC",
            ])
            
            telegram_client.send_message(message)
            logger.info("Retrain notification sent to Telegram")
            
        except Exception as exc:
            logger.error("Failed to send retrain notification: %s", exc)
    
    def generate_report(self, days: int = 7) -> dict:
        """Generate detailed retraining report (without actually retraining)"""
        logger.info("Generating retraining report for last %d days", days)
        
        feedbacks = self._load_feedbacks(days)
        
        if not feedbacks:
            return {
                "total_samples": 0,
                "error": "No feedback data available",
            }
        
        # Statistics
        total = len(feedbacks)
        glm_collected = sum(1 for f in feedbacks if f.glm_feedback_collected)
        model_correct = sum(1 for f in feedbacks if f.predicted_direction == f.actual_direction)
        glm_correct = sum(1 for f in feedbacks if f.glm_feedback_collected and f.glm_correct)
        glm_incorrect = sum(1 for f in feedbacks if f.glm_feedback_collected and f.glm_correct is False)
        
        # Feature statistics
        feature_stats = {}
        if glm_collected > 0:
            important_features = [f.glm_important_feature for f in feedbacks if f.glm_important_feature]
            from collections import Counter
            feature_counts = Counter(important_features)
            feature_stats = dict(feature_counts.most_common(5))
        
        report = {
            "total_samples": total,
            "glm_feedback_collected": glm_collected,
            "glm_feedback_rate": glm_collected / total if total > 0 else 0,
            "model_accuracy": model_correct / total if total > 0 else 0,
            "glm_agreement_rate": glm_correct / glm_collected if glm_collected > 0 else 0,
            "glm_flagged_errors": glm_incorrect,
            "important_features": feature_stats,
            "ready_for_retrain": total >= self.min_samples,
        }
        
        return report
