import logging
import re
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    pass


def parse_invalidation_price(invalidation_condition: str) -> Optional[float]:
    """
    Invalidation condition'dan fiyat seviyesini çıkar

    Örnekler:
    - "If price closes below 109000 on 3m candle" → 109000.0
    - "If price closes above 113000 on 5m candle" → 113000.0
    - "Fiyat 109200 seviyesinin altında kapanırsa" → 109200.0
    - "If RSI drops below 65 AND price closes below 111000" → 111000.0
    """
    if not invalidation_condition:
        return None

    patterns = [
        r"(?:below|above|altında|üstünde|üstü|altı)\s+[\$]?([0-9]{3,}(?:[.,][0-9]+)?)",
        r"[\$]?([0-9]{3,}(?:[.,][0-9]+)?)\s+(?:seviyesinin|level)",
        r"(?:closes|kapan|kapat)\s+(?:below|above|altında|üstünde)\s+[\$]?([0-9]{3,}(?:[.,][0-9]+)?)",
    ]

    for pattern in patterns:
        match = re.search(pattern, invalidation_condition, re.IGNORECASE)
        if match:
            price_str = match.group(1).replace(",", ".")
            try:
                return float(price_str)
            except ValueError:
                continue

    logger.warning(f"Could not parse invalidation price from: {invalidation_condition}")
    return None


def validate_exit_plan_update(
    old_plan: Dict, new_plan: Dict, entry_price: float, position_side: str
) -> Tuple[bool, Optional[str]]:
    """
    Exit plan güncellemesi validasyon kontrolleri

    Args:
        old_plan: Mevcut exit plan
        new_plan: Yeni önerilen exit plan
        entry_price: Pozisyon giriş fiyatı
        position_side: "LONG" veya "SHORT"

    Returns:
        (is_valid, error_message)

    Raises:
        ValidationError: Validasyon başarısız olduğunda
    """
    try:
        stop_loss = new_plan.get("stop_loss")
        profit_target = new_plan.get("profit_target")
        invalidation_condition = new_plan.get("invalidation_condition", "")

        if not stop_loss or not profit_target:
            raise ValidationError("Stop loss veya profit target eksik")

        inv_price = parse_invalidation_price(invalidation_condition)

        if position_side == "LONG":
            _validate_long_position(entry_price, stop_loss, profit_target, inv_price)
        elif position_side == "SHORT":
            _validate_short_position(entry_price, stop_loss, profit_target, inv_price)
        else:
            raise ValidationError(f"Geçersiz position_side: {position_side}")

        _validate_common_rules(entry_price, stop_loss, profit_target, position_side)

        return True, None

    except ValidationError as e:
        logger.warning(f"Exit plan validation failed: {str(e)}")
        return False, str(e)
    except Exception as e:
        logger.error(f"Unexpected validation error: {str(e)}", exc_info=True)
        return False, f"Beklenmeyen hata: {str(e)}"


def _validate_long_position(
    entry_price: float, stop_loss: float, profit_target: float, inv_price: Optional[float]
):
    """LONG pozisyon özel validasyonları"""

    if stop_loss >= entry_price:
        raise ValidationError(
            f"LONG SL entry'nin üstünde olamaz! " f"Entry={entry_price}, SL={stop_loss}"
        )

    if profit_target <= entry_price:
        raise ValidationError(
            f"LONG TP entry'nin altında olamaz! " f"Entry={entry_price}, TP={profit_target}"
        )

    if inv_price is not None:
        if not (entry_price > inv_price > stop_loss):
            raise ValidationError(
                f"LONG invalidation hatalı konumda! "
                f"Olması gereken: Entry({entry_price:.2f}) > Invalidation({inv_price:.2f}) > SL({stop_loss:.2f})"
            )

        inv_position_pct = (inv_price - stop_loss) / (entry_price - stop_loss)
        if not (0.2 <= inv_position_pct <= 0.6):
            raise ValidationError(
                f"LONG invalidation SL'ye çok yakın/uzak! "
                f"Pozisyon: %{inv_position_pct*100:.1f} (olmalı: %20-%60)"
            )


def _validate_short_position(
    entry_price: float, stop_loss: float, profit_target: float, inv_price: Optional[float]
):
    """SHORT pozisyon özel validasyonları"""

    if stop_loss <= entry_price:
        raise ValidationError(
            f"SHORT SL entry'nin altında olamaz! " f"Entry={entry_price}, SL={stop_loss}"
        )

    if profit_target >= entry_price:
        raise ValidationError(
            f"SHORT TP entry'nin üstünde olamaz! " f"Entry={entry_price}, TP={profit_target}"
        )

    if inv_price is not None:
        if not (stop_loss > inv_price > entry_price):
            raise ValidationError(
                f"SHORT invalidation hatalı konumda! "
                f"Olması gereken: SL({stop_loss:.2f}) > Invalidation({inv_price:.2f}) > Entry({entry_price:.2f})"
            )

        inv_position_pct = (stop_loss - inv_price) / (stop_loss - entry_price)
        if not (0.2 <= inv_position_pct <= 0.6):
            raise ValidationError(
                f"SHORT invalidation Entry'ye çok yakın/uzak! "
                f"Pozisyon: %{inv_position_pct*100:.1f} (olmalı: %20-%60)"
            )


def _validate_common_rules(
    entry_price: float, stop_loss: float, profit_target: float, position_side: str
):
    """Hem LONG hem SHORT için ortak kurallar"""

    max_sl_distance_pct = 0.10
    sl_distance = abs(stop_loss - entry_price) / entry_price
    if sl_distance > max_sl_distance_pct:
        raise ValidationError(f"SL çok geniş: %{sl_distance*100:.1f} (max: %10)")

    risk = abs(entry_price - stop_loss)
    reward = abs(profit_target - entry_price)

    if risk == 0:
        raise ValidationError("Risk 0 olamaz!")

    risk_reward_ratio = reward / risk
    if risk_reward_ratio < 1.5:
        raise ValidationError(f"Risk/Reward çok düşük: {risk_reward_ratio:.2f} (min: 1.5)")
