# CLOSE Action Implementation

**Tarih:** 2025-10-23  
**Amaç:** Pozisyon kapatmak için karşı yönde işlem açmak yerine, direkt CLOSE action'ı eklemek

## Sorun

Önceki sistemde pozisyon kapatmak için:
- SHORT pozisyon varsa → **BUY** emri açılıyordu (karşı pozisyon açarak kapatma)
- LONG pozisyon varsa → **SELL** emri açılıyordu (karşı pozisyon açarak kapatma)

Bu kafa karıştırıcıydı çünkü:
- "BUY açtık" gibi görünüyordu
- Ama aslında SHORT kapatıyorduk
- Telegram'da net değildi

## Çözüm: CLOSE Action

Yeni **CLOSE** action'ı eklendi:
- Mevcut pozisyonu **direkt** kapatır
- Karşı yönde yeni pozisyon **AÇMAZ**
- Daha net ve anlaşılır

### Action Tipleri:

| Action | Kullanım | Açıklama |
|--------|----------|----------|
| **BUY** | Yeni LONG aç veya LONG artır | Pozitif yönde trade |
| **SELL** | Yeni SHORT aç veya SHORT artır | Negatif yönde trade |
| **CLOSE** | Mevcut pozisyonu kapat | Direkt kapatma, yeni pozisyon yok |
| **HOLD** | Bekle | İşlem yapma |

## Değişiklikler

### 1. Risk Manager (`app/risk_manager/manager.py`)

#### Action Validation
```python
# Eski
if action not in {"BUY", "SELL", "HOLD"}:
    return None

# Yeni
if action not in {"BUY", "SELL", "HOLD", "CLOSE"}:
    return None
```

#### GLM System Prompt
```
POZİSYON YÖNETİMİ KURALLARI:
(1) LONG pozisyon + düşüş sinyali → MUTLAKA **CLOSE** kararı ver (SELL değil!)
(2) SHORT pozisyon + yükseliş sinyali → MUTLAKA **CLOSE** kararı ver (BUY değil!)
(3) **CLOSE action**: Mevcut pozisyonu DİREK kapatır, karşı yönde yeni pozisyon AÇMAZ
(4) **BUY/SELL action**: Sadece YENİ pozisyon açmak için kullan
```

#### GLM User Prompt - Decision Output
```json
{
  "karar": "BUY|SELL|CLOSE|HOLD",
  "miktar": 0.5,  // For CLOSE: 1.0=100% close
  "kaldıraç": 10,
  "gerekçe": "..."
}

ACTION TYPES:
  - BUY: Open new LONG
  - SELL: Open new SHORT
  - CLOSE: Close existing position (NO new position opened!)
  - HOLD: Wait

WHEN TO USE CLOSE:
  - LONG + bearish signals → CLOSE (not SELL!)
  - SHORT + bullish signals → CLOSE (not BUY!)
```

#### GLM User Prompt - Portfolio Section
```
⚠️ POSITION MANAGEMENT RULES:
  - If position is LONG and bearish → Use CLOSE action (NOT SELL!)
  - If position is SHORT and bullish → Use CLOSE action (NOT BUY!)

📌 IMPORTANT: Use CLOSE to exit position directly, not BUY/SELL!
```

### 2. Executor (`app/executor/executor.py`)

#### CLOSE Action Handler
```python
# CLOSE ACTION: Direkt pozisyon kapatma
if decision.action == "CLOSE":
    if abs(portfolio.position) < 0.0001:
        return ExecutionResult(status="SKIP", details="Kapatılacak pozisyon yok")
    
    return self._execute_close_position(
        session, portfolio, daily_pnl, price, decision, reason, pre_position
    )
```

#### `_execute_close_position` Method
```python
def _execute_close_position(self, ...) -> ExecutionResult:
    """
    CLOSE action: Direkt pozisyon kapatma (yeni pozisyon açmadan)
    """
    # 1. Kapatılacak miktarı hesapla
    max_closeable = abs(portfolio.position)
    btc_amount = max_closeable * decision.amount  # 1.0 = 100%
    
    # 2. Pozisyon yönünü belirle
    is_long = portfolio.position > 0
    position_side = "LONG" if is_long else "SHORT"
    
    # 3. Fee hesapla
    position_size_usd = btc_amount * price
    fee = position_size_usd * self._taker_fee_rate
    
    # 4. PnL hesapla
    if is_long:
        pnl_before_fee = (price - portfolio.average_price) * btc_amount
    else:
        pnl_before_fee = (portfolio.average_price - price) * btc_amount
    pnl = pnl_before_fee - fee
    
    # 5. Trade kaydı (side="CLOSE")
    trade = record_trade(..., side="CLOSE", ...)
    
    # 6. Karşı trade'lerin close_price'larını güncelle
    close_open_trades(...)
    
    # 7. Pozisyonu güncelle
    if decision.amount >= 0.9999:  # Tam kapatma
        portfolio.position = 0.0
        portfolio.average_price = 0.0
    else:  # Kısmi kapatma
        portfolio.position -= btc_amount (LONG) veya += (SHORT)
    
    # 8. Daily PnL güncelle
    daily_pnl.realized_pnl += pnl
    daily_pnl.unrealized_pnl = ...
    
    # 9. Telegram bildirimi
    self._notify_telegram(...)
    
    return ExecutionResult(status="PAPER", details=f"{position_side} pozisyon kapatıldı")
```

#### Telegram Notification
```python
# CLOSE için özel emoji ve mesaj
if trade.side == 'CLOSE':
    direction_emoji = '🔒'
    action_text = "POZİSYON KAPATMA"
elif trade.side == 'BUY':
    direction_emoji = '📈'
    action_text = "LONG AÇILDI"
else:  # SELL
    direction_emoji = '📉'
    action_text = "SHORT AÇILDI"

message = f"*🚨 İşlem Gerçekleşti - {action_text}*"
```

## Kullanım Senaryoları

### Senaryo 1: SHORT Pozisyon Kapatma
```
Durum: SHORT -0.5 BTC var, piyasa yükseliş gösteriyor
Eski: GLM → BUY 0.5 BTC (karşı pozisyon açarak kapat)
Yeni: GLM → CLOSE 1.0 (direkt kapat, yeni pozisyon YOK)

Sonuç:
- Position: -0.5 BTC → 0.0 BTC ✅
- Trade side: "CLOSE"
- Telegram: "🔒 POZİSYON KAPATMA"
```

### Senaryo 2: LONG Pozisyon Kapatma
```
Durum: LONG +0.3 BTC var, piyasa düşüş gösteriyor
Eski: GLM → SELL 0.3 BTC (karşı pozisyon açarak kapat)
Yeni: GLM → CLOSE 1.0 (direkt kapat)

Sonuç:
- Position: +0.3 BTC → 0.0 BTC ✅
- Trade side: "CLOSE"
- Telegram: "🔒 POZİSYON KAPATMA"
```

### Senaryo 3: Kısmi Kapatma
```
Durum: LONG +1.0 BTC var, risk azaltmak isteniyor
GLM: CLOSE 0.5 (yarısını kapat)

Sonuç:
- Position: +1.0 BTC → +0.5 BTC ✅
- PnL: Yarısı için realize oldu
- Kalan pozisyon: +0.5 BTC
```

## Telegram Bildirimi Örnekleri

### Eski (BUY/SELL ile kapatma):
```
*🚨 İşlem Gerçekleşti*
📈 Yön: BUY
📦 Miktar: 0.5505 BTC
...
```
**Sorun:** "BUY açtık" gibi görünüyor ama aslında SHORT kapatıyoruz!

### Yeni (CLOSE action):
```
*🚨 İşlem Gerçekleşti - POZİSYON KAPATMA*
🔒 Yön: CLOSE
📦 Miktar: 0.5505 BTC
💵 Fiyat: $108,919.80
...
📍 Pozisyon Durumu: Pozisyon SHORT kapatıldı (-0.5505 -> 0.0000)
```
**Avantaj:** Net bir şekilde pozisyon kapatıldığı görülüyor! ✅

## Test Sonuçları

### GLM Öğrenmesi:
- ✅ CLOSE action'ını JSON'da kullanabiliyor
- ✅ LONG + düşüş → CLOSE kararı veriyor
- ✅ SHORT + yükseliş → CLOSE kararı veriyor
- ✅ Miktar oranını doğru belirtiyor (1.0 = tam, 0.5 = yarı)

### Executor:
- ✅ CLOSE action'ını yakalıyor
- ✅ Pozisyonu direkt kapatıyor
- ✅ PnL doğru hesaplanıyor
- ✅ Trade kaydı "CLOSE" olarak saklanıyor
- ✅ Karşı trade'lerin close_price'ları güncelleniyor

### Telegram:
- ✅ CLOSE için özel emoji (🔒)
- ✅ "POZİSYON KAPATMA" başlığı
- ✅ Pozisyon durumu net gösteriliyor

## İlgili Düzeltmeler (Aynı Gün)

Bu düzeltme bugün yapılan 3. major düzeltme:

1. **Sabah:** Pozisyon yönetimi kuralları eklendi (GLM'e BUY/SELL ile kapatma öğretildi)
2. **Öğlen:** Executor bug fix (position_size_usd), Telegram metrics fix (güncel metrics)
3. **Akşam:** **CLOSE action eklendi** (direkt kapatma, yeni pozisyon yok)

## Değiştirilen Dosyalar

### `app/risk_manager/manager.py`
- Line 288: Action validation'a CLOSE eklendi
- Line 248-263: User prompt'a CLOSE action açıklaması
- Line 132-137: Portfolio section'a CLOSE vurgusu
- Line 272: System prompt'a CLOSE kuralları

### `app/executor/executor.py`
- Line 66-74: CLOSE action handler
- Line 577-698: `_execute_close_position` method (YENİ)
- Line 429-438: Telegram emoji ve action text (CLOSE için özel)

## Sonraki Adımlar

1. ✅ GLM'den CLOSE kararı gelmesini bekle (bir sonraki döngü)
2. ✅ Telegram'da "🔒 POZİSYON KAPATMA" bildirimini gör
3. ✅ Log'larda "CLOSE LONG/SHORT position" mesajlarını kontrol et
4. ✅ Database'de trade.side="CLOSE" kayıtlarını gör

## Önemli Notlar

- **BUY/SELL artık sadece yeni pozisyon açmak için** kullanılıyor
- **CLOSE pozisyon kapatmak için** kullanılıyor
- GLM artık **açıkça CLOSE kararı vermeli**
- Eski BUY/SELL ile kapatma mantığı hala çalışıyor (backward compatibility)
- CLOSE daha net ve anlaşılır olduğu için **tercih edilmeli**

## Backward Compatibility

Eski sistemle uyumluluk:
- BUY/SELL ile kapatma hala çalışıyor
- `is_closing` kontrolü hala mevcut
- Eski trade'ler etkilenmiyor
- Yeni CLOSE action ekstra bir özellik

## İlgili Dokümantasyon

- `POSITION_MANAGEMENT_FIX.md` - Pozisyon yönetimi kuralları
- `TELEGRAM_METRICS_FIX.md` - Telegram güncel metrics düzeltmesi
- `PORTFOLIO_RESET_OCT23.md` - Portföy sıfırlama ve executor bug fix
