import asyncio
import contextlib
import fcntl
import os
import sys
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
from app.risk_manager.glm_client import GLMClient
from app.utils.logging import configure_logging, get_logger
from app.utils.telegram import format_markdown, telegram_client
from app.monitoring.service_monitor import monitor_all, verify_htf_data
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.risk_manager.nof1_prompt_builder import Nof1PromptBuilder
from app.risk_manager.dynamic_risk_manager import DynamicRiskManager
from app.risk_manager.advanced_parser import AdvancedInvalidationParser
from app.monitoring.advanced_monitor import AdvancedExitMonitor
from app.monitoring.timing_monitor import CycleTimingTracker
from app.monitoring.crash_protection import (
    CrashProtectionManager,
    CrashAction,
    CrashEvent,
)


settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


class AutomatedRunner:
    def __init__(
        self,
        symbols: Optional[List[str]] = None,  # New argument for multiple symbols
        symbol: str = "BTCUSDT",  # Keep for backward compatibility
        interval: str = "15min",
        backtest_window_minutes: int = 2880,
        cycle_seconds: int = 900,  # 15 dakika (900 saniye)
        risk_manager: Optional[RiskManager] = None,
        executor: Optional[Executor] = None,
        agent: Optional[Agent] = None,
        enable_feedback_collector: bool = True,
        enable_daily_retraining: bool = True,
        retraining_hour: int = 2,  # UTC hour for daily retraining
        execution_concurrency: Optional[int] = None,
    ) -> None:
        # Handle symbols list
        if symbols:
            self._symbols = symbols
        else:
            self._symbols = [symbol]
            
        self._interval = interval
        self._window = backtest_window_minutes
        
        # nof1.ai style aktifse 3 dakikalık döngü kullan
        settings = get_settings()
        if settings.use_nof1_style:
            self._cycle = 180  # 3 dakika (180 saniye) - nof1.ai style için
            logger.info("nof1.ai style enabled: Using 3-minute cycle (180 seconds)")
        else:
            self._cycle = cycle_seconds
        
        # Execution concurrency guard (limits concurrent DB writes if needed)
        self._execution_concurrency = max(1, execution_concurrency or len(self._symbols))
        self._execution_semaphore = asyncio.Semaphore(self._execution_concurrency)
        logger.info("Execution concurrency limit set to %d", self._execution_concurrency)
        
        # Initialize components for each symbol
        self._risk_managers = {}
        self._executors = {}
        self._agents = {}
        self._glm_clients = {}

        # API key mapping for parallel GLM calls - each symbol gets its own key
        api_key_map = {
            "BTCUSDT": settings.zai.api_key,
            "ETHUSDT": settings.zai.api_key_2,
            "SOLUSDT": settings.zai.api_key_3,
        }

        for sym in self._symbols:
            # Create GLM client with symbol-specific API key for parallel processing
            api_key = api_key_map.get(sym, settings.zai.api_key)
            self._glm_clients[sym] = GLMClient(api_key=api_key)
            logger.info("🔑 Created GLMClient for %s with key: %s...", sym, api_key[:8])

            # Use passed components if single symbol and matches, otherwise create new
            if sym == symbol and risk_manager:
                self._risk_managers[sym] = risk_manager
            else:
                self._risk_managers[sym] = RiskManager(glm_client=self._glm_clients[sym])
                
            if sym == symbol and executor:
                self._executors[sym] = executor
            else:
                self._executors[sym] = Executor(symbol=sym)
                
            if sym == symbol and agent:
                self._agents[sym] = agent
            else:
                self._agents[sym] = PureDataCollector(symbol=sym)

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

        # Initialize advanced monitoring systems (per symbol if needed, but currently shared or primary)
        # For now, we'll monitor the primary symbol or all? 
        # AdvancedExitMonitor seems to take a single symbol. We might need one per symbol.
        self._advanced_exit_monitors = {sym: AdvancedExitMonitor(sym) for sym in self._symbols}
        self._dynamic_risk_manager = DynamicRiskManager()
        self._advanced_parser = AdvancedInvalidationParser()
        self._timing_trackers = {sym: CycleTimingTracker(symbol=sym, interval=self._interval) for sym in self._symbols}
        
        # Start advanced monitoring for all symbols
        for monitor in self._advanced_exit_monitors.values():
            monitor.start_monitoring()
        logger.info("✅ Advanced exit monitoring started for all symbols")

        # Initialize Crash Protection Manager for swing trading safety
        if settings.crash_protection_enabled:
            self._crash_protection_manager = CrashProtectionManager(
                symbols=self._symbols,
                close_position_callback=self._on_crash_protection_trigger,
                htf_data_getter=self._get_htf_data_for_crash,
            )
            logger.info("🛡️ Crash Protection Manager initialized for swing trading")
        else:
            self._crash_protection_manager = None
            logger.info("⚠️ Crash Protection DISABLED in settings")

    def _default_metrics(self) -> dict:
        """Safe fallback metrics to keep pipeline moving when DB access fails."""
        return {
            "price": 0.0,
            "position": 0.0,
            "average_price": 0.0,
            "exposure": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
            "equity": 10000.0,
            "margin_used": 0.0,
            "free_cash": 10000.0,
            "total_notional": 0.0,
            "position_details": [],
            "starting_cash": 10000.0,
        }

    def _safe_portfolio_metrics(self, symbol: str, executor: Executor, fallback: Optional[dict] = None) -> dict:
        try:
            return executor.portfolio_metrics()
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] Portfolio metrics fetch failed: %s", symbol, exc, exc_info=True)
            return fallback or self._default_metrics()

    def _build_failed_eval_result(self, symbol: str, exc: Exception) -> dict:
        """Create a safe evaluation result so Phase 2 can notify even after a crash."""
        logger.error("[%s] Evaluation crashed - using HOLD fallback: %s", symbol, exc, exc_info=True)
        now = datetime.now(timezone.utc)
        tracker = self._timing_trackers.get(symbol) or CycleTimingTracker(symbol=symbol, interval=self._interval)
        # Avoid resetting an already-started tracker; only start if not running
        if getattr(tracker, "_cycle_start", None) is None:
            tracker.start_cycle()
        tracker.add_stage("evaluation_error", now, now)

        fallback_signal = AgentSignal(
            direction="HOLD",
            confidence=0.0,
            reasoning=f"Evaluation failed: {exc}",
            timestamp=now.isoformat(),
            metadata={},
        )
        fallback_decision = RiskDecision(
            action="HOLD",
            amount=0.0,
            leverage=1.0,
            reasoning=f"Evaluation failed: {exc}",
            decision_timestamp=now,
        )

        return {
            "symbol": symbol,
            "signal": fallback_signal,
            "decision": fallback_decision,
            "metrics_before": self._default_metrics(),
            "backtest_data": None,
            "timing_tracker": tracker,
            "evaluation_error": str(exc),
        }

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

            for sym in self._symbols:
                # Get crash handler for this symbol (if crash protection is enabled)
                crash_handler = None
                if self._crash_protection_manager:
                    crash_handler = self._crash_protection_manager.get_handler(sym)

                position_monitor = PositionMonitor(
                    symbol=sym,
                    interval_seconds=settings.position_monitor_interval_seconds,
                    executor=self._executors[sym],
                    enable_telegram=True,
                    crash_handler=crash_handler,  # Pass crash handler for state sync
                )
                # Create unique task name
                task_name = f"position_monitor_{sym}"
                position_monitor_task = asyncio.create_task(position_monitor.start())
                tasks.append((task_name, position_monitor_task))
                logger.info(
                    "✅ Position Monitor started for %s (%d-second interval checks for exit plan)",
                    sym,
                    settings.position_monitor_interval_seconds
                )
        else:
            logger.info("Position Monitor disabled in settings")
        
        # NOTE: feature workers and data feed disabled
        # feeder = asyncio.create_task(data_feed_orchestrator())
        # feature_task = asyncio.create_task(start_feature_workers(self._symbols, ["1m", self._interval]))

        # 5. Crash Protection registration with price_cache
        if self._crash_protection_manager:
            self._crash_protection_manager.register_with_price_cache()
            logger.info("🛡️ Crash Protection registered with price_cache for real-time monitoring")
        
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

    async def _evaluate_and_execute_symbol(self, symbol: str) -> dict:
        """
        Execute-As-You-Go Pattern: Evaluate GLM and immediately execute for a single symbol.

        This prevents STALE decisions by executing right after GLM returns,
        instead of waiting for all symbols' GLM calls to complete.

        Returns:
            dict with 'success', 'error' (if any), 'duration'
        """
        start_time = datetime.now(timezone.utc)
        logger.info("[%s] 🤖 Starting GLM evaluation...", symbol)

        try:
            # Phase 1: Evaluate
            eval_result = await self._evaluate_symbol(symbol)
            eval_duration = (datetime.now(timezone.utc) - start_time).total_seconds()

            if isinstance(eval_result, Exception):
                logger.warning(
                    "[%s] ⚠️ Evaluation failed in %.1fs → continuing with HOLD fallback",
                    symbol, eval_duration
                )
                eval_result = self._build_failed_eval_result(symbol, eval_result)
            else:
                logger.info("[%s] ⏱️ Evaluation finished in %.1fs → IMMEDIATELY executing", symbol, eval_duration)

            # Phase 2: Execute IMMEDIATELY (no waiting for other symbols)
            await self._execute_with_guard(symbol, eval_result)

            total_duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.info("[%s] ✅ Evaluation+Execution completed in %.1fs", symbol, total_duration)
            return {'success': True, 'duration': total_duration, 'symbol': symbol}

        except Exception as exc:
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.error("[%s] ❌ Evaluation+Execution failed in %.1fs: %s", symbol, duration, exc, exc_info=True)
            return {'success': False, 'error': str(exc), 'duration': duration, 'symbol': symbol}

    async def _run_cycle(self) -> None:
        """
        Execute-As-You-Go Cycle: Each symbol evaluates and executes independently.

        Key difference from old approach:
        - OLD: Wait for ALL GLM evaluations → Then execute ALL
        - NEW: Each symbol executes IMMEDIATELY after its GLM returns

        This prevents STALE decisions caused by waiting for slow symbols.
        """
        start_time = datetime.now(timezone.utc)
        logger.info("🚀 Cycle started at %s for symbols: %s", start_time.isoformat(), self._symbols)
        logger.info("📊 Execute-As-You-Go pattern: Each symbol executes immediately after GLM returns")

        # Launch all symbols in parallel - each handles its own execution
        tasks = [
            asyncio.create_task(
                self._evaluate_and_execute_symbol(symbol),
                name=f"eval_exec:{symbol}"
            )
            for symbol in self._symbols
        ]

        # Wait for all to complete (but each has already executed individually)
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log summary
        cycle_duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        successes = sum(1 for r in results if isinstance(r, dict) and r.get('success'))
        failures = len(results) - successes

        logger.info(
            "⏱️ Cycle completed in %.1fs | success=%d/%d | failures=%d",
            cycle_duration, successes, len(self._symbols), failures
        )

        # Log any failures
        for result in results:
            if isinstance(result, Exception):
                logger.error("Cycle task exception: %s", result, exc_info=result)
            elif isinstance(result, dict) and not result.get('success'):
                logger.warning("[%s] Failed: %s", result.get('symbol', '?'), result.get('error', 'unknown'))

    async def _evaluate_symbol(self, symbol: str):
        """
        Phase 1: Veri toplama ve GLM değerlendirme (paralel çalışabilir)
        
        Returns:
            Dict containing: signal, decision, metrics_before, backtest_data, timing_data
        """
        # Get components for this symbol
        agent = self._agents[symbol]
        executor = self._executors[symbol]
        risk_manager = self._risk_managers[symbol]
        timing_tracker = self._timing_trackers[symbol]

        # Start timing tracker
        timing_tracker.start_cycle()

        # Check global cooldown from crash protection
        if self._crash_protection_manager and self._crash_protection_manager.is_in_global_cooldown():
            logger.warning(
                "[%s] 🚫 GLOBAL COOLDOWN ACTIVE - No new positions allowed after crash protection",
                symbol
            )
            # Return HOLD decision during cooldown
            now = datetime.now(timezone.utc)
            cooldown_signal = AgentSignal(
                direction="HOLD",
                confidence=0.0,
                reasoning="🚫 Global cooldown active after crash protection",
                timestamp=now.isoformat(),
                metadata={},
            )
            cooldown_decision = RiskDecision(
                action="HOLD",
                amount=0.0,
                leverage=1.0,
                reasoning="🚫 Global cooldown active - no new positions until cooldown expires",
                decision_timestamp=now,
            )
            timing_tracker.add_stage("cooldown_skip", now, now)
            return {
                'symbol': symbol,
                'signal': cooldown_signal,
                'decision': cooldown_decision,
                'metrics_before': self._safe_portfolio_metrics(symbol, executor),
                'backtest_data': None,
                'timing_tracker': timing_tracker,
            }

        # Parallelize data loading (Backtest + Agent signal generation)
        async def load_backtest_data():
            try:
                data = await asyncio.to_thread(load_historical_from_influx, symbol, self._interval, self._window)
                if data.empty:
                    logger.warning("[%s] Backtest skipped: no historical data", symbol)
                    return None
                return data
            except Exception as exc:
                logger.warning("[%s] Backtest data loading failed: %s", symbol, exc)
                return None

        async def generate_agent_signal():
            t1 = datetime.now(timezone.utc)
            # Agent signal generation might be CPU bound, run in thread
            signal = await asyncio.to_thread(agent.generate_signal)
            t2 = datetime.now(timezone.utc)
            timing_tracker.add_stage("data_collection", t1, t2)
            return signal

        # Run backtest data load and agent signal generation concurrently
        backtest_data, signal = await asyncio.gather(load_backtest_data(), generate_agent_signal())

        # Run backtest simulation if data available (cpu bound, separate thread)
        if backtest_data is not None:
            try:
                aggregated = backtest_data.pivot(index="timestamp", columns="field", values="value").reset_index(drop=True)
                tester = Backtester(agent)
                # Run CPU-intensive backtest in thread
                await asyncio.to_thread(tester.run, aggregated.to_dict(orient="records"))
                summary = tester.summary()
                net_pnl = summary["portfolio_value"] - summary["initial_cash"]
                logger.info("[%s] Backtest completed: PnL=%.2f", symbol, net_pnl)
            except Exception as exc:
                logger.warning("[%s] Backtest simulation failed: %s", symbol, exc)

        logger.info(
            "[%s] Agent signal | direction=%s | confidence=%.4f | reasoning=%s",
            symbol,
            signal.direction,
            signal.confidence,
            signal.reasoning,  # Show full reasoning
        )
        
        # Get portfolio metrics for GLM context (BEFORE execution)
        metrics_before = self._safe_portfolio_metrics(symbol, executor)
        
        # GÜVENLIK KONTROLÜ: Portfolio sanity check
        if abs(metrics_before.get('position', 0)) > 1000.0:
            logger.error(
                "[%s] CRITICAL: Portfolio position astronomical (%.6f) - running emergency fix",
                symbol,
                metrics_before['position']
            )
            # Emergency portfolio fix
            from app.executor.portfolio_sync import sync_portfolio_with_trades
            from app.executor.ledger import engine
            from sqlalchemy.orm import Session
            
            with Session(engine) as fix_session:
                sync_portfolio_with_trades(fix_session, symbol)
                fix_session.commit()
            
            logger.info("[%s] ✅ Portfolio emergency sync completed", symbol)
            # Refresh metrics
            metrics_before = self._safe_portfolio_metrics(symbol, executor)
            
            # Tekrar kontrol et - hala çok büyükse sistemi durdur
            if abs(metrics_before.get('position', 0)) > 1000.0:
                logger.critical(
                    "[%s] SYSTEM HALT: Cannot fix astronomical position (%.6f) - stopping trading",
                    symbol,
                    metrics_before['position']
                )
                raise RuntimeError(f"[{symbol}] Cannot fix astronomical position")
        
        # Evaluate with portfolio context (GLM çağrısı - en yavaş kısım)
        t3 = datetime.now(timezone.utc)
        logger.info("[%s] 🤖 Starting GLM evaluation at %s...", symbol, t3.strftime("%H:%M:%S.%f")[:-3])
        # Run GLM evaluation in thread to avoid blocking loop
        decision = await asyncio.to_thread(self._safe_evaluate, risk_manager, [signal], metrics_before)
        t4 = datetime.now(timezone.utc)
        timing_tracker.add_stage("glm_evaluation", t3, t4)
        logger.info("[%s] ✅ GLM evaluation completed at %s...", symbol, t4.strftime("%H:%M:%S.%f")[:-3])
        
        glm_duration = (t4 - t3).total_seconds()
        logger.info(
            "[%s] ✅ GLM evaluation complete (%.1fs) | action=%s | amount=%.4f | leverage=%.2f",
            symbol,
            glm_duration,
            decision.action,
            decision.amount,
            decision.leverage,
        )
        
        # Return evaluation results
        logger.info("[%s] 📊 Returning decision (id=%s) context_atr_pct=%s", symbol, id(decision), getattr(decision, 'context_atr_pct', 'NOT_SET'))
        return {
            'symbol': symbol,
            'signal': signal,
            'decision': decision,
            'metrics_before': metrics_before,
            'backtest_data': backtest_data,
            'timing_tracker': timing_tracker,
        }
    
    async def _execute_with_guard(self, symbol: str, eval_result: dict) -> None:
        """
        Execution için concurrency guard: aynı anda en fazla n sembol çalışsın.
        """
        async with self._execution_semaphore:
            return await self._execute_symbol(symbol, eval_result)
 
    async def _execute_symbol(self, symbol: str, eval_result: dict) -> None:
        """
        Phase 2: Execution ve notification (sıralı çalışmalı - database race condition önleme)
        
        Args:
            symbol: Trading symbol
            eval_result: Evaluation results from _evaluate_symbol
        """
        # Extract evaluation results
        signal = eval_result['signal']
        decision = eval_result['decision']
        metrics_before = eval_result['metrics_before']
        timing_tracker = eval_result['timing_tracker']
        eval_error = eval_result.get("evaluation_error")

        logger.info("[%s] 📊 Executing decision (id=%s) context_atr_pct=%s", symbol, id(decision), getattr(decision, 'context_atr_pct', 'NOT_SET'))
        
        # Get components for this symbol
        executor = self._executors[symbol]
        advanced_exit_monitor = self._advanced_exit_monitors[symbol]

        if eval_error:
            logger.warning("[%s] Skipping execution due to evaluation error: %s", symbol, eval_error)
            try:
                now = datetime.now(timezone.utc)
                timing_tracker.add_stage("execution_skipped", now, now)
            except Exception:
                pass
            try:
                await asyncio.to_thread(
                    self._notify_cycle_complete,
                    symbol,
                    decision,
                    ExecutionResult(status="EVAL_ERROR", details=str(eval_error)),
                    metrics_before,
                )
                logger.info("[%s] ✅ Sent evaluation-error notification", symbol)
            except Exception as exc:  # noqa: BLE001
                logger.error("[%s] Failed to notify evaluation error: %s", symbol, exc, exc_info=True)
            timing_tracker.finish_cycle()
            return
        
        logger.info(
            "[%s] GLM decision | action=%s | amount=%.4f | leverage=%.2f | reasoning=%s",
            symbol,
            decision.action,
            decision.amount,
            decision.leverage,
            decision.reasoning[:100],
        )
        
        # Execute decision
        t5 = datetime.now(timezone.utc)
        try:
            result = await asyncio.to_thread(executor.execute, decision)
        except Exception as exc:  # noqa: BLE001
            t6 = datetime.now(timezone.utc)
            timing_tracker.add_stage("execution", t5, t6)
            logger.error("[%s] Execution raised an exception: %s", symbol, exc, exc_info=True)
            result = ExecutionResult(status="ERROR", details=str(exc), telemetry={})
        else:
            t6 = datetime.now(timezone.utc)
            timing_tracker.add_stage("execution", t5, t6)

        # Get UPDATED portfolio metrics AFTER execution
        metrics_after = await asyncio.to_thread(self._safe_portfolio_metrics, symbol, executor, metrics_before)

        # Tek bildirim - hem döngü hem risk kararı (UPDATED metrics kullan)
        try:
            logger.info("[%s] 🔔 About to send Telegram notification for cycle complete", symbol)
            t7 = datetime.now(timezone.utc)
            # Send telegram message asynchronously if possible, or just in thread
            await asyncio.to_thread(self._notify_cycle_complete, symbol, decision, result, metrics_after)
            t8 = datetime.now(timezone.utc)
            timing_tracker.add_stage("telegram_notification", t7, t8)
            logger.info("[%s] ✅ Telegram notification sent successfully", symbol)
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] Telegram cycle notify failed: %s", symbol, exc, exc_info=True)
        
        # Finish cycle timing and log breakdown
        timing_breakdown = timing_tracker.finish_cycle()

        # Add position to advanced monitoring
        if result.status == "EXECUTED" and result.telemetry:
            telemetry = result.telemetry
            if telemetry.get("last_action") in ["LONG", "SHORT"]:
                position_id = telemetry.get("position_id")
                if position_id:
                    # Get exit plan from decision
                    exit_plan = decision.exit_plan if hasattr(decision, 'exit_plan') else {}
                    
                    # Add to advanced monitoring
                    advanced_exit_monitor.add_position(
                        position_id=position_id,
                        exit_plan=exit_plan,
                        entry_price=telemetry.get("price", 0.0),
                        position_side=telemetry.get("position_side"),
                        amount=telemetry.get("amount", 0.0),
                        leverage=telemetry.get("leverage", 1.0)
                    )
                    
                    logger.info(
                        "[%s] ✅ Added position to advanced monitoring: %s %s @ %.2f",
                        symbol,
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
                    advanced_exit_monitor.remove_position(position_id)
                    logger.info(
                        "[%s] ✅ Removed position %s from advanced monitoring due to %s",
                        symbol,
                        position_id,
                        telemetry.get("last_action")
                    )

        # Check position status and send notifications
        # Offload to thread to prevent blocking other symbols during synchronous Telegram calls
        await asyncio.to_thread(self._check_position_status_and_notify, symbol)
        
        # Send position summary notification
        await asyncio.to_thread(self._send_position_summary_notification, symbol)

    def _safe_evaluate(self, risk_manager: RiskManager, signals: List[AgentSignal], portfolio_metrics: dict = None) -> RiskDecision:
        try:
            return risk_manager.evaluate(signals, portfolio_metrics)
        except Exception as exc:  # noqa: BLE001
            logger.error("Risk evaluation failed: %s", exc, exc_info=True)
            # Try to generate a safe fallback decision using risk manager's fallback logic
            friendly_reason = "Teknik hata nedeniyle GLM devre dışı bırakıldı - HOLD kararı (güvenli mod)"
            try:
                fallback = risk_manager._fallback_decision(signals, friendly_reason)  # type: ignore[attr-defined]
                # Ensure timestamps are set to avoid staleness checks failing
                fallback.decision_timestamp = datetime.now(timezone.utc)
                return fallback
            except Exception as fb_exc:  # noqa: BLE001
                logger.error("Fallback decision generation failed: %s", fb_exc, exc_info=True)
                error_msg = str(exc)
                if isinstance(exc, NameError):
                    error_msg = f"Internal System Error (NameError): {exc}"
                return RiskDecision(
                    action="HOLD", 
                    amount=0.0, 
                    reasoning=f"Evaluation failed: {error_msg}",
                    decision_timestamp=datetime.now(timezone.utc)  # Fix: Add timestamp to prevent STALE error
                )

    def _notify_cycle_complete(
        self,
        symbol: str,
        decision: RiskDecision,
        result: ExecutionResult,
        metrics: Dict[str, float],
    ) -> None:
        """Tek Telegram bildirimi - döngü tamamlandı"""
        if not telegram_client.enabled():
            logger.warning("[%s] Telegram client is not enabled, skipping notification", symbol)
            return
        
        logger.info("[%s] 📨 Preparing Telegram notification - telegram_client enabled: %s", symbol, telegram_client.enabled())

        starting_capital = 10000.0
        total_pnl = metrics['total_pnl']
        pnl_pct = (total_pnl / starting_capital) * 100
        
        # Get trade history
        from app.executor.ledger import Session, engine, Trade
        with Session(engine) as session:
            total_trades = session.query(Trade).filter(Trade.symbol == symbol).count()
            executor = self._executors[symbol]
            recent_trades = executor.get_recent_trades_with_pnl(limit=5)
            skipped_anomalies = getattr(executor, "_last_trade_report_stats", {}).get("skipped_anomalies", 0)
            displayed_trade_count = max(0, total_trades - skipped_anomalies)
            # Compute total open notional (unleveraged)
            try:
                open_trades = (
                    session.query(Trade)
                    .filter_by(symbol=symbol)
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
        base_asset = symbol.replace("USDT", "")
        position = metrics['position']
        position_type = ""
        if position > 0.0001:
            position_type = "📈 LONG"
        elif position < -0.0001:
            position_type = "📉 SHORT"
        else:
            position_type = "⚪ FLAT"
        
        # Generate timestamp header like BTC_ANALYZER
        timestamp_header = f"{symbol}_ANALYZER, [{datetime.now().strftime('%d.%m.%Y %H:%M')}]"
        
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
        has_open_position = abs(position) > 0.0001
        open_trade = None
        if has_open_position and recent_trades:
            open_trade = next((t for t in recent_trades if not t['is_closed']), None)

        # DEBUG: Log exit plan status
        should_show_exit_plan = (
            (decision.exit_plan or open_trade) and 
            (decision.action in ["BUY", "SELL"] or (decision.action == "HOLD" and has_open_position))
        )
        
        if should_show_exit_plan:
            # Prefer the active trade's exit plan if available to avoid mismatched levels
            source_exit_plan = decision.exit_plan or {}
            if open_trade:
                source_exit_plan = {
                    "profit_target": open_trade.get("profit_target", 0.0),
                    "stop_loss": open_trade.get("stop_loss", 0.0),
                    "invalidation_condition": open_trade.get("invalidation_condition", ""),
                }

            profit_target = source_exit_plan.get("profit_target", 0.0)
            stop_loss = source_exit_plan.get("stop_loss", 0.0)
            invalidation = source_exit_plan.get("invalidation_condition", "")
            
            if stop_loss or profit_target:
                exit_plan_lines = [
                    "",
                    "*🎯 Çıkış Planı*",
                ]
                if profit_target:
                    exit_plan_lines.append(f"✅ Take Profit: ${profit_target:,.2f}")
                if stop_loss:
                    exit_plan_lines.append(f"🛑 Stop Loss: ${stop_loss:,.2f}")
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
                    f"  ├─ {side}: {amount:.4f} {base_asset} @ {leverage:.1f}x → ${notional:,.2f} (Margin: ${margin:,.2f})"
                )

        # Calculate real PnL (gross PnL is already net of fees in realized_pnl)
        # But we want to show: Gross PnL (before fees) + Total Fees + Net PnL
        
        # Calculate gross PnL from Trade table: Sum of (pnl + fees) for all closed trades
        # + unrealized gross PnL for open positions
        with Session(engine) as session:
            # Sum of (pnl + fees) for all closed trades
            # Handle NULL values properly: coalesce each field to 0 before summing
            closed_gross_pnl = (
                session.query(
                    func.coalesce(
                        func.sum(func.coalesce(Trade.pnl, 0.0) + func.coalesce(Trade.fees, 0.0)), 
                        0.0
                    )
                )
                .filter(Trade.symbol == symbol)
                .filter(Trade.close_price.isnot(None))
                .scalar()
            ) or 0.0
            
            # Calculate unrealized gross PnL for open positions
            # Note: metrics['unrealized_pnl'] is already GROSS (no fees deducted in calculation)
            # It's calculated as: (current_price - entry_price) * position_amount
            unrealized_pnl_gross = metrics.get('unrealized_pnl', 0.0)
            # No need to add fees again - unrealized_pnl is already gross
            unrealized_gross_pnl = unrealized_pnl_gross
            
            # Total gross PnL = closed trades gross PnL + open positions unrealized gross PnL
            gross_pnl = float(closed_gross_pnl or 0.0) + unrealized_gross_pnl
            
            # Calculate total fees including both closed and open positions
            total_fees_all = (
                session.query(func.coalesce(func.sum(Trade.fees), 0.0))
                .filter(Trade.symbol == symbol)
                .scalar()
            ) or 0.0
            total_fees = float(total_fees_all)
            
            logger.info("💰 Gross PnL calculation - closed_gross_pnl: %.2f, unrealized_pnl_gross: %.2f, total_fees: %.2f, total_gross_pnl: %.2f",
                       float(closed_gross_pnl or 0.0), unrealized_pnl_gross, total_fees, gross_pnl)
        
        gross_pnl_pct = (gross_pnl / starting_cash) * 100
        
        # Calculate NET PnL correctly: Gross PnL - Total Fees
        net_pnl = gross_pnl - total_fees
        net_pnl_pct = (net_pnl / starting_cash) * 100
        
        portfolio_lines.extend([
            f"🏦 Toplam Equity: ${equity:,.2f}",
            f"📦 Pozisyon Değeri: ${total_notional:,.2f}",
            f"💰 Başlangıç Sermayesi: ${starting_cash:,.2f}",
            f"Pozisyon: {position_type} {abs(position):.4f} {symbol.replace('USDT', '')}",
            f"{symbol.replace('USDT', '')} Fiyat: ${metrics['price']:,.2f}",
            f"Brüt PnL: ${gross_pnl:,.2f} ({gross_pnl_pct:+.2f}%) | Fee: ${total_fees:,.2f} → Net: ${net_pnl:,.2f} ({net_pnl_pct:+.2f}%)",
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
            profit_target = 0.0
            stop_loss = 0.0
            invalidation = ""
            # Reuse the open_trade detected above (if any)
            if open_trade:
                profit_target = open_trade.get('profit_target', 0.0)
                stop_loss = open_trade.get('stop_loss', 0.0)
                invalidation = open_trade.get('invalidation_condition', '')

                # DEBUG: Log found exit plan from trade
                logger.info("🔍 Exit Plan from Open Trade: tp=%s, sl=%s, invalidation=%s",
                           profit_target, stop_loss, invalidation)
            
            if stop_loss or profit_target:
                # Insert exit plan right after "Pozisyon:" line (before BTC Fiyat)
                position_line_idx = len(portfolio_lines) - 4  # 4 lines back from current end
                
                if stop_loss:
                    portfolio_lines.insert(position_line_idx, f"  ├─ 🛑 Stop Loss: ${stop_loss:,.2f}")
                    position_line_idx += 1
                
                if profit_target:
                    portfolio_lines.insert(position_line_idx, f"  ├─ ✅ Take Profit: ${profit_target:,.2f}")
                    position_line_idx += 1
                
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

        def append_recent_trades(trades: list[dict], start_index: int = 1, max_items: int = 5) -> None:
            """Append formatted recent trades to the Telegram lines."""
            if not trades or max_items <= 0:
                return
            
            for i, trade in enumerate(trades[:max_items], start_index):
                emoji = "🟢" if trade['pnl'] >= 0 else "🔴"
                status = "🔒 Kapandı" if trade['is_closed'] else "🔓 Açık"
                
                # Format trade line with position ID
                position_id_text = f" [{trade['position_id']}]" if trade.get('position_id') else ""
                trade_line = f"{i}. {emoji} {trade['side']} {trade['amount']:.4f} {base_asset}{position_id_text} {status}"
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
                net_pnl = trade['pnl']
                pnl_sign = "+" if net_pnl >= 0 else ""
                pct = float(trade.get('pnl_pct', 0.0) or 0.0)
                if abs(pct) < 0.01:
                    bps = abs(pct) * 100.0
                    pct_text = f"({('-' if pct < 0 else '+')}{bps:.1f} bp)"
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
                
                # Add stop loss and take profit ONLY for open positions
                if not trade['is_closed']:
                    stop_loss = trade.get('stop_loss')
                    profit_target = trade.get('profit_target')
                    
                    if profit_target:
                        lines.append(f"   ✅ Take Profit: ${profit_target:,.2f}")
                    
                    if stop_loss:
                        lines.append(f"   🛑 Stop Loss: ${stop_loss:,.2f}")
        
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
            lines.append(f"1. 🔒 CLOSE {side} {amt:.4f} {base_asset}{id_text}")
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
                lines.append(f"   Kalan: {remaining_percentage:.1f}% pozisyon → {abs(rem_pos):.4f} {base_asset} {remaining_side} @ ${avg_price:,.2f} | ⏳ Unrealized: ${unreal:,.2f}")
            else:
                lines.append(f"   Kalan: Pozisyon tamamıyla kapatıldı")
            
            pnl_val = result.telemetry.get('pnl')
            if pnl_val is not None:
                sign = '+' if pnl_val >= 0 else ''
                lines.append(f"   ✅ Gerçekleşen PnL: {sign}${pnl_val:,.2f}")
            lines.append("   ⛔ Yeni pozisyon açılmadı (CLOSE)")

            # Also show the recent trade history so it matches non-CLOSE cycles
            filtered_trades = recent_trades or []
            close_position_id = result.telemetry.get('position_id')
            if close_position_id and filtered_trades and filtered_trades[0].get('position_id') == close_position_id:
                filtered_trades = filtered_trades[1:]
            
            # Keep total visible trades to five (one slot used by the close summary above)
            append_recent_trades(filtered_trades, start_index=2, max_items=4)
        else:
            append_recent_trades(recent_trades)
        
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
                    operation_text = f"🔒 Pozisyon Kapatıldı: {side} {amt:.4f} {base_asset} @ ${prc:,.2f}"
                elif last_action in ['LONG', 'SHORT']:
                    operation_emoji = "📈" if last_action == 'LONG' else "📉"
                    operation_text = f"{operation_emoji} Pozisyon Eklendi: {last_action} {amt:.4f} {base_asset} @ ${prc:,.2f}"
                    
                    # Add current position info after addition
                    current_position = metrics.get('position', 0.0) or 0.0
                    avg_price = metrics.get('average_price', 0.0) or 0.0
                    if abs(current_position) > 0.0001:
                        current_side = "LONG" if current_position > 0 else "SHORT"
                        lines.append(operation_text)
                        lines.append(f"   📊 Güncel Pozisyon: {abs(current_position):.4f} {base_asset} {current_side} @ ${avg_price:,.2f}")
                        operation_text = None  # Skip adding to lines again
                else:
                    operation_text = f"🔄 İşlem: {side} {amt:.4f} {base_asset} @ ${prc:,.2f}"
                
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
                        f"{side_emoji} En Son İşlem: {latest_trade['side']} {latest_trade['amount']:.4f} {base_asset} @ ${price_for_notional:,.2f}",
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
        reasoning_text = decision.reasoning if decision.reasoning else ""
        reasoning_escaped = reasoning_text.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
        
        logger.info("📨 Message prepared - main_message length: %d, reasoning length: %d", len(main_message), len(reasoning_escaped))
        
        # Telegram has 4096 char limit - ilk mesajda mümkün olduğunca uzun gönder, kalanı parçalara böl
        # Send GLM response JSON if available (before main message)
        if decision.glm_response_json:
            try:
                import json
                from app.risk_manager.manager import _json_serializer
                
                # Update response time in JSON if available
                if decision.glm_response_time_ms > 0:
                    decision.glm_response_json["karar"]["yanit_suresi_ms"] = decision.glm_response_time_ms
                
                # Format the complete GLM response as JSON for telegram
                telegram_message = "```json\n" + json.dumps(decision.glm_response_json, indent=2, ensure_ascii=False, default=_json_serializer) + "\n```"
                
                # Add summary header
                action = decision.action
                quantity = decision.glm_response_json["karar"]["miktar_btc"]
                leverage = decision.glm_response_json["karar"]["kaldirac"]
                confidence = decision.glm_response_json["karar"]["guven"]
                header_message = f"*🤖 GLM TAM YANITI*\n📊 Karar: {action} | Miktar: {quantity:.6f} {base_asset} | Kaldıraç: {leverage:.1f}x | Güven: {confidence:.1f}%\n\n📝 JSON Yanıt:"
                
                # Send header and JSON separately to avoid size limits
                telegram_client.send_message(header_message)
                telegram_client.send_message(telegram_message)
                
                logger.info("✅ Complete GLM response JSON sent to Telegram")
            except Exception as json_exc:
                logger.error("❌ Failed to send GLM response JSON to Telegram: %s", json_exc)
        
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
            logger.error("❌ Telegram cycle notify failed: %s", exc, exc_info=True)
            logger.error("❌ Failed message details - main_message length: %d, reasoning length: %d, action: %s", 
                        len(main_message) if 'main_message' in locals() else 0, 
                        len(reasoning_escaped) if 'reasoning_escaped' in locals() else 0,
                        decision.action if decision else "N/A")
    
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
                
                # Tüm servisleri ve HTF verilerini izle (her sembol için)
                for sym in self._symbols:
                    results = monitor_all(sym)
                    
                    # Özet bilgi logla
                    summary = results.get("summary", {})
                    active_count = summary.get("active_services", 0)
                    total_services = summary.get("total_services", 0)
                    
                    if active_count < total_services:
                        logger.warning(
                            "⚠️ Service monitoring [%s]: %d/%d services active",
                            sym,
                            active_count,
                            total_services,
                        )
                    else:
                        logger.debug("✅ All services active for %s (%d/%d)", sym, active_count, total_services)
                    
                    # HTF verisi kontrolü
                    htf_results = results.get("htf_verification", {})
                    if not htf_results.get("enriched_15min_structure_valid", False):
                        logger.warning("⚠️ HTF 15min data not usable for %s - check enriched_15min measurement", sym)
                    if not htf_results.get("longterm_4h_usable", False):
                        logger.warning("⚠️ HTF 4h data not usable for %s - check enriched_4h measurement", sym)
                
            except asyncio.CancelledError:
                logger.info("Service monitoring loop cancelled")
                raise
            except Exception as exc:
                logger.error("Service monitoring error: %s", exc, exc_info=True)
                # Hata durumunda kısa bir bekleme sonra devam et
                await asyncio.sleep(60)

    def _check_position_status_and_notify(self, symbol: str) -> None:
        """Pozisyon durumunu kontrol eder ve bildirim gönderir"""
        try:
            # Get current position status from advanced monitor
            advanced_exit_monitor = self._advanced_exit_monitors[symbol]
            active_positions = advanced_exit_monitor.active_positions
            
            if not active_positions:
                logger.debug("No active positions to check")
                return
            
            # Get current price
            current_price = advanced_exit_monitor._get_current_price()
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
• Amount: {position_data.get('amount', 0.0):.6f} {symbol.replace('USDT', '')}

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
    
    def _send_position_summary_notification(self, symbol: str) -> None:
        """Pozisyon özet bildirimi gönderir"""
        try:
            advanced_exit_monitor = self._advanced_exit_monitors[symbol]
            active_positions = advanced_exit_monitor.active_positions
            
            if not active_positions:
                return
            
            current_price = advanced_exit_monitor._get_current_price()
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
📊 *{symbol} POSITION SUMMARY UPDATE*

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
• {position_side} {position_id}: {amount:.6f} {symbol.replace('USDT', '')} @ ${entry_price:.2f}
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

    def _on_crash_protection_trigger(
        self,
        symbol: str,
        current_price: float,
        reason: str,
        action: CrashAction,
    ) -> None:
        """
        Callback for crash protection events.
        Called when crash detection triggers a position close/reduce action.
        """
        try:
            logger.critical(
                "🚨 CRASH PROTECTION TRIGGERED %s | Action=%s | Price=$%.2f | Reason: %s",
                symbol, action.value, current_price, reason
            )

            executor = self._executors.get(symbol)
            if not executor:
                logger.error("No executor found for %s during crash protection", symbol)
                return

            # Get current position info
            metrics = self._safe_portfolio_metrics(symbol, executor)
            position = metrics.get('position', 0)

            if abs(position) < 0.0001:
                logger.info("No position to close for %s during crash protection", symbol)
                return

            # Execute action based on crash level
            if action == CrashAction.CLOSE_ALL:
                # Create emergency CLOSE decision
                from app.risk_manager.manager import RiskDecision
                from datetime import datetime, timezone

                close_decision = RiskDecision(
                    action="CLOSE",
                    amount=1.0,  # 100% close
                    leverage=1.0,
                    reasoning=f"🚨 CRASH PROTECTION: {reason}",
                    decision_timestamp=datetime.now(timezone.utc),
                )

                try:
                    result = executor.execute(close_decision)
                    logger.critical(
                        "✅ CRASH PROTECTION executed for %s: %s | Position closed at $%.2f",
                        symbol, result.status, current_price
                    )

                    # Set global cooldown
                    if self._crash_protection_manager:
                        self._crash_protection_manager.set_global_cooldown()

                except Exception as exec_exc:
                    logger.critical("❌ CRASH PROTECTION EXECUTION FAILED for %s: %s", symbol, exec_exc)

            elif action == CrashAction.REDUCE_50:
                # Reduce position by 50%
                from app.risk_manager.manager import RiskDecision
                from datetime import datetime, timezone

                reduce_decision = RiskDecision(
                    action="CLOSE",
                    amount=0.5,  # 50% close
                    leverage=1.0,
                    reasoning=f"⚠️ CRASH WARNING REDUCTION: {reason}",
                    decision_timestamp=datetime.now(timezone.utc),
                )

                try:
                    result = executor.execute(reduce_decision)
                    logger.warning(
                        "✅ CRASH REDUCTION executed for %s: %s | 50%% position closed at $%.2f",
                        symbol, result.status, current_price
                    )
                except Exception as exec_exc:
                    logger.error("❌ CRASH REDUCTION EXECUTION FAILED for %s: %s", symbol, exec_exc)

            elif action == CrashAction.ALERT_ONLY:
                # Send Telegram alert without taking action
                try:
                    message = f"""
🚨 *CRASH WARNING - {symbol}*

⚠️ {reason}

📊 **Current Status:**
• Price: ${current_price:,.2f}
• Position: {position:.6f} {symbol.replace('USDT', '')}
• HTF Trend: Bullish (no action taken)

💡 *System is monitoring closely. Manual intervention may be needed.*

⏰ *{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}*
"""
                    telegram_client.send_message(message)
                    logger.warning("📨 Crash warning alert sent for %s (no action taken)", symbol)
                except Exception as tg_exc:
                    logger.error("Failed to send crash warning telegram: %s", tg_exc)

        except Exception as exc:
            logger.critical("❌ CRASH PROTECTION CALLBACK ERROR: %s", exc, exc_info=True)

    def _get_htf_data_for_crash(self, symbol: str) -> Dict:
        """
        Get HTF (30m) data for crash protection HTF confirmation.
        Returns dict with ema_20 and ema_50 values.
        """
        try:
            from influxdb_client import InfluxDBClient
            from app.config.settings import get_settings

            settings = get_settings()
            htf_timeframe = settings.crash_protection_htf_timeframe  # "30m"

            client = InfluxDBClient(
                url=str(settings.influx.url),
                token=settings.influx.token,
                org=settings.influx.org,
            )

            # Query latest HTF bar with EMA data
            query = f'''
            from(bucket: "{settings.influx.bucket}")
              |> range(start: -2h)
              |> filter(fn: (r) => r["_measurement"] == "enriched_{htf_timeframe}")
              |> filter(fn: (r) => r["symbol"] == "{symbol}")
              |> filter(fn: (r) => r["_field"] == "ema_20" or r["_field"] == "ema_50")
              |> last()
              |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
            '''

            tables = client.query_api().query(query, org=settings.influx.org)

            for table in tables:
                for record in table.records:
                    ema_20 = record.values.get("ema_20", 0)
                    ema_50 = record.values.get("ema_50", 0)

                    logger.debug(
                        "HTF data for %s (%s): EMA20=%.2f, EMA50=%.2f",
                        symbol, htf_timeframe, ema_20, ema_50
                    )

                    return {
                        "ema_20": ema_20,
                        "ema_50": ema_50,
                        "timeframe": htf_timeframe,
                    }

            client.close()

        except Exception as exc:
            logger.error("Failed to get HTF data for %s: %s", symbol, exc)

        # Return empty dict on failure
        return {}


async def run_automated(
    symbols: Optional[List[str]] = None,
    symbol: str = "BTCUSDT",
    interval: str = "5min",
    execution_concurrency: Optional[int] = None,
) -> None:
    # Default to multi-symbol if not provided
    if not symbols:
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        
    # Allow full parallelism across provided symbols by default
    resolved_concurrency = execution_concurrency or len(symbols)
    runner = AutomatedRunner(
        symbols=symbols,
        symbol=symbol,
        interval=interval,
        execution_concurrency=resolved_concurrency,
    )
    await runner.start()


def acquire_pid_lock():
    """
    Acquire an exclusive PID lock to prevent multiple orchestrator instances.
    
    Returns:
        file descriptor of the lock file (must be kept open)
    
    Raises:
        SystemExit: if another instance is already running
    """
    lock_file_path = "/tmp/trading_orchestrator.lock"
    
    try:
        lock_fd = open(lock_file_path, 'w')
        # Try to acquire exclusive lock (non-blocking)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        
        # Write current PID to lock file
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        
        logger.info("🔒 PID lock acquired: %s (PID: %d)", lock_file_path, os.getpid())
        return lock_fd
        
    except IOError as e:
        logger.error("❌ Another orchestrator instance is already running!")
        logger.error("Lock file: %s", lock_file_path)
        logger.error("If you're sure no other instance is running, delete: %s", lock_file_path)
        sys.exit(1)


def main() -> None:
    # Acquire PID lock to prevent duplicate instances
    lock_fd = acquire_pid_lock()
    
    try:
        asyncio.run(run_automated())
    except KeyboardInterrupt:
        logger.info("Automated runner stopped by user")
    finally:
        # Release lock on exit (though file descriptor closing does this automatically)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
            logger.info("🔓 PID lock released")
        except Exception as e:
            logger.debug("Error releasing lock: %s", e)


if __name__ == "__main__":
    main()
