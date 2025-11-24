#!/usr/bin/env python3
"""
Active Learning Retrain - Use REAL prediction results + GLM feedback
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score
import joblib
from datetime import datetime

from app.executor.ledger import engine
from app.utils.logging import get_logger

logger = get_logger(__name__)

# 25 features (20 Binance + 5 TwelveData)
FEATURES = [
    # Futures metrics (3)
    "long_short_ratio",
    "open_interest",
    "funding_rate",
    
    # Binance spot indicators (17)
    "close",
    "ema_20",
    "ema_50",
    "rsi_14",
    "macd",
    "macd_signal",
    "atr_14",
    "stoch_k",
    "stoch_d",
    "bb_upper",
    "bb_middle",
    "bb_lower",
    "willr",
    "cci",
    "mfi",
    "obv",
    "vwap_20",
    
    # TwelveData special indicators (5)
    "sar",
    "ichimoku_base",
    "ichimoku_conversion",
    "ichimoku_span_a",
    "ichimoku_span_b",
]


def fetch_active_learning_data(min_samples: int = 20):
    """
    Fetch predictions with ACTUAL results from prediction_logs
    This is the REAL active learning data!
    """
    logger.info("Fetching Active Learning data from prediction_logs...")
    
    # Read from database
    query = """
    SELECT 
        -- Features (Futures)
        long_short_ratio, open_interest, funding_rate,
        
        -- Features (Binance spot)
        close_price as close, ema_20, ema_50, rsi_14,
        
        -- Features (need to map from prediction_logs columns)
        0.0 as macd, 0.0 as macd_signal, 0.0 as atr_14,
        stoch_k, stoch_d,
        bb_upper, bb_middle, bb_lower,
        0.0 as willr, 0.0 as cci, 0.0 as mfi, 0.0 as obv, 0.0 as vwap_20,
        
        -- TwelveData
        sar, ichimoku_base, ichimoku_conversion, ichimoku_span_a, ichimoku_span_b,
        
        -- Labels (REAL outcomes!)
        predicted_direction,
        actual_direction,
        actual_price_change_pct,
        
        -- GLM feedback (for weighting)
        glm_correct,
        glm_feedback_collected,
        confidence,
        model_score,
        timestamp
        
    FROM prediction_logs
    WHERE result_collected = 1  -- Only predictions with actual results
    ORDER BY timestamp DESC
    """
    
    df = pd.read_sql(query, engine)
    
    if len(df) < min_samples:
        logger.warning(f"Only {len(df)} samples with results (need {min_samples}+)")
        return None
    
    logger.info(f"Fetched {len(df)} predictions with ACTUAL results")
    return df


def create_labels_from_actuals(df: pd.DataFrame):
    """
    Create labels from ACTUAL outcomes, not predictions!
    
    Label strategy:
    1. If actual_direction == 'BUY' -> label = 1 (profitable to buy)
    2. If actual_direction == 'SELL' -> label = 0 (profitable to sell)
    3. If actual_direction == 'HOLD' -> depends on price change
    """
    df = df.copy()
    
    # Map actual direction to labels
    def map_actual_to_label(row):
        actual = row['actual_direction']
        price_change = row['actual_price_change_pct']
        
        if actual == 'BUY':
            return 1  # Market went up, BUY was correct
        elif actual == 'SELL':
            return 0  # Market went down, SELL was correct
        else:  # HOLD
            # Use price change as tie-breaker
            if price_change > 0.1:
                return 1  # Went up slightly
            elif price_change < -0.1:
                return 0  # Went down slightly
            else:
                return None  # Too neutral, skip
    
    df['label'] = df.apply(map_actual_to_label, axis=1)
    
    # Drop rows with no clear label
    df = df.dropna(subset=['label'])
    df['label'] = df['label'].astype(int)
    
    logger.info(f"Label distribution: BUY={df['label'].sum()} SELL={(~df['label'].astype(bool)).sum()}")
    
    return df


def calculate_sample_weights(df: pd.DataFrame):
    """
    Calculate sample weights based on GLM feedback
    
    Weight strategy:
    1. GLM said correct (glm_correct=1): weight = 2.0 (trust GLM)
    2. GLM said wrong (glm_correct=0): weight = 1.5 (learn from mistakes)
    3. No GLM feedback: weight = 1.0 (neutral)
    4. High confidence predictions: weight *= 1.2 (trust model confidence)
    """
    weights = np.ones(len(df))
    
    # GLM feedback weighting
    if 'glm_feedback_collected' in df.columns:
        glm_correct_mask = (df['glm_feedback_collected'] == 1) & (df['glm_correct'] == 1)
        glm_wrong_mask = (df['glm_feedback_collected'] == 1) & (df['glm_correct'] == 0)
        
        weights[glm_correct_mask] *= 2.0  # Trust GLM when it says correct
        weights[glm_wrong_mask] *= 1.5    # Learn from mistakes
        
        logger.info(f"GLM weighting: {glm_correct_mask.sum()} correct (2.0x), {glm_wrong_mask.sum()} wrong (1.5x)")
    
    # Confidence weighting
    if 'confidence' in df.columns:
        high_conf_mask = df['confidence'] > 0.7
        weights[high_conf_mask] *= 1.2
        logger.info(f"Confidence weighting: {high_conf_mask.sum()} high confidence (1.2x)")
    
    return weights


def train_model(df: pd.DataFrame):
    """Train RandomForest with ACTIVE LEARNING data"""
    
    # Check features
    missing = set(FEATURES) - set(df.columns)
    if missing:
        logger.error(f"Missing features: {missing}")
        logger.info(f"Available columns: {df.columns.tolist()}")
        return None
    
    # Prepare features and labels
    X = df[FEATURES].copy()
    y = df['label'].copy()
    
    # Fill NaN with defaults
    X = X.fillna({
        'long_short_ratio': 1.0,
        'open_interest': 0.0,
        'funding_rate': 0.0,
        'close': 0.0,
        'ema_20': 0.0,
        'ema_50': 0.0,
        'rsi_14': 50.0,
        'macd': 0.0,
        'macd_signal': 0.0,
        'atr_14': 0.0,
        'stoch_k': 50.0,
        'stoch_d': 50.0,
        'bb_upper': 0.0,
        'bb_middle': 0.0,
        'bb_lower': 0.0,
        'willr': -50.0,
        'cci': 0.0,
        'mfi': 50.0,
        'obv': 0.0,
        'vwap_20': 0.0,
        'sar': 0.0,
        'ichimoku_base': 0.0,
        'ichimoku_conversion': 0.0,
        'ichimoku_span_a': 0.0,
        'ichimoku_span_b': 0.0,
    })
    
    logger.info(f"Training with {len(X)} REAL predictions, {len(FEATURES)} features")
    
    # Calculate sample weights (GLM feedback)
    sample_weights = calculate_sample_weights(df)
    
    # Train/test split (stratified to keep label balance)
    try:
        X_train, X_test, y_train, y_test, w_train, w_test = train_test_split(
            X, y, sample_weights, test_size=0.2, random_state=42, stratify=y
        )
    except ValueError as e:
        logger.warning(f"Stratified split failed: {e}, using random split")
        X_train, X_test, y_train, y_test, w_train, w_test = train_test_split(
            X, y, sample_weights, test_size=0.2, random_state=42
        )
    
    # Train model with sample weights
    logger.info("Training RandomForest with Active Learning data + GLM weights...")
    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=10,
        min_samples_split=10,
        min_samples_leaf=5,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train, sample_weight=w_train)
    
    # Evaluate
    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    
    logger.info(f"\n{'='*60}")
    logger.info(f"Active Learning Model Performance:")
    logger.info(f"{'='*60}")
    logger.info(f"Training samples: {len(X_train)}")
    logger.info(f"Test samples: {len(X_test)}")
    logger.info(f"Accuracy: {accuracy*100:.2f}%")
    
    # Classification report
    unique_classes = len(np.unique(y_test))
    if unique_classes > 1:
        logger.info(f"\nClassification Report:\n{classification_report(y_test, y_pred, target_names=['SELL', 'BUY'])}")
    else:
        logger.warning(f"Only 1 class in test set")
    
    # Feature importance (with GLM insights)
    feature_importance = pd.DataFrame({
        'feature': FEATURES,
        'importance': model.feature_importances_
    }).sort_values('importance', ascending=False)
    
    logger.info(f"\n{'='*60}")
    logger.info(f"Top 10 Feature Importance (Active Learning):")
    logger.info(f"{'='*60}")
    for idx, row in feature_importance.head(10).iterrows():
        logger.info(f"  {row['feature']:25s}: {row['importance']:.4f}")
    
    # Compare with GLM feedback
    if 'glm_important_feature' in df.columns:
        glm_features = df['glm_important_feature'].value_counts().head(5)
        logger.info(f"\n{'='*60}")
        logger.info(f"GLM's Most Important Features:")
        logger.info(f"{'='*60}")
        for feat, count in glm_features.items():
            logger.info(f"  {feat:25s}: mentioned {count} times")
    
    return model


def save_model(model, output_path: str):
    """Save trained model"""
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Backup old model
    if output_file.exists():
        backup_path = output_file.parent / f"{output_file.stem}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.joblib"
        import shutil
        shutil.copy(output_file, backup_path)
        logger.info(f"📦 Old model backed up to: {backup_path}")
    
    joblib.dump(model, output_file)
    logger.info(f"✅ Active Learning model saved to: {output_file}")


def main():
    print("="*60)
    print("Active Learning Retrain: Using REAL prediction outcomes!")
    print("="*60)
    
    # 1. Fetch REAL predictions with outcomes
    df = fetch_active_learning_data(min_samples=20)
    if df is None:
        logger.error("Not enough active learning data. Need at least 20 predictions with results.")
        logger.info("Wait for more predictions to accumulate or run with lower threshold.")
        return
    
    # 2. Create labels from ACTUAL outcomes
    df_labeled = create_labels_from_actuals(df)
    if df_labeled.empty:
        logger.error("No labeled data after processing. Exiting.")
        return
    
    # 3. Train model with GLM-weighted samples
    model = train_model(df_labeled)
    if model is None:
        logger.error("Training failed. Exiting.")
        return
    
    # 4. Save model
    save_model(model, "models/derivatives.joblib")
    
    print("\n"+"="*60)
    print("✅ Active Learning Training Complete!")
    print("="*60)
    print("Model now learns from:")
    print("  1. Real prediction outcomes (not just price movements)")
    print("  2. GLM feedback (weighted by correctness)")
    print("  3. High-confidence predictions (weighted higher)")


if __name__ == "__main__":
    main()
