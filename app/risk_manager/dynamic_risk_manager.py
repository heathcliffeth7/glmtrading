"""
Dinamik Risk Yönetimi Sistemi
Piyasa volatilitesine göre dinamik exit seviyeleri hesaplar
"""

import math
from typing import Dict, Optional, Tuple

from app.utils.logging import get_logger

logger = get_logger(__name__)


class DynamicRiskManager:
    """Piyasa koşullarına göre dinamik risk yönetimi sağlar"""

    def __init__(self):
        # Volatilite bazlı risk yüzdesi ayarları
        self.volatility_settings = {
            "low": {
                "risk_pct": 0.015,
                "reward_pct": 0.03,
            },  # Düşük volatilite: %1.5 risk, %3.0 ödül
            "medium": {
                "risk_pct": 0.02,
                "reward_pct": 0.04,
            },  # Normal volatilite: %2.0 risk, %4.0 ödül
            "high": {
                "risk_pct": 0.025,
                "reward_pct": 0.05,
            },  # Yüksek volatilite: %2.5 risk, %5.0 ödül
            "extreme": {
                "risk_pct": 0.03,
                "reward_pct": 0.06,
            },  # Ekstrem volatilite: %3.0 risk, %6.0 ödül
        }

        # ATR (Average True Range) bazlı ayarlar
        self.atr_multiplier = {
            "risk": 0.5,  # Stop loss için ATR çarpanı
            "reward": 1.5,  # Profit target için ATR çarpanı
            "invalidation": 0.6,  # Invalidation için ATR çarpanı
        }

    def calculate_volatility(self, price_data: list) -> float:
        """
        Fiyat verilerinden volatilite hesaplar (standart sapma)

        Args:
            price_data: Son N periyotun kapanış fiyatları

        Returns:
            Volatilite değeri (0-1 aralığında normalize edilmiş)
        """
        if len(price_data) < 2:
            return 0.5  # Varsayılan değer

        # Günlük getirileri hesapla
        returns = []
        for i in range(1, len(price_data)):
            if price_data[i - 1] > 0:
                daily_return = (price_data[i] - price_data[i - 1]) / price_data[i - 1]
                returns.append(daily_return)

        if not returns:
            return 0.5

        # Standart sapmayı hesapla
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)

        # Volatiliteyi normalize et (0-1 aralığı)
        # Yıllık volatilite varsayımı
        annualized_vol = std_dev * math.sqrt(252)  # 252 işlem günü

        # Normalize et (0.1 = çok düşük, 0.5 = normal, 1.0 = çok yüksek)
        normalized_vol = min(1.0, max(0.1, annualized_vol / 0.2))

        return normalized_vol

    def calculate_atr(
        self, price_data: list, high_data: list = None, low_data: list = None, period: int = 14
    ) -> float:
        """
        Average True Range (ATR) hesaplar

        Args:
            price_data: Kapanış fiyatları
            high_data: En yüksek fiyatlar (opsiyonel)
            low_data: En düşük fiyatlar (opsiyonel)
            period: ATR periyodu

        Returns:
            ATR değeri
        """
        if len(price_data) < period:
            return 0.0

        # Eğer high/low verisi yoksa, price_data'dan türet
        if not high_data:
            high_data = price_data
        if not low_data:
            low_data = price_data

        true_ranges = []
        for i in range(1, len(price_data)):
            high = high_data[i]
            low = low_data[i]
            prev_close = price_data[i - 1]

            # True Range = max(high-low, abs(high-prev_close), abs(low-prev_close))
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

        # ATR = son periyotun TR ortalaması
        if len(true_ranges) < period:
            return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

        atr = sum(true_ranges[-period:]) / period
        return atr

    def calculate_dynamic_exit_levels(
        self,
        entry_price: float,
        position_side: str,
        volatility: float = None,
        atr: float = None,
        price_data: list = None,
    ) -> Dict[str, float]:
        """
        Piyasa volatilitesine göre dinamik exit seviyeleri hesaplar

        Args:
            entry_price: Giriş fiyatı
            position_side: "LONG" veya "SHORT"
            volatility: Volatilite değeri (0-1)
            atr: ATR değeri
            price_data: Fiyat verileri (volatilite hesabı için)

        Returns:
            {
                "stop_loss": float,
                "profit_target": float,
                "invalidation_price": float,
                "risk_reward_ratio": float,
                "volatility": float
            }
        """
        # Volatilite hesapla (eğer sağlanmadıysa)
        if volatility is None and price_data:
            volatility = self.calculate_volatility(price_data)

        # Varsayılan volatilite
        if volatility is None:
            volatility = 0.5

        # Volatilite kategorisini belirle
        if volatility < 0.25:
            vol_category = "low"
        elif volatility < 0.5:
            vol_category = "medium"
        elif volatility < 0.75:
            vol_category = "high"
        else:
            vol_category = "extreme"

        settings = self.volatility_settings[vol_category]
        risk_pct = settings["risk_pct"]
        reward_pct = settings["reward_pct"]

        # ATR kullanıyorsak, risk/reward yüzdesini güncelle
        if atr and atr > 0:
            # ATR'ı fiyat yüzdesine çevir
            atr_pct = atr / entry_price

            # Daha yüksek olanı kullan (volatilite veya ATR bazlı)
            risk_pct = max(risk_pct, atr_pct * self.atr_multiplier["risk"])
            reward_pct = max(reward_pct, atr_pct * self.atr_multiplier["reward"])

        # Exit seviyelerini hesapla
        if position_side.upper() == "LONG":
            stop_loss = entry_price * (1 - risk_pct)
            profit_target = entry_price * (1 + reward_pct)
            invalidation_price = entry_price * (1 - risk_pct * 1.2)  # Stop loss'tan %20 daha düşük
        else:  # SHORT
            stop_loss = entry_price * (1 + risk_pct)
            profit_target = entry_price * (1 - reward_pct)
            invalidation_price = entry_price * (1 + risk_pct * 1.2)  # Stop loss'tan %20 daha yüksek

        return {
            "stop_loss": stop_loss,
            "profit_target": profit_target,
            "invalidation_price": invalidation_price,
            "risk_reward_ratio": reward_pct / risk_pct,
            "volatility": volatility,
            "atr": atr or 0.0,
        }

    def generate_invalidation_condition(
        self, position_side: str, invalidation_price: float, time_frame: str = "3m"
    ) -> str:
        """
        Dinamik invalidation condition metni oluşturur

        Args:
            position_side: "LONG" veya "SHORT"
            invalidation_price: Invalidation fiyat seviyesi
            time_frame: Zaman dilimi ("3m", "5m", "15m", vb.)

        Returns:
            Invalidation condition metni
        """
        if position_side.upper() == "LONG":
            return f"If price closes below {invalidation_price:.2f} on {time_frame} candle"
        else:  # SHORT
            return f"If price closes above {invalidation_price:.2f} on {time_frame} candle"

    def adjust_exit_plan_for_market_conditions(
        self,
        exit_plan: dict,
        current_price: float,
        price_data: list = None,
        high_data: list = None,
        low_data: list = None,
    ) -> dict:
        """
        Mevcut exit planını piyasa koşullarına göre ayarlar

        Args:
            exit_plan: Mevcut exit planı
            current_price: Güncel fiyat
            price_data: Fiyat verileri
            high_data: En yüksek fiyatlar
            low_data: En düşük fiyatlar

        Returns:
            Ayarlanmış exit planı
        """
        if not exit_plan:
            return exit_plan

        # Volatilite ve ATR hesapla
        volatility = self.calculate_volatility(price_data) if price_data else None
        atr = self.calculate_atr(price_data, high_data, low_data) if price_data else None

        # Pozisyon yönünü belirle (fiyata göre)
        position_side = "LONG" if current_price > exit_plan.get("stop_loss", 0) else "SHORT"

        # Dinamik seviyeleri hesapla
        dynamic_levels = self.calculate_dynamic_exit_levels(
            exit_plan.get("entry_price", current_price), position_side, volatility, atr, price_data
        )

        # Mevcut planı koru ama seviyeleri güncelle
        adjusted_plan = exit_plan.copy()
        adjusted_plan["stop_loss"] = dynamic_levels["stop_loss"]
        adjusted_plan["profit_target"] = dynamic_levels["profit_target"]

        # Invalidation condition'u güncelle
        adjusted_plan["invalidation_condition"] = self.generate_invalidation_condition(
            position_side, dynamic_levels["invalidation_price"]
        )

        # Ek bilgiler ekle
        adjusted_plan["risk_reward_ratio"] = dynamic_levels["risk_reward_ratio"]
        adjusted_plan["volatility"] = dynamic_levels["volatility"]
        adjusted_plan["atr"] = dynamic_levels["atr"]

        return adjusted_plan
