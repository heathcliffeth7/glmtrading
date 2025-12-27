from datetime import datetime
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from app.agents.base import Agent, AgentSignal
from app.utils.influx import query_latest_snapshot, query_historical_snapshots, detect_htf_support_resistance
from app.utils.logging import get_logger


logger = get_logger(__name__)

# Shared thread pool for parallel InfluxDB queries
_influx_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="influx_query")


class PureDataCollector(Agent):
    """
    Pure data collector - sends ALL raw market data to GLM
    without ANY processing, weighting, or pre-calculated signals.
    
    GLM makes ALL decisions including portfolio management.
    """
    
    def __init__(self, symbol: str = "BTCUSDT", enable_htf_filter: bool = True) -> None:
        self._symbol = symbol
        self._enable_htf_filter = enable_htf_filter

    def generate_signal(self) -> AgentSignal:
        """Send all raw market data to GLM for complete decision making"""
        
        # Collect ALL available market data without processing
        raw_market_data = self._collect_all_raw_data()
        
        # HTF analysis if enabled
        htf_analysis = None
        if self._enable_htf_filter:
            htf_analysis = detect_htf_support_resistance(self._symbol)
        
        # Simple reasoning - GLM will do all analysis
        reasoning = f"GLM Portfolio Management - Raw data for {self._symbol}"

        # Extract current price from collected data (prefer 1m, fallback to 4h)
        current_price = 0.0
        snapshots = raw_market_data.get("current_snapshots", {})
        for tf in ["1m", "5m", "15m", "4h", "1h"]:
            tf_data = snapshots.get(tf, {})
            if tf_data and tf_data.get("close"):
                current_price = float(tf_data["close"])
                break

        # Send everything to GLM
        metadata = {
            "raw_market_data": raw_market_data,
            "htf_analysis": htf_analysis if htf_analysis and htf_analysis.get("status") == "success" else None,
            "price": current_price,  # Required for exit_plan calculation
        }
        
        return AgentSignal(
            direction="GLM_ONLY",
            confidence=0.0,
            reasoning=reasoning,
            timestamp=datetime.utcnow().isoformat(),
            metadata=metadata,
        )

    def _collect_all_raw_data(self) -> Dict[str, any]:
        """Collect ALL available raw market data from all timeframes - PARALLEL"""

        import time
        start_time = time.time()

        # Define all queries to run in parallel
        snapshot_queries = {
            "1m": ("enriched_1m", self._symbol, "1m"),
            "5m": ("enriched_5m", self._symbol, "5m"),
            "15m": ("enriched_15min", self._symbol, "15min"),
            "30m": ("enriched_30min", self._symbol, "30min"),
            "1h": ("enriched_1h", self._symbol, "1h"),
            "4h": ("enriched_4h", self._symbol, "4h"),
            "1d": ("enriched_1d", self._symbol, "1d"),
        }

        historical_queries = {
            "hist_1m": ("enriched_1m", self._symbol, "1m", 200),
            "hist_5m": ("enriched_5m", self._symbol, "5m", 200),
            "hist_15m": ("enriched_15min", self._symbol, "15min", 200),
            "hist_30m": ("enriched_30min", self._symbol, "30min", 200),
            "hist_1h": ("enriched_1h", self._symbol, "1h", 200),
            "hist_4h": ("enriched_4h", self._symbol, "4h", 200),
            "hist_1d": ("enriched_1d", self._symbol, "1d", 100),
        }

        futures_queries = {
            "futures_current": ("derivatives", self._symbol, "30m"),
            "futures_hist": ("derivatives", self._symbol, "30m", 20),
        }

        # Submit all queries to thread pool
        futures = {}

        # Current snapshots
        for key, args in snapshot_queries.items():
            futures[key] = _influx_executor.submit(query_latest_snapshot, *args)

        # Historical snapshots
        for key, args in historical_queries.items():
            futures[key] = _influx_executor.submit(query_historical_snapshots, *args)

        # Futures data
        futures["futures_current"] = _influx_executor.submit(
            query_latest_snapshot, *futures_queries["futures_current"]
        )
        futures["futures_hist"] = _influx_executor.submit(
            query_historical_snapshots, *futures_queries["futures_hist"]
        )

        # Collect results
        results = {}
        for key, future in futures.items():
            try:
                results[key] = future.result(timeout=30)  # 30s timeout per query
            except Exception as e:
                logger.warning(f"Query {key} failed: {e}")
                results[key] = {} if not key.startswith("hist") else []

        elapsed = time.time() - start_time
        logger.info(f"⚡ Parallel data collection completed in {elapsed:.2f}s for {self._symbol}")

        # Build futures data from parallel results
        futures_data = self._build_futures_data_from_results(
            results.get("futures_current") or {},
            results.get("futures_hist") or []
        )

        # BTC prices for altcoin correlation analysis (ETH, SOL)
        btc_prices = None
        if not self._symbol.upper().startswith("BTC"):
            btc_prices = self._fetch_btc_prices_for_correlation()

        # Order book data for imbalance analysis
        orderbook_data = self._fetch_orderbook_data()

        return {
            "symbol": self._symbol,
            "current_snapshots": {
                "1m": results.get("1m") or {},
                "5m": results.get("5m") or {},
                "15m": results.get("15m") or {},
                "30m": results.get("30m") or {},
                "1h": results.get("1h") or {},
                "4h": results.get("4h") or {},
                "1d": results.get("1d") or {},
            },
            "historical_arrays": {
                "1m": self._format_historical_arrays(results.get("hist_1m") or []),
                "5m": self._format_historical_arrays(results.get("hist_5m") or []),
                "15m": self._format_historical_arrays(results.get("hist_15m") or []),
                "30m": self._format_historical_arrays(results.get("hist_30m") or []),
                "1h": self._format_historical_arrays(results.get("hist_1h") or []),
                "4h": self._format_historical_arrays(results.get("hist_4h") or []),
                "1d": self._format_historical_arrays(results.get("hist_1d") or []),
            },
            "futures_data": futures_data,
            "btc_prices": btc_prices,  # For altcoin correlation analysis
            "orderbook_data": orderbook_data,  # For order book imbalance analysis
        }

    def _build_futures_data_from_results(
        self, futures_snapshot: Dict, futures_historical: List[Dict]
    ) -> Dict[str, any]:
        """Build futures data structure from parallel query results"""
        current_funding_rate = futures_snapshot.get("funding_rate", 0)
        current_open_interest = futures_snapshot.get("open_interest", 0)
        current_long_short_ratio = futures_snapshot.get("long_short_ratio", 0)
        current_taker_ratio = futures_snapshot.get("taker_buy_sell_ratio", 1.0)
        current_taker_buy = futures_snapshot.get("taker_buy_volume", 0)
        current_taker_sell = futures_snapshot.get("taker_sell_volume", 0)

        avg_funding_rate = 0
        avg_open_interest = 0
        avg_long_short_ratio = 0

        if futures_historical:
            funding_rates = [f.get("funding_rate", 0) for f in futures_historical if f.get("funding_rate")]
            open_interests = [f.get("open_interest", 0) for f in futures_historical if f.get("open_interest")]
            lsr_values = [f.get("long_short_ratio", 0) for f in futures_historical if f.get("long_short_ratio")]

            if funding_rates:
                avg_funding_rate = sum(funding_rates) / len(funding_rates)
            if open_interests:
                avg_open_interest = sum(open_interests) / len(open_interests)
            if lsr_values:
                avg_long_short_ratio = sum(lsr_values) / len(lsr_values)

        return {
            "current": {
                "funding_rate": current_funding_rate,
                "open_interest": current_open_interest,
                "long_short_ratio": current_long_short_ratio,
                "taker_buy_sell_ratio": current_taker_ratio,
                "taker_buy_volume": current_taker_buy,
                "taker_sell_volume": current_taker_sell,
            },
            "averages": {
                "funding_rate_avg": avg_funding_rate,
                "open_interest_avg": avg_open_interest,
                "long_short_ratio_avg": avg_long_short_ratio,
            },
            "historical": self._format_historical_arrays(futures_historical),
        }
    
    def _format_historical_arrays(self, snapshots: List[Dict]) -> Dict[str, List]:
        """Format historical snapshots into raw arrays for GLM analysis"""
        if not snapshots:
            return {}

        # Extract ALL available indicators into arrays
        result = {}
        all_keys = set()

        # Collect all possible keys from all snapshots
        for snapshot in snapshots:
            all_keys.update(snapshot.keys())

        # Create arrays for each key
        for key in sorted(all_keys):
            values = []
            for snapshot in snapshots:
                if key in snapshot:
                    values.append(snapshot[key])
            if values:
                result[key] = values

        return result

    def _fetch_btc_prices_for_correlation(self) -> Optional[List[float]]:
        """
        Fetch BTCUSDT close prices for correlation analysis with altcoins.
        Uses 1h timeframe to match primary trading timeframe.

        Returns:
            List of BTC close prices or None if unavailable
        """
        try:
            # Fetch BTCUSDT historical data from 1h timeframe (matches primary TF)
            btc_historical = query_historical_snapshots("enriched_1h", "BTCUSDT", "1h", limit=50)

            if not btc_historical:
                logger.warning("No BTCUSDT historical data for correlation")
                return None

            # Extract close prices
            btc_closes = []
            for snapshot in btc_historical:
                close_price = snapshot.get("close")
                if close_price is not None:
                    btc_closes.append(float(close_price))

            if len(btc_closes) < 21:
                logger.warning(f"Not enough BTC data for correlation: {len(btc_closes)} (need 21+)")
                return None

            logger.debug(f"✅ Fetched {len(btc_closes)} BTC prices for correlation analysis")
            return btc_closes

        except Exception as e:
            logger.warning(f"Failed to fetch BTC prices for correlation: {e}")
            return None

    def _fetch_orderbook_data(self) -> Optional[Dict]:
        """
        Fetch order book data from Binance API for imbalance analysis.

        Returns:
            Dict with 'bids' and 'asks' lists, or None if unavailable
        """
        try:
            url = "https://api.binance.com/api/v3/depth"
            params = {"symbol": self._symbol.upper(), "limit": 20}

            with httpx.Client(timeout=5.0) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

            bids = data.get("bids", [])
            asks = data.get("asks", [])

            if not bids or not asks:
                logger.warning(f"Empty orderbook for {self._symbol}")
                return None

            logger.debug(f"✅ Fetched orderbook: {len(bids)} bids, {len(asks)} asks")
            return {"bids": bids, "asks": asks}

        except Exception as e:
            logger.warning(f"Failed to fetch orderbook for {self._symbol}: {e}")
            return None

