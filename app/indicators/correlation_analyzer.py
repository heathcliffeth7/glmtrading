"""
BTC Correlation Analyzer - Altcoin/BTC correlation analysis.

Features:
- Pearson correlation coefficient
- Rolling correlation window
- Correlation regime detection
- Beta calculation

Helps identify when altcoins diverge from BTC.
"""

from typing import List, Dict, Optional, Tuple
import logging
import math

logger = logging.getLogger(__name__)


class CorrelationAnalyzer:
    """
    BTC correlation analyzer for altcoins.

    Calculates how closely an altcoin follows BTC price movements.
    High correlation = follows BTC, Low correlation = independent movement.
    """

    def __init__(self, window: int = 20, feature_enabled: bool = True):
        """
        Initialize CorrelationAnalyzer.

        Args:
            window: Rolling window for correlation calculation
            feature_enabled: Enable/disable the feature
        """
        self.window = window
        self.feature_enabled = feature_enabled
        self._cache: Dict[str, float] = {}

        logger.info(
            "CorrelationAnalyzer initialized | Window: %d | Enabled: %s",
            window, feature_enabled
        )

    def calculate_returns(self, prices: List[float]) -> List[float]:
        """
        Calculate percentage returns from price series.

        Args:
            prices: List of prices

        Returns:
            List of percentage returns
        """
        if len(prices) < 2:
            return []

        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                ret = (prices[i] - prices[i - 1]) / prices[i - 1]
                returns.append(ret)
            else:
                returns.append(0.0)

        return returns

    def calculate_correlation(
        self,
        symbol_prices: List[float],
        btc_prices: List[float],
        window: Optional[int] = None
    ) -> Tuple[float, str]:
        """
        Calculate Pearson correlation between symbol and BTC.

        Args:
            symbol_prices: Altcoin prices
            btc_prices: BTC prices (same timeframe)
            window: Override default window

        Returns:
            Tuple[correlation, interpretation]
            correlation: -1 to +1
            interpretation: HIGH, MEDIUM, LOW, INVERSE
        """
        if not self.feature_enabled:
            return 0.0, "DISABLED"

        use_window = window or self.window

        # Need at least window + 1 prices to calculate window returns
        min_length = use_window + 1
        if len(symbol_prices) < min_length or len(btc_prices) < min_length:
            return 0.0, "INSUFFICIENT_DATA"

        # Calculate returns
        symbol_returns = self.calculate_returns(symbol_prices[-min_length:])
        btc_returns = self.calculate_returns(btc_prices[-min_length:])

        if len(symbol_returns) < use_window or len(btc_returns) < use_window:
            return 0.0, "INSUFFICIENT_DATA"

        # Use last 'window' returns
        x = symbol_returns[-use_window:]
        y = btc_returns[-use_window:]

        # Pearson correlation
        correlation = self._pearson_correlation(x, y)

        # Interpretation
        abs_corr = abs(correlation)
        if correlation < -0.5:
            interpretation = "INVERSE"
        elif abs_corr >= 0.8:
            interpretation = "HIGH"
        elif abs_corr >= 0.5:
            interpretation = "MEDIUM"
        else:
            interpretation = "LOW"

        return round(correlation, 3), interpretation

    def _pearson_correlation(self, x: List[float], y: List[float]) -> float:
        """
        Calculate Pearson correlation coefficient.

        Args:
            x: First series
            y: Second series

        Returns:
            Correlation coefficient (-1 to +1)
        """
        n = len(x)
        if n == 0 or len(y) != n:
            return 0.0

        # Means
        mean_x = sum(x) / n
        mean_y = sum(y) / n

        # Covariance and standard deviations
        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n
        std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x) / n)
        std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y) / n)

        if std_x == 0 or std_y == 0:
            return 0.0

        return cov / (std_x * std_y)

    def calculate_beta(
        self,
        symbol_prices: List[float],
        btc_prices: List[float],
        window: Optional[int] = None
    ) -> float:
        """
        Calculate beta (sensitivity to BTC moves).

        Beta > 1: More volatile than BTC
        Beta = 1: Same volatility as BTC
        Beta < 1: Less volatile than BTC
        Beta < 0: Moves opposite to BTC

        Args:
            symbol_prices: Altcoin prices
            btc_prices: BTC prices

        Returns:
            Beta coefficient
        """
        if not self.feature_enabled:
            return 1.0

        use_window = window or self.window
        min_length = use_window + 1

        if len(symbol_prices) < min_length or len(btc_prices) < min_length:
            return 1.0

        symbol_returns = self.calculate_returns(symbol_prices[-min_length:])
        btc_returns = self.calculate_returns(btc_prices[-min_length:])

        x = btc_returns[-use_window:]  # BTC is x (market)
        y = symbol_returns[-use_window:]  # Symbol is y (asset)

        n = len(x)
        if n == 0:
            return 1.0

        # Beta = Cov(symbol, btc) / Var(btc)
        mean_x = sum(x) / n
        mean_y = sum(y) / n

        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n
        var_x = sum((xi - mean_x) ** 2 for xi in x) / n

        if var_x == 0:
            return 1.0

        return round(cov / var_x, 3)

    def get_correlation_analysis(
        self,
        symbol: str,
        symbol_prices: List[float],
        btc_prices: List[float]
    ) -> Dict:
        """
        Full correlation analysis for prompt.

        Args:
            symbol: Symbol name (e.g., "ETHUSDT")
            symbol_prices: Altcoin prices
            btc_prices: BTC prices

        Returns:
            Dict with correlation, beta, interpretation
        """
        if not self.feature_enabled:
            return {
                "symbol": symbol,
                "correlation": 0.0,
                "interpretation": "DISABLED",
                "beta": 1.0,
                "follows_btc": True,
                "feature_enabled": False,
            }

        # Skip for BTC itself
        if symbol.upper().startswith("BTC"):
            return {
                "symbol": symbol,
                "correlation": 1.0,
                "interpretation": "SELF",
                "beta": 1.0,
                "follows_btc": True,
                "feature_enabled": True,
            }

        correlation, interpretation = self.calculate_correlation(
            symbol_prices, btc_prices
        )
        beta = self.calculate_beta(symbol_prices, btc_prices)

        # Does it strongly follow BTC?
        follows_btc = correlation > 0.7

        return {
            "symbol": symbol,
            "correlation": correlation,
            "interpretation": interpretation,
            "beta": beta,
            "follows_btc": follows_btc,
            "feature_enabled": True,
        }

    def get_prompt_section(
        self,
        symbol: str,
        symbol_prices: List[float],
        btc_prices: List[float]
    ) -> str:
        """
        Generate BTC correlation section for prompt.

        Args:
            symbol: Symbol name
            symbol_prices: Altcoin prices
            btc_prices: BTC prices

        Returns:
            Formatted string for prompt
        """
        if symbol.upper().startswith("BTC"):
            return ""  # No need for BTC itself

        if not self.feature_enabled or len(symbol_prices) < self.window + 1:
            return ""

        analysis = self.get_correlation_analysis(symbol, symbol_prices, btc_prices)

        lines = [
            f"BTC CORRELATION ({self.window}-bar):",
            f"  Correlation: {analysis['correlation']:.2f} ({analysis['interpretation']})",
            f"  Beta: {analysis['beta']:.2f}",
        ]

        # Add interpretation
        if analysis['interpretation'] == "HIGH":
            lines.append(f"  → {symbol} strongly follows BTC movements")
        elif analysis['interpretation'] == "INVERSE":
            lines.append(f"  → {symbol} moves OPPOSITE to BTC (hedge)")
        elif analysis['interpretation'] == "LOW":
            lines.append(f"  → {symbol} moves independently from BTC")

        if analysis['beta'] > 1.5:
            lines.append(f"  ⚠️ HIGH BETA: {symbol} amplifies BTC moves by {analysis['beta']:.1f}x")
        elif analysis['beta'] < 0.5:
            lines.append(f"  ℹ️ LOW BETA: {symbol} less volatile than BTC")

        return "\n".join(lines)


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    analyzer = CorrelationAnalyzer(window=20)

    # Sample data: ETH following BTC with some noise
    import random
    random.seed(42)

    btc_prices = [95000 + i * 100 + random.uniform(-200, 200) for i in range(50)]
    eth_prices = [3200 + i * 3.5 + random.uniform(-10, 10) for i in range(50)]

    # Get correlation analysis
    result = analyzer.get_correlation_analysis("ETHUSDT", eth_prices, btc_prices)
    print(f"Correlation: {result['correlation']}")
    print(f"Interpretation: {result['interpretation']}")
    print(f"Beta: {result['beta']}")

    # Get prompt section
    print("\n" + analyzer.get_prompt_section("ETHUSDT", eth_prices, btc_prices))
