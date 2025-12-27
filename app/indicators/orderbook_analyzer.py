"""
Order Book Analyzer - Order book imbalance and depth analysis.

Features:
- Bid/Ask imbalance calculation
- Buy/Sell pressure detection
- Depth analysis at different levels
- Large order detection

Used for identifying short-term directional bias.
"""

from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
import logging
import httpx
import asyncio

logger = logging.getLogger(__name__)


@dataclass
class OrderBookSnapshot:
    """Order book snapshot data."""
    symbol: str
    bid_volume: float
    ask_volume: float
    imbalance: float  # -1 to +1 (positive = bid heavy)
    bias: str  # BUY_PRESSURE, SELL_PRESSURE, NEUTRAL
    best_bid: float
    best_ask: float
    spread_pct: float


class OrderBookAnalyzer:
    """
    Order book imbalance analyzer.

    Calculates bid/ask imbalance to detect short-term pressure.
    """

    def __init__(
        self,
        depth: int = 20,
        imbalance_threshold: float = 0.15,
        feature_enabled: bool = True
    ):
        """
        Initialize OrderBookAnalyzer.

        Args:
            depth: Number of price levels to analyze
            imbalance_threshold: Threshold for directional bias
            feature_enabled: Enable/disable the feature
        """
        self.depth = depth
        self.imbalance_threshold = imbalance_threshold
        self.feature_enabled = feature_enabled
        self._cache: Dict[str, OrderBookSnapshot] = {}
        self._cache_ttl = 5  # seconds

        logger.info(
            "OrderBookAnalyzer initialized | Depth: %d | Threshold: %.2f | Enabled: %s",
            depth, imbalance_threshold, feature_enabled
        )

    def calculate_imbalance(
        self,
        bids: List[Tuple[float, float]],
        asks: List[Tuple[float, float]],
        depth: Optional[int] = None
    ) -> OrderBookSnapshot:
        """
        Calculate order book imbalance.

        Args:
            bids: List of (price, quantity) tuples
            asks: List of (price, quantity) tuples
            depth: Override default depth

        Returns:
            OrderBookSnapshot with imbalance data
        """
        use_depth = depth or self.depth

        # Sum volumes at top levels
        bid_volume = sum(float(b[1]) for b in bids[:use_depth])
        ask_volume = sum(float(a[1]) for a in asks[:use_depth])
        total = bid_volume + ask_volume

        # Imbalance: -1 (all asks) to +1 (all bids)
        if total > 0:
            imbalance = (bid_volume - ask_volume) / total
        else:
            imbalance = 0.0

        # Determine bias
        if imbalance > self.imbalance_threshold:
            bias = "BUY_PRESSURE"
        elif imbalance < -self.imbalance_threshold:
            bias = "SELL_PRESSURE"
        else:
            bias = "NEUTRAL"

        # Best bid/ask and spread
        best_bid = float(bids[0][0]) if bids else 0.0
        best_ask = float(asks[0][0]) if asks else 0.0
        spread_pct = (
            (best_ask - best_bid) / best_bid * 100
            if best_bid > 0 else 0.0
        )

        return OrderBookSnapshot(
            symbol="",
            bid_volume=round(bid_volume, 2),
            ask_volume=round(ask_volume, 2),
            imbalance=round(imbalance, 3),
            bias=bias,
            best_bid=best_bid,
            best_ask=best_ask,
            spread_pct=round(spread_pct, 4),
        )

    async def fetch_orderbook(
        self,
        symbol: str,
        client: Optional[httpx.AsyncClient] = None
    ) -> Optional[OrderBookSnapshot]:
        """
        Fetch order book from Binance API.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            client: Optional httpx client

        Returns:
            OrderBookSnapshot or None on error
        """
        if not self.feature_enabled:
            return None

        url = f"https://api.binance.com/api/v3/depth"
        params = {"symbol": symbol.upper(), "limit": self.depth}

        try:
            if client:
                response = await client.get(url, params=params, timeout=5.0)
            else:
                async with httpx.AsyncClient() as c:
                    response = await c.get(url, params=params, timeout=5.0)

            response.raise_for_status()
            data = response.json()

            bids = [(float(b[0]), float(b[1])) for b in data.get("bids", [])]
            asks = [(float(a[0]), float(a[1])) for a in data.get("asks", [])]

            snapshot = self.calculate_imbalance(bids, asks)
            snapshot.symbol = symbol

            # Cache it
            self._cache[symbol] = snapshot

            return snapshot

        except Exception as e:
            logger.warning(f"Failed to fetch orderbook for {symbol}: {e}")
            return self._cache.get(symbol)

    def calculate_from_raw(
        self,
        symbol: str,
        bids: List[List],
        asks: List[List]
    ) -> OrderBookSnapshot:
        """
        Calculate imbalance from raw order book data.

        Args:
            symbol: Trading pair
            bids: Raw bids [[price, qty], ...]
            asks: Raw asks [[price, qty], ...]

        Returns:
            OrderBookSnapshot
        """
        if not self.feature_enabled:
            return OrderBookSnapshot(
                symbol=symbol,
                bid_volume=0,
                ask_volume=0,
                imbalance=0,
                bias="DISABLED",
                best_bid=0,
                best_ask=0,
                spread_pct=0,
            )

        bids_tuples = [(float(b[0]), float(b[1])) for b in bids]
        asks_tuples = [(float(a[0]), float(a[1])) for a in asks]

        snapshot = self.calculate_imbalance(bids_tuples, asks_tuples)
        snapshot.symbol = symbol

        return snapshot

    def detect_large_orders(
        self,
        bids: List[Tuple[float, float]],
        asks: List[Tuple[float, float]],
        threshold_mult: float = 3.0
    ) -> Dict[str, List[Tuple[float, float]]]:
        """
        Detect large orders (walls) in the order book.

        Args:
            bids: List of (price, quantity) tuples
            asks: List of (price, quantity) tuples
            threshold_mult: Multiplier for average to consider "large"

        Returns:
            Dict with "bid_walls" and "ask_walls"
        """
        if not self.feature_enabled:
            return {"bid_walls": [], "ask_walls": []}

        # Calculate average order size
        all_orders = [b[1] for b in bids] + [a[1] for a in asks]
        if not all_orders:
            return {"bid_walls": [], "ask_walls": []}

        avg_size = sum(all_orders) / len(all_orders)
        threshold = avg_size * threshold_mult

        # Find walls
        bid_walls = [(p, q) for p, q in bids if q >= threshold]
        ask_walls = [(p, q) for p, q in asks if q >= threshold]

        return {
            "bid_walls": bid_walls[:5],  # Top 5
            "ask_walls": ask_walls[:5],
        }

    def get_prompt_section(
        self,
        snapshot: Optional[OrderBookSnapshot] = None,
        symbol: str = "",
        bids: Optional[List] = None,
        asks: Optional[List] = None
    ) -> str:
        """
        Generate order book section for prompt.

        Args:
            snapshot: Pre-calculated snapshot
            symbol: Symbol name (if calculating from raw data)
            bids: Raw bids (if no snapshot)
            asks: Raw asks (if no snapshot)

        Returns:
            Formatted string for prompt
        """
        if not self.feature_enabled:
            return ""

        # Calculate if no snapshot provided
        if snapshot is None and bids is not None and asks is not None:
            snapshot = self.calculate_from_raw(symbol, bids, asks)

        if snapshot is None:
            return ""

        # Bias emoji
        if snapshot.bias == "BUY_PRESSURE":
            bias_emoji = "🟢"
            bias_text = "Buyers dominant"
        elif snapshot.bias == "SELL_PRESSURE":
            bias_emoji = "🔴"
            bias_text = "Sellers dominant"
        else:
            bias_emoji = "⚪"
            bias_text = "Balanced"

        # Format imbalance as percentage
        imb_pct = snapshot.imbalance * 100
        imb_sign = "+" if imb_pct > 0 else ""

        lines = [
            "ORDER BOOK IMBALANCE:",
            f"  {bias_emoji} Imbalance: {imb_sign}{imb_pct:.1f}% ({snapshot.bias})",
            f"  Bid Volume: {snapshot.bid_volume:,.2f} | Ask Volume: {snapshot.ask_volume:,.2f}",
            f"  → {bias_text}",
        ]

        # Spread info if significant
        if snapshot.spread_pct > 0.05:
            lines.append(f"  ⚠️ Wide spread: {snapshot.spread_pct:.3f}% (low liquidity)")

        return "\n".join(lines)


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    analyzer = OrderBookAnalyzer(depth=10)

    # Sample order book data
    bids = [
        (95000, 1.5),
        (94990, 2.0),
        (94980, 0.8),
        (94970, 3.5),
        (94960, 1.2),
    ]
    asks = [
        (95010, 0.5),
        (95020, 0.8),
        (95030, 1.0),
        (95040, 0.3),
        (95050, 2.0),
    ]

    # Calculate imbalance
    snapshot = analyzer.calculate_imbalance(bids, asks)
    print(f"Imbalance: {snapshot.imbalance}")
    print(f"Bias: {snapshot.bias}")
    print(f"Bid Volume: {snapshot.bid_volume}")
    print(f"Ask Volume: {snapshot.ask_volume}")

    # Detect large orders
    walls = analyzer.detect_large_orders(bids, asks, threshold_mult=2.0)
    print(f"\nBid Walls: {walls['bid_walls']}")
    print(f"Ask Walls: {walls['ask_walls']}")

    # Get prompt section
    snapshot.symbol = "BTCUSDT"
    print("\n" + analyzer.get_prompt_section(snapshot))
