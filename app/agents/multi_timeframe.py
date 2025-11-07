"""
Multi-Timeframe Agent - Analyzes multiple timeframes for comprehensive signals
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

from app.agents.base import Agent, AgentSignal
from app.agents.multi_signal import MultiSignalAgent
from app.utils.influx import detect_htf_support_resistance
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TimeframeSignal:
    """Signal from a specific timeframe"""
    timeframe: str
    direction: str
    confidence: float
    reasoning: str


class MultiTimeframeAgent(Agent):
    """
    Combines signals from multiple timeframes
    
    Strategy:
    - 1m: Short-term noise, low weight (10%)
    - 5m: Primary timeframe, high weight (40%)
    - 15m: Medium-term trend, medium weight (30%)
    - 1h: Long-term context, low weight (20%)
    
    Alignment bonus: If all timeframes agree, boost confidence
    """
    
    def __init__(self, symbol: str = "BTCUSDT", enable_htf_filter: bool = True) -> None:
        self._symbol = symbol
        self._enable_htf_filter = enable_htf_filter
        
        # Create agents for each timeframe
        self._agents: Dict[str, MultiSignalAgent] = {
            "1min": MultiSignalAgent(symbol, "1min", adaptive=True),
            "5min": MultiSignalAgent(symbol, "5min", adaptive=True),
            "15min": MultiSignalAgent(symbol, "15min", adaptive=True),
            "1h": MultiSignalAgent(symbol, "1h", adaptive=True),
        }
        
        # Timeframe weights
        self._weights = {
            "1min": 0.10,
            "5min": 0.40,
            "15min": 0.30,
            "1h": 0.20,
        }
    
    def generate_signal(self) -> AgentSignal:
        """HTF filtreli multi-timeframe sinyali üretir"""
        
        # HTF analizini yap
        htf_analysis = None
        if self._enable_htf_filter:
            htf_analysis = detect_htf_support_resistance(self._symbol)
        
        # Tüm timeframe'lerden sinyalleri topla
        timeframe_signals: List[TimeframeSignal] = []
        
        for tf, agent in self._agents.items():
            try:
                signal = agent.generate_signal()
                timeframe_signals.append(TimeframeSignal(
                    timeframe=tf,
                    direction=signal.direction,
                    confidence=signal.confidence,
                    reasoning=signal.reasoning,
                ))
            except Exception as exc:
                logger.warning("Failed to get %s signal: %s", tf, exc)
        
        if not timeframe_signals:
            return self._fallback_signal()
        
        # HTF Filtresini Uygula
        if htf_analysis and htf_analysis.get("status") == "success":
            in_support = htf_analysis.get("in_support_zone", False)
            in_resistance = htf_analysis.get("in_resistance_zone", False)
            
            # Destek bölgesindeyse SELL sinyallerini engelle
            if in_support:
                timeframe_signals = [s for s in timeframe_signals if s.direction != "SELL"]
                logger.info("🛡️ HTF Filtre: SELL sinyalleri engellendi (destek bölgesi)")
            
            # Direnç bölgesindeyse BUY sinyallerini engelle  
            if in_resistance:
                timeframe_signals = [s for s in timeframe_signals if s.direction != "BUY"]
                logger.info("🛡️ HTF Filtre: BUY sinyalleri engellendi (direnç bölgesi)")
        
        # Calculate weighted score
        buy_score = 0.0
        sell_score = 0.0
        
        for tf_signal in timeframe_signals:
            weight = self._weights.get(tf_signal.timeframe, 0.0)
            weighted_confidence = tf_signal.confidence * weight
            
            if tf_signal.direction == "BUY":
                buy_score += weighted_confidence
            elif tf_signal.direction == "SELL":
                sell_score += weighted_confidence
        
        # Check for alignment (all pointing same direction)
        directions = [s.direction for s in timeframe_signals]
        alignment_bonus = 0.0
        
        if all(d == "BUY" for d in directions):
            alignment_bonus = 0.15
        elif all(d == "SELL" for d in directions):
            alignment_bonus = 0.15
        
        # Determine final direction
        if buy_score > sell_score + 0.1:
            direction = "BUY"
            confidence = min(1.0, buy_score + alignment_bonus)
        elif sell_score > buy_score + 0.1:
            direction = "SELL"
            confidence = min(1.0, sell_score + alignment_bonus)
        else:
            direction = "HOLD"
            confidence = 0.0
        
        # Build reasoning
        reasoning = self._build_reasoning(timeframe_signals, alignment_bonus > 0)
        
        return AgentSignal(
            direction=direction,
            confidence=confidence,
            reasoning=reasoning,
            timestamp=datetime.utcnow().isoformat(),
        )
    
    def _build_reasoning(self, signals: List[TimeframeSignal], aligned: bool) -> str:
        """Build comprehensive reasoning from all timeframes"""
        
        parts = []
        
        if aligned:
            parts.append("🎯 Tüm timeframe'ler uyumlu!")
        
        for signal in signals:
            if signal.confidence > 0.3:  # Only show significant signals
                icon = "📈" if signal.direction == "BUY" else "📉" if signal.direction == "SELL" else "⏸️"
                parts.append(
                    f"{icon} {signal.timeframe}: {signal.direction} ({signal.confidence:.0%})"
                )
        
        return " | ".join(parts) if parts else "Zayıf sinyaller"
    
    def _fallback_signal(self) -> AgentSignal:
        """Fallback when no timeframes available"""
        return AgentSignal(
            direction="HOLD",
            confidence=0.0,
            reasoning="Timeframe verileri alınamadı",
            timestamp=datetime.utcnow().isoformat(),
        )
