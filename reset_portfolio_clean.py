#!/usr/bin/env python3
"""
Reset Portfolio and Trading Data
Clear all old trades, positions, and portfolio history
"""

import os
from datetime import datetime, timedelta
from influxdb_client import InfluxDBClient
from app.config.settings import get_settings
from app.utils.influx import _ensure_client


def reset_portfolio_data():
    """Reset all portfolio and trading data"""
    
    print('='*60)
    print('🔄 RESETTING PORTFOLIO AND TRADING DATA')
    print('='*60)
    print()
    
    try:
        # Get InfluxDB client
        _ensure_client()
        from app.utils.influx import _client, _query_api, _write_api
        client = _client
        query_api = _query_api
        delete_api = client.delete_api()
        
        # Measurements to delete
        measurements_to_reset = [
            "portfolio",
            "positions", 
            "trades",
            "pnl",
            "signals",
            "executions",
            "risk_decisions"
        ]
        
        print('📊 Checking data before reset...')
        print()
        
        total_deleted = 0
        
        for measurement in measurements_to_reset:
            try:
                # Check if data exists
                query = f'''
                from(bucket: "trading")
                |> range(start: -30d)
                |> filter(fn: (r) => r._measurement == "{measurement}")
                |> count()
                '''
                
                result = query_api.query(query)
                count = 0
                
                for table in result:
                    for record in table.records:
                        count += record.get_value()
                
                if count > 0:
                    print(f'  📋 {measurement}: {count:,} records found')
                    
                    # Delete all data for this measurement
                    start_time = datetime.utcnow() - timedelta(days=30)
                    end_time = datetime.utcnow()
                    
                    delete_api.delete(
                        start=start_time,
                        stop=end_time,
                        predicate=f'_measurement="{measurement}"',
                        bucket="trading",
                        org="trading"
                    )
                    
                    print(f'  ✅ {measurement}: DELETED')
                    total_deleted += count
                else:
                    print(f'  📋 {measurement}: No data found')
                    
            except Exception as e:
                print(f'  ⚠️  {measurement}: Error - {e}')
        
        print()
        print(f'🗑️  Total records deleted: {total_deleted:,}')
        print()
        
        # Reset portfolio to initial state
        print('🏗️  Setting initial portfolio state...')
        
        # Write initial portfolio state
        from app.utils.influx import write_measurement
        
        # Reset portfolio to $10,000
        write_measurement(
            measurement="portfolio",
            tags={"symbol": "BTCUSDT"},
            fields={"equity": 10000.0, "available_cash": 10000.0},
            timestamp=datetime.utcnow()
        )
        
        # Reset position to 0 (FLAT)
        write_measurement(
            measurement="positions", 
            tags={"symbol": "BTCUSDT"},
            fields={"quantity": 0.0},
            timestamp=datetime.utcnow()
        )
        
        print('  ✅ Portfolio reset to $10,000 initial capital')
        print('  ✅ Position reset to 0 BTC (FLAT)')
        print('  ✅ Available cash reset to $10,000')
        print()
        
        print('='*60)
        print('✅ PORTFOLIO RESET COMPLETE!')
        print('='*60)
        print()
        print('🎯 Ready to start fresh trading!')
        print()
        print('Next steps:')
        print('1. Run: python demo_pure_glm.py')
        print('2. Run: python -m app.orchestrator.automated')
        print('3. GLM will start with clean portfolio!')
        print()
        
    except Exception as e:
        print(f'❌ Error during reset: {e}')
        import traceback
        traceback.print_exc()
    
    finally:
        if 'client' in locals():
            client.close()


if __name__ == "__main__":
    reset_portfolio_data()
