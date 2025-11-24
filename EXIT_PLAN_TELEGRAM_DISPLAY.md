# Exit Plan Display in Telegram Messages

## Problem Solved
BTC_ANALYZER Telegram mesajlarında GLM'nin çıkış planı (exit plan) bilgileri gösterilmiyordu:
- Kar hedefi (profit_target)
- Stop loss seviyesi
- Geçersiz kılma koşulu (invalidation_condition)

## Solution
`/root/trading/app/orchestrator/runtime.py` dosyasındaki `_notify_cycle_complete()` metoduna **🎯 Çıkış Planı** bölümü eklendi.

## Changes Made

### File: `app/orchestrator/runtime.py`

**Lines 350-373:** Exit plan section builder eklendi
```python
# Build exit plan section (profit target, stop loss, invalidation)
# Show for BUY/SELL, or HOLD when there's an open position
exit_plan_lines = []
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
        if invalidation and invalidation.strip() and invalidation.strip().upper() not in ["N/A", "NA", "NONE", ""]:
            # Escape markdown characters in invalidation text
            invalidation_escaped = format_markdown(invalidation)
            exit_plan_lines.append(f"⚠️ Geçersiz Kılma: {invalidation_escaped}")
```

**Line 418:** Message assembly updated to include exit_plan_lines
```python
lines = [
    f"{timestamp_header}",
    f"*📊 {self._cycle // 60} Dakikalık Döngü Tamamlandı*",
    "",
    "*🎯 GLM Kararı*",
] + decision_section + exit_plan_lines + portfolio_lines
```

## New Telegram Message Format

### Before (Missing Exit Plan):
```
BTC_ANALYZER, [02.11.2025 20:37]
*📊 3 Dakikalık Döngü Tamamlandı*

*🎯 GLM Kararı*
Karar: HOLD
Miktar: 0.0% equity
Kaldıraç: 10.0x
Durum: SKIP
🔴 GLM Yanıt: 19321ms

*💰 Portföy Durumu*
💎 Serbest Sermaye: $8,852.61 (89.9%)
...
```

### After (With Exit Plan):
```
BTC_ANALYZER, [02.11.2025 20:37]
*📊 3 Dakikalık Döngü Tamamlandı*

*🎯 GLM Kararı*
Karar: HOLD
Miktar: 0.0% equity
Kaldıraç: 10.0x
Durum: SKIP
🔴 GLM Yanıt: 19321ms

*🎯 Çıkış Planı*
💰 Kar Hedefi: $107,500.00
🛑 Stop Loss: $111,500.00
⚠️ Geçersiz Kılma: If price closes above 111000 on 3-minute candle

*💰 Portföy Durumu*
💎 Serbest Sermaye: $8,852.61 (89.9%)
...
```

## When Exit Plan is Displayed

Exit plan gösterilir:
1. ✅ **BUY** kararında - Yeni pozisyon açılırken
2. ✅ **SELL** kararında - Yeni pozisyon açılırken
3. ✅ **HOLD** kararında VE açık pozisyon varsa - Mevcut pozisyonun exit planını göster

Exit plan gösterilmez:
- ❌ **HOLD** kararında VE pozisyon yoksa (FLAT)
- ❌ **CLOSE** kararında (pozisyon kapatılıyor, exit plan artık geçerli değil)
- ❌ Exit plan bilgisi eksikse (`profit_target` veya `stop_loss` 0.0 veya None)

## Display Logic

### Invalidation Condition
Invalidation condition gösterilir:
- ✅ Metin doluysa
- ✅ "N/A", "NA", "NONE" veya boş değilse

Markdown karakterleri escape edilir:
- `_` → `\_`
- `*` → `\*`
- `[` → `\[`
- `` ` `` → `` \` ``

### Price Formatting
Fiyatlar ABD formatında gösterilir:
- `${profit_target:,.2f}` → `$115,000.00`
- `${stop_loss:,.2f}` → `$108,000.00`

## Benefits

✅ **Şeffaflık:** Kullanıcı GLM'nin belirlediği çıkış noktalarını görür
✅ **Risk Yönetimi:** Stop loss ve kar hedefi net
✅ **Pozisyon Takibi:** HOLD kararlarında da exit plan görünür
✅ **Otomatik Kapatma:** Hangi koşulda pozisyonun otomatik kapanacağı belli

## Testing

Syntax validated:
```bash
python3 -m py_compile app/orchestrator/runtime.py
# ✅ Success
```

Next steps:
1. Bot'u restart et
2. Bir sonraki GLM kararında Telegram mesajını kontrol et
3. Exit plan bölümünün doğru gösterildiğini doğrula

## Files Modified

- ✅ `/root/trading/app/orchestrator/runtime.py` (Lines 350-373, 418)

## Related Files

Exit plan verileri buralardan geliyor:
- `app/risk_manager/manager.py` - GLM'den exit_plan parse eder
- `app/executor/executor.py` - Exit plan'ı trade'e kaydeder
- `app/monitoring/exit_checker.py` - Exit koşullarını izler
