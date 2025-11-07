"""
Gelişmiş Exit Plan Monitoring Sistemi
Anlık fiyat hareketlerini izler ve exit koşullarını kontrol eder
"""

import asyncio
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.executor.ledger import (
    Trade,
    close_open_trades,
    engine,
    get_open_position_details,
)
from app.risk_manager.advanced_parser import AdvancedInvalidationParser
from app.utils.logging import get_logger
from app.utils.price_cache import price_cache
from app.utils.redis import get_redis_client

logger = get_logger(__name__)


class AdvancedExitMonitor:
    """Gelişmiş exit plan monitoring sistemi"""

    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol
        self.active_positions = {}  # position_id -> position_data
        self.price_history = []  # Son fiyatlar
        self.monitoring_active = False
        self.parser = AdvancedInvalidationParser()
        self.redis = get_redis_client()

        # Monitoring ayarları
        self.check_interval_seconds = 30  # 30 saniyede bir kontrol
        self.max_price_history = 100  # Son 100 fiyatı sakla
        self.notification_cooldown_seconds = 60  # Aynı bildirim için bekleme süresi

        # WebSocket bağlantısı
        self.ws_client = None
        self.ws_thread = None

    def start_monitoring(self):
        """Monitoring sistemini başlatır"""
        if self.monitoring_active:
            logger.warning("Exit monitoring already active")
            return

        self.monitoring_active = True
        logger.info("Starting advanced exit monitoring for %s", self.symbol)

        # Mevcut pozisyonları yükle
        self._load_active_positions()

        # WebSocket monitoring thread'ini başlat
        self.ws_thread = threading.Thread(
            target=self._websocket_monitor, name=f"advanced-exit-monitor-{self.symbol}", daemon=True
        )
        self.ws_thread.start()

        # Periyodik kontrol thread'ini başlat
        self._start_periodic_checks()

    def stop_monitoring(self):
        """Monitoring sistemini durdurur"""
        self.monitoring_active = False
        logger.info("Stopping advanced exit monitoring for %s", self.symbol)

    def add_position(
        self,
        position_id: str,
        exit_plan: dict,
        entry_price: float,
        position_side: str,
        amount: float,
        leverage: float,
    ):
        """Yeni pozisyonu monitoring sistemine ekler"""
        self.active_positions[position_id] = {
            "exit_plan": exit_plan,
            "entry_price": entry_price,
            "position_side": position_side,
            "amount": amount,
            "leverage": leverage,
            "created_at": datetime.utcnow(),
            "notifications_sent": set(),  # Tekrar bildirimleri önlemek için
            "last_check": None,  # Son kontrol zamanı
            "price_at_entry": entry_price,  # Giriş anındaki fiyat
            "highest_price": entry_price,  # En yüksek fiyat (trailing için)
            "lowest_price": entry_price,  # En düşük fiyat (trailing için)
            "trailing_stop_enabled": False,  # Trailing stop aktif mi?
            "trailing_profit_enabled": False,  # Trailing profit aktif mi?
        }

        logger.info(
            "Added position to monitoring: %s %s @ %.2f", position_side, position_id, entry_price
        )

        # Redis'e pozisyon bilgisini kaydet
        self._save_position_to_redis(position_id)

    def remove_position(self, position_id: str):
        """Pozisyonu monitoring sisteminden kaldırır"""
        if position_id in self.active_positions:
            position_data = self.active_positions[position_id]
            del self.active_positions[position_id]

            logger.info("Removed position from monitoring: %s", position_id)

            # Redis'ten pozisyon bilgisini sil
            self._remove_position_from_redis(position_id)

    def _load_active_positions(self):
        """Veritabanından aktif pozisyonları yükler"""
        try:
            with Session(engine) as session:
                open_positions = get_open_position_details(session, self.symbol)

                if open_positions and open_positions.get("total_amount", 0) > 0.0001:
                    # En son trade'i bul
                    latest_trade = (
                        session.query(Trade)
                        .filter_by(symbol=self.symbol)
                        .filter(Trade.close_price.is_(None))
                        .order_by(Trade.timestamp.desc())
                        .first()
                    )

                    if latest_trade and latest_trade.exit_plan:
                        self.add_position(
                            position_id=latest_trade.position_id,
                            exit_plan=latest_trade.exit_plan,
                            entry_price=latest_trade.price,
                            position_side=latest_trade.position_side,
                            amount=latest_trade.amount,
                            leverage=latest_trade.leverage,
                        )
        except Exception as exc:
            logger.error("Failed to load active positions: %s", exc)

    def _websocket_monitor(self):
        """WebSocket üzerinden fiyatları izler"""
        try:
            import asyncio

            from app.data_feeds.binance_ws import BinanceWebSocketClient

            async def monitor():
                try:

                    def price_handler(message):
                        try:
                            # WebSocketMessage formatını kontrol et
                            if hasattr(message, "payload"):
                                payload = message.payload
                            elif isinstance(message, dict):
                                payload = message.get("payload", {})
                            else:
                                logger.warning("Unknown message format: %s", type(message))
                                return

                            kline = payload.get("k", {})
                            close_price = float(kline.get("c", 0.0))

                            if close_price > 0:
                                self._process_price_update(close_price)
                        except Exception as e:
                            logger.error("Error in price handler: %s", e)

                    ws_client = BinanceWebSocketClient(self.symbol, "1m")
                    await ws_client.listen(price_handler)

                except Exception as e:
                    logger.error("WebSocket monitoring error: %s", e)

            # Async loop çalıştır
            asyncio.run(monitor())

        except Exception as e:
            logger.error("Failed to start WebSocket monitoring: %s", e)

    def _start_periodic_checks(self):
        """Periyodik kontrolleri başlatır"""

        def periodic_check():
            while self.monitoring_active:
                try:
                    # Mevcut fiyatı al
                    current_price = self._get_current_price()
                    if current_price > 0:
                        self._process_price_update(current_price)

                    # Tüm pozisyonların exit koşullarını kontrol et
                    self._check_all_exit_conditions(current_price)

                    # Fiyat geçmişini temizle (eski kayıtları sil)
                    self._cleanup_old_data()

                except Exception as e:
                    logger.error("Error in periodic check: %s", e)

                # Belirtilen aralıkta bekle
                time.sleep(self.check_interval_seconds)

        # Periyodik kontrol thread'ini başlat
        periodic_thread = threading.Thread(
            target=periodic_check, name=f"periodic-exit-check-{self.symbol}", daemon=True
        )
        periodic_thread.start()

    def _process_price_update(self, current_price: float):
        """Fiyat güncellemesini işler"""
        # Fiyat geçmişine ekle
        self.price_history.append({"price": current_price, "timestamp": datetime.utcnow()})

        # Fiyat geçmişini sınırla
        if len(self.price_history) > self.max_price_history:
            self.price_history = self.price_history[-self.max_price_history :]

        # Her pozisyon için trailing stop/profit güncellemelerini yap
        for position_id, position_data in self.active_positions.items():
            self._update_trailing_levels(position_id, position_data, current_price)

    def _update_trailing_levels(self, position_id: str, position_data: dict, current_price: float):
        """Trailing stop/profit seviyelerini günceller"""
        position_side = position_data["position_side"]
        entry_price = position_data["entry_price"]

        # Trailing stop seviyesini güncelle
        if position_side == "LONG":
            # LONG için: en yüksek fiyatı güncelle
            if current_price > position_data["highest_price"]:
                position_data["highest_price"] = current_price

                # Trailing stop'ı etkinleştir (eğer %1'den fazla arttıysa)
                if current_price > entry_price * 1.01:  # %1'den fazla
                    position_data["trailing_stop_enabled"] = True

                    # Yeni trailing stop seviyesini hesapla
                    # En yüksek fiyattan %2 aşağı
                    new_stop_loss = current_price * 0.98
                    old_stop_loss = position_data["exit_plan"].get("stop_loss", 0)

                    # Sadece yukarı yönde güncelle
                    if new_stop_loss > old_stop_loss:
                        position_data["exit_plan"]["stop_loss"] = new_stop_loss
                        logger.debug(
                            "Updated trailing stop loss for %s: %.2f -> %.2f",
                            position_id,
                            old_stop_loss,
                            new_stop_loss,
                        )

            # Trailing profit seviyesini güncelle
            if current_price > position_data["highest_price"]:
                # En yüksek fiyattan %1 yukarı trailing profit
                new_profit_target = current_price * 1.01
                old_profit_target = position_data["exit_plan"].get("profit_target", float("inf"))

                # Sadece aşağı yönde güncelle
                if new_profit_target < old_profit_target:
                    position_data["exit_plan"]["profit_target"] = new_profit_target
                    position_data["trailing_profit_enabled"] = True
                    logger.debug(
                        "Updated trailing profit target for %s: %.2f -> %.2f",
                        position_id,
                        old_profit_target,
                        new_profit_target,
                    )

        else:  # SHORT
            # SHORT için: en düşük fiyatı güncelle
            if current_price < position_data["lowest_price"]:
                position_data["lowest_price"] = current_price

                # Trailing stop'ı etkinleştir (eğer %1'den fazla düştüyse)
                if current_price < entry_price * 0.99:  # %1'den fazla
                    position_data["trailing_stop_enabled"] = True

                    # Yeni trailing stop seviyesini hesapla
                    # En düşük fiyattan %2 yukarı
                    new_stop_loss = current_price * 1.02
                    old_stop_loss = position_data["exit_plan"].get("stop_loss", 0)

                    # Sadece aşağı yönde güncelle
                    if new_stop_loss < old_stop_loss:
                        position_data["exit_plan"]["stop_loss"] = new_stop_loss
                        logger.debug(
                            "Updated trailing stop loss for %s: %.2f -> %.2f",
                            position_id,
                            old_stop_loss,
                            new_stop_loss,
                        )

            # Trailing profit seviyesini güncelle
            if current_price < position_data["lowest_price"]:
                # En düşük fiyattan %1 aşağı trailing profit
                new_profit_target = current_price * 0.99
                old_profit_target = position_data["exit_plan"].get("profit_target", 0)

                # Sadece yukarı yönde güncelle
                if new_profit_target > old_profit_target:
                    position_data["exit_plan"]["profit_target"] = new_profit_target
                    position_data["trailing_profit_enabled"] = True
                    logger.debug(
                        "Updated trailing profit target for %s: %.2f -> %.2f",
                        position_id,
                        old_profit_target,
                        new_profit_target,
                    )

    def _check_all_exit_conditions(self, current_price: float):
        """Tüm pozisyonların exit koşullarını kontrol eder"""
        for position_id, position_data in list(self.active_positions.items()):
            try:
                # Exit koşullarını kontrol et
                should_close, trigger_type, reason = self._check_exit_conditions(
                    position_id, position_data, current_price
                )

                if should_close:
                    logger.warning(
                        "Exit condition triggered for %s: %s - %s",
                        position_id,
                        trigger_type,
                        reason,
                    )

                    # Pozisyonu kapat
                    self._close_position(position_id, trigger_type, current_price, reason)

            except Exception as e:
                logger.error("Error checking exit conditions for position %s: %s", position_id, e)

    def _check_exit_conditions(
        self, position_id: str, position_data: dict, current_price: float
    ) -> Tuple[bool, str, str]:
        """Tek bir pozisyonun exit koşullarını kontrol eder"""
        exit_plan = position_data["exit_plan"]
        position_side = position_data["position_side"]
        entry_price = position_data["entry_price"]

        # 1. Profit Target kontrolü
        profit_target = exit_plan.get("profit_target")
        if profit_target:
            if (position_side == "LONG" and current_price >= profit_target) or (
                position_side == "SHORT" and current_price <= profit_target
            ):
                return True, "profit_target", f"Profit target reached: {profit_target:.2f}"

        # 2. Stop Loss kontrolü
        stop_loss = exit_plan.get("stop_loss")
        if stop_loss:
            if (position_side == "LONG" and current_price <= stop_loss) or (
                position_side == "SHORT" and current_price >= stop_loss
            ):
                return True, "stop_loss", f"Stop loss triggered: {stop_loss:.2f}"

        # 3. Invalidation Condition kontrolü
        invalidation_condition = exit_plan.get("invalidation_condition", "")
        if invalidation_condition and invalidation_condition != "N/A":
            # Gelişmiş parser kullan
            direction, price, time_frame, metadata = self.parser.parse(invalidation_condition)

            if direction and price:
                if self.parser.validate_condition(direction, price, current_price):
                    return (
                        True,
                        "invalidation",
                        f"Invalidation condition met: {invalidation_condition}",
                    )

        # Hiçbir koşul sağlanmadı
        return False, "", ""

    def _close_position(
        self, position_id: str, trigger_type: str, current_price: float, reason: str
    ):
        """Pozisyonu kapatır ve bildirim gönderir"""
        if position_id not in self.active_positions:
            logger.warning("Position %s not found in active positions", position_id)
            return

        position_data = self.active_positions[position_id]

        # Bildirim anahtarını oluştur (tekrar bildirimleri önlemek için)
        notification_key = f"{position_id}_{trigger_type}_{current_price:.2f}"
        current_time = datetime.utcnow()

        # Son bildirim zamanını kontrol et
        last_notification_time = position_data.get("last_notification_time")
        if (
            last_notification_time
            and (current_time - last_notification_time).total_seconds()
            < self.notification_cooldown_seconds
        ):
            logger.debug("Skipping notification due to cooldown: %s", notification_key)
            return

        # Bildirim gönder
        self._send_exit_notification(
            position_id, position_data, trigger_type, current_price, reason
        )

        # Son bildirim zamanını güncelle
        position_data["last_notification_time"] = current_time

        # Pozisyonu veritabanında kapat
        self._close_position_in_db(position_id, position_data, current_price, trigger_type)

        # Pozisyonu monitoring sisteminden kaldır
        self.remove_position(position_id)

    def _close_position_in_db(
        self, position_id: str, position_data: dict, current_price: float, trigger_type: str
    ):
        """Pozisyonu veritabanında kapatır"""
        try:
            with Session(engine) as session:
                # Pozisyonu kapat
                close_side = "SELL" if position_data["position_side"] == "LONG" else "BUY"

                updated_count, realized_delta, closing_fee_total = close_open_trades(
                    session=session,
                    symbol=self.symbol,
                    close_side=close_side,
                    close_amount=position_data["amount"],
                    close_price=current_price,
                    taker_fee_rate=0.0005,  # Binance USDT-M Futures Taker: 0.05%
                )

                session.commit()

                logger.info(
                    "Closed position %s in DB: %d trades updated, realized=%.2f, fee=%.2f",
                    position_id,
                    updated_count,
                    realized_delta,
                    closing_fee_total,
                )

        except Exception as exc:
            logger.error("Failed to close position in DB: %s", exc)

    def _send_exit_notification(
        self,
        position_id: str,
        position_data: dict,
        trigger_type: str,
        current_price: float,
        reason: str,
    ):
        """Exit bildirimi gönderir"""
        try:
            from app.utils.telegram import telegram_client

            # PnL hesapla
            entry_price = position_data["entry_price"]
            position_side = position_data["position_side"]
            amount = position_data["amount"]

            if position_side == "LONG":
                pnl_pct = (current_price - entry_price) / entry_price * 100
                pnl_amount = (current_price - entry_price) * amount
            else:  # SHORT
                pnl_pct = (entry_price - current_price) / entry_price * 100
                pnl_amount = (entry_price - current_price) * amount

            # Emoji seçimi
            emoji_map = {"profit_target": "🎯", "stop_loss": "🛑", "invalidation": "⚠️"}
            emoji = emoji_map.get(trigger_type, "🔔")
            position_emoji = "📉" if position_side == "SHORT" else "📈"

            # Trailing bilgileri
            trailing_info = ""
            if position_data.get("trailing_stop_enabled"):
                trailing_info += " (Trailing Stop)"
            if position_data.get("trailing_profit_enabled"):
                trailing_info += " (Trailing Profit)"

            message = f"""
{emoji} *{trigger_type.upper().replace('_', ' ')} TETİKLENDİ*

{position_emoji} **Pozisyon Bilgisi:**
• ID: {position_id}
• Tür: {position_side}
• Miktar: {amount:.6f} BTC
• Giriş Fiyatı: ${entry_price:,.2f}
• Mevcut Fiyat: ${current_price:,.2f}
• Tetik Fiyatı: ${current_price:,.2f}{trailing_info}

💰 **Kar/Zarar:**
• Yüzde: {pnl_pct:+.2f}%
• Tutar: ${pnl_amount:+,.2f}

⏰ *{datetime.utcnow().strftime('%H:%M:%S')}*

💬 **Sebep:** {reason}
"""

            # Telegram'a gönder
            telegram_client.send_message(message)
            logger.info("Exit notification sent for position %s", position_id)

        except Exception as exc:
            logger.error("Failed to send exit notification: %s", exc)

    def _get_current_price(self) -> float:
        """Mevcut fiyatı alır"""
        # Önce price cache'den dene
        cached_price = price_cache.get(self.symbol)
        if cached_price and cached_price > 0:
            return cached_price

        # Binance API'den al
        try:
            import httpx

            response = httpx.get(
                "https://fapi.binance.com/fapi/v1/ticker/price",
                params={"symbol": self.symbol},
                timeout=5.0,
            )
            response.raise_for_status()
            data = response.json()
            price = float(data["price"])
            if price > 0:
                return price
        except Exception as exc:
            logger.warning("Could not fetch price from Binance API: %s", exc)

        return 0.0

    def _cleanup_old_data(self):
        """Eski verileri temizler"""
        # 1 saatten eski fiyat geçmişini temizle
        cutoff_time = datetime.utcnow() - timedelta(hours=1)
        self.price_history = [
            entry for entry in self.price_history if entry["timestamp"] > cutoff_time
        ]

        # 1 saatten eski bildirim kayıtlarını temizle
        for position_id, position_data in self.active_positions.items():
            if "last_notification_time" in position_data:
                last_notification_time = position_data["last_notification_time"]
                if last_notification_time and last_notification_time < cutoff_time:
                    # Eski bildirimleri temizle
                    position_data["notifications_sent"] = {
                        key
                        for key in position_data["notifications_sent"]
                        if key.split("_")[1] > cutoff_time.timestamp()
                    }

    def _save_position_to_redis(self, position_id: str):
        """Pozisyon bilgisini Redis'e kaydeder"""
        try:
            import json

            if position_id in self.active_positions:
                position_data = self.active_positions[position_id]

                # Sadece gerekli alanları kaydet
                redis_data = {
                    "position_id": position_id,
                    "position_side": position_data["position_side"],
                    "entry_price": position_data["entry_price"],
                    "exit_plan": position_data["exit_plan"],
                    "created_at": position_data["created_at"].isoformat(),
                }

                self.redis.setex(
                    f"position:{self.symbol}:{position_id}",
                    86400,  # 24 saat
                    json.dumps(redis_data),
                )
        except Exception as exc:
            logger.error("Failed to save position to Redis: %s", exc)

    def _remove_position_from_redis(self, position_id: str):
        """Pozisyon bilgisini Redis'ten kaldırır"""
        try:
            self.redis.delete(f"position:{self.symbol}:{position_id}")
        except Exception as exc:
            logger.error("Failed to remove position from Redis: %s", exc)

    def get_position_status(self, position_id: str) -> Optional[dict]:
        """Pozisyon durumunu döndürür"""
        if position_id not in self.active_positions:
            return None

        position_data = self.active_positions[position_id]
        current_price = self._get_current_price()

        # PnL hesapla
        entry_price = position_data["entry_price"]
        position_side = position_data["position_side"]
        amount = position_data["amount"]

        if position_side == "LONG":
            pnl_pct = (current_price - entry_price) / entry_price * 100
            pnl_amount = (current_price - entry_price) * amount
        else:  # SHORT
            pnl_pct = (entry_price - current_price) / entry_price * 100
            pnl_amount = (entry_price - current_price) * amount

        return {
            "position_id": position_id,
            "position_side": position_side,
            "entry_price": entry_price,
            "current_price": current_price,
            "pnl_pct": pnl_pct,
            "pnl_amount": pnl_amount,
            "exit_plan": position_data["exit_plan"],
            "trailing_stop_enabled": position_data.get("trailing_stop_enabled", False),
            "trailing_profit_enabled": position_data.get("trailing_profit_enabled", False),
            "created_at": position_data["created_at"],
            "last_check": position_data.get("last_check"),
        }
