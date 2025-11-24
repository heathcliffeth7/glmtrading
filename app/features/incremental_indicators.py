"""
Incremental Indicator Calculator

Optimizes indicator calculation by:
1. Using incremental updates for fast-changing indicators (RSI, Bollinger)
2. Caching slow-moving indicators (EMA 50, EMA 200) - only recalculate on 1h/4h updates
3. Session-based VWAP calculation (resets at 00:00 UTC daily)

Reduces CPU load and InfluxDB traffic significantly.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
from typing import Dict, Optional, Tuple
import pandas as pd
import numpy as np

from app.utils.logging import get_logger


logger = get_logger(__name__)


@dataclass
class IndicatorState:
    """Cached state for incremental indicator calculation"""
    # RSI state
    rsi_avg_gain: float = 0.0
    rsi_avg_loss: float = 0.0
    rsi_prev_close: float = 0.0
    rsi_period: int = 14
    rsi_count: int = 0
    
    # EMA state (fast)
    ema_20_value: float = 0.0
    ema_20_initialized: bool = False
    
    # EMA state (slow - cache longer)
    ema_50_value: float = 0.0
    ema_50_initialized: bool = False
    ema_50_last_update: Optional[datetime] = None
    
    ema_200_value: float = 0.0
    ema_200_initialized: bool = False
    ema_200_last_update: Optional[datetime] = None
    
    # Bollinger Bands state
    bb_prices: list = field(default_factory=list)  # Last 20 prices for rolling calculation
    bb_period: int = 20
    
    # VWAP state (session-based)
    vwap_cum_pv: float = 0.0  # Cumulative price * volume
    vwap_cum_volume: float = 0.0  # Cumulative volume
    vwap_session_date: Optional[str] = None  # Track session (YYYY-MM-DD)


class IncrementalIndicatorCalculator:
    """
    Incremental indicator calculator with state caching
    
    Optimizations:
    - Fast indicators (RSI, BB): Update every bar with incremental calculation
    - Slow indicators (EMA 50, EMA 200): Cache and only recalculate on 1h/4h updates
    - VWAP: Session-based calculation (resets at 00:00 UTC)
    """
    
    def __init__(self, symbol: str, interval: str):
        self._symbol = symbol
        self._interval = interval
        self._state = IndicatorState()
        
        # Determine if this is a slow interval (1h, 4h) that should recalculate slow indicators
        self._is_slow_interval = interval in ["1h", "4h", "1d"]
    
    def calculate_incremental(
        self,
        close: float,
        high: float,
        low: float,
        volume: float,
        timestamp: datetime,
        force_full_recalc: bool = False
    ) -> Dict[str, float]:
        """
        Calculate indicators incrementally
        
        Args:
            close: Current close price
            high: Current high price
            low: Current low price
            volume: Current volume
            timestamp: Current bar timestamp
            force_full_recalc: Force full recalculation (e.g., on startup)
        
        Returns:
            Dict of calculated indicators
        """
        indicators = {}
        
        # === FAST INDICATORS (Always calculate) ===
        
        # 1. RSI (Incremental)
        rsi = self._calculate_rsi_incremental(close)
        indicators['rsi_14'] = rsi
        
        # 2. EMA 20 (Fast - always update)
        ema_20 = self._calculate_ema_incremental(close, 20, 'ema_20')
        indicators['ema_20'] = ema_20
        
        # 3. Bollinger Bands (Incremental with rolling window)
        bb_upper, bb_middle, bb_lower = self._calculate_bollinger_incremental(close)
        indicators['bb_upper'] = bb_upper
        indicators['bb_middle'] = bb_middle
        indicators['bb_lower'] = bb_lower
        
        # === SLOW INDICATORS (Cache and recalculate only on slow intervals or force) ===
        
        should_recalc_slow = force_full_recalc or self._is_slow_interval or self._should_recalc_slow_indicators(timestamp)
        
        if should_recalc_slow:
            # 4. EMA 50 (Slow - cache)
            ema_50 = self._calculate_ema_incremental(close, 50, 'ema_50')
            indicators['ema_50'] = ema_50
            self._state.ema_50_last_update = timestamp
            
            # 5. EMA 200 (Very slow - cache longer)
            if self._is_slow_interval or force_full_recalc:
                ema_200 = self._calculate_ema_incremental(close, 200, 'ema_200')
                indicators['ema_200'] = ema_200
                self._state.ema_200_last_update = timestamp
            else:
                # Use cached value
                indicators['ema_200'] = self._state.ema_200_value
        else:
            # Use cached values
            indicators['ema_50'] = self._state.ema_50_value
            indicators['ema_200'] = self._state.ema_200_value
        
        # === SESSION-BASED VWAP ===
        
        # 6. VWAP (Session-based, resets at 00:00 UTC)
        vwap = self._calculate_vwap_session_based(close, high, low, volume, timestamp)
        indicators['vwap'] = vwap
        
        return indicators
    
    def _calculate_rsi_incremental(self, close: float) -> float:
        """
        Calculate RSI incrementally using Wilder's smoothing
        
        Formula:
        - First RSI: SMA of gains/losses over period
        - Subsequent: Smoothed average = (prev_avg * (period-1) + current) / period
        """
        if self._state.rsi_prev_close == 0:
            # First bar - initialize
            self._state.rsi_prev_close = close
            return 50.0
        
        # Calculate price change
        change = close - self._state.rsi_prev_close
        gain = max(change, 0)
        loss = max(-change, 0)
        
        # Increment count
        self._state.rsi_count += 1
        
        if self._state.rsi_count < self._state.rsi_period:
            # Accumulating initial period
            self._state.rsi_avg_gain += gain
            self._state.rsi_avg_loss += loss
            self._state.rsi_prev_close = close
            return 50.0
        elif self._state.rsi_count == self._state.rsi_period:
            # First RSI calculation (SMA)
            self._state.rsi_avg_gain = (self._state.rsi_avg_gain + gain) / self._state.rsi_period
            self._state.rsi_avg_loss = (self._state.rsi_avg_loss + loss) / self._state.rsi_period
        else:
            # Incremental RSI (Wilder's smoothing)
            self._state.rsi_avg_gain = (self._state.rsi_avg_gain * (self._state.rsi_period - 1) + gain) / self._state.rsi_period
            self._state.rsi_avg_loss = (self._state.rsi_avg_loss * (self._state.rsi_period - 1) + loss) / self._state.rsi_period
        
        self._state.rsi_prev_close = close
        
        # Calculate RSI
        if self._state.rsi_avg_loss == 0:
            return 100.0
        
        rs = self._state.rsi_avg_gain / self._state.rsi_avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        
        return rsi
    
    def _calculate_ema_incremental(self, close: float, period: int, state_key: str) -> float:
        """
        Calculate EMA incrementally
        
        Formula: EMA = (Close - EMA_prev) * multiplier + EMA_prev
        where multiplier = 2 / (period + 1)
        """
        multiplier = 2.0 / (period + 1)
        
        if state_key == 'ema_20':
            if not self._state.ema_20_initialized:
                # First value - use close as initial EMA
                self._state.ema_20_value = close
                self._state.ema_20_initialized = True
                return close
            
            # Incremental update
            ema = (close - self._state.ema_20_value) * multiplier + self._state.ema_20_value
            self._state.ema_20_value = ema
            return ema
        
        elif state_key == 'ema_50':
            if not self._state.ema_50_initialized:
                self._state.ema_50_value = close
                self._state.ema_50_initialized = True
                return close
            
            ema = (close - self._state.ema_50_value) * multiplier + self._state.ema_50_value
            self._state.ema_50_value = ema
            return ema
        
        elif state_key == 'ema_200':
            if not self._state.ema_200_initialized:
                self._state.ema_200_value = close
                self._state.ema_200_initialized = True
                return close
            
            ema = (close - self._state.ema_200_value) * multiplier + self._state.ema_200_value
            self._state.ema_200_value = ema
            return ema
        
        return close
    
    def _calculate_bollinger_incremental(self, close: float) -> Tuple[float, float, float]:
        """
        Calculate Bollinger Bands incrementally using rolling window
        
        Formula:
        - Middle Band = SMA(20)
        - Upper Band = Middle + (2 * StdDev)
        - Lower Band = Middle - (2 * StdDev)
        """
        # Add new price to rolling window
        self._state.bb_prices.append(close)
        
        # Keep only last 20 prices
        if len(self._state.bb_prices) > self._state.bb_period:
            self._state.bb_prices.pop(0)
        
        if len(self._state.bb_prices) < self._state.bb_period:
            # Not enough data yet
            return close, close, close
        
        # Calculate SMA and StdDev
        prices_array = np.array(self._state.bb_prices)
        sma = np.mean(prices_array)
        std = np.std(prices_array, ddof=1)  # Sample standard deviation
        
        upper = sma + (2 * std)
        lower = sma - (2 * std)
        
        return upper, sma, lower
    
    def _calculate_vwap_session_based(self, close: float, high: float, low: float, volume: float, timestamp: datetime) -> float:
        """
        Calculate VWAP (Volume Weighted Average Price) with session-based reset
        
        VWAP resets at 00:00 UTC daily (Binance session start)
        
        Formula: VWAP = Σ(Typical Price * Volume) / Σ(Volume)
        where Typical Price = (High + Low + Close) / 3
        """
        # Get current session date (YYYY-MM-DD)
        current_session = timestamp.strftime("%Y-%m-%d")
        
        # Check if we need to reset (new session)
        if self._state.vwap_session_date != current_session:
            logger.info(f"📅 VWAP session reset: {self._state.vwap_session_date} → {current_session}")
            self._state.vwap_cum_pv = 0.0
            self._state.vwap_cum_volume = 0.0
            self._state.vwap_session_date = current_session
        
        # Calculate typical price
        typical_price = (high + low + close) / 3.0
        
        # Accumulate
        self._state.vwap_cum_pv += typical_price * volume
        self._state.vwap_cum_volume += volume
        
        # Calculate VWAP
        if self._state.vwap_cum_volume == 0:
            return close
        
        vwap = self._state.vwap_cum_pv / self._state.vwap_cum_volume
        
        return vwap
    
    def _should_recalc_slow_indicators(self, timestamp: datetime) -> bool:
        """
        Determine if slow indicators should be recalculated
        
        Recalculate if:
        - Never calculated before
        - More than 1 hour since last update
        """
        if self._state.ema_50_last_update is None:
            return True
        
        # Ensure both timestamps are timezone-aware for comparison
        ts = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
        last_update = self._state.ema_50_last_update
        if last_update.tzinfo is None:
            last_update = last_update.replace(tzinfo=timezone.utc)
        
        time_since_update = (ts - last_update).total_seconds()
        
        # Recalculate every hour
        return time_since_update >= 3600
    
    def initialize_from_history(self, df: pd.DataFrame) -> None:
        """
        Initialize indicator state from historical data
        
        Args:
            df: DataFrame with columns: close, high, low, volume, timestamp
        """
        if df.empty or len(df) < 50:
            logger.warning("Not enough historical data to initialize indicators (need 50+, got %d)", len(df))
            return
        
        logger.info("🔄 Initializing incremental indicators from %d historical bars", len(df))
        
        # Calculate full indicators on historical data
        closes = df['close'].values
        
        # Initialize RSI
        if len(closes) >= self._state.rsi_period:
            changes = np.diff(closes)
            gains = np.maximum(changes, 0)
            losses = np.maximum(-changes, 0)
            
            # Use last period for initial averages
            self._state.rsi_avg_gain = np.mean(gains[-self._state.rsi_period:])
            self._state.rsi_avg_loss = np.mean(losses[-self._state.rsi_period:])
            self._state.rsi_prev_close = closes[-1]
            self._state.rsi_count = self._state.rsi_period
        
        # Initialize EMAs (use SMA as starting point for better accuracy)
        if len(closes) >= 20:
            self._state.ema_20_value = np.mean(closes[-20:])
            self._state.ema_20_initialized = True
        
        if len(closes) >= 50:
            self._state.ema_50_value = np.mean(closes[-50:])
            self._state.ema_50_initialized = True
            self._state.ema_50_last_update = df['timestamp'].iloc[-1] if 'timestamp' in df.columns else datetime.now(timezone.utc)
        
        if len(closes) >= 200:
            self._state.ema_200_value = np.mean(closes[-200:])
            self._state.ema_200_initialized = True
            self._state.ema_200_last_update = self._state.ema_50_last_update
        
        # Initialize Bollinger Bands
        if len(closes) >= self._state.bb_period:
            self._state.bb_prices = list(closes[-self._state.bb_period:])
        
        # Initialize VWAP (session-based)
        if 'timestamp' in df.columns and 'volume' in df.columns and 'high' in df.columns and 'low' in df.columns:
            latest_timestamp = df['timestamp'].iloc[-1]
            self._state.vwap_session_date = latest_timestamp.strftime("%Y-%m-%d")
            
            # Calculate cumulative VWAP for current session
            session_df = df[df['timestamp'].dt.date == latest_timestamp.date()]
            if not session_df.empty:
                typical_prices = (session_df['high'] + session_df['low'] + session_df['close']) / 3.0
                self._state.vwap_cum_pv = (typical_prices * session_df['volume']).sum()
                self._state.vwap_cum_volume = session_df['volume'].sum()
        
        current_vwap = self._state.vwap_cum_pv / self._state.vwap_cum_volume if self._state.vwap_cum_volume > 0 else closes[-1]
        
        logger.info("✅ Incremental indicators initialized: RSI=%.2f, EMA20=%.2f, EMA50=%.2f, VWAP=%.2f",
                   self._calculate_rsi_incremental(closes[-1]),
                   self._state.ema_20_value,
                   self._state.ema_50_value,
                   current_vwap)
    
    def get_state(self) -> IndicatorState:
        """Get current indicator state for persistence"""
        return self._state
    
    def set_state(self, state: IndicatorState) -> None:
        """Restore indicator state from persistence"""
        self._state = state
        logger.info("♻️ Indicator state restored: RSI count=%d, EMA20=%.2f, EMA50=%.2f",
                   state.rsi_count, state.ema_20_value, state.ema_50_value)


class ThreadSafeIndicatorCalculator(IncrementalIndicatorCalculator):
    """
    Thread-safe version of IncrementalIndicatorCalculator
    
    Adds thread safety with reentrant locks to prevent race conditions
    when multiple threads access the same indicator state.
    
    Use this when:
    - Multiple timeframes are calculated in parallel
    - WebSocket and REST API updates happen concurrently
    - State persistence happens while calculations are running
    """
    
    def __init__(self, symbol: str, interval: str):
        super().__init__(symbol, interval)
        self._lock = threading.RLock()  # Reentrant lock allows same thread to acquire multiple times
    
    @contextmanager
    def _state_lock(self):
        """Context manager for thread-safe state operations"""
        self._lock.acquire()
        try:
            yield
        finally:
            self._lock.release()
    
    def calculate_incremental(
        self,
        close: float,
        high: float,
        low: float,
        volume: float,
        timestamp: datetime,
        force_full_recalc: bool = False
    ) -> Dict[str, float]:
        """Thread-safe incremental calculation"""
        with self._state_lock():
            return super().calculate_incremental(
                close, high, low, volume, timestamp, force_full_recalc
            )
    
    def initialize_from_history(self, df: pd.DataFrame) -> None:
        """Thread-safe initialization"""
        with self._state_lock():
            return super().initialize_from_history(df)
    
    def get_state(self) -> IndicatorState:
        """Thread-safe state export"""
        with self._state_lock():
            return super().get_state()
    
    def set_state(self, state: IndicatorState) -> None:
        """Thread-safe state import"""
        with self._state_lock():
            return super().set_state(state)
