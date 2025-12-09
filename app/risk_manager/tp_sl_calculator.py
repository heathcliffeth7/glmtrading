"""
TP/SL Calculator - ATR-based dynamic take profit and stop loss calculation.

Provides dynamic TP/SL levels based on market volatility (ATR) and volatility regime.
"""
from typing import Dict, Optional
from app.utils.logging import get_logger

logger = get_logger(__name__)


class TPSLCalculator:
    """
    ATR-based dynamic TP/SL calculator with volatility regime support.
    
    Strategy:
    - Stop Loss: ATR × regime-based multiplier from entry
    - Take Profit: SL distance × regime-based risk/reward ratio
    
    Volatility Regimes:
    - LOW: SL 1.0x ATR, R:R 3.0:1 (tight stops, higher targets)
    - MEDIUM: SL 1.5x ATR, R:R 2.5:1 (balanced)
    - HIGH: SL 2.0x ATR, R:R 2.0:1 (wider stops)
    - EXTREME: SL 2.5x ATR, R:R 1.5:1 (very wide stops, conservative targets)
    
    This ensures:
    1. SL adapts to market volatility
    2. Risk/reward ratio adapts to regime
    3. Wider stops in volatile markets, tighter in calm markets
    """
    
    # Default regime configuration
    DEFAULT_REGIME_CONFIG = {
        'low': {'sl_multiplier': 1.0, 'risk_reward': 3.0},
        'medium': {'sl_multiplier': 1.5, 'risk_reward': 2.5},
        'high': {'sl_multiplier': 2.0, 'risk_reward': 2.0},
        'extreme': {'sl_multiplier': 2.5, 'risk_reward': 1.5},
    }
    
    def __init__(
        self,
        use_dynamic_regime: bool = True,
        regime_config: Optional[Dict] = None,
        default_atr_multiplier: float = 1.5,
        default_risk_reward: float = 2.5,
    ):
        """
        Initialize calculator.
        
        Args:
            use_dynamic_regime: Use volatility regime-based parameters
            regime_config: Custom regime config (overrides DEFAULT_REGIME_CONFIG)
            default_atr_multiplier: Fallback ATR multiplier if regime disabled
            default_risk_reward: Fallback R:R ratio if regime disabled
        """
        self.use_dynamic_regime = use_dynamic_regime
        self.regime_config = regime_config or self.DEFAULT_REGIME_CONFIG
        self.default_atr_multiplier = default_atr_multiplier
        self.default_risk_reward = default_risk_reward
        
    def calculate(
        self,
        signal: str,
        entry_price: float,
        atr: float,
        volatility_regime: Optional[str] = None,
        atr_multiplier: Optional[float] = None,
        risk_reward: Optional[float] = None,
    ) -> Dict[str, float]:
        """
        Calculate TP and SL levels for a trade.
        
        Args:
            signal: 'BUY' or 'SELL'
            entry_price: Entry price level
            atr: ATR value (absolute price units)
            volatility_regime: 'low', 'medium', 'high', 'extreme' (case-insensitive)
            atr_multiplier: Manual override for ATR multiplier
            risk_reward: Manual override for risk/reward ratio
            
        Returns:
            {
                'take_profit': TP price level,
                'stop_loss': SL price level,
                'risk_pct': Risk as % of entry,
                'reward_pct': Reward as % of entry,
                'sl_distance': Absolute SL distance,
                'tp_distance': Absolute TP distance,
                'atr_multiplier': Used ATR multiplier,
                'risk_reward': Used R:R ratio,
                'regime': Used regime
            }
        """
        if signal not in ['BUY', 'SELL']:
            raise ValueError(f"Invalid signal: {signal}. Must be BUY or SELL.")
        
        if atr <= 0:
            raise ValueError(f"ATR must be positive, got {atr}")
        
        if entry_price <= 0:
            raise ValueError(f"Entry price must be positive, got {entry_price}")
        
        # Determine parameters
        if atr_multiplier is not None and risk_reward is not None:
            # Manual override
            atr_mult = atr_multiplier
            rr_ratio = risk_reward
            regime = "manual"
        elif self.use_dynamic_regime and volatility_regime:
            # Regime-based
            regime_key = volatility_regime.lower()
            config = self.regime_config.get(regime_key)
            
            if config is None:
                logger.warning("Unknown regime '%s', using default 'medium'", regime_key)
                config = self.regime_config['medium']
                regime = "medium"
            else:
                regime = regime_key
            
            atr_mult = config['sl_multiplier']
            rr_ratio = config['risk_reward']
        else:
            # Fallback to defaults
            atr_mult = self.default_atr_multiplier
            rr_ratio = self.default_risk_reward
            regime = "default"
        
        # Calculate distances
        sl_distance = atr * atr_mult
        tp_distance = sl_distance * rr_ratio
        
        # Calculate price levels
        if signal == 'BUY':
            stop_loss = entry_price - sl_distance
            take_profit = entry_price + tp_distance
        else:  # SELL
            stop_loss = entry_price + sl_distance
            take_profit = entry_price - tp_distance
        
        # Calculate percentages
        risk_pct = (sl_distance / entry_price) * 100
        reward_pct = (tp_distance / entry_price) * 100
        
        result = {
            'take_profit': round(take_profit, 2),
            'stop_loss': round(stop_loss, 2),
            'risk_pct': round(risk_pct, 2),
            'reward_pct': round(reward_pct, 2),
            'sl_distance': round(sl_distance, 2),
            'tp_distance': round(tp_distance, 2),
            'atr_multiplier': round(atr_mult, 2),
            'risk_reward': round(rr_ratio, 2),
            'regime': regime,
        }
        
        logger.info(
            "TP/SL [%s regime] for %s @ $%.2f | ATR=%.2f | SL=$%.2f (-%.1f%%) | TP=$%.2f (+%.1f%%) | R:R=%.1f:1",
            regime.upper(), signal, entry_price, atr,
            result['stop_loss'], result['risk_pct'],
            result['take_profit'], result['reward_pct'],
            rr_ratio
        )
        
        return result


def get_tp_sl_calculator(
    use_dynamic_regime: bool = True,
    regime_config: Optional[Dict] = None,
    default_atr_multiplier: float = 1.5,
    default_risk_reward: float = 2.5,
) -> TPSLCalculator:
    """
    Factory function to get TPSLCalculator instance.
    
    Args:
        use_dynamic_regime: Use volatility regime-based parameters
        regime_config: Custom regime config
        default_atr_multiplier: Fallback ATR multiplier
        default_risk_reward: Fallback R:R ratio
    
    Returns:
        TPSLCalculator instance
    """
    return TPSLCalculator(
        use_dynamic_regime=use_dynamic_regime,
        regime_config=regime_config,
        default_atr_multiplier=default_atr_multiplier,
        default_risk_reward=default_risk_reward,
    )
