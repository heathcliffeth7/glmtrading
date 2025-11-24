#!/usr/bin/env python3
"""
Backtesting with Enriched Data - Test multi-signal strategy
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Any

from app.agents.multi_signal import MultiSignalAgent
from app.utils.influx import query_range_between
from app.utils.logging import configure_logging, get_logger
from app.config.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


class EnrichedBacktester:
    """Backtest trading strategy with enriched multi-signal data"""
    
    def __init__(self, symbol: str = "BTCUSDT", interval: str = "5min", initial_cash: float = 10000.0):
        self._symbol = symbol
        self._interval = interval
        self._initial_cash = initial_cash
        self._agent = MultiSignalAgent(symbol, interval, adaptive=True)
        
        # State
        self._cash = initial_cash
        self._position = 0.0  # BTC
        self._avg_price = 0.0
        self._trades: List[Dict[str, Any]] = []
    
    def fetch_data(self, days: int = 7) -> pd.DataFrame:
        """Fetch enriched historical data"""
        logger.info("Fetching %d days of data for %s", days, self._symbol)
        
        end = datetime.utcnow()
        start = end - timedelta(days=days)
        
        data = query_range_between(f"enriched_{self._interval}", self._symbol, self._interval, start, end)
        
        if not data:
            logger.error("No data found!")
            return pd.DataFrame()
        
        df = pd.DataFrame(data)
        df = df.pivot(index='timestamp', columns='field', values='value')
        df = df.reset_index()
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp')
        
        logger.info("Fetched %d bars", len(df))
        
        return df
    
    def run(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Run backtest"""
        logger.info("Running backtest on %d bars", len(df))
        
        for idx, row in df.iterrows():
            # Get signal (simulate using row data)
            signal = self._generate_signal_from_row(row)
            
            # Execute trade
            self._execute_signal(signal, row)
        
        # Final metrics
        return self._calculate_metrics(df)
    
    def _generate_signal_from_row(self, row: pd.Series) -> Dict[str, Any]:
        """Generate signal from enriched row data"""
        # Use MultiSignalAgent logic but with row data
        # Simplified for backtesting
        
        close = row.get('close', 0)
        rsi = row.get('rsi_14', 50)
        ema_20 = row.get('ema_20', close)
        ema_50 = row.get('ema_50', close)
        
        # Simple rules for demo
        if rsi < 30 and ema_20 > ema_50:
            return {"direction": "BUY", "confidence": 0.7}
        elif rsi > 70 and ema_20 < ema_50:
            return {"direction": "SELL", "confidence": 0.7}
        else:
            return {"direction": "HOLD", "confidence": 0.0}
    
    def _execute_signal(self, signal: Dict[str, Any], row: pd.Series) -> None:
        """Execute trade based on signal"""
        direction = signal['direction']
        confidence = signal['confidence']
        price = row.get('close', 0)
        
        if price <= 0 or confidence < 0.5:
            return
        
        # Position sizing based on confidence
        position_size_pct = confidence * 0.2  # Max 20% of portfolio
        
        if direction == "BUY" and self._position == 0:
            # Open long
            amount = (self._cash * position_size_pct) / price
            if amount > 0:
                self._position = amount
                self._avg_price = price
                self._cash -= amount * price
                self._trades.append({
                    'timestamp': row['timestamp'],
                    'side': 'BUY',
                    'price': price,
                    'amount': amount,
                    'pnl': 0.0,
                })
        
        elif direction == "SELL" and self._position > 0:
            # Close long
            pnl = (price - self._avg_price) * self._position
            self._cash += self._position * price
            self._trades.append({
                'timestamp': row['timestamp'],
                'side': 'SELL',
                'price': price,
                'amount': self._position,
                'pnl': pnl,
            })
            self._position = 0.0
            self._avg_price = 0.0
    
    def _calculate_metrics(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Calculate performance metrics"""
        final_price = df.iloc[-1]['close'] if not df.empty else 0
        
        # Close any open position
        if self._position > 0 and final_price > 0:
            pnl = (final_price - self._avg_price) * self._position
            self._cash += self._position * final_price
            self._position = 0.0
        
        total_value = self._cash
        total_return = ((total_value - self._initial_cash) / self._initial_cash) * 100
        
        # Trade statistics
        num_trades = len(self._trades)
        winning_trades = [t for t in self._trades if t['pnl'] > 0]
        losing_trades = [t for t in self._trades if t['pnl'] < 0]
        
        win_rate = (len(winning_trades) / num_trades * 100) if num_trades > 0 else 0
        
        avg_win = sum(t['pnl'] for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t['pnl'] for t in losing_trades) / len(losing_trades) if losing_trades else 0
        
        metrics = {
            'initial_cash': self._initial_cash,
            'final_value': total_value,
            'total_return': total_return,
            'num_trades': num_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': abs(avg_win / avg_loss) if avg_loss != 0 else 0,
        }
        
        return metrics


def main():
    """Run backtest"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Backtest enriched strategy')
    parser.add_argument('--symbol', default='BTCUSDT', help='Trading symbol')
    parser.add_argument('--interval', default='5min', help='Timeframe')
    parser.add_argument('--days', type=int, default=7, help='Days to backtest')
    parser.add_argument('--cash', type=float, default=10000, help='Initial cash')
    
    args = parser.parse_args()
    
    # Create backtester
    backtester = EnrichedBacktester(args.symbol, args.interval, args.cash)
    
    # Fetch data
    df = backtester.fetch_data(args.days)
    
    if df.empty:
        logger.error("No data available!")
        return
    
    # Run backtest
    metrics = backtester.run(df)
    
    # Print results
    print("\n" + "="*60)
    print(" BACKTEST RESULTS")
    print("="*60)
    print(f" Symbol: {args.symbol}")
    print(f" Period: {args.days} days")
    print(f" Bars: {len(df)}")
    print("-"*60)
    print(f" Initial Cash: ${metrics['initial_cash']:,.2f}")
    print(f" Final Value: ${metrics['final_value']:,.2f}")
    print(f" Total Return: {metrics['total_return']:+.2f}%")
    print("-"*60)
    print(f" Total Trades: {metrics['num_trades']}")
    print(f" Winning Trades: {metrics['winning_trades']}")
    print(f" Losing Trades: {metrics['losing_trades']}")
    print(f" Win Rate: {metrics['win_rate']:.1f}%")
    print(f" Avg Win: ${metrics['avg_win']:.2f}")
    print(f" Avg Loss: ${metrics['avg_loss']:.2f}")
    print(f" Profit Factor: {metrics['profit_factor']:.2f}")
    print("="*60)
    print()


if __name__ == '__main__':
    main()
