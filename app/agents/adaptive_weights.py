"""
Adaptive Weight System - Dynamically adjusts weights based on market conditions
"""
from dataclasses import dataclass
from typing import Dict, Any

from app.utils.logging import get_logger


logger = get_logger(__name__)


@dataclass
class MarketRegime:
    """Market regime classification"""
    volatility: str  # "low", "medium", "high"
    trend_strength: str  # "weak", "moderate", "strong"
    volume_profile: str  # "low", "normal", "high"


class AdaptiveWeightManager:
    """
    Dynamically adjust signal component weights based on market conditions
    
    Strategy:
    - High Volatility → Reduce trend weight, increase volatility awareness
    - Strong Trend → Increase trend weight, reduce momentum
    - Choppy/Sideways → Increase sentiment/volume, reduce trend
    - Low Volume → Reduce all weights, more conservative
    """
    
    # Base weights (default)
    BASE_WEIGHTS = {
        'trend': 0.35,
        'momentum': 0.30,
        'sentiment': 0.20,
        'volatility': 0.10,
        'volume': 0.05,
    }
    
    # Regime-specific adjustments (EXPANDED - all combinations covered)
    REGIME_ADJUSTMENTS = {
        # HIGH VOLATILITY
        ('high', 'weak', 'low'): {  # Choppy low volume
            'trend': -0.15,
            'momentum': +0.10,
            'sentiment': +0.10,
            'volatility': +0.15,
            'volume': -0.20,
        },
        ('high', 'weak', 'normal'): {  # Choppy volatile market
            'trend': -0.10,
            'momentum': +0.05,
            'sentiment': +0.10,
            'volatility': +0.15,
            'volume': -0.20,
        },
        ('high', 'weak', 'high'): {  # Choppy with high volume
            'trend': -0.10,
            'momentum': +0.05,
            'sentiment': +0.05,
            'volatility': +0.10,
            'volume': -0.10,
        },
        ('high', 'moderate', 'normal'): {  # Moderate trend, high vol
            'trend': +0.00,
            'momentum': +0.05,
            'sentiment': +0.05,
            'volatility': +0.10,
            'volume': -0.20,
        },
        ('high', 'strong', 'normal'): {  # Strong trend, high vol
            'trend': +0.05,
            'momentum': -0.05,
            'sentiment': +0.00,
            'volatility': +0.10,
            'volume': -0.10,
        },
        ('high', 'strong', 'high'): {  # Strong volatile trending
            'trend': +0.10,
            'momentum': -0.05,
            'sentiment': +0.00,
            'volatility': +0.05,
            'volume': -0.10,
        },
        
        # MEDIUM VOLATILITY
        ('medium', 'weak', 'low'): {  # Sideways, low volume
            'trend': -0.10,
            'momentum': +0.10,
            'sentiment': +0.15,
            'volatility': -0.05,
            'volume': -0.10,
        },
        ('medium', 'weak', 'normal'): {  # Sideways market
            'trend': -0.08,
            'momentum': +0.12,
            'sentiment': +0.10,
            'volatility': -0.04,
            'volume': -0.10,
        },
        ('medium', 'moderate', 'normal'): {  # Balanced
            'trend': +0.00,
            'momentum': +0.05,
            'sentiment': +0.00,
            'volatility': -0.05,
            'volume': +0.00,
        },
        ('medium', 'moderate', 'high'): {  # Moderate trend, good volume
            'trend': +0.05,
            'momentum': +0.00,
            'sentiment': -0.05,
            'volatility': -0.05,
            'volume': +0.05,
        },
        ('medium', 'strong', 'normal'): {  # Clean trend
            'trend': +0.10,
            'momentum': -0.05,
            'sentiment': -0.05,
            'volatility': -0.05,
            'volume': +0.05,
        },
        ('medium', 'strong', 'high'): {  # Good trending with volume
            'trend': +0.15,
            'momentum': -0.05,
            'sentiment': -0.10,
            'volatility': -0.05,
            'volume': +0.05,
        },
        
        # LOW VOLATILITY
        ('low', 'weak', 'low'): {  # Dead market
            'trend': -0.15,
            'momentum': +0.10,
            'sentiment': +0.20,
            'volatility': -0.10,
            'volume': -0.05,
        },
        ('low', 'weak', 'normal'): {  # Consolidation (CURRENT ISSUE!)
            'trend': -0.10,
            'momentum': +0.15,
            'sentiment': +0.15,
            'volatility': -0.10,
            'volume': -0.10,
        },
        ('low', 'moderate', 'normal'): {  # Slow grind
            'trend': +0.05,
            'momentum': +0.10,
            'sentiment': +0.00,
            'volatility': -0.10,
            'volume': -0.05,
        },
        ('low', 'strong', 'normal'): {  # Clean trending
            'trend': +0.15,
            'momentum': -0.10,
            'sentiment': -0.10,
            'volatility': -0.05,
            'volume': +0.10,
        },
        ('low', 'strong', 'high'): {  # Strong trend, high conviction
            'trend': +0.20,
            'momentum': -0.10,
            'sentiment': -0.15,
            'volatility': -0.05,
            'volume': +0.10,
        },
    }
    
    def classify_regime(self, enriched_data: Dict[str, Any]) -> MarketRegime:
        """
        Classify current market regime
        
        Args:
            enriched_data: Dict with all indicators
        
        Returns:
            MarketRegime classification
        """
        close = enriched_data.get('close', 0)
        
        # 1. Volatility Classification (more sensitive)
        atr = enriched_data.get('atr_14', 0)
        bb_upper = enriched_data.get('bb_upper', 0)
        bb_lower = enriched_data.get('bb_lower', 0)
        
        # ATR percentage
        atr_pct = (atr / close * 100) if close > 0 else 0.5
        
        # Bollinger Band width percentage
        bb_width_pct = ((bb_upper - bb_lower) / close * 100) if close > 0 else 1.0
        
        # Combined volatility score
        vol_score = (atr_pct * 0.6) + (bb_width_pct * 0.4)
        
        if vol_score < 0.8:
            volatility = "low"
        elif vol_score > 1.8:
            volatility = "high"
        else:
            volatility = "medium"
        
        # 2. Trend Strength (improved with multiple indicators)
        ema_20 = enriched_data.get('ema_20', 0)
        ema_50 = enriched_data.get('ema_50', 0)
        macd = enriched_data.get('macd', 0)
        macd_signal = enriched_data.get('macd_signal', 0)
        
        # EMA divergence
        ema_diff_pct = abs(ema_20 - ema_50) / close * 100 if close > 0 else 0
        
        # MACD strength
        macd_strength = abs(macd - macd_signal) if macd and macd_signal else 0
        
        # Price distance from EMAs
        price_ema_dist = abs(close - ema_20) / close * 100 if close > 0 and ema_20 > 0 else 0
        
        # Combined trend score
        trend_score = (ema_diff_pct * 0.4) + (macd_strength / 100 * 0.3) + (price_ema_dist * 0.3)
        
        if trend_score < 0.15:
            trend_strength = "weak"
        elif trend_score > 0.6:
            trend_strength = "strong"
        else:
            trend_strength = "moderate"
        
        # 3. Volume Profile (more dynamic)
        oi = enriched_data.get('open_interest', 0)
        obv = enriched_data.get('obv', 0)
        
        # OI thresholds (adjusted for BTC)
        if oi < 5_000_000_000:
            volume_profile = "low"
        elif oi > 12_000_000_000:
            volume_profile = "high"
        else:
            volume_profile = "normal"
        
        logger.debug(
            "Regime classification: vol_score=%.3f trend_score=%.3f oi=%.1fB → %s/%s/%s",
            vol_score, trend_score, oi / 1e9,
            volatility, trend_strength, volume_profile
        )
        
        return MarketRegime(
            volatility=volatility,
            trend_strength=trend_strength,
            volume_profile=volume_profile,
        )
    
    def get_adaptive_weights(self, enriched_data: Dict[str, Any]) -> Dict[str, float]:
        """
        Calculate adaptive weights based on current market regime
        
        Args:
            enriched_data: Dict with all indicators
        
        Returns:
            Dict of adjusted weights
        """
        regime = self.classify_regime(enriched_data)
        
        # Start with base weights
        weights = self.BASE_WEIGHTS.copy()
        
        # Find matching regime adjustment
        regime_key = (regime.volatility, regime.trend_strength, regime.volume_profile)
        
        adjustments = self.REGIME_ADJUSTMENTS.get(regime_key)
        
        if not adjustments:
            # Find closest match (fuzzy matching)
            adjustments = self._find_closest_regime(regime)
        
        # Apply adjustments
        for component, adjustment in adjustments.items():
            weights[component] = max(0.0, min(1.0, weights[component] + adjustment))
        
        # Normalize to sum to 1.0
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        
        logger.info(
            "Adaptive weights: regime=%s/%s/%s trend=%.2f momentum=%.2f sentiment=%.2f",
            regime.volatility,
            regime.trend_strength,
            regime.volume_profile,
            weights['trend'],
            weights['momentum'],
            weights['sentiment'],
        )
        
        return weights
    
    def _find_closest_regime(self, regime: MarketRegime) -> Dict[str, float]:
        """Find closest matching regime if exact match not found"""
        
        # Priority: volatility > trend > volume
        for key, adjustments in self.REGIME_ADJUSTMENTS.items():
            vol, trend, vol_prof = key
            
            # Match volatility first
            if vol == regime.volatility:
                return adjustments
        
        # Fallback to balanced (medium/moderate/normal)
        return self.REGIME_ADJUSTMENTS.get(
            ('medium', 'moderate', 'normal'),
            {k: 0.0 for k in self.BASE_WEIGHTS}
        )
    
    def explain_regime(self, enriched_data: Dict[str, Any]) -> str:
        """
        Generate human-readable explanation of current regime
        
        Returns:
            String description
        """
        regime = self.classify_regime(enriched_data)
        weights = self.get_adaptive_weights(enriched_data)
        
        return (
            f"Market Regime: {regime.volatility.upper()} volatility, "
            f"{regime.trend_strength.upper()} trend, "
            f"{regime.volume_profile.upper()} volume | "
            f"Weights: T={weights['trend']:.0%} M={weights['momentum']:.0%} "
            f"S={weights['sentiment']:.0%}"
        )
