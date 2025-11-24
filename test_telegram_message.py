#!/usr/bin/env python3
import sys

sys.path.insert(0, '/root/trading')

from app.orchestrator.runtime import AutomatedRunner
from app.risk_manager.manager import RiskDecision

# Simulate a decision with reasoning
decision = RiskDecision(
    action="HOLD",
    amount=0.0,
    reasoning="Mevcut uzun pozisyonun çıkış planında hata var - kar hedefi giriş fiyatının altında, stop loss ise üzerinde. 4 saatlik grafikte MACD negatif ve EMA20 EMA50'nin altında, bu da düşüş trendini gösteriyor. 30 dakikalık RSI 51.90 ile nötr ancak MACD düşüş eğiliminde. Pozisyonu kapatıp daha güvenli bir giriş noktası beklemek mantıklı.",
    leverage=10.0,
    glm_confidence=85.0,
    reason_primary="Mevcut uzun pozisyonun çıkış planında hata var",
    reason_secondary=""
)

# Build a simple test message
lines = [
    "BTC_ANALYZER, [31.10.2025 TEST]",
    "*📊 3 Dakikalık Döngü Tamamlandı*",
    "",
    "*🎯 GLM Kararı*",
    "Karar: HOLD",
    "",
    "*💬 Gerekçe*",
]

main_message = "\n".join(lines)
reasoning_escaped = decision.reasoning.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')

full_message = main_message + "\n" + reasoning_escaped

print("=" * 60)
print("MAIN MESSAGE (header only):")
print("=" * 60)
print(main_message)
print()
print("=" * 60)
print("FULL MESSAGE (with reasoning):")
print("=" * 60)
print(full_message)
print()
print("=" * 60)
print(f"Main message length: {len(main_message)}")
print(f"Full message length: {len(full_message)}")
print(f"Reasoning length: {len(reasoning_escaped)}")
print("=" * 60)
