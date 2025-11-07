import asyncio
from collections import deque
from datetime import datetime
from typing import Deque, Dict, List

import pandas as pd
import ta

from app.data_feeds.constants import HEALTH_CHANNEL, KLINE_CHANNEL
from app.features.listeners import start_stream_listener
from app.features.qlib_converter import QLibConverter
from app.utils.influx import write_measurement
from app.utils.logging import get_logger
from app.utils.redis import publish

logger = get_logger(__name__)


class FeatureWorker:
    def __init__(self, symbol: str, interval: str, min_ta_history: int = 50) -> None:
        self._symbol = symbol
        self._interval = interval
        self._min_ta_history = min_ta_history

        # 24 saatlik veri hedefi - dinamik hesaplama
        target_hours = 24
        interval_minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}
        minutes = interval_minutes.get(interval, 5)
        window_size = (target_hours * 60) // minutes

        self._window: Deque[Dict[str, float]] = deque(maxlen=window_size)
        self._qlib = QLibConverter()
        try:
            start_stream_listener(KLINE_CHANNEL, self._on_message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Listener start failed: %s", exc)

    async def run(self) -> None:
        while True:
            try:
                await asyncio.sleep(1)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Feature worker error: %s", exc)

    def _on_message(self, message: Dict) -> None:
        if message.get("symbol") != self._symbol or message.get("interval") != self._interval:
            return
        payload = message.get("payload", {})
        kline = payload.get("k") if payload else None
        if not kline:
            return
        record = {
            "open": float(kline["o"]),
            "high": float(kline["h"]),
            "low": float(kline["l"]),
            "close": float(kline["c"]),
            "volume": float(kline["v"]),
            "datetime": kline["T"],
        }
        self._window.append(record)
        features = self._compute_features(list(self._window))
        timestamp = datetime.utcnow()
        self._persist(features, timestamp)

    def _compute_features(self, data: List[Dict[str, float]]) -> Dict[str, float]:
        df = pd.DataFrame(data)
        if df.empty:
            return {}

        df = df.astype(float)
        latest = df.iloc[-1]

        features: Dict[str, float] = {
            "close": float(latest.get("close", 0.0)),
            "volume": float(latest.get("volume", 0.0)),
        }

        if features["close"] <= 0 or features["volume"] < 0:
            publish(
                HEALTH_CHANNEL,
                {
                    "source": "feature_worker",
                    "status": "data_error",
                    "detail": f"Invalid close/volume: {features}",
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )
            return {}

        if ta and len(df) >= self._min_ta_history:
            try:
                features.update(self._ta_features(df))
            except Exception as exc:  # noqa: BLE001
                logger.warning("TA feature error: %s", exc)
                features.update(self._fallback_features(df))
        else:
            features.update(self._fallback_features(df))

        return features

    def _ta_features(self, df: pd.DataFrame) -> Dict[str, float]:
        if len(df) < self._min_ta_history:
            raise ValueError("Insufficient history for TA features")

        close = df["close"]
        high = df.get("high", close)
        low = df.get("low", close)
        volume = df.get("volume", pd.Series([0] * len(df)))

        ema_20 = ta.trend.EMAIndicator(close, window=20, fillna=True).ema_indicator().iloc[-1]
        ema_50 = ta.trend.EMAIndicator(close, window=50, fillna=True).ema_indicator().iloc[-1]
        rsi_14 = ta.momentum.RSIIndicator(close, window=14, fillna=True).rsi().iloc[-1]
        macd = ta.trend.MACD(close, fillna=True)
        macd_value = macd.macd().iloc[-1]
        macd_signal = macd.macd_signal().iloc[-1]
        atr_14 = (
            ta.volatility.AverageTrueRange(high, low, close, window=14, fillna=True)
            .average_true_range()
            .iloc[-1]
        )
        vol_sma = (
            ta.volume.VolumeWeightedAveragePrice(high, low, close, volume, window=20, fillna=True)
            .volume_weighted_average_price()
            .iloc[-1]
        )

        return {
            "ema_20": float(ema_20),
            "ema_50": float(ema_50),
            "rsi_14": float(rsi_14),
            "macd": float(macd_value),
            "macd_signal": float(macd_signal),
            "atr_14": float(atr_14),
            "vwap_20": float(vol_sma),
        }

    def _fallback_features(self, df: pd.DataFrame) -> Dict[str, float]:
        close = df["close"]
        ema_20 = float(close.ewm(span=20, adjust=False, min_periods=1).mean().iloc[-1])
        ema_50 = float(close.ewm(span=50, adjust=False, min_periods=1).mean().iloc[-1])
        rsi = self._simple_rsi(close, period=min(len(close), 14))
        atr = float(
            close.diff().abs().rolling(window=min(14, len(close)), min_periods=1).mean().iloc[-1]
        )
        volume_series = df.get("volume", close)
        cum_volume = volume_series.cumsum().replace(0, pd.NA)
        vwap = float((close * volume_series).cumsum().div(cum_volume).fillna(close).iloc[-1])
        return {
            "ema_20": float(ema_20),
            "ema_50": float(ema_50),
            "rsi_14": float(rsi),
            "atr_14": atr,
            "vwap_20": vwap,
        }

    def _simple_rsi(self, series: pd.Series, period: int = 14) -> float:
        delta = series.diff().dropna()
        if delta.empty:
            return 50.0
        window = max(period, 1)
        gain = delta.clip(lower=0).rolling(window=window, min_periods=1).mean()
        loss = -delta.clip(upper=0).rolling(window=window, min_periods=1).mean()
        loss = loss.replace(0, pd.NA)
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.fillna(50.0).iloc[-1])

    def _persist(self, features: Dict[str, float], timestamp: datetime) -> None:
        if not features:
            return
        write_measurement(
            f"features_{self._interval}",
            tags={"symbol": self._symbol, "interval": self._interval},
            fields=features,
            timestamp=timestamp,
        )
        self._qlib.append_records(
            self._symbol,
            [{"datetime": timestamp.isoformat(), **features}],
        )
