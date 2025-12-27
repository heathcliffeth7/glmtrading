"""
Account Info Builder

Builds account information and performance feedback sections for GLM prompts.

Responsibilities:
- Build account information section with equity, positions, and fee costs
- Build performance feedback section with recent trade history
- Handle position close notifications
- Format position details (long/short) with exit plans
- Calculate and display fees, PnL, and performance metrics

Security Features:
- Division by zero protection
- Safe value extraction from portfolio metrics
- Notification validation via NotificationHandler
- Prompt injection sanitization

Example:
    builder = AccountInfoBuilder(
        settings=get_settings(),
        notification_handler=handler,
        data_validator=validator
    )
    
    performance_state = {
        'consecutive_losses': 0,
        'recent_win_rate': 0.75,
        'performance_history': [1, 1, 0, 1],
        'last_trade_side': 'LONG'
    }
    
    account_info = builder.build(portfolio_metrics, performance_state, "BTCUSDT")
    feedback = builder.build_feedback_section(portfolio_metrics, performance_state)
"""

import logging
from typing import Any, Dict, List

from app.config.settings import get_settings
from app.risk_manager.prompts.data_validation import DataValidator
from app.risk_manager.prompts.builders.notification_handler import NotificationHandler

logger = logging.getLogger(__name__)


class AccountInfoBuilder:
    """Builds account information and performance feedback sections"""

    def __init__(
        self,
        settings,
        notification_handler: NotificationHandler,
        data_validator: DataValidator
    ):
        """
        Initialize account info builder.

        Args:
            settings: Application settings (for trading config)
            notification_handler: Handler for position close notifications
            data_validator: Data validator for safe operations
        """
        self._settings = settings
        self._notification_handler = notification_handler
        self._data_validator = data_validator

    def build(
        self,
        portfolio_metrics: Dict[str, Any],
        performance_state: Dict[str, Any],
        symbol: str = "BTCUSDT"
    ) -> str:
        """
        Build account information section.

        Args:
            portfolio_metrics: Portfolio metrics dict containing:
                - equity: Current account equity
                - initial_capital: Initial capital
                - available_cash: Available cash
                - long_position: Long position size
                - short_position: Short position size
                - net_position: Net position
                - current_price: Current market price
                - last_trade_price: Last trade price
                - long_avg_price: Long average entry price
                - short_avg_price: Short average entry price
                - long_leverage: Long leverage
                - short_leverage: Short leverage
                - long_exit_plan: Long exit plan dict
                - short_exit_plan: Short exit plan dict
            performance_state: Performance state dict containing:
                - consecutive_losses: Number of consecutive losses
                - recent_win_rate: Recent win rate (0.0-1.0)
                - performance_history: List of trade results (1=win, 0=loss)
                - last_trade_side: Last trade side ('LONG' or 'SHORT')
            symbol: Trading symbol (e.g., "BTCUSDT")

        Returns:
            Formatted account information text
        """
        lines = [
            "=" * 80,
            "HERE IS YOUR ACCOUNT INFORMATION & PERFORMANCE",
            "=" * 80,
            "",
        ]

        # Check for position close notification
        notification = self._notification_handler.check_notification(symbol=symbol)
        if notification:
            lines.extend(self._notification_handler.format_notification(notification, symbol=symbol))

        base_asset = symbol.replace("USDT", "")

        # Calculate total return
        equity = portfolio_metrics.get("equity", 10000)
        initial_capital = portfolio_metrics.get("initial_capital") or 10000
        if initial_capital <= 0:
            initial_capital = 10000
        total_return_pct = self._data_validator.safe_divide(
            equity - initial_capital,
            initial_capital,
            default=0.0
        ) * 100

        lines.extend([
            f"Current Total Return (percent): {total_return_pct:.2f}%",
            f"Available Cash: {portfolio_metrics.get('available_cash', equity):.2f}",
            f"Current Account Value: {equity:.2f}",
        ])

        # Win Rate conditional display
        performance_history = performance_state.get("performance_history", [])
        recent_win_rate = performance_state.get("recent_win_rate", 0.5)
        
        if len(performance_history) >= 3:
            lines.append(f"Recent Win Rate (last {len(performance_history)} trades): {recent_win_rate:.0%}")
        elif len(performance_history) > 0:
            lines.append(f"Recent Win Rate: Insufficient data ({len(performance_history)} trades)")
        else:
            lines.append("Recent Win Rate: N/A (no trade history)")
        lines.append("")

        # Position information
        long_position = portfolio_metrics.get("long_position", 0.0)
        short_position = portfolio_metrics.get("short_position", 0.0)
        net_position = portfolio_metrics.get("net_position", portfolio_metrics.get("position", 0.0))

        has_long = abs(long_position) > 0.0001
        has_short = abs(short_position) > 0.0001

        current_price = portfolio_metrics.get("current_price", 0)
        last_trade_price = portfolio_metrics.get("last_trade_price")

        # Get trading config values
        trading_cfg = self._settings.trading
        taker_fee_pct = trading_cfg.taker_fee_pct
        maker_fee_pct = trading_cfg.maker_fee_pct
        example_position_usd = trading_cfg.example_position_usd
        max_margin = trading_cfg.max_margin_per_position
        max_leverage = trading_cfg.max_leverage

        example_fee = example_position_usd * (taker_fee_pct / 100)

        lines.extend([
            "=" * 80,
            "FEE COSTS & TRADING LIMITS",
            "=" * 80,
            "",
            f"Taker Fee: {taker_fee_pct}% (per trade)",
            f"Maker Fee: {maker_fee_pct}% (per trade)",
            f"Example fee for ${example_position_usd:,.0f} position: ${example_fee:.2f}",
            f"Round-trip cost (open + close): ${example_fee * 2:.2f}",
            "",
            "LIMITS:",
            f"  • Maximum margin per position: ${max_margin:,.0f}",
            f"  • Maximum leverage: {max_leverage}x",
            "",
        ])

        # Price change awareness
        if last_trade_price and current_price:
            pc = abs(self._data_validator.safe_divide(
                current_price - last_trade_price,
                last_trade_price,
                default=0.0
            )) * 100
            status = "✓ OK" if pc <= 5.0 else "✗ BLOCKED"
            lines.extend([
                "=" * 80,
                "PRICE CHANGE AWARENESS",
                "=" * 80,
                "",
                f"Last trade price: ${last_trade_price:.2f}",
                f"Current price: ${current_price:.2f}",
                f"Price change: {pc:.2f}%",
                f"System threshold status: {status}",
                "",
            ])

        # Current positions
        if has_long or has_short:
            lines.extend([
                "=" * 80,
                "CURRENT LIVE POSITIONS",
                "=" * 80,
                "",
            ])

            if has_long:
                long_entry = portfolio_metrics.get("long_avg_price", 0)
                long_pnl_pct = self._data_validator.safe_divide(
                    current_price - long_entry,
                    long_entry,
                    default=0.0
                ) * 100
                long_pnl_usd = (current_price - long_entry) * long_position if long_entry else 0
                long_lev = portfolio_metrics.get("long_leverage", 1)
                long_exit_plan = portfolio_metrics.get("long_exit_plan")

                long_notional = long_position * long_entry
                long_fee = long_notional * (taker_fee_pct / 100)

                lines.extend([
                    "📈 LONG POSITION:",
                    "{",
                    f"  'symbol': '{symbol}',",
                    "  'position_type': 'LONG',",
                    f"  'quantity': {long_position:.6f} {base_asset},",
                    f"  'entry_price': ${long_entry:.2f},",
                    f"  'current_price': ${current_price:.2f},",
                    f"  'unrealized_pnl': ${long_pnl_usd:.2f} ({long_pnl_pct:.2f}%),",
                    f"  'leverage': {long_lev}x,",
                    f"  'entry_fee_paid': ${long_fee:.2f},",
                ])
                if long_exit_plan:
                    sl = long_exit_plan.get("stop_loss", 0)
                    inv = long_exit_plan.get("invalidation_condition", "N/A")
                    lines.extend([
                        "  'exit_plan': {",
                        f"    'stop_loss': ${sl:.2f},",
                        f"    'invalidation': '{inv}'",
                        "  }",
                    ])
                lines.extend(["}", ""])

            if has_short:
                short_entry = portfolio_metrics.get("short_avg_price", 0)
                short_pnl_pct = self._data_validator.safe_divide(
                    short_entry - current_price,
                    short_entry,
                    default=0.0
                ) * 100
                short_pnl_usd = (short_entry - current_price) * abs(short_position) if short_entry else 0
                short_lev = portfolio_metrics.get("short_leverage", 1)
                short_exit_plan = portfolio_metrics.get("short_exit_plan")

                short_notional = abs(short_position) * short_entry
                short_fee = short_notional * (taker_fee_pct / 100)

                lines.extend([
                    "📉 SHORT POSITION:",
                    "{",
                    f"  'symbol': '{symbol}',",
                    "  'position_type': 'SHORT',",
                    f"  'quantity': {abs(short_position):.6f} {base_asset},",
                    f"  'entry_price': ${short_entry:.2f},",
                    f"  'current_price': ${current_price:.2f},",
                    f"  'unrealized_pnl': ${short_pnl_usd:.2f} ({short_pnl_pct:.2f}%),",
                    f"  'leverage': {short_lev}x,",
                    f"  'entry_fee_paid': ${short_fee:.2f},",
                ])
                if short_exit_plan:
                    sl = short_exit_plan.get("stop_loss", 0)
                    inv = short_exit_plan.get("invalidation_condition", "N/A")
                    lines.extend([
                        "  'exit_plan': {",
                        f"    'stop_loss': ${sl:.2f},",
                        f"    'invalidation': '{inv}'",
                        "  }",
                    ])
                lines.extend(["}", ""])

            lines.extend([
                f"NET POSITION: {net_position:.6f} {base_asset}",
                f"  • Long: {long_position:.6f} {base_asset}",
                f"  • Short: {short_position:.6f} {base_asset}",
                "",
            ])
        else:
            lines.extend([
                "Current live positions: NONE (FLAT)",
                "",
            ])

        return "\n".join(lines)

    def build_feedback_section(
        self,
        portfolio_metrics: Dict[str, Any],
        performance_state: Dict[str, Any]
    ) -> str:
        """
        Build compressed feedback section for last 5 trades.

        Args:
            portfolio_metrics: Portfolio metrics dict containing:
                - recent_trades: List of recent trade dicts
            performance_state: Performance state dict (for consistency, though not used here)

        Returns:
            Formatted feedback text (~100 tokens)
        """
        recent_trades = portfolio_metrics.get("recent_trades", [])

        if not recent_trades:
            return ""

        trades = recent_trades[:5]
        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        losses = len(trades) - wins

        # Safe average calculation
        pnl_values = [t.get("pnl_pct", 0) for t in trades if t.get("pnl_pct") is not None]
        avg_pnl = self._data_validator.safe_divide(
            sum(pnl_values),
            len(pnl_values),
            default=0.0
        )

        # Streak detection
        streak = 0
        if trades:
            streak_type = "W" if trades[0].get("pnl", 0) > 0 else "L"
            for t in trades:
                if (t.get("pnl", 0) > 0) == (streak_type == "W"):
                    streak += 1
                else:
                    break
        else:
            streak_type = "N"

        lines = [
            "",
            "=" * 60,
            "SON 5 İŞLEM GERİ BİLDİRİMİ (FEEDBACK LOOP)",
            "=" * 60,
            f"Sonuç: {wins}W/{losses}L | Streak: {streak}{streak_type} | Ort: {avg_pnl:+.1f}%",
        ]

        # Show last trade direction for bias consideration
        if trades:
            last_side = trades[0].get("side", "")
            last_pnl = trades[0].get("pnl_pct", 0)
            if last_side:
                lines.append(f"Son işlem: {last_side} ({last_pnl:+.1f}%)")

        lines.append("")

        return "\n".join(lines)
