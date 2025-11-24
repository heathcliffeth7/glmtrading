#!/usr/bin/env python3
"""
Backtesting Engine
Tarihsel verilerle trading stratejisini simüle eder
"""

import asyncio
import sys
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
import pandas as pd
import numpy as np
import yaml
from pathlib import Path

# Add paths
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from influxdb_client import InfluxDBClient
from influxdb_client.client.query_api import QueryApi
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class Trade:
    """Individual trade record"""
    timestamp: datetime
    action: str  # BUY, SELL, CLOSE
    price: float
    amount: float
    value: float
    pnl: float
    confidence: float
    reasoning: str
    portfolio_value: float

@dataclass
class Portfolio:
    """Portfolio state at a point in time"""
    timestamp: datetime
    btc_position: float
    usdt_balance: float
    total_value: float
    equity: float
    used_margin: float
    free_equity: float
    unrealized_pnl: float

@dataclass
class BacktestConfig:
    """Backtest configuration"""
    start_date: datetime
    end_date: datetime
    symbol: str
    timeframe: str
    initial_capital: float
    commission_rate: float
    slippage: float
    max_position_size: float
    confidence_threshold: float
    risk_per_trade: float

class MockSignalAgent:
    """Simulated signal agent for backtesting"""
    def __init__(self, symbol: str, timeframe: str):
        self.symbol = symbol
        self.timeframe = timeframe
        self.last_close_time = None

    def generate_signal(self, market_data: Dict) -> Tuple[str, float, str]:
        """
        Generate trading signal based on technical indicators
        Returns: (action, confidence, reasoning)
        """
        # Simulate GLM-4 decision making
        rsi = market_data.get('rsi', 50)
        macd = market_data.get('macd', 0)
        macd_signal = market_data.get('macd_signal', 0)
        ema_12 = market_data.get('ema_12', 0)
        ema_26 = market_data.get('ema_26', 0)

        # Trading logic simulation
        bullish_signals = 0
        bearish_signals = 0

        # RSI signals
        if rsi < 30:
            bullish_signals += 2
        elif rsi > 70:
            bearish_signals += 2

        # MACD crossover
        if macd > macd_signal and macd > 0:
            bullish_signals += 1
        elif macd < macd_signal and macd < 0:
            bearish_signals += 1

        # EMA crossover
        if ema_12 > ema_26:
            bullish_signals += 1
        else:
            bearish_signals += 1

        # Make decision
        total_signals = bullish_signals + bearish_signals
        if total_signals == 0:
            return 'HOLD', 0.0, 'Insufficient signal strength'

        bullish_ratio = bullish_signals / total_signals
        confidence = min(abs(bullish_ratio - 0.5) * 2, 0.95)

        # Cooldown check
        if self.last_close_time:
            time_since_close = (datetime.now() - self.last_close_time).total_seconds()
            if time_since_close < 900:  # 15 minutes
                return 'HOLD', confidence, 'Cooldown period active'

        if bullish_ratio > 0.65:
            return 'BUY', confidence, f'Bullish signals: {bullish_signals}/{total_signals}'
        elif bullish_ratio < 0.35:
            return 'SELL', confidence, f'Bearish signals: {bearish_signals}/{total_signals}'
        else:
            return 'HOLD', confidence, 'Mixed signals'

    def calculate_position_size(self, portfolio_value: float, confidence: float) -> float:
        """Calculate position size based on confidence and risk"""
        base_size = portfolio_value * self.risk_per_trade
        return min(base_size * confidence * 2, self.max_position_size)

class BacktestEngine:
    """Main backtesting engine"""

    def __init__(self, config_path: str = None):
        """Initialize engine with configuration"""
        if config_path is None:
            # Get the directory where this script is located
            script_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(script_dir, "config", "parameters.yaml")
            # If that doesn't exist, try relative to trading root
            if not os.path.exists(config_path):
                trading_root = os.path.dirname(os.path.dirname(os.path.dirname(script_dir)))
                config_path = os.path.join(trading_root, "backtest", "config", "parameters.yaml")

        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        # Setup database connection
        self.db_url = os.getenv('DATABASE_URL')
        if self.db_url:
            self.db_engine = create_engine(self.db_url)
            self.Session = sessionmaker(bind=self.db_engine)

        # InfluxDB setup
        self.influx_url = os.getenv('INFLUXDB_URL', 'http://localhost:8086')
        self.influx_token = os.getenv('INFLUXDB_TOKEN', '')
        self.influx_org = os.getenv('INFLUXDB_ORG', 'trading')
        self.influx_bucket = 'market_data'

        self.client = InfluxDBClient(
            url=self.influx_url,
            token=self.influx_token,
            org=self.influx_org
        )

        self.query_api = self.client.query_api()

    def load_historical_data(self, config: BacktestConfig) -> pd.DataFrame:
        """Load historical data from InfluxDB"""
        measurement = f"backtest_{config.timeframe}"

        query = f'''
        from(bucket: "{self.influx_bucket}")
          |> range(start: {config.start_date.strftime("%Y-%m-%dT%H:%M:%SZ")}, stop: {config.end_date.strftime("%Y-%m-%dT%H:%M:%SZ")})
          |> filter(fn: (r) => r._measurement == "{measurement}")
          |> filter(fn: (r) => r.symbol == "{config.symbol}")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
          |> sort(columns: ["_time"])
        '''

        logger.info(f"Loading data from {config.start_date} to {config.end_date}")

        try:
            result = self.query_api.query_data_frame(query)
            if result.empty:
                raise ValueError("No data found in InfluxDB")

            result['_time'] = pd.to_datetime(result['_time'])
            result.set_index('_time', inplace=True)

            logger.info(f"Loaded {len(result)} bars")
            return result

        except Exception as e:
            logger.error(f"Error loading data: {e}")
            # Fallback to simulated data
            return self._generate_simulated_data(config)

    def _generate_simulated_data(self, config: BacktestConfig) -> pd.DataFrame:
        """Generate simulated data if real data unavailable"""
        logger.warning("Using simulated data - implement real data loading")

        # Create date range
        freq_map = {
            '1m': '1min', '5m': '5min', '15m': '15min',
            '30m': '30min', '1h': '1H', '4h': '4H'
        }

        date_range = pd.date_range(
            start=config.start_date,
            end=config.end_date,
            freq=freq_map.get(config.timeframe, '15min')
        )[:1000]  # Limit to 1000 bars

        # Generate simulated OHLCV
        np.random.seed(42)
        base_price = 50000

        data = []
        for i, timestamp in enumerate(date_range):
            price = base_price * (1 + 0.0001 * i + np.random.normal(0, 0.01))
            open_price = price * (1 + np.random.normal(0, 0.005))
            high = max(open_price, price) * (1 + abs(np.random.normal(0, 0.01)))
            low = min(open_price, price) * (1 - abs(np.random.normal(0, 0.01)))
            close = price
            volume = np.random.exponential(1000)

            data.append({
                'timestamp': timestamp,
                'open': open_price,
                'high': high,
                'low': low,
                'close': close,
                'volume': volume,
                'rsi': 30 + 40 * np.random.random(),
                'macd': np.random.normal(0, 100),
                'macd_signal': np.random.normal(0, 100),
                'ema_12': close * 0.999,
                'ema_26': close * 0.998,
            })

        df = pd.DataFrame(data)
        df.set_index('timestamp', inplace=True)

        logger.info(f"Generated {len(df)} simulated bars")
        return df

    def run_backtest(self, config: BacktestConfig, run_name: str) -> Dict:
        """Run backtest simulation"""
        logger.info("=" * 60)
        logger.info(f"Starting backtest: {run_name}")
        logger.info(f"Period: {config.start_date} to {config.end_date}")
        logger.info(f"Initial capital: {config.initial_capital:,.2f}")
        logger.info(f"Timeframe: {config.timeframe}")
        logger.info("=" * 60)

        # Load data
        data = self.load_historical_data(config)

        # Initialize components
        signal_agent = MockSignalAgent(config.symbol, config.timeframe)
        signal_agent.risk_per_trade = config.risk_per_trade
        signal_agent.max_position_size = config.max_position_size

        # Initialize portfolio
        portfolio = Portfolio(
            timestamp=data.index[0],
            btc_position=0.0,
            usdt_balance=config.initial_capital,
            total_value=config.initial_capital,
            equity=config.initial_capital,
            used_margin=0.0,
            free_equity=config.initial_capital,
            unrealized_pnl=0.0
        )

        trades = []
        portfolio_history = [portfolio]

        # Main simulation loop
        for i in range(1, len(data)):
            current_time = data.index[i]
            current_bar = data.iloc[i]

            # Update portfolio with current price
            current_price = current_bar['close']
            portfolio.btc_position = portfolio.btc_position  # No change yet
            portfolio.total_value = portfolio.usdt_balance + (portfolio.btc_position * current_price)
            portfolio.equity = portfolio.total_value

            # Generate signal
            signal_action, signal_confidence, signal_reasoning = signal_agent.generate_signal(current_bar.to_dict())

            # Execute signal
            if signal_action in ['BUY', 'SELL', 'CLOSE'] and signal_confidence >= config.confidence_threshold:
                # Calculate position size
                if signal_action == 'CLOSE':
                    position_value = portfolio.btc_position * current_price
                    commission = position_value * config.commission_rate
                    slippage_cost = position_value * config.slippage

                    # Close position
                    portfolio.usdt_balance += position_value - commission - slippage_cost
                    realized_pnl = position_value - portfolio_history[-1].btc_position * current_price

                    trade = Trade(
                        timestamp=current_time,
                        action='CLOSE',
                        price=current_price,
                        amount=portfolio.btc_position,
                        value=position_value,
                        pnl=realized_pnl,
                        confidence=signal_confidence,
                        reasoning=signal_reasoning,
                        portfolio_value=portfolio.total_value
                    )
                    trades.append(trade)
                    portfolio.btc_position = 0.0

                    # Set cooldown
                    signal_agent.last_close_time = current_time

                elif signal_action == 'BUY' and portfolio.btc_position == 0:
                    # Calculate position size
                    position_value = portfolio.equity * config.risk_per_trade * signal_confidence
                    position_value = min(position_value, config.max_position_size * current_price)

                    commission = position_value * config.commission_rate
                    slippage_cost = position_value * config.slippage
                    total_cost = position_value + commission + slippage_cost

                    if total_cost <= portfolio.usdt_balance:
                        btc_amount = position_value / current_price

                        trade = Trade(
                            timestamp=current_time,
                            action='BUY',
                            price=current_price,
                            amount=btc_amount,
                            value=position_value,
                            pnl=0.0,
                            confidence=signal_confidence,
                            reasoning=signal_reasoning,
                            portfolio_value=portfolio.total_value
                        )
                        trades.append(trade)

                        portfolio.btc_position += btc_amount
                        portfolio.usdt_balance -= total_cost

                elif signal_action == 'SELL' and portfolio.btc_position > 0:
                    # Close position (simple approach)
                    signal_agent.last_close_time = current_time
                    # This would trigger on next iteration

            # Store portfolio state
            portfolio_history.append(Portfolio(
                timestamp=current_time,
                btc_position=portfolio.btc_position,
                usdt_balance=portfolio.usdt_balance,
                total_value=portfolio.total_value,
                equity=portfolio.equity,
                used_margin=0.0,
                free_equity=portfolio.usdt_balance,
                unrealized_pnl=0.0
            ))

        # Calculate metrics
        metrics = self._calculate_metrics(config, trades, portfolio_history, data)

        # Save results
        if self.db_url:
            self._save_results(config, run_name, trades, metrics)

        # Log results
        self._log_results(metrics)

        return {
            'config': asdict(config),
            'metrics': metrics,
            'trades': [asdict(t) for t in trades],
            'portfolio_history': [asdict(p) for p in portfolio_history]
        }

    def _calculate_metrics(self, config: BacktestConfig, trades: List[Trade],
                          portfolio_history: List[Portfolio], data: pd.DataFrame) -> Dict:
        """Calculate performance metrics"""

        if not portfolio_history:
            return {}

        # Calculate returns
        initial_value = config.initial_capital
        final_value = portfolio_history[-1].total_value
        total_return = (final_value - initial_value) / initial_value * 100

        # Calculate daily returns
        portfolio_df = pd.DataFrame([asdict(p) for p in portfolio_history])
        portfolio_df['timestamp'] = pd.to_datetime(portfolio_df['timestamp'])
        portfolio_df.set_index('timestamp', inplace=True)

        daily_returns = portfolio_df['total_value'].resample('D').last().pct_change().dropna()

        # Risk metrics
        sharpe = (daily_returns.mean() * 252) / (daily_returns.std() * np.sqrt(252)) if daily_returns.std() > 0 else 0

        # Calculate drawdown
        rolling_max = portfolio_df['total_value'].expanding().max()
        drawdown = (portfolio_df['total_value'] - rolling_max) / rolling_max * 100
        max_drawdown = drawdown.min()

        # Trade metrics
        winning_trades = [t for t in trades if t.pnl > 0]
        losing_trades = [t for t in trades if t.pnl < 0]

        win_rate = len(winning_trades) / len(trades) * 100 if trades else 0

        avg_win = np.mean([t.pnl for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t.pnl for t in losing_trades]) if losing_trades else 0

        profit_factor = abs(sum(t.pnl for t in winning_trades) / sum(t.pnl for t in losing_trades)) if losing_trades else float('inf')

        # Benchmark (Buy and Hold)
        initial_price = data['close'].iloc[0]
        final_price = data['close'].iloc[-1]
        benchmark_return = (final_price - initial_price) / initial_price * 100

        # Time period
        trading_days = (config.end_date - config.start_date).days
        annual_return = (final_value / initial_value) ** (365 / trading_days) - 1 if trading_days > 0 else 0

        return {
            'initial_capital': initial_value,
            'final_capital': final_value,
            'total_return': total_return,
            'annual_return': annual_return * 100,
            'benchmark_return': benchmark_return,
            'excess_return': total_return - benchmark_return,
            'sharpe_ratio': sharpe,
            'max_drawdown': max_drawdown,
            'num_trades': len(trades),
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'trading_days': trading_days,
            'final_btc_position': portfolio_history[-1].btc_position,
            'final_usdt_balance': portfolio_history[-1].usdt_balance,
        }

    def _save_results(self, config: BacktestConfig, run_name: str, trades: List[Trade], metrics: Dict):
        """Save results to database"""
        try:
            session = self.Session()

            # Save backtest result
            from setup_database import BacktestResult, BacktestTrade

            result = BacktestResult(
                run_name=run_name,
                scenario_name='backtest',
                start_date=config.start_date,
                end_date=config.end_date,
                timeframe=config.timeframe,
                initial_capital=config.initial_capital,
                final_capital=metrics['final_capital'],
                total_return=metrics['total_return'],
                cagr=metrics['annual_return'],
                benchmark_return=metrics['benchmark_return'],
                excess_return=metrics['excess_return'],
                sharpe_ratio=metrics['sharpe_ratio'],
                max_drawdown=metrics['max_drawdown'],
                num_trades=metrics['num_trades'],
                win_rate=metrics['win_rate'],
                profit_factor=metrics['profit_factor'],
                parameters=json.dumps(asdict(config))
            )

            session.add(result)
            session.flush()  # Get ID

            # Save trades
            for trade in trades:
                trade_record = BacktestTrade(
                    backtest_id=result.id,
                    timestamp=trade.timestamp,
                    action=trade.action,
                    price=trade.price,
                    amount=trade.amount,
                    value=trade.value,
                    pnl=trade.pnl,
                    signal_confidence=trade.confidence,
                    glm_reasoning=trade.reasoning,
                    portfolio_value=trade.portfolio_value
                )
                session.add(trade_record)

            session.commit()
            logger.info(f"✅ Results saved to database (ID: {result.id})")

        except Exception as e:
            logger.error(f"❌ Error saving results: {e}")
        finally:
            session.close()

    def _log_results(self, metrics: Dict):
        """Log backtest results"""
        logger.info("=" * 60)
        logger.info("BACKTEST RESULTS")
        logger.info("=" * 60)
        logger.info(f"Total Return:      {metrics['total_return']:.2f}%")
        logger.info(f"Annual Return:     {metrics['annual_return']:.2f}%")
        logger.info(f"Benchmark Return:  {metrics['benchmark_return']:.2f}%")
        logger.info(f"Excess Return:     {metrics['excess_return']:.2f}%")
        logger.info(f"Sharpe Ratio:      {metrics['sharpe_ratio']:.2f}")
        logger.info(f"Max Drawdown:      {metrics['max_drawdown']:.2f}%")
        logger.info(f"Number of Trades:  {metrics['num_trades']}")
        logger.info(f"Win Rate:          {metrics['win_rate']:.1f}%")
        logger.info(f"Profit Factor:     {metrics['profit_factor']:.2f}")
        logger.info("=" * 60)

async def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Run backtest simulation')
    parser.add_argument('--run-name', default='test_backtest', help='Run name')
    parser.add_argument('--start-date', required=True, help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', required=True, help='End date (YYYY-MM-DD)')
    parser.add_argument('--timeframe', default='15m', help='Timeframe')
    parser.add_argument('--initial-capital', type=float, default=10000, help='Initial capital')
    parser.add_argument('--confidence-threshold', type=float, default=0.65, help='Confidence threshold')
    parser.add_argument('--risk-per-trade', type=float, default=0.02, help='Risk per trade')

    args = parser.parse_args()

    # Create config
    config = BacktestConfig(
        start_date=datetime.strptime(args.start_date, '%Y-%m-%d'),
        end_date=datetime.strptime(args.end_date, '%Y-%m-%d'),
        symbol='BTCUSDT',
        timeframe=args.timeframe,
        initial_capital=args.initial_capital,
        commission_rate=0.001,
        slippage=0.0005,
        max_position_size=1.0,
        confidence_threshold=args.confidence_threshold,
        risk_per_trade=args.risk_per_trade
    )

    # Run backtest
    engine = BacktestEngine()
    results = engine.run_backtest(config, args.run_name)

    print(json.dumps(results['metrics'], indent=2))

if __name__ == '__main__':
    asyncio.run(main())