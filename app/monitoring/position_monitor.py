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
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.executor.ledger import engine, get_open_position_with_exit_plan
from app.monitoring.exit_checker import check_all_exit_conditions, check_invalidation_condition
from app.utils.logging import get_logger
from app.utils.price_cache import price_cache
from app.utils.telegram import telegram_client


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
    ):
        self._symbol = symbol
        self._interval = interval_seconds
        self._executor = executor
        self._running = False
        self._enable_telegram = enable_telegram
        self._glm_client = glm_client
        self._dynamic_exit_updater = None
        
        logger.info(
            "Position Monitor initialized | symbol=%s interval=%ds dynamic_updates=%s",
            self._symbol,
            self._interval,
            glm_client is not None
        )
    
    async def start(self) -> None:
        """Ana monitor loop'u başlat"""
        self._running = True
        logger.info("Position Monitor started (3-minute interval checks)")
        
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
    
    async def _monitor_invalidation_conditions(self) -> None:
        """Invalidation condition'ları gerçek zamanlı izle"""
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
                    is_closed = kline.get("x", False)  # Kapanış mı?
                    
                    if close_price > 0 and is_closed:
                        # Sadece mum kapandığında invalidation condition'ları kontrol et
                        asyncio.create_task(self._check_invalidation_conditions_realtime(close_price))
                except Exception as e:
                    logger.error("Error in invalidation handler: %s", e)
            
            ws_client = BinanceWebSocketClient(self._symbol, "1m")
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
                return
            
            # 2. Current price al
            current_price = await self._get_current_price()
            
            if current_price is None:
                logger.warning("Could not fetch current price, skipping cycle")
                return
            
            # 2.5. [YENİ] Dinamik exit plan güncellemesi değerlendir
            try:
                await self._evaluate_dynamic_exit_plan_update(position_info, current_price)
            except Exception as e:
                logger.error("Dynamic exit plan update failed: %s", e, exc_info=True)
            
            # 3. ÖNCELİKLİ: Invalidation condition kontrolü
            entry_price = position_info["entry_price"]
            is_long = position_info["is_long"]
            exit_plan = position_info["exit_plan"]
            position_id = position_info["position_id"]
            quantity = position_info["quantity"]
            
            invalidation_condition = exit_plan.get("invalidation_condition", "")
            if invalidation_condition and invalidation_condition != "N/A":
                triggered, reason = check_invalidation_condition(
                    current_price=current_price,
                    invalidation_condition=invalidation_condition,
                    is_long=is_long
                )
                
                if triggered:
                    logger.warning(
                        "🚨 INVALIDATION CONDITION TRIGGERED (PRIORITY) | %s",
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
                        entry_price=entry_price
                    )
                    return  # Diğer kontrolleri atla
            
            # 4. Diğer exit koşullarını kontrol et
            should_close, trigger_type, reason = check_all_exit_conditions(
                entry_price=entry_price,
                current_price=current_price,
                exit_plan=exit_plan,
                is_long=is_long
            )
            
            if should_close:
                logger.info(
                    "🔔 EXIT CONDITION TRIGGERED | type=%s | %s",
                    trigger_type,
                    reason
                )
                
                # Pozisyonu kapat
                await self._close_position(
                    position_id=position_id,
                    trigger_type=trigger_type,
                    reason=reason,
                    current_price=current_price,
                    is_long=is_long,
                    quantity=quantity,
                    entry_price=entry_price
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
    
    async def _get_current_price(self) -> Optional[float]:
        """
        Current price al
        1. Price cache'den dene (WebSocket)
        2. Başarısızsa REST API'den al
        """
        # Try price cache first (WebSocket)
        current_price = price_cache.get(self._symbol)
        
        if current_price is not None:
            return current_price
        
        # Fallback: REST API
        try:
            import httpx
            response = await asyncio.to_thread(
                httpx.get,
                "https://fapi.binance.com/fapi/v1/ticker/price",
                params={"symbol": self._symbol},
                timeout=5.0
            )
            price_data = response.json()
            current_price = float(price_data["price"])
            logger.debug("Price fetched from REST API: %.2f", current_price)
            return current_price
        except Exception as exc:
            logger.error("Failed to fetch price from REST API: %s", exc)
            return None
    
    async def _check_invalidation_conditions_realtime(self, current_price: float) -> None:
        """Invalidation condition'ları gerçek zamanlı kontrol et"""
        try:
            position_info = await self._get_open_position()
            
            if position_info is None:
                return
            
            entry_price = position_info["entry_price"]
            is_long = position_info["is_long"]
            exit_plan = position_info["exit_plan"]
            position_id = position_info["position_id"]
            quantity = position_info["quantity"]
            
            # Sadece invalidation condition'ı kontrol et
            invalidation_condition = exit_plan.get("invalidation_condition", "")
            if not invalidation_condition or invalidation_condition == "N/A":
                return
            
            triggered, reason = check_invalidation_condition(
                current_price=current_price,
                invalidation_condition=invalidation_condition,
                is_long=is_long
            )
            
            if triggered:
                logger.warning(
                    "🚨 INVALIDATION CONDITION TRIGGERED (REALTIME) | %s",
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
                    entry_price=entry_price
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
        entry_price: float
    ) -> None:
        """Pozisyonu kapat ve bildirim gönder"""
        try:
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
                    trigger_type=trigger_type
                )
                
                logger.info("Position closed | result=%s", result.status)
                
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
                    quantity=quantity
                )
                
                # Telegram bildirimi
                if self._enable_telegram:
                    await self._send_telegram_notification(
                        position_id=position_id,
                        trigger_type=trigger_type,
                        reason=reason,
                        current_price=current_price,
                        is_long=is_long,
                        quantity=quantity,
                        entry_price=entry_price
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
        quantity: float
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
            }
            
            # Redis key: position_closed:{symbol}
            redis_key = f"position_closed:{self._symbol}"
            
            # 10 dakika TTL (GLM bir sonraki döngüde okur)
            # 3 dakikalık cycle + buffer = 10 dakika yeterli
            ttl_seconds = 600
            
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
        
        except Exception as exc:
            logger.error("Failed to send GLM notification via Redis: %s", exc)
            # Don't raise - notification failure shouldn't block position closing
    
    async def _send_telegram_notification(
        self,
        position_id: str,
        trigger_type: str,
        reason: str,
        current_price: float,
        is_long: bool,
        quantity: float,
        entry_price: float
    ) -> None:
        """Telegram bildirimi gönder"""
        try:
            pnl = (current_price - entry_price) * quantity if is_long else (entry_price - current_price) * quantity
            pnl_pct = ((current_price - entry_price) / entry_price * 100) if is_long else ((entry_price - current_price) / entry_price * 100)
            
            emoji_map = {
                "profit_target": "🎯",
                "stop_loss": "🛑",
                "invalidation": "⚠️"
            }
            emoji = emoji_map.get(trigger_type, "🔔")
            
            message = (
                f"{emoji} *Position Monitor Alert*\n\n"
                f"*Position ID:* `{position_id}`\n"
                f"*Type:* {trigger_type.upper().replace('_', ' ')}\n"
                f"*Side:* {'LONG' if is_long else 'SHORT'}\n"
                f"*Quantity:* {quantity:.6f} BTC\n\n"
                f"*Entry:* ${entry_price:.2f}\n"
                f"*Exit:* ${current_price:.2f}\n"
                f"*PnL:* ${pnl:.2f} ({pnl_pct:+.2f}%)\n\n"
                f"*Reason:* {reason}\n"
                f"*Time:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
                f"✅ *GLM Notification:* Sent (will be visible in next cycle)"
            )
            
            await telegram_client.send_message(message)
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
                        await self._send_exit_plan_update_notification(update_result)
                
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
    
    async def _send_exit_plan_update_notification(self, update_result: dict) -> None:
        """Exit plan güncellemesi için Telegram bildirimi"""
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
            
            await telegram_client.send_message(message)
            logger.info("Exit plan update notification sent via Telegram")
            
        except Exception as e:
            logger.error("Failed to send exit plan update notification: %s", e)
