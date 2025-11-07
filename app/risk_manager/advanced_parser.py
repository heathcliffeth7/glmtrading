"""
Gelişmiş Invalidation Condition Parser
Karmaşık koşulları düzgün şekilde parse eder
"""

import re
from typing import Tuple, Optional, Dict, List
from app.utils.logging import get_logger

logger = get_logger(__name__)


class AdvancedInvalidationParser:
    """Gelişmiş invalidation condition parser"""
    
    def __init__(self):
        # Önceden derlenmiş regex desenleri
        self.patterns = [
            # Zaman bazlı desenler
            {
                "name": "time_based_candle",
                "pattern": r'if\s+price\s+closes?\s+(below|above)\s+([\d,.]+)\s+on\s+(\d+)-?(m|min|minute|hour|h|day|d)s?\s+candle',
                "processor": self._parse_time_based_candle
            },
            
            # Basit fiyat seviyesi desenleri
            {
                "name": "simple_price_level",
                "pattern": r'(if\s+)?price\s+(closes?\s+)?(below|above)\s+([\d,.]+)',
                "processor": self._parse_simple_price_level
            },
            
            # Türkçe desenler
            {
                "name": "turkish_time_based_candle",
                "pattern": r'(eğer\s+)?fiyat\s+(\d+)\s+(dakikalık|saatlik|günlük)\s+mumda\s+([\d,.]+)(?:\'i?n?)?\s*(?:seviyesinin\s+)?(altında|üstünde|üzerinde)\s+kapanırsa',
                "processor": self._parse_turkish_time_based_candle
            },
            
            {
                "name": "turkish_level",
                "pattern": r'fiyat\s+([\d,.]+)\s+seviyesinin\s+(altında|üstünde|üzerinde)',
                "processor": self._parse_turkish_level
            },
            
            {
                "name": "turkish_simple",
                "pattern": r'([\d,.]+)\s+(altında|üstünde|üzerinde)',
                "processor": self._parse_turkish_simple
            },
            
            {
                "name": "turkish_reverse",
                "pattern": r'(altında|üstünde|üzerinde)\s+([\d,.]+)',
                "processor": self._parse_turkish_reverse
            },
            
            # Yüzde değişim desenleri
            {
                "name": "percentage_change",
                "pattern": r'if\s+price\s+(drops|rises|falls|increases)\s+(by|more\s+than)\s+([\d.]+)%',
                "processor": self._parse_percentage_change
            },
            
            # Teknik gösterge bazlı desenler
            {
                "name": "technical_indicator",
                "pattern": r'if\s+(rsi|macd|ema|sma|bb|bollinger)\s+(crosses?|goes?\s+(above|below|through))\s+([\d.]+)',
                "processor": self._parse_technical_indicator
            },
            
            # Çoklu koşul desenleri
            {
                "name": "multiple_conditions",
                "pattern": r'if\s+price\s+(closes?\s+)?(below|above)\s+([\d,.]+)\s+and\s+(rsi|macd|volume)\s+(is|crosses?|goes?\s+(above|below))',
                "processor": self._parse_multiple_conditions
            }
        ]
    
    def parse(self, text: str) -> Tuple[Optional[str], Optional[float], Optional[str], Optional[Dict]]:
        """
        Invalidation condition metnini parse eder
        
        Args:
            text: GLM'den gelen invalidation condition metni
            
        Returns:
            (direction, price, time_frame, metadata) tuple
            direction: "below" veya "above"
            price: Fiyat seviyesi
            time_frame: Zaman dilimi ("3m", "5m", vb.)
            metadata: Ek bilgiler (yüzde, teknik gösterge, vb.)
        """
        if not text or text == "N/A":
            return None, None, None, None
        
        try:
            # Tüm desenleri dene
            for pattern_info in self.patterns:
                match = re.search(pattern_info["pattern"], text, re.IGNORECASE)
                if match:
                    result = pattern_info["processor"](match, text)
                    if result:
                        direction, price, time_frame, metadata = result
                        logger.debug(
                            "✅ Parsed invalidation with pattern '%s': direction=%s price=%.2f time_frame=%s metadata=%s",
                            pattern_info["name"],
                            direction,
                            price,
                            time_frame,
                            metadata
                        )
                        return direction, price, time_frame, metadata
            
            # Hiçbir desen eşleşmedi
            logger.warning(
                "⚠️ Could not parse invalidation condition with any pattern: '%s'",
                text
            )
            return None, None, None, None
            
        except Exception as exc:
            logger.error(
                "❌ Error parsing invalidation condition '%s': %s",
                text,
                exc
            )
            return None, None, None, None
    
    def _parse_time_based_candle(self, match, original_text: str) -> Tuple[Optional[str], Optional[float], Optional[str], Optional[Dict]]:
        """Zaman bazlı mum deseni parse eder"""
        direction = match.group(1).lower()
        price = float(match.group(2).replace(",", ""))
        time_value = int(match.group(3))
        time_unit = match.group(4).lower()
        
        # Zaman birimini normalize et
        if time_unit.startswith('m'):
            time_frame = f"{time_value}m"
        elif time_unit.startswith('h'):
            time_frame = f"{time_value}h"
        elif time_unit.startswith('d'):
            time_frame = f"{time_value}d"
        else:
            time_frame = "3m"  # Varsayılan
            
        return direction, price, time_frame, {"type": "time_based_candle"}
    
    def _parse_simple_price_level(self, match, original_text: str) -> Tuple[Optional[str], Optional[float], Optional[str], Optional[Dict]]:
        """Basit fiyat seviyesi deseni parse eder"""
        direction = match.group(3).lower()
        price = float(match.group(4).replace(",", ""))
        
        return direction, price, "3m", {"type": "simple_price_level"}
    
    def _parse_turkish_time_based_candle(self, match, original_text: str) -> Tuple[Optional[str], Optional[float], Optional[str], Optional[Dict]]:
        """Türkçe zaman bazlı mum deseni parse eder"""
        time_value = int(match.group(2))
        time_unit = match.group(3).lower()
        price_str = match.group(4).replace(",", "").replace(".", "").replace("'", "")
        price = float(price_str)
        direction_tr = match.group(5).lower()
        
        # Yönü çevir
        direction = "below" if direction_tr == "altında" else "above"
        
        # Zaman birimini normalize et
        if "dakika" in time_unit:
            time_frame = f"{time_value}m"
        elif "saat" in time_unit:
            time_frame = f"{time_value}h"
        elif "gün" in time_unit:
            time_frame = f"{time_value}d"
        else:
            time_frame = "3m"  # Varsayılan
        
        return direction, price, time_frame, {"type": "turkish_time_based_candle"}
    
    def _parse_turkish_level(self, match, original_text: str) -> Tuple[Optional[str], Optional[float], Optional[str], Optional[Dict]]:
        """Türkçe seviye deseni parse eder"""
        price = float(match.group(1).replace(",", "").replace(".", ""))
        direction = "below" if match.group(2).lower() == "altında" else "above"
        
        return direction, price, "3m", {"type": "turkish_level"}
    
    def _parse_turkish_simple(self, match, original_text: str) -> Tuple[Optional[str], Optional[float], Optional[str], Optional[Dict]]:
        """Basit Türkçe deseni parse eder"""
        price = float(match.group(1).replace(",", "").replace(".", ""))
        direction = "below" if match.group(2).lower() == "altında" else "above"
        
        return direction, price, "3m", {"type": "turkish_simple"}
    
    def _parse_turkish_reverse(self, match, original_text: str) -> Tuple[Optional[str], Optional[float], Optional[str], Optional[Dict]]:
        """Ters Türkçe deseni parse eder"""
        direction = "below" if match.group(1).lower() == "altında" else "above"
        price = float(match.group(2).replace(",", "").replace(".", ""))
        
        return direction, price, "3m", {"type": "turkish_reverse"}
    
    def _parse_percentage_change(self, match, original_text: str) -> Tuple[Optional[str], Optional[float], Optional[str], Optional[Dict]]:
        """Yüzde değişim deseni parse eder"""
        direction = "below" if match.group(1).lower() in ["drops", "falls"] else "above"
        percentage = float(match.group(2))
        
        # Bu durumda mevcut fiyata göre hesaplanmalı
        # Metadata olarak yüzdeyi sakla
        return direction, None, "3m", {
            "type": "percentage_change",
            "percentage": percentage,
            "action": match.group(1).lower()
        }
    
    def _parse_technical_indicator(self, match, original_text: str) -> Tuple[Optional[str], Optional[float], Optional[str], Optional[Dict]]:
        """Teknik gösterge deseni parse eder"""
        indicator = match.group(1).lower()
        direction = match.group(3).lower()
        level = float(match.group(4))
        
        # Teknik gösterge bazlı koşullar metadata olarak sakla
        return direction, level, "3m", {
            "type": "technical_indicator",
            "indicator": indicator,
            "action": match.group(2).lower()
        }
    
    def _parse_multiple_conditions(self, match, original_text: str) -> Tuple[Optional[str], Optional[float], Optional[str], Optional[Dict]]:
        """Çoklu koşul deseni parse eder"""
        direction = match.group(2).lower()
        price = float(match.group(3).replace(",", ""))
        second_indicator = match.group(4).lower()
        second_action = match.group(5).lower()
        
        # Çoklu koşulları metadata olarak sakla
        return direction, price, "3m", {
            "type": "multiple_conditions",
            "second_indicator": second_indicator,
            "second_action": second_action
        }
    
    def validate_condition(self, direction: str, price: float, current_price: float) -> bool:
        """
        Koşulun geçerliliğini kontrol eder
        
        Args:
            direction: "below" veya "above"
            price: Fiyat seviyesi
            current_price: Güncel fiyat
            
        Returns:
            True if koşul sağlanıyorsa
        """
        if direction == "below":
            return current_price < price
        elif direction == "above":
            return current_price > price
        
        return False
    
    def generate_condition_description(self, direction: str, price: float, time_frame: str = "3m") -> str:
        """
        Koşul açıklaması oluşturur
        
        Args:
            direction: "below" veya "above"
            price: Fiyat seviyesi
            time_frame: Zaman dilimi
            
        Returns:
            Formatlanmış koşul metni
        """
        if direction == "below":
            return f"If price closes below {price:.2f} on {time_frame} candle"
        else:
            return f"If price closes above {price:.2f} on {time_frame} candle"
