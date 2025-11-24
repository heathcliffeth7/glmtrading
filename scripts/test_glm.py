#!/usr/bin/env python3
"""Test GLM API connectivity and response"""
import sys

sys.path.insert(0, '/root/trading')

import json
from datetime import datetime

from app.agents.base import AgentSignal
from app.risk_manager.glm_client import GLMClient

print('╔══════════════════════════════════════════╗')
print('║   GLM API TEST                           ║')
print('╚══════════════════════════════════════════╝\n')

# Create test signal
test_signal = AgentSignal(
    direction='BUY',
    confidence=0.65,
    reasoning='RSI 35 (oversold), EMA 20 > EMA 50 (uptrend), L/S ratio 1.3 (balanced)',
    timestamp=datetime.utcnow().isoformat()
)

# Build prompt
messages = [
    {'role': 'system', 'content': 'Finansal risk yöneticisi olarak karar ver.'},
    {'role': 'user', 'content': f'''Ajan Sinyalleri:
- {test_signal.timestamp} | {test_signal.direction} | güven: {test_signal.confidence} | {test_signal.reasoning}

Çıktı formatı JSON: {{"karar": "BUY/SELL/HOLD", "miktar": 0-1, "kaldıraç": 5-20, "gerekçe": "..."}}
'''}
]

print('📤 GLM\'e Gönderilen Prompt:')
print(f'  Signal: {test_signal.direction}')
print(f'  Confidence: {test_signal.confidence}')
print(f'  Reasoning: {test_signal.reasoning}')
print()

# Send request
client = GLMClient()

try:
    print('⏳ GLM API\'ye istek gönderiliyor...')
    response = client.request(messages)
    
    print('\n✅ GLM YANITLADI!\n')
    
    # Parse response
    content = response['choices'][0]['message']['content']
    print('📥 Raw Response:')
    print('-' * 50)
    print(content)
    print('-' * 50)
    print()
    
    # Try to parse JSON
    content_clean = content.strip()
    if content_clean.startswith('```json'):
        content_clean = content_clean[7:]
    elif content_clean.startswith('```'):
        content_clean = content_clean[3:]
    if content_clean.endswith('```'):
        content_clean = content_clean[:-3]
    content_clean = content_clean.strip()
    
    try:
        decision = json.loads(content_clean)
        
        print('📊 Parsed Decision:')
        print(f'  Karar: {decision.get("karar")}')
        print(f'  Miktar: {decision.get("miktar")}')
        print(f'  Kaldıraç: {decision.get("kaldıraç")}')
        print(f'  Gerekçe: {decision.get("gerekçe", "")[:100]}...')
    except json.JSONDecodeError as je:
        print(f'⚠️  JSON parse hatası: {je}')
        print(f'Temizlenmiş içerik: {content_clean[:200]}')
    
    # Token usage
    usage = response.get('usage', {})
    print(f'\n💰 Token Kullanımı:')
    print(f'  Prompt: {usage.get("prompt_tokens", 0)}')
    print(f'  Completion: {usage.get("completion_tokens", 0)}')
    print(f'  Total: {usage.get("total_tokens", 0)}')
    
    # Model info
    print(f'\n🤖 Model:')
    print(f'  {response.get("model", "N/A")}')
    print(f'  Request ID: {response.get("id", "N/A")}')
    
    print('\n✅ GLM API çalışıyor ve yanıt veriyor!')
    
except Exception as e:
    print(f'\n❌ HATA: {e}')
    import traceback
    traceback.print_exc()
    
    print('\n💡 Kontrol Edilecekler:')
    print('  1. GLM API key doğru mu? (.env dosyasında GLM_API_KEY)')
    print('  2. GLM servisi erişilebilir mi?')
    print('  3. Network bağlantısı var mı?')
