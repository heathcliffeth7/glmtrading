#!/usr/bin/env python3
"""
Train ML model with 20 features (no TwelveData) using historical enriched_30min data
"""
import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score

from app.utils.logging import configure_logging, get_logger


configure_logging("INFO")
logger = get_logger(__name__)


def prepare_labels(df: pd.DataFrame, horizon_periods: int = 2, threshold_pct: float = 0.5) -> pd.DataFrame:
    """
    Create labels for classification based on future price movement
    
    Args:
        df: DataFrame with 'close' column
        horizon_periods: How many periods ahead to look (e.g., 2 = 1 hour for 30min data)
        threshold_pct: Minimum % move to consider as significant (0.5% default)
    
    Returns:
        DataFrame with 'label' column (1 = price will go up, 0 = price will go down)
    """
    logger.info(f"Creating labels: horizon={horizon_periods} periods, threshold={threshold_pct}%")
    
    # Calculate future price
    df['future_close'] = df['close'].shift(-horizon_periods)
    
    # Calculate % change
    df['price_change_pct'] = ((df['future_close'] - df['close']) / df['close']) * 100
    
    # Create binary label
    # 1 = price will increase by at least threshold %
    # 0 = price will decrease by at least threshold %
    df['label'] = (df['price_change_pct'] > threshold_pct).astype(int)
    
    # Remove rows where we don't have future data
    df_labeled = df[df['future_close'].notna()].copy()
    
    logger.info(f"Label distribution:")
    logger.info(f"  BUY (1): {(df_labeled['label'] == 1).sum()} ({(df_labeled['label'] == 1).mean() * 100:.1f}%)")
    logger.info(f"  SELL (0): {(df_labeled['label'] == 0).sum()} ({(df_labeled['label'] == 0).mean() * 100:.1f}%)")
    
    return df_labeled


def train_model(
    input_csv: Path,
    output_model: Path,
    horizon_periods: int = 2,
    threshold_pct: float = 0.5,
) -> None:
    """
    Train RandomForest classifier on 20 features
    
    Args:
        input_csv: Path to CSV with historical data
        output_model: Path to save trained model
        horizon_periods: Prediction horizon
        threshold_pct: Classification threshold
    """
    logger.info("=" * 60)
    logger.info("TRAINING ML MODEL (20 FEATURES, NO TWELVEDATA)")
    logger.info("=" * 60)
    
    # Load data
    logger.info(f"Loading data from {input_csv}")
    df = pd.read_csv(input_csv)
    logger.info(f"Loaded {len(df)} records")
    
    # Show sample
    logger.info("\nFirst 3 rows:")
    logger.info(df.head(3).to_string())
    
    # Define features (20 total, no TwelveData)
    FEATURES = [
        # Futures (3)
        "long_short_ratio",
        "open_interest",
        "funding_rate",
        
        # Binance Spot (17)
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
    ]
    
    # Check all features exist
    missing_features = [f for f in FEATURES if f not in df.columns]
    if missing_features:
        logger.error(f"Missing features: {missing_features}")
        return
    
    logger.info(f"\nUsing {len(FEATURES)} features")
    
    # Prepare labels
    df_labeled = prepare_labels(df, horizon_periods, threshold_pct)
    logger.info(f"\nDataset after labeling: {len(df_labeled)} records")
    
    if len(df_labeled) < 20:
        logger.error("Not enough data for training (need at least 20 samples)")
        logger.error("Suggestion: Collect more data and try again later")
        return
    
    # Prepare X and y
    X = df_labeled[FEATURES]
    y = df_labeled['label']
    
    # Check for NaN/inf
    if X.isnull().any().any():
        logger.warning("Found NaN values in features, filling with 0")
        X = X.fillna(0)
    
    if np.isinf(X.values).any():
        logger.warning("Found inf values in features, replacing with max/min")
        X = X.replace([np.inf, -np.inf], [X.max().max(), X.min().min()])
    
    # Split train/test
    test_size = 0.2 if len(df_labeled) > 50 else 0.1
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=42, stratify=y if len(df_labeled) > 20 else None
    )
    
    logger.info(f"\nTrain set: {len(X_train)} samples")
    logger.info(f"Test set: {len(X_test)} samples")
    
    # Train model
    logger.info("\nTraining RandomForestClassifier...")
    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=10,
        min_samples_split=5,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1,
    )
    
    model.fit(X_train, y_train)
    logger.info("✅ Training completed")
    
    # Evaluate
    logger.info("\n" + "=" * 60)
    logger.info("MODEL EVALUATION")
    logger.info("=" * 60)
    
    # Train accuracy
    train_score = model.score(X_train, y_train)
    logger.info(f"Train accuracy: {train_score:.4f}")
    
    # Test accuracy
    test_score = model.score(X_test, y_test)
    logger.info(f"Test accuracy: {test_score:.4f}")
    
    # Predictions
    y_pred = model.predict(X_test)
    y_pred_proba = model.predict_proba(X_test)[:, 1]
    
    # Classification report
    logger.info("\nClassification Report:")
    try:
        report = classification_report(y_test, y_pred, target_names=['SELL (0)', 'BUY (1)'], zero_division=0)
        logger.info("\n" + report)
    except ValueError as e:
        logger.warning(f"Could not generate full classification report: {e}")
        logger.info(f"Test set contains only classes: {y_test.unique()}")
        # Generate report without target_names
        report = classification_report(y_test, y_pred, zero_division=0)
        logger.info("\n" + report)
    
    # Confusion matrix
    logger.info("Confusion Matrix:")
    cm = confusion_matrix(y_test, y_pred, labels=[0, 1])
    logger.info(f"\n{cm}")
    try:
        logger.info(f"  [[TN={cm[0,0]} FP={cm[0,1]}]")
        logger.info(f"   [FN={cm[1,0]} TP={cm[1,1]}]]")
    except IndexError:
        logger.warning("Confusion matrix has only one class in test set")
    
    # ROC AUC
    try:
        roc_auc = roc_auc_score(y_test, y_pred_proba)
        logger.info(f"\nROC AUC Score: {roc_auc:.4f}")
    except Exception as exc:
        logger.warning(f"Could not calculate ROC AUC: {exc}")
    
    # Feature importances
    logger.info("\n" + "=" * 60)
    logger.info("FEATURE IMPORTANCES (Top 10)")
    logger.info("=" * 60)
    
    importances = pd.DataFrame({
        'feature': FEATURES,
        'importance': model.feature_importances_
    }).sort_values('importance', ascending=False)
    
    logger.info("\n" + importances.head(10).to_string(index=False))
    
    # Save model
    output_model.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, output_model)
    logger.info(f"\n✅ Model saved to {output_model}")
    
    # Model info
    logger.info("\n" + "=" * 60)
    logger.info("MODEL INFO")
    logger.info("=" * 60)
    logger.info(f"Model type: {type(model).__name__}")
    logger.info(f"Features: {len(FEATURES)}")
    logger.info(f"Feature names: {model.feature_names_in_}")
    logger.info(f"Classes: {model.classes_}")
    logger.info(f"Test accuracy: {test_score:.4f}")
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("TRAINING SUMMARY")
    logger.info("=" * 60)
    logger.info(f"✅ Model trained successfully with {len(FEATURES)} features")
    logger.info(f"✅ Test accuracy: {test_score:.4f}")
    logger.info(f"✅ Model saved to: {output_model}")
    
    if test_score < 0.55:
        logger.warning("\n⚠️ Model accuracy is low (<55%)")
        logger.warning("Suggestions:")
        logger.warning("  1. Collect more historical data (currently only 48 records)")
        logger.warning("  2. Wait a few days for more data to accumulate")
        logger.warning("  3. Adjust hyperparameters (horizon, threshold)")
        logger.warning("  4. Use fallback logic for now")
    elif test_score < 0.65:
        logger.warning("\n⚠️ Model accuracy is moderate (55-65%)")
        logger.warning("Consider collecting more data for better performance")
    else:
        logger.info("\n🎉 Model accuracy is good (>65%)")
        logger.info("Ready to deploy!")


def main():
    parser = argparse.ArgumentParser(description="Train ML model with 20 features")
    parser.add_argument(
        "--input",
        default="data/derivatives_20features.csv",
        help="Input CSV file with historical data"
    )
    parser.add_argument(
        "--output",
        default="models/derivatives.joblib",
        help="Output model file"
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=2,
        help="Prediction horizon in periods (default: 2 = 1 hour for 30min data)"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Minimum price change threshold in % (default: 0.5%)"
    )
    
    args = parser.parse_args()
    
    train_model(
        input_csv=Path(args.input),
        output_model=Path(args.output),
        horizon_periods=args.horizon,
        threshold_pct=args.threshold,
    )


if __name__ == "__main__":
    main()
