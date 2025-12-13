"""
Portfolio synchronization module for maintaining consistency between trades and portfolio tables.
This should be called before each trade execution to ensure accurate position tracking.
"""

import logging
import os
import pickle
import time
from datetime import datetime
from threading import Lock
from typing import Dict, Optional, Set, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.executor.ledger import Portfolio, Trade
try:
    from app.utils.price_cache import price_cache as _price_cache
except Exception:
    _price_cache = None

logger = logging.getLogger(__name__)

# Persistent cache file for abnormal trade IDs
_CACHE_FILE = "/tmp/abnormal_trade_cache.pkl"

def _load_abnormal_cache() -> Set[str]:
    """Load abnormal trade IDs from disk cache."""
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            logger.debug("Could not load abnormal trade cache: %s", e)
    return set()

def _save_abnormal_cache(cache: Set[str]) -> None:
    """Save abnormal trade IDs to disk cache."""
    try:
        with open(_CACHE_FILE, 'wb') as f:
            pickle.dump(cache, f)
    except Exception as e:
        logger.debug("Could not save abnormal trade cache: %s", e)

_abnormal_trade_ids = _load_abnormal_cache()

# In-memory portfolio cache for reducing DB load
_portfolio_cache: Dict[str, Tuple[Portfolio, float]] = {}  # {symbol: (portfolio, timestamp)}
_cache_lock = Lock()
_CACHE_TTL = 15.0  # 15 seconds TTL (reduced DB load for concurrent access)

# Constants for dynamic position limits
MAX_POSITION_USD = 60000.0  # $3.000 margin × 20x leverage
DEFAULT_BTC_PRICE = 100000.0
DEFAULT_ETH_PRICE = 3500.0
DEFAULT_SOL_PRICE = 150.0


def _get_dynamic_symbol_limit(symbol: str) -> float:
    """
    Get symbol-specific amount limit based on current price from WebSocket.
    Returns maximum amount in units (not USD) that respects the $60,000 position limit.
    """
    symbol_upper = (symbol or "").upper()

    # Try to get current price from WebSocket price cache first
    current_price = None
    if _price_cache:
        try:
            cached = _price_cache.get(symbol_upper, max_age_seconds=60)  # 1 minute freshness
            if cached and cached > 0:
                current_price = float(cached)
                logger.debug("Using WebSocket price for %s: $%.2f", symbol_upper, current_price)
        except Exception:
            logger.debug("WebSocket price unavailable for %s", symbol_upper)

    # Fallback to default prices if WebSocket unavailable
    if not current_price:
        if "BTC" in symbol_upper:
            current_price = DEFAULT_BTC_PRICE
        elif "ETH" in symbol_upper:
            current_price = DEFAULT_ETH_PRICE
        elif "SOL" in symbol_upper:
            current_price = DEFAULT_SOL_PRICE
        else:
            # Generic fallback for other symbols
            current_price = 100.0
        logger.info("Using fallback price for %s: $%.2f", symbol_upper, current_price)

    # Calculate max amount based on USD limit
    max_amount = MAX_POSITION_USD / current_price

    # Add buffer for price rounding/volatility - SOLANA için daha yüksek buffer
    if "SOL" in symbol_upper:
        max_amount *= 1.20  # SOL için %20 buffer (daha volatil)
    else:
        max_amount *= 1.05  # Diğerleri için %5 buffer

    logger.debug("Dynamic limit for %s: %.6f units (price: $%.2f, USD limit: $%.0f)",
                 symbol_upper, max_amount, current_price, MAX_POSITION_USD)

    return max_amount


def _get_price_hint(symbol: str, latest_trade: Optional[Trade]) -> Optional[float]:
    """
    Sağlıklı bir fiyat ipucu yakala. Önce bellek içi cache, sonra son trade fiyatı.
    """
    symbolsafe = (symbol or "").upper()
    if _price_cache:
        try:
            cached = _price_cache.get(symbolsafe, max_age_seconds=300)
            if cached:
                return float(cached)
        except Exception:
            logger.debug("Price cache hint unavailable for %s", symbolsafe)

    if latest_trade and latest_trade.price and latest_trade.price > 0:
        return float(latest_trade.price)
    return None


def _normalize_trade(trade: Trade, price_hint: Optional[float]) -> tuple[bool, float, float, list[str]]:
    """
    Temel tutarlılık kontrolleri yap. (usable, amount, price, warnings) döner.
    Fiyat aşırı sapıksa ve ipucu varsa fiyatı onarır.
    Negatif miktarları otomatik olarak pozitife çevirir.
    """
    raw_amount = float(trade.amount or 0.0)
    amount = abs(raw_amount)  # Always work with positive amount
    price = float(trade.price or 0.0)
    warnings: list[str] = []

    if amount <= 0:
        warnings.append("amount=0")
    if price <= 0:
        warnings.append("price<=0")

    # Use dynamic symbol-specific limit instead of hardcoded amount limit
    dynamic_limit = _get_dynamic_symbol_limit(trade.symbol)

    # Debug log for SOLANA
    if "SOL" in (trade.symbol or "").upper():
        logger.info("SOLANA trade debug - ID:%s, Amount:%.6f, Price:%.2f, Dynamic_Limit:%.6f, Price_Hint:%.2f",
                   getattr(trade, 'id', 'N/A'), amount, price, dynamic_limit, price_hint or 0.0)

    amount_over_limit = amount > dynamic_limit
    if amount_over_limit:
        warnings.append(f"amount>{dynamic_limit:.1f}")

    # Debug log for abnormal amounts to understand why it was rejected
    if amount_over_limit:
        logger.info(
            "Abnormal amount detected for %s: amount=%.6f > limit=%.6f (price=$%.2f, max_usd=$%.0f)",
            trade.symbol, amount, dynamic_limit, price, MAX_POSITION_USD
        )

    if price_hint and price > 0:
        diff_pct = abs(price - price_hint) / price_hint
        # SOLANA için daha esnek fiyat sapma eşiği
        threshold = 0.98
        if "SOL" in (trade.symbol or "").upper():
            threshold = 0.99  # SOL için %99 (daha volatil)

        if diff_pct > threshold:
            warnings.append(f"price_deviation:{diff_pct*100:.1f}% (hint={price_hint:.2f})")
            price = price_hint  # kullanılır bir fiyatla onar

    usable = amount > 0 and price > 0 and not amount_over_limit
    return usable, amount, price, warnings


def _log_abnormal(trade: Trade, amount: float, price: float, warnings: list[str], action: str) -> None:
    """Tekrarlı log spamini önlemek için aynı trade'i bir kez yaz ve disk'e kaydet."""
    trade_id = getattr(trade, "id", None)
    key = f"{trade_id}:{action}"
    if key in _abnormal_trade_ids:
        return

    # Only log if there are actual warnings - reduce noise
    if warnings:
        logger.info(
            "%s %s trade: amount=%.6f price=%.2f id=%s (%s)",
            action,
            trade.position_side or "UNKNOWN",
            amount,
            price,
            trade_id,
            "; ".join(warnings) if warnings else "no-details",
        )
    
    # Add to cache and persist to disk
    _abnormal_trade_ids.add(key)
    _save_abnormal_cache(_abnormal_trade_ids)


def calculate_actual_position_from_trades(session: Session, symbol: str) -> Dict[str, float]:
    """
    Gerçek long/short pozisyonları açık (close_price IS NULL) işlemler üzerinden hesapla.
    YENİ: Long ve short pozisyonları ayrı takip eder (hedge desteği).
    """

    # Yalnızca AÇIK işlemleri (close_price IS NULL) kronolojik sırada al
    open_trades = (
        session.query(Trade)
        .filter(Trade.symbol == symbol)
        .filter(Trade.close_price.is_(None))
        .order_by(Trade.timestamp.asc())
        .all()
    )

    latest_trade = (
        session.query(Trade)
        .filter(Trade.symbol == symbol)
        .order_by(Trade.timestamp.desc())
        .first()
    )
    price_hint = _get_price_hint(symbol, latest_trade)

    if not open_trades:
        return {
            "position": 0.0,
            "average_price": 0.0,
            "total_cost": 0.0,
            "long_position": 0.0,
            "long_avg_price": None,
            "short_position": 0.0,
            "short_avg_price": None,
            "net_position": 0.0,
            "last_trade_price": None,
            "last_trade_timestamp": None,
        }

    # Long ve short trade'leri ayır (position_side boşsa side'dan türet)
    long_trades = [
        t for t in open_trades
        if t.position_side == "LONG" or (not t.position_side and t.side == "BUY")
    ]
    short_trades = [
        t for t in open_trades
        if t.position_side == "SHORT" or (not t.position_side and t.side == "SELL")
    ]

    # Long pozisyon hesaplama
    long_position = 0.0
    long_total_cost = 0.0
    long_avg_price = None

    for trade in long_trades:
        usable, amount, price, warnings = _normalize_trade(trade, price_hint)
        if not usable:
            _log_abnormal(trade, amount, price, warnings, "Ignoring abnormal LONG")
            continue
        if warnings:
            _log_abnormal(trade, amount, price, warnings, "Adjusted LONG")

        if trade.side == "BUY":
            long_position += amount
            long_total_cost += amount * price

    if long_position > 0.0001:
        long_avg_price = long_total_cost / long_position

    # Short pozisyon hesaplama
    short_position = 0.0
    short_total_cost = 0.0
    short_avg_price = None

    for trade in short_trades:
        usable, amount, price, warnings = _normalize_trade(trade, price_hint)
        if not usable:
            _log_abnormal(trade, amount, price, warnings, "Ignoring abnormal SHORT")
            continue
        if warnings:
            _log_abnormal(trade, amount, price, warnings, "Adjusted SHORT")

        if trade.side == "SELL":
            short_position -= amount
            short_total_cost += amount * price

    if abs(short_position) > 0.0001:
        short_avg_price = short_total_cost / abs(short_position)

    # Net pozisyon hesapla
    net_position = long_position + short_position

    # Backward compatibility için eski position değeri
    position = net_position
    average_price = long_avg_price if long_avg_price else (short_avg_price if short_avg_price else 0.0)
    total_cost = long_total_cost + short_total_cost

    # Son işlem bilgisi (TÜM trade'lerden, açık olanlar da dahil)
    last_trade_price = float(latest_trade.price) if latest_trade else None
    last_trade_timestamp = latest_trade.timestamp if latest_trade else None

    return {
        "position": position,
        "average_price": average_price,
        "total_cost": total_cost,
        "long_position": long_position,
        "long_avg_price": long_avg_price,
        "short_position": short_position,
        "short_avg_price": short_avg_price,
        "net_position": net_position,
        "last_trade_price": last_trade_price,
        "last_trade_timestamp": last_trade_timestamp,
    }

# Lock retry configuration - daha fazla deneme, daha hızlı başlangıç
_MAX_LOCK_RETRIES = 5
_INITIAL_RETRY_DELAY = 0.2  # 200ms (0.2, 0.4, 0.8, 1.6, 3.2s = toplam ~6s)


def _get_advisory_lock_id(symbol: str) -> int:
    """Generate a consistent positive 32-bit integer lock ID for a symbol"""
    return abs(hash(symbol)) % (2**31)


def _acquire_symbol_advisory_lock(session: Session, symbol: str) -> bool:
    """
    Try to acquire PostgreSQL advisory lock for symbol.
    Returns True if lock acquired, False otherwise.
    Advisory locks are session-level and released when session closes.
    """
    lock_id = _get_advisory_lock_id(symbol)
    try:
        result = session.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": lock_id}
        ).scalar()
        return bool(result)
    except Exception as e:
        logger.debug("Failed to acquire advisory lock for %s: %s", symbol, e)
        return False


def _release_symbol_advisory_lock(session: Session, symbol: str) -> None:
    """Release PostgreSQL advisory lock for symbol"""
    lock_id = _get_advisory_lock_id(symbol)
    try:
        session.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": lock_id}
        )
    except Exception as e:
        logger.debug("Failed to release advisory lock for %s: %s", symbol, e)


def sync_portfolio_with_trades(session: Session, symbol: str) -> Portfolio:
    """
    Synchronize portfolio table with actual trades and return the correct portfolio state.
    Uses advisory locks + row-level locks with retry logic for better concurrency.
    """
    def _fallback_portfolio() -> Portfolio:
        # 1. First check in-memory cache for recent data
        with _cache_lock:
            if symbol in _portfolio_cache:
                cached_portfolio, cached_time = _portfolio_cache[symbol]
                age = time.time() - cached_time
                if age < 30:  # 30 saniye - daha taze data tercih et
                    logger.info("Using cached portfolio for %s (age: %.1fs) due to lock contention", symbol, age)
                    return cached_portfolio

        # 2. Try to read from DB without lock (read-only fallback)
        try:
            existing = session.query(Portfolio).filter(Portfolio.symbol == symbol).first()
            if existing:
                logger.debug("Using unlocked DB read for %s fallback", symbol)
                return existing
        except Exception:
            pass

        # 3. Return empty portfolio as last resort
        return Portfolio(
            symbol=symbol,
            position=0.0,
            average_price=0.0,
            long_position=0.0,
            long_avg_price=None,
            short_position=0.0,
            short_avg_price=None,
            net_position=0.0,
            last_trade_price=None,
            last_trade_timestamp=None,
        )

    # Keep lock waits bounded and give heavier work enough headroom.
    try:
        session.execute(text("SET LOCAL lock_timeout = '10s'"))
        session.execute(text("SET LOCAL statement_timeout = '30s'"))
    except Exception:
        logger.debug("Could not set local DB timeouts for portfolio sync")

    # Try to acquire advisory lock first (non-blocking coordination)
    advisory_lock_acquired = _acquire_symbol_advisory_lock(session, symbol)
    if not advisory_lock_acquired:
        logger.debug("Advisory lock not available for %s, proceeding with row lock only", symbol)

    # Use SKIP LOCKED with retry logic for better concurrency
    portfolio = None
    try:
        query = session.query(Portfolio).filter(Portfolio.symbol == symbol)

        for attempt in range(_MAX_LOCK_RETRIES):
            try:
                portfolio = query.with_for_update(skip_locked=True).first()
                if portfolio:
                    break  # Successfully acquired lock

                # Row is locked by another worker, retry with backoff
                if attempt < _MAX_LOCK_RETRIES - 1:
                    delay = _INITIAL_RETRY_DELAY * (2 ** attempt)  # 0.5s, 1s, 2s
                    logger.debug("Portfolio row locked for %s, retrying in %.1fs (attempt %d/%d)",
                                symbol, delay, attempt + 1, _MAX_LOCK_RETRIES)
                    time.sleep(delay)
                else:
                    logger.debug("Portfolio sync skipped after %d retries for %s", _MAX_LOCK_RETRIES, symbol)
                    return _fallback_portfolio()

            except OperationalError as e:
                session.rollback()
                if attempt < _MAX_LOCK_RETRIES - 1:
                    delay = _INITIAL_RETRY_DELAY * (2 ** attempt)
                    logger.debug("Lock contention for %s, retrying in %.1fs (attempt %d/%d): %s",
                                symbol, delay, attempt + 1, _MAX_LOCK_RETRIES, e)
                    time.sleep(delay)
                else:
                    logger.warning("Portfolio sync failed after %d retries for %s: %s",
                                  _MAX_LOCK_RETRIES, symbol, e)
                    return _fallback_portfolio()

        if not portfolio:
            portfolio = Portfolio(
                symbol=symbol,
                position=0.0,
                average_price=0.0,
                long_position=0.0,
                long_avg_price=None,
                short_position=0.0,
                short_avg_price=None,
                net_position=0.0,
                last_trade_price=None,
                last_trade_timestamp=None,
            )
            session.add(portfolio)

        actual_state = calculate_actual_position_from_trades(session, symbol)

        position_diff = abs(portfolio.position - actual_state["position"])
        long_diff = abs((portfolio.long_position or 0.0) - actual_state["long_position"])
        short_diff = abs((portfolio.short_position or 0.0) - actual_state["short_position"])

        sync_needed = (
            position_diff > 0.0001
            or long_diff > 0.0001
            or short_diff > 0.0001
        )

        if sync_needed:
            logger.info(
                "Portfolio sync needed for %s: "
                "position=%.6f→%.6f, long=%.6f→%.6f, short=%.6f→%.6f",
                symbol,
                portfolio.position,
                actual_state["position"],
                portfolio.long_position or 0.0,
                actual_state["long_position"],
                portfolio.short_position or 0.0,
                actual_state["short_position"],
            )

            portfolio.position = actual_state["position"]
            portfolio.average_price = actual_state["average_price"]
            portfolio.long_position = actual_state["long_position"]
            portfolio.long_avg_price = actual_state["long_avg_price"]
            portfolio.short_position = actual_state["short_position"]
            portfolio.short_avg_price = actual_state["short_avg_price"]
            portfolio.net_position = actual_state["net_position"]
            portfolio.last_trade_price = actual_state["last_trade_price"]
            portfolio.last_trade_timestamp = actual_state["last_trade_timestamp"]
            portfolio.updated_at = datetime.utcnow()

            session.flush()

            logger.info(
                "Portfolio synchronized for %s: "
                "long=%.6f@%.2f, short=%.6f@%.2f, net=%.6f",
                symbol,
                portfolio.long_position,
                portfolio.long_avg_price or 0.0,
                portfolio.short_position,
                portfolio.short_avg_price or 0.0,
                portfolio.net_position,
            )

        return portfolio
    except Exception as e:
        session.rollback()
        logger.warning("Portfolio sync error for %s, returning fallback: %s", symbol, e)
        return _fallback_portfolio()
    finally:
        # Always release advisory lock if we acquired it
        if advisory_lock_acquired:
            _release_symbol_advisory_lock(session, symbol)


def get_synced_portfolio(session: Session, symbol: str, force_sync: bool = False) -> Portfolio:
    """
    Get portfolio with automatic synchronization.
    Uses 15-second in-memory cache to reduce DB load.
    
    Args:
        session: Database session
        symbol: Trading symbol
        force_sync: If True, bypass cache and force DB sync (use after trades)
    
    Returns:
        Synced Portfolio object
    """
    now = time.time()
    
    # Check cache first (unless forced)
    if not force_sync:
        with _cache_lock:
            if symbol in _portfolio_cache:
                cached_portfolio, cached_time = _portfolio_cache[symbol]
                if now - cached_time < _CACHE_TTL:
                    logger.debug("Portfolio cache hit for %s (age: %.1fs)", symbol, now - cached_time)
                    return cached_portfolio
    
    # Cache miss or expired - sync from DB
    portfolio = sync_portfolio_with_trades(session, symbol)
    
    # Update cache
    with _cache_lock:
        _portfolio_cache[symbol] = (portfolio, now)
    
    return portfolio

def get_cache_stats() -> Dict[str, any]:
    """Return cache hit/miss statistics"""
    with _cache_lock:
        return {
            "cached_symbols": list(_portfolio_cache.keys()),
            "cache_size": len(_portfolio_cache),
            "ttl_seconds": _CACHE_TTL,
        }

def calculate_correct_margin_usage(portfolio: Portfolio, current_price: float, leverage: float) -> float:
    """
    Calculate correct margin usage based on current position and current price.
    Uses the synced portfolio position to ensure accuracy.
    """

    if leverage <= 0:
        leverage = 1.0

    position_value = abs(portfolio.position * current_price)
    used_margin = position_value / leverage if position_value > 0 else 0.0

    return used_margin

def validate_portfolio_consistency(session: Session, symbol: str) -> bool:
    """
    Validate that portfolio is consistent with trades.
    Returns True if consistent, False if issues detected.
    """

    portfolio = session.query(Portfolio).filter(Portfolio.symbol == symbol).first()
    if not portfolio:
        return True

    actual_state = calculate_actual_position_from_trades(session, symbol)
    position_diff = abs(portfolio.position - actual_state["position"])
    return position_diff <= 0.0001
