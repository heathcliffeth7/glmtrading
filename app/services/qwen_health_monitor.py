"""
Qwen Token Service Health Monitor & Auto-Recovery

Watchdog service that:
1. Monitors token service health (token age, status, service state)
2. Detects failures (Playwright missing, token stale, service crashed)
3. Auto-recovers (installs Playwright, restarts service)
4. Logs recovery attempts to Redis

Run as: python -m app.services.qwen_health_monitor
Or via systemd: trading-qwen-watchdog.service (oneshot, triggered by timer)
"""

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Optional, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.utils.logging import get_logger
from app.utils.redis import get_redis_client

logger = get_logger(__name__)


class HealthStatus:
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"


class QwenHealthMonitor:
    """Health monitor and auto-recovery for Qwen token service."""
    
    TOKEN_MAX_AGE_HOURS = 2
    MAX_RECOVERY_PER_HOUR = 10
    RECOVERY_LOG_MAX_SIZE = 10
    PLAYWRIGHT_PATH = Path.home() / ".cache/ms-playwright"
    
    REDIS_KEYS = {
        "token_status": "qwen:token_status",
        "last_refresh": "qwen:last_refresh",
        "health_check": "qwen:health_check",
        "health_status": "qwen:health_status",
        "recovery_log": "qwen:recovery_log",
        "last_recovery": "qwen:last_recovery",
        "recovery_count": "qwen:recovery_count",
    }
    
    def __init__(self):
        self.redis = get_redis_client()
    
    async def run_health_check(self) -> str:
        """Main health check - returns HEALTHY/DEGRADED/CRITICAL."""
        logger.info("=" * 60)
        logger.info("Qwen Health Check Starting")
        logger.info("=" * 60)
        
        try:
            token_health = self._check_token_health()
            service_health = self._check_service_health()
            playwright_health = self._check_playwright_health()
            
            logger.info("Token Health: %s", token_health)
            logger.info("Service Health: %s", service_health)
            logger.info("Playwright Health: %s", playwright_health)
            
            if token_health == HealthStatus.CRITICAL or service_health == HealthStatus.CRITICAL:
                status = HealthStatus.CRITICAL
                reason = []
                if token_health == HealthStatus.CRITICAL:
                    reason.append("Token not refreshed for 2+ hours")
                if service_health == HealthStatus.CRITICAL:
                    reason.append("Service not running")
                if playwright_health == HealthStatus.CRITICAL:
                    reason.append("Playwright browser missing")
                
                logger.warning("🚨 CRITICAL status detected: %s", ", ".join(reason))
                
                if self._should_attempt_recovery():
                    await self._auto_recover(reason)
                else:
                    logger.error("Recovery rate limit exceeded, skipping auto-recovery")
                    status = HealthStatus.CRITICAL
            
            elif token_health == HealthStatus.DEGRADED:
                status = HealthStatus.DEGRADED
                logger.info("⚠️ DEGRADED: Token is aging but service running")
            
            else:
                status = HealthStatus.HEALTHY
                logger.info("✅ HEALTHY: All systems operational")
            
            self._update_health_status(status)
            return status
        
        except Exception as e:
            logger.error("Health check failed: %s", e)
            self._update_health_status(HealthStatus.CRITICAL)
            return HealthStatus.CRITICAL
    
    def _check_token_health(self) -> str:
        """Check token freshness and status."""
        try:
            status = self.redis.get(self.REDIS_KEYS["token_status"])
            last_refresh_str = self.redis.get(self.REDIS_KEYS["last_refresh"])
            
            if not last_refresh_str:
                logger.warning("No last_refresh timestamp found")
                return HealthStatus.CRITICAL
            
            last_refresh = datetime.fromisoformat(last_refresh_str.decode() if isinstance(last_refresh_str, bytes) else last_refresh_str)
            age = datetime.now(timezone.utc) - last_refresh
            
            logger.info("Token age: %s", age)
            logger.info("Token status: %s", status)
            
            if status == b"ERROR" or status == "ERROR":
                return HealthStatus.CRITICAL
            
            if age > timedelta(hours=self.TOKEN_MAX_AGE_HOURS):
                return HealthStatus.CRITICAL
            
            if age > timedelta(hours=1):
                return HealthStatus.DEGRADED
            
            return HealthStatus.HEALTHY
        
        except Exception as e:
            logger.error("Token health check failed: %s", e)
            return HealthStatus.CRITICAL
    
    def _check_service_health(self) -> str:
        """Check if systemd service is active."""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "trading-qwen-token.service"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            is_active = result.stdout.strip() == "active"
            logger.info("Service active: %s", is_active)
            
            return HealthStatus.HEALTHY if is_active else HealthStatus.CRITICAL
        
        except Exception as e:
            logger.error("Service health check failed: %s", e)
            return HealthStatus.CRITICAL
    
    def _check_playwright_health(self) -> str:
        """Check if Playwright browser binaries exist."""
        try:
            chromium_exists = self.PLAYWRIGHT_PATH.exists() and any(self.PLAYWRIGHT_PATH.glob("chromium*"))
            
            logger.info("Playwright browser exists: %s", chromium_exists)
            
            return HealthStatus.HEALTHY if chromium_exists else HealthStatus.CRITICAL
        
        except Exception as e:
            logger.error("Playwright health check failed: %s", e)
            return HealthStatus.CRITICAL
    
    def _should_attempt_recovery(self) -> bool:
        """Check if we should attempt recovery (rate limit protection)."""
        try:
            count_str = self.redis.get(self.REDIS_KEYS["recovery_count"])
            count = int(count_str) if count_str else 0
            
            if count >= self.MAX_RECOVERY_PER_HOUR:
                logger.error("Recovery rate limit hit: %d/%d attempts in last hour", 
                           count, self.MAX_RECOVERY_PER_HOUR)
                return False
            
            return True
        
        except Exception as e:
            logger.warning("Failed to check recovery rate limit: %s", e)
            return True
    
    async def _auto_recover(self, reasons: List[str]):
        """Attempt automatic recovery."""
        logger.info("🔧 Starting auto-recovery...")
        
        recovery_log = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": ", ".join(reasons),
            "playwright_reinstalled": False,
            "service_restarted": False,
            "success": False
        }
        
        try:
            playwright_ok = self._check_playwright_health() == HealthStatus.HEALTHY
            
            if not playwright_ok:
                logger.info("Attempting Playwright recovery...")
                playwright_ok = await self._recover_playwright()
                recovery_log["playwright_reinstalled"] = playwright_ok
            
            logger.info("Attempting service restart...")
            service_ok = await self._restart_token_service()
            recovery_log["service_restarted"] = service_ok
            
            if service_ok:
                logger.info("Waiting 30s for token refresh...")
                await asyncio.sleep(30)
                
                token_health = self._check_token_health()
                recovery_log["success"] = token_health in [HealthStatus.HEALTHY, HealthStatus.DEGRADED]
                
                if recovery_log["success"]:
                    logger.info("✅ Recovery successful!")
                    self._update_health_status(HealthStatus.HEALTHY)
                else:
                    logger.error("❌ Recovery failed: Token still unhealthy")
                    self._update_health_status(HealthStatus.CRITICAL)
            else:
                logger.error("❌ Recovery failed: Service restart failed")
                recovery_log["success"] = False
                self._update_health_status(HealthStatus.CRITICAL)
        
        except Exception as e:
            logger.error("Recovery error: %s", e)
            recovery_log["success"] = False
            self._update_health_status(HealthStatus.CRITICAL)
        
        finally:
            self._log_recovery_attempt(recovery_log)
    
    async def _recover_playwright(self) -> bool:
        """Reinstall Playwright browser."""
        try:
            logger.info("Installing Playwright chromium...")
            
            result = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "playwright", "install", "chromium",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(result.communicate(), timeout=300)
            
            if result.returncode == 0:
                logger.info("Playwright installed successfully")
                return True
            else:
                logger.error("Playwright install failed: %s", stderr.decode())
                return False
        
        except asyncio.TimeoutError:
            logger.error("Playwright install timed out")
            return False
        except Exception as e:
            logger.error("Playwright recovery failed: %s", e)
            return False
    
    async def _restart_token_service(self) -> bool:
        """Restart token service via systemctl."""
        try:
            logger.info("Restarting trading-qwen-token.service...")
            
            result = subprocess.run(
                ["systemctl", "restart", "trading-qwen-token.service"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                logger.error("Service restart failed: %s", result.stderr)
                return False
            
            await asyncio.sleep(5)
            
            status_result = subprocess.run(
                ["systemctl", "is-active", "trading-qwen-token.service"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            is_active = status_result.stdout.strip() == "active"
            
            if is_active:
                logger.info("Service restarted successfully")
                self.redis.set(self.REDIS_KEYS["last_recovery"], datetime.now(timezone.utc).isoformat())
                return True
            else:
                logger.error("Service not active after restart")
                return False
        
        except Exception as e:
            logger.error("Service restart failed: %s", e)
            return False
    
    def _update_health_status(self, status: str):
        """Update health status in Redis."""
        try:
            self.redis.set(self.REDIS_KEYS["health_check"], datetime.now(timezone.utc).isoformat())
            self.redis.set(self.REDIS_KEYS["health_status"], status)
        except Exception as e:
            logger.warning("Failed to update health status: %s", e)
    
    def _log_recovery_attempt(self, recovery_log: Dict):
        """Log recovery attempt to Redis."""
        try:
            log_str = self.redis.get(self.REDIS_KEYS["recovery_log"])
            
            if log_str:
                logs = json.loads(log_str)
            else:
                logs = []
            
            logs.append(recovery_log)
            
            if len(logs) > self.RECOVERY_LOG_MAX_SIZE:
                logs = logs[-self.RECOVERY_LOG_MAX_SIZE:]
            
            self.redis.set(self.REDIS_KEYS["recovery_log"], json.dumps(logs))
            
            self.redis.incr(self.REDIS_KEYS["recovery_count"])
            self.redis.expire(self.REDIS_KEYS["recovery_count"], 3600)
            
            logger.info("Recovery attempt logged")
        
        except Exception as e:
            logger.warning("Failed to log recovery attempt: %s", e)


async def main():
    """Entry point for health monitor."""
    monitor = QwenHealthMonitor()
    status = await monitor.run_health_check()
    
    logger.info("=" * 60)
    logger.info("Health Check Complete: %s", status)
    logger.info("=" * 60)
    
    sys.exit(0 if status != HealthStatus.CRITICAL else 1)


if __name__ == "__main__":
    asyncio.run(main())
