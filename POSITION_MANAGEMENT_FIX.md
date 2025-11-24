# Pozisyon Yönetimi Düzeltmesi

**Tarih:** 2025-10-23

## Sorun

Long pozisyon açıldığında kapatılma kararı alınırsa kapatılması gerekiyor, aynı şekilde short pozisyon için de. GLM'in pozisyon kapatma kararlarında eksiklik vardı.

## Analiz

### Executor Mantığı ✅
- `executor.py` dosyasındaki pozisyon kapatma mantığı **zaten doğru çalışıyordu**
- Line 72-73: `is_closing` kontrolü doğru
  - LONG (position > 0) + SELL action → Kapatma
  - SHORT (position < 0) + BUY action → Kapatma
- Tüm test senaryoları başarılı geçti

### Asıl Sorun: GLM Pozisyon Farkındalığı ❌
- GLM'e mevcut pozisyon bilgisi gönderiliyordu ama:
  - **Pozisyon yönetimi talimatları eksikti**
  - GLM, pozisyon kapatma kurallarını bilmiyordu
  - Prompt'da pozisyon durumu yeterince vurgulanmıyordu

## Çözüm

### 1. GLM System Prompt Güncellendi

**Eski:**
```
Sen profesyonel bir kripto türev piyasası risk yöneticisisin. 
MULTI-TIMEFRAME ANALİZİ ÖNCELİKLİ...
[pozisyon yönetimi kuralları YOK]
```

**Yeni:**
```
Sen profesyonel bir kripto türev piyasası risk yöneticisisin. 
MULTI-TIMEFRAME ANALİZİ ÖNCELİKLİ...

**POZİSYON YÖNETİMİ KURALLARI** (ÇOK ÖNEMLİ!): 
(1) Mevcut pozisyon LONG (+pozitif BTC) ve piyasa düşüş sinyali → MUTLAKA SELL ile kapat
(2) Mevcut pozisyon SHORT (-negatif BTC) ve piyasa yükseliş sinyali → MUTLAKA BUY ile kapat  
(3) Pozisyon kapatma miktarı: tam kapatma için 1.0, kısmi için 0.5-0.8
(4) Ters yönde pozisyon açmadan önce mevcut pozisyonu kapat
(5) Pozisyon = 0 ise yeni pozisyon açabilirsin
```

### 2. GLM User Prompt Güncellendi

**Eski:**
```
Current Portfolio:
  Equity: $10,000.00
  Position: +0.5000 BTC
  Total PnL: $100.00 (+1.00%)
```

**Yeni:**
```
=== CURRENT PORTFOLIO (CRITICAL!) ===

  Equity: $10,000.00
  Position: +0.500000 BTC (LONG (positive BTC))
  Total PnL: $100.00 (+1.00%)

⚠️ POSITION MANAGEMENT RULES:
  - If position is LONG (+0.500000 BTC) and market shows bearish signals → SELL to close
  - If position is SHORT (+0.500000 BTC) and market shows bullish signals → BUY to close
  - If position is FLAT (0 BTC) → You can open new LONG (BUY) or SHORT (SELL)
```

## Test Sonuçları

### Executor Mantık Testleri ✅
```
✅ LONG + SELL = Kapatma (Expected: True, Got: True)
✅ SHORT + BUY = Kapatma (Expected: True, Got: True)
✅ LONG + BUY = Artırma (Expected: False, Got: False)
✅ SHORT + SELL = Artırma (Expected: False, Got: False)
✅ FLAT + BUY = Yeni pozisyon (Expected: False, Got: False)
✅ FLAT + SELL = Yeni pozisyon (Expected: False, Got: False)
```

### Pozisyon Yönetimi Senaryoları

| Senaryo | Mevcut Pozisyon | Piyasa Sinyali | Beklenen Karar |
|---------|----------------|----------------|----------------|
| 1 | LONG (+0.5 BTC) | BEARISH | ✅ SELL (kapat) |
| 2 | SHORT (-0.5 BTC) | BULLISH | ✅ BUY (kapat) |
| 3 | LONG (+0.5 BTC) | BULLISH | ✅ HOLD/BUY (devam/artır) |
| 4 | SHORT (-0.5 BTC) | BEARISH | ✅ HOLD/SELL (devam/artır) |
| 5 | FLAT (0 BTC) | BULLISH | ✅ BUY (yeni LONG) |
| 6 | FLAT (0 BTC) | BEARISH | ✅ SELL (yeni SHORT) |

## Değişiklikler

### Dosya: `app/risk_manager/manager.py`

1. **System prompt'a pozisyon yönetimi kuralları eklendi** (line 248)
2. **User prompt'ta portföy bölümü vurgulandı** (line 117-136)
   - Pozisyon tipi net belirtildi (LONG/SHORT/FLAT)
   - Her pozisyon için kapatma kuralları eklendi

## Sonraki Adımlar

1. ✅ **Executor mantığı doğru çalışıyor** - değişiklik gerekmedi
2. ✅ **GLM prompt'ları güncellendi** - pozisyon farkındalığı artırıldı
3. 🔜 **Gerçek trading'de test edilmeli**
4. 🔜 **Telegram bildirimlerini izle** - GLM'in kararlarını gözlemle
5. 🔜 **Pozisyon kapatma durumlarını log'lardan takip et**

## Önemli Notlar

- Executor'daki `is_closing` kontrolü zaten doğruydu
- Asıl sorun GLM'in pozisyon yönetimi kurallarını bilmemesiydi
- Şimdi GLM her döngüde:
  - Mevcut pozisyonu görecek (LONG/SHORT/FLAT)
  - Pozisyon kapatma kurallarını bilecek
  - Gerekçesinde pozisyon durumunu belirtecek
- Trade execution mantığı değişmedi, sadece GLM'in karar verme süreci iyileştirildi

## Test Komutları

```bash
# Pozisyon kapatma mantığını test et
python3 test_position_closing.py

# Pozisyon yönetimi senaryolarını test et  
python3 test_position_management.py
```

## İlgili Dosyalar

- `app/risk_manager/manager.py` - GLM prompt'ları (güncellendi)
- `app/executor/executor.py` - Pozisyon kapatma mantığı (değişiklik YOK)
- `test_position_closing.py` - Executor mantık testleri
- `test_position_management.py` - Pozisyon yönetimi testleri (yeni)
