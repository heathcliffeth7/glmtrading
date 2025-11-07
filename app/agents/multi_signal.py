"""
Multi-Signal Agent - Uses enriched data from all sources
Combines: TA indicators + TwelveData + Binance Futures
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any

from app.agents.base import Agent, AgentSignal
from app.agents.adaptive_weights import AdaptiveWeightManager
from app.utils.influx import query_latest_snapshot
from app.utils.logging import get_logger


logger = get_logger(__name__)


@dataclass
class SignalComponents:
    """Individual signal components with weights"""
    trend: float = 0.0  # -1 to 1
    momentum: float = 0.0  # -1 to 1
    volatility: float = 0.0  # 0 to 1 (higher = more volatile)
    sentiment: float = 0.0  # -1 to 1
    volume: float = 0.0  # 0 to 1 (higher = stronger)


class MultiSignalAgent(Agent):
    """
    Advanced agent that combines multiple signal sources
    
    Weights:
    - Trend (35%): EMA crossovers, MACD
    - Momentum (30%): RSI, Stochastic
    - Sentiment (20%): Long/Short Ratio, Funding Rate
    - Volatility (10%): ATR, Bollinger Bands
    - Volume (5%): Volume trends, OI
    """
    
    def __init__(self, symbol: str = "BTCUSDT", interval: str = "5min", adaptive: bool = True) -> None:
        self._symbol = symbol
        self._interval = interval
        self._adaptive = adaptive
        
        # Adaptive weight manager
        self._weight_manager = AdaptiveWeightManager() if adaptive else None
        
        # Base weights (used if not adaptive)
        self._base_weights = {
            'trend': 0.35,
            'momentum': 0.30,
            'sentiment': 0.20,
            'volatility': 0.10,
            'volume': 0.05,
        }
    
    def generate_signal(self) -> AgentSignal:
        """Generate signal from enriched data"""
        
        # Get enriched data from InfluxDB
        enriched = query_latest_snapshot(f"enriched_{self._interval}", self._symbol, self._interval)
        
        if not enriched:
            # Fallback to basic features
            logger.warning("No enriched data, falling back to basic features")
            return self._fallback_signal()
        
        # Calculate signal components
        components = self._calculate_components(enriched)
        
        # Get adaptive or base weights
        if self._adaptive and self._weight_manager:
            weights = self._weight_manager.get_adaptive_weights(enriched)
        else:
            weights = self._base_weights
        
        # Weighted aggregate
        score = (
            components.trend * weights['trend'] +
            components.momentum * weights['momentum'] +
            components.sentiment * weights['sentiment'] +
            components.volatility * weights['volatility'] * -1 +  # High volatility = caution
            components.volume * weights['volume']
        )
        
        # Determine direction and confidence
        # Threshold increased to 0.30 to avoid weak signals
        if score > 0.30:
            direction = "BUY"
            confidence = min(1.0, abs(score))
        elif score < -0.30:
            direction = "SELL"
            confidence = min(1.0, abs(score))
        else:
            direction = "HOLD"
            confidence = 0.0
        
        # Build reasoning
        reasoning = self._build_reasoning(components, enriched)
        
        return AgentSignal(
            direction=direction,
            confidence=confidence,
            reasoning=reasoning,
            timestamp=datetime.utcnow().isoformat(),
        )
    
    def _calculate_components(self, data: Dict[str, Any]) -> SignalComponents:
        """Calculate individual signal components"""
        
        # 1. TREND ANALYSIS
        trend = self._analyze_trend(data)
        
        # 2. MOMENTUM ANALYSIS
        momentum = self._analyze_momentum(data)
        
        # 3. SENTIMENT ANALYSIS
        sentiment = self._analyze_sentiment(data)
        
        # 4. VOLATILITY ANALYSIS
        volatility = self._analyze_volatility(data)
        
        # 5. VOLUME ANALYSIS
        volume = self._analyze_volume(data)
        
        return SignalComponents(
            trend=trend,
            momentum=momentum,
            sentiment=sentiment,
            volatility=volatility,
            volume=volume,
        )
    
    def _analyze_trend(self, data: Dict[str, Any]) -> float:
        """
        Analyze trend strength and direction
        Returns: -1 (strong downtrend) to 1 (strong uptrend)
        """
        score = 0.0
        count = 0
        
        # EMA crossover
        ema_20 = data.get('ema_20', 0)
        ema_50 = data.get('ema_50', 0)
        close = data.get('close', 0)
        
        if ema_20 > 0 and ema_50 > 0 and close > 0:
            # EMA alignment
            if ema_20 > ema_50:
                score += 0.4
            else:
                score -= 0.4
            count += 1
            
            # Price relative to EMAs
            if close > ema_20:
                score += 0.3
            else:
                score -= 0.3
            count += 1
        
        # MACD
        macd = data.get('macd', 0)
        macd_signal = data.get('macd_signal', 0)
        
        if macd > macd_signal:
            score += 0.2
        else:
            score -= 0.2
        count += 1
        
        # Parabolic SAR (NEW)
        sar = data.get('sar', 0)
        if sar > 0 and close > 0:
            if close > sar:
                score += 0.2  # Price above SAR = uptrend
            else:
                score -= 0.2  # Price below SAR = downtrend
            count += 1
        
        # VWAP (NEW)
        vwap = data.get('vwap_twelvedata', 0)
        if vwap > 0 and close > 0:
            if close > vwap:
                score += 0.15  # Price above VWAP = bullish
            else:
                score -= 0.15  # Price below VWAP = bearish
            count += 1
        
        return score / count if count > 0 else 0.0
    
    def _analyze_momentum(self, data: Dict[str, Any]) -> float:
        """
        Analyze momentum indicators
        Returns: -1 (oversold/bullish) to 1 (overbought/bearish)
        """
        score = 0.0
        count = 0
        
        # RSI
        rsi = data.get('rsi_14', 50)
        if rsi < 30:
            score -= 0.4  # Oversold = bullish
        elif rsi > 70:
            score += 0.4  # Overbought = bearish
        else:
            score += (rsi - 50) / 100  # Neutral scaling
        count += 1
        
        # Stochastic
        stoch_k = data.get('stoch_k', 50)
        stoch_d = data.get('stoch_d', 50)
        
        if stoch_k < 20:
            score -= 0.25
        elif stoch_k > 80:
            score += 0.25
        
        if stoch_k > stoch_d:
            score -= 0.15  # K crossing above D = bullish
        else:
            score += 0.15
        count += 1
        
        # Williams %R (NEW)
        willr = data.get('willr', -50)
        if willr < -80:
            score -= 0.3  # Oversold = bullish
        elif willr > -20:
            score += 0.3  # Overbought = bearish
        else:
            score += (willr + 50) / 100  # Neutral scaling (-50 is neutral)
        count += 1
        
        # CCI (NEW)
        cci = data.get('cci', 0)
        if cci < -100:
            score -= 0.3  # Oversold = bullish
        elif cci > 100:
            score += 0.3  # Overbought = bearish
        else:
            score += cci / 200  # Normalize to ±0.5
        count += 1
        
        # MFI (NEW)
        mfi = data.get('mfi', 50)
        if mfi < 20:
            score -= 0.25  # Oversold = bullish (weak buying pressure)
        elif mfi > 80:
            score += 0.25  # Overbought = bearish (extreme buying)
        else:
            score += (mfi - 50) / 100  # Neutral scaling
        count += 1
        
        return score / count if count > 0 else 0.0
    
    def _analyze_sentiment(self, data: Dict[str, Any]) -> float:
        """
        Analyze market sentiment from derivatives
        Returns: -1 (bearish sentiment) to 1 (bullish sentiment)
        """
        score = 0.0
        count = 0
        
        # Long/Short Ratio
        ls_ratio = data.get('long_short_ratio', 1.0)
        
        # Extreme values indicate potential reversal
        if ls_ratio > 1.5:
            score -= 0.4  # Too many longs = bearish
        elif ls_ratio < 0.7:
            score += 0.4  # Too many shorts = bullish
        else:
            score += (ls_ratio - 1.0) * 0.5
        count += 1
        
        # Funding Rate
        funding = data.get('funding_rate', 0)
        
        if funding > 0.001:  # Positive funding = longs paying shorts
            score -= 0.3
        elif funding < -0.001:  # Negative funding = shorts paying longs
            score += 0.3
        count += 1
        
        return score / count if count > 0 else 0.0
    
    def _analyze_volatility(self, data: Dict[str, Any]) -> float:
        """
        Analyze volatility level
        Returns: 0 (low vol) to 1 (high vol)
        """
        score = 0.0
        count = 0
        
        # ATR relative to price
        atr = data.get('atr_14', 0)
        close = data.get('close', 0)
        
        if close > 0 and atr > 0:
            atr_pct = (atr / close) * 100
            # Normalize: 0.5% = low, 3% = high
            score += min(1.0, atr_pct / 3.0)
            count += 1
        
        # Bollinger Band width
        bb_upper = data.get('bb_upper', 0)
        bb_lower = data.get('bb_lower', 0)
        
        if bb_upper > bb_lower and close > 0:
            bb_width = (bb_upper - bb_lower) / close
            # Normalize: 0.02 = low, 0.08 = high
            score += min(1.0, bb_width / 0.08)
            count += 1
        
        return score / count if count > 0 else 0.5
    
    def _analyze_volume(self, data: Dict[str, Any]) -> float:
        """
        Analyze volume strength and trends
        Returns: 0 (weak) to 1 (strong)
        """
        score = 0.0
        count = 0
        
        # Open Interest
        oi = data.get('open_interest', 0)
        if oi > 1000000000:  # > 1B
            score += 0.8
        elif oi > 500000000:  # > 500M
            score += 0.6
        elif oi > 100000000:  # > 100M
            score += 0.4
        else:
            score += 0.2
        count += 1
        
        # OBV (NEW) - On Balance Volume
        # Note: OBV needs historical comparison for true signal
        # For now, just check if it exists (baseline for future)
        obv = data.get('obv', 0)
        if obv != 0:
            # OBV is cumulative, positive = accumulation
            # We'll use a simple threshold for now
            if obv > 1000:
                score += 0.7
            elif obv > 100:
                score += 0.5
            else:
                score += 0.3
            count += 1
        
        return score / count if count > 0 else 0.5
    
    def _build_reasoning(self, components: SignalComponents, data: Dict[str, Any]) -> str:
        """Build human-readable reasoning"""
        
        parts = []
        
        # Trend
        if abs(components.trend) > 0.3:
            trend_dir = "Yukarı" if components.trend > 0 else "Aşağı"
            parts.append(f"Trend: {trend_dir} ({components.trend:+.2f})")
        
        # Momentum
        rsi = data.get('rsi_14', 50)
        if rsi < 30:
            parts.append(f"RSI aşırı satım ({rsi:.1f})")
        elif rsi > 70:
            parts.append(f"RSI aşırı alım ({rsi:.1f})")
        
        # Sentiment
        ls_ratio = data.get('long_short_ratio', 1.0)
        if ls_ratio > 1.3:
            parts.append(f"L/S oranı yüksek ({ls_ratio:.2f})")
        elif ls_ratio < 0.8:
            parts.append(f"L/S oranı düşük ({ls_ratio:.2f})")
        
        # Volatility
        if components.volatility > 0.7:
            parts.append("Yüksek volatilite")
        
        # Funding
        funding = data.get('funding_rate', 0)
        if abs(funding) > 0.001:
            funding_pct = funding * 100
            parts.append(f"Funding: {funding_pct:+.3f}%")
        
        if not parts:
            return "Nötr piyasa koşulları"
        
        return " | ".join(parts)
    
    def _fallback_signal(self) -> AgentSignal:
        """Fallback when enriched data not available"""
        
        # Try basic features
        features = query_latest_snapshot(f"features_{self._interval}", self._symbol, self._interval)
        
        if not features:
            return AgentSignal(
                direction="HOLD",
                confidence=0.0,
                reasoning="Veri yok",
                timestamp=datetime.utcnow().isoformat(),
            )
        
        # Simple RSI-based signal
        rsi = features.get('rsi_14', 50)
        
        if rsi < 30:
            return AgentSignal(
                direction="BUY",
                confidence=0.6,
                reasoning=f"Basit RSI sinyali (aşırı satım: {rsi:.1f})",
                timestamp=datetime.utcnow().isoformat(),
            )
        elif rsi > 70:
            return AgentSignal(
                direction="SELL",
                confidence=0.6,
                reasoning=f"Basit RSI sinyali (aşırı alım: {rsi:.1f})",
                timestamp=datetime.utcnow().isoformat(),
            )
        else:
            return AgentSignal(
                direction="HOLD",
                confidence=0.0,
                reasoning=f"Basit RSI nötr ({rsi:.1f})",
                timestamp=datetime.utcnow().isoformat(),
            )
