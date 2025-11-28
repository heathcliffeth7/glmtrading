"""
Drawdown Manager - Comprehensive drawdown management system.

Features:
- Daily/Weekly/Max drawdown tracking
- Automatic position sizing adjustment
- Trading pause enforcement
- Recovery tracking
- Consecutive loss monitoring

Supports both Scalp (15-30m) and Swing (4H) trading modes.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class DrawdownAction(Enum):
    NONE = "none"
    REDUCE_LEVERAGE = "reduce_leverage"
    REDUCE_POSITION_SIZE = "reduce_position_size"
    PAUSE_TRADING = "pause_trading"
    CLOSE_ALL_POSITIONS = "close_all"
    FULL_LOCKOUT = "full_lockout"


@dataclass
class DrawdownLimit:
    """Single drawdown limit configuration"""
    name: str
    threshold_pct: float  # Negative percentage
    action: DrawdownAction
    duration_hours: Optional[float] = None  # How long to enforce
    leverage_cap: Optional[int] = None  # Max leverage if REDUCE_LEVERAGE
    position_size_mult: Optional[float] = None  # Multiplier if REDUCE_POSITION_SIZE


@dataclass
class TradingSession:
    """Tracking for a trading session (daily/weekly)"""
    start_time: datetime
    start_equity: float
    current_equity: float
    peak_equity: float
    low_equity: float
    trades: List[Dict] = field(default_factory=list)

    @property
    def pnl_pct(self) -> float:
        if self.start_equity <= 0:
            return 0.0
        return ((self.current_equity - self.start_equity) / self.start_equity) * 100

    @property
    def drawdown_from_peak_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return ((self.current_equity - self.peak_equity) / self.peak_equity) * 100


class DrawdownManager:
    """
    Comprehensive drawdown management system.

    Features:
    - Daily/Weekly/Max drawdown tracking
    - Automatic position sizing adjustment
    - Trading pause enforcement
    - Recovery tracking

    Mode-aware configuration for Scalp vs Swing trading.
    """

    # Mode-specific default configurations
    MODE_CONFIGS = {
        "scalp": {
            "daily_limit": -2.0,
            "weekly_limit": -5.0,
            "max_limit": -15.0,
            "consecutive_loss_limit": 3,
        },
        "swing": {
            "daily_limit": -3.0,
            "weekly_limit": -7.0,
            "max_limit": -20.0,
            "consecutive_loss_limit": 2,
        }
    }

    def __init__(
        self,
        initial_equity: float,
        mode: str = "scalp",
        daily_limit: Optional[float] = None,
        weekly_limit: Optional[float] = None,
        max_limit: Optional[float] = None,
        consecutive_loss_limit: Optional[int] = None,
        feature_enabled: bool = True,
    ):
        """
        Initialize DrawdownManager.

        Args:
            initial_equity: Starting account equity
            mode: Trading mode - "scalp" or "swing"
            daily_limit: Override daily drawdown limit (negative %)
            weekly_limit: Override weekly drawdown limit (negative %)
            max_limit: Override max drawdown limit (negative %)
            consecutive_loss_limit: Override consecutive loss limit
            feature_enabled: Enable/disable the feature (for backward compatibility)
        """
        self.feature_enabled = feature_enabled
        self.mode = mode

        # Get mode-specific defaults
        config = self.MODE_CONFIGS.get(mode, self.MODE_CONFIGS["scalp"])

        self.initial_equity = initial_equity
        self.current_equity = initial_equity
        self.peak_equity = initial_equity

        # Use provided limits or defaults
        _daily = daily_limit if daily_limit is not None else config["daily_limit"]
        _weekly = weekly_limit if weekly_limit is not None else config["weekly_limit"]
        _max = max_limit if max_limit is not None else config["max_limit"]
        self.consecutive_loss_limit = consecutive_loss_limit or config["consecutive_loss_limit"]

        # Configure limits
        self.limits = [
            DrawdownLimit(
                name="consecutive_loss",
                threshold_pct=-999,  # Special handling
                action=DrawdownAction.REDUCE_POSITION_SIZE,
                position_size_mult=0.5,
            ),
            DrawdownLimit(
                name="daily",
                threshold_pct=_daily,
                action=DrawdownAction.PAUSE_TRADING,
                duration_hours=self._hours_until_utc_midnight(),
            ),
            DrawdownLimit(
                name="weekly",
                threshold_pct=_weekly,
                action=DrawdownAction.REDUCE_LEVERAGE,
                leverage_cap=5,
                duration_hours=168 - self._hours_since_week_start(),
            ),
            DrawdownLimit(
                name="max",
                threshold_pct=_max,
                action=DrawdownAction.FULL_LOCKOUT,
                duration_hours=24,
            ),
        ]

        self.consecutive_losses = 0

        # Session tracking
        self.daily_session = self._create_session()
        self.weekly_session = self._create_session()

        # Active restrictions
        self.active_restrictions: Dict[str, Dict] = {}

        # Trade history for analysis
        self.trade_history: List[Dict] = []

        logger.info(
            "DrawdownManager initialized | Mode: %s | Daily: %.1f%% | Weekly: %.1f%% | Max: %.1f%% | Enabled: %s",
            mode, _daily, _weekly, _max, feature_enabled
        )

    def _create_session(self) -> TradingSession:
        """Create a new trading session"""
        return TradingSession(
            start_time=datetime.utcnow(),
            start_equity=self.current_equity,
            current_equity=self.current_equity,
            peak_equity=self.current_equity,
            low_equity=self.current_equity,
        )

    def _hours_until_utc_midnight(self) -> float:
        """Calculate hours until next UTC midnight"""
        now = datetime.utcnow()
        midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return (midnight - now).total_seconds() / 3600

    def _hours_since_week_start(self) -> float:
        """Calculate hours since Monday 00:00 UTC"""
        now = datetime.utcnow()
        monday = now - timedelta(days=now.weekday())
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        return (now - monday).total_seconds() / 3600

    def update_equity(self, new_equity: float) -> Dict:
        """
        Update current equity and check all drawdown limits.

        Returns:
            Dict with current status and any triggered actions
        """
        if not self.feature_enabled:
            return {
                "current_equity": new_equity,
                "can_trade": True,
                "position_size_multiplier": 1.0,
                "max_leverage_allowed": 20,
                "feature_enabled": False,
            }

        old_equity = self.current_equity
        self.current_equity = new_equity

        # Update peak
        if new_equity > self.peak_equity:
            self.peak_equity = new_equity

        # Update sessions
        self._update_sessions(new_equity)

        # Check daily reset
        self._check_session_resets()

        # Clean expired restrictions
        self._clean_expired_restrictions()

        # Check all limits
        triggered_actions = self._check_all_limits()

        # Calculate current drawdown
        max_dd = ((self.current_equity - self.peak_equity) / self.peak_equity) * 100 if self.peak_equity > 0 else 0
        daily_dd = self.daily_session.pnl_pct
        weekly_dd = self.weekly_session.pnl_pct

        return {
            "current_equity": self.current_equity,
            "peak_equity": self.peak_equity,
            "max_drawdown_pct": max_dd,
            "daily_pnl_pct": daily_dd,
            "weekly_pnl_pct": weekly_dd,
            "consecutive_losses": self.consecutive_losses,
            "triggered_actions": triggered_actions,
            "active_restrictions": list(self.active_restrictions.keys()),
            "can_trade": self._can_trade(),
            "position_size_multiplier": self._get_position_size_multiplier(),
            "max_leverage_allowed": self._get_max_leverage(),
            "feature_enabled": True,
        }

    def record_trade(self, trade: Dict) -> Dict:
        """
        Record a completed trade and update consecutive loss tracking.

        Args:
            trade: Dict with at least 'pnl' and optionally 'pnl_pct' keys

        Returns:
            Updated drawdown status
        """
        if not self.feature_enabled:
            return self.update_equity(self.current_equity)

        self.trade_history.append({
            **trade,
            "timestamp": datetime.utcnow(),
            "equity_before": self.current_equity,
        })

        # Update consecutive losses
        pnl = trade.get("pnl", 0)

        if pnl < 0:
            self.consecutive_losses += 1
            logger.warning(
                "Loss recorded | Consecutive losses: %d/%d",
                self.consecutive_losses, self.consecutive_loss_limit
            )
        else:
            if self.consecutive_losses > 0:
                logger.info(
                    "Win recorded | Consecutive loss streak broken at %d",
                    self.consecutive_losses
                )
            self.consecutive_losses = 0

        # Update session trades
        self.daily_session.trades.append(trade)
        self.weekly_session.trades.append(trade)

        # Check limits after trade
        return self.update_equity(self.current_equity + pnl)

    def _update_sessions(self, new_equity: float):
        """Update session equity tracking"""
        self.daily_session.current_equity = new_equity
        self.weekly_session.current_equity = new_equity

        if new_equity > self.daily_session.peak_equity:
            self.daily_session.peak_equity = new_equity
        if new_equity < self.daily_session.low_equity:
            self.daily_session.low_equity = new_equity

        if new_equity > self.weekly_session.peak_equity:
            self.weekly_session.peak_equity = new_equity
        if new_equity < self.weekly_session.low_equity:
            self.weekly_session.low_equity = new_equity

    def _check_session_resets(self):
        """Check if daily or weekly sessions should be reset"""
        now = datetime.utcnow()

        # Daily reset at 00:00 UTC
        if now.date() > self.daily_session.start_time.date():
            logger.info("Daily session reset")
            self.daily_session = self._create_session()

            # Clear daily restriction if exists
            if "daily" in self.active_restrictions:
                del self.active_restrictions["daily"]

        # Weekly reset on Monday 00:00 UTC
        session_week = self.weekly_session.start_time.isocalendar()[1]
        current_week = now.isocalendar()[1]

        if current_week != session_week:
            logger.info("Weekly session reset")
            self.weekly_session = self._create_session()

            if "weekly" in self.active_restrictions:
                del self.active_restrictions["weekly"]

    def _check_all_limits(self) -> List[Dict]:
        """Check all drawdown limits and return triggered actions"""
        triggered = []

        for limit in self.limits:
            if limit.name in self.active_restrictions:
                continue  # Already active

            should_trigger = False
            current_dd = 0.0

            if limit.name == "consecutive_loss":
                should_trigger = self.consecutive_losses >= self.consecutive_loss_limit
            elif limit.name == "daily":
                current_dd = self.daily_session.pnl_pct
                should_trigger = current_dd <= limit.threshold_pct
            elif limit.name == "weekly":
                current_dd = self.weekly_session.pnl_pct
                should_trigger = current_dd <= limit.threshold_pct
            elif limit.name == "max":
                if self.peak_equity > 0:
                    current_dd = ((self.current_equity - self.peak_equity) / self.peak_equity) * 100
                should_trigger = current_dd <= limit.threshold_pct

            if should_trigger:
                self._activate_restriction(limit)

                action_info = {
                    "limit_name": limit.name,
                    "threshold": limit.threshold_pct,
                    "current_value": current_dd if limit.name != "consecutive_loss" else self.consecutive_losses,
                    "action": limit.action.value,
                }

                if limit.leverage_cap:
                    action_info["leverage_cap"] = limit.leverage_cap
                if limit.position_size_mult:
                    action_info["position_size_mult"] = limit.position_size_mult
                if limit.duration_hours:
                    action_info["duration_hours"] = limit.duration_hours

                triggered.append(action_info)

                logger.warning(
                    "DRAWDOWN LIMIT TRIGGERED: %s | Threshold: %.1f%% | Action: %s",
                    limit.name.upper(), limit.threshold_pct, limit.action.value
                )

        return triggered

    def _activate_restriction(self, limit: DrawdownLimit):
        """Activate a restriction"""
        expires_at = None
        if limit.duration_hours:
            expires_at = datetime.utcnow() + timedelta(hours=limit.duration_hours)

        self.active_restrictions[limit.name] = {
            "action": limit.action,
            "activated_at": datetime.utcnow(),
            "expires_at": expires_at,
            "leverage_cap": limit.leverage_cap,
            "position_size_mult": limit.position_size_mult,
        }

    def _clean_expired_restrictions(self):
        """Remove expired restrictions"""
        now = datetime.utcnow()
        expired = []

        for name, restriction in self.active_restrictions.items():
            if restriction["expires_at"] and restriction["expires_at"] <= now:
                expired.append(name)

        for name in expired:
            logger.info("Restriction expired: %s", name)
            del self.active_restrictions[name]

    def _can_trade(self) -> bool:
        """Check if trading is allowed"""
        for restriction in self.active_restrictions.values():
            if restriction["action"] in [
                DrawdownAction.PAUSE_TRADING,
                DrawdownAction.CLOSE_ALL_POSITIONS,
                DrawdownAction.FULL_LOCKOUT,
            ]:
                return False
        return True

    def _get_position_size_multiplier(self) -> float:
        """Get current position size multiplier based on restrictions"""
        multiplier = 1.0

        for restriction in self.active_restrictions.values():
            if restriction.get("position_size_mult"):
                multiplier = min(multiplier, restriction["position_size_mult"])

        # Additional reduction based on consecutive losses (even if not at limit)
        if self.consecutive_losses > 0:
            # Reduce by 15% per consecutive loss
            loss_reduction = 1.0 - (self.consecutive_losses * 0.15)
            multiplier = min(multiplier, max(0.3, loss_reduction))

        return multiplier

    def _get_max_leverage(self) -> int:
        """Get maximum allowed leverage based on restrictions"""
        max_lev = 20  # Default max

        for restriction in self.active_restrictions.values():
            if restriction.get("leverage_cap"):
                max_lev = min(max_lev, restriction["leverage_cap"])

        return max_lev

    def get_status_for_prompt(self) -> str:
        """Get human-readable status for GLM prompt"""
        if not self.feature_enabled:
            return ""

        max_dd = ((self.current_equity - self.peak_equity) / self.peak_equity) * 100 if self.peak_equity > 0 else 0

        lines = [
            "=" * 60,
            "DRAWDOWN STATUS",
            "=" * 60,
            "",
            f"Current Equity: ${self.current_equity:,.2f}",
            f"Peak Equity: ${self.peak_equity:,.2f}",
            f"Max Drawdown: {max_dd:.2f}%",
            "",
            f"Daily P&L: {self.daily_session.pnl_pct:+.2f}%",
            f"Weekly P&L: {self.weekly_session.pnl_pct:+.2f}%",
            "",
            f"Consecutive Losses: {self.consecutive_losses}/{self.consecutive_loss_limit}",
            "",
        ]

        # Show active restrictions
        if self.active_restrictions:
            lines.append("ACTIVE RESTRICTIONS:")
            for name, restriction in self.active_restrictions.items():
                action = restriction["action"].value
                expires = restriction.get("expires_at")
                expires_str = expires.strftime("%H:%M UTC") if expires else "N/A"
                lines.append(f"  - {name.upper()}: {action} (expires: {expires_str})")
            lines.append("")

        # Trading permission
        can_trade = self._can_trade()
        pos_mult = self._get_position_size_multiplier()
        max_lev = self._get_max_leverage()

        if not can_trade:
            lines.append("TRADING PAUSED - Wait for restriction to expire")
        else:
            lines.append(f"Trading Allowed | Position Size: {pos_mult:.0%} | Max Leverage: {max_lev}x")

        return "\n".join(lines)

    def reset_consecutive_losses(self):
        """Manually reset consecutive losses (e.g., after review)"""
        old = self.consecutive_losses
        self.consecutive_losses = 0
        logger.info("Consecutive losses manually reset: %d -> 0", old)

        # Remove consecutive_loss restriction if exists
        if "consecutive_loss" in self.active_restrictions:
            del self.active_restrictions["consecutive_loss"]

    def should_apply_to_trade(self, trade_created_at: datetime) -> bool:
        """
        Check if drawdown management should apply to a specific trade.
        For backward compatibility with existing positions.

        Args:
            trade_created_at: Timestamp when the trade was created

        Returns:
            True if drawdown management should apply
        """
        if not self.feature_enabled:
            return False

        # Feature release date - trades before this use old behavior
        feature_release_date = datetime(2024, 11, 26, 0, 0, 0)
        return trade_created_at >= feature_release_date

    def get_multiplier(self) -> float:
        """
        Get position size multiplier based on current drawdown state.
        Used by DynamicPositionSizer for risk-adjusted sizing.

        Returns:
            float: Multiplier between 0.25 and 1.0
        """
        if not self.feature_enabled:
            return 1.0
        return self._get_position_size_multiplier()

    def get_status(self) -> Dict:
        """Get current drawdown status - alias for get_summary"""
        return self.get_summary()

    def get_summary(self) -> Dict:
        """Get summary of current drawdown state"""
        max_dd = ((self.current_equity - self.peak_equity) / self.peak_equity) * 100 if self.peak_equity > 0 else 0

        return {
            "mode": self.mode,
            "feature_enabled": self.feature_enabled,
            "current_equity": self.current_equity,
            "peak_equity": self.peak_equity,
            "initial_equity": self.initial_equity,
            "max_drawdown_pct": max_dd,
            "daily_pnl_pct": self.daily_session.pnl_pct,
            "weekly_pnl_pct": self.weekly_session.pnl_pct,
            "consecutive_losses": self.consecutive_losses,
            "consecutive_loss_limit": self.consecutive_loss_limit,
            "can_trade": self._can_trade(),
            "position_size_multiplier": self._get_position_size_multiplier(),
            "max_leverage_allowed": self._get_max_leverage(),
            "active_restrictions": list(self.active_restrictions.keys()),
            "trades_today": len(self.daily_session.trades),
            "trades_this_week": len(self.weekly_session.trades),
        }


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test scalp mode
    dd_manager = DrawdownManager(
        initial_equity=10000,
        mode="scalp",
    )

    print("Initial status:")
    print(dd_manager.get_status_for_prompt())

    # Simulate trades
    print("\n--- Recording losing trades ---")
    dd_manager.record_trade({"pnl": -100, "pnl_pct": -1.0})
    dd_manager.record_trade({"pnl": -80, "pnl_pct": -0.8})
    dd_manager.record_trade({"pnl": -120, "pnl_pct": -1.2})

    print("\nAfter losses:")
    print(dd_manager.get_status_for_prompt())

    print("\nSummary:", dd_manager.get_summary())
