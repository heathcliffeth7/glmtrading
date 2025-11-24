#!/usr/bin/env python3
import sys

sys.path.insert(0, '/root/trading')

from app.orchestrator.runtime import AutomatedRunner
from app.risk_manager.manager import RiskDecision

# Simulate a decision with VERY LONG reasoning
long_reasoning = "Piyasa analizi: " + ("Detaylı teknik analiz verisi. " * 200)  # ~5000 chars

decision = RiskDecision(
    action="HOLD",
    amount=0.0,
    reasoning=long_reasoning,
    leverage=10.0,
    glm_confidence=85.0,
    reason_primary="Test",
    reason_secondary=""
)

# Build message like runtime does
lines = ["BTC_ANALYZER", "*📊 Döngü*", "", "*🎯 GLM*", "Karar: HOLD"] + ["Detail line " + str(i) for i in range(50)]
lines.extend(["", "*💬 Gerekçe*"])

main_message = "\n".join(lines)
reasoning_escaped = decision.reasoning.replace('_', '\\_').replace('*', '\\*')

print(f"Main message length: {len(main_message)}")
print(f"Reasoning length: {len(reasoning_escaped)}")
print(f"Total length: {len(main_message) + len(reasoning_escaped)}")
print()

available_space = 4096 - len(main_message) - 10

if len(reasoning_escaped) <= available_space:
    print("✅ Fits in one message")
    full_message = main_message + "\n" + reasoning_escaped
    print(f"Single message: {len(full_message)} chars")
else:
    print("⚠️ Too long - needs splitting")
    first_reasoning = reasoning_escaped[:available_space]
    first_message = main_message + "\n" + first_reasoning
    remaining = reasoning_escaped[available_space:]
    
    print(f"First message: {len(first_message)} chars")
    print(f"Remaining: {len(remaining)} chars")
    
    # Split remaining into chunks
    chunk_size = 4000
    chunks = []
    for i in range(0, len(remaining), chunk_size):
        chunks.append(remaining[i:i+chunk_size])
    
    print(f"Additional chunks: {len(chunks)}")
    for i, chunk in enumerate(chunks, 1):
        print(f"  Chunk {i}: {len(chunk)} chars")
