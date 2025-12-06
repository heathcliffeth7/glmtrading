"""
Risk Market Analysis Module

Market condition analysis for risk management:
- Confidence bands
- Market condition assessment
- Bias reliability
- Sharpe ratio calculation
"""

from typing import Any, Dict, List, Optional

from app.utils.logging import get_logger


logger = get_logger(__name__)


class RiskMarketAnalyzer:
    """
    Analyzes market conditions for risk management decisions.
    """

    def analyze_market_condition(self, signals: List[Any]) -> Dict[str, float]:
        """
        Analyze current market conditions from agent signals.

        Returns a dict with:
        - trend_strength: 0-1 scale
        - volatility: 0-1 scale
        - confidence: overall confidence score
        """
        if not signals:
            return {
                "trend_strength": 0.5,
                "volatility": 0.5,
                "confidence": 0.0,
            }

        # Extract metadata from signals
        total_confidence = 0.0
        total_weight = 0.0
        volatility_sum = 0.0
        trend_sum = 0.0

        for signal in signals:
            weight = getattr(signal, 'confidence', 0.5)
            total_confidence += weight
            total_weight += 1.0

            meta = getattr(signal, 'metadata', {}) or {}

            # Extract volatility indicators
            vol_ratio = meta.get('vol_ratio', 0.5)
            atr_pct = meta.get('atr_pct', 0.5)
            volatility_sum += (vol_ratio + atr_pct) / 2

            # Extract trend indicators
            trend_score = 0.5
            if 'bias_score' in meta:
                bias = meta['bias_score']
                trend_score = abs(bias - 0.5) * 2  # Convert 0-1 to strength

            trend_sum += trend_score

        avg_confidence = total_confidence / max(total_weight, 1)
        avg_volatility = volatility_sum / max(total_weight, 1)
        avg_trend = trend_sum / max(total_weight, 1)

        return {
            "trend_strength": min(1.0, max(0.0, avg_trend)),
            "volatility": min(1.0, max(0.0, avg_volatility)),
            "confidence": min(1.0, max(0.0, avg_confidence)),
        }

    def confidence_band(self, signals: List[Any]) -> Dict[str, Any]:
        """
        Calculate confidence band from signals.

        Returns:
        - action: suggested action
        - amount: suggested position size
        - confidence: overall confidence
        """
        if not signals:
            return {
                "action": "HOLD",
                "amount": 0.0,
                "confidence": 0.0,
            }

        buy_conf = 0.0
        sell_conf = 0.0
        total = 0.0

        for signal in signals:
            direction = getattr(signal, 'direction', '').upper()
            conf = getattr(signal, 'confidence', 0.0)

            if direction in ['BUY', 'LONG']:
                buy_conf += conf
            elif direction in ['SELL', 'SHORT']:
                sell_conf += conf

            total += 1.0

        if total == 0:
            return {"action": "HOLD", "amount": 0.0, "confidence": 0.0}

        avg_buy = buy_conf / total
        avg_sell = sell_conf / total

        if avg_buy > avg_sell and avg_buy > 0.5:
            return {
                "action": "BUY",
                "amount": min(0.15, avg_buy * 0.2),
                "confidence": avg_buy,
            }
        elif avg_sell > avg_buy and avg_sell > 0.5:
            return {
                "action": "SELL",
                "amount": min(0.15, avg_sell * 0.2),
                "confidence": avg_sell,
            }

        return {
            "action": "HOLD",
            "amount": 0.0,
            "confidence": max(avg_buy, avg_sell),
        }

    def bias_confidence_band(self, bias_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate confidence band from bias scores.
        """
        if not bias_data:
            return {"action": "HOLD", "amount": 0.0, "confidence": 0.0}

        bias_score = bias_data.get("bias_score", 0.5)
        reliability = bias_data.get("reliability", 0.5)

        # Bias > 0.5 = bullish, < 0.5 = bearish
        if bias_score > 0.6:
            strength = (bias_score - 0.5) * 2
            return {
                "action": "BUY",
                "amount": min(0.15, strength * reliability * 0.2),
                "confidence": strength * reliability,
            }
        elif bias_score < 0.4:
            strength = (0.5 - bias_score) * 2
            return {
                "action": "SELL",
                "amount": min(0.15, strength * reliability * 0.2),
                "confidence": strength * reliability,
            }

        return {"action": "HOLD", "amount": 0.0, "confidence": 0.0}

    def get_bias_reliability_data(self, signals: List[Any]) -> Dict[str, Any]:
        """
        Extract bias reliability data from signals.
        """
        for signal in signals:
            meta = getattr(signal, 'metadata', {}) or {}
            if 'bias_score' in meta:
                return {
                    "bias_score": meta.get('bias_score', 0.5),
                    "reliability": meta.get('bias_reliability', 0.5),
                }
        return {}

    def calculate_sharpe_ratio(self, portfolio_metrics: Dict[str, Any]) -> float:
        """
        Calculate Sharpe ratio from portfolio metrics.

        Uses simplified formula:
        Sharpe = (Return - RiskFreeRate) / StdDev
        """
        if not portfolio_metrics:
            return 0.0

        pnl = portfolio_metrics.get("total_pnl", 0.0)
        equity = portfolio_metrics.get("equity", 10000.0)

        if equity <= 0:
            return 0.0

        # Simple return calculation
        return_pct = pnl / equity

        # Assume risk-free rate of 0 and volatility from metrics
        volatility = portfolio_metrics.get("volatility", 0.1)

        if volatility <= 0:
            return 0.0

        return return_pct / volatility
