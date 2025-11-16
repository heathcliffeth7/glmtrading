import json
import re
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Dict, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.executor.ledger import (
    DailyPnL, Portfolio, Trade, engine, get_daily_pnl, get_portfolio, record_trade, get_recent_trades,
    get_open_position_details, close_open_trades, generate_position_id,
    create_stop_loss_order, get_active_stop_loss_orders,
    trigger_stop_loss_order, create_stop_loss_notification, get_recent_notifications,
    StopLossOrder, StopLossNotification
)
from app.executor.portfolio_sync import get_synced_portfolio, calculate_correct_margin_usage
from app.risk_manager.manager import RiskDecision
from app.risk_manager.dynamic_risk_manager import DynamicRiskManager
from app.risk_manager.advanced_parser import AdvancedInvalidationParser
from app.utils.influx import query_latest
from app.utils.price_cache import ensure_price_cache_listener, price_cache
from app.utils.logging import get_logger
from app.utils.telegram import format_markdown, telegram_client


logger = get_logger(__name__)


@dataclass
class ExecutionResult:
    status: str
    details: str
    telemetry: Optional[dict] = None


class Executor:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        max_position: float = 1.0,
        max_daily_loss: float = 0.1,
        min_leverage: float = 1.0,
        max_leverage: float = 20.0,  # YENİ: Max 20x kaldıraç
        max_trade_value: float = 3000.0,  # YENİ: Her pozisyon için max $3000 margin
        taker_fee_rate: float = 0.0005,  # Binance USDT-M Futures Taker: 0.05%
    ) -> None:
        self._symbol = symbol
        self._max_position_per_side = max_position  # YENİ: Her yön için ayrı limit
        self._max_daily_loss = max_daily_loss
        self._min_leverage = min_leverage
        self._max_leverage = max_leverage
        self._max_margin_per_trade = max_trade_value  # YENİ: Rename for clarity
        self._starting_cash = 10000.0
        self._taker_fee_rate = taker_fee_rate
        
        # BACKWARD COMPATIBILITY
        self._max_position = max_position
        self._max_trade_value = max_trade_value
        
        # Global notional cap (sum of open positions' amount×price)
        self._max_total_notional_usd = self._starting_cash * self._max_leverage
        # Hard per-trade BTC cap (safety)
        self._max_btc_per_trade = 1.0
        # Reasonable price sanity range for BTCUSDT (display/guardrail only)
        self._min_price_sanity = 100.0
        self._max_price_sanity = 1_000_000.0
        
        # CLOSE sonrası cooldown mekanizması
        self._last_close_time = None
        self._close_cooldown_seconds = 0  # Cooldown kaldırıldı - hızlı müdahale için
        
        # STOP-LOSS sistemi
        self._stop_loss_enabled = True
        self._base_stop_loss_percent = 0.05  # %5 baz stop-loss (Nof1.ai standardı)

        # Log'daki pozisyona göre özel seviyeler (SHORT pozisyonu)
        self._current_entry_price = 111607.60  # Mevcut SHORT giriş fiyatı
        self._stop_loss_price_short = 112187.98  # Entry + %0.52 (stop-loss)
        
        # İşlem sıklığı kontrolü
        self._last_trade_direction = None
        self._last_trade_time = None
        self._same_direction_cooldown = 900  # 15 dakika aynı yöne işlem bekleme
        
        # YENİ: Dinamik risk yönetimi ve gelişmiş parser
        self._dynamic_risk_manager = DynamicRiskManager()
        self._advanced_parser = AdvancedInvalidationParser()
        
        ensure_price_cache_listener(self._symbol)
        self._last_trade_report_stats: Dict[str, int] = {"skipped_anomalies": 0}
        
        # WebSocket stop-loss monitoring
        self._stop_loss_monitoring_active = False
        self._last_sl_tp_notification = None

    def get_current_position_id(self, session: Session, symbol: str) -> Optional[str]:
        """DEPRECATED: Tek pozisyon varsayar. get_position_id_by_side kullanın."""
        from app.executor.ledger import Trade
        open_trade = (
            session.query(Trade)
            .filter_by(symbol=symbol)
            .filter(Trade.close_price.is_(None))
            .filter(Trade.position_id.isnot(None))
            .order_by(Trade.timestamp.desc())
            .first()
        )
        return open_trade.position_id if open_trade else None

    def get_position_id_by_side(self, session: Session, symbol: str, position_side: str) -> Optional[str]:
        """Belirtilen yön (LONG/SHORT) için açık pozisyon ID'sini bul"""
        from app.executor.ledger import Trade
        open_trade = (
            session.query(Trade)
            .filter_by(symbol=symbol, position_side=position_side)
            .filter(Trade.close_price.is_(None))
            .filter(Trade.position_id.isnot(None))
            .order_by(Trade.timestamp.desc())
            .first()
        )
        return open_trade.position_id if open_trade else None

    def get_all_open_position_ids(self, session: Session, symbol: str) -> Dict[str, str]:
        """Tüm açık pozisyon ID'lerini döndür {position_side: position_id}"""
        from app.executor.ledger import Trade
        open_trades = (
            session.query(Trade)
            .filter_by(symbol=symbol)
            .filter(Trade.close_price.is_(None))
            .filter(Trade.position_id.isnot(None))
            .all()
        )
        result = {}
        for trade in open_trades:
            if trade.position_side and trade.position_id:
                result[trade.position_side] = trade.position_id
        return result

    def _check_price_change_threshold(
        self, 
        portfolio: Portfolio, 
        current_price: float, 
        action: str
    ) -> tuple[bool, str]:
        """
        Son işlemden bu yana minimum %3 fiyat değişimi kontrolü.
        
        Args:
            portfolio: Güncel portfolio durumu
            current_price: Mevcut BTC fiyatı
            action: BUY/SELL/CLOSE
            
        Returns:
            (allowed: bool, reason: str)
        """
        MIN_PRICE_CHANGE_PERCENT = 3.0
        
        # İlk işlem kontrolü
        if not portfolio.last_trade_price:
            logger.info("✅ Price change check: First trade, no threshold required")
            return True, "First trade"
        
        # Fiyat değişimini hesapla
        price_change_pct = abs(
            (current_price - portfolio.last_trade_price) / portfolio.last_trade_price
        ) * 100.0
        
        if price_change_pct < MIN_PRICE_CHANGE_PERCENT:
            reason = (
                f"Fiyat değişimi %.2f%% < minimum %.2f%% "
                f"(son: ${portfolio.last_trade_price:.2f}, şu an: ${current_price:.2f})"
            ) % (price_change_pct, MIN_PRICE_CHANGE_PERCENT)
            logger.warning(
                "❌ BLOCKED by price change threshold: %.2f%% < %.2f%% (last: $%.2f, current: $%.2f)",
                price_change_pct,
                MIN_PRICE_CHANGE_PERCENT,
                portfolio.last_trade_price,
                current_price
            )
            return False, reason
        
        reason = (
            f"Fiyat değişimi yeterli: %.2f%% ≥ %.2f%% "
            f"(son: ${portfolio.last_trade_price:.2f}, şu an: ${current_price:.2f})"
        ) % (price_change_pct, MIN_PRICE_CHANGE_PERCENT)
        
        logger.info(
            "✅ Price change threshold OK: %.2f%% ≥ %.2f%% (last: $%.2f, current: $%.2f)",
            price_change_pct,
            MIN_PRICE_CHANGE_PERCENT,
            portfolio.last_trade_price,
            current_price
        )
        return True, reason

    def _calculate_volatility_adjusted_position_size(self, base_amount: float, volatility: float = 0.5) -> float:
        """Volatiliteye göre pozisyon boyutunu ayarla - $4000 max trade için optimize"""
        if volatility < 0.3:  # Düşük volatilite - daha büyük pozisyon
            multiplier = 1.3
        elif volatility < 0.6:  # Normal volatilite
            multiplier = 1.0
        elif volatility < 0.8:  # Yüksek volatilite
            multiplier = 0.8
        else:  # Çok yüksek volatilite
            multiplier = 0.6
        
        adjusted_amount = base_amount * multiplier
        logger.info(
            "Position size adjusted: %.4f → %.4f (volatility=%.2f, multiplier=%.2f)",
            base_amount,
            adjusted_amount,
            volatility,
            multiplier
        )
        return adjusted_amount

    def execute(self, decision: RiskDecision) -> ExecutionResult:
        reason = self.format_reason(decision.reasoning)
        logger.info(
            "Executing decision | action=%s | amount=%.4f | leverage=%.2f | reason=%s",
            decision.action,
            decision.amount,
            decision.leverage,
            reason,
        )
        
        # CLOSE ACTION: Strict validation with PnL-based rules
        if decision.action == "CLOSE":
            from app.executor.ledger import Trade
            
            # Get current position PnL for validation
            portfolio = get_synced_portfolio(session, self._symbol)
            current_pnl_pct = 0.0
            has_position = abs(portfolio.position) > 0.0001
            
            if has_position:
                # Calculate current PnL percentage
                entry_price = portfolio.long_avg_price if portfolio.position > 0 else portfolio.short_avg_price
                if entry_price > 0:
                    if portfolio.position > 0:  # LONG
                        current_pnl_pct = ((price - entry_price) / entry_price) * 100
                    else:  # SHORT
                        current_pnl_pct = ((entry_price - price) / entry_price) * 100
            
            # 1. Confidence kontrolü (PnL durumuna göre)
            min_confidence = 95.0  # Default minimum
            if has_position and current_pnl_pct > 2.0:  # Kârlı pozisyon (>%2)
                min_confidence = 98.0  # Daha katı kural
            
            if decision.glm_confidence < min_confidence:
                logger.warning(
                    "❌ CLOSE action rejected: GLM confidence %.1f%% < %.1f%% (required: %.1f%% for %s position)",
                    decision.glm_confidence,
                    min_confidence,
                    min_confidence,
                    "profitable" if current_pnl_pct > 2.0 else "normal"
                )
                return ExecutionResult(
                    status="INVALID",
                    details=f"CLOSE action rejected - confidence {decision.glm_confidence:.1f}% < {min_confidence:.1f}% required"
                )
            
            # 2. Küçük negatif PnL kontrolü (<%5 ve stop-loss tetiklenmemişse)
            if has_position and -5.0 < current_pnl_pct < 0:
                # Stop-loss kontrolü yap
                stop_loss_triggered = False
                latest_trade = (
                    session.query(Trade)
                    .filter_by(symbol=self._symbol)
                    .filter(Trade.close_price.is_(None))
                    .order_by(Trade.timestamp.desc())
                    .first()
                )
                
                if latest_trade and latest_trade.exit_plan:
                    stop_loss = latest_trade.exit_plan.get("stop_loss")
                    if stop_loss:
                        if portfolio.position > 0:  # LONG
                            if price <= stop_loss:
                                stop_loss_triggered = True
                        else:  # SHORT
                            if price >= stop_loss:
                                stop_loss_triggered = True
                
                if not stop_loss_triggered:
                    logger.warning(
                        "❌ CLOSE action rejected: Small negative PnL (%.2f%%) and stop-loss NOT triggered - must HOLD",
                        current_pnl_pct
                    )
                    return ExecutionResult(
                        status="INVALID",
                        details=f"CLOSE rejected - small negative PnL ({current_pnl_pct:.2f}%) and stop-loss not triggered - must HOLD"
                    )
            
            logger.info(
                "✅ CLOSE action validated: confidence=%.1f%% (min=%.1f%%), PnL=%.2f%% - proceeding to close",
                decision.glm_confidence,
                min_confidence,
                current_pnl_pct
            )
            # Continue to execution - CLOSE doesn't need exit_plan validation
        
        # VALIDATE EXIT PLAN: BUY/SELL requires valid exit_plan (CLOSE doesn't need it)
        if decision.action in ["BUY", "SELL"]:
            # Exit plan must exist
            if not decision.exit_plan:
                logger.error("BLOCKED: GLM must provide exit_plan for %s", decision.action)
                return ExecutionResult(
                    status="BLOCKED",
                    details="Exit plan eksik - GLM hatası"
                )
            
            # Exit plan values must be valid (profit_target removed - not required)
            stop_loss = decision.exit_plan.get("stop_loss")
            invalidation = decision.exit_plan.get("invalidation_condition")
            
            if not stop_loss or stop_loss == 0.0:
                logger.error("BLOCKED: Invalid stop_loss=%s for %s", stop_loss, decision.action)
                return ExecutionResult(
                    status="BLOCKED",
                    details="Stop loss geçersiz veya eksik"
                )
            
            logger.info(
                "✅ Exit plan validated: SL=%.2f INV=%s",
                stop_loss,
                invalidation or "N/A"
            )
        
        # === DECISION STALENESS CHECK ===
        # Reject decisions that are too old (default: 60 seconds)
        age = decision.age_seconds()
        logger.info("🔍 Decision age check: %.1fs old (max: 60s)", age)
        
        if decision.is_stale(max_age_seconds=60):
            logger.error(
                "❌ SKIP REASON: Decision is STALE (%.1fs old > 60s max) - market conditions may have changed",
                age
            )
            telegram_client.send_message(
                f"⚠️ TRADE SKIPPED: Decision too old ({age:.1f}s)\n"
                f"Action: {decision.action}\n"
                f"Market may have moved significantly"
            )
            return ExecutionResult(
                status="SKIPPED",
                details=f"Decision stale ({age:.1f}s old)"
            )
        
        # === PRICE FRESHNESS CHECK ===
        # Validate that we have fresh price data before executing
        if decision.action in ["BUY", "SELL", "CLOSE"]:
            logger.info("🔍 Checking price freshness for %s action...", decision.action)
            validated_price = self._get_current_price_validated(self._symbol)
            if validated_price is None:
                logger.error("❌ SKIP REASON: No fresh price available from data feed")
                
                # Send Telegram notification about price fetch failure
                cache_age = price_cache.get_age_seconds(self._symbol)
                try:
                    telegram_client.send_message(
                        f"⚠️ **İŞLEM ATLANDI - FİYAT VERİSİ YOK**\n\n"
                        f"**Action:** {decision.action}\n"
                        f"**Miktar:** {decision.amount:.4f} BTC\n"
                        f"**Kaldıraç:** {decision.leverage:.1f}x\n"
                        f"**Güven:** {decision.glm_confidence:.1f}%\n\n"
                        f"**Sorun:** Tüm fiyat kaynakları başarısız\n"
                        f"- WebSocket cache yaşı: {cache_age:.1f}s\n"
                        f"- REST API: Başarısız\n"
                        f"- InfluxDB: Başarısız\n\n"
                        f"**Durum:** WebSocket listener kontrol ediliyor...",
                        parse_mode="Markdown"
                    )
                except Exception as exc:
                    logger.error("Failed to send Telegram notification: %s", exc)
                
                return ExecutionResult(
                    status="SKIPPED",
                    details="No fresh price available"
                )
            logger.info("✅ Price is fresh: %.2f", validated_price)
        
        if decision.action == "HOLD" or decision.amount == 0:
            # HOLD kararında cooldown bilgisi ver
            logger.info("🔍 HOLD decision - checking close cooldown...")
            if self._last_close_time:
                elapsed = (datetime.utcnow() - self._last_close_time).total_seconds()
                remaining = self._close_cooldown_seconds - elapsed
                logger.info("   └─ Last close: %.0fs ago, cooldown: %.0fs (%.0fs remaining)", 
                           elapsed, self._close_cooldown_seconds, max(0, remaining))
                if elapsed < self._close_cooldown_seconds:
                    logger.info("❌ SKIP REASON: HOLD during close cooldown (%.0f/%.0f minutes remaining)", 
                               remaining/60, self._close_cooldown_seconds/60)
                    return ExecutionResult(
                        status="SKIP",
                        details=f"Hold kararı (cooldown: {int(remaining/60)} dakika {int(remaining%60)} saniye kaldı)"
                    )
            logger.info("✅ HOLD decision accepted")
            return ExecutionResult(status="SKIP", details="Hold kararı")

        # İŞLEM SIKLIĞI KONTROLÜ: Aynı yöne 15 dk ara
        if decision.action in ["BUY", "SELL"]:
            current_time = datetime.utcnow()
            logger.info("🔍 Checking same-direction trade cooldown for %s...", decision.action)
            logger.info("   └─ Last trade direction: %s, Last trade time: %s", 
                       self._last_trade_direction, self._last_trade_time)
            
            if (self._last_trade_direction == decision.action and 
                self._last_trade_time and 
                (current_time - self._last_trade_time).total_seconds() < self._same_direction_cooldown):
                
                elapsed = (current_time - self._last_trade_time).total_seconds()
                remaining = self._same_direction_cooldown - elapsed
                logger.warning(
                    "❌ SKIP REASON: Same-direction trade cooldown - %s blocked (%.0fs elapsed, %.0fs remaining)",
                    decision.action,
                    elapsed,
                    remaining
                )
                return ExecutionResult(
                    status="SKIP", 
                    details=f"{decision.action} atlandı (aynı yönde {int(remaining/60)} dk bekleme)"
                )
            
            logger.info("✅ Same-direction cooldown check passed")

        # ACİL GÜVENLİK KONTROLÜ: Portfolio astronomik mi?
        with Session(engine) as safety_session:
            safety_portfolio = get_synced_portfolio(safety_session, self._symbol)
            
            if abs(safety_portfolio.position) > 10.0:  # 10 BTC acil limit
                logger.critical(
                    "EMERGENCY: Position astronomical (%.6f BTC), forcing reset",
                    safety_portfolio.position
                )
                # Tüm açık pozisyonları kapat
                price = self._resolve_price()
                close_side = "SELL" if safety_portfolio.position > 0 else "BUY"
                _, _, _ = close_open_trades(
                    session=safety_session,
                    symbol=self._symbol,
                    close_side=close_side,
                    close_amount=abs(safety_portfolio.position),
                    close_price=price,
                    taker_fee_rate=self._taker_fee_rate,
                )
                safety_portfolio.position = 0.0
                safety_portfolio.average_price = 0.0
                safety_portfolio.updated_at = datetime.utcnow()
                safety_session.commit()
                return ExecutionResult(status="EMERGENCY_RESET", details="Astronomik pozisyon sıfırlandı")
        
        # COOLDOWN KONTROLÜ: CLOSE sonrası hemen işlem yapma
        if self._last_close_time and decision.action in ["BUY", "SELL"]:
            elapsed = (datetime.utcnow() - self._last_close_time).total_seconds()
            if elapsed < self._close_cooldown_seconds:
                remaining = self._close_cooldown_seconds - elapsed
                logger.info(
                    "COOLDOWN: Last CLOSE was %.0f seconds ago, waiting %.0f more seconds before opening new position",
                    elapsed,
                    remaining
                )
                return ExecutionResult(
                    status="COOLDOWN",
                    details=f"CLOSE sonrası cooldown: {int(remaining/60)} dakika {int(remaining%60)} saniye kaldı"
                )

        with Session(engine) as session:
            # Use synced portfolio to ensure accuracy before trade execution
            portfolio = get_synced_portfolio(session, self._symbol)
            daily_pnl = get_daily_pnl(session)

            pre_position = portfolio.position
            price = self._resolve_price()
            if price <= 0:
                logger.error("Invalid price: %.2f, skipping execution", price)
                return ExecutionResult(status="SKIP", details="Invalid price")
            # Price sanity guardrail for obviously wrong feeds
            if not (self._min_price_sanity <= price <= self._max_price_sanity):
                logger.error(
                    "Price out of sanity bounds: %.2f (%.2f..%.2f) — skipping",
                    price,
                    self._min_price_sanity,
                    self._max_price_sanity,
                )
                return ExecutionResult(status="SKIP", details="Price out of sanity bounds")
            
            # %3 FİYAT DEĞİŞİMİ KONTROLÜ: Gereksiz işlemleri engelle
            if decision.action in ["BUY", "SELL", "CLOSE"]:
                logger.info("🔍 Checking 3%% price change threshold for %s...", decision.action)
                allowed, reason = self._check_price_change_threshold(portfolio, price, decision.action)
                if not allowed:
                    logger.warning("❌ SKIP REASON: Price change threshold not met - %s", reason)
                    return ExecutionResult(
                        status="BLOCKED_PRICE_THRESHOLD",
                        details=reason
                    )
                logger.info("✅ Price change threshold passed: %s", reason)
            
            # NOTE: Position sizing is handled by Risk Manager's _apply_safety_limits()
            # which enforces max 3000 USD margin and max 20x leverage
            # No need for additional equity % cap here
            
            leverage = self._normalize_leverage(decision.leverage)
            
            # Toplam equity hesapla
            total_equity = self._starting_cash + daily_pnl.realized_pnl + daily_pnl.unrealized_pnl
            
            # GÜVENLIK KONTROLÜ: Negatif veya sıfır equity
            if total_equity <= 100:  # Minimum $100 gerekli
                logger.error(
                    "CRITICAL: Total equity too low or negative (%.2f) - halting trading",
                    total_equity
                )
                return ExecutionResult(status="SKIP", details=f"Yetersiz sermaye: ${total_equity:.2f}")
            
            # Pozisyon durumu kontrolü (YENİ: Long ve Short ayrı)
            has_long = portfolio.long_position > 0.0001
            has_short = portfolio.short_position < -0.0001
            
            # HEDGE LOGIC: Aynı yönde çift pozisyon engelleme + ters yön hedge izni
            if decision.action == "BUY":
                if has_long:
                    logger.warning(
                        "❌ BLOCKED: Long pozisyon zaten açık (%.6f BTC @ $%.2f), ikinci long açılamaz",
                        portfolio.long_position,
                        portfolio.long_avg_price or 0.0
                    )
                    return ExecutionResult(
                        status="BLOCKED_DUPLICATE_POSITION",
                        details=f"Long pozisyon zaten açık: {portfolio.long_position:.6f} BTC"
                    )
                elif has_short:
                    logger.info(
                        "🔀 HEDGE: Short pozisyon açıkken (%.6f BTC @ $%.2f) yeni LONG pozisyon açılıyor",
                        portfolio.short_position,
                        portfolio.short_avg_price or 0.0
                    )
                    position_side = "LONG"
                    is_closing = False
                else:
                    logger.info("📈 OPENING NEW LONG POSITION")
                    position_side = "LONG"
                    is_closing = False
                    
            elif decision.action == "SELL":
                if has_short:
                    logger.warning(
                        "❌ BLOCKED: Short pozisyon zaten açık (%.6f BTC @ $%.2f), ikinci short açılamaz",
                        portfolio.short_position,
                        portfolio.short_avg_price or 0.0
                    )
                    return ExecutionResult(
                        status="BLOCKED_DUPLICATE_POSITION",
                        details=f"Short pozisyon zaten açık: {portfolio.short_position:.6f} BTC"
                    )
                elif has_long:
                    logger.info(
                        "🔀 HEDGE: Long pozisyon açıkken (%.6f BTC @ $%.2f) yeni SHORT pozisyon açılıyor",
                        portfolio.long_position,
                        portfolio.long_avg_price or 0.0
                    )
                    position_side = "SHORT"
                    is_closing = False
                else:
                    logger.info("📉 OPENING NEW SHORT POSITION")
                    position_side = "SHORT"
                    is_closing = False
                    
            elif decision.action == "CLOSE":
                # CLOSE action için hangi pozisyon kapatılacak?
                # RiskDecision'da close_side varsa onu kullan, yoksa belirle
                close_side = getattr(decision, 'close_side', None)
                
                if not close_side:
                    # close_side yoksa, ikisi de açıksa en zararlı olanı kapat
                    if has_long and has_short:
                        long_pnl_pct = ((price - portfolio.long_avg_price) / portfolio.long_avg_price) * 100 if portfolio.long_avg_price else 0
                        short_pnl_pct = ((portfolio.short_avg_price - price) / portfolio.short_avg_price) * 100 if portfolio.short_avg_price else 0
                        
                        if long_pnl_pct < short_pnl_pct:
                            close_side = "LONG"
                            logger.info("🎯 Auto-selecting worst position: LONG (%.2f%% PnL)", long_pnl_pct)
                        else:
                            close_side = "SHORT"
                            logger.info("🎯 Auto-selecting worst position: SHORT (%.2f%% PnL)", short_pnl_pct)
                    elif has_long:
                        close_side = "LONG"
                    elif has_short:
                        close_side = "SHORT"
                    else:
                        logger.warning("❌ BLOCKED: No position to close")
                        return ExecutionResult(status="SKIP", details="Kapatılacak pozisyon yok")
                
                position_side = close_side
                is_closing = True
                logger.info(
                    "🔚 CLOSING %s POSITION (%.6f BTC @ $%.2f)",
                    close_side,
                    portfolio.long_position if close_side == "LONG" else abs(portfolio.short_position),
                    portfolio.long_avg_price if close_side == "LONG" else portfolio.short_avg_price
                )
            else:
                # HOLD veya bilinmeyen action
                logger.info("HOLD decision, no action taken")
                return ExecutionResult(status="SKIP", details="Hold kararı")
            
            # POZİSYON KAPATMA PATH
            if is_closing:
                # Kapatılacak pozisyon miktarını belirle
                if position_side == "LONG":
                    max_closeable = portfolio.long_position
                    entry_price = portfolio.long_avg_price
                else:  # SHORT
                    max_closeable = abs(portfolio.short_position)
                    entry_price = portfolio.short_avg_price
                
                if max_closeable < 0.0001:
                    logger.warning("❌ No %s position to close!", position_side)
                    return ExecutionResult(status="SKIP", details=f"Kapatılacak {position_side} pozisyon yok")
                
                # decision.amount: kapatılacak oran (1.0 = %100)
                btc_amount = min(max_closeable, max_closeable * decision.amount)
                
                logger.info(
                    "CLOSING %s position: max=%.6f, amount=%.6f, price=$%.2f",
                    position_side,
                    max_closeable,
                    btc_amount,
                    price
                )
                
                # ERKEN RETURN: Kapatma işlemi
                return self._execute_closing_trade(
                    session, portfolio, daily_pnl, price, decision,
                    reason, portfolio.net_position, btc_amount, leverage, position_side
                )
            
            # YENİ POZİSYON AÇMA PATH
            else:
                # DOĞRU MARGIN HESABI: Portfolio sync ile doğru pozisyon kullanılıyor
                used_margin = calculate_correct_margin_usage(portfolio, price, leverage)
                
                # Realized PnL = Sadece kapalı pozisyonların net PnL'i (Trade tablosundan gerçek zamanlı)
                closed_trades_net_pnl = session.query(
                    func.coalesce(func.sum(Trade.pnl), 0.0)
                ).filter(
                    Trade.symbol == self._symbol,
                    Trade.close_price.isnot(None)  # Sadece kapalı pozisyonlar
                ).scalar() or 0.0
                
                safe_equity = self._starting_cash + closed_trades_net_pnl
                safe_equity = max(100, min(safe_equity, self._starting_cash * 10))  # Max 10x büyüme
                
                free_equity = safe_equity - used_margin
                
                if free_equity <= 0:
                    logger.error(
                        "No free equity available: safe_equity=%.2f used_margin=%.2f free=%.2f",
                        safe_equity,
                        used_margin,
                        free_equity,
                    )
                    return ExecutionResult(status="SKIP", details="Serbest sermaye yok")
                
                # DOĞRU KALDIRAÇ HESAPLAMASI:
                # 1. Margin = equity'nin ne kadarını teminat olarak ayıralım (decision.amount)
                # 2. Position size = Margin × Leverage (kaldıraç ile büyütülmüş pozisyon)
                # 3. BTC amount = Position size / BTC price
                
                margin_to_use = free_equity * decision.amount  # Örn: 10,000 × 0.3 = 3,000 USDT
                
                # Max MARGIN kontrolü ($3000 margin limiti)
                # Risk Manager already enforces this, but double-check as safety net
                if margin_to_use > self._max_trade_value:
                    margin_to_use = self._max_trade_value
                    logger.warning(
                        "Margin limited to $%.2f (Risk Manager should have handled this)",
                        self._max_trade_value
                    )
                
                position_size_usd = margin_to_use * leverage   # Örn: 2,000 × 20 = 40,000 USDT
                btc_amount = position_size_usd / price          # Örn: 40,000 / 100,000 = 0.4 BTC

                # Sanity debug log of raw sizing inputs/outputs
                logger.debug(
                    "Sizing sanity | safe_equity=%.2f used_margin=%.2f amount=%.4f leverage=%.2f price=%.2f -> position_usd=%.2f btc=%.6f",
                    safe_equity,
                    used_margin,
                    decision.amount,
                    leverage,
                    price,
                    position_size_usd,
                    btc_amount,
                )
                
                # GÜVENLIK KONTROLÜ: Maximum BTC per trade
                MAX_BTC_PER_TRADE = self._max_btc_per_trade  # Tek işlemde max 1 BTC
                if btc_amount > MAX_BTC_PER_TRADE:
                    logger.warning(
                        "BTC amount capped: requested=%.6f capped=%.6f",
                        btc_amount,
                        MAX_BTC_PER_TRADE
                    )
                    btc_amount = MAX_BTC_PER_TRADE
                    position_size_usd = btc_amount * price  # Recalculate position size
                
                # SON GÜVENLİK KONTROLÜ: Total pozisyon limiti
                total_position_after = abs(portfolio.position) + btc_amount
                if total_position_after > self._max_position:
                    # Limiti aşıyorsa, sadece kalan kadar aç
                    btc_amount = max(0, self._max_position - abs(portfolio.position))
                    if btc_amount < 0.0001:
                        logger.warning(
                            "Position limit reached: current=%.6f limit=%.6f",
                            abs(portfolio.position),
                            self._max_position
                        )
                        return ExecutionResult(status="SKIP", details="Pozisyon limiti doldu")
                    position_size_usd = btc_amount * price  # Recalculate position size
                
                # NOTIONAL CAP GUARDRAIL: Sum of open notional must not exceed cap
                try:
                    open_trades = (
                        session.query(Trade)
                        .filter_by(symbol=self._symbol)
                        .filter(Trade.close_price.is_(None))
                        .all()
                    )
                    current_total_notional = sum(abs(t.amount) * t.price for t in open_trades)
                except Exception:
                    current_total_notional = abs(portfolio.position) * price  # fallback

                allowed_remaining = self._max_total_notional_usd - current_total_notional
                if allowed_remaining <= 0:
                    logger.warning(
                        "Notional cap reached: current=%.2f cap=%.2f",
                        current_total_notional,
                        self._max_total_notional_usd,
                    )
                    self._notify_guardrail_block(
                        decision,
                        reason,
                        current_position=pre_position,
                        requested_amount=btc_amount,
                        leverage=leverage,
                    )
                    return ExecutionResult(status="BLOCKED", details="Toplam notional limiti dolu")

                new_notional = btc_amount * price
                if new_notional > allowed_remaining + 1e-8:
                    # Trim BTC amount to fit remaining notional
                    trimmed_btc = max(0.0, allowed_remaining / price)
                    if trimmed_btc < 0.0001:
                        logger.warning(
                            "Notional cap blocks trade: requested=%.6f BTC allowed=%.6f BTC",
                            btc_amount,
                            trimmed_btc,
                        )
                        self._notify_guardrail_block(
                            decision,
                            reason,
                            current_position=pre_position,
                            requested_amount=btc_amount,
                            leverage=leverage,
                        )
                        return ExecutionResult(status="BLOCKED", details="Notional limiti nedeniyle engellendi")
                    logger.warning(
                        "Notional trimmed: requested=%.6f -> %.6f (remaining $%.2f)",
                        btc_amount,
                        trimmed_btc,
                        allowed_remaining,
                    )
                    btc_amount = trimmed_btc
                    position_size_usd = btc_amount * price
                
                logger.info(
                    "OPENING position: total_equity=%.2f free=%.2f margin_to_use=%.2f (%.1f%%) leverage=%.1fx position_size=%.2f btc_amount=%.6f",
                    total_equity,
                    free_equity,
                    margin_to_use,
                    decision.amount * 100,
                    leverage,
                    position_size_usd,
                    btc_amount,
                )
            
            # Guardrail kontrolü - pozisyon miktarı ile
            if not self._check_guardrails(portfolio, btc_amount, total_equity):
                self._notify_guardrail_block(
                    decision,
                    reason,
                    current_position=pre_position,
                    requested_amount=btc_amount,
                    leverage=leverage,
                )
                return ExecutionResult(status="BLOCKED", details="Guardrail engelledi")
            
            # Calculate trading fee (on position size, not margin)
            notional_value = position_size_usd
            fee = notional_value * self._taker_fee_rate
            
            # Calculate PnL (before fee)
            pnl_before_fee = self._calculate_pnl(portfolio, decision.action, btc_amount, price)
            
            # Subtract fee from PnL
            pnl = pnl_before_fee - fee
            
            # Determine is_long and is_short from position_side (needed for logging)
            is_long = (position_side == "LONG")
            is_short = (position_side == "SHORT")
            
            # SAFETY CHECK: Bu noktada is_closing=False olmalı
            if is_closing:
                logger.error(
                    "CRITICAL LOGIC ERROR: is_closing is True in opening path! "
                    "portfolio=%.6f action=%s is_long=%s is_short=%s",
                    portfolio.position,
                    decision.action,
                    is_long,
                    is_short
                )
                # Force to opening position to prevent bug
                is_closing = False
            
            # LOG: Opening position decision
            position_type = "LONG" if is_long else "SHORT" if is_short else "FLAT"
            logger.info(
                "POSITION OPENING - portfolio=%.6f %s action=%s btc_amount=%.6f",
                portfolio.position,
                position_type,
                decision.action,
                btc_amount if 'btc_amount' in locals() else 0.0
            )
            
            # Yeni pozisyon açılıyor veya mevcut pozisyon artırılıyor
            close_price = None  # Henüz kapatılmadı
            
            # Determine position side (LONG/SHORT)
            # - If BUY, we're opening/adding to LONG
            # - If SELL, we're opening/adding to SHORT
            if decision.action == "BUY":
                position_side = "LONG"
            else:
                position_side = "SHORT"
            
            # Pozisyon ID yönetimi (is_closing durumunda zaten dönmüştük)
            current_position_id = None
            if pre_position == 0:
                # Yeni pozisyon açılıyor - yeni ID oluştur
                current_position_id = generate_position_id()
            else:
                # Mevcut pozisyonu artırıyor - mevcut pozisyon ID'sini kullan
                current_position_id = self.get_current_position_id(session, self._symbol)
            
            logger.info(
                "Opening trade: notional=%.2f fee_rate=%.4f%% fee=%.2f pnl_before=%.2f pnl_after=%.2f leverage=%.1fx position_side=%s position_id=%s",
                notional_value,
                self._taker_fee_rate * 100,
                fee,
                pnl_before_fee,
                pnl,
                leverage,
                position_side,
                current_position_id,
            )
            
            # YENİ TRADE KAYDET (pozisyon açma veya artırma)
            # VOLATILITEYE GÖRE POZISYON BOYUTU AYARLAMA
            volatility = 0.5  # Basit volatilite tahmini (daha gelişmiş yöntem eklenebilir)
            adjusted_amount = self._calculate_volatility_adjusted_position_size(btc_amount, volatility)
            
            # EXIT PLAN: GLM'in verdiği exit_plan MUTLAKA kullanılmalı
            is_long = position_side == "LONG"
            
            # Extract exit plan values
            stop_loss = decision.exit_plan.get("stop_loss") if decision.exit_plan else None
            
            # Değerler geçersizse fallback exit plan kullan
            has_valid_stop_loss = stop_loss is not None and stop_loss != 0.0
            
            if not has_valid_stop_loss:
                logger.error(
                    "CRITICAL: GLM exit_plan has invalid stop_loss: %s",
                    stop_loss
                )
                logger.warning("⚠️ Using FALLBACK exit plan calculation (GLM failed to provide valid exit plan)")
                
                # FALLBACK: Calculate default exit plan
                # Simple volatility-based risk: 2% stop loss only
                sl_pct = 0.02  # 2% stop loss

                if position_side == "LONG":
                    stop_loss = price * (1 - sl_pct)
                    # Invalidation BETWEEN stop_loss and entry (50% distance = early warning)
                    invalidation_price = stop_loss + (price - stop_loss) * 0.5
                    invalidation_condition = f"If price closes below {invalidation_price:.2f} on 3-minute candle"
                else:  # SHORT
                    stop_loss = price * (1 + sl_pct)
                    # Invalidation BETWEEN entry and stop_loss (50% distance = early warning)
                    invalidation_price = price + (stop_loss - price) * 0.5
                    invalidation_condition = f"If price closes above {invalidation_price:.2f} on 3-minute candle"
                
                logger.warning(
                    "✅ FALLBACK exit plan created: SL=%.2f (-%.1f%%), Invalidation=%s",
                    stop_loss,
                    sl_pct * 100,
                    invalidation_condition
                )
                
                # Send Telegram notification about fallback usage
                try:
                    from app.utils.telegram import telegram_client, format_markdown
                    
                    fallback_message = (
                        "⚠️ *FALLBACK EXIT PLAN USED*\n\n"
                        f"GLM did not provide valid exit plan for {decision.action} action.\n\n"
                        "*GLM provided:*\n"
                        f"  • Stop Loss: {decision.exit_plan.get('stop_loss') if decision.exit_plan else 'None'}\n\n"
                        "*System calculated fallback:*\n"
                        f"  • Stop Loss: ${stop_loss:,.2f} (-{sl_pct*100:.1f}%)\n"
                        f"  • Invalidation: {invalidation_condition}\n\n"
                        f"*Action:* {decision.action} {position_side}\n"
                        f"*Entry Price:* ${price:,.2f}\n\n"
                        "💡 *Note:* Please review GLM prompt to ensure exit plan requirements are clear."
                    )
                    
                    telegram_client.send_message(fallback_message)
                    logger.info("✅ Fallback notification sent to Telegram")
                except Exception as exc:
                    logger.warning("Failed to send fallback notification: %s", exc)
                    # Don't fail on notification error
            
            # GLM'nin exit_plan'ı geçerli, kullan
            exit_plan = {
                "stop_loss": stop_loss,
                "invalidation_condition": decision.exit_plan.get("invalidation_condition", ""),
            }
            
            # GELİŞMİŞ VALIDATION: Exit planını doğrula
            if position_side and price:
                is_valid, validation_error = self._validate_exit_plan(
                    exit_plan, position_side, price
                )
                
                if not is_valid:
                    logger.error(
                        "❌ EXIT PLAN VALIDATION FAILED: %s",
                        validation_error
                    )
                    
                    # FALLBACK: Calculate default exit plan
                    logger.warning("⚠️ Using FALLBACK exit plan due to validation failure")

                    sl_pct = 0.02  # 2% stop loss

                    if position_side == "LONG":
                        stop_loss = price * (1 - sl_pct)
                        # Invalidation BETWEEN stop_loss and entry (50% distance = early warning)
                        invalidation_price = stop_loss + (price - stop_loss) * 0.5
                        invalidation_condition = f"If price closes below {invalidation_price:.2f} on 3-minute candle"
                    else:  # SHORT
                        stop_loss = price * (1 + sl_pct)
                        # Invalidation BETWEEN entry and stop_loss (50% distance = early warning)
                        invalidation_price = price + (stop_loss - price) * 0.5
                        invalidation_condition = f"If price closes above {invalidation_price:.2f} on 3-minute candle"

                    exit_plan = {
                        "stop_loss": stop_loss,
                        "invalidation_condition": invalidation_condition,
                    }
                    
                    logger.warning(
                        "✅ FALLBACK exit plan created: SL=%.2f (%.1f%%), Invalidation=%s",
                        stop_loss,
                        sl_pct * 100,
                        invalidation_condition
                    )
                    
                    # Telegram bildirimi gönder
                    try:
                        from app.utils.telegram import telegram_client, format_markdown
                        
                        fallback_message = (
                            "⚠️ *EXIT PLAN VALIDATION FAILED - FALLBACK USED*\n\n"
                            f"GLM exit plan failed validation: {validation_error}\n\n"
                            "*GLM provided:*\n"
                            f"  • Stop Loss: {decision.exit_plan.get('stop_loss') if decision.exit_plan else 'None'}\n"
                            f"  • Invalidation: {decision.exit_plan.get('invalidation_condition', 'None') if decision.exit_plan else 'None'}\n\n"
                            "*System calculated fallback:*\n"
                            f"  • Stop Loss: ${stop_loss:,.2f} (-{sl_pct*100:.1f}%)\n"
                            f"  • Invalidation: {invalidation_condition}\n\n"
                            f"*Action:* {decision.action} {position_side}\n"
                            f"*Entry Price:* ${price:,.2f}\n\n"
                            "💡 *Note:* Exit plan validation failed - please review GLM response format"
                        )
                        
                        telegram_client.send_message(fallback_message)
                        logger.info("✅ Fallback notification sent to Telegram")
                    except Exception as exc:
                        logger.warning("Failed to send fallback notification: %s", exc)
            
            logger.info(
                "✅ Using validated exit plan: stop_loss=%.2f invalidation=%s",
                stop_loss or 0.0,
                exit_plan.get("invalidation_condition", "N/A")
            )
            
            trade = record_trade(
                session,
                symbol=self._symbol,
                side=decision.action,
                amount=adjusted_amount,
                price=price,
                pnl=pnl,
                leverage=leverage,
                fees=fee,
                position_side=position_side,
                position_id=current_position_id,
                exit_plan=exit_plan,
            )
            session.flush()

            # WebSocket monitoring'i başlat (eğer henüz başlatılmadıysa)
            if not self._stop_loss_monitoring_active:
                try:
                    self.start_stop_loss_monitoring()
                    logger.info("✅ WebSocket stop-loss/take-profit monitoring started")
                except Exception as e:
                    logger.warning("Failed to start WebSocket monitoring: %s", e)

            # İŞLEM SIKLIĞI KONTROLÜ - Son işlemi kaydet
            self._last_trade_direction = decision.action
            self._last_trade_time = datetime.utcnow()

            self._update_portfolio(portfolio, decision.action, adjusted_amount, price)
            post_position = portfolio.position
            self._update_daily_pnl(daily_pnl, pnl, portfolio, price, fee)
            session.flush()

            trade_summary = SimpleNamespace(
                amount=trade.amount,
                price=trade.price,
                pnl=trade.pnl,
                side=trade.side,
                timestamp=trade.timestamp,
                fee=fee,
                notional=notional_value,
                close_price=trade.close_price,
                position_side=trade.position_side,
            )
            portfolio_summary = SimpleNamespace(
                position=portfolio.position,
                average_price=portfolio.average_price,
            )
            daily_summary = SimpleNamespace(
                realized_pnl=daily_pnl.realized_pnl,
                unrealized_pnl=daily_pnl.unrealized_pnl,
            )

            session.commit()

        position_status = self._describe_position_change(pre_position, post_position)
        # Telegram mesajı orchestrator tarafından gönderiliyor, buradan göndermiyoruz
        # self._notify_telegram(trade_summary, portfolio_summary, daily_summary, leverage, reason, position_status)
        
        # Telemetry for position opening operations
        telemetry = {
            "last_action": position_side,  # LONG or SHORT
            "amount": adjusted_amount,
            "price": price,
            "position_side": position_side,
            "position_id": current_position_id,
            "fee": fee,
            "pnl": pnl,  # Will be 0 for opening trades
        }
        return ExecutionResult(status="PAPER", details="Paper trading kaydedildi", telemetry=telemetry)

    def _check_guardrails(self, portfolio: Portfolio, btc_amount: float, equity: float) -> bool:
        """
        Guardrail kontrolleri:
        1. Tek işlemde max BTC limiti
        2. Total pozisyon limiti
        3. Equity sanity check
        """
        # 1. HARD LIMIT: Tek işlemde max 1 BTC
        if btc_amount > 1.0:
            logger.warning(
                "Single trade BTC limit exceeded: requested=%.6f limit=1.0",
                btc_amount,
                extra={"skip_telegram": True},
            )
            return False
        
        # 2. Total pozisyon kontrolü (BTC cinsinden)
        new_total_position = abs(portfolio.position) + btc_amount
        if new_total_position > self._max_position:
            logger.warning(
                "Total position limit exceeded: current=%.6f + new=%.6f = %.6f > limit=%.6f",
                abs(portfolio.position),
                btc_amount,
                new_total_position,
                self._max_position,
                extra={"skip_telegram": True},
            )
            return False
        
        # 3. Equity sanity check - astronomik değerleri engelle
        if equity > self._starting_cash * 10:
            logger.warning(
                "Equity too high (%.2f), possible calculation error. Max allowed: %.2f",
                equity,
                self._starting_cash * 10,
                extra={"skip_telegram": True},
            )
            return False
        
        return True

    def _get_current_price_validated(self, symbol: str, max_age_seconds: int = 10) -> Optional[float]:
        """
        Get current price with comprehensive fallback chain.
        
        Fallback chain:
        1. WebSocket cache (fresh: < 10s)
        2. REST API (Binance with retry)
        3. InfluxDB (may be delayed)
        4. Stale cache (emergency: < 60s)
        
        Args:
            symbol: Trading symbol (e.g., BTCUSDT)
            max_age_seconds: Maximum acceptable age for fresh data (default 10s)
        
        Returns:
            Price if available, None if all sources fail
        """
        logger.info("🔍 Fetching price for %s (max_age=%ds)...", symbol, max_age_seconds)
        
        # SOURCE 1: Try fresh cache first (WebSocket data)
        cached_price = price_cache.get(symbol, max_age_seconds=max_age_seconds)
        if cached_price is not None:
            logger.info("✅ Price from WebSocket cache: %.2f (fresh)", cached_price)
            return cached_price
        
        # Get cache age for diagnostics
        cache_age = price_cache.get_age_seconds(symbol)
        logger.warning(
            "⚠️ WebSocket cache miss for %s (age: %.1fs)",
            symbol, cache_age
        )
        
        # SOURCE 2: REST API as fallback (with built-in retry)
        logger.info("🔄 Trying REST API fallback...")
        rest_price = self._resolve_price()
        
        if rest_price > 0:
            # Update cache for next time
            price_cache.set(symbol, rest_price, source="rest_fallback")
            logger.info("✅ Price from REST API: %.2f", rest_price)
            return rest_price
        
        logger.error("❌ REST API fallback failed")
        
        # SOURCE 3: Try InfluxDB directly (in case _resolve_price's InfluxDB also failed)
        logger.info("🔄 Trying InfluxDB direct fallback...")
        try:
            from app.utils.influx import query_latest_snapshot
            snapshot = query_latest_snapshot("features_1m", symbol, "1m")
            if snapshot and "close" in snapshot:
                influx_price = float(snapshot.get("close", 0.0))
                if influx_price > 0:
                    price_cache.set(symbol, influx_price, source="influx_fallback")
                    logger.warning("⚠️ Price from InfluxDB: %.2f", influx_price)
                    return influx_price
        except Exception as exc:
            logger.error("❌ InfluxDB direct fallback failed: %s", exc)
        
        # SOURCE 4: Emergency - use stale cache (up to 60 seconds old)
        logger.warning("🚨 EMERGENCY: All fresh sources failed, checking stale cache...")
        stale_price = price_cache.get(symbol, max_age_seconds=60)
        if stale_price is not None:
            logger.warning(
                "⚠️ Using STALE cache price: %.2f (age: %.1fs) - EMERGENCY FALLBACK",
                stale_price, cache_age
            )
            return stale_price
        
        # All sources exhausted
        logger.critical(
            "❌ CRITICAL: NO PRICE AVAILABLE from any source (cache age: %.1fs)",
            cache_age
        )
        return None
    
    def _resolve_price(self) -> float:
        """
        Resolve current price with retry mechanism and multiple fallbacks.
        
        Strategy:
        1. Try Binance REST API (3 attempts with exponential backoff)
        2. Fallback to InfluxDB if Binance fails
        3. Return 0.0 if all sources fail
        """
        import httpx
        import time
        
        # Primary: Binance real-time price with retry
        max_attempts = 3
        base_timeout = 5.0
        
        for attempt in range(1, max_attempts + 1):
            timeout = base_timeout * attempt  # 5s, 10s, 15s
            try:
                start_time = time.time()
                response = httpx.get(
                    "https://fapi.binance.com/fapi/v1/ticker/price",
                    params={"symbol": self._symbol},
                    timeout=timeout
                )
                response.raise_for_status()
                data = response.json()
                price = float(data["price"])
                elapsed = time.time() - start_time
                
                if price > 0:
                    logger.info(
                        "✅ Binance API price: %.2f (attempt %d/%d, %.2fs)",
                        price, attempt, max_attempts, elapsed
                    )
                    return price
                    
            except httpx.ConnectTimeout as exc:
                logger.warning(
                    "⚠️ Binance API connection timeout (attempt %d/%d, timeout=%.1fs): %s",
                    attempt, max_attempts, timeout, exc
                )
            except httpx.ReadTimeout as exc:
                logger.warning(
                    "⚠️ Binance API read timeout (attempt %d/%d, timeout=%.1fs): %s",
                    attempt, max_attempts, timeout, exc
                )
            except httpx.RequestError as exc:
                logger.warning(
                    "⚠️ Binance API request error (attempt %d/%d): %s",
                    attempt, max_attempts, exc
                )
            except Exception as exc:
                logger.warning(
                    "⚠️ Binance API unexpected error (attempt %d/%d): %s",
                    attempt, max_attempts, exc
                )
            
            # Exponential backoff between retries (except on last attempt)
            if attempt < max_attempts:
                backoff = 2 ** (attempt - 1)  # 1s, 2s
                logger.debug("Retrying in %.1fs...", backoff)
                time.sleep(backoff)
        
        logger.error("❌ Binance API failed after %d attempts", max_attempts)
        
        # Fallback: InfluxDB (may be delayed)
        try:
            from app.utils.influx import query_latest_snapshot
            snapshot = query_latest_snapshot("features_1m", self._symbol, "1m")
            if snapshot and "close" in snapshot:
                price = float(snapshot.get("close", 0.0))
                if price > 0:
                    logger.warning("⚠️ Using FALLBACK price from InfluxDB: %.2f", price)
                    return price
        except Exception as exc:
            logger.error("❌ InfluxDB fallback also failed: %s", exc)
        
        logger.error("❌ Could not resolve valid price from ANY source")
        return 0.0

    def _calculate_pnl(self, portfolio: Portfolio, action: str, amount: float, price: float) -> float:
        """
        Realized PnL hesaplama:
        - Yeni pozisyon açılıyorsa veya artırılıyorsa: PnL = 0
        - Mevcut pozisyon kapatılıyor/azaltılıyorsa: PnL hesaplanır
        
        LONG pozisyon (position > 0):
          - BUY: pozisyon artırma → PnL = 0
          - SELL: pozisyon kapatma → PnL = (sell_price - avg_price) * amount
        
        SHORT pozisyon (position < 0):
          - SELL: pozisyon artırma → PnL = 0  
          - BUY: pozisyon kapatma → PnL = (avg_price - buy_price) * amount
        """
        if portfolio.position == 0:
            # Yeni pozisyon açılıyor, henüz realize olmadı
            return 0.0
        
        # Pozisyonun yönünü belirle
        is_long = portfolio.position > 0
        is_closing = (is_long and action == "SELL") or (not is_long and action == "BUY")
        
        if not is_closing:
            # Pozisyon artırılıyor, henüz realize PnL yok
            return 0.0
        
        # Pozisyon kapatılıyor/azaltılıyor - Realized PnL hesapla
        if is_long:
            # LONG pozisyon kapatılıyor: kar = sell price - avg price
            return (price - portfolio.average_price) * amount
        else:
            # SHORT pozisyon kapatılıyor: kar = avg price - buy price
            return (portfolio.average_price - price) * amount

    def _update_portfolio(self, portfolio: Portfolio, action: str, amount: float, price: float) -> None:
        """
        Portföy güncelleme:
        - Pozisyon artırılıyorsa: weighted average price hesapla
        - Pozisyon azaltılıyorsa: average price değişmez (kısmi kapatma) veya sıfırlanır (tam kapatma)
        - Pozisyon yön değiştiriyorsa: yeni pozisyon price'ı average price olur
        """
        old_position = portfolio.position
        
        # Pozisyon değişimi
        if action == "BUY":
            portfolio.position += amount
        else:  # SELL
            portfolio.position -= amount
        
        # Average price güncelleme mantığı
        if old_position == 0:
            # Yeni pozisyon açılıyor (LONG veya SHORT)
            portfolio.average_price = price
        elif (old_position > 0 and action == "BUY") or (old_position < 0 and action == "SELL"):
            # Pozisyon artırılıyor (LONG artırma veya SHORT artırma) - weighted average
            total_cost = abs(old_position) * portfolio.average_price + amount * price
            portfolio.average_price = total_cost / abs(portfolio.position)
        elif portfolio.position == 0:
            # Pozisyon tamamen kapandı
            portfolio.average_price = 0.0
        elif (old_position > 0 and portfolio.position < 0) or (old_position < 0 and portfolio.position > 0):
            # Pozisyon yön değiştirdi (LONG'dan SHORT'a veya SHORT'dan LONG'a)
            portfolio.average_price = price
        # else: Pozisyon azaltılıyor (kısmi kapatma), average price değişmez
        
        portfolio.updated_at = datetime.utcnow()

    def _update_daily_pnl(self, daily_pnl: DailyPnL, pnl: float, portfolio: Portfolio, price: float, fee: float = 0.0) -> None:
        daily_pnl.realized_pnl += pnl
        daily_pnl.unrealized_pnl = (price - portfolio.average_price) * portfolio.position
        if fee > 0:
            daily_pnl.total_fees += fee

    def _notify_guardrail_block(
        self,
        decision: RiskDecision,
        reason: str,
        current_position: float,
        requested_amount: float,
        leverage: float,
    ) -> None:
        if not telegram_client.enabled():
            raise RuntimeError("Telegram bildirimi devre dışı bırakılamaz")

        new_position = abs(current_position + requested_amount)
        message = "\n".join(
            [
                "*🚫 İşlem Engellendi (Guardrail)*",
                f"📊 Sembol: {format_markdown(self._symbol)}",
                f"{'📈' if decision.action == 'BUY' else '📉'} Talep Yön: {format_markdown(decision.action)}",
                f"📊 Risk Oranı: {decision.amount * 100:.1f}% equity",
                f"⚡ Kaldıraç: {leverage:.1f}x",
                f"📦 Kaldıraçlı Miktar: {requested_amount:.4f} BTC",
                "",
                "*⚠️ Pozisyon Durumu*",
                f"📍 Mevcut Pozisyon: {current_position:+.4f} BTC",
                f"🎯 İstenen Yeni Pozisyon: {new_position:.4f} BTC",
                f"🔴 Limit: {self._max_position:.4f} BTC",
                f"❌ Limit Aşımı: {(new_position - self._max_position):.4f} BTC",
                "",
                f"💬 Gerekçe: {format_markdown(reason)}",
            ]
        )
        try:
            telegram_client.send_message(message)
        except Exception as exc:  # noqa: BLE001
            logger.error("Telegram guardrail notify failed: %s", exc)

    def _notify_telegram(
        self,
        trade: SimpleNamespace,
        portfolio: SimpleNamespace,
        daily_pnl: SimpleNamespace,
        leverage: float,
        reason: str,
        position_status: str,
    ) -> None:
        if not telegram_client.enabled():
            raise RuntimeError("Telegram bildirimi devre dışı bırakılamaz")
        # Use sanitized portfolio metrics to avoid astronomical display values
        metrics = self.portfolio_metrics()
        equity = metrics.get("equity", self._starting_cash)
        equity_pct = ((equity - self._starting_cash) / self._starting_cash) * 100
        position_value = abs(metrics.get("position", 0.0) * metrics.get("price", trade.price))
        margin_used = metrics.get("margin_used", 0.0)
        free_cash = metrics.get("free_cash", max(0.0, equity - margin_used))
        free_cash_pct = (free_cash / equity * 100) if equity > 0 else 0.0

        # Get fee and notional from trade summary
        fee = getattr(trade, 'fee', 0)
        notional = getattr(trade, 'notional', trade.amount * trade.price)
        
        close_price = getattr(trade, 'close_price', None)
        is_close_trade = close_price is not None
        position_side = getattr(trade, 'position_side', '') or ''

        # Emoji seçimi ve action text
        if is_close_trade:
            direction_emoji = '🔒'
            side_label = position_side if position_side else ('LONG' if trade.side == 'SELL' else 'SHORT')
            action_text = f"{side_label} POZİSYON KAPATILDI"
        elif trade.side == 'BUY':
            direction_emoji = '📈'
            action_text = "LONG AÇILDI"
        else:  # SELL
            direction_emoji = '📉'
            action_text = "SHORT AÇILDI"
        
        # Generate timestamp header like BTC_ANALYZER
        timestamp_header = f"BTC_ANALYZER, [{trade.timestamp.strftime('%d.%m.%Y %H:%M')}]"
        
        message_lines = [
            f"{timestamp_header}",
            f"*🚨 İşlem Gerçekleşti - {action_text}*",
            f"📊 Sembol: {format_markdown(self._symbol)}",
            f"{direction_emoji} Yön: {format_markdown(trade.side)}",
            f"📦 Miktar: {trade.amount:.4f} BTC",
            f"💵 {'Kapanış' if is_close_trade else 'Fiyat'}: ${trade.price:.2f}",
            f"⚡ Kaldıraç: {leverage:.1f}x",
            f"💰 Notional: ${notional:.2f}",
            f"💸 İşlem Ücreti: ${fee:.2f} ({self._taker_fee_rate*100:.2f}%)",
            f"{'🟢' if trade.pnl >= 0 else '🔴'} Net PnL: ${trade.pnl:.2f} (ücret sonrası)",
            "",
        ]

        if is_close_trade:
            # CLOSE özel bilgileri: yeni pozisyon açılmadı + kalan pozisyon özetleri
            message_lines.extend([
                "⛔ Yeni pozisyon açılmadı (CLOSE)",
                f"🔒 Kapatılan: {trade.amount:.4f} BTC | Kalan: {portfolio.position:.4f} BTC",
                f"⏳ Kalan pozisyon unrealized: ${daily_pnl.unrealized_pnl:.2f}",
                "",
            ])

        # Get enhanced position details if available
        position_details = metrics.get('position_details', [])
        starting_cash = metrics.get('starting_cash', self._starting_cash)

        message_lines.extend([
            f"📍 Pozisyon Durumu: {format_markdown(position_status)}",
            f"📊 Portföy Pozisyonu: {portfolio.position:.4f} BTC @ ${portfolio.average_price:.2f}",
            f"💸 Pozisyon Değeri: ${position_value:.2f}",
            "",
            "*💰 Portföy Özeti*",
            f"💎 Serbest Sermaye: ${free_cash:.2f} ({free_cash_pct:.1f}%)",
            f"📊 Kullanılan Margin: ${margin_used:.2f}",
        ])

        # Add position-specific details if available
        if position_details:
            for i, pos in enumerate(position_details, 1):
                side = pos.get('position_side', '')
                leverage = pos.get('leverage', 1.0)
                margin = pos.get('margin', 0.0)
                notional = pos.get('notional', 0.0)
                amount = pos.get('amount', 0.0)
                message_lines.append(
                    f"  ├─ {side}: {amount:.4f} BTC @ {leverage:.1f}x → ${notional:,.2f} (Margin: ${margin:,.2f})"
                )

        message_lines.extend([
            f"🏦 Toplam Equity: ${equity:.2f}",
            f"💰 Başlangıç Sermayesi: ${starting_cash:.2f}",
            f"{'🟢' if equity_pct >= 0 else '🔴'} Toplam Değişim: {equity_pct:+.2f}%",
            f"✅ Gerçekleşen PnL: ${daily_pnl.realized_pnl:.2f}",
            f"⏳ Gerçekleşmemiş PnL: ${daily_pnl.unrealized_pnl:.2f}",
            "",
            f"🕒 Zaman: {trade.timestamp.isoformat()}",
        ])

        message = "\n".join(message_lines)
        
        # Gerekçeyi ayrı mesaj olarak gönder (çok uzun olabilir)
        reason_escaped = reason.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`').replace('(', '\\(').replace(')', '\\)')
        
        # Gerekçe çok uzunsa kısalt (Telegram 4096 karakter limiti)
        max_reason_length = 3000  # Ana mesaj + gerekçe için yer bırak
        if len(reason_escaped) > max_reason_length:
            reason_escaped = reason_escaped[:max_reason_length] + "... (kısaltıldı)"
        
        message_with_reason = message + f"\n\n💬 *Gerekçe:*\n{reason_escaped}"
        logger.info(
            "Telegram trade notify: side=%s amount=%.4f price=%.2f position=%.4f avg_price=%.2f realized=%.2f unrealized=%.2f equity=%.2f message_len=%d (with reason: %d)",
            trade.side,
            trade.amount,
            trade.price,
            portfolio.position,
            portfolio.average_price,
            daily_pnl.realized_pnl,
            daily_pnl.unrealized_pnl,
            equity,
            len(message),
            len(message_with_reason),
        )
        try:
            # Mesajı gönder
            telegram_client.send_message(message_with_reason)
            logger.info("Telegram trade notification sent successfully")
        except Exception as exc:  # noqa: BLE001
            logger.error("Telegram trade notify failed: %s", exc, exc_info=True)
            # Gerekçe olmadan tekrar dene
            try:
                telegram_client.send_message(message + "\n\n💬 Gerekçe: (çok uzun, log'lara bakın)")
                logger.warning("Sent telegram notification without detailed reasoning")
            except Exception as exc2:  # noqa: BLE001
                logger.error("Failed to send simplified telegram notification: %s", exc2)

    def _describe_position_change(self, before: float, after: float) -> str:
        if before == 0 and after == 0:
            return "Pozisyon yok"
        if after == 0:
            return f"Pozisyon kapandı ({before:+.4f} -> {after:+.4f})"
        if before == 0:
            return f"Yeni pozisyon açıldı ({after:+.4f})"
        if before * after < 0:
            return f"Pozisyon yön değiştirdi ({before:+.4f} -> {after:+.4f})"
        if abs(after) > abs(before):
            return f"Pozisyon artırıldı ({before:+.4f} -> {after:+.4f})"
        if abs(after) < abs(before):
            return f"Pozisyon azaltıldı ({before:+.4f} -> {after:+.4f})"
        return "Pozisyon değişmedi"

    def _normalize_leverage(self, value: float) -> float:
        if value <= 0:
            return self._min_leverage
        return max(self._min_leverage, min(value, self._max_leverage))

    def portfolio_metrics(self) -> Dict[str, float]:
        price = self._resolve_price()
        with Session(engine) as session:
            # Use synced portfolio to ensure accuracy before trade execution
            portfolio = get_synced_portfolio(session, self._symbol)
            daily_pnl = get_daily_pnl(session)

            # Kullanılan margin'i açık pozisyonlardan hesapla
            open_trades = (
                session.query(Trade)
                .filter_by(symbol=self._symbol)
                .filter(Trade.close_price.is_(None))
                .all()
            )

            margin_used = 0.0
            total_notional = 0.0
            position_details = []

            # MULTI-POSITION SUPPORT: Separate LONG and SHORT trades by position_side
            # position_side field explicitly stores "LONG" or "SHORT"
            long_trades = [t for t in open_trades if t.position_side == "LONG"]
            short_trades = [t for t in open_trades if t.position_side == "SHORT"]

            # Get latest open trade for exit plan (Nof1.ai style) - kept for backward compatibility
            latest_trade = None
            if open_trades:
                latest_trade = sorted(open_trades, key=lambda t: t.timestamp, reverse=True)[0]

            # Calculate LONG position metrics
            long_position = sum(t.amount for t in long_trades)
            long_avg_price = 0.0
            long_exit_plan = None
            long_leverage = 1.0
            
            if long_trades:
                # Weighted average price for LONG positions
                total_long_value = sum(t.amount * t.price for t in long_trades)
                long_avg_price = total_long_value / long_position if long_position > 0 else 0.0
                
                # Use most recent LONG trade's exit plan
                long_trade = sorted(long_trades, key=lambda t: t.timestamp, reverse=True)[0]
                long_exit_plan = long_trade.exit_plan
                long_leverage = long_trade.leverage if long_trade.leverage and long_trade.leverage > 0 else 1.0
            
            # Calculate SHORT position metrics
            short_position = sum(t.amount for t in short_trades)  # Will be negative
            short_avg_price = 0.0
            short_exit_plan = None
            short_leverage = 1.0
            
            if short_trades:
                # Weighted average price for SHORT positions (use absolute amounts for weighting)
                total_short_value = sum(abs(t.amount) * t.price for t in short_trades)
                total_short_amount = sum(abs(t.amount) for t in short_trades)
                short_avg_price = total_short_value / total_short_amount if total_short_amount > 0 else 0.0
                
                # Use most recent SHORT trade's exit plan
                short_trade = sorted(short_trades, key=lambda t: t.timestamp, reverse=True)[0]
                short_exit_plan = short_trade.exit_plan
                short_leverage = short_trade.leverage if short_trade.leverage and short_trade.leverage > 0 else 1.0

            # Calculate margin and notional for all positions
            for trade in open_trades:
                leverage = trade.leverage if trade.leverage and trade.leverage > 0 else 1.0
                trade_notional = abs(trade.price * trade.amount)
                trade_margin = trade_notional / leverage
                margin_used += trade_margin
                total_notional += trade_notional

                position_details.append({
                    "amount": trade.amount,
                    "price": trade.price,
                    "leverage": leverage,
                    "notional": trade_notional,
                    "margin": trade_margin,
                    "position_side": trade.position_side,
                })
            
            # Get exit plan from latest trade (Nof1.ai style) - kept for backward compatibility
            exit_plan = None
            if latest_trade and latest_trade.exit_plan:
                exit_plan = latest_trade.exit_plan
            
            # Get order IDs for latest trade (Nof1.ai style) - kept for backward compatibility
            sl_oid = -1
            tp_oid = -1
            entry_oid = -1
            if latest_trade:
                entry_oid = latest_trade.id
                # Find active stop-loss order for this trade
                sl_orders = (
                    session.query(StopLossOrder)
                    .filter(
                        StopLossOrder.trade_id == latest_trade.id,
                        StopLossOrder.is_active == True,  # noqa: E712
                        StopLossOrder.triggered == False,  # noqa: E712
                    )
                    .order_by(StopLossOrder.created_at.desc())
                    .first()
                )
                if sl_orders:
                    sl_oid = sl_orders.id
                
                
        exposure = portfolio.position * price
        unrealized = (price - portfolio.average_price) * portfolio.position if portfolio.position else 0.0

        # ÖNEMLİ: Unrealized PnL'i sınırla (astronomik değerleri önle)
        max_reasonable_pnl = self._starting_cash * 5  # Max 5x kâr/zarar
        unrealized = max(-max_reasonable_pnl, min(unrealized, max_reasonable_pnl))

        total_pnl = daily_pnl.realized_pnl + unrealized
        equity = self._starting_cash + total_pnl

        # Equity sanity check - astronomik değerleri sınırla
        equity = max(0, min(equity, self._starting_cash * 10))

        # DÜZELTME: Serbest sermaye sadece realized PnL ile hesaplanmalı (unrealized dahil değil)
        # Realized PnL = Sadece kapalı pozisyonların net PnL'i (Trade tablosundan gerçek zamanlı)
        closed_trades_net_pnl = session.query(
            func.coalesce(func.sum(Trade.pnl), 0.0)
        ).filter(
            Trade.symbol == self._symbol,
            Trade.close_price.isnot(None)  # Sadece kapalı pozisyonlar
        ).scalar() or 0.0
        
        safe_equity = self._starting_cash + closed_trades_net_pnl
        safe_equity = max(100, min(safe_equity, self._starting_cash * 10))  # Max 10x büyüme
        free_cash = safe_equity - margin_used
        
        # Calculate notional_usd for latest trade (Nof1.ai style)
        notional_usd = 0.0
        if latest_trade:
            notional_usd = abs(latest_trade.amount * price)

        result = {
            "price": price,
            "position": portfolio.position,
            "average_price": portfolio.average_price,
            "exposure": exposure,
            "realized_pnl": daily_pnl.realized_pnl,
            "unrealized_pnl": unrealized,
            "total_pnl": total_pnl,
            "equity": equity,
            "margin_used": margin_used,
            "free_cash": free_cash,
            "total_notional": total_notional,
            "position_details": position_details,
            "starting_cash": self._starting_cash,
            "total_fees": daily_pnl.total_fees,
            # Price change tracking for GLM awareness
            "last_trade_price": portfolio.last_trade_price,
            "last_trade_timestamp": portfolio.last_trade_timestamp,
            "current_price": price,
            # MULTI-POSITION SUPPORT: Add LONG and SHORT specific fields for GLM
            "long_position": long_position,
            "short_position": short_position,
            "net_position": portfolio.position,  # Net position = long + short
            "long_avg_price": long_avg_price,
            "short_avg_price": short_avg_price,
            "long_exit_plan": long_exit_plan,
            "short_exit_plan": short_exit_plan,
            "long_leverage": long_leverage,
            "short_leverage": short_leverage,
        }
        
        # Add exit plan info (Nof1.ai style) - kept for backward compatibility
        if exit_plan:
            result["exit_plan"] = exit_plan
            # Also add entry price for easy comparison
            if latest_trade:
                result["entry_price"] = latest_trade.price
                result["entry_oid"] = entry_oid
                result["sl_oid"] = sl_oid
                result["tp_oid"] = tp_oid
                result["notional_usd"] = notional_usd
                result["leverage"] = latest_trade.leverage if latest_trade.leverage and latest_trade.leverage > 0 else 10.0
                # Confidence için şimdilik None (trade modelinde yok, default değer _build_nof1_prompt'ta kullanılacak)
                result["confidence"] = None
        
        return result

    def calculate_real_pnl(self, pnl_before_fee: float, fees: float) -> dict:
        """
        Real PnL hesaplama: Brüt kar - komisyon ücretleri = Net kar

        Args:
            pnl_before_fee: Komisyon ücretleri düşülmemiş brüt PnL
            fees: Toplam komisyon ücretleri

        Returns:
            dict: {
                'pnl_gross': float,  # Brüt PnL
                'fees': float,       # Toplam ücretler
                'pnl_net': float,    # Net PnL (gerçek kar)
                'fee_percentage': float  # Ücret oranı (%)
            }
        """
        pnl_gross = pnl_before_fee
        total_fees = fees
        pnl_net = pnl_gross - total_fees

        # Entry notional hesapla (fee percentage için)
        # Notional değeri direkt hesaplanamaz, bu yüzden oran tahmini
        fee_percentage = 0.0
        if total_fees > 0:
            # Komisyon oranı: ~%0.05 (0.0005) per trade
            # Toplam fee'den oranı tahmin et
            fee_percentage = total_fees * 200  # 0.0005 × 200 = 0.1% (estimate)

        return {
            'pnl_gross': pnl_gross,
            'fees': total_fees,
            'pnl_net': pnl_net,
            'fee_percentage': fee_percentage,
        }

    def get_recent_trades_with_pnl(self, limit: int = 5) -> list[dict]:
        price = self._resolve_price()
        with Session(engine) as session:
            trades = get_recent_trades(session, self._symbol, limit)

        result = []
        for trade in trades:
            # Compute entry notional (unleveraged)
            entry_notional = abs((trade.price or 0.0) * (trade.amount or 0.0))
            notional_value = getattr(trade, 'notional_value', None)
            if notional_value is None or notional_value <= 0:
                notional_value = entry_notional

            # İF POSITION IS CLOSED: Use fixed PnL (already calculated at close)
            if trade.close_price is not None:
                # Kapanış fiyatı var = Pozisyon kapatılmış
                # PnL sabit kalmalı (trade.pnl kapanışta hesaplanmış veya en azından fee içerir)
                # Percent PnL = pnl / entry_notional × 100 (fee etkisi dahil)
                trade_pct_pnl = ((trade.pnl or 0.0) / entry_notional) * 100 if entry_notional > 0 else 0.0

                # REAL PNL HESAPLAMA
                fees = trade.fees if trade.fees else 0.0
                # Brüt PnL hesapla: PnL + fees (çünkü PnL'de fee zaten düşülmüş)
                pnl_gross = (trade.pnl or 0.0) + fees
                pnl_net = trade.pnl or 0.0
                real_pnl = self.calculate_real_pnl(pnl_gross, fees)

                # Extract exit_plan if available
                exit_plan = trade.exit_plan if hasattr(trade, 'exit_plan') and trade.exit_plan else {}
                
                result.append({
                    "side": trade.side,
                    "amount": trade.amount,
                    "open_price": trade.price,  # Açılış fiyatı
                    "close_price": trade.close_price,  # Kapanış fiyatı
                    "pnl": trade.pnl,  # ✅ Sabit PnL (kapanışta hesaplanan, fee düşülmüş)
                    "pnl_pct": trade_pct_pnl,
                    "fee": fees,  # ✅ Fee ekle
                    "is_closed": True,
                    "timestamp": trade.timestamp,
                    "position_id": trade.position_id,  # ✅ Pozisyon ID eklendi
                    "notional": entry_notional,
                    "real_pnl": real_pnl,  # ✅ Real PnL bilgisi (brüt, net, fee)
                    "stop_loss": exit_plan.get("stop_loss"),  # ✅ Stop loss ekle
                    "invalidation_condition": exit_plan.get("invalidation_condition"),  # ✅ Invalidation condition ekle
                })
            else:
                # ELSE: Position still open - calculate current PnL
                current_pnl = 0.0
                trade_pct_pnl = 0.0

                if trade.side == "BUY":
                    # LONG: Current PnL = (current_price - entry_price) * amount - original_fee
                    price_diff = price - trade.price
                    current_pnl = (price_diff * trade.amount) + trade.pnl  # trade.pnl has -fee
                    trade_pct_pnl = (current_pnl / entry_notional) * 100 if entry_notional > 0 else 0.0
                else:
                    # SHORT: Current PnL = (entry_price - current_price) * amount - original_fee
                    price_diff = trade.price - price
                    current_pnl = (price_diff * trade.amount) + trade.pnl  # trade.pnl has -fee
                    trade_pct_pnl = (current_pnl / entry_notional) * 100 if entry_notional > 0 else 0.0

                # REAL PNL HESAPLAMA (açık pozisyon için)
                fees = trade.fees if trade.fees else 0.0
                pnl_gross = current_pnl + fees  # Brüt PnL: current PnL + fees (fee'leri geri ekle)
                real_pnl = self.calculate_real_pnl(pnl_gross, fees)

                # Extract exit_plan if available
                exit_plan = trade.exit_plan if hasattr(trade, 'exit_plan') and trade.exit_plan else {}

                result.append({
                    "side": trade.side,
                    "amount": trade.amount,
                    "open_price": trade.price,  # Açılış fiyatı
                    "close_price": price,  # Mevcut piyasa fiyatı (henüz kapatılmadı)
                    "pnl": current_pnl,  # Güncel PnL (pozisyon hala açık)
                    "pnl_pct": trade_pct_pnl,
                    "fee": fees,  # ✅ Fee ekle
                    "is_closed": False,
                    "timestamp": trade.timestamp,
                    "position_id": trade.position_id,  # ✅ Pozisyon ID eklendi
                    "notional": entry_notional,
                    "real_pnl": real_pnl,  # ✅ Real PnL bilgisi (açık pozisyon)
                    "stop_loss": exit_plan.get("stop_loss"),  # ✅ Stop loss ekle
                    "invalidation_condition": exit_plan.get("invalidation_condition"),  # ✅ Invalidation condition ekle
                })

        return result

    def get_position_details(self) -> dict:
        price = self._resolve_price()
        with Session(engine) as session:
            position_details = get_open_position_details(session, self._symbol, price)
        return position_details

    def _execute_closing_trade(
        self,
        session: Session,
        portfolio: Portfolio,
        daily_pnl: DailyPnL,
        price: float,
        decision: RiskDecision,
        reason: str,
        pre_position: float,
        btc_amount: float,
        leverage: float,
        position_side: str = None,  # YENİ: "LONG" veya "SHORT"
    ) -> ExecutionResult:
        """
        Pozisyon kapatma trade'i (BUY ile SHORT kapatma veya SELL ile LONG kapatma)
        Yeni trade kaydedilmez, sadece mevcut trade'ler güncellenir.
        
        Args:
            position_side: Kapatılacak pozisyonun yönü ("LONG" veya "SHORT")
                          None ise eski mantık (decision.action'dan çıkar)
        """
        # Use Influx-aligned close price if available
        from app.utils.influx import query_price_at_time
        close_ts = datetime.utcnow()
        eff_price = price_cache.get(self._symbol) or query_price_at_time(
            self._symbol, close_ts, interval="1m", window_minutes=2
        ) or price
        
        position_size_usd = btc_amount * eff_price
        
        # Position side belirleme
        if not position_side:
            # Backward compatibility: action'dan çıkar
            position_side = "LONG" if decision.action == "SELL" else "SHORT"
        
        logger.info(
            "CLOSING %s position: %.6f BTC @ $%.2f via %s",
            position_side,
            btc_amount,
            eff_price,
            decision.action
        )
        
        # Sadece mevcut trade'leri güncelle (YENİ TRADE YOK!)
        updated_count, realized_delta, closing_fee_total = close_open_trades(
            session=session,
            symbol=self._symbol,
            close_side=decision.action,
            close_amount=btc_amount,
            close_price=eff_price,
            taker_fee_rate=self._taker_fee_rate,
        )
        
        logger.info(
            "Closed %s position: Updated %d trade(s) | PnL: $%.2f | Fee: $%.2f",
            position_side,
            updated_count,
            realized_delta,
            closing_fee_total,
        )
        
        # Portfolio ve PnL güncelle
        self._update_portfolio(portfolio, decision.action, btc_amount, eff_price)
        self._update_daily_pnl(daily_pnl, realized_delta, portfolio, eff_price, closing_fee_total)
        
        post_position = portfolio.net_position  # YENİ: net_position kullan
        session.flush()
        session.commit()
        
        # Bildirim için geçici trade objesi
        trade_summary = SimpleNamespace(
            amount=btc_amount,
            price=eff_price,
            pnl=realized_delta,
            side=decision.action,
            timestamp=datetime.utcnow(),
            fee=closing_fee_total,
            notional=position_size_usd,
            close_price=eff_price,
            position_side=position_side,
        )
        
        portfolio_summary = SimpleNamespace(
            position=portfolio.net_position,  # YENİ: net_position
            average_price=portfolio.average_price,
            long_position=portfolio.long_position,
            short_position=portfolio.short_position,
        )
        
        daily_summary = SimpleNamespace(
            realized_pnl=daily_pnl.realized_pnl,
            unrealized_pnl=daily_pnl.unrealized_pnl,
        )
        
        position_status = self._describe_position_change(pre_position, post_position)
        
        # Telemetry for cycle notifier
        current_position_id = self.get_position_id_by_side(session, self._symbol, position_side)
        telemetry = {
            "last_action": "CLOSE",
            "amount": btc_amount,
            "price": eff_price,
            "position_side": "LONG" if decision.action == "SELL" else "SHORT",
            "position_id": current_position_id,
            "fee": closing_fee_total,
            "pnl": realized_delta,
        }
        return ExecutionResult(status="PAPER", details="Pozisyon kapatıldı (trade güncellendi)", telemetry=telemetry)
    
    def close_position_by_exit_plan(
        self,
        reason: str,
        trigger_type: str  # "stop_loss" | "invalidation"
    ) -> ExecutionResult:
        """
        Exit plan tarafından tetiklenen pozisyon kapatma
        Position monitor tarafından çağrılır (AUTOMATIC CLOSING)
        
        Args:
            reason: Kapatma sebebi (detaylı açıklama)
            trigger_type: Tetikleyici tip ("stop_loss" | "invalidation")
        
        Returns:
            ExecutionResult
        """
        logger.info(
            "🔔 Closing position by exit plan | trigger=%s | reason=%s",
            trigger_type,
            reason
        )
        
        with Session(engine) as session:
            # Portfolio'yu kontrol et
            portfolio = get_synced_portfolio(session, self._symbol)
            
            if abs(portfolio.position) < 0.0001:
                logger.warning("No open position to close")
                return ExecutionResult(status="SKIP", details="Kapatılacak pozisyon yok")
            
            daily_pnl = get_daily_pnl(session)
            price = self._resolve_price()
            
            if price <= 0:
                logger.error("Invalid price: %.2f, cannot close position", price)
                return ExecutionResult(status="ERROR", details="Invalid price")
            
            pre_position = portfolio.position
            
            # CLOSE zamanını kaydet (cooldown başlat)
            self._last_close_time = datetime.utcnow()
            
            # === POSITION CLOSING LOGIC ===
            max_closeable = abs(portfolio.position)
            btc_amount = max_closeable  # Always close 100%
            
            # Efektif kapanış fiyatını al
            from app.utils.influx import query_price_at_time
            close_ts = datetime.utcnow()
            eff_price = price_cache.get(self._symbol) or query_price_at_time(self._symbol, close_ts, interval="1m", window_minutes=2) or price
            position_size_usd = btc_amount * eff_price
            
            # Pozisyon yönü
            is_long = portfolio.position > 0
            position_side = "LONG" if is_long else "SHORT"
            
            logger.info(
                "CLOSE %s position (exit plan): current=%.6f btc_to_close=%.6f notional=%.2f trigger=%s",
                position_side,
                portfolio.position,
                btc_amount,
                position_size_usd,
                trigger_type
            )
            
            # Pozisyon ID'sini al
            current_position_id = self.get_current_position_id(session, self._symbol)
            
            # Mevcut açık trade'lerin close_price'larını güncelle
            trade_side = "SELL" if is_long else "BUY"
            updated_count, realized_delta, closing_fee_total = close_open_trades(
                session=session,
                symbol=self._symbol,
                close_side=trade_side,
                close_amount=btc_amount,
                close_price=eff_price,
                taker_fee_rate=self._taker_fee_rate,
            )
            
            logger.info(
                "Closed %s position: %d trade(s) updated with close_price=%.2f",
                position_side,
                updated_count,
                eff_price,
            )
            
            # Portfolio güncelle (tam kapatma)
            portfolio.position = 0.0
            portfolio.average_price = 0.0
            portfolio.updated_at = datetime.utcnow()
            
            # Daily PnL güncelle
            self._update_daily_pnl(daily_pnl, realized_delta, portfolio, eff_price, closing_fee_total)
            
            session.flush()
            session.commit()
            
            logger.info("✅ Position closed by exit plan | pnl=%.2f fee=%.2f", realized_delta, closing_fee_total)
            
            telemetry = {
                "last_action": "CLOSE",
                "amount": btc_amount,
                "price": eff_price,
                "position_side": position_side,
                "position_id": current_position_id,
                "fee": closing_fee_total,
                "pnl": realized_delta,
                "trigger_type": trigger_type,
            }
            
            return ExecutionResult(
                status="PAPER",
                details=f"{position_side} position closed by {trigger_type}",
                telemetry=telemetry
            )

    def format_reason(self, text: str) -> str:
        cleaned = text.replace("```", "")
        cleaned = cleaned.replace("\\n", "\n")
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        summary = cleaned.strip()
        if match:
            json_text = match.group(0)
            prefix = cleaned[: match.start()].strip(" ()\n")
            try:
                payload = json.loads(json_text)
                parts: list[str] = []
                if prefix:
                    parts.append(prefix)
                karar = payload.get("karar")
                miktar = payload.get("miktar")
                kaldirac = payload.get("kaldıraç") or payload.get("kaldirac")
                gerekce = payload.get("gerekçe") or payload.get("gerekce")
                if karar is not None:
                    parts.append(f"karar={karar}")
                if miktar is not None:
                    parts.append(f"miktar={miktar}")
                if kaldirac is not None:
                    parts.append(f"kaldıraç={kaldirac}")
                if gerekce:
                    parts.append(f"açıklama={gerekce}")
                summary = " | ".join(parts) if parts else prefix
            except json.JSONDecodeError:
                summary = cleaned
        summary = re.sub(r"`+", "", summary)
        summary = re.sub(r"\s+", " ", summary).strip()
        return summary

    def start_stop_loss_monitoring(self) -> None:
        """WebSocket ile stop-loss/take-profit izlemeyi başlat"""
        if self._stop_loss_monitoring_active:
            return
            
        self._stop_loss_monitoring_active = True
        logger.info("Stop-loss/Take-profit monitoring started for %s", self._symbol)
        
        # WebSocket monitoring thread başlat
        import threading
        monitor_thread = threading.Thread(
            target=self._monitor_stop_loss_websocket,
            name=f"sl-tp-monitor-{self._symbol}",
            daemon=True
        )
        monitor_thread.start()

    def _monitor_stop_loss_websocket(self) -> None:
        """WebSocket üzerinden fiyatları izle ve stop-loss/take-profit kontrolü yap"""
        import asyncio
        import time
        from datetime import datetime
        
        async def monitor():
            try:
                from app.data_feeds.binance_ws import BinanceWebSocketClient
                
                def price_handler(message):
                    try:
                        # WebSocketMessage formatını kontrol et
                        if hasattr(message, 'payload'):
                            payload = message.payload
                        elif isinstance(message, dict):
                            payload = message.get("payload", {})
                        else:
                            logger.warning("Unknown message format: %s", type(message))
                            return
                            
                        kline = payload.get("k", {})
                        close_price = float(kline.get("c", 0.0))
                        
                        if close_price > 0:
                            self._check_and_notify_sl_tp(close_price)
                    except Exception as e:
                        logger.error("Error in price handler: %s", e)
                
                ws_client = BinanceWebSocketClient(self._symbol, "1m")
                await ws_client.listen(price_handler)
                
            except Exception as e:
                logger.error("WebSocket monitoring error: %s", e)
        
        # Async loop çalıştır
        try:
            asyncio.run(monitor())
        except Exception as e:
            logger.error("Failed to start WebSocket monitoring: %s", e)

    def _check_and_notify_sl_tp(self, current_price: float) -> None:
        """Stop-loss seviyesini kontrol et ve pozisyonu kapat (exit_plan'dan değerleri kullan)
        
        NOT: Take profit kontrolü kaldırılmıştır. Sadece stop loss kontrolü yapılır.
        Profit target seviyesine ulaşıldığında bilgilendirme log'u gönderilir ama pozisyon kapatılmaz.
        """
        try:
            # Mevcut pozisyonu kontrol et
            with Session(engine) as session:
                portfolio = get_synced_portfolio(session, self._symbol)
                
                if abs(portfolio.position) < 0.0001:  # Pozisyon yoksa izleme
                    return
                
                # En son açık trade'i bul ve exit_plan'ını kontrol et
                from app.executor.ledger import Trade
                latest_trade = (
                    session.query(Trade)
                    .filter_by(symbol=self._symbol)
                    .filter(Trade.close_price.is_(None))
                    .order_by(Trade.timestamp.desc())
                    .first()
                )
                
                if not latest_trade:
                    return
                
                entry_price = latest_trade.price
                is_long = portfolio.position > 0
                
                # GLM'nin exit_plan'ı öncelikli olarak kullanılmalı
                # Fallback mantık sadece exit_plan yoksa veya değerler geçersizse çalışacak
                stop_loss_price = None

                if latest_trade.exit_plan:
                    exit_plan = latest_trade.exit_plan
                    stop_loss_raw = exit_plan.get("stop_loss")

                    # GLM'nin exit_plan'ı varsa ve içinde geçerli değerler varsa kesinlikle kullan
                    if stop_loss_raw is not None and stop_loss_raw != 0.0:
                        stop_loss_price = stop_loss_raw
                
                # Exit plan yoksa veya değerler geçersizse fallback mantığı kullan (sadece stop loss için)
                if not stop_loss_price:
                    logger.debug("GLM exit plan not found or values invalid, using fallback SL logic")
                    if is_long:
                        stop_loss_price = entry_price * 0.995  # %0.5 stop-loss
                    else:
                        stop_loss_price = entry_price * 1.005  # %0.5 stop-loss
                
                            
                trigger_type = None
                trigger_price = None
                
                # Sadece stop loss kontrolü (take profit kaldırıldı)
                if is_long:
                    if stop_loss_price and current_price <= stop_loss_price:
                        trigger_type = "STOP-LOSS"
                        trigger_price = stop_loss_price
                else:  # SHORT
                    if stop_loss_price and current_price >= stop_loss_price:
                        trigger_type = "STOP-LOSS"
                        trigger_price = stop_loss_price
                
                # Seviye tetiklendi mi kontrol et
                if trigger_type:
                    logger.warning(
                        "%s triggered: %s position %.6f BTC @ %.2f, current %.2f, trigger %.2f",
                        trigger_type,
                        "LONG" if is_long else "SHORT",
                        abs(portfolio.position),
                        entry_price,
                        current_price,
                        trigger_price
                    )
                    
                    # Pozisyonu kapat
                    self._close_position_on_trigger(session, trigger_type, current_price, trigger_price)
                else:
                    # Seviye tetiklenmedi, sadece log (debug için)
                    logger.debug(
                        "SL check: %s @ %.2f, current %.2f, SL=%.2f",
                        "LONG" if is_long else "SHORT",
                        entry_price,
                        current_price,
                        stop_loss_price or 0.0
                    )
                        
        except Exception as e:
            logger.error("Error checking SL: %s", e, exc_info=True)
    
    def _close_position_on_trigger(self, session: Session, trigger_type: str, current_price: float, trigger_price: float) -> None:
        """Stop-loss tetiklendiğinde pozisyonu kapat"""
        try:
            from app.executor.ledger import get_daily_pnl, Trade
            from app.risk_manager.manager import RiskDecision
            
            portfolio = get_synced_portfolio(session, self._symbol)
            
            if abs(portfolio.position) < 0.0001:
                logger.warning("Position already closed or no position")
                return
            
            # Pozisyon bilgilerini kaydet (kapatmadan önce)
            position_amount = abs(portfolio.position)
            entry_price = portfolio.average_price
            position_type = "LONG" if portfolio.position > 0 else "SHORT"
            
            # En son trade'den leverage bilgisini al
            latest_trade = (
                session.query(Trade)
                .filter_by(symbol=self._symbol)
                .filter(Trade.close_price.is_(None))
                .order_by(Trade.timestamp.desc())
                .first()
            )
            leverage = latest_trade.leverage if latest_trade else self._leverage
            
            logger.info(
                "Closing position due to %s trigger: %.6f BTC %s @ %.2f, current %.2f",
                trigger_type,
                position_amount,
                position_type,
                entry_price,
                current_price
            )
            
            # CLOSE kararı oluştur
            close_decision = RiskDecision(
                action="CLOSE",
                amount=1.0,  # %100 kapat
                reasoning=f"{trigger_type} triggered at {trigger_price:.2f}",
                leverage=leverage,
                glm_confidence=100.0,
                reason_primary=f"Risk Management: {trigger_type}",
                reason_secondary=f"Triggered at ${trigger_price:,.2f}",
            )
            
            daily_pnl = get_daily_pnl(session)
            
            # Pozisyonu kapat
            result = self._execute_close_position(
                session=session,
                portfolio=portfolio,
                daily_pnl=daily_pnl,
                price=current_price,
                decision=close_decision,
                reason=trigger_type,
                position_amount=position_amount
            )
            
            if result.status == "EXECUTED":
                logger.info("✅ Position closed successfully due to %s", trigger_type)
                
                # Bildirim gönder
                self._send_sl_tp_notification(
                    trigger_type=trigger_type,
                    position_type=position_type,
                    amount=position_amount,
                    entry_price=entry_price,
                    current_price=current_price,
                    trigger_price=trigger_price
                )
            else:
                logger.error("❌ Failed to close position: %s", result.status)
                
        except Exception as e:
            logger.error("Error closing position on trigger: %s", e, exc_info=True)

    def _send_sl_tp_notification(self, trigger_type: str, position_type: str, 
                                 amount: float, entry_price: float, current_price: float, 
                                 trigger_price: float) -> None:
        """Stop-loss veya take-profit bildirimi gönder"""
        try:
            # Aynı bildirimi tekrar tekrar göndermeyi önle
            notification_key = f"{trigger_type}_{position_type}_{current_price:.2f}"
            current_time = datetime.utcnow()
            
            if (self._last_sl_tp_notification and 
                self._last_sl_tp_notification.get("key") == notification_key and
                (current_time - self._last_sl_tp_notification["time"]).total_seconds() < 60):
                return  # 1 dakika içinde aynı bildirimi gönderme
            
            # PnL hesapla
            if position_type == "SHORT":
                pnl_pct = (entry_price - current_price) / entry_price * 100
                pnl_amount = (entry_price - current_price) * abs(amount)
            else:  # LONG
                pnl_pct = (current_price - entry_price) / entry_price * 100
                pnl_amount = (current_price - entry_price) * amount
            
            # Database'e bildirimi kaydet
            db_notification = None
            try:
                with Session(engine) as session:
                    # İlgili emri bul (sadece stop-loss)
                    if trigger_type == "STOP-LOSS":
                        orders = get_active_stop_loss_orders(session, self._symbol)
                    else:
                        orders = []
                    
                    # İlgili emri bul ve bildirimi oluştur
                    order_id = None
                    if orders:
                        # En yakın emri bul
                        for order in orders:
                            if abs(order.entry_price - entry_price) < 1.0:  # $1 fark tolerance
                                order_id = order.id
                                break
                    
                    if order_id:
                        db_notification = create_stop_loss_notification(
                            session=session,
                            order_id=order_id,
                            symbol=self._symbol,
                            notification_type=trigger_type,
                            position_type=position_type,
                            position_amount=abs(amount),
                            entry_price=entry_price,
                            trigger_price=trigger_price,
                            current_price=current_price,
                            pnl_amount=pnl_amount,
                            pnl_percentage=pnl_pct,
                            telegram_sent=False,  # Henüz gönderilmedi
                        )
                        
                        # Emri tetikle (sadece stop-loss)
                        if trigger_type == "STOP-LOSS":
                            trigger_stop_loss_order(session, order_id, current_price, pnl_amount, pnl_pct)
                        
                        session.commit()
                        
            except Exception as db_e:
                logger.error("Database notification failed: %s", db_e)
            
            # Telegram bildirimi formatla
            emoji = "🛑" if trigger_type == "STOP-LOSS" else "🎯"
            position_emoji = "📉" if position_type == "SHORT" else "📈"
            
            message = f"""
{emoji} *{trigger_type} TETİKLENDİ*

{position_emoji} **Pozisyon Bilgisi:**
• Tür: {position_type}
• Miktar: {abs(amount):.6f} BTC
• Giriş Fiyatı: ${entry_price:,.2f}
• Mevcut Fiyat: ${current_price:,.2f}
• Tetik Fiyatı: ${trigger_price:,.2f}

💰 **Kar/Zarar:**
• Yüzde: {pnl_pct:+.2f}%
• Tutar: ${pnl_amount:+,.2f}

⏰ *{current_time.strftime('%H:%M:%S')}*
"""
            
            # Telegram'a gönder
            telegram_sent = False
            try:
                telegram_client.send_message(message)
                telegram_sent = True
                
                # Database'de telegram gönderimini güncelle
                if db_notification:
                    with Session(engine) as session:
                        notification = session.query(StopLossNotification).filter_by(id=db_notification.id).first()
                        if notification:
                            notification.telegram_sent = True
                            notification.telegram_sent_at = current_time
                            session.commit()
                            
            except Exception as telegram_e:
                logger.error("Telegram notification failed: %s", telegram_e)
            
            # Bildirim kaydını güncelle
            self._last_sl_tp_notification = {
                "key": notification_key,
                "time": current_time,
                "type": trigger_type,
                "price": current_price
            }
            
            logger.warning(
                "%s notification sent: %s %s at %.2f (entry: %.2f, trigger: %.2f, telegram: %s)",
                trigger_type, position_type, abs(amount), current_price, entry_price, trigger_price, 
                "✅" if telegram_sent else "❌"
            )
            
        except Exception as e:
            logger.error("Error sending SL/TP notification: %s", e)

    def _validate_exit_plan(self, exit_plan: dict, position_side: str, entry_price: float) -> tuple[bool, str]:
        """
        Exit planını doğrular ve mantıksal çatışmaları kontrol eder
        
        Args:
            exit_plan: GLM'den gelen exit planı
            position_side: "LONG" veya "SHORT"
            entry_price: Giriş fiyatı
            
        Returns:
            (is_valid, error_message)
        """
        if not exit_plan:
            return False, "Exit plan eksik"
        
        stop_loss = exit_plan.get("stop_loss")
        invalidation_condition = exit_plan.get("invalidation_condition", "")

        # Değerlerin geçerliliğini kontrol et
        if stop_loss is None:
            return False, "Stop loss boş olamaz"

        if stop_loss <= 0.0:
            return False, "Stop loss 0'dan büyük olmalı"
        
        # Invalidation condition parse et (advanced_parser returns 4 values now)
        direction, invalidation_price, time_frame, metadata = self._advanced_parser.parse(invalidation_condition)
        
        # Invalidation condition kontrolü - entry ile stop_loss ARASI olmalı (early warning)
        if direction and invalidation_price:
            if position_side == "LONG":
                if direction == "below":
                    # LONG için "below" yönünde: stop_loss < invalidation < entry olmalı
                    if not (stop_loss < invalidation_price < entry_price):
                        return False, f"LONG için invalidation({invalidation_price}) stop_loss({stop_loss}) ile entry({entry_price}) arasında olmalı (erken uyarı)"
                elif direction == "above":
                    return False, f"LONG için invalidation 'above' yönünde olamaz, 'below' olmalı"
            else:  # SHORT
                if direction == "above":
                    # SHORT için "above" yönünde: entry < invalidation < stop_loss olmalı
                    if not (entry_price < invalidation_price < stop_loss):
                        return False, f"SHORT için invalidation({invalidation_price}) entry({entry_price}) ile stop_loss({stop_loss}) arasında olmalı (erken uyarı)"
                elif direction == "below":
                    return False, f"SHORT için invalidation 'below' yönünde olamaz, 'above' olmalı"
        
        return True, ""
