#!/usr/bin/env python3
"""
Retrain Derivatives Model with 24 features (16→24: added 8 TwelveData indicators)
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
from datetime import datetime, timedelta

from app.utils.influx import query_range_between
from app.utils.logging import get_logger

logger = get_logger(__name__)

# 25 features - Binance indicators + TwelveData special indicators
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
    # These cannot be calculated from OHLCV alone - require complex algorithms
    "sar",                  # Parabolic SAR
    "ichimoku_base",        # Ichimoku base line (kijun-sen)
    "ichimoku_conversion",  # Ichimoku conversion line (tenkan-sen)
    "ichimoku_span_a",      # Ichimoku leading span A (senkou span A)
    "ichimoku_span_b",      # Ichimoku leading span B (senkou span B)
]


def fetch_training_data(symbol: str = "BTCUSDT", days: int = 30):
    """Fetch historical data from InfluxDB enriched_5min"""
    logger.info(f"Fetching {days} days of training data for {symbol}...")
    
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    
    data = query_range_between("enriched_5min", symbol, "5min", start, end)
    
    if not data:
        logger.error("No data found!")
        return pd.DataFrame()
    
    # Convert to DataFrame
    df = pd.DataFrame(data)
    df = df.pivot(index='timestamp', columns='field', values='value')
    df = df.reset_index()
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp')
    
    logger.info(f"Fetched {len(df)} rows")
    return df


def create_labels(df: pd.DataFrame, forward_periods: int = 3):
    """
    Create labels: 
    - BUY (1): price increases > 0.3% in next N periods
    - SELL (0): otherwise
    """
    df = df.copy()
    
    df['future_close'] = df['close'].shift(-forward_periods)
    df['future_return'] = (df['future_close'] - df['close']) / df['close'] * 100
    
    # Binary classification: BUY vs SELL (lower threshold for more variation)
    df['label'] = (df['future_return'] > 0.1).astype(int)
    
    df = df.dropna(subset=['future_return'])
    
    logger.info(f"Label distribution: BUY={df['label'].sum()} SELL={(~df['label'].astype(bool)).sum()}")
    
    return df


def train_model(df: pd.DataFrame):
    """Train RandomForest with 24 features"""
    
    # Check if all features exist
    missing = set(FEATURES) - set(df.columns)
    if missing:
        logger.error(f"Missing features: {missing}")
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
    
    logger.info(f"Training with {len(X)} samples, {len(FEATURES)} features")
    
    # Train/test split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    
    # Train model
    logger.info("Training RandomForest model...")
    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=10,
        min_samples_split=20,
        min_samples_leaf=10,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)
    
    # Evaluate
    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    
    logger.info(f"\n{'='*60}")
    logger.info(f"Model Performance ({len(FEATURES)} features):")
    logger.info(f"{'='*60}")
    logger.info(f"Accuracy: {accuracy*100:.2f}%")
    
    # Only show classification report if both classes exist
    unique_classes = len(np.unique(y_test))
    if unique_classes > 1:
        logger.info(f"\nClassification Report:\n{classification_report(y_test, y_pred, target_names=['SELL', 'BUY'])}")
    else:
        logger.warning(f"Only 1 class in test set - skipping classification report")
        logger.info(f"Train set BUY ratio: {y_train.mean()*100:.2f}%")
        logger.info(f"Test set BUY ratio: {y_test.mean()*100:.2f}%")
    
    # Feature importance
    feature_importance = pd.DataFrame({
        'feature': FEATURES,
        'importance': model.feature_importances_
    }).sort_values('importance', ascending=False)
    
    logger.info(f"\n{'='*60}")
    logger.info(f"Top 10 Feature Importance:")
    logger.info(f"{'='*60}")
    for idx, row in feature_importance.head(10).iterrows():
        logger.info(f"  {row['feature']:25s}: {row['importance']:.4f}")
    
    return model


def save_model(model, output_path: str):
    """Save trained model"""
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    joblib.dump(model, output_file)
    logger.info(f"✅ Model saved to: {output_file}")
    
    # Archive old model
    if output_file.exists():
        archive_path = output_file.parent / f"derivatives_model_16feat_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pkl"
        if (output_file.parent / "derivatives_model.pkl").exists() and output_file.name == "derivatives_model_24feat.pkl":
            import shutil
            shutil.copy(output_file.parent / "derivatives_model.pkl", archive_path)
            logger.info(f"📦 Old model archived to: {archive_path}")


def main():
    print("="*60)
    print(f"Derivatives Model Training: {len(FEATURES)} Real Features (Binance)")
    print("="*60)
    
    # 1. Fetch data
    df = fetch_training_data(symbol="BTCUSDT", days=30)
    if df.empty:
        logger.error("No data fetched. Exiting.")
        return
    
    # 2. Create labels
    df_labeled = create_labels(df, forward_periods=3)
    if df_labeled.empty:
        logger.error("No labeled data. Exiting.")
        return
    
    # 3. Train model
    model = train_model(df_labeled)
    if model is None:
        logger.error("Training failed. Exiting.")
        return
    
    # 4. Save model
    save_model(model, "models/derivatives.joblib")
    
    print("\n"+"="*60)
    print("✅ Training Complete!")
    print("="*60)


if __name__ == "__main__":
    main()
