"""
Partial Take Profit Manager - Kademeli kar alma sistemi.

Features:
- 3-4 kademeli TP: Strateji bazlı dağılım
- TP1 sonrası breakeven hareketi
- TP2 sonrası trailing aktivasyonu
- Risk azaltma ve kar kilitleme
- Dinamik strateji seçimi (Conservative/Balanced/Aggressive)
- S/R seviyelerine snap özelliği

Supports both Scalp (15-30m) and Swing (4H) trading modes.

v2.0 Features:
- TPStrategy enum for dynamic strategy selection
- Automatic strategy selection based on market conditions
- S/R level snapping for smarter TP placement
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class TPStrategy(Enum):
    """TP strateji tipleri"""
    CONSERVATIVE = "conservative"  # Hızlı kar al - düşük vol, zayıf trend
    BALANCED = "balanced"          # Dengeli - orta koşullar
    AGGRESSIVE = "aggressive"      # Trendi sür - güçlü trend, yüksek confluence


class TPStatus(Enum):
    PENDING = "pending"
    TRIGGERED = "triggered"
    CANCELLED = "cancelled"


@dataclass
class TPLevel:
    """Single take profit level"""
    level_id: int
    price: float
    percentage: float  # % of position to close
    status: TPStatus = TPStatus.PENDING
    triggered_at: Optional[datetime] = None
    actual_close_price: Optional[float] = None


@dataclass
class PartialTPPlan:
    """Complete partial take profit plan"""
    entry_price: float
    position_side: str  # "LONG" or "SHORT"
    initial_quantity: float
    stop_loss: float
    levels: List[TPLevel] = field(default_factory=list)
    remaining_quantity: float = 0.0
    is_breakeven: bool = False
    trailing_active: bool = False
    trailing_stop: Optional[float] = None
    created_at: Optional[datetime] = None
    strategy: TPStrategy = TPStrategy.BALANCED  # v2.0: Selected strategy

    def __post_init__(self):
        self.remaining_quantity = self.initial_quantity
        self.created_at = datetime.utcnow()

    @property
    def risk_per_unit(self) -> float:
        """1R = Entry ile SL arası mesafe"""
        return abs(self.entry_price - self.stop_loss)

    @property
    def remaining_position_pct(self) -> float:
        """Henüz kapatılmamış pozisyon yüzdesi"""
        if self.initial_quantity <= 0:
            return 0.0
        return (self.remaining_quantity / self.initial_quantity) * 100


class PartialTakeProfitManager:
    """
    Manages partial take profit execution with automatic:
    - TP level triggering
    - Breakeven adjustment after TP1
    - Trailing stop activation after TP2
    - Dynamic strategy selection (v2.0)

    Mode-aware configuration for Scalp vs Swing trading.
    """

    # Mode-specific default configurations
    MODE_CONFIGS = {
        "scalp": {
            "rr_levels": [1.0, 1.5, 2.5],  # R:R targets
            "percentages": [40, 30, 30],   # % to close at each level
            "atr_pct": 1.0,                # Trailing distance
        },
        "swing": {
            # GLM Elite Swing Trader config update
            "rr_levels": [1.5, 2.5, 3.5],  # TP1: 1.5R, TP2: 2.5R, TP3: 3.5R (changed from 4.0)
            "percentages": [33, 33, 34],   # Equal distribution (changed from 35/35/30)
            "atr_pct": 1.0,                # Trailing distance (changed from 1.5)
        }
    }

    # v2.0: Strategy-based TP distributions
    STRATEGY_DISTRIBUTIONS = {
        TPStrategy.CONSERVATIVE: [
            {"r": 0.5, "pct": 30},   # 0.5R'de %30 - Hızlı kar al
            {"r": 1.0, "pct": 40},   # 1R'de %40
            {"r": 1.5, "pct": 20},   # 1.5R'de %20
            {"r": 2.0, "pct": 10},   # 2R'de kalan %10
        ],
        TPStrategy.BALANCED: [
            {"r": 1.0, "pct": 25},   # 1R'de %25
            {"r": 2.0, "pct": 35},   # 2R'de %35
            {"r": 3.0, "pct": 25},   # 3R'de %25
            {"r": 4.0, "pct": 15},   # 4R'de kalan %15
        ],
        TPStrategy.AGGRESSIVE: [
            {"r": 1.5, "pct": 20},   # 1.5R'de %20
            {"r": 3.0, "pct": 30},   # 3R'de %30
            {"r": 5.0, "pct": 30},   # 5R'de %30
            {"r": 8.0, "pct": 20},   # 8R'de kalan %20 (runner)
        ],
    }

    def __init__(
        self,
        mode: str = "scalp",
        atr_pct: Optional[float] = None,
        feature_enabled: bool = True,
    ):
        """
        Initialize PartialTakeProfitManager.

        Args:
            mode: Trading mode - "scalp" or "swing"
            atr_pct: Override ATR percentage for trailing
            feature_enabled: Enable/disable the feature
        """
        self.mode = mode
        self.feature_enabled = feature_enabled
        self.config = self.MODE_CONFIGS.get(mode, self.MODE_CONFIGS["scalp"])
        self.atr_pct = atr_pct if atr_pct is not None else self.config["atr_pct"]
        self.active_plans: Dict[str, PartialTPPlan] = {}

        logger.info(
            "PartialTakeProfitManager initialized | Mode: %s | R:R Levels: %s | Enabled: %s",
            mode, self.config["rr_levels"], feature_enabled
        )

    def create_tp_plan(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        position_side: str,
        quantity: float,
        custom_rr_levels: Optional[List[float]] = None,
        custom_percentages: Optional[List[float]] = None,
    ) -> Optional[PartialTPPlan]:
        """
        Create a partial take profit plan.

        Args:
            symbol: Trading pair
            entry_price: Entry price
            stop_loss: Stop loss price
            position_side: "LONG" or "SHORT"
            quantity: Total position size
            custom_rr_levels: Optional custom R:R levels
            custom_percentages: Optional custom percentages

        Returns:
            PartialTPPlan or None if feature disabled
        """
        if not self.feature_enabled:
            return None

        # Calculate risk (R)
        if position_side == "LONG":
            risk = entry_price - stop_loss
        else:
            risk = stop_loss - entry_price

        if risk <= 0:
            logger.error(
                "Invalid risk calculation: entry=%.2f, sl=%.2f, side=%s",
                entry_price, stop_loss, position_side
            )
            return None

        # Use custom or default levels
        rr_levels = custom_rr_levels or self.config["rr_levels"]
        percentages = custom_percentages or self.config["percentages"]

        # Ensure they match
        if len(rr_levels) != len(percentages):
            logger.error("R:R levels and percentages must have same length")
            return None

        # Create TP levels
        levels = []
        for i, (rr, pct) in enumerate(zip(rr_levels, percentages)):
            if position_side == "LONG":
                tp_price = entry_price + (risk * rr)
            else:
                tp_price = entry_price - (risk * rr)

            levels.append(TPLevel(
                level_id=i + 1,
                price=round(tp_price, 2),
                percentage=pct,
            ))

        plan = PartialTPPlan(
            entry_price=entry_price,
            position_side=position_side,
            initial_quantity=quantity,
            stop_loss=stop_loss,
            levels=levels,
        )

        self.active_plans[symbol] = plan

        logger.info(
            "Created Partial TP Plan for %s: Entry=%.2f, SL=%.2f, "
            "TP1=%.2f (%.0f%%), TP2=%.2f (%.0f%%), TP3=%.2f (%.0f%%)",
            symbol, entry_price, stop_loss,
            levels[0].price, levels[0].percentage,
            levels[1].price, levels[1].percentage,
            levels[2].price, levels[2].percentage
        )

        return plan

    # =========================================================================
    # v2.0: Dynamic Strategy Selection Methods
    # =========================================================================

    def select_strategy(
        self,
        trend_strength: str,
        volatility_regime: str,
        mtf_confluence: float,
        win_rate: float = 0.50,
    ) -> TPStrategy:
        """
        Piyasa koşullarına göre en uygun TP stratejisini seç.

        Args:
            trend_strength: "STRONG", "MODERATE", "WEAK"
            volatility_regime: "low", "medium", "high", "extreme"
            mtf_confluence: MTF uyum skoru (0-100)
            win_rate: Son işlemlerin win rate'i (0-1)

        Returns:
            TPStrategy enum value
        """
        score = 0

        # Trend gücü
        trend_upper = trend_strength.upper() if trend_strength else "MODERATE"
        if trend_upper == "STRONG":
            score += 2
        elif trend_upper == "MODERATE":
            score += 1
        elif trend_upper == "WEAK":
            score -= 1

        # Volatilite
        vol_lower = volatility_regime.lower() if volatility_regime else "medium"
        if vol_lower in ["low", "medium"]:
            score += 1  # Düşük vol = trend sürebilir
        elif vol_lower == "extreme":
            score -= 2  # Yüksek vol = hızlı kar al

        # MTF uyumu
        if mtf_confluence >= 70:
            score += 2
        elif mtf_confluence >= 55:
            score += 1
        else:
            score -= 1

        # Win rate (düşükse daha konservatif)
        if win_rate < 0.45:
            score -= 2
        elif win_rate > 0.60:
            score += 1

        # Strateji seçimi
        if score >= 4:
            strategy = TPStrategy.AGGRESSIVE
        elif score >= 1:
            strategy = TPStrategy.BALANCED
        else:
            strategy = TPStrategy.CONSERVATIVE

        logger.info(
            "Strategy selected: %s | Score: %d | Trend: %s, Vol: %s, MTF: %.0f, WR: %.0f%%",
            strategy.value, score, trend_strength, volatility_regime, mtf_confluence, win_rate * 100
        )

        return strategy

    def _snap_to_sr(
        self,
        tp_price: float,
        sr_levels: List[float],
        position_side: str,
        snap_threshold_pct: float = 1.0,
    ) -> float:
        """
        TP fiyatı S/R seviyesine yakınsa, S/R'yi kullan.
        Akıllı TP yerleşimi için.

        Args:
            tp_price: Hesaplanan TP fiyatı
            sr_levels: S/R seviyeleri listesi
            position_side: "LONG" veya "SHORT"
            snap_threshold_pct: Snap için max mesafe yüzdesi

        Returns:
            Adjust edilmiş TP fiyatı
        """
        if not sr_levels or tp_price <= 0:
            return tp_price

        for sr in sr_levels:
            if sr <= 0:
                continue

            distance_pct = abs(tp_price - sr) / tp_price * 100

            if distance_pct <= snap_threshold_pct:
                # LONG için S/R'nin biraz altında, SHORT için biraz üstünde
                if position_side == "LONG":
                    adjusted = sr * 0.998  # %0.2 altında
                else:
                    adjusted = sr * 1.002  # %0.2 üstünde

                logger.debug(
                    "TP snapped to S/R: %.2f -> %.2f (S/R: %.2f, dist: %.2f%%)",
                    tp_price, adjusted, sr, distance_pct
                )
                return adjusted

        return tp_price

    def create_tp_plan_advanced(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        position_side: str,
        quantity: float,
        # Market context for strategy selection
        trend_strength: str = "MODERATE",
        volatility_regime: str = "medium",
        mtf_confluence: float = 50.0,
        win_rate: float = 0.50,
        sr_levels: Optional[List[float]] = None,
    ) -> Optional[PartialTPPlan]:
        """
        Dinamik strateji seçimi ile TP planı oluştur.
        S/R seviyelerine göre TP'leri ayarla.

        Args:
            symbol: Trading pair
            entry_price: Entry price
            stop_loss: Stop loss price
            position_side: "LONG" or "SHORT"
            quantity: Total position size
            trend_strength: Trend gücü
            volatility_regime: Volatilite rejimi
            mtf_confluence: MTF uyum skoru
            win_rate: Recent win rate
            sr_levels: S/R seviyeleri (optional)

        Returns:
            PartialTPPlan with dynamic strategy or None
        """
        if not self.feature_enabled:
            return None

        # Calculate risk (R)
        if position_side == "LONG":
            risk = entry_price - stop_loss
        else:
            risk = stop_loss - entry_price

        if risk <= 0:
            logger.error(
                "Invalid risk calculation: entry=%.2f, sl=%.2f, side=%s",
                entry_price, stop_loss, position_side
            )
            return None

        # Select strategy based on market conditions
        strategy = self.select_strategy(
            trend_strength, volatility_regime, mtf_confluence, win_rate
        )

        # Get distribution for selected strategy
        distribution = self.STRATEGY_DISTRIBUTIONS[strategy]

        # Create TP levels
        levels = []
        for idx, level_config in enumerate(distribution):
            r_mult = level_config["r"]
            pct = level_config["pct"]

            # Calculate TP price
            if position_side == "LONG":
                tp_price = entry_price + (risk * r_mult)
            else:
                tp_price = entry_price - (risk * r_mult)

            # Snap to S/R if provided
            if sr_levels:
                tp_price = self._snap_to_sr(tp_price, sr_levels, position_side)

            levels.append(TPLevel(
                level_id=idx + 1,
                price=round(tp_price, 2),
                percentage=pct,
            ))

        # Create plan
        plan = PartialTPPlan(
            entry_price=entry_price,
            position_side=position_side,
            initial_quantity=quantity,
            stop_loss=stop_loss,
            levels=levels,
            strategy=strategy,
        )

        self.active_plans[symbol] = plan

        # Log creation
        level_str = ", ".join([
            f"TP{l.level_id}=${l.price:.2f}({l.percentage}%)"
            for l in levels
        ])
        logger.info(
            "Created Advanced TP Plan for %s: Strategy=%s | Entry=%.2f, SL=%.2f | %s",
            symbol, strategy.value, entry_price, stop_loss, level_str
        )

        return plan

    def get_compact_summary(self, symbol: str) -> str:
        """
        Get compact summary for GLM prompt (~40 tokens).

        Args:
            symbol: Trading pair

        Returns:
            Compact string like: "TP: BALANCED | TP1✓ TP2○ TP3○ TP4○ | BE✓ TRAIL○"
        """
        if symbol not in self.active_plans:
            return ""

        plan = self.active_plans[symbol]

        # Status emojis - using ASCII for compatibility
        tp_status = []
        for level in plan.levels:
            status_char = "Y" if level.status == TPStatus.TRIGGERED else "O"
            tp_status.append(f"TP{level.level_id}{status_char}")

        be_status = "Y" if plan.is_breakeven else "O"
        trail_status = "Y" if plan.trailing_active else "O"

        return (
            f"TP: {plan.strategy.value.upper()} | "
            f"{' '.join(tp_status)} | "
            f"BE{be_status} TRAIL{trail_status}"
        )

    def check_and_execute(
        self,
        symbol: str,
        current_price: float,
        current_high: float,
        current_low: float
    ) -> Optional[Dict]:
        """
        Check if any TP levels are triggered and return execution instructions.

        Args:
            symbol: Trading pair
            current_price: Current market price
            current_high: Current candle high
            current_low: Current candle low

        Returns:
            Dict with action details or None if no action needed
        """
        if not self.feature_enabled:
            return None

        if symbol not in self.active_plans:
            return None

        plan = self.active_plans[symbol]
        actions = []

        for level in plan.levels:
            if level.status != TPStatus.PENDING:
                continue

            # Check if price hit TP level
            triggered = False
            if plan.position_side == "LONG":
                if current_high >= level.price:
                    triggered = True
            else:  # SHORT
                if current_low <= level.price:
                    triggered = True

            if triggered:
                # Calculate quantity to close
                close_qty = plan.initial_quantity * (level.percentage / 100)

                level.status = TPStatus.TRIGGERED
                level.triggered_at = datetime.utcnow()
                level.actual_close_price = level.price
                plan.remaining_quantity -= close_qty

                action = {
                    "action": "PARTIAL_CLOSE",
                    "level": level.level_id,
                    "price": level.price,
                    "quantity": close_qty,
                    "percentage": level.percentage,
                    "remaining_qty": plan.remaining_quantity,
                    "remaining_pct": (plan.remaining_quantity / plan.initial_quantity) * 100,
                }
                actions.append(action)

                logger.info(
                    "TP%d triggered for %s at %.2f - Closing %.4f (%.0f%%), Remaining: %.4f",
                    level.level_id, symbol, level.price, close_qty,
                    level.percentage, plan.remaining_quantity
                )

                # Special actions after specific TPs
                if level.level_id == 1:
                    # After TP1: Move SL to breakeven
                    self._move_to_breakeven(plan)
                    action["sl_adjustment"] = {
                        "new_stop_loss": plan.stop_loss,
                        "reason": "Moved to breakeven after TP1"
                    }

                elif level.level_id == 2:
                    # After TP2: Activate trailing stop
                    self._activate_trailing(plan, current_price)
                    action["trailing_activated"] = True
                    action["trailing_stop"] = plan.trailing_stop

        # Check trailing stop if active
        if plan.trailing_active and plan.remaining_quantity > 0:
            trail_action = self._check_trailing_stop(plan, current_price, current_low, current_high)
            if trail_action:
                actions.append(trail_action)

        if actions:
            return {
                "symbol": symbol,
                "actions": actions,
                "plan_status": self._get_plan_status(plan)
            }

        return None

    def _move_to_breakeven(self, plan: PartialTPPlan):
        """Move stop loss to breakeven (entry price) after TP1"""
        # Add small buffer for fees
        buffer_pct = 0.05  # 0.05% buffer

        if plan.position_side == "LONG":
            new_sl = plan.entry_price * (1 + buffer_pct / 100)
        else:
            new_sl = plan.entry_price * (1 - buffer_pct / 100)

        old_sl = plan.stop_loss
        plan.stop_loss = round(new_sl, 2)
        plan.is_breakeven = True

        logger.info(
            "Moved SL to breakeven for %s: %.2f -> %.2f",
            plan.position_side, old_sl, plan.stop_loss
        )

    def _activate_trailing(self, plan: PartialTPPlan, current_price: float):
        """Activate trailing stop after TP2"""
        # Trail distance: ATR percentage (approximately)
        trail_distance_pct = max(self.atr_pct, 0.5)

        if plan.position_side == "LONG":
            plan.trailing_stop = current_price * (1 - trail_distance_pct / 100)
        else:
            plan.trailing_stop = current_price * (1 + trail_distance_pct / 100)

        plan.trailing_stop = round(plan.trailing_stop, 2)
        plan.trailing_active = True

        logger.info(
            "Trailing stop activated: %.2f (%.1f%% distance)",
            plan.trailing_stop, trail_distance_pct
        )

    def _check_trailing_stop(
        self,
        plan: PartialTPPlan,
        current_price: float,
        current_low: float,
        current_high: float
    ) -> Optional[Dict]:
        """Update trailing stop and check if hit"""
        if not plan.trailing_stop:
            return None

        trail_distance_pct = max(self.atr_pct, 0.5)

        # Update trailing stop
        if plan.position_side == "LONG":
            new_trail = current_price * (1 - trail_distance_pct / 100)
            if new_trail > plan.trailing_stop:
                old_trail = plan.trailing_stop
                plan.trailing_stop = round(new_trail, 2)
                logger.debug("Trailing stop updated: %.2f -> %.2f", old_trail, plan.trailing_stop)

            # Check if hit
            if current_low <= plan.trailing_stop:
                return {
                    "action": "TRAILING_STOP_HIT",
                    "price": plan.trailing_stop,
                    "quantity": plan.remaining_quantity,
                    "reason": "Trailing stop triggered"
                }
        else:  # SHORT
            new_trail = current_price * (1 + trail_distance_pct / 100)
            if new_trail < plan.trailing_stop:
                plan.trailing_stop = round(new_trail, 2)

            if current_high >= plan.trailing_stop:
                return {
                    "action": "TRAILING_STOP_HIT",
                    "price": plan.trailing_stop,
                    "quantity": plan.remaining_quantity,
                    "reason": "Trailing stop triggered"
                }

        return None

    def _get_plan_status(self, plan: PartialTPPlan) -> Dict:
        """Get current status of the plan"""
        triggered_count = sum(1 for l in plan.levels if l.status == TPStatus.TRIGGERED)
        pending_count = sum(1 for l in plan.levels if l.status == TPStatus.PENDING)

        return {
            "triggered_levels": triggered_count,
            "pending_levels": pending_count,
            "remaining_quantity": plan.remaining_quantity,
            "remaining_pct": (plan.remaining_quantity / plan.initial_quantity) * 100,
            "is_breakeven": plan.is_breakeven,
            "trailing_active": plan.trailing_active,
            "trailing_stop": plan.trailing_stop,
            "current_stop_loss": plan.stop_loss,
        }

    def get_plan_for_prompt(self, symbol: str) -> Optional[str]:
        """Get human-readable plan status for GLM prompt"""
        if not self.feature_enabled:
            return None

        if symbol not in self.active_plans:
            return None

        plan = self.active_plans[symbol]
        status = self._get_plan_status(plan)

        lines = [
            f"Partial TP Plan ({plan.position_side}):",
        ]

        for level in plan.levels:
            status_emoji = "Triggered" if level.status == TPStatus.TRIGGERED else "Pending"
            lines.append(f"  TP{level.level_id}: ${level.price:.2f} ({level.percentage}%) - {status_emoji}")

        lines.extend([
            f"  Current SL: ${plan.stop_loss:.2f} {'(Breakeven)' if plan.is_breakeven else ''}",
            f"  Remaining: {plan.remaining_quantity:.4f} ({status['remaining_pct']:.0f}%)",
        ])

        if plan.trailing_active:
            lines.append(f"  Trailing Stop: ${plan.trailing_stop:.2f}")

        return "\n".join(lines)

    def get_status(self, symbol: str) -> Optional[Dict]:
        """Get current status of a plan"""
        if not self.feature_enabled:
            return None

        if symbol not in self.active_plans:
            return None

        plan = self.active_plans[symbol]
        return {
            "symbol": symbol,
            "position_side": plan.position_side,
            "entry_price": plan.entry_price,
            "initial_quantity": plan.initial_quantity,
            "remaining_quantity": plan.remaining_quantity,
            "stop_loss": plan.stop_loss,
            "is_breakeven": plan.is_breakeven,
            "trailing_active": plan.trailing_active,
            "trailing_stop": plan.trailing_stop,
            "levels": [
                {
                    "level_id": l.level_id,
                    "price": l.price,
                    "percentage": l.percentage,
                    "status": l.status.value,
                    "triggered_at": l.triggered_at.isoformat() if l.triggered_at else None,
                }
                for l in plan.levels
            ],
            "feature_enabled": True,
        }

    def remove_plan(self, symbol: str) -> bool:
        """Remove plan for a symbol (e.g., when position is fully closed)"""
        if symbol in self.active_plans:
            del self.active_plans[symbol]
            logger.info("Partial TP plan removed for %s", symbol)
            return True
        return False

    def should_apply_to_trade(self, trade_created_at: datetime) -> bool:
        """
        Check if partial TP should apply to a specific trade.
        For backward compatibility with existing positions.
        """
        if not self.feature_enabled:
            return False

        feature_release_date = datetime(2024, 11, 26, 0, 0, 0)
        return trade_created_at >= feature_release_date

    def get_all_active(self) -> List[Dict]:
        """Get all active partial TP plans"""
        if not self.feature_enabled:
            return []

        return [
            {
                "symbol": symbol,
                "position_side": plan.position_side,
                "remaining_pct": (plan.remaining_quantity / plan.initial_quantity) * 100,
                "is_breakeven": plan.is_breakeven,
                "trailing_active": plan.trailing_active,
            }
            for symbol, plan in self.active_plans.items()
        ]


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test scalp mode
    manager = PartialTakeProfitManager(mode="scalp")

    # Create plan for LONG position
    plan = manager.create_tp_plan(
        symbol="BTCUSDT",
        entry_price=100000,
        stop_loss=98500,  # 1.5% SL
        position_side="LONG",
        quantity=0.5,
    )

    print(f"Created plan:")
    print(f"TP1: ${plan.levels[0].price} ({plan.levels[0].percentage}%)")
    print(f"TP2: ${plan.levels[1].price} ({plan.levels[1].percentage}%)")
    print(f"TP3: ${plan.levels[2].price} ({plan.levels[2].percentage}%)")

    # Simulate price hitting TP1
    print("\n--- Price hits TP1 ---")
    result = manager.check_and_execute("BTCUSDT", 101500, 101600, 101400)
    print(f"Result: {result}")

    # Simulate price hitting TP2
    print("\n--- Price hits TP2 ---")
    result = manager.check_and_execute("BTCUSDT", 102250, 102300, 102200)
    print(f"Result: {result}")

    print("\n" + manager.get_plan_for_prompt("BTCUSDT"))
