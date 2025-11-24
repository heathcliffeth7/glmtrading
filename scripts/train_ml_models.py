#!/usr/bin/env python3
"""
Train ML models for all agents using historical data from InfluxDB
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import classification_report, confusion_matrix
import joblib
from datetime import datetime, timedelta

from app.utils.influx import query_range_between
from app.utils.logging import configure_logging, get_logger
from app.config.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


def fetch_training_data(symbol: str = "BTCUSDT", days: int = 30) -> pd.DataFrame:
    """
    Fetch historical data from InfluxDB for training
    
    Args:
        symbol: Trading symbol
        days: Number of days of historical data
    
    Returns:
        DataFrame with features and labels
    """
    logger.info("Fetching %d days of training data for %s", days, symbol)
    
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    
    # Fetch enriched data
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
    
    logger.info("Fetched %d rows", len(df))
    
    return df


def create_labels(df: pd.DataFrame, forward_periods: int = 3) -> pd.DataFrame:
    """
    Create labels for supervised learning
    
    Label strategy:
    - 1 (BUY): Price increases > 0.3% in next N periods
    - 0 (SELL): Price decreases > 0.3% in next N periods  
    - -1 (HOLD): Price changes < 0.3%
    
    Args:
        df: DataFrame with 'close' column
        forward_periods: How many periods ahead to look
    
    Returns:
        DataFrame with 'label' column added
    """
    df = df.copy()
    
    # Calculate future return
    df['future_close'] = df['close'].shift(-forward_periods)
    df['future_return'] = (df['future_close'] - df['close']) / df['close'] * 100
    
    # Create labels (lowered threshold from ±0.5% to ±0.3% for more training examples)
    df['label'] = 0  # Default HOLD
    df.loc[df['future_return'] > 0.3, 'label'] = 1  # BUY
    df.loc[df['future_return'] < -0.3, 'label'] = -1  # SELL (but treat as 0 for binary)
    
    # For binary classification: BUY (1) vs NOT-BUY (0)
    df['label_binary'] = (df['label'] == 1).astype(int)
    
    # Drop rows without future data
    df = df.dropna(subset=['future_return'])
    
    logger.info("Label distribution: BUY=%d HOLD=%d SELL=%d",
                (df['label'] == 1).sum(),
                (df['label'] == 0).sum(),
                (df['label'] == -1).sum())
    
    return df


def train_derivatives_model(df: pd.DataFrame) -> None:
    """
    Train derivatives model with ALL 25 features (futures + Binance + TwelveData)
    
    Features: 3 futures + 17 Binance + 5 TwelveData = 25 total
    """
    logger.info("Training derivatives model...")
    
    # Select ALL 25 features to match derivatives.py DEFAULT_MODEL_COLUMNS
    feature_cols = [
        # Futures metrics (3)
        'long_short_ratio',
        'open_interest',
        'funding_rate',
        
        # Binance spot indicators (17)
        'close',
        'ema_20',
        'ema_50',
        'rsi_14',
        'macd',
        'macd_signal',
        'atr_14',
        'stoch_k',
        'stoch_d',
        'bb_upper',
        'bb_middle',
        'bb_lower',
        'willr',
        'cci',
        'mfi',
        'obv',
        'vwap_20',
        
        # TwelveData special indicators (5)
        'sar',
        'ichimoku_base',
        'ichimoku_conversion',
        'ichimoku_span_a',
        'ichimoku_span_b',
    ]
    
    # Prepare data
    df_clean = df.dropna(subset=feature_cols + ['label_binary'])
    
    if len(df_clean) < 100:
        logger.error("Insufficient data for training: %d rows", len(df_clean))
        return
    
    X = df_clean[feature_cols].values
    y = df_clean['label_binary'].values
    
    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    
    # Train
    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=10,
        min_samples_split=20,
        random_state=42,
        class_weight='balanced',
    )
    
    model.fit(X_train, y_train)
    
    # Evaluate
    train_score = model.score(X_train, y_train)
    test_score = model.score(X_test, y_test)
    cv_scores = cross_val_score(model, X_train, y_train, cv=5)
    
    logger.info("Derivatives Model Performance:")
    logger.info("  Train accuracy: %.2f%%", train_score * 100)
    logger.info("  Test accuracy: %.2f%%", test_score * 100)
    logger.info("  CV accuracy: %.2f%% (+/- %.2f%%)", 
                cv_scores.mean() * 100, cv_scores.std() * 100)
    
    # Predictions
    y_pred = model.predict(X_test)
    logger.info("\nClassification Report:\n%s", classification_report(y_test, y_pred))
    
    # Feature importance
    importances = model.feature_importances_
    for feat, imp in zip(feature_cols, importances):
        logger.info("  %s: %.3f", feat, imp)
    
    # Save model
    output_path = Path("models/derivatives.joblib")
    output_path.parent.mkdir(exist_ok=True)
    joblib.dump(model, output_path)
    logger.info("Model saved to %s", output_path)


def train_multi_signal_model(df: pd.DataFrame) -> None:
    """
    Train comprehensive multi-signal model
    
    Uses all available features from enriched data
    """
    logger.info("Training multi-signal model...")
    
    # All available features
    feature_cols = [
        'close',
        'volume',
        'ema_20',
        'ema_50',
        'rsi_14',
        'rsi_twelvedata',
        'macd',
        'macd_signal',
        'macd_twelvedata',
        'macd_signal_twelvedata',
        'macd_hist_twelvedata',
        'bb_upper',
        'bb_middle',
        'bb_lower',
        'stoch_k',
        'stoch_d',
        'atr_14',
        'atr_twelvedata',
        'long_short_ratio',
        'open_interest',
        'funding_rate',
        'vwap_20',
    ]
    
    # Filter available columns
    available_features = [f for f in feature_cols if f in df.columns]
    
    logger.info("Using %d features: %s", len(available_features), available_features)
    
    # Prepare data
    df_clean = df.dropna(subset=available_features + ['label_binary'])
    
    if len(df_clean) < 100:
        logger.error("Insufficient data: %d rows", len(df_clean))
        return
    
    X = df_clean[available_features].values
    y = df_clean['label_binary'].values
    
    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    
    # Train Gradient Boosting (better for this use case)
    model = GradientBoostingClassifier(
        n_estimators=200,
        learning_rate=0.05,
        max_depth=5,
        min_samples_split=20,
        subsample=0.8,
        random_state=42,
    )
    
    model.fit(X_train, y_train)
    
    # Evaluate
    train_score = model.score(X_train, y_train)
    test_score = model.score(X_test, y_test)
    cv_scores = cross_val_score(model, X_train, y_train, cv=5)
    
    logger.info("Multi-Signal Model Performance:")
    logger.info("  Train accuracy: %.2f%%", train_score * 100)
    logger.info("  Test accuracy: %.2f%%", test_score * 100)
    logger.info("  CV accuracy: %.2f%% (+/- %.2f%%)", 
                cv_scores.mean() * 100, cv_scores.std() * 100)
    
    # Predictions
    y_pred = model.predict(X_test)
    logger.info("\nClassification Report:\n%s", classification_report(y_test, y_pred))
    
    # Feature importance (top 10)
    importances = model.feature_importances_
    indices = np.argsort(importances)[::-1][:10]
    
    logger.info("\nTop 10 Important Features:")
    for i in indices:
        logger.info("  %s: %.3f", available_features[i], importances[i])
    
    # Save
    output_path = Path("models/multi_signal.joblib")
    joblib.dump(model, output_path)
    logger.info("Model saved to %s", output_path)


def main():
    """Main training pipeline"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Train ML models')
    parser.add_argument('--symbol', default='BTCUSDT', help='Trading symbol')
    parser.add_argument('--days', type=int, default=30, help='Days of historical data')
    parser.add_argument('--model', default='all', choices=['all', 'derivatives', 'multi'], 
                        help='Which model to train')
    
    args = parser.parse_args()
    
    # Fetch data
    df = fetch_training_data(args.symbol, args.days)
    
    if df.empty:
        logger.error("No training data available!")
        return
    
    # Create labels
    df = create_labels(df, forward_periods=3)
    
    # Train models
    if args.model in ['all', 'derivatives']:
        train_derivatives_model(df)
    
    if args.model in ['all', 'multi']:
        train_multi_signal_model(df)
    
    logger.info("Training complete!")


if __name__ == '__main__':
    main()
