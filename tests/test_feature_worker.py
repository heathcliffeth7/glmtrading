import pandas as pd
import pytest

from app.features.feature_worker import FeatureWorker


def test_feature_worker_initializes():
    worker = FeatureWorker(symbol="BTCUSDT", interval="1m")
    assert worker._symbol == "BTCUSDT"


def test_feature_computation(monkeypatch):
    worker = FeatureWorker(symbol="BTCUSDT", interval="1m")

    data = [
        {"open": 100, "high": 105, "low": 95, "close": 102, "volume": 10, "datetime": "1"},
        {"open": 102, "high": 106, "low": 99, "close": 104, "volume": 12, "datetime": "2"},
    ]

    class FakeEMA:
        def __init__(self, *_, **__):
            self._series = pd.Series([101, 103])

        def ema_indicator(self):
            return self._series

    class FakeRSI:
        def __init__(self, *_, **__):
            self._series = pd.Series([50, 55])

        def rsi(self):
            return self._series

    class FakeATR:
        def __init__(self, *_, **__):
            self._series = pd.Series([1.0, 1.2])

        def average_true_range(self):
            return self._series

    class FakeVWAP:
        def __init__(self, *_, **__):
            self._series = pd.Series([101, 103])

        def volume_weighted_average_price(self):
            return self._series

    monkeypatch.setattr("app.features.feature_worker.ta.trend.EMAIndicator", FakeEMA)
    monkeypatch.setattr("app.features.feature_worker.ta.momentum.RSIIndicator", FakeRSI)
    monkeypatch.setattr("app.features.feature_worker.ta.volatility.AverageTrueRange", FakeATR)
    monkeypatch.setattr("app.features.feature_worker.ta.volume.VolumeWeightedAveragePrice", FakeVWAP)

    result = worker._compute_features(data)
    assert "ema_20" in result
