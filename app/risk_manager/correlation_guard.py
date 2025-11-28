"""
Correlation Guard - Prevent correlated exposure in multi-asset trading.

Features:
- Real-time correlation calculation between assets
- Exposure limit per correlation group
- Dynamic correlation tracking
- Position weighting based on correlation

Supports both Scalp (15-30m) and Swing (4H) trading modes.
"""

from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict
import logging
import math

logger = logging.getLogger(__name__)


@dataclass
class CorrelationResult:
    """Result from correlation analysis"""
    asset1: str
    asset2: str
    correlation: float
    sample_size: int
    is_significant: bool
    timestamp: datetime


@dataclass
class ExposureCheck:
    """Result from exposure check"""
    can_add_position: bool
    current_exposure: float
    max_exposure: float
    correlated_positions: List[str]
    warnings: List[str]
    recommended_size_multiplier: float
    feature_enabled: bool = True


class CorrelationGuard:
    """
    Guard against over-exposure to correlated assets.

    Features:
    - Tracks correlation between crypto assets
    - Limits exposure to highly correlated pairs
    - Adjusts position sizing based on correlation

    Known crypto correlations:
    - BTC/ETH: High (0.85+)
    - BTC/SOL: Medium-High (0.70-0.85)
    - BTC/ALTs: Medium (0.50-0.70)

    Mode-aware configuration for Scalp vs Swing trading.
    """

    # Pre-defined correlation groups (approximate values)
    DEFAULT_CORRELATIONS = {
        ("BTCUSDT", "ETHUSDT"): 0.85,
        ("BTCUSDT", "SOLUSDT"): 0.75,
        ("ETHUSDT", "SOLUSDT"): 0.80,
        ("BTCUSDT", "BNBUSDT"): 0.70,
        ("ETHUSDT", "BNBUSDT"): 0.75,
    }

    # Correlation groups for quick lookup
    CORRELATION_GROUPS = {
        "major": ["BTCUSDT", "ETHUSDT"],
        "layer1": ["SOLUSDT", "AVAXUSDT", "DOTUSDT", "ADAUSDT"],
        "defi": ["LINKUSDT", "UNIUSDT", "AAVEUSDT"],
        "meme": ["DOGEUSDT", "SHIBUSDT", "PEPEUSDT"],
    }

    # Mode-specific configurations
    MODE_CONFIGS = {
        "scalp": {
            "max_correlation_exposure": 0.6,  # Max 60% exposure to correlated assets
            "high_correlation_threshold": 0.7,  # Above this = highly correlated
            "size_reduction_factor": 0.5,  # Reduce size by 50% for correlated
            "max_same_direction": 2,  # Max 2 positions in same direction
        },
        "swing": {
            "max_correlation_exposure": 0.7,  # More lenient for swing
            "high_correlation_threshold": 0.8,
            "size_reduction_factor": 0.6,
            "max_same_direction": 3,
        }
    }

    def __init__(
        self,
        mode: str = "scalp",
        max_correlation_exposure: Optional[float] = None,
        feature_enabled: bool = True,
    ):
        """
        Initialize CorrelationGuard.

        Args:
            mode: Trading mode - "scalp" or "swing"
            max_correlation_exposure: Override max correlation exposure
            feature_enabled: Enable/disable the feature
        """
        self.mode = mode
        self.feature_enabled = feature_enabled
        self.config = self.MODE_CONFIGS.get(mode, self.MODE_CONFIGS["scalp"])

        if max_correlation_exposure is not None:
            self.config["max_correlation_exposure"] = max_correlation_exposure

        # Active positions tracking
        self.active_positions: Dict[str, Dict] = {}

        # Dynamic correlation cache
        self.correlation_cache: Dict[Tuple[str, str], CorrelationResult] = {}

        # Price history for correlation calculation
        self.price_history: Dict[str, List[Tuple[datetime, float]]] = defaultdict(list)

        logger.info(
            "CorrelationGuard initialized | Mode: %s | Max Exposure: %.0f%% | Enabled: %s",
            mode, self.config["max_correlation_exposure"] * 100, feature_enabled
        )

    def register_position(
        self,
        symbol: str,
        side: str,  # "LONG" or "SHORT"
        size_usd: float,
        entry_price: float,
    ) -> Dict:
        """
        Register a new position.

        Args:
            symbol: Trading pair
            side: Position side
            size_usd: Position size in USD
            entry_price: Entry price

        Returns:
            Registration result
        """
        if not self.feature_enabled:
            return {"feature_enabled": False}

        self.active_positions[symbol] = {
            "side": side,
            "size_usd": size_usd,
            "entry_price": entry_price,
            "registered_at": datetime.utcnow(),
        }

        logger.info(
            "Position registered: %s %s $%.2f",
            symbol, side, size_usd
        )

        return {
            "symbol": symbol,
            "side": side,
            "size_usd": size_usd,
            "feature_enabled": True,
        }

    def remove_position(self, symbol: str) -> bool:
        """Remove position from tracking"""
        if symbol in self.active_positions:
            del self.active_positions[symbol]
            logger.info("Position removed from correlation tracking: %s", symbol)
            return True
        return False

    def check_exposure(
        self,
        symbol: str,
        side: str,
        proposed_size_usd: float,
        total_equity: float,
    ) -> ExposureCheck:
        """
        Check if a new position would exceed correlation exposure limits.

        Args:
            symbol: Proposed trading pair
            side: Proposed position side
            proposed_size_usd: Proposed position size in USD
            total_equity: Total account equity

        Returns:
            ExposureCheck with decision
        """
        if not self.feature_enabled:
            return ExposureCheck(
                can_add_position=True,
                current_exposure=0,
                max_exposure=1.0,
                correlated_positions=[],
                warnings=["Correlation guard disabled"],
                recommended_size_multiplier=1.0,
                feature_enabled=False,
            )

        warnings = []
        correlated_positions = []
        total_correlated_exposure = 0.0

        # Check correlation with existing positions
        for existing_symbol, pos_data in self.active_positions.items():
            correlation = self._get_correlation(symbol, existing_symbol)

            if correlation >= self.config["high_correlation_threshold"]:
                correlated_positions.append(existing_symbol)
                exposure = pos_data["size_usd"] / total_equity
                total_correlated_exposure += exposure * correlation

                # Same direction check
                if pos_data["side"] == side:
                    warnings.append(
                        f"Same direction as {existing_symbol} (corr: {correlation:.2f})"
                    )

        # Add proposed position to exposure calculation
        proposed_exposure = proposed_size_usd / total_equity
        total_with_proposed = total_correlated_exposure + proposed_exposure

        # Check exposure limit
        max_exposure = self.config["max_correlation_exposure"]
        can_add = total_with_proposed <= max_exposure

        # Calculate recommended size multiplier
        if total_correlated_exposure >= max_exposure:
            recommended_multiplier = 0.0
            warnings.append("Correlation exposure at maximum")
        elif total_with_proposed > max_exposure:
            # Reduce size to fit within limit
            available = max_exposure - total_correlated_exposure
            recommended_multiplier = available / proposed_exposure
            recommended_multiplier = max(0.25, min(1.0, recommended_multiplier))
            warnings.append(f"Reduce size to {recommended_multiplier:.0%} to fit exposure limit")
        elif correlated_positions:
            # Apply default reduction for correlated positions
            recommended_multiplier = self.config["size_reduction_factor"]
        else:
            recommended_multiplier = 1.0

        # Count same-direction positions
        same_direction_count = sum(
            1 for p in self.active_positions.values()
            if p["side"] == side
        )

        if same_direction_count >= self.config["max_same_direction"]:
            can_add = False
            warnings.append(
                f"Max same-direction positions ({self.config['max_same_direction']}) reached"
            )

        return ExposureCheck(
            can_add_position=can_add,
            current_exposure=total_correlated_exposure,
            max_exposure=max_exposure,
            correlated_positions=correlated_positions,
            warnings=warnings,
            recommended_size_multiplier=recommended_multiplier,
            feature_enabled=True,
        )

    def _get_correlation(self, symbol1: str, symbol2: str) -> float:
        """
        Get correlation between two symbols.

        First checks cache, then calculated correlation, then defaults.
        """
        if symbol1 == symbol2:
            return 1.0

        # Normalize order for cache lookup
        pair = tuple(sorted([symbol1, symbol2]))

        # Check cache
        if pair in self.correlation_cache:
            cached = self.correlation_cache[pair]
            # Cache valid for 1 hour
            if datetime.utcnow() - cached.timestamp < timedelta(hours=1):
                return cached.correlation

        # Check default correlations
        if pair in self.DEFAULT_CORRELATIONS:
            return self.DEFAULT_CORRELATIONS[pair]

        # Check if in same group
        for group_name, symbols in self.CORRELATION_GROUPS.items():
            if symbol1 in symbols and symbol2 in symbols:
                # Same group = assumed correlation
                return 0.65

        # Try to calculate from price history
        calculated = self._calculate_correlation(symbol1, symbol2)
        if calculated is not None:
            return calculated

        # Default: assume moderate correlation for crypto
        return 0.5

    def _calculate_correlation(
        self,
        symbol1: str,
        symbol2: str,
        window: int = 24,
    ) -> Optional[float]:
        """
        Calculate correlation from price history.

        Args:
            symbol1: First symbol
            symbol2: Second symbol
            window: Number of data points to use

        Returns:
            Correlation coefficient or None if insufficient data
        """
        if symbol1 not in self.price_history or symbol2 not in self.price_history:
            return None

        prices1 = self.price_history[symbol1][-window:]
        prices2 = self.price_history[symbol2][-window:]

        if len(prices1) < window // 2 or len(prices2) < window // 2:
            return None

        # Align timestamps
        aligned = self._align_prices(prices1, prices2)
        if len(aligned) < window // 2:
            return None

        returns1 = []
        returns2 = []

        for i in range(1, len(aligned)):
            r1 = (aligned[i][0] - aligned[i-1][0]) / aligned[i-1][0]
            r2 = (aligned[i][1] - aligned[i-1][1]) / aligned[i-1][1]
            returns1.append(r1)
            returns2.append(r2)

        if len(returns1) < 5:
            return None

        # Calculate Pearson correlation
        n = len(returns1)
        mean1 = sum(returns1) / n
        mean2 = sum(returns2) / n

        cov = sum((r1 - mean1) * (r2 - mean2) for r1, r2 in zip(returns1, returns2)) / n
        std1 = math.sqrt(sum((r - mean1) ** 2 for r in returns1) / n)
        std2 = math.sqrt(sum((r - mean2) ** 2 for r in returns2) / n)

        if std1 == 0 or std2 == 0:
            return None

        correlation = cov / (std1 * std2)

        # Cache result
        pair = tuple(sorted([symbol1, symbol2]))
        self.correlation_cache[pair] = CorrelationResult(
            asset1=symbol1,
            asset2=symbol2,
            correlation=correlation,
            sample_size=n,
            is_significant=n >= 20,
            timestamp=datetime.utcnow(),
        )

        return correlation

    def _align_prices(
        self,
        prices1: List[Tuple[datetime, float]],
        prices2: List[Tuple[datetime, float]],
        tolerance_minutes: int = 5,
    ) -> List[Tuple[float, float]]:
        """Align two price series by timestamp"""
        aligned = []

        for ts1, p1 in prices1:
            for ts2, p2 in prices2:
                if abs((ts1 - ts2).total_seconds()) <= tolerance_minutes * 60:
                    aligned.append((p1, p2))
                    break

        return aligned

    def update_price(self, symbol: str, price: float, timestamp: Optional[datetime] = None):
        """
        Update price history for correlation calculation.

        Args:
            symbol: Trading pair
            price: Current price
            timestamp: Optional timestamp (defaults to now)
        """
        if not self.feature_enabled:
            return

        if timestamp is None:
            timestamp = datetime.utcnow()

        self.price_history[symbol].append((timestamp, price))

        # Keep last 100 prices
        if len(self.price_history[symbol]) > 100:
            self.price_history[symbol] = self.price_history[symbol][-100:]

    def get_correlation_matrix(self, symbols: Optional[List[str]] = None) -> Dict:
        """
        Get correlation matrix for symbols.

        Args:
            symbols: List of symbols (defaults to active positions)

        Returns:
            Dict with correlation values
        """
        if not self.feature_enabled:
            return {"feature_enabled": False}

        if symbols is None:
            symbols = list(self.active_positions.keys())

        if len(symbols) < 2:
            return {"correlations": {}, "symbols": symbols}

        matrix = {}
        for i, s1 in enumerate(symbols):
            for s2 in symbols[i+1:]:
                pair = f"{s1}_{s2}"
                matrix[pair] = self._get_correlation(s1, s2)

        return {
            "correlations": matrix,
            "symbols": symbols,
            "feature_enabled": True,
        }

    def get_portfolio_risk(self, total_equity: float) -> Dict:
        """
        Calculate portfolio risk considering correlations.

        Args:
            total_equity: Total account equity

        Returns:
            Portfolio risk metrics
        """
        if not self.feature_enabled:
            return {"feature_enabled": False}

        if not self.active_positions:
            return {
                "total_exposure": 0,
                "correlated_risk": 0,
                "diversification_score": 1.0,
                "positions": 0,
                "feature_enabled": True,
            }

        total_exposure = sum(
            pos["size_usd"] / total_equity
            for pos in self.active_positions.values()
        )

        # Calculate correlation-adjusted risk
        symbols = list(self.active_positions.keys())
        corr_risk = 0.0

        for i, s1 in enumerate(symbols):
            pos1 = self.active_positions[s1]
            w1 = pos1["size_usd"] / total_equity

            for s2 in symbols[i+1:]:
                pos2 = self.active_positions[s2]
                w2 = pos2["size_usd"] / total_equity
                corr = self._get_correlation(s1, s2)

                # Same direction amplifies risk
                if pos1["side"] == pos2["side"]:
                    corr_risk += w1 * w2 * corr
                else:
                    corr_risk -= w1 * w2 * corr * 0.5  # Partial offset

        # Diversification score (1 = perfectly diversified, 0 = fully correlated)
        if total_exposure > 0:
            max_risk = total_exposure ** 2
            div_score = max(0, 1 - (corr_risk / max_risk if max_risk > 0 else 0))
        else:
            div_score = 1.0

        return {
            "total_exposure": total_exposure,
            "correlated_risk": corr_risk,
            "diversification_score": div_score,
            "positions": len(self.active_positions),
            "feature_enabled": True,
        }

    def get_prompt_section(self, total_equity: float) -> str:
        """Generate prompt section for GLM"""
        if not self.feature_enabled:
            return ""

        if not self.active_positions:
            return ""

        risk = self.get_portfolio_risk(total_equity)

        lines = [
            "",
            "=" * 60,
            "CORRELATION ANALYSIS",
            "=" * 60,
            "",
            f"Active Positions: {risk['positions']}",
            f"Total Exposure: {risk['total_exposure']:.1%}",
            f"Correlation Risk: {risk['correlated_risk']:.3f}",
            f"Diversification Score: {risk['diversification_score']:.2f}",
            "",
            "Position Details:",
        ]

        for symbol, pos in self.active_positions.items():
            exposure = pos["size_usd"] / total_equity
            lines.append(f"  - {symbol}: {pos['side']} ${pos['size_usd']:.0f} ({exposure:.1%})")

        # Add correlation warnings
        symbols = list(self.active_positions.keys())
        high_corr_pairs = []

        for i, s1 in enumerate(symbols):
            for s2 in symbols[i+1:]:
                corr = self._get_correlation(s1, s2)
                if corr >= self.config["high_correlation_threshold"]:
                    high_corr_pairs.append((s1, s2, corr))

        if high_corr_pairs:
            lines.append("")
            lines.append("High Correlation Warnings:")
            for s1, s2, corr in high_corr_pairs:
                lines.append(f"  - {s1} <-> {s2}: {corr:.2f}")

        return "\n".join(lines)

    def get_trade_filter(
        self,
        symbol: str,
        side: str,
        proposed_size_usd: float,
        total_equity: float,
    ) -> Dict:
        """
        Get trade filter decision for integration.

        Args:
            symbol: Proposed symbol
            side: Proposed side
            proposed_size_usd: Proposed size
            total_equity: Account equity

        Returns:
            Dict with filter decision
        """
        if not self.feature_enabled:
            return {
                "allow_trade": True,
                "size_multiplier": 1.0,
                "reason": "Correlation guard disabled",
                "feature_enabled": False,
            }

        check = self.check_exposure(symbol, side, proposed_size_usd, total_equity)

        return {
            "allow_trade": check.can_add_position,
            "size_multiplier": check.recommended_size_multiplier,
            "current_exposure": check.current_exposure,
            "correlated_with": check.correlated_positions,
            "warnings": check.warnings,
            "feature_enabled": True,
        }

    def get_summary(self) -> Dict:
        """Get summary of correlation guard state"""
        return {
            "mode": self.mode,
            "feature_enabled": self.feature_enabled,
            "max_correlation_exposure": self.config["max_correlation_exposure"],
            "high_correlation_threshold": self.config["high_correlation_threshold"],
            "active_positions": len(self.active_positions),
            "cached_correlations": len(self.correlation_cache),
        }


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test scalp mode
    guard = CorrelationGuard(mode="scalp")

    # Register some positions
    guard.register_position("BTCUSDT", "LONG", 5000, 100000)
    guard.register_position("ETHUSDT", "LONG", 3000, 3500)

    print("Portfolio Risk:")
    risk = guard.get_portfolio_risk(total_equity=20000)
    print(f"  Total Exposure: {risk['total_exposure']:.1%}")
    print(f"  Correlation Risk: {risk['correlated_risk']:.3f}")
    print(f"  Diversification: {risk['diversification_score']:.2f}")

    # Check exposure for new position
    print("\n--- Checking SOL position ---")
    check = guard.check_exposure(
        symbol="SOLUSDT",
        side="LONG",
        proposed_size_usd=2000,
        total_equity=20000,
    )
    print(f"  Can Add: {check.can_add_position}")
    print(f"  Current Exposure: {check.current_exposure:.1%}")
    print(f"  Correlated With: {check.correlated_positions}")
    print(f"  Size Multiplier: {check.recommended_size_multiplier:.2f}")

    if check.warnings:
        print("  Warnings:")
        for w in check.warnings:
            print(f"    - {w}")

    # Get correlation matrix
    print("\n--- Correlation Matrix ---")
    matrix = guard.get_correlation_matrix(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    for pair, corr in matrix["correlations"].items():
        print(f"  {pair}: {corr:.2f}")

    print("\n--- Prompt Section ---")
    print(guard.get_prompt_section(20000))
