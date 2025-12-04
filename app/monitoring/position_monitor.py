"""
Position Monitor - 3 dakikalık mum kapanışlarında pozisyon takibi

Her 3 dakikada bir (3-minute candle close) şu kontrolleri yapar:
1. Profit Target: Fiyat hedef seviyesine ulaştı mı?
2. Stop Loss: Fiyat stop seviyesine düştü mü?
3. Invalidation Condition: Özel koşul gerçekleşti mi? (örn: "closes below X on 3m candle")

Bu 3 koşuldan biri sağlandığında pozisyon otomatik olarak kapatılır.
Hiçbiri sağlanmadığı sürece pozisyon açık kalır.
"""
import asyncio
import functools
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.executor.ledger import engine, get_open_position_with_exit_plan
from app.monitoring.exit_checker import (
    check_all_exit_conditions,
    check_invalidation_condition,
    check_stop_loss,
)
from app.risk_manager.time_exit_manager import TimeBasedExitManager, ExitType
from app.risk_manager.trailing_stop import AdvancedTrailingStop, TrailingStopType
from app.risk_manager.breakeven_manager import BreakevenManager
from app.risk_manager.partial_tp_manager import PartialTakeProfitManager, TPStatus
from app.utils.logging import get_logger
from app.utils.price_cache import price_cache
from app.utils.telegram import telegram_client

# Forward reference for type hint
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.monitoring.crash_protection import CrashProtectionHandler


logger = get_logger(__name__)


class PositionMonitor:
    """3 dakikalık mum kapanışlarında pozisyon takibi"""
    
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        interval_seconds: int = 180,  # 3 dakika (geri alındı)
        executor = None,  # Executor instance (pozisyon kapatmak için)
        enable_telegram: bool = True,
        glm_client = None,  # GLM client for dynamic exit plan updates
        crash_handler: "CrashProtectionHandler" = None,  # Crash protection handler
    ):
        self._symbol = symbol
        self._interval = interval_seconds
        self._executor = executor
        self._running = False
        self._enable_telegram = enable_telegram
        self._glm_client = glm_client
        self._dynamic_exit_updater = None
        self._crash_handler = crash_handler  # For position state sync

        # Time-based exit manager (max hold time, stagnation, weekend risk enforcement)
        self._time_exit_manager = TimeBasedExitManager(mode="swing", weekend_rule_enabled=True)

        # Trailing Stop manager (ATR_BASED strategy)
        self._trailing_stop_manager = AdvancedTrailingStop(mode="swing", feature_enabled=True)

        # Breakeven manager (move SL to entry after 1.5R profit)
        self._breakeven_manager = BreakevenManager(mode="swing", feature_enabled=True)

        # Partial Take Profit manager (kademeli kar alma)
        self._partial_tp_manager = PartialTakeProfitManager(mode="swing", feature_enabled=True)

        # Duplicate close prevention - track recently closed position IDs
        self._recently_closed_positions: set = set()

        logger.info(
            "Position Monitor initialized | symbol=%s interval=%ds trailing_stop=%s breakeven=%s time_exit=%s",
            self._symbol,
            self._interval,
            True,  # trailing_stop enabled
            True,  # breakeven enabled
            True,  # time_exit enabled
        )
    
    async def start(self) -> None:
        """Ana monitor loop'u başlat"""
        self._running = True
        logger.info("Position Monitor started (3-minute interval checks)")
        
        # ⏳ WARMUP DELAY: WebSocket listener'ların ilk fiyatı alması için 5 saniye bekle
        # Bu race condition'ı önler (listener başladı ama henüz Redis'ten mesaj almadı)
        logger.info("⏳ Waiting 5 seconds for websocket listeners to warm up...")
        await asyncio.sleep(5)
        logger.info("✅ Warmup complete, starting position monitoring")
        
        try:
            # Invalidation condition için WebSocket izleyiciyi başlat
            invalidation_task = asyncio.create_task(self._monitor_invalidation_conditions())
            
            # Ana monitor döngüsü
            while self._running:
                await self._monitor_cycle()
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            logger.info("Position Monitor cancelled")
            raise
        except Exception as exc:
            logger.error("Position Monitor error: %s", exc, exc_info=True)
            raise
    
    def stop(self) -> None:
        """Monitor'u durdur"""
        self._running = False
        logger.info("Position Monitor stopped")
    
    def _get_interval_str(self) -> str:
        """Convert interval seconds to Binance WebSocket interval string"""
        if self._interval == 60:
            return "1m"
        elif self._interval == 180:
            return "3m"
        elif self._interval == 300:
            return "5m"
        elif self._interval == 900:
            return "15m"
        elif self._interval == 1800:
            return "30m"
        elif self._interval == 3600:
            return "1h"
        elif self._interval == 14400:
            return "4h"
        else:
            # Fallback to 1m if unknown
            logger.warning("Unknown interval seconds: %s, defaulting to 1m", self._interval)
            return "1m"

    async def _monitor_invalidation_conditions(self) -> None:
        """Invalidation condition'ları 15m mumda gerçek zamanlı izle

        NOT: Invalidation condition'lar 15m candle üzerinden kontrol edilir.
        Bu, yanlış erken çıkışları önler. Stop loss kontrolü 3m'de devam eder.
        """
        try:
            from app.data_feeds.binance_ws import BinanceWebSocketClient

            # DÜZELTME: Invalidation için 15m candle kullan (nof1_prompt_builder ile tutarlı)
            invalidation_interval = "15m"

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
                    is_closed = kline.get("x", False)  # Kapanış mı?

                    # DEBUG: Log kline details for debugging "Wrong Price" issues
                    if is_closed:
                        event_time = payload.get("E", 0)
                        symbol = payload.get("s", "UNKNOWN")
                        open_price = float(kline.get("o", 0.0))
                        high_price = float(kline.get("h", 0.0))
                        low_price = float(kline.get("l", 0.0))

                        logger.info(
                            "🕐 15m Candle Close | symbol=%s o=%.2f h=%.2f l=%.2f c=%.2f time=%s",
                            symbol,
                            open_price,
                            high_price,
                            low_price,
                            close_price,
                            datetime.fromtimestamp(event_time / 1000.0).isoformat() if event_time else "N/A",
                        )

                        # Cache durumu ile karşılaştır (aynı sembol için)
                        try:
                            snapshot = price_cache.get_snapshot(symbol)
                            if snapshot is not None and getattr(snapshot, "price", 0.0) > 0:
                                cache_price = snapshot.price
                                cache_source = getattr(snapshot, "source", "unknown")
                                cache_age = snapshot.age_seconds()
                                diff_pct = abs(close_price - cache_price) / cache_price * 100.0
                                logger.debug(
                                    "🔎 PriceCache Snapshot | symbol=%s price=%.2f source=%s age=%.1fs diff_vs_ws=%.2f%%",
                                    symbol,
                                    cache_price,
                                    cache_source,
                                    cache_age,
                                    diff_pct,
                                )
                            else:
                                logger.debug(
                                    "🔎 PriceCache Snapshot | symbol=%s snapshot=%s (no valid cache)",
                                    symbol,
                                    "None" if snapshot is None else "invalid",
                                )
                        except Exception as cache_exc:  # noqa: BLE001
                            logger.warning(
                                "⚠️ Failed to inspect price cache for %s in invalidation monitor: %s",
                                symbol,
                                cache_exc,
                            )

                    if close_price > 0 and is_closed:
                        # 15m mum kapandığında invalidation condition'ları kontrol et
                        asyncio.create_task(self._check_invalidation_conditions_realtime(close_price, "15m"))
                except Exception as e:
                    logger.error("Error in invalidation handler: %s", e)

            logger.info("🕐 Starting 15m candle invalidation monitor for %s", self._symbol)
            ws_client = BinanceWebSocketClient(self._symbol, invalidation_interval)
            await ws_client.listen(price_handler)

        except Exception as e:
            logger.error("WebSocket monitoring error: %s", e)
    
    async def _monitor_cycle(self) -> None:
        """Tek bir kontrol döngüsü"""
        try:
            # 1. Açık pozisyon var mı?
            position_info = await self._get_open_position()
            
            if position_info is None:
                logger.debug("No open position, skipping monitor cycle")
                # Update crash handler: no position to protect
                if self._crash_handler:
                    self._crash_handler.update_position_state(
                        has_position=False,
                        position_side=None,
                        entry_price=0.0,
                        quantity=0.0,
                    )
                return

            # FIX: Pozisyonun gerçek sembolünü kullan (örn: BTCUSDT monitörü ETHUSDT pozisyonu bulabilir)
            symbol = position_info.get("symbol", self._symbol)

            # Update crash handler with current position state for real-time protection
            if self._crash_handler:
                self._crash_handler.update_position_state(
                    has_position=True,
                    position_side="LONG" if position_info.get("is_long") else "SHORT",
                    entry_price=position_info.get("entry_price", 0.0),
                    quantity=position_info.get("quantity", 0.0),
                )
                logger.debug(
                    "🛡️ Crash handler state updated | symbol=%s side=%s entry=%.2f qty=%.6f",
                    symbol,
                    "LONG" if position_info.get("is_long") else "SHORT",
                    position_info.get("entry_price", 0.0),
                    position_info.get("quantity", 0.0),
                )
            
            # DEBUG: Log symbol resolution
            logger.debug(
                "🔍 Monitor Cycle | Self Symbol: %s | Position Symbol: %s | Resolved: %s",
                self._symbol, position_info.get("symbol"), symbol
            )

            # DEBUG: Aktif pozisyon bağlamını logla
            try:
                logger.debug(
                    "🔍 Monitor Position Context | symbol=%s position_id=%s side=%s entry=%.2f qty=%.6f",
                    symbol,
                    position_info.get("position_id"),
                    "LONG" if position_info.get("is_long") else "SHORT",
                    position_info.get("entry_price", 0.0),
                    position_info.get("quantity", 0.0),
                )
            except Exception:
                # Savunmacı: logging hiçbir durumda monitor döngüsünü bozmasın
                logger.debug("🔍 Monitor Position Context logging failed", exc_info=True)

            # 2.0. TIME-BASED EXIT CHECK (ENFORCEMENT - Hard Stop)
            # Bu kontrol diğer tüm exit kontrollerinden önce yapılır
            try:
                entry_time_str = position_info.get("open_time")
                if entry_time_str:
                    # Eğer datetime objesi ise string'e çevir
                    if hasattr(entry_time_str, 'isoformat'):
                        entry_time_str = entry_time_str.isoformat()

                    current_time_str = datetime.utcnow().isoformat()
                    entry_price = position_info.get("entry_price", 0)

                    # Basit fiyat değişimi hesapla (current_price henüz alınmadı, cache'den hızlı al)
                    snapshot = price_cache.get_snapshot(symbol)
                    quick_price = getattr(snapshot, "price", entry_price) if snapshot else entry_price
                    price_change_pct = ((quick_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

                    # Unrealized PnL hesapla
                    is_long = position_info.get("is_long", True)
                    if is_long:
                        unrealized_pnl_pct = price_change_pct
                    else:
                        unrealized_pnl_pct = -price_change_pct

                    time_result = self._time_exit_manager.check_time_based_exit(
                        entry_time=entry_time_str,
                        current_time=current_time_str,
                        unrealized_pnl_pct=unrealized_pnl_pct,
                        price_change_since_entry_pct=abs(price_change_pct),
                    )

                    if time_result.should_exit:
                        logger.warning(
                            "⏰ TIME-BASED EXIT TRIGGERED | type=%s | urgency=%s | held=%.1fh | reason=%s",
                            time_result.exit_type.value,
                            time_result.urgency.value,
                            time_result.hours_held,
                            time_result.reason,
                        )

                        # Pozisyonu hemen kapat
                        await self._close_position(
                            position_id=position_info.get("position_id"),
                            trigger_type=f"time_exit_{time_result.exit_type.value.lower()}",
                            reason=time_result.reason,
                            current_price=quick_price,
                            is_long=is_long,
                            quantity=position_info.get("quantity", 0),
                            entry_price=entry_price,
                            symbol=symbol
                        )
                        return  # Diğer kontrolleri atla
                    elif time_result.exit_type == ExitType.APPROACHING_LIMIT:
                        logger.info(
                            "⚠️ TIME WARNING | %s | %.1fh / %.1fh remaining",
                            time_result.reason,
                            time_result.hours_held,
                            time_result.hours_remaining,
                        )
            except Exception as time_exc:
                logger.error("Time-based exit check failed: %s", time_exc, exc_info=True)
                # Time check hatası diğer kontrolleri engellemez

            # 2. Current price al (doğrudan WebSocket)
            current_price = await self._get_current_price(symbol=symbol, confirm_with_rest=False)
            
            if current_price is None:
                logger.warning("Could not fetch current price for %s, skipping cycle", symbol)
                return
            
            logger.debug("💰 Current Price for %s: %.2f", symbol, current_price)

            # 2.5. TRAILING STOP & BREAKEVEN UPDATE
            # Bu kontroller pozisyon açılışında kaydedilmiş olmalı
            try:
                entry_price_ts = position_info.get("entry_price", 0)
                is_long_ts = position_info.get("is_long", True)
                position_side_ts = "LONG" if is_long_ts else "SHORT"
                stop_loss_ts = position_info.get("exit_plan", {}).get("stop_loss", 0)

                # Eğer pozisyon henüz kayıtlı değilse, kaydet (lazy registration)
                if symbol not in self._trailing_stop_manager.active_trails:
                    if entry_price_ts > 0 and stop_loss_ts > 0:
                        # ATR'yi hesapla (basit tahmin: SL distance / 2)
                        if is_long_ts:
                            atr_estimate = (entry_price_ts - stop_loss_ts) / 2
                        else:
                            atr_estimate = (stop_loss_ts - entry_price_ts) / 2

                        self._trailing_stop_manager.create_trailing_stop(
                            symbol=symbol,
                            entry_price=entry_price_ts,
                            position_side=position_side_ts,
                            initial_stop=stop_loss_ts,
                            trail_type=TrailingStopType.ATR_BASED,
                            atr_value=atr_estimate,
                        )
                        logger.info(
                            "📈 Trailing stop registered for %s | entry=%.2f | SL=%.2f | ATR≈%.2f",
                            symbol, entry_price_ts, stop_loss_ts, atr_estimate
                        )

                if symbol not in self._breakeven_manager.active_positions:
                    if entry_price_ts > 0 and stop_loss_ts > 0:
                        self._breakeven_manager.register_position(
                            symbol=symbol,
                            entry_price=entry_price_ts,
                            stop_loss=stop_loss_ts,
                            side=position_side_ts,
                        )
                        logger.info(
                            "🔒 Breakeven manager registered for %s | entry=%.2f | SL=%.2f",
                            symbol, entry_price_ts, stop_loss_ts
                        )

                # Trailing Stop Update
                trail_result = self._trailing_stop_manager.update(
                    symbol=symbol,
                    current_price=current_price,
                    current_high=current_price,  # Simplified: use current price
                    current_low=current_price,
                    current_atr=None,  # Uses stored ATR
                )

                if trail_result:
                    if trail_result.get("triggered"):
                        logger.warning(
                            "🎯 TRAILING STOP HIT | %s | trigger_price=%.2f",
                            symbol, trail_result.get("trigger_price", 0)
                        )
                        await self._close_position(
                            position_id=position_info.get("position_id"),
                            trigger_type="trailing_stop",
                            reason=f"Trailing stop triggered at {trail_result.get('trigger_price', 0):.2f}",
                            current_price=current_price,
                            is_long=is_long_ts,
                            quantity=position_info.get("quantity", 0),
                            entry_price=entry_price_ts,
                            symbol=symbol
                        )
                        return  # Exit - pozisyon kapandı

                    if trail_result.get("updated"):
                        new_stop = trail_result.get("new_stop", 0)
                        old_stop = trail_result.get("old_stop", 0)
                        logger.info(
                            "📈 TRAILING STOP MOVED | %s | %.2f → %.2f",
                            symbol, old_stop, new_stop
                        )
                        # Database'deki stop_loss'u güncelle
                        await self._update_stop_loss_in_db(
                            position_id=position_info.get("position_id"),
                            new_stop=new_stop,
                            reason="trailing_stop_update"
                        )

                # Breakeven Check (trailing stop aktif değilse)
                be_result = self._breakeven_manager.check_and_update(
                    symbol=symbol,
                    current_price=current_price,
                )

                if be_result and be_result.get("action") == "MOVE_TO_BREAKEVEN":
                    new_stop_be = be_result.get("new_stop", 0)
                    logger.info(
                        "🔒 BREAKEVEN ACTIVATED | %s | SL moved to %.2f (entry + buffer)",
                        symbol, new_stop_be
                    )
                    await self._update_stop_loss_in_db(
                        position_id=position_info.get("position_id"),
                        new_stop=new_stop_be,
                        reason="breakeven_activation"
                    )

            except Exception as trail_exc:
                logger.error("Trailing stop/breakeven update failed: %s", trail_exc, exc_info=True)
                # Hata olsa da diğer kontrollere devam et

            # 2.6. PARTIAL TAKE PROFIT CHECK
            # Kademeli kar alma - TP seviyeleri kontrol et
            try:
                await self._check_partial_tp_levels(
                    symbol=symbol,
                    position_info=position_info,
                    current_price=current_price,
                )
            except Exception as tp_exc:
                logger.error("Partial TP check failed: %s", tp_exc, exc_info=True)
                # Hata olsa da diğer kontrollere devam et

            # 2.7. [YENİ] Dinamik exit plan güncellemesi değerlendir
            try:
                await self._evaluate_dynamic_exit_plan_update(position_info, current_price)
            except Exception as e:
                logger.error("Dynamic exit plan update failed: %s", e, exc_info=True)

            # 3. Pozisyon verilerini hazırla
            entry_price = position_info["entry_price"]
            is_long = position_info["is_long"]
            exit_plan = position_info["exit_plan"]
            position_id = position_info["position_id"]

            if not position_id:
                logger.warning("⚠️ Position ID is missing for open position on %s", symbol)

            quantity = position_info["quantity"]

            interval_str = self._get_interval_str()

            # NOT: Invalidation condition artık 15m WebSocket listener tarafından kontrol ediliyor
            # (bkz: _monitor_invalidation_conditions). Bu döngü sadece stop loss kontrolü yapar.
            # Bu sayede 15m candle koşulu yanlışlıkla 3m'de tetiklenmez.

            # 4. Stop loss kontrolü (invalidation hariç)
            should_close, trigger_type, reason = check_all_exit_conditions(
                entry_price=entry_price,
                current_price=current_price,
                exit_plan=exit_plan,
                is_long=is_long,
                interval_label=interval_str
            )
            
            if should_close:
                logger.info(
                    "🔔 EXIT CONDITION TRIGGERED | type=%s | %s",
                    trigger_type,
                    reason
                )
                
                # DOĞRUDAN WebSocket fiyatı kullan - REST validasyonu kaldırıldı
                if trigger_type == "stop_loss":
                    reason = f"Stop loss triggered: {exit_plan.get('stop_loss', 0):.2f}"
                
                # Pozisyonu kapat
                await self._close_position(
                    position_id=position_id,
                    trigger_type=trigger_type,
                    reason=reason,
                    current_price=current_price,
                    is_long=is_long,
                    quantity=quantity,
                    entry_price=entry_price,
                    symbol=symbol
                )
            else:
                logger.debug("No exit conditions met, position remains open")
        
        except Exception as exc:
            logger.error("Monitor cycle failed: %s", exc, exc_info=True)
            # Don't stop monitor on error, continue to next cycle
    
    async def _get_open_position(self) -> Optional[dict]:
        """Açık pozisyonu database'den al"""
        try:
            with Session(engine) as session:
                position_info = get_open_position_with_exit_plan(session, self._symbol)
                return position_info
        except Exception as exc:
            logger.error("Failed to get open position: %s", exc)
            return None
    
    async def _get_current_price(self, symbol: Optional[str] = None, confirm_with_rest: bool = False) -> Optional[float]:
        """WebSocket kline fiyatını cache'den güvenli şekilde al.

        Sadece Binance WebSocket'ten gelen ve hâlâ geçerli olan fiyatlar
        kullanılır. REST / diğer kaynaklardan gelen fiyatlar INVALIDATION
        ve exit kontrollerinde YOK sayılır.
        """
        target_symbol = symbol or self._symbol

        snapshot = price_cache.get_snapshot(target_symbol)

        if not snapshot:
            logger.warning(
                "❌ No price snapshot available for %s - price cache empty", target_symbol
            )
            return None

        # Fiyat çok eski veya mantıksız ise kullanma
        try:
            if hasattr(snapshot, "is_stale") and snapshot.is_stale():
                logger.warning(
                    "⚠️ Stale price snapshot ignored for %s | age=%.1fs | source=%s",
                    target_symbol,
                    snapshot.age_seconds() if hasattr(snapshot, "age_seconds") else -1.0,
                    getattr(snapshot, "source", "unknown"),
                )
                return None

            if hasattr(snapshot, "is_valid") and not snapshot.is_valid():
                logger.warning(
                    "⚠️ Invalid price snapshot ignored for %s | price=%.4f | source=%s",
                    target_symbol,
                    getattr(snapshot, "price", 0.0),
                    getattr(snapshot, "source", "unknown"),
                )
                return None
        except Exception as exc:  # Savunmacı kontrol
            logger.warning("⚠️ Failed to validate price snapshot for %s: %s", target_symbol, exc)
            return None

        # Sadece gerçek WebSocket (kline) kaynağını kabul et
        source = getattr(snapshot, "source", "unknown")
        allowed_sources = {"binance_websocket", "websocket"}
        if source not in allowed_sources:
            logger.warning(
                "⚠️ Ignoring non-websocket price for monitor checks | symbol=%s | source=%s | price=%.4f",
                target_symbol,
                source,
                getattr(snapshot, "price", 0.0),
            )
            return None

        # Buraya kadar geldiysek snapshot geçerli ve kabul edilebilir
        try:
            logger.debug(
                "💾 Using price snapshot for monitor | symbol=%s price=%.4f source=%s age=%.1fs",
                target_symbol,
                getattr(snapshot, "price", 0.0),
                getattr(snapshot, "source", "unknown"),
                snapshot.age_seconds() if hasattr(snapshot, "age_seconds") else -1.0,
            )
        except Exception:
            logger.debug("Failed to log price snapshot details for %s", target_symbol, exc_info=True)

        return getattr(snapshot, "price", None)
    
    # REST API fonksiyonları kaldırıldı - doğrudan WebSocket kullanılıyor
    
    async def _check_invalidation_conditions_realtime(self, current_price: float, interval: str = "15m") -> None:
        """Invalidation condition'ları gerçek zamanlı kontrol et

        Args:
            current_price: Mum kapanış fiyatı
            interval: Kontrol edilen mum intervali (örn: "15m")
                      Sadece exit_plan'daki invalidation_timeframe ile eşleşirse kontrol edilir.
        """
        try:
            position_info = await self._get_open_position()

            if position_info is None:
                return

            entry_price = position_info["entry_price"]
            is_long = position_info["is_long"]
            exit_plan = position_info["exit_plan"]
            position_id = position_info["position_id"]
            quantity = position_info["quantity"]
            symbol = position_info.get("symbol", self._symbol)

            # Sadece invalidation condition'ı kontrol et
            invalidation_condition = exit_plan.get("invalidation_condition", "")
            if not invalidation_condition or invalidation_condition == "N/A":
                return

            # DÜZELTME: Timeframe eşleştirmesi - sadece doğru interval'de kontrol et
            required_timeframe = exit_plan.get("invalidation_timeframe", "15m")
            if interval != required_timeframe:
                logger.debug(
                    "⏭️ Skipping invalidation check | received=%s required=%s",
                    interval,
                    required_timeframe,
                )
                return

            triggered, reason = check_invalidation_condition(
                current_price=current_price,
                invalidation_condition=invalidation_condition,
                is_long=is_long,
                interval_label=interval
            )

            if triggered:
                # DOĞRUDAN WebSocket fiyatı kullan - REST validasyonu kaldırıldı

                try:
                    logger.info(
                        "📉 Realtime invalidation context | symbol=%s position_id=%s side=%s entry=%.2f qty=%.6f price=%.2f condition=%s interval=%s",
                        symbol,
                        position_id,
                        "LONG" if is_long else "SHORT",
                        entry_price,
                        quantity,
                        current_price,
                        invalidation_condition,
                        interval,
                    )
                except Exception:
                    logger.debug("Failed to log realtime invalidation context", exc_info=True)

                logger.warning(
                    "🚨 INVALIDATION CONDITION TRIGGERED (15m CANDLE) | %s",
                    reason
                )

                # Pozisyonu hemen kapat
                await self._close_position(
                    position_id=position_id,
                    trigger_type="invalidation",
                    reason=reason,
                    current_price=current_price,
                    is_long=is_long,
                    quantity=quantity,
                    entry_price=entry_price,
                    symbol=symbol
                )

        except Exception as exc:
            logger.error("Error checking invalidation conditions: %s", exc)
    
    async def _close_position(
        self,
        position_id: str,
        trigger_type: str,
        reason: str,
        current_price: float,
        is_long: bool,
        quantity: float,
        entry_price: float,
        symbol: Optional[str] = None
    ) -> None:
        """Pozisyonu kapat ve bildirim gönder"""
        try:
            symbol_to_use = symbol or self._symbol

            # Duplicate close prevention - skip if already closed recently
            if position_id in self._recently_closed_positions:
                logger.warning(
                    "⚠️ DUPLICATE CLOSE BLOCKED | position_id=%s | Already closed in this session",
                    position_id
                )
                return

            # Trailing stop ve breakeven manager'lardan temizlik yap
            self._cleanup_trailing_managers(symbol_to_use)

            # Kapanışta kullanılan fiyat kaynağını logla (debug için)
            price_source = "unknown"
            try:
                snapshot = price_cache.get_snapshot(symbol_to_use)
                if snapshot is not None:
                    price_source = getattr(snapshot, "source", "unknown")
            except Exception:
                price_source = "error"

            logger.info(
                "PositionMonitor closing position | position_id=%s | trigger=%s | exit_price=%.2f | price_source=%s",
                position_id,
                trigger_type,
                current_price,
                price_source,
            )

            # Ek bağlam: snapshot fiyatı ve farkı
            try:
                if snapshot is not None and getattr(snapshot, "price", 0.0) > 0:
                    cache_price = snapshot.price
                    cache_age = snapshot.age_seconds() if hasattr(snapshot, "age_seconds") else -1.0
                    diff_pct = abs(current_price - cache_price) / cache_price * 100.0
                    logger.debug(
                        "📊 Close Snapshot Context | symbol=%s position_id=%s exit_price=%.2f cache_price=%.2f source=%s age=%.1fs diff=%.2f%%",
                        symbol_to_use,
                        position_id,
                        current_price,
                        cache_price,
                        getattr(snapshot, "source", "unknown"),
                        cache_age,
                        diff_pct,
                    )
                else:
                    logger.debug(
                        "📊 Close Snapshot Context | symbol=%s position_id=%s exit_price=%.2f snapshot=%s",
                        symbol_to_use,
                        position_id,
                        current_price,
                        "None" if snapshot is None else "invalid",
                    )
            except Exception:
                logger.debug("Failed to log close snapshot context for %s", symbol_to_use, exc_info=True)
            # Executor'a close komutu gönder
            if self._executor:
                logger.info(
                    "Closing position via executor | position_id=%s trigger=%s",
                    position_id,
                    trigger_type
                )
                
                # Executor'un close_position_by_exit_plan metodunu çağır
                result = await asyncio.to_thread(
                    self._executor.close_position_by_exit_plan,
                    reason=reason,
                    trigger_type=trigger_type,
                    exit_price=current_price  # Gerçek kapanış fiyatını gönder
                )
                
                logger.info("Position closed | result=%s", result.status)

                # Mark position as closed to prevent duplicates
                if result.status == "PAPER":
                    self._recently_closed_positions.add(position_id)
                    logger.info("✅ Position marked as closed: %s (preventing duplicates)", position_id)

                # Calculate PnL
                pnl = (current_price - entry_price) * quantity if is_long else (entry_price - current_price) * quantity
                pnl_pct = ((current_price - entry_price) / entry_price * 100) if is_long else ((entry_price - current_price) / entry_price * 100)
                
                # GLM'ye bildirim için Redis'e yaz
                await self._notify_glm_via_redis(
                    position_id=position_id,
                    trigger_type=trigger_type,
                    reason=reason,
                    entry_price=entry_price,
                    exit_price=current_price,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    is_long=is_long,
                    quantity=quantity,
                    symbol=symbol_to_use
                )
                
                # Telegram bildirimi
                if self._enable_telegram:
                    await self._send_telegram_notification_async(
                        position_id=position_id,
                        trigger_type=trigger_type,
                        reason=reason,
                        current_price=current_price,
                        is_long=is_long,
                        quantity=quantity,
                        entry_price=entry_price,
                        symbol=symbol_to_use
                    )
            else:
                logger.warning("No executor available, cannot close position")
        
        except Exception as exc:
            logger.error("Failed to close position: %s", exc, exc_info=True)
    
    async def _notify_glm_via_redis(
        self,
        position_id: str,
        trigger_type: str,
        reason: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
        is_long: bool,
        quantity: float,
        symbol: Optional[str] = None
    ) -> None:
        """
        GLM'ye position close bildirimini Redis üzerinden gönder
        
        GLM bir sonraki döngüde bu bildirimi okur ve pozisyonun otomatik
        kapandığını öğrenir.
        """
        try:
            import json
            from app.utils.redis import get_redis_client
            
            redis = get_redis_client()
            symbol_to_use = symbol or self._symbol
            
            # Notification data
            notification_data = {
                "timestamp": datetime.utcnow().isoformat(),
                "position_id": position_id,
                "trigger_type": trigger_type,
                "reason": reason,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "position_type": "LONG" if is_long else "SHORT",
                "quantity": quantity,
                "symbol": symbol_to_use,
            }
            
            # Redis key: position_closed:{symbol}
            redis_key = f"position_closed:{symbol_to_use}"
            
            # 10 dakika TTL (GLM bir sonraki döngüde okur)
            # 3 dakikalık cycle + buffer = 10 dakika yeterli
            ttl_seconds = 600
            
            try:
                redis.setex(
                    redis_key,
                    ttl_seconds,
                    json.dumps(notification_data)
                )
                
                logger.info(
                    "✅ GLM notification sent via Redis | key=%s | trigger=%s | ttl=%ds",
                    redis_key,
                    trigger_type,
                    ttl_seconds
                )
            except Exception as e:
                # Read-only replica hatasını yakala ve warning olarak logla
                error_str = str(e).lower()
                if "read only" in error_str:
                    logger.warning(
                        "⚠️ GLM notification skipped: Redis is in READ-ONLY mode (Replica?). Check Redis config. (key=%s)", 
                        redis_key
                    )
                else:
                    logger.error(
                        "❌ Failed to send GLM notification via Redis: %s", 
                        e
                    )
        
        except Exception as exc:
            logger.error("Failed to send GLM notification via Redis (general): %s", exc)
            # Don't raise - notification failure shouldn't block position closing
    
    async def _send_telegram_notification_async(
        self,
        position_id: str,
        trigger_type: str,
        reason: str,
        current_price: float,
        is_long: bool,
        quantity: float,
        entry_price: float,
        symbol: Optional[str] = None
    ) -> None:
        """Telegram bildirimi gönder (Asenkron wrapper)"""
        try:
            pnl = (current_price - entry_price) * quantity if is_long else (entry_price - current_price) * quantity
            pnl_pct = ((current_price - entry_price) / entry_price * 100) if is_long else ((entry_price - current_price) / entry_price * 100)
            symbol_to_use = (symbol or self._symbol).upper()
            
            emoji_map = {
                "profit_target": "🎯",
                "stop_loss": "🛑",
                "invalidation": "⚠️"
            }
            emoji = emoji_map.get(trigger_type, "🔔")
            
            # Base asset'i bul (örn: BTCUSDT -> BTC)
            if "USDT" in symbol_to_use:
                base_asset = symbol_to_use.replace("USDT", "")
            elif "USD" in symbol_to_use:
                base_asset = symbol_to_use.replace("USD", "")
            else:
                base_asset = symbol_to_use
            
            # Safety check: If symbol is ETHUSDT but base_asset became BTC (impossible via replace, but good to log)
            if "ETH" in symbol_to_use and "BTC" in base_asset:
                logger.error("CRITICAL: Symbol/BaseAsset mismatch! Symbol: %s, Base: %s", symbol_to_use, base_asset)
                base_asset = "ETH" # Force fix
            
            # Safety check for SOL
            if "SOL" in symbol_to_use and "BTC" in base_asset:
                logger.error("CRITICAL: Symbol/BaseAsset mismatch! Symbol: %s, Base: %s", symbol_to_use, base_asset)
                base_asset = "SOL" # Force fix
            
            logger.info(
                "📨 Sending Telegram Alert | Symbol: %s | Base Asset: %s | Price: %.2f",
                symbol_to_use, base_asset, current_price
            )
            
            message = (
                f"{emoji} *Position Monitor Alert*\n\n"
                f"*Position ID:* `{position_id}`\n"
                f"*Type:* {trigger_type.upper().replace('_', ' ')}\n"
                f"*Symbol:* {symbol_to_use}\n"
                f"*Side:* {'LONG' if is_long else 'SHORT'}\n"
                f"*Quantity:* {quantity:.6f} {base_asset}\n\n"
                f"*Entry:* ${entry_price:.2f}\n"
                f"*Exit:* ${current_price:.2f}\n"
                f"*PnL:* ${pnl:.2f} ({pnl_pct:+.2f}%)\n\n"
                f"*Reason:* {reason}\n"
                f"*Time:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
                f"✅ *GLM Notification:* Sent (will be visible in next cycle)"
            )
            
            # Sync fonksiyonu executor'da çalıştır
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, 
                functools.partial(telegram_client.send_message, message)
            )
            logger.info("Telegram notification sent for position close")
        
        except Exception as exc:
            logger.error("Failed to send telegram notification: %s", exc)
    
    async def _evaluate_dynamic_exit_plan_update(
        self,
        position_info: dict,
        current_price: float
    ) -> None:
        """Dinamik exit plan güncellemesi değerlendir ve uygula"""
        if not self._glm_client:
            return
        
        if not self._dynamic_exit_updater:
            from app.risk_manager.dynamic_exit_updater import DynamicExitUpdater
            from sqlalchemy.orm import Session
            
            with Session(engine) as session:
                self._dynamic_exit_updater = DynamicExitUpdater(
                    glm_client=self._glm_client,
                    session=session
                )
        
        try:
            from app.executor.ledger import Trade
            from sqlalchemy.orm import Session
            
            with Session(engine) as session:
                trade = session.query(Trade).filter_by(
                    position_id=position_info['position_id'],
                    close_price=None
                ).first()
                
                if not trade:
                    logger.warning("Trade not found for dynamic update: %s", position_info['position_id'])
                    return
                
                initial_volatility = position_info.get('exit_plan', {}).get('volatility', 0.5)
                
                market_data = await self._get_market_data_for_update(current_price)
                
                position_data = {
                    'position_id': position_info['position_id'],
                    'entry_price': position_info['entry_price'],
                    'position_side': 'LONG' if position_info['is_long'] else 'SHORT',
                    'exit_plan': position_info['exit_plan'],
                    'exit_plan_history': trade.exit_plan_history or {'updates': []},
                    'timestamp': trade.timestamp,
                    'initial_volatility': initial_volatility
                }
                
                self._dynamic_exit_updater.session = session
                
                update_result = self._dynamic_exit_updater.process_position_update(
                    position_data=position_data,
                    market_data=market_data
                )
                
                if update_result:
                    logger.info(
                        "✅ Dynamic exit plan updated | position=%s | "
                        "old_sl=%.2f new_sl=%.2f | old_tp=%.2f new_tp=%.2f",
                        update_result['position_id'],
                        update_result['old_exit_plan'].get('stop_loss', 0),
                        update_result['new_exit_plan'].get('stop_loss', 0),
                        update_result['old_exit_plan'].get('profit_target', 0),
                        update_result['new_exit_plan'].get('profit_target', 0)
                    )
                    
                    if self._enable_telegram:
                        await asyncio.to_thread(self._send_exit_plan_update_notification_sync, update_result)
                
        except Exception as e:
            logger.error("Failed to evaluate dynamic exit plan update: %s", e, exc_info=True)
    
    async def _get_market_data_for_update(self, current_price: float) -> dict:
        """Dinamik güncelleme için piyasa verisi topla"""
        try:
            market_data = {
                'current_price': current_price,
                'current_volatility': 0.5,
                'trend_strength': 0.75,
                'rsi': 50,
                'momentum': 0.5
            }
            
            return market_data
            
        except Exception as e:
            logger.warning("Could not fetch full market data: %s", e)
            return {
                'current_price': current_price,
                'current_volatility': 0.5,
                'trend_strength': 0.75,
                'rsi': 50,
                'momentum': 0.5
            }
    
    def _send_exit_plan_update_notification_sync(self, update_result: dict) -> None:
        """Exit plan güncellemesi için Telegram bildirimi (Senkron)"""
        try:
            old_plan = update_result['old_exit_plan']
            new_plan = update_result['new_exit_plan']
            reasoning = update_result.get('reasoning', 'N/A')
            
            old_sl = old_plan.get('stop_loss', 0)
            new_sl = new_plan.get('stop_loss', 0)
            old_tp = old_plan.get('profit_target', 0)
            new_tp = new_plan.get('profit_target', 0)
            old_inv = old_plan.get('invalidation_condition', 'N/A')
            new_inv = new_plan.get('invalidation_condition', 'N/A')
            
            sl_change = "genişletildi" if new_sl < old_sl else "sıkılaştırıldı" if new_sl > old_sl else "değişmedi"
            tp_change = "yükseltildi" if new_tp > old_tp else "düşürüldü" if new_tp < old_tp else "değişmedi"
            
            message = (
                f"🔄 *Exit Plan Güncellendi*\n\n"
                f"*Position:* {update_result['position_id']}\n\n"
                f"📊 *Değişiklikler:*\n"
                f"  SL: ${old_sl:,.2f} → ${new_sl:,.2f} ({sl_change})\n"
                f"  TP: ${old_tp:,.2f} → ${new_tp:,.2f} ({tp_change})\n"
                f"  INV: {old_inv[:30]}... → {new_inv[:30]}...\n\n"
                f"💡 *Sebep:* {reasoning}\n\n"
                f"⏰ *Zaman:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            )
            
            telegram_client.send_message(message)
            logger.info("Exit plan update notification sent via Telegram")

        except Exception as e:
            logger.error("Failed to send exit plan update notification: %s", e)

    async def _update_stop_loss_in_db(
        self,
        position_id: str,
        new_stop: float,
        reason: str = "trailing_stop"
    ) -> bool:
        """
        Database'deki exit_plan.stop_loss değerini güncelle.

        Args:
            position_id: Pozisyon ID
            new_stop: Yeni stop loss fiyatı
            reason: Güncelleme nedeni (trailing_stop, breakeven, vb.)

        Returns:
            bool: Başarılı ise True
        """
        try:
            import json
            from app.executor.ledger import Trade

            with Session(engine) as session:
                trade = session.query(Trade).filter_by(
                    position_id=position_id,
                    close_price=None  # Sadece açık pozisyonları güncelle
                ).first()

                if not trade:
                    logger.warning(
                        "Trade not found for SL update: %s", position_id
                    )
                    return False

                # Mevcut exit_plan'i al ve güncelle
                current_exit_plan = trade.exit_plan or {}
                old_stop = current_exit_plan.get("stop_loss", 0)

                # Yeni stop loss'u ayarla
                current_exit_plan["stop_loss"] = round(new_stop, 2)

                # History'ye ekle
                if trade.exit_plan_history is None:
                    trade.exit_plan_history = {"updates": []}

                trade.exit_plan_history["updates"].append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "type": reason,
                    "old_stop": old_stop,
                    "new_stop": new_stop,
                })

                # Trade'i güncelle
                trade.exit_plan = current_exit_plan

                session.commit()

                logger.info(
                    "✅ SL updated in DB | position=%s | %.2f → %.2f | reason=%s",
                    position_id, old_stop, new_stop, reason
                )
                return True

        except Exception as e:
            logger.error("Failed to update SL in database: %s", e, exc_info=True)
            return False

    def _cleanup_trailing_managers(self, symbol: str) -> None:
        """
        Pozisyon kapandığında trailing stop ve breakeven manager'larından temizle.

        Args:
            symbol: Temizlenecek sembol
        """
        try:
            # Trailing stop cleanup
            if symbol in self._trailing_stop_manager.active_trails:
                del self._trailing_stop_manager.active_trails[symbol]
                logger.debug("Trailing stop removed for %s", symbol)

            # Breakeven cleanup
            if symbol in self._breakeven_manager.active_positions:
                del self._breakeven_manager.active_positions[symbol]
                logger.debug("Breakeven tracker removed for %s", symbol)

            # Partial TP cleanup
            if symbol in self._partial_tp_manager.active_plans:
                del self._partial_tp_manager.active_plans[symbol]
                logger.debug("Partial TP plan removed for %s", symbol)

        except Exception as e:
            logger.warning("Cleanup failed for %s: %s", symbol, e)

    async def _check_partial_tp_levels(
        self,
        symbol: str,
        position_info: dict,
        current_price: float,
    ) -> None:
        """
        Partial Take Profit seviyelerini kontrol et ve tetiklenen TP'leri execute et.

        Args:
            symbol: Trading pair
            position_info: Pozisyon bilgileri
            current_price: Güncel fiyat
        """
        try:
            entry_price = position_info.get("entry_price", 0)
            stop_loss = position_info.get("exit_plan", {}).get("stop_loss", 0)
            is_long = position_info.get("is_long", True)
            position_side = "LONG" if is_long else "SHORT"
            quantity = position_info.get("quantity", 0)
            position_id = position_info.get("position_id")

            if entry_price <= 0 or stop_loss <= 0 or quantity <= 0:
                logger.debug("Missing data for partial TP check: entry=%.2f sl=%.2f qty=%.6f",
                            entry_price, stop_loss, quantity)
                return

            # Lazy registration: Eğer bu sembol için TP planı yoksa oluştur
            if symbol not in self._partial_tp_manager.active_plans:
                # Redis'ten yükle veya yeni oluştur
                plan = await self._load_or_create_tp_plan(
                    symbol=symbol,
                    position_id=position_id,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    position_side=position_side,
                    quantity=quantity,
                )
                if not plan:
                    logger.debug("Could not create TP plan for %s", symbol)
                    return

            # TP seviyelerini kontrol et
            # Candle high/low için current_price kullan (simplified)
            tp_result = self._partial_tp_manager.check_and_execute(
                symbol=symbol,
                current_price=current_price,
                current_high=current_price,
                current_low=current_price,
            )

            if tp_result and tp_result.get("actions"):
                for action in tp_result["actions"]:
                    if action.get("action") == "PARTIAL_CLOSE":
                        tp_level = action.get("level", 0)
                        close_qty = action.get("quantity", 0)
                        close_price = action.get("price", current_price)
                        remaining_qty = action.get("remaining_qty", 0)

                        # SL adjustment bilgisi (TP1 sonrası breakeven)
                        sl_adjustment = action.get("sl_adjustment")
                        is_breakeven = sl_adjustment is not None

                        # Trailing activation (TP2 sonrası)
                        trailing_activated = action.get("trailing_activated", False)

                        logger.info(
                            "🎯 TP%d TRIGGERED | %s | qty=%.6f @ $%.2f | remaining=%.6f",
                            tp_level, symbol, close_qty, close_price, remaining_qty
                        )

                        # Executor ile partial close yap
                        if self._executor:
                            result = await asyncio.to_thread(
                                self._executor.execute_partial_close,
                                position_side=position_side,
                                close_quantity=close_qty,
                                close_price=close_price,
                                tp_level=tp_level,
                                reason=f"TP{tp_level} triggered at ${close_price:.2f}",
                                remaining_quantity=remaining_qty,
                                is_breakeven=is_breakeven,
                                trailing_activated=trailing_activated,
                            )
                            logger.info("Partial close result: %s", result.status)

                            # TP1 sonrası SL'yi breakeven'a taşı (DB'de güncelle)
                            if is_breakeven and sl_adjustment:
                                new_sl = sl_adjustment.get("new_stop_loss", 0)
                                if new_sl > 0:
                                    await self._update_stop_loss_in_db(
                                        position_id=position_id,
                                        new_stop=new_sl,
                                        reason="breakeven_after_tp1"
                                    )

                    elif action.get("action") == "TRAILING_STOP_HIT":
                        # Trailing stop tetiklendi - kalan pozisyonu kapat
                        close_qty = action.get("quantity", 0)
                        close_price = action.get("price", current_price)

                        logger.warning(
                            "📉 TRAILING STOP HIT (from partial TP) | %s | qty=%.6f @ $%.2f",
                            symbol, close_qty, close_price
                        )

                        if self._executor:
                            result = await asyncio.to_thread(
                                self._executor.execute_partial_close,
                                position_side=position_side,
                                close_quantity=close_qty,
                                close_price=close_price,
                                tp_level=99,  # Special level for trailing stop
                                reason="Trailing stop hit after TP2",
                                remaining_quantity=0,
                                is_breakeven=True,
                                trailing_activated=True,
                            )
                            logger.info("Trailing close result: %s", result.status)

                # Plan durumunu logla
                plan_status = tp_result.get("plan_status", {})
                logger.info(
                    "📊 TP Plan Status | %s | triggered=%d pending=%d remaining=%.1f%% BE=%s TRAIL=%s",
                    symbol,
                    plan_status.get("triggered_levels", 0),
                    plan_status.get("pending_levels", 0),
                    plan_status.get("remaining_pct", 100),
                    plan_status.get("is_breakeven", False),
                    plan_status.get("trailing_active", False),
                )

        except Exception as e:
            logger.error("Partial TP check error for %s: %s", symbol, e, exc_info=True)

    async def _load_or_create_tp_plan(
        self,
        symbol: str,
        position_id: str,
        entry_price: float,
        stop_loss: float,
        position_side: str,
        quantity: float,
    ):
        """
        Redis'ten TP planı yükle veya yeni oluştur.

        Returns:
            PartialTPPlan or None
        """
        try:
            import json
            from app.utils.redis import get_redis_client

            redis = get_redis_client()
            redis_key = f"partial_tp:{symbol}:{position_id}"

            # Redis'ten yüklemeyi dene
            try:
                cached_plan = redis.get(redis_key)
                if cached_plan:
                    plan_data = json.loads(cached_plan)
                    # Plan'ı reconstruct et
                    from app.risk_manager.partial_tp_manager import PartialTPPlan, TPLevel, TPStatus, TPStrategy

                    levels = []
                    for level_data in plan_data.get("levels", []):
                        level = TPLevel(
                            level_id=level_data["level_id"],
                            price=level_data["price"],
                            percentage=level_data["percentage"],
                            status=TPStatus(level_data.get("status", "pending")),
                        )
                        levels.append(level)

                    plan = PartialTPPlan(
                        entry_price=plan_data["entry_price"],
                        position_side=plan_data["position_side"],
                        initial_quantity=plan_data["initial_quantity"],
                        stop_loss=plan_data["stop_loss"],
                        levels=levels,
                        remaining_quantity=plan_data.get("remaining_quantity", plan_data["initial_quantity"]),
                        is_breakeven=plan_data.get("is_breakeven", False),
                        trailing_active=plan_data.get("trailing_active", False),
                        trailing_stop=plan_data.get("trailing_stop"),
                        strategy=TPStrategy(plan_data.get("strategy", "balanced")),
                    )

                    self._partial_tp_manager.active_plans[symbol] = plan
                    logger.info("📥 Loaded TP plan from Redis for %s", symbol)
                    return plan
            except Exception as redis_exc:
                logger.debug("Could not load TP plan from Redis: %s", redis_exc)

            # Redis'te yoksa yeni plan oluştur (dinamik strateji ile)
            # Market context'i al (basitleştirilmiş)
            trend_strength = "MODERATE"
            volatility_regime = "medium"
            mtf_confluence = 60.0

            plan = self._partial_tp_manager.create_tp_plan_advanced(
                symbol=symbol,
                entry_price=entry_price,
                stop_loss=stop_loss,
                position_side=position_side,
                quantity=quantity,
                trend_strength=trend_strength,
                volatility_regime=volatility_regime,
                mtf_confluence=mtf_confluence,
            )

            if plan:
                # Redis'e kaydet (24 saat TTL)
                await self._save_tp_plan_to_redis(symbol, position_id, plan)
                logger.info(
                    "📤 Created new TP plan for %s | Strategy=%s | Levels: %s",
                    symbol,
                    plan.strategy.value,
                    ", ".join([f"TP{l.level_id}=${l.price:.2f}({l.percentage}%)" for l in plan.levels])
                )

            return plan

        except Exception as e:
            logger.error("Failed to load/create TP plan: %s", e, exc_info=True)
            return None

    async def _save_tp_plan_to_redis(self, symbol: str, position_id: str, plan) -> bool:
        """TP planını Redis'e kaydet"""
        try:
            import json
            from app.utils.redis import get_redis_client

            redis = get_redis_client()
            redis_key = f"partial_tp:{symbol}:{position_id}"

            plan_data = {
                "entry_price": plan.entry_price,
                "position_side": plan.position_side,
                "initial_quantity": plan.initial_quantity,
                "stop_loss": plan.stop_loss,
                "remaining_quantity": plan.remaining_quantity,
                "is_breakeven": plan.is_breakeven,
                "trailing_active": plan.trailing_active,
                "trailing_stop": plan.trailing_stop,
                "strategy": plan.strategy.value,
                "levels": [
                    {
                        "level_id": l.level_id,
                        "price": l.price,
                        "percentage": l.percentage,
                        "status": l.status.value,
                    }
                    for l in plan.levels
                ],
            }

            # 24 saat TTL
            redis.setex(redis_key, 86400, json.dumps(plan_data))
            logger.debug("TP plan saved to Redis: %s", redis_key)
            return True

        except Exception as e:
            error_str = str(e).lower()
            if "read only" in error_str:
                logger.warning("Redis is read-only, TP plan not saved")
            else:
                logger.error("Failed to save TP plan to Redis: %s", e)
            return False
