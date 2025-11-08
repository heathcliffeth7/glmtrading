from datetime import date, datetime
from typing import Optional, List

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, create_engine, text, JSON
from sqlalchemy.orm import DeclarativeBase, Session

from app.config.settings import get_settings


settings = get_settings()


class Base(DeclarativeBase):
    pass


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(String(20), nullable=True)  # Her pozisyona unique ID (örn: POS-20251024-001)
    symbol = Column(String(20), nullable=False)
    side = Column(String(4), nullable=False)  # BUY/SELL (trade direction)
    position_side = Column(String(5), nullable=True)  # LONG/SHORT (position type)
    amount = Column(Float, nullable=False)
    price = Column(Float, nullable=False)  # Entry price (açılış fiyatı)
    close_price = Column(Float, nullable=True)  # NULL = pozisyon hala açık, değer varsa = kapalı
    close_time = Column(DateTime, nullable=True)  # Kapanış zamanı (UTC)
    leverage = Column(Float, default=1.0)  # Kaldıraç
    notional_value = Column(Float, nullable=True)  # price × amount × leverage
    fees = Column(Float, default=0.0)  # İşlem ücreti
    pnl = Column(Float, default=0.0)  # Realized PnL (kapanışta hesaplanır)
    exit_plan = Column(JSON, nullable=True)  # Nof1.ai style exit plan: {stop_loss, invalidation_condition} (profit_target removed - not required)
    exit_plan_history = Column(JSON, nullable=True)  # Tüm exit plan güncellemelerin logu: {"updates": [...]}
    timestamp = Column(DateTime, default=datetime.utcnow)


class Portfolio(Base):
    __tablename__ = "portfolio"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    long_position = Column(Float, default=0.0)
    long_avg_price = Column(Float, nullable=True)
    short_position = Column(Float, default=0.0)
    short_avg_price = Column(Float, nullable=True)
    net_position = Column(Float, default=0.0)
    last_trade_price = Column(Float, nullable=True)
    last_trade_timestamp = Column(DateTime, nullable=True)
    position = Column(Float, default=0.0)
    average_price = Column(Float, default=0.0)
    updated_at = Column(DateTime, default=datetime.utcnow)


class DailyPnL(Base):
    __tablename__ = "daily_pnl"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, unique=True)
    realized_pnl = Column(Float, default=0.0)
    unrealized_pnl = Column(Float, default=0.0)
    total_fees = Column(Float, default=0.0)  # Toplam işlem ücretleri


class PredictionLog(Base):
    """Active Learning: Her prediction'ı feature'larıyla birlikte kaydet"""
    __tablename__ = "prediction_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=True)
    symbol = Column(String(20), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Features - Futures metrics
    long_short_ratio = Column(Float)
    open_interest = Column(Float)
    funding_rate = Column(Float)
    
    # Features - Spot indicators (TA-Lib)
    ema_20 = Column(Float)
    ema_50 = Column(Float)
    rsi_14 = Column(Float)
    close_price = Column(Float)
    
    # Features - Twelve Data indicators
    rsi_twelvedata = Column(Float, nullable=True)
    macd_twelvedata = Column(Float, nullable=True)
    macd_signal_twelvedata = Column(Float, nullable=True)
    macd_hist_twelvedata = Column(Float, nullable=True)
    atr_twelvedata = Column(Float, nullable=True)
    stoch_k = Column(Float, nullable=True)
    stoch_d = Column(Float, nullable=True)
    bb_upper = Column(Float, nullable=True)
    bb_middle = Column(Float, nullable=True)
    bb_lower = Column(Float, nullable=True)

    # Model prediction
    model_score = Column(Float)
    predicted_direction = Column(String(4))
    confidence = Column(Float)

    # Actual result (sonradan doldurulacak)
    result_collected = Column(Boolean, default=False)
    minutes_after = Column(Integer, nullable=True)
    actual_price_change_pct = Column(Float, nullable=True)
    actual_direction = Column(String(4), nullable=True)

    # GLM feedback (sonradan doldurulacak)
    glm_feedback_collected = Column(Boolean, default=False)
    glm_correct = Column(Boolean, nullable=True)
    glm_important_feature = Column(String(50), nullable=True)
    glm_reasoning = Column(String(500), nullable=True)


class StopLossOrder(Base):
    """Stop-loss emirlerini takip et"""
    __tablename__ = "stop_loss_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False)
    position_id = Column(String(50), nullable=False)
    symbol = Column(String(20), nullable=False)
    
    entry_price = Column(Float, nullable=False)
    stop_loss_price = Column(Float, nullable=False)
    stop_loss_pct = Column(Float, nullable=False)  # % olarak
    
    is_active = Column(Boolean, default=True)
    triggered = Column(Boolean, default=False)
    triggered_at = Column(DateTime, nullable=True)
    triggered_price = Column(Float, nullable=True)
    
    # Trigger anında PnL bilgileri
    pnl_at_trigger = Column(Float, nullable=True)
    pnl_pct_at_trigger = Column(Float, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TakeProfitOrder(Base):
    """Take-profit emirlerini takip et"""
    __tablename__ = "take_profit_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False)
    position_id = Column(String(50), nullable=False)
    symbol = Column(String(20), nullable=False)
    
    entry_price = Column(Float, nullable=False)
    take_profit_price = Column(Float, nullable=False)
    take_profit_pct = Column(Float, nullable=False)  # % olarak
    
    is_active = Column(Boolean, default=True)
    triggered = Column(Boolean, default=False)
    triggered_at = Column(DateTime, nullable=True)
    triggered_price = Column(Float, nullable=True)
    
    # Trigger anında PnL bilgileri
    pnl_at_trigger = Column(Float, nullable=True)
    pnl_pct_at_trigger = Column(Float, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StopLossNotification(Base):
    """Stop-loss/take-profit bildirimlerini kaydet"""
    __tablename__ = "stop_loss_notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, nullable=False)  # SL veya TP order ID
    symbol = Column(String(20), nullable=False)
    
    notification_type = Column(String(20), nullable=False)  # STOP-LOSS, TAKE-PROFIT
    position_type = Column(String(10), nullable=False)  # LONG, SHORT
    
    position_amount = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    trigger_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=False)
    
    # PnL bilgileri
    pnl_amount = Column(Float, nullable=False)
    pnl_percentage = Column(Float, nullable=False)
    
    # Telegram bildirim durumu
    telegram_sent = Column(Boolean, default=False)
    telegram_sent_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)


class FeedbackSummary(Base):
    """GLM'in genel feedback'ini kaydet (retrain için)"""
    __tablename__ = "feedback_summary"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, unique=True, nullable=False)

    total_predictions = Column(Integer, default=0)
    correct_predictions = Column(Integer, default=0)
    glm_validated_count = Column(Integer, default=0)

    # Feature importance (GLM'in önerileri) - JSON string
    feature_weights = Column(String(500), nullable=True)


engine = create_engine(
    "postgresql://trading_user:trading_pass_2025@localhost/trading_db",
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)
Base.metadata.create_all(engine)


def get_portfolio(session: Session, symbol: str) -> Portfolio:
    """
    DEPRECATED: Use get_synced_portfolio from portfolio_sync module instead.
    This function does NOT synchronize with trades table and may return stale data.
    """
    import warnings
    warnings.warn(
        "get_portfolio is deprecated, use get_synced_portfolio from app.executor.portfolio_sync",
        DeprecationWarning,
        stacklevel=2
    )
    portfolio = session.query(Portfolio).filter_by(symbol=symbol).first()
    if portfolio is None:
        portfolio = Portfolio(symbol=symbol)
        session.add(portfolio)
        session.flush()
    return portfolio


def get_daily_pnl(session: Session, current_date: Optional[date] = None) -> DailyPnL:
    day = current_date or date.today()
    record = session.query(DailyPnL).filter_by(date=day).first()
    if record is None:
        record = DailyPnL(date=day)
        session.add(record)
        session.flush()
    return record


def generate_position_id() -> str:
    """Yeni pozisyon için unique ID üret"""
    from datetime import datetime
    # Format: POS-YYYYMMDD-NNN
    date_str = datetime.utcnow().strftime("%Y%m%d")
    # Basit sayaç - son pozisyon ID'sini bul ve artır
    try:
        with Session(engine) as counter_session:
            latest_trade = counter_session.query(Trade)\
                .filter(Trade.position_id.like(f"POS-{date_str}-%"))\
                .order_by(Trade.timestamp.desc())\
                .first()
            if latest_trade and latest_trade.position_id:
                # Son pozisyon ID'sini parçala ve artır
                parts = latest_trade.position_id.split("-")
                if len(parts) == 3 and parts[2].isdigit():
                    counter = int(parts[2]) + 1
                else:
                    counter = 1
            else:
                counter = 1
    except Exception:
        counter = 1
    
    return f"POS-{date_str}-{counter:03d}"


def record_trade(
    session: Session, 
    symbol: str, 
    side: str, 
    amount: float, 
    price: float, 
    pnl: float,
    leverage: float = 1.0,
    fees: float = 0.0,
    close_price: Optional[float] = None,
    position_side: Optional[str] = None,
    position_id: Optional[str] = None,
    exit_plan: Optional[dict] = None,
) -> Trade:
    """
    Record a trade
    
    Args:
        side: Trade direction (BUY/SELL)
        position_side: Position type (LONG/SHORT) - for futures
        leverage: Kaldıraç oranı
        fees: İşlem ücreti
        close_price: If position is being closed, set this to the closing price.
                     If None, position is still open (or being opened/increased).
    """
    # Standardize notional as unleveraged position value (amount × price)
    notional_value = price * amount if price and amount else None
    
    trade = Trade(
        position_id=position_id,
        symbol=symbol, 
        side=side, 
        position_side=position_side,
        amount=amount, 
        price=price, 
        close_price=close_price,
        leverage=leverage,
        notional_value=notional_value,
        fees=fees,
        pnl=pnl,
        exit_plan=exit_plan,
    )
    session.add(trade)
    return trade


def close_open_trades(
    session: Session,
    symbol: str,
    close_side: str,
    close_amount: float,
    close_price: float,
    taker_fee_rate: float | None = None,
) -> tuple[int, float, float]:
    """
    FIFO mantığıyla açık pozisyonları kapat ve close_price'larını güncelle.
    
    Args:
        symbol: Sembol (örn: BTCUSDT)
        close_side: Kapanış trade'inin yönü (BUY veya SELL)
        close_amount: Kapatılan miktar
        close_price: Kapanış fiyatı
    
    Returns:
        Tuple(updated_trade_count, realized_pnl_delta, total_closing_fee)
    """
    # Karşı yönü belirle (BUY ise SELL'leri kapat, SELL ise BUY'ları kapat)
    opposite_side = "SELL" if close_side == "BUY" else "BUY"
    
    # Açık olan karşı trade'leri bul (FIFO: en eskiden yeniye)
    open_trades = (
        session.query(Trade)
        .filter_by(symbol=symbol, side=opposite_side)
        .filter(Trade.close_price.is_(None))
        .order_by(Trade.timestamp.asc())
        .all()
    )
    
    from datetime import datetime as _dt
    remaining_amount = close_amount
    updated_count = 0
    realized_delta = 0.0
    closing_fee_total = 0.0
    fee_rate = float(taker_fee_rate or 0.0)
    
    for trade in open_trades:
        if remaining_amount <= 0:
            break

        original_amount = float(trade.amount or 0.0)
        if original_amount <= 0:
            continue

        close_part = min(remaining_amount, original_amount)
        if close_part <= 0:
            break

        now = _dt.utcnow()
        is_full_close = close_part >= original_amount - 1e-12
        ratio = 1.0 if is_full_close else close_part / original_amount

        open_fee_total = float(trade.fees or 0.0)
        closed_open_fee = open_fee_total * ratio
        remaining_open_fee = open_fee_total - closed_open_fee

        old_trade_pnl = float(trade.pnl or 0.0)
        if is_full_close:
            old_closed_pnl = old_trade_pnl
        else:
            old_closed_pnl = old_trade_pnl * ratio

        closing_fee = 0.0
        if fee_rate > 0:
            closing_fee = close_price * close_part * fee_rate
            closing_fee_total += closing_fee

        gross_pnl = (close_price - trade.price) * close_part if trade.side == "BUY" else (trade.price - close_price) * close_part
        new_pnl = gross_pnl - closed_open_fee - closing_fee

        # Trade değerlerini güncelle (kapalı kısım)
        trade.amount = close_part
        trade.close_price = close_price
        trade.close_time = now
        if trade.price and close_part:
            trade.notional_value = trade.price * close_part
        trade.fees = closed_open_fee + closing_fee
        trade.pnl = new_pnl

        # Kalan açık kısmı yeni trade olarak ekle
        if not is_full_close:
            open_remainder = original_amount - close_part
            remaining_pnl = old_trade_pnl - old_closed_pnl
            split = Trade(
                position_id=trade.position_id,
                symbol=trade.symbol,
                side=trade.side,
                position_side=trade.position_side,
                amount=open_remainder,
                price=trade.price,
                close_price=close_price,  # Kapanış fiyatı (kısmi kapanışta da aynı fiyat kullanılır)
                close_time=None,  # Bu kısım henüz kapanmadı
                leverage=trade.leverage,
                notional_value=(trade.price * open_remainder) if trade.price and open_remainder else None,
                fees=remaining_open_fee,
                pnl=remaining_pnl,
                timestamp=trade.timestamp,
            )
            session.add(split)

        delta = new_pnl - old_closed_pnl
        realized_delta += delta

        remaining_amount -= close_part
        updated_count += 1

        if remaining_amount <= 1e-12:
            break

    session.flush()
    return updated_count, realized_delta, closing_fee_total


def get_recent_trades(session: Session, symbol: str, limit: int = 5) -> list[Trade]:
    return (
        session.query(Trade)
        .filter_by(symbol=symbol)
        .order_by(Trade.timestamp.desc())
        .limit(limit)
        .all()
    )


def get_open_position_details(session: Session, symbol: str, current_price: float = None) -> dict:
    # ÖNCE PORTFOLIO'YU SENKRONİZE ET
    from app.executor.portfolio_sync import get_synced_portfolio
    
    portfolio = get_synced_portfolio(session, symbol)
    
    # Eğer current_price verilmemişse, Binance'den al
    if current_price is None:
        try:
            import httpx
            response = httpx.get(
                "https://fapi.binance.com/fapi/v1/ticker/price",
                params={"symbol": symbol},
                timeout=5.0
            )
            current_price = float(response.json()["price"])
        except Exception:
            current_price = portfolio.average_price if portfolio.average_price > 0 else 0.0  # Fallback
    
    if portfolio.position == 0:
        return {
            "position": 0.0,
            "average_price": 0.0,
            "current_price": current_price,
            "unrealized_pnl": 0.0,
            "unrealized_pnl_pct": 0.0,
            "position_value": 0.0,
        }
    
    unrealized_pnl = (current_price - portfolio.average_price) * portfolio.position
    unrealized_pnl_pct = ((current_price - portfolio.average_price) / portfolio.average_price) * 100 if portfolio.average_price > 0 else 0.0
    position_value = portfolio.position * current_price
    
    return {
        "position": portfolio.position,
        "average_price": portfolio.average_price,
        "current_price": current_price,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "position_value": position_value,
    }


def record_prediction(
    session: Session,
    symbol: str,
    features: dict,
    model_score: float,
    predicted_direction: str,
    confidence: float,
    trade_id: Optional[int] = None,
) -> PredictionLog:
    """Active Learning: Prediction'ı kaydet"""
    prediction = PredictionLog(
        trade_id=trade_id,
        symbol=symbol,
        # Futures
        long_short_ratio=features.get("long_short_ratio", 0.0),
        open_interest=features.get("open_interest", 0.0),
        funding_rate=features.get("funding_rate", 0.0),
        # Spot
        ema_20=features.get("ema_20", 0.0),
        ema_50=features.get("ema_50", 0.0),
        rsi_14=features.get("rsi_14", 50.0),
        close_price=features.get("close", 0.0),
        # Twelve Data
        rsi_twelvedata=features.get("rsi_twelvedata"),
        macd_twelvedata=features.get("macd_twelvedata"),
        macd_signal_twelvedata=features.get("macd_signal_twelvedata"),
        macd_hist_twelvedata=features.get("macd_hist_twelvedata"),
        atr_twelvedata=features.get("atr_twelvedata"),
        stoch_k=features.get("stoch_k"),
        stoch_d=features.get("stoch_d"),
        bb_upper=features.get("bb_upper"),
        bb_middle=features.get("bb_middle"),
        bb_lower=features.get("bb_lower"),
        # Prediction
        model_score=model_score,
        predicted_direction=predicted_direction,
        confidence=confidence,
    )
    session.add(prediction)
    session.flush()
    return prediction


def get_pending_predictions(session: Session, minutes_after: int = 15, limit: int = 100) -> list[PredictionLog]:
    """Actual result toplanmamış prediction'ları getir"""
    from datetime import timedelta
    
    cutoff_time = datetime.utcnow() - timedelta(minutes=minutes_after)
    
    return (
        session.query(PredictionLog)
        .filter(
            PredictionLog.result_collected == False,  # noqa: E712
            PredictionLog.timestamp <= cutoff_time,
        )
        .order_by(PredictionLog.timestamp.asc())
        .limit(limit)
        .all()
    )


def update_prediction_result(
    session: Session,
    prediction_id: int,
    actual_price_change_pct: float,
    actual_direction: str,
    minutes_after: int = 15,
) -> None:
    """Prediction'ın actual result'ını güncelle"""
    prediction = session.query(PredictionLog).filter_by(id=prediction_id).first()
    if prediction:
        prediction.result_collected = True
        prediction.minutes_after = minutes_after
        prediction.actual_price_change_pct = actual_price_change_pct
        prediction.actual_direction = actual_direction
        session.flush()


def update_prediction_glm_feedback(
    session: Session,
    prediction_id: int,
    glm_correct: bool,
    glm_important_feature: str,
    glm_reasoning: str,
) -> None:
    """Prediction'a GLM feedback'i ekle"""
    prediction = session.query(PredictionLog).filter_by(id=prediction_id).first()
    if prediction:
        prediction.glm_feedback_collected = True
        prediction.glm_correct = glm_correct
        prediction.glm_important_feature = glm_important_feature
        prediction.glm_reasoning = glm_reasoning[:500]  # Truncate
        session.flush()


def get_feedback_summary(session: Session, current_date: Optional[date] = None) -> FeedbackSummary:
    """Günlük feedback summary'yi getir veya oluştur"""
    day = current_date or date.today()
    record = session.query(FeedbackSummary).filter_by(date=day).first()
    if record is None:
        record = FeedbackSummary(date=day)
        session.add(record)
        session.flush()
    return record


def create_stop_loss_order(
    session: Session,
    trade_id: int,
    position_id: str,
    symbol: str,
    entry_price: float,
    stop_loss_pct: float = 0.005,  # %0.5 default
) -> "StopLossOrder":
    """Yeni stop-loss emri oluştur"""
    stop_loss_price = entry_price * (1 + stop_loss_pct)  # SHORT için entry'nin üstü
    
    order = StopLossOrder(
        trade_id=trade_id,
        position_id=position_id,
        symbol=symbol,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        stop_loss_pct=stop_loss_pct * 100,  # % olarak kaydet
    )
    session.add(order)
    session.flush()
    return order


def create_take_profit_order(
    session: Session,
    trade_id: int,
    position_id: str,
    symbol: str,
    entry_price: float,
    take_profit_pct: float = 0.015,  # %1.5 default
) -> "TakeProfitOrder":
    """Yeni take-profit emri oluştur"""
    take_profit_price = entry_price * (1 - take_profit_pct)  # SHORT için entry'nin altı
    
    order = TakeProfitOrder(
        trade_id=trade_id,
        position_id=position_id,
        symbol=symbol,
        entry_price=entry_price,
        take_profit_price=take_profit_price,
        take_profit_pct=take_profit_pct * 100,  # % olarak kaydet
    )
    session.add(order)
    session.flush()
    return order


def get_active_stop_loss_orders(session: Session, symbol: str) -> List["StopLossOrder"]:
    """Aktif stop-loss emirlerini getir"""
    return (
        session.query(StopLossOrder)
        .filter(
            StopLossOrder.symbol == symbol,
            StopLossOrder.is_active == True,  # noqa: E712
            StopLossOrder.triggered == False,  # noqa: E712
        )
        .all()
    )


def get_active_take_profit_orders(session: Session, symbol: str) -> List["TakeProfitOrder"]:
    """Aktif take-profit emirlerini getir"""
    return (
        session.query(TakeProfitOrder)
        .filter(
            TakeProfitOrder.symbol == symbol,
            TakeProfitOrder.is_active == True,  # noqa: E712
            TakeProfitOrder.triggered == False,  # noqa: E712
        )
        .all()
    )


def trigger_stop_loss_order(
    session: Session,
    order_id: int,
    triggered_price: float,
    pnl_amount: float,
    pnl_pct: float,
) -> None:
    """Stop-loss emrini tetikle"""
    order = session.query(StopLossOrder).filter_by(id=order_id).first()
    if order:
        order.triggered = True
        order.triggered_at = datetime.utcnow()
        order.triggered_price = triggered_price
        order.pnl_at_trigger = pnl_amount
        order.pnl_pct_at_trigger = pnl_pct
        order.is_active = False
        session.flush()


def trigger_take_profit_order(
    session: Session,
    order_id: int,
    triggered_price: float,
    pnl_amount: float,
    pnl_pct: float,
) -> None:
    """Take-profit emrini tetikle"""
    order = session.query(TakeProfitOrder).filter_by(id=order_id).first()
    if order:
        order.triggered = True
        order.triggered_at = datetime.utcnow()
        order.triggered_price = triggered_price
        order.pnl_at_trigger = pnl_amount
        order.pnl_pct_at_trigger = pnl_pct
        order.is_active = False
        session.flush()


def create_stop_loss_notification(
    session: Session,
    order_id: int,
    symbol: str,
    notification_type: str,
    position_type: str,
    position_amount: float,
    entry_price: float,
    trigger_price: float,
    current_price: float,
    pnl_amount: float,
    pnl_percentage: float,
    telegram_sent: bool = False,
) -> "StopLossNotification":
    """Stop-loss/take-profit bildirimi oluştur"""
    notification = StopLossNotification(
        order_id=order_id,
        symbol=symbol,
        notification_type=notification_type,
        position_type=position_type,
        position_amount=position_amount,
        entry_price=entry_price,
        trigger_price=trigger_price,
        current_price=current_price,
        pnl_amount=pnl_amount,
        pnl_percentage=pnl_percentage,
        telegram_sent=telegram_sent,
        telegram_sent_at=datetime.utcnow() if telegram_sent else None,
    )
    session.add(notification)
    session.flush()
    return notification


def get_recent_notifications(
    session: Session,
    symbol: str,
    limit: int = 10,
) -> List["StopLossNotification"]:
    """Son bildirimleri getir"""
    return (
        session.query(StopLossNotification)
        .filter(StopLossNotification.symbol == symbol)
        .order_by(StopLossNotification.created_at.desc())
        .limit(limit)
        .all()
    )


def get_open_position_with_exit_plan(session: Session, symbol: str) -> Optional[dict]:
    """
    Açık pozisyonu exit plan ile birlikte getir
    Position monitor için kullanılır
    
    Args:
        session: Database session
        symbol: Trading symbol (e.g., BTCUSDT)
    
    Returns:
        {
            'position_id': str,
            'trade_id': int,
            'quantity': float,
            'entry_price': float,
            'is_long': bool,
            'exit_plan': {
                'profit_target': float,
                'stop_loss': float,
                'invalidation_condition': str
            }
        } or None if no open position
    """
    from app.executor.portfolio_sync import get_synced_portfolio
    
    # FIX: Session cache'i temizle - güncel veriyi DB'den çek
    # Bu, dynamic exit plan güncellemelerinin görünmesini sağlar
    session.expire_all()
    
    # Portfolio'yu kontrol et
    portfolio = get_synced_portfolio(session, symbol)
    
    if abs(portfolio.position) < 0.0001:  # Pozisyon yok
        return None
    
    # En son açık trade'i bul
    open_trade = (
        session.query(Trade)
        .filter_by(symbol=symbol)
        .filter(Trade.close_price.is_(None))  # Açık pozisyon
        .order_by(Trade.timestamp.desc())
        .first()
    )
    
    if not open_trade:
        return None
    
    is_long = portfolio.position > 0
    
    return {
        "position_id": open_trade.position_id,
        "trade_id": open_trade.id,
        "quantity": abs(portfolio.position),
        "entry_price": open_trade.price,
        "is_long": is_long,
        "exit_plan": open_trade.exit_plan or {},
    }
