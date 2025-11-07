import asyncio
import contextlib
from datetime import datetime, time, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from app.agents.base import Agent, AgentSignal
from app.agents.short_term import PureDataCollector
from app.agents.multi_timeframe import MultiTimeframeAgent
from app.agents.feedback.collector import FeedbackCollector
from app.agents.feedback.retrainer import ActiveLearningRetrainer
from app.config.settings import get_settings
from app.data_feeds.service import orchestrator as data_feed_orchestrator
from app.features.orchestrator import start_feature_workers
from app.executor.executor import Executor, ExecutionResult
from app.executor.ledger import engine
from app.research.backtest import Backtester, load_historical_from_influx
from app.risk_manager.manager import RiskDecision, RiskManager
from app.utils.logging import configure_logging, get_logger
from app.utils.telegram import format_markdown, telegram_client
from app.monitoring.service_monitor import monitor_all, verify_htf_data
from sqlalchemy.orm import Session
from app.risk_manager.nof1_prompt_builder import Nof1PromptBuilder
from app.risk_manager.dynamic_risk_manager import DynamicRiskManager
from app.risk_manager.advanced_parser import AdvancedInvalidationParser
from app.monitoring.advanced_monitor import AdvancedExitMonitor
from app.monitoring.timing_monitor import CycleTimingTracker


settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


class AutomatedRunner:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "15min",
        backtest_window_minutes: int = 2880,
        cycle_seconds: int = 900,  # 15 dakika (900 saniye)
        risk_manager: Optional[RiskManager] = None,
        executor: Optional[Executor] = None,
        agent: Optional[Agent] = None,
        enable_feedback_collector: bool = True,
        enable_daily_retraining: bool = True,
        retraining_hour: int = 2,  # UTC hour for daily retraining
    ) -> None:
        self._symbol = symbol
        self._interval = interval
        self._window = backtest_window_minutes
        
        # nof1.ai style aktifse 3 dakikalık döngü kullan
        settings = get_settings()
        if settings.use_nof1_style:
            self._cycle = 180  # 3 dakika (180 saniye) - nof1.ai style için
            logger.info("nof1.ai style enabled: Using 3-minute cycle (180 seconds)")
        else:
            self._cycle = cycle_seconds
        
        self._risk_manager = risk_manager or RiskManager()
        self._executor = executor or Executor(symbol=symbol)
        # Use PureDataCollector - GLM makes ALL decisions with raw data
        self._agent = agent or PureDataCollector(symbol=symbol)
        self._running = False
        
        # Active Learning components
        self._enable_feedback = enable_feedback_collector
        self._enable_retraining = enable_daily_retraining
        self._retraining_hour = retraining_hour
        
        if self._enable_feedback:
            self._feedback_collector = FeedbackCollector(
                check_interval_minutes=30,
                result_after_minutes=60,
                batch_size=20,
                enable_glm_feedback=True,
            )
        else:
            self._feedback_collector = None
        
        if self._enable_retraining:
            self._retrainer = ActiveLearningRetrainer(
                model_path="models/derivatives.joblib",
                min_samples=50,
            )
        else:
            self._retrainer = None

        # Initialize advanced monitoring systems
        self._advanced_exit_monitor = AdvancedExitMonitor(self._symbol)
        self._dynamic_risk_manager = DynamicRiskManager()
        self._advanced_parser = AdvancedInvalidationParser()
        self._timing_tracker = CycleTimingTracker(symbol=self._symbol, interval=self._interval)
        
        # Start advanced monitoring
        self._advanced_exit_monitor.start_monitoring()
        logger.info("✅ Advanced exit monitoring started")

    async def start(self) -> None:
        cycle_minutes = self._cycle // 60
        logger.info("Starting automated runner with %dmin cycle (15min interval data, feature workers disabled)", cycle_minutes)
        logger.info("Active Learning: feedback=%s retraining=%s", self._enable_feedback, self._enable_retraining)
        
        self._running = True
        
        # Start background tasks
        tasks = []
        
        # 1. Feedback collector task
        if self._feedback_collector:
            feedback_task = asyncio.create_task(self._feedback_collector.start())
            tasks.append(("feedback_collector", feedback_task))
            logger.info("✅ Feedback Collector started")
        
        # 2. Daily retraining scheduler task
        if self._retrainer:
            retrain_task = asyncio.create_task(self._daily_retraining_scheduler())
            tasks.append(("retraining_scheduler", retrain_task))
            logger.info("✅ Retraining Scheduler started (daily at %02d:00 UTC)", self._retraining_hour)
        
        # 3. Service monitoring task (runs every 5 minutes)
        service_monitor_task = asyncio.create_task(self._service_monitoring_loop())
        tasks.append(("service_monitor", service_monitor_task))
        logger.info("✅ Service Monitor started (checking every 5 minutes)")
        
        # 4. Position Monitor task (3-minute exit plan checks)
        if settings.enable_position_monitor:
            from app.monitoring.position_monitor import PositionMonitor
            position_monitor = PositionMonitor(
                symbol=self._symbol,
                interval_seconds=settings.position_monitor_interval_seconds,
                executor=self._executor,
                enable_telegram=True
            )
            position_monitor_task = asyncio.create_task(position_monitor.start())
            tasks.append(("position_monitor", position_monitor_task))
            logger.info(
                "✅ Position Monitor started (%d-second interval checks for exit plan)",
                settings.position_monitor_interval_seconds
            )
        else:
            logger.info("Position Monitor disabled in settings")
        
        # NOTE: feature workers and data feed disabled - using TwelveData sync service instead
        # feeder = asyncio.create_task(data_feed_orchestrator())
        # feature_task = asyncio.create_task(start_feature_workers([self._symbol], ["1m", self._interval]))
        
        try:
            # Main trading cycle
            while self._running:
                await self._run_cycle()
                await asyncio.sleep(self._cycle)
        except asyncio.CancelledError:
            logger.info("Runner cancelled")
        finally:
            # Cleanup background tasks
            logger.info("Stopping background tasks...")
            
            if self._feedback_collector:
                self._feedback_collector.stop()
            
            for task_name, task in tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.info("✅ %s stopped", task_name)
                except Exception as exc:
                    logger.error("Error stopping %s: %s", task_name, exc)

    async def _run_cycle(self) -> None:
        start_time = datetime.now(timezone.utc)
        logger.info("Cycle started at %s", start_time.isoformat())
        
        # Start timing tracker
        self._timing_tracker.start_cycle()

        # Backtest is optional - don't block trading if historical data is missing
        try:
            data = load_historical_from_influx(self._symbol, self._interval, self._window)
            if data.empty:
                logger.warning("Backtest skipped: no historical data (this is OK, trading continues)")
            else:
                # Run backtest simulation if data available
                try:
                    aggregated = data.pivot(index="timestamp", columns="field", values="value").reset_index(drop=True)
                    tester = Backtester(self._agent)
                    trades = tester.run(aggregated.to_dict(orient="records"))
                    summary = tester.summary()
                    net_pnl = summary["portfolio_value"] - summary["initial_cash"]
                    logger.info("Backtest completed: PnL=%.2f", net_pnl)
                except Exception as exc:
                    logger.warning("Backtest failed: %s (continuing with trading)", exc)
        except Exception as exc:
            logger.warning("Backtest data loading failed: %s (continuing with trading)", exc)

        # Generate signal from agent (independent of backtest)
        t1 = datetime.now(timezone.utc)
        signal = self._agent.generate_signal()
        t2 = datetime.now(timezone.utc)
        self._timing_tracker.add_stage("data_collection", t1, t2)
        
        logger.info(
            "Agent signal | direction=%s | confidence=%.4f | reasoning=%s",
            signal.direction,
            signal.confidence,
            signal.reasoning,  # Show full reasoning (TwelveData indicators included)
        )
        
        # Get portfolio metrics for GLM context (BEFORE execution)
        metrics_before = self._executor.portfolio_metrics()
        
        # GÜVENLIK KONTROLÜ: Portfolio sanity check
        if abs(metrics_before.get('position', 0)) > 10.0:
            logger.error(
                "CRITICAL: Portfolio position astronomical (%.6f BTC) - running emergency fix",
                metrics_before['position']
            )
            # Emergency portfolio fix
            from app.executor.portfolio_sync import sync_portfolio_with_trades
            from app.executor.ledger import engine
            from sqlalchemy.orm import Session
            
            with Session(engine) as fix_session:
                sync_portfolio_with_trades(fix_session, self._symbol)
                fix_session.commit()
            
            logger.info("✅ Portfolio emergency sync completed")
            # Refresh metrics
            metrics_before = self._executor.portfolio_metrics()
            
            # Tekrar kontrol et - hala çok büyükse sistemi durdur
            if abs(metrics_before.get('position', 0)) > 10.0:
                logger.critical(
                    "SYSTEM HALT: Cannot fix astronomical position (%.6f BTC) - stopping trading",
                    metrics_before['position']
                )
                self._running = False
                return
        
        # Evaluate with portfolio context
        t3 = datetime.now(timezone.utc)
        decision = self._safe_evaluate([signal], metrics_before)
        t4 = datetime.now(timezone.utc)
        self._timing_tracker.add_stage("glm_evaluation", t3, t4)
        
        logger.info(
            "GLM decision | action=%s | amount=%.4f | leverage=%.2f | reasoning=%s",
            decision.action,
            decision.amount,
            decision.leverage,
            decision.reasoning[:100],
        )
        
        # Execute decision
        t5 = datetime.now(timezone.utc)
        result = self._executor.execute(decision)
        t6 = datetime.now(timezone.utc)
        self._timing_tracker.add_stage("execution", t5, t6)

        # Get UPDATED portfolio metrics AFTER execution
        metrics_after = self._executor.portfolio_metrics()

        # Tek bildirim - hem döngü hem risk kararı (UPDATED metrics kullan)
        try:
            logger.info("🔔 About to send Telegram notification for cycle complete")
            t7 = datetime.now(timezone.utc)
            self._notify_cycle_complete(decision, result, metrics_after)
            t8 = datetime.now(timezone.utc)
            self._timing_tracker.add_stage("telegram_notification", t7, t8)
            logger.info("✅ Telegram notification sent successfully")
        except Exception as exc:  # noqa: BLE001
            logger.error("Telegram cycle notify failed: %s", exc, exc_info=True)
        
        # Finish cycle timing and log breakdown
        timing_breakdown = self._timing_tracker.finish_cycle()

        # Add position to advanced monitoring
        if result.status == "EXECUTED" and result.telemetry:
            telemetry = result.telemetry
            if telemetry.get("last_action") in ["LONG", "SHORT"]:
                position_id = telemetry.get("position_id")
                if position_id:
                    # Get exit plan from decision
                    exit_plan = decision.exit_plan if hasattr(decision, 'exit_plan') else {}
                    
                    # Add to advanced monitoring
                    self._advanced_exit_monitor.add_position(
                        position_id=position_id,
                        exit_plan=exit_plan,
                        entry_price=telemetry.get("price", 0.0),
                        position_side=telemetry.get("position_side"),
                        amount=telemetry.get("amount", 0.0),
                        leverage=telemetry.get("leverage", 1.0)
                    )
                    
                    logger.info(
                        "✅ Added position to advanced monitoring: %s %s @ %.2f",
                        telemetry.get("position_side"),
                        position_id,
                        telemetry.get("price", 0.0)
                    )

        # Remove from advanced monitoring if position closed
        if result.status == "EXECUTED" and result.telemetry:
            telemetry = result.telemetry
            if telemetry.get("last_action") in ["CLOSE", "profit_target", "stop_loss", "invalidation"]:
                position_id = telemetry.get("position_id")
                if position_id:
                    self._advanced_exit_monitor.remove_position(position_id)
                    logger.info(
                        "✅ Removed position %s from advanced monitoring due to %s",
                        position_id,
                        telemetry.get("last_action")
                    )

        # Check position status and send notifications
        self._check_position_status_and_notify()
        
        # Send position summary notification
        self._send_position_summary_notification()

    def _safe_evaluate(self, signals: List[AgentSignal], portfolio_metrics: dict = None) -> RiskDecision:
        try:
            return self._risk_manager.evaluate(signals, portfolio_metrics)
        except Exception as exc:  # noqa: BLE001
            logger.error("Risk evaluation failed: %s", exc)
            return RiskDecision(
                action="HOLD", 
                amount=0.0, 
                reasoning=str(exc),
                decision_timestamp=datetime.now(timezone.utc)  # Fix: Add timestamp to prevent STALE error
            )

    def _notify_cycle_complete(
        self,
        decision: RiskDecision,
        result: ExecutionResult,
        metrics: Dict[str, float],
    ) -> None:
        """Tek Telegram bildirimi - döngü tamamlandı"""
        if not telegram_client.enabled():
            return

        starting_capital = 10000.0
        total_pnl = metrics['total_pnl']
        pnl_pct = (total_pnl / starting_capital) * 100
        
        # Get trade history
        from app.executor.ledger import Session, engine, Trade
        with Session(engine) as session:
            total_trades = session.query(Trade).filter(Trade.symbol == self._symbol).count()
            recent_trades = self._executor.get_recent_trades_with_pnl(limit=5)
            skipped_anomalies = getattr(self._executor, "_last_trade_report_stats", {}).get("skipped_anomalies", 0)
            displayed_trade_count = max(0, total_trades - skipped_anomalies)
            # Compute total open notional (unleveraged)
            try:
                open_trades = (
                    session.query(Trade)
                    .filter_by(symbol=self._symbol)
                    .filter(Trade.close_price.is_(None))
                    .all()
                )
                total_open_notional = sum(abs(t.amount) * t.price for t in open_trades)
            except Exception:
                # Fallback: approximate with position value
                total_open_notional = abs(metrics.get('position', 0.0)) * metrics.get('price', 0.0)

        # Display guardrail: cap absurd notional to a reasonable upper bound
        starting_capital = 10000.0
        if total_open_notional > starting_capital * 10:
            logger.warning(
                "Display cap applied to total_open_notional: %.2f -> %.2f",
                total_open_notional,
                starting_capital * 10,
            )
            total_open_notional = starting_capital * 10
        
        # Build message
        position = metrics['position']
        position_type = ""
        if position > 0.0001:
            position_type = "📈 LONG"
        elif position < -0.0001:
            position_type = "📉 SHORT"
        else:
            position_type = "⚪ FLAT"
        
        # Generate timestamp header like BTC_ANALYZER
        timestamp_header = f"BTC_ANALYZER, [{datetime.now().strftime('%d.%m.%Y %H:%M')}]"
        
        # Build GLM decision section with position info for CLOSE decisions
        # Format status with more details
        status_display = result.status
        if result.status == "SKIP" and hasattr(result, 'details') and result.details:
            # Extract reason from details for clearer display
            details = result.details
            if "cooldown" in details.lower():
                status_display = "SKIP_COOLDOWN"
            elif "hold" in details.lower():
                status_display = "SKIP_HOLD"
            elif "bekleme" in details.lower():
                status_display = "SKIP_FREQUENCY"
        elif result.status == "SKIPPED" and hasattr(result, 'details') and result.details:
            details = result.details
            if "stale" in details.lower():
                status_display = "SKIP_STALE"
            elif "price" in details.lower():
                status_display = "SKIP_NO_PRICE"
        elif result.status == "BLOCKED_PRICE_THRESHOLD":
            status_display = "SKIP_PRICE_CHANGE"
        
        decision_section = [
            f"Karar: {format_markdown(decision.action)}",
            f"Miktar: {decision.amount*100:.1f}% equity",
            f"Kaldıraç: {decision.leverage:.1f}x",
            f"Durum: {format_markdown(status_display)}",
        ]
        
        # Add GLM response latency if available
        # Add GLM response latency if available
        if decision.glm_response_time_ms > 0:
            latency_emoji = "🟢" if decision.glm_response_time_ms < 500 else "🟡" if decision.glm_response_time_ms < 1000 else "🔴"
            decision_section.append(f"{latency_emoji} GLM Yanıt: {decision.glm_response_time_ms:.0f}ms")
        
        if decision.action == "CLOSE":
            # Kapatılan pozisyon bilgisini telemetry'den al (execution öncesi pozisyon)
            is_close_cycle = bool(getattr(result, 'telemetry', None) and result.telemetry.get('last_action') == 'CLOSE')
            
            if is_close_cycle and result.telemetry:
                closed_position_side = result.telemetry.get('position_side', '')
                
                if closed_position_side == 'LONG':
                    position_text = "📈 Kapatılan Pozisyon: LONG"
                elif closed_position_side == 'SHORT':
                    position_text = "📉 Kapatılan Pozisyon: SHORT"
                else:
                    position_text = f"📊 Kapatılan Pozisyon: {closed_position_side or 'UNKNOWN'}"
                
                # Kapatılan pozisyon bilgisini ekle
                position_id = result.telemetry.get('position_id')
                if position_id:
                    position_text += f" (ID: {position_id})"
                
                decision_section.append(position_text)
            else:
                # Fallback: Mevcut pozisyon durumuna göre
                position = metrics.get('position', 0)
                if abs(position) < 0.0001:
                    # Pozisyon kapatıldıktan sonra FLAT olduysa, kapatılan pozisyon tipini bulamayız
                    decision_section.append("⚪ Kapatılan Pozisyon: FLAT")
                elif position > 0:
                    decision_section.append("📈 Kapatılan Pozisyon: LONG (hesaplanmış)")
                else:
                    decision_section.append("📉 Kapatılan Pozisyon: SHORT (hesaplanmış)")
        
        # Build exit plan section (profit target, stop loss, invalidation)
        # Show for BUY/SELL, or HOLD when there's an open position
        exit_plan_lines = []
        # DEBUG: Log exit plan status
        has_open_position = abs(position) > 0.0001
        should_show_exit_plan = (
            decision.exit_plan and 
            (decision.action in ["BUY", "SELL"] or (decision.action == "HOLD" and has_open_position))
        )
        
        if should_show_exit_plan:
            profit_target = decision.exit_plan.get("profit_target", 0.0)
            stop_loss = decision.exit_plan.get("stop_loss", 0.0)
            invalidation = decision.exit_plan.get("invalidation_condition", "")
            
            if profit_target and stop_loss:
                exit_plan_lines = [
                    "",
                    "*🎯 Çıkış Planı*",
                    f"💰 Kar Hedefi: ${profit_target:,.2f}",
                    f"🛑 Stop Loss: ${stop_loss:,.2f}",
                ]
                # Always show invalidation condition (even if N/A or empty)
                if invalidation and invalidation.strip() and invalidation.strip().upper() not in ["N/A", "NA", "NONE", ""]:
                    # Escape markdown characters in invalidation text
                    invalidation_escaped = format_markdown(invalidation)
                    exit_plan_lines.append(f"⚠️ Geçersiz Kılma: {invalidation_escaped}")
                else:
                    # Show default message when invalidation condition is not provided or is N/A
                    exit_plan_lines.append("⚠️ Geçersiz Kılma: Belirtilmemiş")
        
        # Get enhanced position details if available
        position_details = metrics.get('position_details', [])
        starting_cash = metrics.get('starting_cash', 10000.0)
        total_notional = metrics.get('total_notional', total_open_notional)
        margin_used = metrics.get('margin_used', 0.0)
        free_cash = metrics.get('free_cash', metrics['equity'] - margin_used)
        equity = metrics['equity']

        # Build portfolio status with enhanced capital breakdown
        portfolio_lines = [
            "*💰 Portföy Durumu*",
            f"💎 Serbest Sermaye: ${free_cash:,.2f} ({free_cash/equity*100:.1f}%)",
            f"📊 Kullanılan Margin: ${margin_used:,.2f}",
        ]

        # Add position-specific details if available
        if position_details:
            for i, pos in enumerate(position_details, 1):
                side = pos.get('position_side', '')
                leverage = pos.get('leverage', 1.0)
                margin = pos.get('margin', 0.0)
                notional = pos.get('notional', 0.0)
                amount = pos.get('amount', 0.0)
                portfolio_lines.append(
                    f"  ├─ {side}: {amount:.4f} BTC @ {leverage:.1f}x → ${notional:,.2f} (Margin: ${margin:,.2f})"
                )

        # Calculate real PnL (gross PnL is already net of fees in realized_pnl)
        # But we want to show: Gross PnL (before fees) + Total Fees + Net PnL
        total_fees = metrics.get('total_fees', 0.0)
        gross_pnl = total_pnl + total_fees  # Geriye fee'leri ekleyerek brüt PnL'i bul
        gross_pnl_pct = (gross_pnl / starting_cash) * 100
        
        portfolio_lines.extend([
            f"🏦 Toplam Equity: ${equity:,.2f}",
            f"📦 Pozisyon Değeri: ${total_notional:,.2f}",
            f"💰 Başlangıç Sermayesi: ${starting_cash:,.2f}",
            f"Pozisyon: {position_type} {abs(position):.4f} BTC",
            f"BTC Fiyat: ${metrics['price']:,.2f}",
            f"Brüt PnL: ${gross_pnl:,.2f} ({gross_pnl_pct:+.2f}%) | Fee: ${total_fees:,.2f} → Net: ${total_pnl:,.2f} ({pnl_pct:+.2f}%)",
            "",
            f"*📝 Son 5 İşlem* (Toplam: {displayed_trade_count})" + (f" | {skipped_anomalies} anomali saklandı" if skipped_anomalies else ""),
        ])
        
        # Add exit plan details to position section if open position exists
        
        # DEBUG: Check exit plan conditions for position section
        logger.info("🔍 Position Exit Plan Debug: has_open_position=%s, exit_plan=%s, position=%.4f", 
                    has_open_position, bool(decision.exit_plan), abs(position))
        if decision.exit_plan:
            logger.info("🔍 Exit Plan Contents: profit_target=%s, stop_loss=%s, invalidation=%s",
                       decision.exit_plan.get("profit_target"), 
                       decision.exit_plan.get("stop_loss"),
                       decision.exit_plan.get("invalidation_condition"))
        
        # Add exit plan details to position section if open position exists
        if has_open_position and recent_trades:
            # Find the open trade (first trade that is not closed)
            open_trade = next((t for t in recent_trades if not t['is_closed']), None)
            if open_trade:
                profit_target = open_trade.get('take_profit', 0.0)
                stop_loss = open_trade.get('stop_loss', 0.0)
                invalidation = open_trade.get('invalidation_condition', '')
                
                # DEBUG: Log found exit plan from trade
                logger.info("🔍 Exit Plan from Open Trade: profit_target=%s, stop_loss=%s, invalidation=%s",
                           profit_target, stop_loss, invalidation)
            
            if profit_target and stop_loss:
                # Insert exit plan right after "Pozisyon:" line (before BTC Fiyat)
                position_line_idx = len(portfolio_lines) - 4  # 4 lines back from current end
                portfolio_lines.insert(position_line_idx, f"  ├─ 🎯 Take Profit: ${profit_target:,.2f} | 🛑 Stop Loss: ${stop_loss:,.2f}")
                
                if invalidation and invalidation.strip() and invalidation.strip().upper() not in ["N/A", "NA", "NONE", ""]:
                    invalidation_escaped = format_markdown(invalidation)
                    portfolio_lines.insert(position_line_idx + 1, f"  └─ ⚠️ Geçersiz Kılma: {invalidation_escaped}")

        lines = [
            f"{timestamp_header}",
            f"*📊 {self._cycle // 60} Dakikalık Döngü Tamamlandı*",
            "",
            "*🎯 GLM Kararı*",
        ] + decision_section + exit_plan_lines + portfolio_lines
        
        # Debug: Log decision section and full message
        logger.info("📊 Decision section: %s", "\n".join(decision_section))
        logger.info("📨 Full Telegram message (first 1500 chars): %s", "\n".join(lines)[:1500])
        
        # Check if this is a CLOSE cycle (using telemetry)
        is_close_cycle = bool(getattr(result, 'telemetry', None) and result.telemetry.get('last_action') == 'CLOSE')
        
        # Add summary or recent trades
        if is_close_cycle and result.telemetry:
            amt = result.telemetry.get('amount', 0.0) or 0.0
            prc = result.telemetry.get('price', 0.0) or 0.0
            side = result.telemetry.get('position_side', '') or ''
            fee = result.telemetry.get('fee', 0.0) or 0.0
            notional = amt * prc
            pos_id = result.telemetry.get('position_id')
            id_text = f" (ID: {pos_id})" if pos_id else ""
            lines.append(f"1. 🔒 CLOSE {side} {amt:.4f} BTC{id_text}")
            lines.append(f"   Kapanış: ${prc:,.2f} | Notional: ${notional:,.2f} | Fee: ${fee:,.2f}")
            # Kısmi kapatma sonrası kalan pozisyon ve unrealized PnL bilgisini ekle
            rem_pos = metrics.get('position', 0.0) or 0.0
            avg_price = metrics.get('average_price', 0.0) or 0.0
            unreal = metrics.get('unrealized_pnl', 0.0) or 0.0
            
            # Calculate remaining position percentage
            original_amount = result.telemetry.get('original_amount', amt) or amt
            remaining_percentage = (abs(rem_pos) / abs(original_amount)) * 100 if original_amount != 0 else 0.0
            
            # Format remaining position info with percentage and opening price
            if abs(rem_pos) > 0.0001:
                remaining_side = "LONG" if rem_pos > 0 else "SHORT"
                lines.append(f"   Kalan: {remaining_percentage:.1f}% pozisyon → {abs(rem_pos):.4f} BTC {remaining_side} @ ${avg_price:,.2f} | ⏳ Unrealized: ${unreal:,.2f}")
            else:
                lines.append(f"   Kalan: Pozisyon tamamıyla kapatıldı")
            
            pnl_val = result.telemetry.get('pnl')
            if pnl_val is not None:
                sign = '+' if pnl_val >= 0 else ''
                lines.append(f"   ✅ Gerçekleşen PnL: {sign}${pnl_val:,.2f}")
            lines.append("   ⛔ Yeni pozisyon açılmadı (CLOSE)")
        else:
            # Add recent trades with detailed info
            if recent_trades:
                for i, trade in enumerate(recent_trades[:5], 1):
                    emoji = "🟢" if trade['pnl'] >= 0 else "🔴"
                    status = "🔒 Kapandı" if trade['is_closed'] else "🔓 Açık"
                    
                    # Format trade line with position ID
                    position_id_text = f" [{trade['position_id']}]" if trade.get('position_id') else ""
                    trade_line = f"{i}. {emoji} {trade['side']} {trade['amount']:.4f} BTC{position_id_text} {status}"
                    lines.append(trade_line)
                    
                    # Add price details - show both opening and current/closing prices
                    price_label = "Kapanış" if trade['is_closed'] else "Güncel"
                    lines.append(f"   Açılış: ${trade['open_price']:,.2f} → {price_label}: ${trade['close_price']:,.2f}")
                    
                    # Add position percentage if it's a partial close
                    if trade.get('is_closed') and trade.get('original_amount') and trade.get('amount'):
                        closed_percentage = (trade['amount'] / trade['original_amount']) * 100
                        if closed_percentage < 100.0:
                            lines.append(f"   Kapatılan: %{closed_percentage:.1f} pozisyon")
                    
                    # Add PnL with bps fallback
                    # NOT: trade['pnl'] zaten NET PnL'dir (fee'ler düşülmüş)
                    net_pnl = trade['pnl']
                    pnl_sign = "+" if net_pnl >= 0 else ""
                    pct = float(trade.get('pnl_pct', 0.0) or 0.0)
                    if abs(pct) < 0.01:
                        bps = abs(pct) * 100.0
                        pct_text = f"({('-' if pct<0 else '+')}{bps:.1f} bp)"
                    else:
                        pct_text = f"({pnl_sign}{pct:.2f}%)"
                    
                    # Brüt PnL'i hesapla (net + fees)
                    fee_amount = float(trade.get('fee', 0.0) or 0.0)
                    gross_pnl = net_pnl + fee_amount
                    gross_pnl_sign = "+" if gross_pnl >= 0 else ""
                    
                    fee_text = ""
                    if fee_amount > 0:
                        fee_text = f" | Fee: ${fee_amount:.2f}"
                    
                    # Show gross PnL, fees, and net PnL
                    lines.append(f"   Brüt PnL: {gross_pnl_sign}${gross_pnl:,.2f} {pct_text}{fee_text} → Net: {pnl_sign}${net_pnl:.2f}")
                    
                    # Add stop loss and take profit for open positions
                    if not trade['is_closed']:
                        stop_loss = trade.get('stop_loss')
                        take_profit = trade.get('take_profit')
                        if stop_loss or take_profit:
                            exit_info = []
                            if take_profit:
                                exit_info.append(f"🎯 Take Profit: ${take_profit:,.2f}")
                            if stop_loss:
                                exit_info.append(f"🛑 Stop Loss: ${stop_loss:,.2f}")
                            if exit_info:
                                lines.append(f"   {' | '.join(exit_info)}")
        
        # Add reasoning header
        # CLOSE, LONG, SHORT durumunda veya PAPER durumunda Son İşlem Detayları göster
        is_close_cycle = bool(getattr(result, 'telemetry', None) and result.telemetry.get('last_action') == 'CLOSE')
        is_position_cycle = bool(getattr(result, 'telemetry', None) and result.telemetry.get('last_action') in ['CLOSE', 'LONG', 'SHORT'])
        
        if (decision.action in ["CLOSE", "LONG", "SHORT"] and is_position_cycle) or (hasattr(result, 'status') and result.status == "PAPER"):
            lines.extend([
                "",
                "*🚨 Son İşlem Detayları*",
            ])
            
            # Prefer executor telemetry for position operations to avoid confusing per-trade listing
            if is_position_cycle and result.telemetry:
                amt = result.telemetry.get('amount', 0.0) or 0.0
                prc = result.telemetry.get('price', 0.0) or 0.0
                side = result.telemetry.get('position_side', '') or ''
                fee = result.telemetry.get('fee', 0.0) or 0.0
                pnl_val = result.telemetry.get('pnl', None)
                last_action = result.telemetry.get('last_action', '')
                notional_value = amt * prc
                
                # Format operation details based on action type
                if last_action == 'CLOSE':
                    operation_text = f"🔒 Pozisyon Kapatıldı: {side} {amt:.4f} BTC @ ${prc:,.2f}"
                elif last_action in ['LONG', 'SHORT']:
                    operation_emoji = "📈" if last_action == 'LONG' else "📉"
                    operation_text = f"{operation_emoji} Pozisyon Eklendi: {last_action} {amt:.4f} BTC @ ${prc:,.2f}"
                    
                    # Add current position info after addition
                    current_position = metrics.get('position', 0.0) or 0.0
                    avg_price = metrics.get('average_price', 0.0) or 0.0
                    if abs(current_position) > 0.0001:
                        current_side = "LONG" if current_position > 0 else "SHORT"
                        lines.append(operation_text)
                        lines.append(f"   📊 Güncel Pozisyon: {abs(current_position):.4f} BTC {current_side} @ ${avg_price:,.2f}")
                        operation_text = None  # Skip adding to lines again
                else:
                    operation_text = f"🔄 İşlem: {side} {amt:.4f} BTC @ ${prc:,.2f}"
                
                if operation_text:  # Only add if not already handled above
                    lines.extend([
                        operation_text,
                        f"💰 Notional Değeri: ${notional_value:,.2f}",
                    ])
                elif last_action not in ['LONG', 'SHORT']:  # Add notional for other cases
                    lines.append(f"💰 Notional Değeri: ${notional_value:,.2f}")
                if fee:
                    lines.append(f"💸 İşlem Ücreti: ${fee:.2f}")
                if pnl_val is not None:
                    sign = "+" if pnl_val >= 0 else ""
                    lines.append(f"✅ Net PnL: {sign}${pnl_val:,.2f}")
            else:
                # Fallback to latest trade when not a position cycle
                if recent_trades:
                    latest_trade = recent_trades[0]
                    price_for_notional = (
                        latest_trade['close_price'] if latest_trade['is_closed'] else latest_trade['open_price']
                    )
                    side_emoji = "📈" if latest_trade['side'] == "BUY" else "📉"
                    notional_value = latest_trade['amount'] * price_for_notional
                    pnl_sign = "+" if latest_trade['pnl'] >= 0 else ""
                    lines.extend([
                        f"{side_emoji} En Son İşlem: {latest_trade['side']} {latest_trade['amount']:.4f} BTC @ ${price_for_notional:,.2f}",
                        f"💰 Notional Değeri: ${notional_value:,.2f}",
                    ])
                    if latest_trade['is_closed']:
                        fee = latest_trade.get('fee', 0)
                        gross_pnl = latest_trade['pnl'] + fee if fee > 0 else latest_trade['pnl']
                        gross_sign = "+" if gross_pnl >= 0 else ""
                        lines.extend([f"🔒 Kapanış: ${latest_trade['close_price']:,.2f}"])
                        if fee > 0:
                            pct = float(latest_trade.get('pnl_pct', 0.0) or 0.0)
                            if abs(pct) < 0.01:
                                pct_txt = f"{('-' if pct<0 else '+')}{abs(pct)*100:.1f} bp"
                            else:
                                pct_txt = f"{pnl_sign}{pct:.2f}%"
                            lines.extend([
                                f"💰 Gross PnL: {gross_sign}${gross_pnl:,.2f}",
                                f"💸 İşlem Ücreti: ${fee:.2f}",
                                f"✅ Net PnL: {pnl_sign}${latest_trade['pnl']:,.2f} ({pct_txt})",
                            ])
                        else:
                            pct = float(latest_trade.get('pnl_pct', 0.0) or 0.0)
                            if abs(pct) < 0.01:
                                pct_txt = f"{('-' if pct<0 else '+')}{abs(pct)*100:.1f} bp"
                            else:
                                pct_txt = f"{pnl_sign}{pct:.2f}%"
                            lines.append(f"✅ Gerçekleşen PnL: {pnl_sign}${latest_trade['pnl']:,.2f} ({pct_txt})")
                    else:
                        fee = latest_trade.get('fee', 0)
                        lines.extend([
                            f"{'🟢' if latest_trade['pnl'] >= 0 else '🔴'} Anlık PnL: {pnl_sign}${latest_trade['pnl']:,.2f} ({pnl_sign}{latest_trade['pnl_pct']:.2f}%)",
                            f"📊 Güncel Fiyat: ${latest_trade['close_price']:,.2f}",
                        ])
                        if fee > 0:
                            lines.append(f"💸 Açılış Ücreti: ${fee:.2f}")
        
        lines.extend([
            "",
            "*💬 Gerekçe*",
        ])
        
        # Build main message
        main_message = "\n".join(lines)
        
        # Escape markdown for reasoning
        reasoning_escaped = decision.reasoning.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
        
        # Telegram has 4096 char limit - ilk mesajda mümkün olduğunca uzun gönder, kalanı parçalara böl
        try:
            available_space = 4096 - len(main_message) - 10  # 10 chars buffer
            
            if len(reasoning_escaped) <= available_space:
                # Fits in one message - tamamını gönder
                full_message = main_message + "\n" + reasoning_escaped
                telegram_client.send_message(full_message)
                logger.info("Telegram cycle notification sent: action=%s trades=%d", decision.action, total_trades)
            else:
                # İlk mesaj: Ana mesaj + mümkün olduğunca uzun gerekçe (4096 karaktere kadar)
                first_reasoning = reasoning_escaped[:available_space]
                first_message = main_message + "\n" + first_reasoning
                telegram_client.send_message(first_message)
                
                # Kalan gerekçe kısmını parçalara böl ve gönder
                remaining_reasoning = reasoning_escaped[available_space:]
                chunk_size = 4000  # Her parça için güvenli limit
                
                part_count = 1
                for i in range(0, len(remaining_reasoning), chunk_size):
                    chunk = remaining_reasoning[i:i+chunk_size]
                    chunk_message = f"*💬 Gerekçe (devam {part_count})*\n{chunk}"
                    telegram_client.send_message(chunk_message)
                    part_count += 1
                
                total_parts = 1 + part_count - 1
                logger.info("Telegram cycle notification sent in %d parts (total %d chars): action=%s trades=%d", 
                           total_parts, len(main_message) + len(reasoning_escaped), decision.action, total_trades)
        except Exception as exc:
            logger.error("Telegram cycle notify failed: %s", exc)
    
    async def _daily_retraining_scheduler(self) -> None:
        """
        Daily retraining scheduler
        Runs retraining at specified UTC hour every day
        """
        logger.info("Daily retraining scheduler started")
        
        while self._running:
            try:
                # Calculate next run time
                now = datetime.utcnow()
                target_time = datetime.combine(
                    now.date(),
                    time(hour=self._retraining_hour, minute=0, second=0)
                )
                
                # If target time has passed today, schedule for tomorrow
                if target_time <= now:
                    target_time += timedelta(days=1)
                
                # Sleep until target time
                sleep_seconds = (target_time - now).total_seconds()
                logger.info(
                    "Next retraining scheduled at %s UTC (in %.1f hours)",
                    target_time.isoformat(),
                    sleep_seconds / 3600,
                )
                
                await asyncio.sleep(sleep_seconds)
                
                # Run retraining
                if not self._running:
                    break
                
                logger.info("🔄 Starting daily retraining...")
                
                try:
                    result = self._retrainer.retrain(days=7, dry_run=False)
                    
                    if result.get("success"):
                        logger.info(
                            "✅ Daily retraining complete: accuracy improved by %+.2f%%",
                            result["improvement"] * 100,
                        )
                    else:
                        logger.warning(
                            "⚠️ Daily retraining did not improve model: %s",
                            result.get("error", "No improvement"),
                        )
                
                except Exception as exc:
                    logger.error("❌ Daily retraining failed: %s", exc, exc_info=True)
                    
                    # Notify Telegram about failure
                    if telegram_client.enabled():
                        try:
                            message = "\n".join([
                                "*❌ Daily Retraining Failed*",
                                "",
                                f"Error: {format_markdown(str(exc))}",
                                f"Time: {datetime.utcnow().isoformat()} UTC",
                                "",
                                "⚠️ Model was not updated. Check logs for details.",
                            ])
                            telegram_client.send_message(message)
                        except Exception:
                            pass
            
            except asyncio.CancelledError:
                logger.info("Retraining scheduler cancelled")
                raise
            except Exception as exc:
                logger.error("Retraining scheduler error: %s", exc, exc_info=True)
                # Wait before retry
                await asyncio.sleep(3600)  # Retry in 1 hour
    
    async def _service_monitoring_loop(self) -> None:
        """Periyodik olarak servisleri ve veri tazeliğini izle (her 5 dakikada bir)"""
        logger.info("Service monitoring loop started")
        
        while self._running:
            try:
                # Her 5 dakikada bir kontrol et
                await asyncio.sleep(300)  # 5 dakika = 300 saniye
                
                if not self._running:
                    break
                
                logger.debug("Running service monitoring check...")
                
                # Tüm servisleri ve HTF verilerini izle
                results = monitor_all(self._symbol)
                
                # Özet bilgi logla
                summary = results.get("summary", {})
                active_count = summary.get("active_services", 0)
                total_services = summary.get("total_services", 0)
                
                if active_count < total_services:
                    logger.warning(
                        "⚠️ Service monitoring: %d/%d services active",
                        active_count,
                        total_services,
                    )
                else:
                    logger.debug("✅ All services active (%d/%d)", active_count, total_services)
                
                # HTF verisi kontrolü
                htf_results = results.get("htf_verification", {})
                if not htf_results.get("enriched_15min_structure_valid", False):
                    logger.warning("⚠️ HTF 15min data not usable - check enriched_15min measurement")
                if not htf_results.get("longterm_4h_usable", False):
                    logger.warning("⚠️ HTF 4h data not usable - check enriched_4h measurement")
                
            except asyncio.CancelledError:
                logger.info("Service monitoring loop cancelled")
                raise
            except Exception as exc:
                logger.error("Service monitoring error: %s", exc, exc_info=True)
                # Hata durumunda kısa bir bekleme sonra devam et
                await asyncio.sleep(60)

    def _check_position_status_and_notify(self) -> None:
        """Pozisyon durumunu kontrol eder ve bildirim gönderir"""
        try:
            # Get current position status from advanced monitor
            active_positions = self._advanced_exit_monitor.active_positions
            
            if not active_positions:
                logger.debug("No active positions to check")
                return
            
            # Get current price
            current_price = self._advanced_exit_monitor._get_current_price()
            if current_price <= 0:
                logger.warning("Invalid current price for position status check")
                return
            
            # Check each position
            for position_id, position_data in active_positions.items():
                try:
                    exit_plan = position_data.get("exit_plan", {})
                    entry_price = position_data.get("entry_price", 0.0)
                    position_side = position_data.get("position_side", "")
                    
                    if not exit_plan or entry_price <= 0:
                        continue
                    
                    # Calculate current PnL
                    if position_side == "LONG":
                        pnl_pct = (current_price - entry_price) / entry_price * 100
                        pnl_amount = (current_price - entry_price) * position_data.get("amount", 0.0)
                    else:  # SHORT
                        pnl_pct = (entry_price - current_price) / entry_price * 100
                        pnl_amount = (entry_price - current_price) * position_data.get("amount", 0.0)
                    
                    # Check if position is in danger zone
                    profit_target = exit_plan.get("profit_target", 0.0)
                    stop_loss = exit_plan.get("stop_loss", 0.0)
                    
                    danger_zone = False
                    warning_message = ""
                    
                    if position_side == "LONG":
                        # LONG: Check if price is approaching stop loss
                        if stop_loss > 0 and current_price <= stop_loss * 1.02:  # Within 2% of stop loss
                            danger_zone = True
                            warning_message = f"⚠️ LONG position approaching stop loss: ${current_price:.2f} (SL: ${stop_loss:.2f})"
                        
                        # Check if price is near profit target
                        elif profit_target > 0 and current_price >= profit_target * 0.98:  # Within 2% of profit target
                            danger_zone = True
                            warning_message = f"🎯 LONG position near profit target: ${current_price:.2f} (PT: ${profit_target:.2f})"
                    
                    else:  # SHORT
                        # SHORT: Check if price is approaching stop loss
                        if stop_loss > 0 and current_price >= stop_loss * 0.98:  # Within 2% of stop loss
                            danger_zone = True
                            warning_message = f"⚠️ SHORT position approaching stop loss: ${current_price:.2f} (SL: ${stop_loss:.2f})"
                        
                        # Check if price is near profit target
                        elif profit_target > 0 and current_price <= profit_target * 1.02:  # Within 2% of profit target
                            danger_zone = True
                            warning_message = f"🎯 SHORT position near profit target: ${current_price:.2f} (PT: ${profit_target:.2f})"
                    
                    # Send warning if in danger zone
                    if danger_zone and warning_message:
                        # Check cooldown to avoid spam
                        last_warning_key = f"{position_id}_danger_warning"
                        current_time = datetime.utcnow()
                        
                        # Get last warning time from position data
                        last_warning_time = position_data.get("last_danger_warning_time")
                        
                        if (not last_warning_time or 
                            (current_time - last_warning_time).total_seconds() > 300):  # 5 minutes cooldown
                            
                            # Send warning notification
                            try:
                                from app.utils.telegram import telegram_client
                                
                                message = f"""
🚨 *POSITION DANGER WARNING*

{position_data.get('position_side', 'UNKNOWN')} Position {position_id}
{warning_message}

📊 **Position Details:**
• Entry Price: ${entry_price:.2f}
• Current Price: ${current_price:.2f}
• PnL: {pnl_pct:+.2f}% (${pnl_amount:+.2f})
• Amount: {position_data.get('amount', 0.0):.6f} BTC

🎯 **Exit Plan:**
• Profit Target: ${profit_target:.2f}
• Stop Loss: ${stop_loss:.2f}
• Invalidation: {exit_plan.get('invalidation_condition', 'N/A')}

⏰ *{current_time.strftime('%H:%M:%S')}*

💡 *Action Required:*
• Monitor closely for exit conditions
• Consider manual intervention if needed
• System will auto-close when conditions are met
"""
                                
                                telegram_client.send_message(message)
                                logger.warning(f"Position danger warning sent for {position_id}")
                                
                                # Update last warning time
                                position_data["last_danger_warning_time"] = current_time
                                
                            except Exception as exc:
                                logger.error(f"Failed to send position danger warning: {exc}")
                    
                    # Log position status
                    logger.info(
                        "Position Status Check | %s | %s | Entry: %.2f | Current: %.2f | PnL: %+.2f%% | PT: %.2f | SL: %.2f",
                        position_id,
                        position_side,
                        entry_price,
                        current_price,
                        pnl_pct,
                        profit_target,
                        stop_loss
                    )
                    
                except Exception as exc:
                    logger.error(f"Error checking position {position_id}: {exc}")
                    
        except Exception as exc:
            logger.error(f"Error in position status check: {exc}")
    
    def _send_position_summary_notification(self) -> None:
        """Pozisyon özet bildirimi gönderir"""
        try:
            active_positions = self._advanced_exit_monitor.active_positions
            
            if not active_positions:
                return
            
            current_price = self._advanced_exit_monitor._get_current_price()
            if current_price <= 0:
                return
            
            # Calculate total exposure
            total_exposure = 0.0
            total_unrealized = 0.0
            position_count = len(active_positions)
            
            for position_id, position_data in active_positions.items():
                amount = position_data.get("amount", 0.0)
                entry_price = position_data.get("entry_price", 0.0)
                position_side = position_data.get("position_side", "")
                
                if position_side == "LONG":
                    pnl = (current_price - entry_price) * amount
                else:  # SHORT
                    pnl = (entry_price - current_price) * amount
                
                total_exposure += abs(amount * current_price)
                total_unrealized += pnl
            
            # Send summary notification
            try:
                from app.utils.telegram import telegram_client
                
                message = f"""
📊 *POSITION SUMMARY UPDATE*

📈 **Active Positions:** {position_count}
💰 **Total Exposure:** ${total_exposure:,.2f}
📊 **Total Unrealized PnL:** ${total_unrealized:+.2f}

🔍 **Position Details:**
"""
                
                for position_id, position_data in active_positions.items():
                    amount = position_data.get("amount", 0.0)
                    entry_price = position_data.get("entry_price", 0.0)
                    position_side = position_data.get("position_side", "")
                    exit_plan = position_data.get("exit_plan", {})
                    
                    if position_side == "LONG":
                        pnl = (current_price - entry_price) * amount
                        pnl_pct = (current_price - entry_price) / entry_price * 100
                    else:  # SHORT
                        pnl = (entry_price - current_price) * amount
                        pnl_pct = (entry_price - current_price) / entry_price * 100
                    
                    message += f"""
• {position_side} {position_id}: {amount:.6f} BTC @ ${entry_price:.2f}
  PnL: {pnl_pct:+.2f}% (${pnl:+.2f})
  PT: ${exit_plan.get('profit_target', 0.0):.2f}
  SL: ${exit_plan.get('stop_loss', 0.0):.2f}
"""
                
                message += f"""

⏰ *{datetime.utcnow().strftime('%H:%M:%S')}*

💡 *System Status:*
• Advanced monitoring: ✅ Active
• Exit plan validation: ✅ Active
• Auto-close: ✅ Ready
"""
                
                telegram_client.send_message(message)
                logger.info("Position summary notification sent")
                
            except Exception as exc:
                logger.error(f"Failed to send position summary notification: {exc}")
                
        except Exception as exc:
            logger.error(f"Error in position summary notification: {exc}")


async def run_automated(symbol: str = "BTCUSDT", interval: str = "5min") -> None:
    runner = AutomatedRunner(symbol=symbol, interval=interval)
    await runner.start()


def main() -> None:
    try:
        asyncio.run(run_automated())
    except KeyboardInterrupt:
        logger.info("Automated runner stopped by user")


if __name__ == "__main__":
    main()
