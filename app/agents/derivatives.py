from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List

import httpx
import pandas as pd
import ta

from app.agents.base import Agent, AgentSignal
from app.config.settings import get_settings
from app.utils.influx import query_latest_snapshot, query_historical_snapshots, detect_htf_support_resistance
from app.utils.latency import get_latency_tracker
from app.utils.logging import get_logger


logger = get_logger(__name__)
settings = get_settings()


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _mean(values: Iterable[float]) -> float:
    items = tuple(values)
    if not items:
        return 0.0
    return sum(items) / len(items)


@dataclass
class DerivativesFeatures:
    # Futures metrics (3)
    long_short_ratio: float = 0.0
    open_interest: float = 0.0
    funding_rate: float = 0.0
    
    # === 30min (Main timeframe) - 17 Binance indicators ===
    close: float = 0.0
    ema_20: float = 0.0
    ema_50: float = 0.0
    rsi_14: float = 50.0
    macd: float = 0.0
    macd_signal: float = 0.0
    atr_14: float = 0.0
    stoch_k: float = 50.0
    stoch_d: float = 50.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    willr: float = -50.0
    cci: float = 0.0
    mfi: float = 50.0
    obv: float = 0.0
    vwap_20: float = 0.0
    sar: float = 0.0
    
    # === 1min (Intraday) - Fast momentum indicators ===
    intraday_close: float = 0.0
    intraday_rsi_14: float = 50.0
    intraday_macd: float = 0.0
    intraday_ema_20: float = 0.0
    
    # === 4h (Long-term) - Trend context ===
    longterm_ema_20: float = 0.0
    longterm_ema_50: float = 0.0
    longterm_rsi_14: float = 50.0
    longterm_macd: float = 0.0
    longterm_atr_14: float = 0.0
    longterm_volume: float = 0.0

    @classmethod
    def from_sources(
        cls,
        futures: Dict[str, Any],
        features_30min: Dict[str, Any],
        features_1m: Dict[str, Any] | None = None,
        features_4h: Dict[str, Any] | None = None,
    ) -> "DerivativesFeatures":
        # Use empty dict if None
        features_1m = features_1m or {}
        features_4h = features_4h or {}
        
        return cls(
            # Futures metrics (3)
            long_short_ratio=float(futures.get("long_short_ratio", 0.0) or 0.0),
            open_interest=float(futures.get("open_interest", 0.0) or 0.0),
            funding_rate=float(futures.get("funding_rate", 0.0) or 0.0),
            
            # === 30min (Main) ===
            # Binance spot indicators (17)
            close=float(features_30min.get("close", 0.0) or 0.0),
            ema_20=float(features_30min.get("ema_20", 0.0) or 0.0),
            ema_50=float(features_30min.get("ema_50", 0.0) or 0.0),
            rsi_14=float(features_30min.get("rsi_14", 50.0) or 50.0),
            macd=float(features_30min.get("macd", 0.0) or 0.0),
            macd_signal=float(features_30min.get("macd_signal", 0.0) or 0.0),
            atr_14=float(features_30min.get("atr_14", 0.0) or 0.0),
            stoch_k=float(features_30min.get("stoch_k", 50.0) or 50.0),
            stoch_d=float(features_30min.get("stoch_d", 50.0) or 50.0),
            bb_upper=float(features_30min.get("bb_upper", 0.0) or 0.0),
            bb_middle=float(features_30min.get("bb_middle", 0.0) or 0.0),
            bb_lower=float(features_30min.get("bb_lower", 0.0) or 0.0),
            willr=float(features_30min.get("willr", -50.0) or -50.0),
            cci=float(features_30min.get("cci", 0.0) or 0.0),
            mfi=float(features_30min.get("mfi", 50.0) or 50.0),
            obv=float(features_30min.get("obv", 0.0) or 0.0),
            vwap_20=float(features_30min.get("vwap_20", 0.0) or 0.0),
            sar=float(features_30min.get("sar", 0.0) or 0.0),
            
            # === 1min (Intraday) ===
            intraday_close=float(features_1m.get("close", 0.0) or 0.0),
            intraday_rsi_14=float(features_1m.get("rsi_14", 50.0) or 50.0),
            intraday_macd=float(features_1m.get("macd", 0.0) or 0.0),
            intraday_ema_20=float(features_1m.get("ema_20", 0.0) or 0.0),
            
            # === 4h (Long-term) ===
            longterm_ema_20=float(features_4h.get("ema_20", 0.0) or 0.0),
            longterm_ema_50=float(features_4h.get("ema_50", 0.0) or 0.0),
            longterm_rsi_14=float(features_4h.get("rsi_14", 50.0) or 50.0),
            longterm_macd=float(features_4h.get("macd", 0.0) or 0.0),
            longterm_atr_14=float(features_4h.get("atr_14", 0.0) or 0.0),
            longterm_volume=float(features_4h.get("volume", 0.0) or 0.0),
        )

    def to_snapshot(self) -> dict[str, float]:
        snapshot = {
            "long_short_ratio": self.long_short_ratio,
            "open_interest": self.open_interest,
            "funding_rate": self.funding_rate,
            "close": self.close,
            "ema_20": self.ema_20,
            "ema_50": self.ema_50,
            "rsi_14": self.rsi_14,
            "macd": self.macd,
            "macd_signal": self.macd_signal,
            "atr_14": self.atr_14,
            "stoch_k": self.stoch_k,
            "stoch_d": self.stoch_d,
            "bb_upper": self.bb_upper,
            "bb_middle": self.bb_middle,
            "bb_lower": self.bb_lower,
            "willr": self.willr,
            "cci": self.cci,
            "mfi": self.mfi,
            "obv": self.obv,
            "vwap_20": self.vwap_20,
            "sar": self.sar,
            "intraday_close": self.intraday_close,
            "intraday_rsi_14": self.intraday_rsi_14,
            "intraday_macd": self.intraday_macd,
            "intraday_ema_20": self.intraday_ema_20,
            "longterm_ema_20": self.longterm_ema_20,
            "longterm_ema_50": self.longterm_ema_50,
            "longterm_rsi_14": self.longterm_rsi_14,
            "longterm_macd": self.longterm_macd,
            "longterm_atr_14": self.longterm_atr_14,
            "longterm_volume": self.longterm_volume,
        }
        snapshot.update(self.bias_snapshot())
        return snapshot

    def bias_snapshot(self) -> dict[str, float]:
        trend_scores: list[float] = []
        if self.ema_50 > 0:
            trend_scores.append(_clamp((self.ema_20 - self.ema_50) / self.ema_50 * 5.0, -1.0, 1.0))
        if self.longterm_ema_50 > 0:
            trend_scores.append(
                _clamp((self.longterm_ema_20 - self.longterm_ema_50) / self.longterm_ema_50 * 5.0, -1.0, 1.0)
            )
        macd_delta = self.macd - self.macd_signal
        if macd_delta != 0.0:
            trend_scores.append(_clamp(macd_delta / max(abs(self.macd_signal), 0.1), -1.0, 1.0))
        trend_bias = _clamp(_mean(trend_scores), -1.0, 1.0)

        momentum_scores: list[float] = []
        momentum_scores.append(_clamp((self.rsi_14 - 50.0) / 25.0, -1.0, 1.0))
        momentum_scores.append(_clamp((self.stoch_k - 50.0) / 40.0, -1.0, 1.0))
        momentum_scores.append(_clamp((self.mfi - 50.0) / 25.0, -1.0, 1.0))
        momentum_scores.append(_clamp(-(self.willr + 50.0) / 30.0, -1.0, 1.0))
        intraday_bias = _clamp((self.intraday_rsi_14 - 50.0) / 25.0, -1.0, 1.0)
        momentum_scores.append(intraday_bias)
        momentum_bias = _clamp(_mean(momentum_scores), -1.0, 1.0)

        futures_scores: list[float] = []
        if self.long_short_ratio > 0:
            futures_scores.append(_clamp((self.long_short_ratio - 1.0) / 0.4, -1.0, 1.0))
        if self.funding_rate != 0.0:
            futures_scores.append(_clamp(self.funding_rate / 0.0004, -1.0, 1.0))
        futures_bias = _clamp(_mean(futures_scores), -1.0, 1.0)

        volatility_components: list[float] = []
        if self.close > 0:
            volatility_components.append(_clamp((self.atr_14 / self.close) / 0.02, 0.0, 1.0))
        if self.longterm_ema_20 > 0 and self.longterm_atr_14 > 0:
            volatility_components.append(_clamp((self.longterm_atr_14 / self.longterm_ema_20) / 0.03, 0.0, 1.0))
        volatility_regime = _clamp(_mean(volatility_components), 0.0, 1.0)

        trend_weight = 0.4
        momentum_weight = 0.3
        futures_weight = 0.2
        intraday_weight = 0.1

        if 0.7 <= volatility_regime < 0.9:
            futures_weight = 0.3
            trend_weight = 0.3
            momentum_weight = 0.25
            intraday_weight = 0.15
        elif volatility_regime >= 0.9:
            futures_weight = 0.35
            trend_weight = 0.25
            momentum_weight = 0.25
            intraday_weight = 0.15

        composite_bias = _clamp(
            trend_weight * trend_bias + momentum_weight * momentum_bias +
            futures_weight * futures_bias + intraday_weight * intraday_bias,
            -1.0,
            1.0,
        )
        bias_confidence = min(
            1.0,
            0.35 * abs(trend_bias)
            + 0.3 * abs(momentum_bias)
            + 0.2 * abs(futures_bias)
            + 0.15 * max(0.0, 1.0 - volatility_regime),
        )

        return {
            "trend_bias_score": trend_bias,
            "momentum_bias_score": momentum_bias,
            "intraday_bias_score": intraday_bias,
            "futures_bias_score": futures_bias,
            "composite_bias_score": composite_bias,
            "bias_confidence_score": bias_confidence,
            "volatility_regime_score": volatility_regime,
        }


class DerivativesAgent(Agent):
    def __init__(self, symbol: str = "BTCUSDT", enable_htf_filter: bool = True) -> None:
        self._symbol = symbol
        self._enable_htf_filter = enable_htf_filter
        self._futures_base = str(settings.binance.futures_rest_endpoint)

    def generate_signal(self) -> AgentSignal:
        # LATENCY TRACKING: Start signal generation timing
        signal_start_time = datetime.utcnow()
        
        features = self._collect_features()
        historical_data = self._collect_historical_data()
        snapshot = features.to_snapshot()
        bias_keys = {
            "trend_bias_score",
            "momentum_bias_score",
            "intraday_bias_score",
            "futures_bias_score",
            "composite_bias_score",
            "bias_confidence_score",
            "volatility_regime_score",
        }
        bias_snapshot = {key: snapshot.get(key, 0.0) for key in bias_keys}

        reasoning = self._build_reasoning(features, bias_snapshot)
        
        # HTF Destek/Direnç Filtresi
        htf_analysis = None
        if self._enable_htf_filter:
            htf_analysis = detect_htf_support_resistance(self._symbol)
            if htf_analysis.get("status") == "success":
                in_support = htf_analysis.get("in_support_zone", False)
                in_resistance = htf_analysis.get("in_resistance_zone", False)
                
                # Sinyal reasoning'ine HTF context ekle
                if in_support:
                    reasoning = f"🛡️ HTF Destek Bölgesi | {reasoning}"
                elif in_resistance:
                    reasoning = f"🛡️ HTF Direnç Bölgesi | {reasoning}"
                else:
                    reasoning = f"📍 HTF Nötr Bölge | {reasoning}"
        
        # LATENCY TRACKING: Stage 4d - Signal Generated
        # Try to find most recent trace for this symbol/interval
        tracker = get_latency_tracker()
        trace_id = f"{self._symbol}_30min_{int(signal_start_time.timestamp() * 1000)}"
        trace = tracker.get_trace(trace_id)
        if not trace:
            # If exact trace not found, create a new one for this signal
            trace = tracker.start_trace(
                symbol=self._symbol,
                interval="30min",
                trace_id=trace_id,
            )
        
        trace.mark_signal_generated()
        
        # Calculate signal generation latency
        signal_latency_ms = (datetime.utcnow() - signal_start_time).total_seconds() * 1000
        
        metadata = {
            "historical_data": historical_data,
            "feature_snapshot": snapshot,
            "bias_snapshot": bias_snapshot,
            "trace_id": trace_id,
            "signal_generation_latency_ms": signal_latency_ms,
        }
        
        # Add HTF analysis to metadata if available
        if htf_analysis and htf_analysis.get("status") == "success":
            metadata["htf_analysis"] = {
                "current_price": htf_analysis.get("current_price"),
                "in_support_zone": htf_analysis.get("in_support_zone", False),
                "in_resistance_zone": htf_analysis.get("in_resistance_zone", False),
                "nearest_support": htf_analysis.get("nearest_support"),
                "nearest_resistance": htf_analysis.get("nearest_resistance"),
                "htf_interval": htf_analysis.get("htf_interval", "1h"),
            }
        
        return AgentSignal(
            direction="GLM_ONLY",
            confidence=0.0,
            reasoning=reasoning,
            timestamp=datetime.utcnow().isoformat(),
            metadata=metadata,
        )

    def _fetch_binance_klines_sync(self, interval: str, limit: int = 100) -> List[Dict]:
        """
        Synchronous fallback: Fetch klines from Binance REST API
        Used when InfluxDB is unavailable
        
        Args:
            interval: Kline interval (1m, 30m, 4h)
            limit: Number of klines to fetch
        
        Returns:
            List of kline dicts with OHLCV data
        """
        try:
            params = {
                "symbol": self._symbol,
                "interval": interval,
                "limit": limit,
            }
            
            with httpx.Client(timeout=10.0) as client:
                response = client.get("https://api.binance.com/api/v3/klines", params=params)
                response.raise_for_status()
                klines = response.json()
            
            # Convert to dict format
            result = []
            for kline in klines:
                result.append({
                    "timestamp": pd.to_datetime(int(kline[0]), unit='ms'),
                    "open": float(kline[1]),
                    "high": float(kline[2]),
                    "low": float(kline[3]),
                    "close": float(kline[4]),
                    "volume": float(kline[5]),
                })
            
            logger.info("✅ REST API fallback: Fetched %d klines for %s/%s", len(result), self._symbol, interval)
            return result
            
        except Exception as e:
            logger.error("REST API fallback failed for %s/%s: %s", self._symbol, interval, e)
            return []
    
    def _calculate_indicators_from_klines(self, klines: List[Dict]) -> Dict[str, Any]:
        """
        Calculate technical indicators from klines (REST API fallback)
        
        Args:
            klines: List of kline dicts with OHLCV data
        
        Returns:
            Dict with calculated indicators
        """
        try:
            if not klines or len(klines) < 50:
                logger.warning("Not enough klines for indicator calculation (need 50+, got %d)", len(klines))
                return {}
            
            df = pd.DataFrame(klines)
            indicators = {}
            
            # Latest bar data
            latest = df.iloc[-1]
            indicators['close'] = float(latest['close'])
            indicators['volume'] = float(latest['volume'])
            
            # Trend indicators
            if len(df) >= 20:
                ema_20 = ta.trend.EMAIndicator(df['close'], window=20, fillna=True)
                indicators['ema_20'] = float(ema_20.ema_indicator().iloc[-1])
            
            if len(df) >= 50:
                ema_50 = ta.trend.EMAIndicator(df['close'], window=50, fillna=True)
                indicators['ema_50'] = float(ema_50.ema_indicator().iloc[-1])
            
            # MACD
            macd = ta.trend.MACD(df['close'], fillna=True)
            indicators['macd'] = float(macd.macd().iloc[-1])
            indicators['macd_signal'] = float(macd.macd_signal().iloc[-1])
            
            # RSI
            if len(df) >= 14:
                rsi14 = ta.momentum.RSIIndicator(df['close'], window=14, fillna=True)
                indicators['rsi_14'] = float(rsi14.rsi().iloc[-1])
            
            # ATR
            if len(df) >= 14:
                atr14 = ta.volatility.AverageTrueRange(
                    df['high'], df['low'], df['close'], window=14, fillna=True
                )
                indicators['atr_14'] = float(atr14.average_true_range().iloc[-1])
            
            # Stochastic
            if len(df) >= 14:
                stoch = ta.momentum.StochasticOscillator(
                    df['high'], df['low'], df['close'], window=14, smooth_window=3, fillna=True
                )
                indicators['stoch_k'] = float(stoch.stoch().iloc[-1])
                indicators['stoch_d'] = float(stoch.stoch_signal().iloc[-1])
            
            # Bollinger Bands
            if len(df) >= 20:
                bb = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2, fillna=True)
                indicators['bb_upper'] = float(bb.bollinger_hband().iloc[-1])
                indicators['bb_middle'] = float(bb.bollinger_mavg().iloc[-1])
                indicators['bb_lower'] = float(bb.bollinger_lband().iloc[-1])
            
            # Williams %R
            if len(df) >= 14:
                willr = ta.momentum.WilliamsRIndicator(
                    df['high'], df['low'], df['close'], lbp=14, fillna=True
                )
                indicators['willr'] = float(willr.williams_r().iloc[-1])
            
            # CCI
            if len(df) >= 20:
                cci = ta.trend.CCIIndicator(df['high'], df['low'], df['close'], window=20, fillna=True)
                indicators['cci'] = float(cci.cci().iloc[-1])
            
            # MFI
            if len(df) >= 14:
                mfi = ta.volume.MFIIndicator(
                    df['high'], df['low'], df['close'], df['volume'], window=14, fillna=True
                )
                indicators['mfi'] = float(mfi.money_flow_index().iloc[-1])
            
            # OBV
            obv = ta.volume.OnBalanceVolumeIndicator(df['close'], df['volume'], fillna=True)
            indicators['obv'] = float(obv.on_balance_volume().iloc[-1])
            
            # VWAP
            if len(df) >= 20:
                vwap = ta.volume.VolumeWeightedAveragePrice(
                    df['high'], df['low'], df['close'], df['volume'], window=20, fillna=True
                )
                indicators['vwap_20'] = float(vwap.volume_weighted_average_price().iloc[-1])
            
            # Parabolic SAR
            try:
                psar = ta.trend.PSARIndicator(
                    high=df['high'], low=df['low'], close=df['close'], step=0.02, max_step=0.2, fillna=True
                )
                indicators['sar'] = float(psar.psar().iloc[-1])
            except Exception:
                indicators['sar'] = 0.0
            
            logger.info("✅ REST API indicators calculated: close=%.2f rsi=%.2f", 
                       indicators.get('close', 0), indicators.get('rsi_14', 50))
            
            return indicators
            
        except Exception as e:
            logger.error("Failed to calculate indicators from REST API klines: %s", e, exc_info=True)
            return {}
    
    def _collect_features(self) -> DerivativesFeatures:
        """
        Collect features with multi-level fallback:
        1. PRIMARY: InfluxDB (cached enriched data)
        2. FALLBACK: Binance REST API (when InfluxDB fails)
        
        Ensures system can query at least 4h and 30m data from REST API if needed.
        """
        futures_metrics = self._fetch_futures_metrics()
        
        # Multi-timeframe data collection with REST API fallback
        # 1min data
        features_1m = query_latest_snapshot("enriched_1m", self._symbol, "1m")
        if not features_1m:
            logger.warning("⚠️ InfluxDB failed for 1m, trying REST API fallback...")
            klines_1m = self._fetch_binance_klines_sync("1m", limit=100)
            if klines_1m:
                features_1m = self._calculate_indicators_from_klines(klines_1m)
            else:
                features_1m = {}
        
        # 30min data (CRITICAL - main timeframe)
        features_30min = query_latest_snapshot("enriched_30min", self._symbol, "30min")
        if not features_30min:
            logger.warning("⚠️ InfluxDB failed for 30min, trying REST API fallback...")
            klines_30m = self._fetch_binance_klines_sync("30m", limit=100)
            if klines_30m:
                features_30min = self._calculate_indicators_from_klines(klines_30m)
            else:
                logger.error("🛑 CRITICAL: Cannot fetch 30min data from any source!")
                features_30min = {}
        
        # 4h data (CRITICAL - long-term context)
        features_4h = query_latest_snapshot("enriched_4h", self._symbol, "4h")
        if not features_4h:
            logger.warning("⚠️ InfluxDB failed for 4h, trying REST API fallback...")
            klines_4h = self._fetch_binance_klines_sync("4h", limit=100)
            if klines_4h:
                features_4h = self._calculate_indicators_from_klines(klines_4h)
            else:
                logger.error("🛑 CRITICAL: Cannot fetch 4h data from any source!")
                features_4h = {}
        
        return DerivativesFeatures.from_sources(
            futures_metrics,
            features_30min,
            features_1m=features_1m,
            features_4h=features_4h,
        )
    
    def _collect_historical_data(self) -> Dict[str, dict]:
        """
        Collect last 10 snapshots for multi-timeframe time-series context (oldest → newest)
        
        Returns:
            Dict with keys: "intraday_1m", "medium_15min", "main_30min", "longterm_4h"
            Each containing arrays of indicator values
        """
        # 1min intraday (last 10 = 10 minutes)
        snapshots_1m = query_historical_snapshots("enriched_1m", self._symbol, "1m", limit=10)
        intraday = {}
        if snapshots_1m:
            intraday = {
                "close": [s.get("close", 0) for s in snapshots_1m],
                "rsi_14": [s.get("rsi_14", 50) for s in snapshots_1m],
                "macd": [s.get("macd", 0) for s in snapshots_1m],
                "ema_20": [s.get("ema_20", 0) for s in snapshots_1m],
            }
        
        # 15min medium (last 10 = 2.5 hours)
        snapshots_15min = query_historical_snapshots("enriched_15min", self._symbol, "15min", limit=10)
        medium = {}
        if snapshots_15min:
            medium = {
                "close": [s.get("close", 0) for s in snapshots_15min],
                "rsi_14": [s.get("rsi_14", 50) for s in snapshots_15min],
                "macd": [s.get("macd", 0) for s in snapshots_15min],
                "ema_20": [s.get("ema_20", 0) for s in snapshots_15min],
                "ema_50": [s.get("ema_50", 0) for s in snapshots_15min],
                "stoch_k": [s.get("stoch_k", 50) for s in snapshots_15min],
                "atr_14": [s.get("atr_14", 0) for s in snapshots_15min],
                "mfi": [s.get("mfi", 50) for s in snapshots_15min],
            }
        
        # 30min main (last 10 = 5 hours)
        snapshots_30min = query_historical_snapshots("enriched_30min", self._symbol, "30min", limit=10)
        main = {}
        if snapshots_30min:
            main = {
                "close": [s.get("close", 0) for s in snapshots_30min],
                "rsi_14": [s.get("rsi_14", 50) for s in snapshots_30min],
                "macd": [s.get("macd", 0) for s in snapshots_30min],
                "ema_20": [s.get("ema_20", 0) for s in snapshots_30min],
                "ema_50": [s.get("ema_50", 0) for s in snapshots_30min],
                "stoch_k": [s.get("stoch_k", 50) for s in snapshots_30min],
                "atr_14": [s.get("atr_14", 0) for s in snapshots_30min],
                "mfi": [s.get("mfi", 50) for s in snapshots_30min],
            }
        
        # 4h long-term (last 10 = 40 hours)
        snapshots_4h = query_historical_snapshots("enriched_4h", self._symbol, "4h", limit=10)
        longterm = {}
        if snapshots_4h:
            longterm = {
                "ema_20": [s.get("ema_20", 0) for s in snapshots_4h],
                "ema_50": [s.get("ema_50", 0) for s in snapshots_4h],
                "rsi_14": [s.get("rsi_14", 50) for s in snapshots_4h],
                "macd": [s.get("macd", 0) for s in snapshots_4h],
                "atr_14": [s.get("atr_14", 0) for s in snapshots_4h],
                "volume": [s.get("volume", 0) for s in snapshots_4h],
            }
        
        return {
            "intraday_1m": intraday,
            "medium_15min": medium,
            "main_30min": main,
            "longterm_4h": longterm,
        }

    def _fetch_futures_metrics(self) -> Dict[str, Any]:
        params = {"symbol": self._symbol.upper(), "period": "5m", "limit": 1}
        try:
            with httpx.Client(base_url=self._futures_base, timeout=5.0) as client:
                ratio = self._fetch_indicator(client, "futures/data/globalLongShortAccountRatio", params)
                interest = self._fetch_indicator(client, "futures/data/openInterestHist", params)
                funding = self._fetch_indicator(client, "fapi/v1/fundingRate", {"symbol": self._symbol.upper(), "limit": 1})
        except Exception as exc:  # noqa: BLE001
            logger.debug("Futures metrikleri alınamadı: %s", exc)
            return {}

        return {
            "long_short_ratio": ratio,
            "open_interest": interest,
            "funding_rate": funding,
        }

    def _fetch_indicator(self, client: httpx.Client, path: str, params: Dict[str, Any]) -> float:
        response = client.get(path, params=params)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and data:
            entry = data[0]
            for key in ("longShortRatio", "sumOpenInterestValue", "fundingRate", "value"):
                if key in entry:
                    return float(entry[key])
        if isinstance(data, dict):
            value = data.get("value")
            if value is not None:
                return float(value)
        raise ValueError(f"Beklenmeyen veri: {data}")

    def _build_reasoning(self, features: DerivativesFeatures, bias: Dict[str, float] | None = None) -> str:
        """Summarize raw indicators used in GLM prompt for logging/debug."""
        parts = []

        bias = bias or features.bias_snapshot()

        # === MULTI-TIMEFRAME HEADER ===
        parts.append("| TF:")
        
        # 1min intraday
        if features.intraday_close > 0:
            intraday_rsi_label = "🔴" if features.intraday_rsi_14 > 70 else "🟢" if features.intraday_rsi_14 < 30 else ""
            parts.append(f"1m(RSI:{features.intraday_rsi_14:.0f}{intraday_rsi_label})")
        
        # 30min main (current timeframe label)
        parts.append("30m(main)")
        
        # 4h long-term
        if features.longterm_ema_20 > 0:
            lt_trend = "↑" if features.longterm_ema_20 > features.longterm_ema_50 else "↓"
            parts.append(f"4h({lt_trend}trend)")
        
        parts.append("|")
        
        # Futures metrics (3)
        parts.append(f"L/S: {features.long_short_ratio:.2f}")
        if features.long_short_ratio > 1.5:
            parts.append("(↑long)")
        elif features.long_short_ratio < 0.7:
            parts.append("(↓short)")
        
        parts.append(f"OI: {features.open_interest:.0f}")
        
        parts.append(f"FR: {features.funding_rate:.6f}")
        if abs(features.funding_rate) > 0.0005:
            parts.append("(!)") 
        
        # 30min Trend indicators
        ema_dir = "↑" if features.ema_20 > features.ema_50 else "↓"
        parts.append(f"EMA: {ema_dir}")
        
        # RSI
        rsi_label = "🔴" if features.rsi_14 > 70 else "🟢" if features.rsi_14 < 30 else ""
        parts.append(f"RSI: {features.rsi_14:.0f}{rsi_label}")
        
        # MACD
        macd_dir = "↑" if features.macd > features.macd_signal else "↓"
        parts.append(f"MACD: {macd_dir} ({features.macd:.1f})")
        
        # ATR (volatility)
        parts.append(f"ATR: {features.atr_14:.1f}")
        
        # Stochastic
        stoch_label = "🔴" if features.stoch_k > 80 else "🟢" if features.stoch_k < 20 else ""
        parts.append(f"Stoch: {features.stoch_k:.0f}/{features.stoch_d:.0f}{stoch_label}")
        
        # Bollinger Bands
        if features.bb_upper > 0 and features.close > 0:
            bb_range = features.bb_upper - features.bb_lower
            if bb_range > 0:
                bb_pct = ((features.close - features.bb_lower) / bb_range) * 100
                bb_label = "üst" if bb_pct > 80 else "alt" if bb_pct < 20 else "orta"
                parts.append(f"BB: {bb_pct:.0f}%({bb_label})")
            else:
                parts.append("BB: N/A")
        else:
            parts.append("BB: N/A")
        
        # Additional indicators
        # Williams %R
        willr_label = "🔴" if features.willr > -20 else "🟢" if features.willr < -80 else ""
        parts.append(f"WillR: {features.willr:.0f}{willr_label}")
        
        # CCI
        cci_label = "🔴" if abs(features.cci) > 100 else ""
        parts.append(f"CCI: {features.cci:.0f}{cci_label}")
        
        # MFI
        mfi_label = "🔴" if features.mfi > 80 else "🟢" if features.mfi < 20 else ""
        parts.append(f"MFI: {features.mfi:.0f}{mfi_label}")
        
        # Volume indicators
        if features.obv != 0:
            obv_m = features.obv / 1_000_000  # Convert to millions
            parts.append(f"OBV: {obv_m:.1f}M")
        
        # VWAP (compare with close)
        if features.vwap_20 > 0 and features.close > 0:
            vwap_diff = ((features.close - features.vwap_20) / features.vwap_20) * 100
            vwap_label = "↑" if vwap_diff > 0.5 else "↓" if vwap_diff < -0.5 else "≈"
            parts.append(f"VWAP: {vwap_label}{vwap_diff:+.2f}%")

        # PSAR summary
        if features.sar > 0 and features.close > 0:
            sar_dir = "↑" if features.close > features.sar else "↓"
            parts.append(f"SAR: {sar_dir}")

        parts.append(
            "Biases: "
            f"trend={bias.get('trend_bias_score', 0.0):+.2f} "
            f"mom={bias.get('momentum_bias_score', 0.0):+.2f} "
            f"fut={bias.get('futures_bias_score', 0.0):+.2f} "
            f"intraday={bias.get('intraday_bias_score', 0.0):+.2f} "
            f"vol={bias.get('volatility_regime_score', 0.0):.2f} "
            f"comp={bias.get('composite_bias_score', 0.0):+.2f}"
        )

        return " | ".join(parts)
