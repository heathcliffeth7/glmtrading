from datetime import datetime
from typing import Dict, List, Optional

from app.agents.base import Agent, AgentSignal
from app.utils.influx import query_latest_snapshot, query_historical_snapshots, detect_htf_support_resistance
from app.utils.logging import get_logger


logger = get_logger(__name__)


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
        """Collect ALL available raw market data from all timeframes"""
        
        # Get ALL latest snapshots from InfluxDB
        data_1m = query_latest_snapshot("enriched_1m", self._symbol, "1m") or {}
        data_5m = query_latest_snapshot("enriched_5m", self._symbol, "5m") or {}
        data_15m = query_latest_snapshot("enriched_15min", self._symbol, "15min") or {}
        data_30m = query_latest_snapshot("enriched_30min", self._symbol, "30min") or {}
        data_1h = query_latest_snapshot("enriched_1h", self._symbol, "1h") or {}
        data_4h = query_latest_snapshot("enriched_4h", self._symbol, "4h") or {}
        data_1d = query_latest_snapshot("enriched_1d", self._symbol, "1d") or {}
        
        # Get FUTURES market data (funding rate, long/short ratio, open interest)
        futures_data = self._collect_futures_data()
        
        # Get ALL historical data for context
        historical_1m = query_historical_snapshots("enriched_1m", self._symbol, "1m", limit=20)
        historical_5m = query_historical_snapshots("enriched_5m", self._symbol, "5m", limit=20)
        historical_15m = query_historical_snapshots("enriched_15min", self._symbol, "15min", limit=20)
        historical_30m = query_historical_snapshots("enriched_30min", self._symbol, "30min", limit=20)
        historical_1h = query_historical_snapshots("enriched_1h", self._symbol, "1h", limit=20)
        historical_4h = query_historical_snapshots("enriched_4h", self._symbol, "4h", limit=20)
        historical_1d = query_historical_snapshots("enriched_1d", self._symbol, "1d", limit=10)
        
        return {
            "symbol": self._symbol,
            "current_snapshots": {
                "1m": data_1m,
                "5m": data_5m,
                "15m": data_15m,
                "30m": data_30m,
                "1h": data_1h,
                "4h": data_4h,
                "1d": data_1d,
            },
            "historical_arrays": {
                "1m": self._format_historical_arrays(historical_1m),
                "5m": self._format_historical_arrays(historical_5m),
                "15m": self._format_historical_arrays(historical_15m),
                "30m": self._format_historical_arrays(historical_30m),
                "1h": self._format_historical_arrays(historical_1h),
                "4h": self._format_historical_arrays(historical_4h),
                "1d": self._format_historical_arrays(historical_1d),
            },
            "futures_data": futures_data,
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
    
    def _collect_futures_data(self) -> Dict[str, any]:
        """Collect futures market data (funding rate, long/short ratio, open interest)"""
        
        try:
            # Query latest futures metrics from InfluxDB
            # These come from the derivatives/futures measurement
            futures_snapshot = query_latest_snapshot("derivatives", self._symbol, "30m") or {}
            
            # Get historical futures data for trend analysis
            futures_historical = query_historical_snapshots("derivatives", self._symbol, "30m", limit=20) or []
            
            # Extract current values
            current_funding_rate = futures_snapshot.get("funding_rate", 0)
            current_open_interest = futures_snapshot.get("open_interest", 0)
            current_long_short_ratio = futures_snapshot.get("long_short_ratio", 0)
            
            # Calculate averages from historical data
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
        
        except Exception as e:
            logger.warning(f"Failed to collect futures data: {e}")
            return {
                "current": {},
                "averages": {},
                "historical": {},
            }
