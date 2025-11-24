"""
Signal Logging System

Logs all trading signals with comprehensive risk scores, decision metadata,
and outcomes for future analysis and optimization.

Each signal log includes:
- Action decision (BUY/SELL/HOLD)
- Risk scores (volatility, consensus, momentum, etc.)
- Market state (price, indicators, bias scores)
- Portfolio state (equity, position, PnL)
- Latency metrics
- Outcome tracking (for feedback loop)
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, Any, Optional

from app.utils.influx import write_measurement
from app.utils.logging import get_logger


logger = get_logger(__name__)


@dataclass
class SignalLog:
    """
    Comprehensive signal log with all decision factors
    
    This structure captures everything needed to:
    1. Understand why a decision was made
    2. Correlate decisions with outcomes
    3. Optimize risk parameters
    4. Train ML models
    """
    # Timestamp
    timestamp: datetime
    symbol: str
    interval: str
    
    # Decision
    action: str  # BUY, SELL, HOLD
    amount: float  # Position size
    leverage: float
    reasoning: str
    
    # Risk Scores (normalized -1 to 1 or 0 to 1)
    volatility_regime: float = 0.0
    trend_bias: float = 0.0
    momentum_bias: float = 0.0
    futures_bias: float = 0.0
    composite_bias: float = 0.0
    bias_confidence: float = 0.0
    
    # Consensus and Corrections
    min_consensus: float = 0.0
    momentum_correction: float = 0.0
    volatility_adjustment: float = 0.0
    
    # Market State
    current_price: float = 0.0
    rsi_14: float = 50.0
    macd: float = 0.0
    ema_20: float = 0.0
    ema_50: float = 0.0
    atr_14: float = 0.0
    
    # Futures Metrics
    long_short_ratio: float = 1.0
    open_interest: float = 0.0
    funding_rate: float = 0.0
    
    # Portfolio State
    equity: float = 0.0
    position: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    total_pnl: float = 0.0
    
    # Latency Metrics
    latency_total_ms: float = 0.0
    latency_signal_generation_ms: float = 0.0
    
    # Outcome Tracking (filled later)
    outcome_price_1h: Optional[float] = None
    outcome_price_4h: Optional[float] = None
    outcome_price_24h: Optional[float] = None
    outcome_pnl_1h: Optional[float] = None
    outcome_pnl_4h: Optional[float] = None
    outcome_pnl_24h: Optional[float] = None
    outcome_success: Optional[bool] = None
    
    # Metadata
    trace_id: Optional[str] = None
    glm_model: Optional[str] = None
    signal_source: str = "derivatives_agent"
    
    # Additional context
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Ensure numeric fields are floats"""
        try:
            self.equity = float(self.equity)
            self.position = float(self.position)
            self.unrealized_pnl = float(self.unrealized_pnl)
            self.realized_pnl = float(self.realized_pnl)
            self.total_pnl = float(self.total_pnl)
        except (ValueError, TypeError):
            pass

    def to_influx_fields(self) -> Dict[str, Any]:
        """
        Convert to InfluxDB fields
        
        Returns:
            Dict suitable for InfluxDB write_measurement
        """
        fields = {
            # Decision
            'action_numeric': float(self._action_to_numeric()),
            'amount': float(self.amount),
            'leverage': float(self.leverage),
            
            # Risk Scores
            'volatility_regime': float(self.volatility_regime),
            'trend_bias': float(self.trend_bias),
            'momentum_bias': float(self.momentum_bias),
            'futures_bias': float(self.futures_bias),
            'composite_bias': float(self.composite_bias),
            'bias_confidence': float(self.bias_confidence),
            
            # Consensus and Corrections
            'min_consensus': float(self.min_consensus),
            'momentum_correction': float(self.momentum_correction),
            'volatility_adjustment': float(self.volatility_adjustment),
            
            # Market State
            'current_price': float(self.current_price),
            'rsi_14': float(self.rsi_14),
            'macd': float(self.macd),
            'ema_20': float(self.ema_20),
            'ema_50': float(self.ema_50),
            'atr_14': float(self.atr_14),
            
            # Futures Metrics
            'long_short_ratio': float(self.long_short_ratio),
            'open_interest': float(self.open_interest),
            'funding_rate': float(self.funding_rate),
            
            # Portfolio State
            'equity': float(self.equity),
            'position': float(self.position),
            'unrealized_pnl': float(self.unrealized_pnl),
            'realized_pnl': float(self.realized_pnl),
            'total_pnl': float(self.total_pnl),
            
            # Latency
            'latency_total_ms': float(self.latency_total_ms),
            'latency_signal_generation_ms': float(self.latency_signal_generation_ms),
        }
        
        # Add outcome fields if available
        if self.outcome_price_1h is not None:
            fields['outcome_price_1h'] = float(self.outcome_price_1h)
        if self.outcome_price_4h is not None:
            fields['outcome_price_4h'] = float(self.outcome_price_4h)
        if self.outcome_price_24h is not None:
            fields['outcome_price_24h'] = float(self.outcome_price_24h)
        if self.outcome_pnl_1h is not None:
            fields['outcome_pnl_1h'] = float(self.outcome_pnl_1h)
        if self.outcome_pnl_4h is not None:
            fields['outcome_pnl_4h'] = float(self.outcome_pnl_4h)
        if self.outcome_pnl_24h is not None:
            fields['outcome_pnl_24h'] = float(self.outcome_pnl_24h)
        if self.outcome_success is not None:
            fields['outcome_success'] = 1.0 if self.outcome_success else 0.0
        
        return fields
    
    def _action_to_numeric(self) -> float:
        """Convert action to numeric for easier querying"""
        action_map = {
            'BUY': 1.0,
            'HOLD': 0.0,
            'SELL': -1.0,
        }
        return action_map.get(self.action, 0.0)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)
    
    def get_summary(self) -> str:
        """Get human-readable summary"""
        return (
            f"Signal: {self.action} {self.amount:.4f} @ {self.current_price:.2f} | "
            f"Volatility: {self.volatility_regime:.2f} | "
            f"Composite Bias: {self.composite_bias:+.2f} | "
            f"Confidence: {self.bias_confidence:.2f}"
        )


class SignalLogger:
    """
    Signal logging manager
    
    Usage:
        logger = SignalLogger()
        
        # Log a signal
        signal_log = SignalLog(
            timestamp=datetime.utcnow(),
            symbol="BTCUSDT",
            interval="30min",
            action="BUY",
            amount=0.05,
            leverage=5.0,
            reasoning="Strong bullish momentum",
            volatility_regime=0.65,
            composite_bias=0.45,
            # ... other fields
        )
        
        logger.log_signal(signal_log)
        
        # Later: Update with outcome
        logger.update_outcome(
            signal_id=signal_log.trace_id,
            outcome_price_1h=95500.0,
            outcome_pnl_1h=250.0,
            outcome_success=True,
        )
    """
    
    def __init__(self, measurement: str = "trading_signals"):
        """
        Initialize signal logger
        
        Args:
            measurement: InfluxDB measurement name
        """
        self.measurement = measurement
    
    def log_signal(self, signal_log: SignalLog) -> None:
        """
        Log a trading signal to InfluxDB
        
        Args:
            signal_log: SignalLog object with all decision factors
        """
        try:
            fields = signal_log.to_influx_fields()
            
            tags = {
                'symbol': signal_log.symbol,
                'interval': signal_log.interval,
                'action': signal_log.action,
                'signal_source': signal_log.signal_source,
            }
            
            if signal_log.trace_id:
                tags['trace_id'] = signal_log.trace_id
            
            write_measurement(
                measurement=self.measurement,
                tags=tags,
                fields=fields,
                timestamp=signal_log.timestamp,
            )
            
            logger.info(
                "📝 Signal logged: %s | %s",
                signal_log.action,
                signal_log.get_summary(),
            )
            
        except Exception as e:
            logger.error("Failed to log signal: %s", e, exc_info=True)
    
    def update_outcome(
        self,
        symbol: str,
        timestamp: datetime,
        outcome_price_1h: Optional[float] = None,
        outcome_price_4h: Optional[float] = None,
        outcome_price_24h: Optional[float] = None,
        outcome_pnl_1h: Optional[float] = None,
        outcome_pnl_4h: Optional[float] = None,
        outcome_pnl_24h: Optional[float] = None,
        outcome_success: Optional[bool] = None,
    ) -> None:
        """
        Update signal with outcome data
        
        This is called later (1h, 4h, 24h after signal) to track actual results.
        
        Args:
            symbol: Trading symbol
            timestamp: Original signal timestamp
            outcome_price_1h: Price 1 hour after signal
            outcome_price_4h: Price 4 hours after signal
            outcome_price_24h: Price 24 hours after signal
            outcome_pnl_1h: PnL 1 hour after signal
            outcome_pnl_4h: PnL 4 hours after signal
            outcome_pnl_24h: PnL 24 hours after signal
            outcome_success: Whether the signal was successful
        """
        try:
            fields = {}
            
            if outcome_price_1h is not None:
                fields['outcome_price_1h'] = outcome_price_1h
            if outcome_price_4h is not None:
                fields['outcome_price_4h'] = outcome_price_4h
            if outcome_price_24h is not None:
                fields['outcome_price_24h'] = outcome_price_24h
            if outcome_pnl_1h is not None:
                fields['outcome_pnl_1h'] = outcome_pnl_1h
            if outcome_pnl_4h is not None:
                fields['outcome_pnl_4h'] = outcome_pnl_4h
            if outcome_pnl_24h is not None:
                fields['outcome_pnl_24h'] = outcome_pnl_24h
            if outcome_success is not None:
                fields['outcome_success'] = 1.0 if outcome_success else 0.0
            
            if not fields:
                logger.warning("No outcome fields to update")
                return
            
            # Write outcome as a separate measurement for easier querying
            write_measurement(
                measurement=f"{self.measurement}_outcomes",
                tags={'symbol': symbol},
                fields=fields,
                timestamp=timestamp,
            )
            
            logger.info(
                "📊 Outcome updated for signal at %s: success=%s, pnl_1h=%.2f",
                timestamp.isoformat(),
                outcome_success,
                outcome_pnl_1h or 0.0,
            )
            
        except Exception as e:
            logger.error("Failed to update outcome: %s", e, exc_info=True)
    
    def create_signal_from_decision(
        self,
        decision: Any,  # RiskDecision
        signal: Any,  # AgentSignal
        portfolio_metrics: Optional[Dict] = None,
        latency_metrics: Optional[Dict] = None,
    ) -> SignalLog:
        """
        Create SignalLog from RiskDecision and AgentSignal
        
        Args:
            decision: RiskDecision object
            signal: AgentSignal object
            portfolio_metrics: Portfolio state dict
            latency_metrics: Latency metrics dict
        
        Returns:
            SignalLog object ready to be logged
        """
        # Extract metadata
        metadata = signal.metadata or {}
        feature_snapshot = metadata.get('feature_snapshot', {})
        bias_snapshot = metadata.get('bias_snapshot', {})
        
        # Portfolio state
        portfolio = portfolio_metrics or {}
        
        # Latency
        latency = latency_metrics or {}
        
        signal_log = SignalLog(
            timestamp=datetime.utcnow(),
            symbol=metadata.get('symbol', 'BTCUSDT'),
            interval=metadata.get('interval', '30min'),
            
            # Decision
            action=decision.action,
            amount=decision.amount,
            leverage=decision.leverage,
            reasoning=decision.reasoning,
            
            # Risk Scores
            volatility_regime=bias_snapshot.get('volatility_regime_score', 0.0),
            trend_bias=bias_snapshot.get('trend_bias_score', 0.0),
            momentum_bias=bias_snapshot.get('momentum_bias_score', 0.0),
            futures_bias=bias_snapshot.get('futures_bias_score', 0.0),
            composite_bias=bias_snapshot.get('composite_bias_score', 0.0),
            bias_confidence=bias_snapshot.get('bias_confidence_score', 0.0),
            
            # Market State
            current_price=feature_snapshot.get('close', 0.0),
            rsi_14=feature_snapshot.get('rsi_14', 50.0),
            macd=feature_snapshot.get('macd', 0.0),
            ema_20=feature_snapshot.get('ema_20', 0.0),
            ema_50=feature_snapshot.get('ema_50', 0.0),
            atr_14=feature_snapshot.get('atr_14', 0.0),
            
            # Futures Metrics
            long_short_ratio=feature_snapshot.get('long_short_ratio', 1.0),
            open_interest=feature_snapshot.get('open_interest', 0.0),
            funding_rate=feature_snapshot.get('funding_rate', 0.0),
            
            # Portfolio State
            equity=portfolio.get('equity', 0.0),
            position=portfolio.get('position', 0.0),
            unrealized_pnl=portfolio.get('unrealized_pnl', 0.0),
            realized_pnl=portfolio.get('realized_pnl', 0.0),
            total_pnl=portfolio.get('total_pnl', 0.0),
            
            # Latency
            latency_total_ms=latency.get('total_e2e', 0.0) * 1000,
            latency_signal_generation_ms=metadata.get('signal_generation_latency_ms', 0.0),
            
            # Metadata
            trace_id=metadata.get('trace_id'),
            signal_source=metadata.get('signal_source', 'derivatives_agent'),
            metadata=metadata,
        )
        
        return signal_log


# Global singleton instance
_global_logger: Optional[SignalLogger] = None


def get_signal_logger() -> SignalLogger:
    """Get or create global signal logger instance"""
    global _global_logger
    if _global_logger is None:
        _global_logger = SignalLogger()
    return _global_logger
