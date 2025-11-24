"""
Teknik Analiz Göstergeleri Hesaplama Modülü
RSI, MACD, Bollinger Bands, Stochastic, ATR gibi göstergeleri hesaplar
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, Any, Optional, Tuple

try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    TALIB_AVAILABLE = False

logger = logging.getLogger(__name__)

class TechnicalAnalyzer:
    """
    Teknik analiz göstergelerini hesaplayan sınıf
    """

    def __init__(self):
        if not TALIB_AVAILABLE:
            logger.warning("TA-Lib kurulu değil, manuel hesaplama kullanılacak")

    def calculate_rsi(self, prices: np.ndarray, period: int = 14) -> float:
        """
        RSI (Relative Strength Index) hesapla

        Args:
            prices: Kapanış fiyatları dizisi
            period: RSI periyodu (varsayılan 14)

        Returns:
            RSI değeri (0-100 arası)
        """
        if len(prices) < period + 1:
            return 50.0  # Yetersiz veri durumunda nötr değer

        try:
            if TALIB_AVAILABLE:
                rsi_values = talib.RSI(prices, timeperiod=period)
                return float(rsi_values[-1]) if not np.isnan(rsi_values[-1]) else 50.0
            else:
                return self._calculate_rsi_manual(prices, period)
        except Exception as e:
            logger.error(f"RSI hesaplama hatası: {e}")
            return 50.0

    def calculate_macd(self, prices: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[float, float, float]:
        """
        MACD (Moving Average Convergence Divergence) hesapla

        Args:
            prices: Kapanış fiyatları dizisi
            fast: Hızlı EMA periyodu (varsayılan 12)
            slow: Yavaş EMA periyodu (varsayılan 26)
            signal: Sinyal hattı periyodu (varsayılan 9)

        Returns:
            (MACD, Signal, Histogram) tuple
        """
        if len(prices) < slow + signal:
            return 0.0, 0.0, 0.0

        try:
            if TALIB_AVAILABLE:
                macd, signal, hist = talib.MACD(prices, fastperiod=fast, slowperiod=slow, signalperiod=signal)
                macd_val = float(macd[-1]) if not np.isnan(macd[-1]) else 0.0
                signal_val = float(signal[-1]) if not np.isnan(signal[-1]) else 0.0
                hist_val = float(hist[-1]) if not np.isnan(hist[-1]) else 0.0
                return macd_val, signal_val, hist_val
            else:
                return self._calculate_macd_manual(prices, fast, slow, signal)
        except Exception as e:
            logger.error(f"MACD hesaplama hatası: {e}")
            return 0.0, 0.0, 0.0

    def calculate_bollinger_bands(self, prices: np.ndarray, period: int = 20, std_dev: float = 2.0) -> Tuple[float, float, float]:
        """
        Bollinger Bantları hesapla

        Args:
            prices: Kapanış fiyatları dizisi
            period: Periyot (varsayılan 20)
            std_dev: Standart sapma çarpanı (varsayılan 2.0)

        Returns:
            (Upper Band, Middle Band, Lower Band) tuple
        """
        if len(prices) < period:
            current_price = float(prices[-1]) if len(prices) > 0 else 0.0
            return current_price, current_price, current_price

        try:
            if TALIB_AVAILABLE:
                upper, middle, lower = talib.BBANDS(prices, timeperiod=period, nbdevup=std_dev, nbdevdn=std_dev, matype=0)
                upper_val = float(upper[-1]) if not np.isnan(upper[-1]) else float(prices[-1])
                middle_val = float(middle[-1]) if not np.isnan(middle[-1]) else float(prices[-1])
                lower_val = float(lower[-1]) if not np.isnan(lower[-1]) else float(prices[-1])
                return upper_val, middle_val, lower_val
            else:
                return self._calculate_bollinger_manual(prices, period, std_dev)
        except Exception as e:
            logger.error(f"Bollinger Bands hesaplama hatası: {e}")
            current_price = float(prices[-1]) if len(prices) > 0 else 0.0
            return current_price, current_price, current_price

    def calculate_stochastic(self, high: np.ndarray, low: np.ndarray, close: np.ndarray,
                           k_period: int = 14, d_period: int = 3) -> Tuple[float, float]:
        """
        Stochastic Oscillator hesapla

        Args:
            high: En yüksek fiyatlar dizisi
            low: En düşük fiyatlar dizisi
            close: Kapanış fiyatları dizisi
            k_period: %K periyodu (varsayılan 14)
            d_period: %D periyodu (varsayılan 3)

        Returns:
            (%K, %D) tuple
        """
        if len(close) < k_period + d_period:
            return 50.0, 50.0

        try:
            if TALIB_AVAILABLE:
                slowk, slowd = talib.STOCH(high, low, close, fastk_period=k_period,
                                         slowk_period=d_period, slowk_matype=0,
                                         slowd_period=d_period, slowd_matype=0)
                k_val = float(slowk[-1]) if not np.isnan(slowk[-1]) else 50.0
                d_val = float(slowd[-1]) if not np.isnan(slowd[-1]) else 50.0
                return k_val, d_val
            else:
                return self._calculate_stochastic_manual(high, low, close, k_period, d_period)
        except Exception as e:
            logger.error(f"Stochastic hesaplama hatası: {e}")
            return 50.0, 50.0

    def calculate_atr(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
        """
        ATR (Average True Range) hesapla

        Args:
            high: En yüksek fiyatlar dizisi
            low: En düşük fiyatlar dizisi
            close: Kapanış fiyatları dizisi
            period: ATR periyodu (varsayılan 14)

        Returns:
            ATR değeri
        """
        if len(close) < period + 1:
            return 0.0

        try:
            if TALIB_AVAILABLE:
                atr_values = talib.ATR(high, low, close, timeperiod=period)
                return float(atr_values[-1]) if not np.isnan(atr_values[-1]) else 0.0
            else:
                return self._calculate_atr_manual(high, low, close, period)
        except Exception as e:
            logger.error(f"ATR hesaplama hatası: {e}")
            return 0.0

    def calculate_ema(self, prices: np.ndarray, period: int = 20) -> float:
        """
        EMA (Exponential Moving Average) hesapla

        Args:
            prices: Kapanış fiyatları dizisi
            period: EMA periyodu

        Returns:
            EMA değeri
        """
        if len(prices) < period:
            return float(prices[-1]) if len(prices) > 0 else 0.0

        try:
            if TALIB_AVAILABLE:
                ema_values = talib.EMA(prices, timeperiod=period)
                return float(ema_values[-1]) if not np.isnan(ema_values[-1]) else float(prices[-1])
            else:
                return self._calculate_ema_manual(prices, period)
        except Exception as e:
            logger.error(f"EMA hesaplama hatası: {e}")
            return float(prices[-1]) if len(prices) > 0 else 0.0

    def calculate_all_indicators(self, ohlc_data: Dict[str, np.ndarray]) -> Dict[str, float]:
        """
        Tüm teknik göstergeleri hesapla

        Args:
            ohlc_data: OHLC verilerini içeren dict

        Returns:
            Göstergeleri içeren dict
        """
        indicators = {}

        try:
            close = ohlc_data.get('close', np.array([]))
            high = ohlc_data.get('high', np.array([]))
            low = ohlc_data.get('low', np.array([]))

            if len(close) == 0:
                logger.warning("OHLC verisi boş")
                return indicators

            # Temel göstergeler
            indicators['rsi'] = self.calculate_rsi(close)
            indicators['ema_20'] = self.calculate_ema(close, 20)
            indicators['ema_50'] = self.calculate_ema(close, 50)

            # MACD
            macd, signal, hist = self.calculate_macd(close)
            indicators['macd'] = macd
            indicators['macd_signal'] = signal
            indicators['macd_histogram'] = hist

            # Bollinger Bands
            bb_upper, bb_middle, bb_lower = self.calculate_bollinger_bands(close)
            indicators['bb_upper'] = bb_upper
            indicators['bb_middle'] = bb_middle
            indicators['bb_lower'] = bb_lower

            # Stochastic
            if len(high) > 0 and len(low) > 0:
                stoch_k, stoch_d = self.calculate_stochastic(high, low, close)
                indicators['stoch_k'] = stoch_k
                indicators['stoch_d'] = stoch_d

            # ATR
            if len(high) > 0 and len(low) > 0:
                indicators['atr'] = self.calculate_atr(high, low, close)

            logger.debug(f"Tüm göstergeler hesaplandı: {len(indicators)} adet")

        except Exception as e:
            logger.error(f"Gösterge hesaplama hatası: {e}")

        return indicators

    def _calculate_ema_manual(self, prices: np.ndarray, period: int) -> float:
        """Manuel EMA hesaplama (Pandas ile optimize edilmiş)"""
        try:
            series = pd.Series(prices)
            ema = series.ewm(span=period, adjust=False).mean()
            return float(ema.iloc[-1])
        except Exception:
            # Fallback to simple calculation if pandas fails
            return float(prices[-1])

    def _calculate_macd_manual(self, prices: np.ndarray, fast: int, slow: int, signal: int) -> Tuple[float, float, float]:
        """Manuel MACD hesaplama (Pandas ile tam implementasyon)"""
        try:
            series = pd.Series(prices)
            # Fast and Slow EMA
            fast_ema = series.ewm(span=fast, adjust=False).mean()
            slow_ema = series.ewm(span=slow, adjust=False).mean()
            
            # MACD Line
            macd_line = fast_ema - slow_ema
            
            # Signal Line (EMA of MACD Line)
            signal_line = macd_line.ewm(span=signal, adjust=False).mean()
            
            # MACD Histogram
            macd_hist = macd_line - signal_line
            
            return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(macd_hist.iloc[-1])
        except Exception:
            return 0.0, 0.0, 0.0

    def _calculate_bollinger_manual(self, prices: np.ndarray, period: int, std_dev: float) -> Tuple[float, float, float]:
        """Manuel Bollinger Bands hesaplama (Pandas ile)"""
        try:
            series = pd.Series(prices)
            middle = series.rolling(window=period).mean()
            std = series.rolling(window=period).std()
            
            upper = middle + (std * std_dev)
            lower = middle - (std * std_dev)
            
            return float(upper.iloc[-1]), float(middle.iloc[-1]), float(lower.iloc[-1])
        except Exception:
            current = float(prices[-1])
            return current, current, current

    def _calculate_stochastic_manual(self, high: np.ndarray, low: np.ndarray, close: np.ndarray,
                                   k_period: int, d_period: int) -> Tuple[float, float]:
        """Manuel Stochastic hesaplama (Pandas ile tam implementasyon)"""
        try:
            high_series = pd.Series(high)
            low_series = pd.Series(low)
            close_series = pd.Series(close)
            
            # Calculate %K
            # Lowest Low over k_period
            lowest_low = low_series.rolling(window=k_period).min()
            # Highest High over k_period
            highest_high = high_series.rolling(window=k_period).max()
            
            # %K Formula: (Current Close - Lowest Low) / (Highest High - Lowest Low) * 100
            # Avoid division by zero
            denominator = highest_high - lowest_low
            numerator = close_series - lowest_low
            
            k_percent = (numerator / denominator) * 100
            
            # %D Formula: SMA of %K over d_period
            d_percent = k_percent.rolling(window=d_period).mean()
            
            # Handle potential NaNs at the beginning
            k_val = float(k_percent.iloc[-1]) if not pd.isna(k_percent.iloc[-1]) else 50.0
            d_val = float(d_percent.iloc[-1]) if not pd.isna(d_percent.iloc[-1]) else 50.0
            
            return k_val, d_val
        except Exception:
            return 50.0, 50.0

    def _calculate_atr_manual(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> float:
        """Manuel ATR hesaplama (Pandas ile Wilder's Smoothing)"""
        try:
            high_s = pd.Series(high)
            low_s = pd.Series(low)
            close_s = pd.Series(close)
            
            # True Range Calculation
            # TR = max(high-low, abs(high-prev_close), abs(low-prev_close))
            
            # Method 1: High - Low
            tr1 = high_s - low_s
            
            # Method 2: |High - Previous Close|
            tr2 = (high_s - close_s.shift(1)).abs()
            
            # Method 3: |Low - Previous Close|
            tr3 = (low_s - close_s.shift(1)).abs()
            
            # Combine to find max per period
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            
            # Wilder's Smoothing for ATR
            # ATR = (Previous ATR * (n-1) + TR) / n
            # This is equivalent to specific EMA
            atr = tr.ewm(alpha=1/period, adjust=False).mean()
            
            return float(atr.iloc[-1])
        except Exception:
            return 0.0

    def _calculate_rsi_manual(self, prices: np.ndarray, period: int = 14) -> float:
        """Manuel RSI hesaplama (Pandas ile Wilder's Smoothing)"""
        try:
            series = pd.Series(prices)
            delta = series.diff()
            
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            
            # Wilder's Smoothing
            avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
            
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            # Handle potential division by zero (if avg_loss is 0, RSI is 100)
            if float(avg_loss.iloc[-1]) == 0:
                return 100.0
                
            return float(rsi.iloc[-1])
        except Exception:
            return 50.0