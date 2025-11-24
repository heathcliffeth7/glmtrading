"""
Latency Monitoring Dashboard Helper

Provides utilities for querying and analyzing end-to-end latency metrics
from InfluxDB for system performance monitoring and bottleneck detection.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.utils.influx import _ensure_client, _query_api
from app.utils.logging import get_logger


logger = get_logger(__name__)


class LatencyMonitor:
    """
    Monitor and analyze pipeline latency metrics
    
    Usage:
        monitor = LatencyMonitor()
        
        # Get recent latency stats
        stats = monitor.get_latency_stats(minutes=60)
        
        # Check for bottlenecks
        bottlenecks = monitor.detect_bottlenecks(threshold_ms=500)
        
        # Get latency percentiles
        p95 = monitor.get_percentile(95, minutes=60)
    """
    
    def __init__(self, bucket: str = None):
        from app.config.settings import get_settings
        settings = get_settings()
        self.bucket = bucket or settings.influx.bucket
    
    def get_latency_stats(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "30min",
        minutes: int = 60,
    ) -> Dict[str, float]:
        """
        Get latency statistics for the specified time window
        
        Args:
            symbol: Trading symbol
            interval: Time interval
            minutes: Time window in minutes
        
        Returns:
            Dict with latency statistics (mean, min, max, p50, p95, p99)
        """
        _ensure_client()
        
        query = f"""
from(bucket: "{self.bucket}")
  |> range(start: -{minutes}m)
  |> filter(fn: (r) => r["_measurement"] == "pipeline_latency")
  |> filter(fn: (r) => r["symbol"] == "{symbol}" and r["interval"] == "{interval}")
  |> filter(fn: (r) => r["_field"] == "latency_total_e2e_ms")
  |> keep(columns: ["_time", "_value"])
"""
        
        try:
            tables = _query_api.query(query)
            
            if not tables or not tables[0].records:
                logger.warning("No latency data found for %s/%s in last %d minutes", symbol, interval, minutes)
                return {}
            
            values = [record.get_value() for record in tables[0].records]
            
            if not values:
                return {}
            
            values.sort()
            n = len(values)
            
            stats = {
                'count': n,
                'mean_ms': sum(values) / n,
                'min_ms': values[0],
                'max_ms': values[-1],
                'p50_ms': values[int(n * 0.50)],
                'p95_ms': values[int(n * 0.95)],
                'p99_ms': values[int(n * 0.99)],
            }
            
            # Calculate percentage above threshold (1000ms = 1s)
            above_threshold = sum(1 for v in values if v > 1000)
            stats['pct_above_1s'] = (above_threshold / n) * 100
            
            return stats
            
        except Exception as e:
            logger.error("Failed to get latency stats: %s", e)
            return {}
    
    def get_stage_breakdown(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "30min",
        minutes: int = 60,
    ) -> Dict[str, Dict[str, float]]:
        """
        Get latency breakdown by pipeline stage
        
        Args:
            symbol: Trading symbol
            interval: Time interval
            minutes: Time window in minutes
        
        Returns:
            Dict with stage-wise latency statistics
        """
        _ensure_client()
        
        stages = [
            'latency_ingestion_to_ta_ms',
            'latency_ta_to_influx_ms',
            'latency_influx_to_signal_ms',
            'latency_signal_to_glm_ms',
        ]
        
        stage_stats = {}
        
        for stage in stages:
            query = f"""
from(bucket: "{self.bucket}")
  |> range(start: -{minutes}m)
  |> filter(fn: (r) => r["_measurement"] == "pipeline_latency")
  |> filter(fn: (r) => r["symbol"] == "{symbol}" and r["interval"] == "{interval}")
  |> filter(fn: (r) => r["_field"] == "{stage}")
  |> keep(columns: ["_value"])
"""
            
            try:
                tables = _query_api.query(query)
                
                if not tables or not tables[0].records:
                    continue
                
                values = [record.get_value() for record in tables[0].records]
                
                if values:
                    values.sort()
                    n = len(values)
                    
                    stage_name = stage.replace('latency_', '').replace('_ms', '')
                    stage_stats[stage_name] = {
                        'mean_ms': sum(values) / n,
                        'min_ms': values[0],
                        'max_ms': values[-1],
                        'p95_ms': values[int(n * 0.95)],
                    }
                    
            except Exception as e:
                logger.error("Failed to get stage breakdown for %s: %s", stage, e)
        
        return stage_stats
    
    def detect_bottlenecks(
        self,
        threshold_ms: float = 500,
        symbol: str = "BTCUSDT",
        interval: str = "30min",
        minutes: int = 60,
    ) -> List[Dict[str, any]]:
        """
        Detect pipeline bottlenecks (stages with high latency)
        
        Args:
            threshold_ms: Latency threshold in milliseconds
            symbol: Trading symbol
            interval: Time interval
            minutes: Time window in minutes
        
        Returns:
            List of bottleneck dicts with stage name and latency
        """
        stage_breakdown = self.get_stage_breakdown(symbol, interval, minutes)
        
        bottlenecks = []
        
        for stage, stats in stage_breakdown.items():
            mean_latency = stats.get('mean_ms', 0)
            p95_latency = stats.get('p95_ms', 0)
            
            if mean_latency > threshold_ms or p95_latency > threshold_ms:
                bottlenecks.append({
                    'stage': stage,
                    'mean_ms': mean_latency,
                    'p95_ms': p95_latency,
                    'severity': 'critical' if mean_latency > threshold_ms * 2 else 'warning',
                })
        
        # Sort by mean latency (descending)
        bottlenecks.sort(key=lambda x: x['mean_ms'], reverse=True)
        
        return bottlenecks
    
    def get_recent_traces(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "30min",
        limit: int = 10,
    ) -> List[Dict[str, any]]:
        """
        Get most recent latency traces
        
        Args:
            symbol: Trading symbol
            interval: Time interval
            limit: Number of traces to return
        
        Returns:
            List of trace dicts with timestamps and latencies
        """
        _ensure_client()
        
        query = f"""
from(bucket: "{self.bucket}")
  |> range(start: -1h)
  |> filter(fn: (r) => r["_measurement"] == "pipeline_latency")
  |> filter(fn: (r) => r["symbol"] == "{symbol}" and r["interval"] == "{interval}")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: {limit})
"""
        
        try:
            tables = _query_api.query(query)
            
            if not tables:
                return []
            
            traces = []
            
            for table in tables:
                for record in table.records:
                    trace = {
                        'timestamp': record.get_time().isoformat(),
                        'total_e2e_ms': record.values.get('latency_total_e2e_ms', 0),
                        'ingestion_to_ta_ms': record.values.get('latency_ingestion_to_ta_ms', 0),
                        'ta_to_influx_ms': record.values.get('latency_ta_to_influx_ms', 0),
                        'influx_to_signal_ms': record.values.get('latency_influx_to_signal_ms', 0),
                        'signal_to_glm_ms': record.values.get('latency_signal_to_glm_ms', 0),
                    }
                    traces.append(trace)
            
            return traces
            
        except Exception as e:
            logger.error("Failed to get recent traces: %s", e)
            return []
    
    def get_health_status(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "30min",
    ) -> Dict[str, any]:
        """
        Get overall pipeline health status based on latency
        
        Args:
            symbol: Trading symbol
            interval: Time interval
        
        Returns:
            Dict with health status and recommendations
        """
        stats = self.get_latency_stats(symbol, interval, minutes=15)
        
        if not stats:
            return {
                'status': 'unknown',
                'message': 'No latency data available',
            }
        
        mean_latency = stats.get('mean_ms', 0)
        p95_latency = stats.get('p95_ms', 0)
        pct_above_1s = stats.get('pct_above_1s', 0)
        
        # Determine health status
        if mean_latency < 500 and p95_latency < 1000 and pct_above_1s < 5:
            status = 'healthy'
            emoji = '🟢'
            message = 'Pipeline latency is within acceptable limits'
        elif mean_latency < 1000 and p95_latency < 2000 and pct_above_1s < 20:
            status = 'degraded'
            emoji = '🟡'
            message = 'Pipeline latency is elevated but acceptable'
        else:
            status = 'critical'
            emoji = '🔴'
            message = 'Pipeline latency is critically high'
        
        # Detect bottlenecks
        bottlenecks = self.detect_bottlenecks(threshold_ms=500, symbol=symbol, interval=interval, minutes=15)
        
        return {
            'status': status,
            'emoji': emoji,
            'message': message,
            'stats': stats,
            'bottlenecks': bottlenecks,
        }
    
    def print_dashboard(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "30min",
    ) -> None:
        """
        Print a formatted latency dashboard to console
        
        Args:
            symbol: Trading symbol
            interval: Time interval
        """
        print("\n" + "="*60)
        print(f"  LATENCY MONITORING DASHBOARD - {symbol}/{interval}")
        print("="*60)
        
        # Health status
        health = self.get_health_status(symbol, interval)
        print(f"\n{health['emoji']} Status: {health['status'].upper()}")
        print(f"   {health['message']}")
        
        # Overall stats (last 60 minutes)
        stats = self.get_latency_stats(symbol, interval, minutes=60)
        if stats:
            print("\n📊 Last 60 Minutes:")
            print(f"   Count:     {stats['count']}")
            print(f"   Mean:      {stats['mean_ms']:.0f}ms")
            print(f"   P50:       {stats['p50_ms']:.0f}ms")
            print(f"   P95:       {stats['p95_ms']:.0f}ms")
            print(f"   P99:       {stats['p99_ms']:.0f}ms")
            print(f"   Max:       {stats['max_ms']:.0f}ms")
            print(f"   >1s:       {stats['pct_above_1s']:.1f}%")
        
        # Stage breakdown
        stage_breakdown = self.get_stage_breakdown(symbol, interval, minutes=60)
        if stage_breakdown:
            print("\n⏱️  Stage Breakdown (Mean Latency):")
            for stage, stage_stats in stage_breakdown.items():
                mean = stage_stats['mean_ms']
                p95 = stage_stats['p95_ms']
                
                # Color code based on latency
                if mean > 500:
                    emoji = "🔴"
                elif mean > 200:
                    emoji = "🟡"
                else:
                    emoji = "🟢"
                
                print(f"   {emoji} {stage:20s}: {mean:6.0f}ms (p95: {p95:.0f}ms)")
        
        # Bottlenecks
        bottlenecks = health.get('bottlenecks', [])
        if bottlenecks:
            print("\n⚠️  Detected Bottlenecks:")
            for bn in bottlenecks:
                severity_emoji = "🔴" if bn['severity'] == 'critical' else "🟡"
                print(f"   {severity_emoji} {bn['stage']:20s}: {bn['mean_ms']:.0f}ms (p95: {bn['p95_ms']:.0f}ms)")
        else:
            print("\n✅ No bottlenecks detected")
        
        # Recent traces
        traces = self.get_recent_traces(symbol, interval, limit=5)
        if traces:
            print("\n🔍 Recent Traces (Last 5):")
            for i, trace in enumerate(traces, 1):
                total = trace['total_e2e_ms']
                emoji = "🔴" if total > 1000 else "🟡" if total > 500 else "🟢"
                timestamp = trace['timestamp'].split('T')[1][:8]  # HH:MM:SS
                print(f"   {emoji} {timestamp}: {total:6.0f}ms total")
        
        print("\n" + "="*60 + "\n")


def main():
    """CLI entry point for latency monitoring"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Latency Monitoring Dashboard')
    parser.add_argument('--symbol', default='BTCUSDT', help='Trading symbol')
    parser.add_argument('--interval', default='30min', help='Time interval')
    parser.add_argument('--stats', action='store_true', help='Show statistics')
    parser.add_argument('--bottlenecks', action='store_true', help='Show bottlenecks')
    parser.add_argument('--traces', action='store_true', help='Show recent traces')
    
    args = parser.parse_args()
    
    monitor = LatencyMonitor()
    
    if args.stats:
        stats = monitor.get_latency_stats(args.symbol, args.interval)
        print("\nLatency Statistics:")
        for key, value in stats.items():
            print(f"  {key}: {value}")
    
    elif args.bottlenecks:
        bottlenecks = monitor.detect_bottlenecks(symbol=args.symbol, interval=args.interval)
        print("\nDetected Bottlenecks:")
        for bn in bottlenecks:
            print(f"  {bn['stage']}: {bn['mean_ms']:.0f}ms (severity: {bn['severity']})")
    
    elif args.traces:
        traces = monitor.get_recent_traces(args.symbol, args.interval)
        print("\nRecent Traces:")
        for trace in traces:
            print(f"  {trace['timestamp']}: {trace['total_e2e_ms']:.0f}ms")
    
    else:
        # Show full dashboard
        monitor.print_dashboard(args.symbol, args.interval)


if __name__ == '__main__':
    main()
