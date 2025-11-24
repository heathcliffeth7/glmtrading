# GLM Exit Plan Fallback Sistemi - Implementasyon Özeti

**Tarih:** 2025-11-01  
**Sorun:** GLM bazen exit_plan vermediğinde veya geçersiz değerler (0.0) gönderdiğinde işlem açılmıyor (SKIP)  
**Çözüm:** Hibrit yaklaşım - Prompt güçlendirme + Fallback mekanizması

---

## 🎯 Yapılan Değişiklikler

### 1. GLM Prompt Güçlendirme ✅
**Dosya:** `app/risk_manager/nof1_prompt_builder.py`

**Değişiklik:**
- Exit plan zorunluluğunu çok daha net vurgulayan başlık ekledik
- Forbidden values (0.0, null) listesi ekledik
- LONG ve SHORT için örnek exit plan değerleri gösterdik
- "Trade REJECTED" uyarıları ekledik

**Örnek eklenen prompt:**
```
⚠️⚠️⚠️ EXIT PLAN IS ABSOLUTELY MANDATORY FOR BUY/SELL ⚠️⚠️⚠️

❌ FORBIDDEN VALUES - THESE WILL CAUSE TRADE REJECTION:
  • profit_target: 0.0 ← WRONG! Your trade will be REJECTED!
  • stop_loss: 0.0 ← WRONG! Your trade will be REJECTED!

✅ CORRECT EXAMPLES:
**For LONG @ $110,000:**
"profit_target": 115000.0,  // +4.5% upside target
"stop_loss": 108000.0,      // -1.8% downside protection
```

---

### 2. Text Parsing Fallback (Manager) ✅
**Dosya:** `app/risk_manager/manager.py`

**Değişiklik:**
GLM JSON'da exit_plan vermezse, gerekçe metninden extract etmeye çalışır.

**Pattern'ler:**
- `Kar hedefi: 108500`
- `Stop loss: 111200`
- `profit_target: 115000`

**Çalışma Mantığı:**
1. JSON'da profit_target/stop_loss yoksa
2. Gerekçe metnini regex ile tara
3. Bulursan exit_plan oluştur
4. Bulamazsan executor'a None gönder (executor fallback yapar)

**Kod:**
```python
tp_patterns = [
    r'[Kk]ar\s+hedef[ıi][:\s]+(\d+\.?\d*)',
    r'profit[_\s]*target[:\s]+(\d+\.?\d*)',
]

sl_patterns = [
    r'[Ss]top\s+loss[:\s]+(\d+\.?\d*)',
]

# Extract and create exit_plan if found
```

---

### 3. Fallback Exit Plan Calculation (Executor) ✅
**Dosya:** `app/executor/executor.py`

**Değişiklik:**
GLM geçersiz exit_plan gönderirse (None veya 0.0), sistem otomatik hesaplar.

**Fallback Kuralları:**
- Stop Loss: **2%** (muhafazakar)
- Profit Target: **4%** (2:1 risk/reward oranı)
- LONG için: TP = price × 1.04, SL = price × 0.98
- SHORT için: TP = price × 0.96, SL = price × 1.02

**Örnek:**
```python
# Entry @ $110,000 LONG
profit_target = 110000 * 1.04 = $114,400
stop_loss = 110000 * 0.98 = $107,800
invalidation = "If price closes below 107,580 on 3-minute candle"
```

**Telegram Notification:**
```
⚠️ FALLBACK EXIT PLAN USED

GLM did not provide valid exit plan for BUY action.

*System calculated fallback:*
  • Profit Target: $114,400.00 (+4.0%)
  • Stop Loss: $107,800.00 (-2.0%)
  
💡 Note: Please review GLM prompt to ensure exit plan requirements are clear.
```

---

## 🔄 Sistem Akışı

### Senaryo 1: GLM JSON'da Exit Plan Veriyor ✅
```
GLM → JSON: profit_target=115000, stop_loss=108000
     ↓
Manager: ✅ Exit plan valid
     ↓
Executor: ✅ Uses GLM's exit plan
     ↓
Position Monitor: ✅ Monitors GLM's levels
```

### Senaryo 2: GLM Gerekçede Exit Plan Veriyor (JSON'da Yok) ✅
```
GLM → JSON: profit_target=0, stop_loss=0
      Text: "Kar hedefi: 115000 | Stop loss: 108000"
     ↓
Manager: 🔍 Extracts from text → profit_target=115000, stop_loss=108000
     ↓
Executor: ✅ Uses extracted values
     ↓
Position Monitor: ✅ Monitors extracted levels
```

### Senaryo 3: GLM Hiç Exit Plan Vermiyor ⚠️ → FALLBACK
```
GLM → JSON: profit_target=null, stop_loss=null
      Text: (exit plan yok)
     ↓
Manager: ⚠️ No exit plan found → sends None to executor
     ↓
Executor: ⚠️ FALLBACK calculation → TP=+4%, SL=-2%
     ↓
Telegram: ⚠️ "FALLBACK EXIT PLAN USED" notification
     ↓
Position Monitor: ✅ Monitors fallback levels
```

---

## 📊 Beklenen Sonuçlar

### Önceki Durum ❌
- GLM exit_plan vermezse → İşlem SKIP
- Fırsat kaçırılıyor
- Kullanıcı hayal kırıklığı

### Yeni Durum ✅
- GLM JSON'da exit_plan veriyor → Kullanılır ✅
- GLM text'te exit_plan veriyor → Extract edilir ✅
- GLM hiç vermiyor → Fallback devreye girer ⚠️
- **İşlem hep açılır, fırsat kaçırılmaz!**

---

## 🧪 Test Durumları

### Test 1: Normal Flow (JSON'da Exit Plan)
```json
{
  "profit_target": 115000.0,
  "stop_loss": 108000.0
}
```
**Sonuç:** ✅ GLM'nin değerleri kullanılır

---

### Test 2: Text Extraction (JSON'da 0.0)
```json
{
  "profit_target": 0.0,
  "stop_loss": 0.0
}
```
```
Gerekçe: "... Kar hedefi: 115000 | Stop loss: 108000 ..."
```
**Sonuç:** ✅ Text'ten extract edilir (115000, 108000)

---

### Test 3: Fallback (Hiç Yok)
```json
{
  "profit_target": null,
  "stop_loss": null
}
```
```
Gerekçe: (exit plan bilgisi yok)
```
**Sonuç:** ⚠️ Fallback devreye girer
- Entry: $110,000
- TP: $114,400 (+4%)
- SL: $107,800 (-2%)
- Telegram notification gönderilir

---

## 📈 İzleme ve Monitoring

### Log Mesajları

**Normal Flow:**
```
✅ GLM Exit Plan created: {'profit_target': 115000.0, 'stop_loss': 108000.0}
```

**Text Extraction:**
```
🔍 Attempting to extract exit plan from justification text...
✅ Extracted profit_target from text: 115000.00
✅ Extracted stop_loss from text: 108000.00
✅ GLM Exit Plan extracted from justification text
```

**Fallback:**
```
⚠️ GLM Exit Plan MISSING in JSON
⚠️ No valid exit plan for BUY action - executor will use fallback
CRITICAL: GLM exit_plan has invalid values
⚠️ Using FALLBACK exit plan calculation
✅ FALLBACK exit plan created: TP=114400.00 (+4.0%), SL=107800.00 (-2.0%)
✅ Fallback notification sent to Telegram
```

---

## 🎯 Faydalar

1. **Trade kaçırma yok:** GLM exit plan vermese bile işlem açılır
2. **Güvenli fallback:** 2:1 risk/reward oranı ile muhafazakar
3. **Şeffaf:** Fallback kullanımı log ve Telegram'da bildirilir
4. **İzlenebilir:** Hangi senaryonun çalıştığı açıkça görünür
5. **GLM eğitilebilir:** Prompt güçlendirme ile GLM daha az hata yapar

---

## 🔧 Gelecek İyileştirmeler (Opsiyonel)

1. **Fallback istatistikleri:** Fallback kullanım oranını track et
2. **Dinamik fallback:** ATR bazlı daha akıllı stop loss hesaplama
3. **Adaptif oranlar:** Volatiliteye göre %2-5 arası stop loss
4. **Metrics dashboard:** Fallback kullanım grafiği

---

## 📝 Notlar

- Fallback sistemin **son çare** korumasıdır
- GLM genelde doğru exit plan verecek (prompt güçlendirildi)
- Fallback kullanımı nadirdir ama kritik durumlarda fırsat kaçırmayı engeller
- Her fallback kullanımı Telegram'da bildirilir

---

## ✅ Syntax Kontrolü

Tüm dosyalar syntax kontrolünden geçti:
```bash
python3 -m py_compile app/risk_manager/nof1_prompt_builder.py
python3 -m py_compile app/risk_manager/manager.py
python3 -m py_compile app/executor/executor.py
```

**Sonuç:** ✅ Başarılı
