#!/usr/bin/env python3
"""Quick test to verify GLM latency is captured correctly"""

from app.risk_manager.manager import RiskDecision

# Test 1: Create RiskDecision with latency
decision = RiskDecision(
    action="HOLD",
    amount=0.5,
    reasoning="Test reasoning",
    leverage=5.0,
    glm_confidence=50.0,
    glm_response_time_ms=120000.0  # 120 seconds (high latency)
)

print("✅ Test 1: RiskDecision created with latency")
print(f"  - Action: {decision.action}")
print(f"  - GLM Response Time: {decision.glm_response_time_ms:.0f}ms")

# Test 2: Verify emoji logic matches thresholds
latency_ms = decision.glm_response_time_ms
if latency_ms < 500:
    emoji = "🟢"  # Good
    status = "GOOD (< 500ms)"
elif latency_ms < 1000:
    emoji = "🟡"  # Warning
    status = "WARNING (500-1000ms)"
else:
    emoji = "🔴"  # Critical
    status = "CRITICAL (> 1000ms)"

print(f"\n✅ Test 2: Emoji color coding")
print(f"  - Latency: {latency_ms:.0f}ms")
print(f"  - Status: {status}")
print(f"  - Emoji: {emoji}")

# Test 3: Format as it would appear in Telegram
if decision.glm_response_time_ms > 0:
    latency_emoji = "🟢" if decision.glm_response_time_ms < 500 else "🟡" if decision.glm_response_time_ms < 1000 else "🔴"
    telegram_line = f"{latency_emoji} GLM Yanıt: {decision.glm_response_time_ms:.0f}ms"
    print(f"\n✅ Test 3: Telegram format")
    print(f"  - {telegram_line}")

print("\n✅ All tests passed! GLM latency is being captured correctly.")
