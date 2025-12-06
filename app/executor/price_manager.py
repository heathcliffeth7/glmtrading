"""
Price Manager Module

Handles price resolution with multiple fallback sources.
"""

import time
from typing import Optional

import httpx

from app.utils.logging import get_logger
from app.utils.price_cache import price_cache


logger = get_logger(__name__)


class PriceManager:
    """
    Manages price resolution with comprehensive fallback chain.

    Fallback chain:
    1. WebSocket cache (fresh: < 10s)
    2. REST API (Binance with retry)
    3. InfluxDB (may be delayed)
    4. Stale cache (emergency: < 60s)
    """

    def __init__(
        self,
        symbol: str,
        min_price_sanity: float = 0.01,
        max_price_sanity: float = 1_000_000.0,
    ):
        """
        Initialize the price manager.

        Args:
            symbol: Trading symbol
            min_price_sanity: Minimum acceptable price
            max_price_sanity: Maximum acceptable price
        """
        self._symbol = symbol
        self._min_price_sanity = min_price_sanity
        self._max_price_sanity = max_price_sanity

    def resolve_price(self) -> float:
        """
        Resolve current price with retry mechanism and multiple fallbacks.

        Strategy:
        1. Try Binance REST API (3 attempts with exponential backoff)
        2. Fallback to InfluxDB if Binance fails
        3. Return 0.0 if all sources fail

        Returns:
            Current price, or 0.0 if unavailable
        """
        max_attempts = 3
        base_timeout = 5.0

        for attempt in range(1, max_attempts + 1):
            timeout = base_timeout * attempt  # 5s, 10s, 15s
            try:
                start_time = time.time()
                response = httpx.get(
                    "https://api.binance.com/api/v3/ticker/price",
                    params={"symbol": self._symbol},
                    timeout=timeout,
                )
                response.raise_for_status()
                data = response.json()
                price = float(data["price"])
                elapsed = time.time() - start_time

                if price > 0:
                    logger.info(
                        "Binance API price: %.2f (attempt %d/%d, %.2fs)",
                        price,
                        attempt,
                        max_attempts,
                        elapsed,
                    )
                    return price

            except httpx.ConnectTimeout as exc:
                logger.warning(
                    "Binance API connection timeout (attempt %d/%d, timeout=%.1fs): %s",
                    attempt,
                    max_attempts,
                    timeout,
                    exc,
                )
            except httpx.ReadTimeout as exc:
                logger.warning(
                    "Binance API read timeout (attempt %d/%d, timeout=%.1fs): %s",
                    attempt,
                    max_attempts,
                    timeout,
                    exc,
                )
            except httpx.RequestError as exc:
                logger.warning(
                    "Binance API request error (attempt %d/%d): %s",
                    attempt,
                    max_attempts,
                    exc,
                )
            except Exception as exc:
                logger.warning(
                    "Binance API unexpected error (attempt %d/%d): %s",
                    attempt,
                    max_attempts,
                    exc,
                )

            # Exponential backoff between retries (except on last attempt)
            if attempt < max_attempts:
                backoff = 2 ** (attempt - 1)  # 1s, 2s
                logger.debug("Retrying in %.1fs...", backoff)
                time.sleep(backoff)

        logger.error("Binance API failed after %d attempts", max_attempts)

        # Fallback: InfluxDB (may be delayed)
        try:
            from app.utils.influx import query_latest_snapshot

            snapshot = query_latest_snapshot("features_1m", self._symbol, "1m")
            if snapshot and "close" in snapshot:
                price = float(snapshot.get("close", 0.0))
                if price > 0:
                    logger.warning("Using FALLBACK price from InfluxDB: %.2f", price)
                    return price
        except Exception as exc:
            logger.error("InfluxDB fallback also failed: %s", exc)

        logger.error("Could not resolve valid price from ANY source")
        return 0.0

    def get_current_price_validated(
        self, max_age_seconds: int = 10
    ) -> Optional[float]:
        """
        Get current price with comprehensive fallback chain.

        Args:
            max_age_seconds: Maximum acceptable age for fresh data

        Returns:
            Price if available, None if all sources fail
        """
        logger.info(
            "Fetching price for %s (max_age=%ds)...", self._symbol, max_age_seconds
        )

        # SOURCE 1: Try fresh cache first (WebSocket data)
        cached_price = price_cache.get(self._symbol, max_age_seconds=max_age_seconds)
        if cached_price is not None:
            logger.info("Price from WebSocket cache: %.2f (fresh)", cached_price)
            return cached_price

        # Get cache age for diagnostics
        cache_age = price_cache.get_age_seconds(self._symbol)
        logger.warning(
            "WebSocket cache miss for %s (age: %.1fs)", self._symbol, cache_age
        )

        # SOURCE 2: REST API as fallback (with built-in retry)
        logger.info("Trying REST API fallback...")
        rest_price = self.resolve_price()

        if rest_price > 0:
            # Check for significant price difference that needs cache reset
            cached_price_stale = price_cache.get(self._symbol)
            if cached_price_stale and cached_price_stale != rest_price:
                diff_pct = abs(rest_price - cached_price_stale) / cached_price_stale
                if diff_pct > 0.10:  # 10% difference triggers reset
                    logger.warning(
                        "SIGNIFICANT PRICE DIFFERENCE %s: REST=%.2f vs Cache=%.2f (%.1f%%) - TRIGGERING CACHE RESET",
                        self._symbol,
                        rest_price,
                        cached_price_stale,
                        diff_pct * 100,
                    )
                    price_cache.update_rest_price(self._symbol, rest_price)
                else:
                    price_cache.set(self._symbol, rest_price, source="rest_fallback")
            else:
                price_cache.set(self._symbol, rest_price, source="rest_fallback")

            logger.info("Price from REST API: %.2f", rest_price)
            return rest_price

        logger.error("REST API fallback failed")

        # SOURCE 3: Try InfluxDB directly
        logger.info("Trying InfluxDB direct fallback...")
        try:
            from app.utils.influx import query_latest_snapshot

            snapshot = query_latest_snapshot("features_1m", self._symbol, "1m")
            if snapshot and "close" in snapshot:
                influx_price = float(snapshot.get("close", 0.0))
                if influx_price > 0:
                    price_cache.set(self._symbol, influx_price, source="influx_fallback")
                    logger.warning("Price from InfluxDB: %.2f", influx_price)
                    return influx_price
        except Exception as exc:
            logger.error("InfluxDB direct fallback failed: %s", exc)

        # SOURCE 4: Emergency - use stale cache (up to 60 seconds old)
        logger.warning("EMERGENCY: All fresh sources failed, checking stale cache...")
        stale_price = price_cache.get(self._symbol, max_age_seconds=60)
        if stale_price is not None:
            logger.warning(
                "Using STALE cache price: %.2f (age: %.1fs) - EMERGENCY FALLBACK",
                stale_price,
                cache_age,
            )
            return stale_price

        # All sources exhausted
        logger.critical(
            "CRITICAL: NO PRICE AVAILABLE from any source (cache age: %.1fs)",
            cache_age,
        )
        return None

    def is_price_valid(self, price: float) -> bool:
        """
        Check if price is within sanity bounds.

        Args:
            price: Price to validate

        Returns:
            True if price is within bounds
        """
        return self._min_price_sanity <= price <= self._max_price_sanity

    def validate_price_change(
        self, current_price: float, last_price: float, max_change_pct: float = 10.0
    ) -> tuple[bool, float]:
        """
        Validate price change against threshold.

        Args:
            current_price: Current market price
            last_price: Last recorded price
            max_change_pct: Maximum allowed percentage change

        Returns:
            Tuple of (is_valid, change_percentage)
        """
        if last_price <= 0:
            return True, 0.0

        change_pct = abs((current_price - last_price) / last_price) * 100
        return change_pct <= max_change_pct, change_pct
