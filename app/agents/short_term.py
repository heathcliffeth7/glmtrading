from datetime import datetime
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

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
        
        # Send everything to GLM
        metadata = {
            "raw_market_data": raw_market_data,
            "htf_analysis": htf_analysis if htf_analysis and htf_analysis.get("status") == "success" else None,
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
            "hist_1m": ("enriched_1m", self._symbol, "1m", 20),
            "hist_5m": ("enriched_5m", self._symbol, "5m", 20),
            "hist_15m": ("enriched_15min", self._symbol, "15min", 20),
            "hist_30m": ("enriched_30min", self._symbol, "30min", 20),
            "hist_1h": ("enriched_1h", self._symbol, "1h", 20),
            "hist_4h": ("enriched_4h", self._symbol, "4h", 20),
            "hist_1d": ("enriched_1d", self._symbol, "1d", 10),
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
        }

    def _build_futures_data_from_results(
        self, futures_snapshot: Dict, futures_historical: List[Dict]
    ) -> Dict[str, any]:
        """Build futures data structure from parallel query results"""
        current_funding_rate = futures_snapshot.get("funding_rate", 0)
        current_open_interest = futures_snapshot.get("open_interest", 0)
        current_long_short_ratio = futures_snapshot.get("long_short_ratio", 0)

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
    
