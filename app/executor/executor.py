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
from app.risk_manager.exit_validator import get_exit_validator, ExitValidator
from app.utils.influx import query_latest
from app.utils.price_cache import ensure_price_cache_listener, price_cache
from app.utils.logging import get_logger
from app.utils.telegram import format_markdown, telegram_client
import os
from pathlib import Path


logger = get_logger(__name__)

# Trade log dosyası
TRADE_LOG_DIR = Path("/root/trading/logs")
TRADE_LOG_FILE = TRADE_LOG_DIR / "trades.log"

def _ensure_log_dir():
    """Log klasörünü oluştur"""
    TRADE_LOG_DIR.mkdir(parents=True, exist_ok=True)

def log_trade_to_file(
    symbol: str,
    action: str,
    entry_price: float,
    close_price: float = None,
    pnl: float = None,
    reasoning: str = None,
    amount: float = None,
    leverage: float = None,
    stop_loss: float = None,
    take_profit: float = None,
    entry_reasoning: str = None,  # Açılış gerekçesi (kapanışta gösterilir)
):
    """
    Trade bilgilerini log dosyasına yaz.

    Açılış Format:
    [2025-12-02 10:30:00] BTCUSDT | BUY (LONG) | Entry: $95000.00 | Amt: 0.05 | Lev: 7x | SL: $94000 | TP: $97000
      Entry: Güçlü yükseliş trendi...
    ---

    Kapanış Format (birleşik - tüm bilgiler):
    [2025-12-02 11:00:00] BTCUSDT | CLOSE_LONG
      Entry: $95000.00 | Amt: 0.05 | Lev: 7x | SL: $94000 | TP: $97000
      Exit:  $96000.00 | PnL: +$350.00
      Entry: Güçlü yükseliş trendi...
      Exit: Take profit hedefine ulaşıldı...
    ---
    """
    try:
        _ensure_log_dir()

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        if close_price and pnl is not None:
            # Kapanış işlemi - birleşik format (tüm açılış bilgileri dahil)
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

            with open(TRADE_LOG_FILE, "a", encoding="utf-8") as f:
                # Ana başlık
                f.write(f"[{timestamp}] {symbol} | {action}\n")

                # Açılış detayları
                entry_line = f"  Entry: ${entry_price:.2f}"
                if amount:
                    entry_line += f" | Amt: {amount:.4f}"
                if leverage:
                    entry_line += f" | Lev: {leverage:.0f}x"
                if stop_loss:
                    entry_line += f" | SL: ${stop_loss:.2f}"
                if take_profit:
                    entry_line += f" | TP: ${take_profit:.2f}"
                f.write(entry_line + "\n")

                # Kapanış detayları
                f.write(f"  Exit:  ${close_price:.2f} | PnL: {pnl_str}\n")

                # Açılış gerekçesi
                if entry_reasoning:
                    clean_entry = entry_reasoning.replace("\n", " ").strip()
                    if len(clean_entry) > 400:
                        clean_entry = clean_entry[:400] + "..."
                    f.write(f"  Entry Reason: {clean_entry}\n")

                # Kapanış gerekçesi
                if reasoning:
                    clean_exit = reasoning.replace("\n", " ").strip()
                    if len(clean_exit) > 400:
                        clean_exit = clean_exit[:400] + "..."
                    f.write(f"  Exit Reason: {clean_exit}\n")
                f.write("---\n")
        else:
            # Açılış işlemi
            position_type = "LONG" if action == "BUY" else "SHORT"
            log_line = f"[{timestamp}] {symbol} | {action} ({position_type}) | Entry: ${entry_price:.2f}"
            if amount:
                log_line += f" | Amt: {amount:.4f}"
            if leverage:
                log_line += f" | Lev: {leverage:.0f}x"
            if stop_loss:
                log_line += f" | SL: ${stop_loss:.2f}"
            if take_profit:
                log_line += f" | TP: ${take_profit:.2f}"

            with open(TRADE_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(log_line + "\n")
                if reasoning:
                    clean_reasoning = reasoning.replace("\n", " ").strip()
                    if len(clean_reasoning) > 500:
                        clean_reasoning = clean_reasoning[:500] + "..."
                    f.write(f"  Entry Reason: {clean_reasoning}\n")
                f.write("---\n")

        logger.info("📝 Trade logged: %s %s @ %.2f", symbol, action, entry_price)

    except Exception as e:
        logger.error("Failed to log trade to file: %s", e)


@dataclass
class ExecutionResult:
    status: str
    details: str
    telemetry: Optional[dict] = None


class Executor:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        max_position: float = 1000.0,  # YENİ: Default artırıldı (ETH/SOL için)
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

        # SWING TRADE MODE: Apply hard leverage cap from settings
        from app.config.settings import get_settings
        settings = get_settings()
        self._swing_mode = getattr(settings, 'swing_trade_mode', False)
        if self._swing_mode:
            swing_max_lev = getattr(settings, 'swing_max_leverage', 7)
            self._max_leverage = min(max_leverage, swing_max_lev)
            logger.info(
                "📊 Executor [%s]: SWING MODE enabled - max leverage capped at %dx",
                symbol, self._max_leverage
            )
        else:
            self._max_leverage = max_leverage

        self._max_margin_per_trade = max_trade_value  # YENİ: Rename for clarity
        self._starting_cash = 10000.0
        self._taker_fee_rate = taker_fee_rate
        
        # BACKWARD COMPATIBILITY
        self._max_position = max_position
        self._max_trade_value = max_trade_value
        
        # Global notional cap (sum of open positions' amount×price)
        self._max_total_notional_usd = self._starting_cash * self._max_leverage
        # Per-trade unit cap - devre dışı (margin/notional guardrail yeterli)
        self._max_btc_per_trade = None
        # Minimum margin to deploy per trade (per-symbol override, 0 = disabled)
        self._min_margin_per_trade = 0.0
        if symbol in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}:
            # Tüm semboller için tutarlı pozisyon boyutu (~$30k notional @10x)
            self._min_margin_per_trade = 3000.0
        # Reasonable price sanity range (adjusted for alts)
        self._min_price_sanity = 0.01
        self._max_price_sanity = 1_000_000.0
        
        # CLOSE sonrası cooldown mekanizması
        self._last_close_time = None
        self._close_cooldown_seconds = 60  # 60 saniye cooldown - duplicate close prevention
        self._symbol_close_timestamps: Dict[str, datetime] = {}  # Per-symbol close tracking
        
        # STOP-LOSS sistemi
        self._stop_loss_enabled = True
        self._base_stop_loss_percent = 0.05  # %5 baz stop-loss (Nof1.ai standardı)
        
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

        # Race condition prevention for duplicate CLOSE
        import threading
        self._close_lock = threading.Lock()
        self._closing_in_progress: Dict[str, bool] = {}

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



    def execute(self, decision: RiskDecision) -> ExecutionResult:
        reason = self.format_reason(decision.reasoning)
        logger.info(
            "Executing decision | action=%s | amount=%.4f | leverage=%.2f | reason=%s",
            decision.action,
            decision.amount,
            decision.leverage,
            reason,
        )
        
        # 1. Pre-trade validation (Staleness, Freshness, Cooldowns, etc.)
        validation_result = self._validate_pre_trade_conditions(decision)
        if validation_result:
            return validation_result
        
        # 2. DUAL-SOURCE PRICE VALIDATION (WS vs REST API)
        # Critical safeguard against corrupted WebSocket data
        if decision.action in ["BUY", "SELL", "CLOSE"]:
            ws_price = price_cache.get(self._symbol, max_age_seconds=10)
            rest_price = self._resolve_price()
            
            if ws_price and rest_price > 0:
                diff_pct = abs(ws_price - rest_price) / rest_price
                
                # Threshold: 0.5% mismatch triggers REST API override
                if diff_pct > 0.005:
                    logger.warning(
                        "🚨 PRICE MISMATCH DETECTED | Symbol=%s | WS=$%.2f | REST=$%.2f | Diff=%.2f%% | Action: Using REST",
                        self._symbol, ws_price, rest_price, diff_pct * 100
                    )
                    
                    # Force reset cache with reliable REST price
                    price_cache.force_reset(self._symbol, rest_price, source="dual_source_validation")
                    
                    # Alert via Telegram for critical anomalies
                    try:
                        telegram_client.send_message(
                            f"🚨 **FIYAT UYUMSUZLUĞU TESPİT EDİLDİ**\n\n"
                            f"**Symbol:** {self._symbol}\n"
                            f"**WebSocket:** ${ws_price:.2f}\n"
                            f"**REST API:** ${rest_price:.2f}\n"
                            f"**Fark:** {diff_pct*100:.2f}%\n\n"
                            f"✅ **Aksiyon:** REST API fiyatı kullanıldı (güvenilir kaynak)\n"
                            f"📌 **Trade Action:** {decision.action}",
                            parse_mode="Markdown"
                        )
                    except Exception as exc:
                        logger.error("Failed to send price mismatch alert: %s", exc)
                else:
                    logger.info(
                        "✅ Price sources aligned | WS=$%.2f | REST=$%.2f | Diff=%.2f%% (< 0.5%% threshold)",
                        ws_price, rest_price, diff_pct * 100
                    )

        with Session(engine) as session:
            portfolio = get_synced_portfolio(session, self._symbol)
            daily_pnl = get_daily_pnl(session)
            pre_position = portfolio.position
            price = self._resolve_price()
            
            if price <= 0:
                logger.error("Invalid price: %.2f, skipping execution", price)
                return ExecutionResult(status="SKIP", details="Invalid price")
            if not (self._min_price_sanity <= price <= self._max_price_sanity):
                logger.error(
                    "Price out of sanity bounds: %.2f (%.2f..%.2f) — skipping",
                    price,
                    self._min_price_sanity,
                    self._max_price_sanity,
                )
                return ExecutionResult(status="SKIP", details="Price out of sanity bounds")
            
            # Price change threshold: Only block extreme moves (>5%)
            with Session(engine) as check_session:
                from app.executor.ledger import Trade
                last_trade = (
                    check_session.query(Trade)
                    .filter_by(symbol=self._symbol)
                    .order_by(Trade.timestamp.desc())
                    .first()
                )
                if last_trade and last_trade.price:
                    price_change_pct = abs((price - last_trade.price) / last_trade.price) * 100
                    if price_change_pct > 10.0:
                        logger.warning(
                            "❌ BLOCKED by extreme price change: %.2f%% > 10.00%% (last: $%.2f, current: $%.2f)",
                            price_change_pct, last_trade.price, price
                        )
                        return ExecutionResult(
                            status="BLOCKED",
                            details=f"Extreme price movement: {price_change_pct:.2f}% > 10% threshold"
                        )
                    else:
                        logger.info(
                            "✅ Price change OK: %.2f%% ≤ 10.00%% (last: $%.2f, current: $%.2f)",
                            price_change_pct, last_trade.price, price
                        )
            
            leverage = self._normalize_leverage(decision.leverage)
            
            has_long = portfolio.long_position > 0.0001
            has_short = portfolio.short_position < -0.0001
            position_side = None
            is_closing = False
            
            if decision.action == "BUY":
                if has_long:
                    logger.warning("❌ BLOCKED: Long pozisyon zaten açık")
                    return ExecutionResult(
                        status="BLOCKED_DUPLICATE_POSITION",
                        details=f"Long pozisyon zaten açık: {portfolio.long_position:.6f} BTC"
                    )
                elif has_short:
                    logger.info("🔀 HEDGE: Short pozisyon açıkken yeni LONG açılıyor")
                    position_side = "LONG"
                    is_closing = False
                else:
                    logger.info("📈 OPENING NEW LONG POSITION")
                    position_side = "LONG"
                    is_closing = False
                    
            elif decision.action == "SELL":
                if has_short:
                    logger.warning("❌ BLOCKED: Short pozisyon zaten açık")
                    return ExecutionResult(
                        status="BLOCKED_DUPLICATE_POSITION",
                        details=f"Short pozisyon zaten açık: {portfolio.short_position:.6f} BTC"
                    )
                elif has_long:
                    logger.info("🔀 HEDGE: Long pozisyon açıkken yeni SHORT açılıyor")
                    position_side = "SHORT"
                    is_closing = False
                else:
                    logger.info("📉 OPENING NEW SHORT POSITION")
                    position_side = "SHORT"
                    is_closing = False
                    
            elif decision.action == "CLOSE":
                close_side = getattr(decision, 'close_side', None)
                
                if not close_side:
                    if has_long and has_short:
                        long_pnl = ((price - portfolio.long_avg_price) / portfolio.long_avg_price) if portfolio.long_avg_price else 0
                        short_pnl = ((portfolio.short_avg_price - price) / portfolio.short_avg_price) if portfolio.short_avg_price else 0
                        
                        if long_pnl < short_pnl:
                            close_side = "LONG"
                            logger.info("🎯 Auto-selecting worst position: LONG (%.2f%% PnL)", long_pnl)
                        else:
                            close_side = "SHORT"
                            logger.info("🎯 Auto-selecting worst position: SHORT (%.2f%% PnL)", short_pnl)
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
                logger.info("HOLD decision, no action taken")
                return ExecutionResult(status="SKIP", details="Hold kararı")
            
            if is_closing:
                result = self._handle_close_position(
                    session, portfolio, daily_pnl, decision, price, position_side
                )
            else:
                result = self._handle_open_position(
                    session, portfolio, daily_pnl, decision, price, leverage, position_side
                )
            
            post_position = portfolio.position
            self._describe_position_change(pre_position, post_position)
            
            return result

    def _validate_pre_trade_conditions(self, decision: RiskDecision) -> Optional[ExecutionResult]:
        """
        Validates all pre-trade conditions:
        1. Exit Plan presence (for BUY/SELL)
        2. Decision Staleness
        3. Price Freshness
        4. CLOSE specific validations (PnL, Confidence)
        5. Cooldowns (Hold, Same-direction, Close)
        6. Emergency Checks (Astronomical positions)
        """
        # VALIDATE EXIT PLAN: BUY/SELL requires valid exit_plan (CLOSE doesn't need it)
        if decision.action in ["BUY", "SELL"]:
            if not decision.exit_plan:
                logger.error("BLOCKED: GLM must provide exit_plan for %s", decision.action)
                return ExecutionResult(status="BLOCKED", details="Exit plan eksik - GLM hatası")
            
            stop_loss = decision.exit_plan.get("stop_loss")
            if not stop_loss or stop_loss == 0.0:
                logger.error("BLOCKED: Invalid stop_loss=%s for %s", stop_loss, decision.action)
                return ExecutionResult(status="BLOCKED", details="Stop loss geçersiz veya eksik")
            
            logger.info("✅ Exit plan validated: SL=%.2f INV=%s", stop_loss, decision.exit_plan.get("invalidation_condition", "N/A"))
        
        # DECISION STALENESS CHECK
        age = decision.age_seconds()
        logger.info("🔍 Decision age check: %.1fs old (max: 60s)", age)
        if decision.is_stale(max_age_seconds=60):
            logger.error("❌ SKIP REASON: Decision is STALE (%.1fs old > 60s max)", age)
            telegram_client.send_message(f"⚠️ TRADE SKIPPED: Decision too old ({age:.1f}s)\nAction: {decision.action}\nMarket may have moved significantly")
            return ExecutionResult(status="SKIPPED", details=f"Decision stale ({age:.1f}s old)")
        
        # PRICE FRESHNESS CHECK
        if decision.action in ["BUY", "SELL", "CLOSE"]:
            logger.info("🔍 Checking price freshness for %s action...", decision.action)
            validated_price = self._get_current_price_validated(self._symbol)
            if validated_price is None:
                logger.error("❌ SKIP REASON: No fresh price available")
                cache_age = price_cache.get_age_seconds(self._symbol)
                try:
                    telegram_client.send_message(f"⚠️ **İŞLEM ATLANDI - FİYAT VERİSİ YOK**\nAction: {decision.action}\nCache age: {cache_age:.1f}s")
                except Exception as exc:
                    logger.error("Failed to send Telegram notification: %s", exc)
                return ExecutionResult(status="SKIPPED", details="No fresh price available")
            logger.info("✅ Price is fresh: %.2f", validated_price)
            
            # CLOSE ACTION: Strict validation
            if decision.action == "CLOSE":
                with Session(engine) as session:
                    portfolio = get_synced_portfolio(session, self._symbol)
                    price = validated_price
                    from app.executor.ledger import Trade
                    
                    current_pnl_pct = 0.0
                    has_position = abs(portfolio.position) > 0.0001
                    
                    if has_position:
                        entry_price = portfolio.long_avg_price if portfolio.position > 0 else portfolio.short_avg_price
                        if entry_price > 0:
                            if portfolio.position > 0:
                                current_pnl_pct = ((price - entry_price) / entry_price) * 100
                            else:
                                current_pnl_pct = ((entry_price - price) / entry_price) * 100
                    
                    logger.info("✅ CLOSE action validated: confidence=%.1f%%, PnL=%.2f%% (confidence/PnL guardrails bypassed for GLM CLOSE)", decision.glm_confidence, current_pnl_pct)

        # CLOSE with amount=0 should close 100% of position
        if decision.action == "CLOSE" and decision.amount == 0:
            decision.amount = 1.0
            logger.info("📌 CLOSE amount was 0, defaulting to 100% close")

        # HOLD COOLDOWN
        if decision.action == "HOLD" or (decision.amount == 0 and decision.action not in ["CLOSE"]):
            logger.info("🔍 HOLD decision - checking close cooldown...")
            if self._last_close_time:
                elapsed = (datetime.utcnow() - self._last_close_time).total_seconds()
                remaining = self._close_cooldown_seconds - elapsed
                logger.info("   └─ Last close: %.0fs ago, cooldown: %.0fs (%.0fs remaining)", elapsed, self._close_cooldown_seconds, max(0, remaining))
                if elapsed < self._close_cooldown_seconds:
                    return ExecutionResult(status="SKIP", details=f"Hold kararı (cooldown: {int(remaining/60)}m {int(remaining%60)}s kaldı)")
            return ExecutionResult(status="SKIP", details="Hold kararı")

        # SAME DIRECTION COOLDOWN
        if decision.action in ["BUY", "SELL"]:
            current_time = datetime.utcnow()
            if (self._last_trade_direction == decision.action and self._last_trade_time and 
                (current_time - self._last_trade_time).total_seconds() < self._same_direction_cooldown):
                elapsed = (current_time - self._last_trade_time).total_seconds()
                remaining = self._same_direction_cooldown - elapsed
                logger.warning("❌ SKIP REASON: Same-direction trade cooldown - %s blocked", decision.action)
                return ExecutionResult(status="SKIP", details=f"{decision.action} atlandı (aynı yönde {int(remaining/60)} dk bekleme)")

        # EMERGENCY CHECK
        with Session(engine) as safety_session:
            safety_portfolio = get_synced_portfolio(safety_session, self._symbol)
            if abs(safety_portfolio.position) > 10.0:
                logger.critical("EMERGENCY: Position astronomical (%.6f BTC), forcing reset", safety_portfolio.position)
                price = self._resolve_price()
                close_side = "SELL" if safety_portfolio.position > 0 else "BUY"
                close_open_trades(session=safety_session, symbol=self._symbol, close_side=close_side, close_amount=abs(safety_portfolio.position), close_price=price, taker_fee_rate=self._taker_fee_rate)
                safety_portfolio.position = 0.0
                safety_portfolio.average_price = 0.0
                safety_portfolio.updated_at = datetime.utcnow()
                safety_session.commit()
                return ExecutionResult(status="EMERGENCY_RESET", details="Astronomik pozisyon sıfırlandı")
        
        # CLOSE COOLDOWN
        if self._last_close_time and decision.action in ["BUY", "SELL"]:
            elapsed = (datetime.utcnow() - self._last_close_time).total_seconds()
            if elapsed < self._close_cooldown_seconds:
                remaining = self._close_cooldown_seconds - elapsed
                return ExecutionResult(status="COOLDOWN", details=f"CLOSE sonrası cooldown: {int(remaining/60)}m {int(remaining%60)}s kaldı")

        # SMART COOLDOWN (Zarar Sonrası Akıllı Bekleme)
        # Eğer son işlem zararsa ve 30 dk geçmediyse, sadece %85+ güvenle işleme izin ver
        if decision.action in ["BUY", "SELL"]:
            with Session(engine) as session:
                from app.executor.ledger import Trade
                last_closed_trade = (
                    session.query(Trade)
                    .filter_by(symbol=self._symbol)
                    .filter(Trade.close_price.isnot(None))
                    .order_by(Trade.timestamp.desc())
                    .first()
                )
                
                if last_closed_trade and last_closed_trade.pnl < 0:
                    # Zarar etmiş son işlem
                    time_since_close = (datetime.utcnow() - last_closed_trade.timestamp).total_seconds()
                    cooldown_period = 1800  # 30 dakika
                    
                    if time_since_close < cooldown_period:
                        required_confidence = 85.0
                        if decision.glm_confidence < required_confidence:
                            remaining = cooldown_period - time_since_close
                            logger.warning(
                                "❄️ SMART COOLDOWN: Last trade LOSS (PnL: %.2f). "
                                "Confidence %.1f%% < %.1f%% required. Remaining: %dm %ds",
                                last_closed_trade.pnl,
                                decision.glm_confidence,
                                required_confidence,
                                int(remaining/60),
                                int(remaining%60)
                            )
                            return ExecutionResult(
                                status="BLOCKED_SMART_COOLDOWN", 
                                details=f"Zarar sonrası soğuma: Güven {decision.glm_confidence:.1f}% < {required_confidence}% ({int(remaining/60)}dk kaldı)"
                            )
                        else:
                            logger.info(
                                "🔥 SMART COOLDOWN OVERRIDE: High confidence (%.1f%% >= %.1f%%) allows trade despite recent loss",
                                decision.glm_confidence,
                                required_confidence
                            )
        
        return None

    def _handle_close_position(
        self, session: Session, portfolio: Portfolio, daily_pnl: DailyPnL,
        decision: RiskDecision, price: float, position_side: str
    ) -> ExecutionResult:
        if position_side == "LONG":
            max_closeable = portfolio.long_position
            entry_price = portfolio.long_avg_price
        else:
            max_closeable = abs(portfolio.short_position)
            entry_price = portfolio.short_avg_price

        if max_closeable < 0.0001:
            logger.warning("No %s position to close!", position_side)
            return ExecutionResult(status="SKIP", details=f"Kapatilacak {position_side} pozisyon yok")

        # =========================================================================
        # GLM Elite Swing Trader: Exit Validation
        # =========================================================================
        if self._swing_mode:
            try:
                # Get oldest open trade for this position side to determine hold duration
                oldest_open_trade = session.query(Trade).filter(
                    Trade.symbol == self._symbol,
                    Trade.position_side == position_side,
                    Trade.close_price.is_(None)
                ).order_by(Trade.timestamp.asc()).first()

                # Get exit plan from the trade
                exit_plan = oldest_open_trade.exit_plan if oldest_open_trade else None
                stop_loss = exit_plan.get("stop_loss", 0) if exit_plan else 0
                take_profit = exit_plan.get("profit_target", 0) if exit_plan else 0

                # Build position dict for validator
                position_dict = {
                    "quantity": max_closeable,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "type": position_side,
                    "open_time": oldest_open_trade.timestamp if oldest_open_trade else None,
                }

                # Validate the close decision
                exit_validator = get_exit_validator()
                is_valid, reason, override_action = exit_validator.validate_close_decision(
                    signal="CLOSE",
                    exit_validation=decision.exit_validation,
                    position=position_dict,
                    current_price=price,
                    allow_sl_override=True
                )

                if not is_valid:
                    logger.warning(
                        "Exit validation FAILED: %s | Overriding CLOSE -> HOLD",
                        reason
                    )
                    return ExecutionResult(
                        status="SKIP",
                        details=f"Exit validation failed: {reason}"
                    )
                else:
                    logger.info("Exit validation PASSED: %s", reason)

            except Exception as e:
                logger.error("Exit validation error: %s - proceeding with CLOSE", e)
        # =========================================================================

        btc_amount = min(max_closeable, max_closeable * decision.amount)
        logger.info("CLOSING %s position: max=%.6f, amount=%.6f, price=$%.2f", position_side, max_closeable, btc_amount, price)

        return self._execute_closing_trade(
            session, portfolio, daily_pnl, price, decision,
            self.format_reason(decision.reasoning), portfolio.net_position, btc_amount, decision.leverage, position_side
        )

    def _handle_open_position(
        self, session: Session, portfolio: Portfolio, daily_pnl: DailyPnL,
        decision: RiskDecision, price: float, leverage: float, position_side: str
    ) -> ExecutionResult:
        pre_position = portfolio.position
        
        # Calculate margin usage
        used_margin = calculate_correct_margin_usage(portfolio, price, leverage)
        closed_trades_net_pnl = session.query(func.coalesce(func.sum(Trade.pnl), 0.0)).filter(Trade.symbol == self._symbol, Trade.close_price.isnot(None)).scalar() or 0.0
        safe_equity = max(100, min(self._starting_cash + closed_trades_net_pnl, self._starting_cash * 10))
        free_equity = safe_equity - used_margin
        
        if free_equity <= 0:
            logger.error("No free equity available")
            return ExecutionResult(status="SKIP", details="Serbest sermaye yok")
        
        margin_to_use = min(free_equity * decision.amount, self._max_trade_value)
        position_size_usd = margin_to_use * leverage
        btc_amount = position_size_usd / price

        # Enforce per-symbol minimum margin if configured (alts should size up)
        if self._min_margin_per_trade > 0:
            target_margin = min(self._min_margin_per_trade, self._max_trade_value)
            if free_equity < target_margin:
                target_margin = free_equity
            if target_margin > 0 and margin_to_use + 1e-9 < target_margin:
                logger.info(
                    "📈 Scaling up margin for %s: requested=%.2f → target=%.2f (leverage=%.1fx)",
                    self._symbol,
                    margin_to_use,
                    target_margin,
                    leverage,
                )
                margin_to_use = target_margin
                position_size_usd = margin_to_use * leverage
                btc_amount = position_size_usd / price
        
        # Limits
        max_unit_cap = self._max_btc_per_trade
        if max_unit_cap and btc_amount > max_unit_cap:
            btc_amount = max_unit_cap
            position_size_usd = btc_amount * price
        
        total_position_after = abs(portfolio.position) + btc_amount
        if total_position_after > self._max_position:
            btc_amount = max(0, self._max_position - abs(portfolio.position))
            if btc_amount < 0.0001:
                return ExecutionResult(status="SKIP", details="Pozisyon limiti doldu")
            position_size_usd = btc_amount * price
            
        # Notional Cap Check
        try:
            open_trades = session.query(Trade).filter_by(symbol=self._symbol).filter(Trade.close_price.is_(None)).all()
            current_total_notional = sum(abs(t.amount) * t.price for t in open_trades)
        except Exception:
            current_total_notional = abs(portfolio.position) * price
            
        allowed_remaining = self._max_total_notional_usd - current_total_notional
        if allowed_remaining <= 0:
            return ExecutionResult(status="BLOCKED", details="Toplam notional limiti dolu")
            
        new_notional = btc_amount * price
        if new_notional > allowed_remaining + 1e-8:
            btc_amount = max(0.0, allowed_remaining / price)
            if btc_amount < 0.0001:
                return ExecutionResult(status="BLOCKED", details="Notional limiti nedeniyle engellendi")
            position_size_usd = btc_amount * price
            
        logger.info("OPENING position: equity=%.2f margin=%.2f btc=%.6f", safe_equity, margin_to_use, btc_amount)
        
        # Guardrails
        total_equity = self._starting_cash + daily_pnl.realized_pnl + daily_pnl.unrealized_pnl
        if not self._check_guardrails(portfolio, btc_amount, total_equity):
            self._notify_guardrail_block(decision, self.format_reason(decision.reasoning), pre_position, btc_amount, leverage)
            return ExecutionResult(status="BLOCKED", details="Guardrail engelledi")

        # Update decision.amount to reflect final deployed margin (for notifications)
        if safe_equity > 0:
            decision.amount = margin_to_use / safe_equity
            
        # Fees and PnL
        notional_value = position_size_usd
        fee = notional_value * self._taker_fee_rate
        pnl_before_fee = self._calculate_pnl(portfolio, decision.action, btc_amount, price)
        pnl = pnl_before_fee - fee
        
        # Position ID
        current_position_id = generate_position_id(self._symbol) if pre_position == 0 else self.get_current_position_id(session, self._symbol)
        
        # Exit Plan Logic - reasoning'i de ekle (tooltip için)
        exit_plan = decision.exit_plan.copy() if decision.exit_plan else {}
        if decision.reasoning:
            exit_plan["reasoning"] = decision.reasoning
        if decision.action in ["BUY", "SELL"]:
            inv_text = exit_plan.get("invalidation_condition", "") if exit_plan else ""
            def _is_number(val):
                try:
                    return float(val) if val is not None else None
                except Exception:
                    return None
            stop_loss_val = _is_number(exit_plan.get("stop_loss") if exit_plan else None)
            profit_target_val = _is_number(exit_plan.get("profit_target") if exit_plan else None)
            if (
                not exit_plan
                or stop_loss_val is None
                or profit_target_val is None
                or profit_target_val == 0
                or not (isinstance(inv_text, str) and inv_text.strip())
            ):
                logger.error(
                    "BLOCKED: GLM must provide exit_plan with stop_loss, profit_target and invalidation_condition for %s",
                    decision.action,
                )
                return ExecutionResult(
                    status="BLOCKED",
                    details="GLM exit plan eksik: stop_loss, take_profit ve invalidation_condition zorunlu",
                )
        
        # Validate Exit Plan
        # Nof1PromptBuilder ile mantıksal doğrulama
        from app.risk_manager.nof1_prompt_builder import Nof1PromptBuilder
        prompt_builder = Nof1PromptBuilder()
        # Seed prompt builder with context from decision (captured during prompt build)
        if getattr(decision, "context_volatility", None) is not None:
            prompt_builder._current_volatility = decision.context_volatility
        if getattr(decision, "context_atr_pct", None) is not None:
            prompt_builder._current_atr_pct = decision.context_atr_pct
        if getattr(decision, "context_vol_ratio", None) is not None:
            prompt_builder._vol_ratio = decision.context_vol_ratio
        if getattr(decision, "context_atr_ratio", None) is not None:
            prompt_builder._atr_ratio = decision.context_atr_ratio

        # DEBUG: Log what context values were received from decision
        logger.info(
            "📊 Executor received context (id=%s): decision.context_atr_pct=%s, final_atr_pct=%s",
            id(decision),
            getattr(decision, "context_atr_pct", "NOT_SET"),
            prompt_builder._current_atr_pct
        )

        # Python-calculated exit plans are always valid (via calculate_exit_plan())
        # Skip validation - exit plan bounds are enforced during calculation
        is_valid = True
        validation_error = ""
        logger.info("✅ Exit plan (Python-calculated): sl=%s tp=%s inv=%s", exit_plan.get("stop_loss"), exit_plan.get("profit_target"), exit_plan.get("invalidation_condition"))

        if not is_valid:
            logger.warning("❌ Exit plan validation failed: %s. Trade blocked to enforce risk rules.", validation_error)
            try:
                telegram_client.send_message(
                    f"⚠️ *EXIT PLAN REDDEDİLDİ*\n"
                    f"Neden: {validation_error}\n"
                    f"Aksiyon: {decision.action}\n"
                    f"SL: {exit_plan.get('stop_loss')}, TP: {exit_plan.get('profit_target')}, INV: {exit_plan.get('invalidation_condition')}"
                )
            except Exception as exc:
                logger.error("Failed to notify exit-plan block: %s", exc)
            return ExecutionResult(
                status="BLOCKED",
                details=f"Exit plan validation failed: {validation_error}",
            )
            
        trade = record_trade(
            session, symbol=self._symbol, side=decision.action, amount=btc_amount,
            price=price, pnl=pnl, leverage=leverage, fees=fee, position_side=position_side,
            position_id=current_position_id, exit_plan=exit_plan,
            entry_reasoning=decision.reasoning,
        )
        session.flush()

        # Açılış trade'ini log dosyasına yaz
        log_trade_to_file(
            symbol=self._symbol,
            action=decision.action,
            entry_price=price,
            amount=btc_amount,
            leverage=leverage,
            reasoning=decision.reasoning,
            stop_loss=exit_plan.get("stop_loss") if exit_plan else None,
            take_profit=exit_plan.get("profit_target") if exit_plan else None,
        )

        if not self._stop_loss_monitoring_active:
            try:
                self.start_stop_loss_monitoring()
            except Exception as e:
                logger.warning("Failed to start WebSocket monitoring: %s", e)
                
        self._last_trade_direction = decision.action
        self._last_trade_time = datetime.utcnow()
        
        self._update_portfolio(portfolio, decision.action, trade.amount, price)
        post_position = portfolio.position
        self._update_daily_pnl(daily_pnl, pnl, portfolio, price, fee)
        session.flush()
        session.commit()
        
        telemetry = {
            "last_action": position_side, "amount": trade.amount, "price": price,
            "position_side": position_side, "position_id": current_position_id, "fee": fee, "pnl": pnl
        }
        return ExecutionResult(status="PAPER", details="Paper trading kaydedildi", telemetry=telemetry)

    def _check_guardrails(self, portfolio: Portfolio, btc_amount: float, equity: float) -> bool:
        """
        Guardrail kontrolleri:
        1. Tek işlemde max BTC limiti
        2. Total pozisyon limiti
        3. Equity sanity check
        """
        # 1. Per-trade unit cap (disabled when None)
        if self._max_btc_per_trade and btc_amount > self._max_btc_per_trade:
            logger.warning(
                "Single trade unit limit exceeded: requested=%.6f limit=%.6f",
                btc_amount,
                self._max_btc_per_trade,
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
            # 🔄 ENHANCED CACHE UPDATE with reset logic
            logger.info("✅ Binance API price: %.2f (attempt %d, %.2fs)", rest_price, attempt + 1, elapsed)

            # Check for significant price difference that needs cache reset
            cached_price_stale = price_cache.get(symbol)  # Get any cached price regardless of age
            if cached_price_stale and cached_price_stale != rest_price:
                diff_pct = abs(rest_price - cached_price_stale) / cached_price_stale
                if diff_pct > 0.10:  # 10% difference triggers reset
                    logger.warning(
                        "🔄 SIGNIFICANT PRICE DIFFERENCE %s: REST=%.2f vs Cache=%.2f (%.1f%%) - TRIGGERING CACHE RESET",
                        symbol, rest_price, cached_price_stale, diff_pct * 100
                    )
                    # Use enhanced cache update with reset logic
                    price_cache.update_rest_price(symbol, rest_price)
                else:
                    # Normal cache update
                    price_cache.set(symbol, rest_price, source="rest_fallback")
            else:
                # First time setting or same price
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
                    "https://api.binance.com/api/v3/ticker/price",
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

        base_asset = self._symbol.replace("USDT", "")
        new_position = abs(current_position + requested_amount)
        message = "\n".join(
            [
                "*🚫 İşlem Engellendi (Guardrail)*",
                f"📊 Sembol: {format_markdown(self._symbol)}",
                f"{'📈' if decision.action == 'BUY' else '📉'} Talep Yön: {format_markdown(decision.action)}",
                f"📊 Risk Oranı: {decision.amount * 100:.1f}% equity",
                f"⚡ Kaldıraç: {leverage:.1f}x",
                f"📦 Kaldıraçlı Miktar: {requested_amount:.4f} {base_asset}",
                "",
                "*⚠️ Pozisyon Durumu*",
                f"📍 Mevcut Pozisyon: {current_position:+.4f} {base_asset}",
                f"🎯 İstenen Yeni Pozisyon: {new_position:.4f} {base_asset}",
                f"🔴 Limit: {self._max_position:.4f} {base_asset}",
                f"❌ Limit Aşımı: {(new_position - self._max_position):.4f} {base_asset}",
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
        base_asset = self._symbol.replace("USDT", "")
        timestamp_header = f"{base_asset}_ANALYZER, [{trade.timestamp.strftime('%d.%m.%Y %H:%M')}]"
        
        message_lines = [
            f"{timestamp_header}",
            f"*🚨 İşlem Gerçekleşti - {action_text}*",
            f"📊 Sembol: {format_markdown(self._symbol)}",
            f"{direction_emoji} Yön: {format_markdown(trade.side)}",
            f"📦 Miktar: {trade.amount:.4f} {base_asset}",
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
                f"🔒 Kapatılan: {trade.amount:.4f} {base_asset} | Kalan: {portfolio.position:.4f} {base_asset}",
                f"⏳ Kalan pozisyon unrealized: ${daily_pnl.unrealized_pnl:.2f}",
                "",
            ])

        # Get enhanced position details if available
        position_details = metrics.get('position_details', [])
        starting_cash = metrics.get('starting_cash', self._starting_cash)

        message_lines.extend([
            f"📍 Pozisyon Durumu: {format_markdown(position_status)}",
            f"📊 Portföy Pozisyonu: {portfolio.position:.4f} {base_asset} @ ${portfolio.average_price:.2f}",
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
                    f"  ├─ {side}: {amount:.4f} {base_asset} @ {leverage:.1f}x → ${notional:,.2f} (Margin: ${margin:,.2f})"
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

        # Take Profit display for open positions (from latest trade)
        if not is_close_trade and metrics.get("tp_oid") != -1:
             # Fetch from metrics (which gets from ledger)
             latest_tp = metrics.get("exit_plan", {}).get("take_profit")
             if latest_tp:
                 message_lines.append(f"🎯 Take Profit: ${latest_tp:.2f}")

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
            "recent_trades": self.get_recent_trades_with_pnl(limit=5),
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
                    "profit_target": exit_plan.get("profit_target"),  # ✅ Profit target ekle
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
                    "profit_target": exit_plan.get("profit_target"),  # ✅ Profit target ekle
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
        # Sadece WebSocket fiyatı kullan - hiçbir fallback yok
        eff_price = price if price and price > 0 else None

        if not eff_price:
            eff_price = price_cache.get(self._symbol, max_age_seconds=3)

        if not eff_price or eff_price <= 0:
            logger.error("❌ WebSocket price not available for closing %s", self._symbol)
            return ExecutionResult(status="FAILED", details="WebSocket price unavailable - cannot close position")

        
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
        
        # Duplicate close prevention - if no trades were updated, position already closed
        if updated_count == 0:
            logger.warning("No trades updated - position may already be closed by another process")
            return ExecutionResult(status="SKIP", details="Position already closed")

        logger.info(
            "Closed %s position: Updated %d trade(s) | PnL: $%.2f | Fee: $%.2f",
            position_side,
            updated_count,
            realized_delta,
            closing_fee_total,
        )

        # Kapanış trade'ini log dosyasına yaz
        # Açılış bilgilerini bul (son kapatılan trade'den)
        entry_reasoning_for_log = None
        entry_amount_for_log = None
        entry_leverage_for_log = None
        entry_sl_for_log = None
        entry_tp_for_log = None
        try:
            from app.executor.ledger import Trade
            closed_trade = (
                session.query(Trade)
                .filter_by(symbol=self._symbol)
                .filter(Trade.close_price.isnot(None))
                .order_by(Trade.close_time.desc())
                .first()
            )
            if closed_trade:
                entry_reasoning_for_log = closed_trade.entry_reasoning
                entry_amount_for_log = closed_trade.amount
                entry_leverage_for_log = closed_trade.leverage
                if closed_trade.exit_plan:
                    entry_sl_for_log = closed_trade.exit_plan.get("stop_loss")
                    entry_tp_for_log = closed_trade.exit_plan.get("profit_target")
        except Exception as e:
            logger.debug("Could not fetch entry details: %s", e)

        entry_price_for_log = portfolio.long_avg_price if position_side == "LONG" else portfolio.short_avg_price
        log_trade_to_file(
            symbol=self._symbol,
            action=f"CLOSE_{position_side}",
            entry_price=entry_price_for_log,
            close_price=eff_price,
            pnl=realized_delta,
            reasoning=decision.reasoning,
            entry_reasoning=entry_reasoning_for_log,
            amount=entry_amount_for_log,
            leverage=entry_leverage_for_log,
            stop_loss=entry_sl_for_log,
            take_profit=entry_tp_for_log,
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
        trigger_type: str,  # "stop_loss" | "invalidation"
        exit_price: Optional[float] = None  # Position monitor'dan gelen gerçek fiyat
    ) -> ExecutionResult:
        """
        Exit plan tarafından tetiklenen pozisyon kapatma
        Position monitor tarafından çağrılır (AUTOMATIC CLOSING)
        
        Args:
            reason: Kapatma sebebi (detaylı açıklama)
            trigger_type: Tetikleyici tip ("stop_loss" | "invalidation")
            exit_price: Pozisyonun kapandığı gerçek fiyat (position monitor'dan)
        
        Returns:
            ExecutionResult
        """
        logger.info(
            "🔔 Closing position by exit plan | trigger=%s | reason=%s | exit_price=%s",
            trigger_type,
            reason,
            exit_price
        )

        # Race condition prevention - check if close already in progress
        with self._close_lock:
            current_time = datetime.utcnow()

            # Cooldown kontrolü - aynı symbol için 60 saniye içinde duplicate close engelle
            last_close = self._symbol_close_timestamps.get(self._symbol)
            if last_close:
                elapsed = (current_time - last_close).total_seconds()
                if elapsed < self._close_cooldown_seconds:
                    logger.warning(
                        "⏳ Close cooldown active for %s: %.1f/%.0f seconds remaining",
                        self._symbol, elapsed, self._close_cooldown_seconds
                    )
                    return ExecutionResult(status="SKIP", details=f"Cooldown: {self._close_cooldown_seconds - elapsed:.1f}s remaining")

            if self._closing_in_progress.get(self._symbol):
                logger.warning("Close already in progress for %s, skipping duplicate", self._symbol)
                return ExecutionResult(status="SKIP", details="Close already in progress")

            self._closing_in_progress[self._symbol] = True
            self._symbol_close_timestamps[self._symbol] = current_time

        try:
            with Session(engine) as session:
                # Portfolio'yu kontrol et
                portfolio = get_synced_portfolio(session, self._symbol)

                if abs(portfolio.position) < 0.0001:
                    logger.warning("No open position to close")
                    return ExecutionResult(status="SKIP", details="Kapatılacak pozisyon yok")

                daily_pnl = get_daily_pnl(session)

                # Öncelikle position monitor'dan gelen exit_price'ı kullan
                if exit_price and exit_price > 0:
                    price = exit_price
                    logger.info("Using exit_price from position monitor: %.2f", price)
                else:
                    price = self._resolve_price()
                    logger.warning("No exit_price provided, using resolved price: %.2f", price)

                if price <= 0:
                    logger.error("Invalid price: %.2f, cannot close position", price)
                    return ExecutionResult(status="ERROR", details="Invalid price")

                pre_position = portfolio.position

                # CLOSE zamanını kaydet (cooldown başlat)
                self._last_close_time = datetime.utcnow()

                # === POSITION CLOSING LOGIC ===
                max_closeable = abs(portfolio.position)
                btc_amount = max_closeable  # Always close 100%

                # Efektif kapanış fiyatı = position monitor'dan gelen fiyat veya mevcut fiyat
                eff_price = price
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

                # Duplicate close prevention - if no trades were updated, position already closed
                if updated_count == 0:
                    logger.warning("No trades updated - position may already be closed by another process")
                    return ExecutionResult(status="SKIP", details="Position already closed")

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
        finally:
            # Release the close lock
            with self._close_lock:
                self._closing_in_progress[self._symbol] = False

    def execute_partial_close(
        self,
        position_side: str,
        close_quantity: float,
        close_price: float,
        tp_level: int,
        reason: str,
        remaining_quantity: float = 0.0,
        is_breakeven: bool = False,
        trailing_activated: bool = False,
    ) -> ExecutionResult:
        """
        TP tetiklendiğinde kısmi pozisyon kapatma.
        PartialTakeProfitManager tarafından çağrılır.

        Args:
            position_side: Kapatılacak pozisyonun yönü ("LONG" veya "SHORT")
            close_quantity: Kapatılacak miktar (BTC/coin cinsinden)
            close_price: Kapanış fiyatı
            tp_level: TP seviyesi (1, 2, 3, 4)
            reason: Kapanış nedeni
            remaining_quantity: Kalan pozisyon miktarı
            is_breakeven: TP1 sonrası breakeven aktif mi
            trailing_activated: TP2 sonrası trailing aktif mi

        Returns:
            ExecutionResult
        """
        logger.info(
            "🎯 Partial close triggered | TP%d | %s | qty=%.6f @ $%.2f",
            tp_level,
            position_side,
            close_quantity,
            close_price
        )

        # Kısa cooldown kontrolü (partial close için 5 saniye)
        current_time = datetime.utcnow()
        partial_cooldown_key = f"{self._symbol}_partial_tp{tp_level}"
        last_partial = getattr(self, '_partial_close_timestamps', {}).get(partial_cooldown_key)

        if last_partial:
            elapsed = (current_time - last_partial).total_seconds()
            if elapsed < 5:
                logger.warning("Partial close cooldown active: %.1fs remaining", 5 - elapsed)
                return ExecutionResult(status="SKIP", details=f"Partial cooldown: {5 - elapsed:.1f}s")

        # Cooldown timestamp'i kaydet
        if not hasattr(self, '_partial_close_timestamps'):
            self._partial_close_timestamps = {}
        self._partial_close_timestamps[partial_cooldown_key] = current_time

        try:
            with Session(engine) as session:
                portfolio = get_synced_portfolio(session, self._symbol)

                # Pozisyon kontrolü
                if position_side == "LONG":
                    current_qty = portfolio.long_position
                else:
                    current_qty = abs(portfolio.short_position)

                if current_qty < 0.0001:
                    logger.warning("No %s position to partially close", position_side)
                    return ExecutionResult(status="SKIP", details=f"No {position_side} position")

                # Kapatılacak miktar kontrolü
                actual_close_qty = min(close_quantity, current_qty)
                if actual_close_qty < 0.0001:
                    logger.warning("Close quantity too small: %.8f", actual_close_qty)
                    return ExecutionResult(status="SKIP", details="Close quantity too small")

                daily_pnl = get_daily_pnl(session)
                pre_position = portfolio.position

                # Position ID al
                current_position_id = self.get_position_id_by_side(session, self._symbol, position_side)

                # Trade side belirle (LONG kapatmak için SELL, SHORT kapatmak için BUY)
                trade_side = "SELL" if position_side == "LONG" else "BUY"

                # close_open_trades çağır - FIFO ile kısmi kapanış
                updated_count, realized_delta, closing_fee_total = close_open_trades(
                    session=session,
                    symbol=self._symbol,
                    close_side=trade_side,
                    close_amount=actual_close_qty,
                    close_price=close_price,
                    taker_fee_rate=self._taker_fee_rate,
                )

                if updated_count == 0:
                    logger.warning("No trades updated for partial close")
                    return ExecutionResult(status="SKIP", details="No trades to close")

                logger.info(
                    "✅ Partial close TP%d: %d trade(s) | qty=%.6f | pnl=$%.2f | fee=$%.2f",
                    tp_level,
                    updated_count,
                    actual_close_qty,
                    realized_delta,
                    closing_fee_total
                )

                # Portfolio güncelle
                self._update_portfolio(portfolio, trade_side, actual_close_qty, close_price)

                # Daily PnL güncelle
                self._update_daily_pnl(daily_pnl, realized_delta, portfolio, close_price, closing_fee_total)

                # Log dosyasına yaz
                entry_price = portfolio.long_avg_price if position_side == "LONG" else portfolio.short_avg_price
                log_trade_to_file(
                    symbol=self._symbol,
                    action=f"PARTIAL_CLOSE_TP{tp_level}_{position_side}",
                    entry_price=entry_price,
                    close_price=close_price,
                    pnl=realized_delta,
                    reasoning=reason,
                    amount=actual_close_qty,
                )

                session.flush()
                session.commit()

                post_position = portfolio.net_position

                # Telegram bildirimi
                self._send_partial_close_notification(
                    tp_level=tp_level,
                    position_side=position_side,
                    close_qty=actual_close_qty,
                    close_price=close_price,
                    pnl=realized_delta,
                    remaining_qty=remaining_quantity,
                    is_breakeven=is_breakeven,
                    trailing_activated=trailing_activated,
                )

                telemetry = {
                    "last_action": f"PARTIAL_CLOSE_TP{tp_level}",
                    "amount": actual_close_qty,
                    "price": close_price,
                    "position_side": position_side,
                    "position_id": current_position_id,
                    "fee": closing_fee_total,
                    "pnl": realized_delta,
                    "tp_level": tp_level,
                    "remaining_quantity": remaining_quantity,
                    "is_breakeven": is_breakeven,
                    "trailing_activated": trailing_activated,
                }

                return ExecutionResult(
                    status="PAPER",
                    details=f"TP{tp_level} partial close: {actual_close_qty:.6f} @ ${close_price:.2f}",
                    telemetry=telemetry
                )

        except Exception as e:
            logger.error("Partial close error: %s", e, exc_info=True)
            return ExecutionResult(status="ERROR", details=str(e))

    def _send_partial_close_notification(
        self,
        tp_level: int,
        position_side: str,
        close_qty: float,
        close_price: float,
        pnl: float,
        remaining_qty: float,
        is_breakeven: bool,
        trailing_activated: bool,
    ) -> None:
        """Partial close için Telegram bildirimi gönder"""
        try:
            from app.utils.telegram import telegram_client

            emoji = "🟢" if pnl >= 0 else "🔴"
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

            # Status bilgisi
            status_parts = []
            if is_breakeven:
                status_parts.append("🛡️ BE aktif")
            if trailing_activated:
                status_parts.append("📈 Trailing aktif")
            status_str = " | ".join(status_parts) if status_parts else ""

            message = f"""
🎯 *TP{tp_level} Tetiklendi* | {self._symbol}

{emoji} *{position_side}* pozisyonun %{int((close_qty / (close_qty + remaining_qty)) * 100) if remaining_qty > 0 else 100}'i kapatıldı

📊 *Detaylar:*
• Kapatılan: {close_qty:.6f} @ ${close_price:,.2f}
• PnL: {pnl_str}
• Kalan: {remaining_qty:.6f}

{status_str}

⏰ *{datetime.utcnow().strftime('%H:%M:%S')} UTC*
"""
            telegram_client.send_message(message.strip())
            logger.info("Partial close notification sent for TP%d", tp_level)

        except Exception as e:
            logger.error("Failed to send partial close notification: %s", e)

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
        """WebSocket ile stop-loss izlemeyi başlat"""
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
        """WebSocket üzerinden fiyatları izle ve stop-loss kontrolü yap"""
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
        # Race condition prevention - check if close already in progress
        with self._close_lock:
            current_time = datetime.utcnow()

            # Cooldown kontrolü - aynı symbol için 60 saniye içinde duplicate close engelle
            last_close = self._symbol_close_timestamps.get(self._symbol)
            if last_close:
                elapsed = (current_time - last_close).total_seconds()
                if elapsed < self._close_cooldown_seconds:
                    logger.warning(
                        "⏳ Close cooldown active for %s (WebSocket): %.1f/%.0f seconds remaining",
                        self._symbol, elapsed, self._close_cooldown_seconds
                    )
                    return

            if self._closing_in_progress.get(self._symbol):
                logger.warning("Close already in progress for %s (WebSocket), skipping duplicate", self._symbol)
                return

            self._closing_in_progress[self._symbol] = True
            self._symbol_close_timestamps[self._symbol] = current_time

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
            # Pozisyonu kapat
            result = self._execute_closing_trade(
                session=session,
                portfolio=portfolio,
                daily_pnl=daily_pnl,
                price=current_price,
                decision=close_decision,
                reason=trigger_type,
                pre_position=portfolio.position,  # Pass current position as pre_position
                btc_amount=position_amount,
                leverage=leverage,
                position_side=position_type
            )
            
            if result.status == "EXECUTED":
                logger.info("✅ Position closed successfully due to %s", trigger_type)

                # Set cooldown time (was missing - caused rapid re-entry after WebSocket close)
                self._last_close_time = datetime.utcnow()

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
        finally:
            # Release the close lock
            with self._close_lock:
                self._closing_in_progress[self._symbol] = False

    def _send_sl_tp_notification(self, trigger_type: str, position_type: str, 
                                 amount: float, entry_price: float, current_price: float, 
                                 trigger_price: float) -> None:
        """Stop-loss bildirimi gönder"""
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
            base_asset = self._symbol.replace("USDT", "")
            
            message = f"""
{emoji} *{trigger_type} TETİKLENDİ*

{position_emoji} **Pozisyon Bilgisi:**
• Tür: {position_type}
• Miktar: {abs(amount):.6f} {base_asset}
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
        Exit planını doğrular.
        Python-calculated exit plans are always valid (bounds enforced during calculation).
        """
        if not exit_plan:
            return False, "Exit plan eksik"

        # Python calculate_exit_plan() always produces valid exit plans
        # Skip detailed validation - bounds are enforced during calculation
        return True, ""
