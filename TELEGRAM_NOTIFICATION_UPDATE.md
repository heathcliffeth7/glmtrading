# Telegram Notification Updates

## ✅ Değişiklikler (Oct 23, 2025 - 10:22 UTC)

3 önemli güncelleme yapıldı:

---

## 1️⃣ Döngü Süresi Mesajı: 30dk → 15dk

### Önce:
```
📊 30 Dakikalık Döngü Tamamlandı
```

### Sonra:
```
📊 15 Dakikalık Döngü Tamamlandı
```

**Dosya:** `app/orchestrator/runtime.py`

---

## 2️⃣ "PAPER" Durum Açıklaması

### Soru: "Durum: PAPER" GLM kararına göre mi belirleniyor?

**Cevap:** Hayır, bu **executor'da hardcoded**.

**Kod:** `app/executor/executor.py`
```python
return ExecutionResult(status="PAPER", details="Paper trading kaydedildi")
```

**Açıklama:**
- `status="PAPER"` → Simülasyon (gerçek Binance API'ye işlem gönderilmiyor)
- Sadece PostgreSQL database'e yazılıyor
- GLM kararı: `BUY/SELL/HOLD` (farklı bir şey)

**Olası Durumlar:**
- `PAPER`: Simülasyon işlemi kaydedildi
- `BLOCKED`: Guardrail engelledi
- `SKIP`: HOLD kararı veya amount=0

---

## 3️⃣ Serbest Margin Gösterimi Eklendi

### Önce:
```
💰 Portföy Özeti
💎 Mevcut Sermaye: $9975.00
📈 Başlangıç: $10000.00
🔴 Toplam: -0.25%
✅ Gerçekleşen PnL: $-25.00
⏳ Gerçekleşmemiş PnL: $0.00
```

**Sorun:** Serbest sermaye gösterilmiyordu!

### Sonra:
```
💰 Portföy Özeti
💎 Toplam Sermaye: $9975.00
📊 Kullanılan Margin: $5000.00
💵 Serbest Margin: $4975.00 (49.9%)
📈 Başlangıç: $10000.00
🔴 Toplam: -0.25%
✅ Gerçekleşen PnL: $-25.00
⏳ Gerçekleşmemiş PnL: $0.00
```

**Hesaplama:**
```python
# Calculate margin used and free margin
margin_used = position_value / leverage if position_value > 0 and leverage > 0 else 0.0
free_margin = equity - margin_used
free_margin_pct = (free_margin / equity * 100) if equity > 0 else 0.0
```

**Örnek:**
- Toplam Sermaye: $10,000
- İşlem Açılıyor: BUY 0.4570 BTC @ $109,404 = $50,000 notional
- Kaldıraç: 10x
- **Kullanılan Margin:** $50,000 / 10 = $5,000
- **Serbest Margin:** $10,000 - $5,000 = $5,000 (50%)
- Fee: -$25
- **Güncel Toplam:** $9,975
- **Güncel Serbest:** $9,975 - $5,000 = $4,975 (49.9%)

---

## 📊 Yeni Mesaj Formatı

### İşlem Bildirimi (Trade Notification)
```
🚨 İşlem Gerçekleşti
📊 Sembol: BTCUSDT
📈 Yön: BUY
📦 Miktar: 0.4570 BTC
💵 Fiyat: $109,404.20
⚡ Kaldıraç: 10.0x
💰 Notional: $50,000.00
💸 Fee: $25.00 (0.05%)
🔴 İşlem PnL: $-25.00 (fee sonrası)

📍 Pozisyon Durumu: Yeni pozisyon açıldı (+0.4570)
📊 Portföy Pozisyonu: 0.4570 BTC @ $109,404.20
💸 Pozisyon Değeri: $50,000.00

💰 Portföy Özeti
💎 Toplam Sermaye: $9,975.00          ← Equity (total capital)
📊 Kullanılan Margin: $5,000.00       ← Used margin
💵 Serbest Margin: $4,975.00 (49.9%)  ← Free margin ✅ YENİ
📈 Başlangıç: $10,000.00
🔴 Toplam: -0.25%
✅ Gerçekleşen PnL: $-25.00
⏳ Gerçekleşmemiş PnL: $0.00

💬 Gerekçe: [GLM reasoning]
🕒 Zaman: 2025-10-23T10:27:13
```

### Döngü Tamamlandı Bildirimi (Cycle Complete)
```
📊 15 Dakikalık Döngü Tamamlandı  ← 30dk → 15dk ✅ YENİ

🎯 GLM Kararı
Karar: BUY
Miktar: 50.0% equity
Kaldıraç: 10.0x
Durum: PAPER                      ← Hardcoded (not from GLM)

💰 Portföy Durumu
Sermaye: $9,975.00
Pozisyon: 📈 LONG 0.4570 BTC
BTC Fiyat: $109,556.90
PnL: $-25.00 (-0.25%)

📝 Son 5 İşlem (Toplam: 1)
[trade list]

💬 Gerekçe
[GLM reasoning]
```

---

## 🎯 Özet

✅ **Döngü süresi:** 30dk → 15dk mesajı güncellendi  
✅ **PAPER durumu:** Hardcoded (simülasyon modu)  
✅ **Serbest margin:** Artık Telegram'da gösteriliyor  

**Tüm değişiklikler aktif! 🚀**
