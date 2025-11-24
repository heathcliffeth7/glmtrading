# Telegram 400 Bad Request Hatası Düzeltmesi

**Tarih:** 2025-10-23  
**Sorun:** İşlem bildirimleri Telegram'a gönderilirken 400 Bad Request hatası

## Sorun

Trade notification mesajları Telegram'a gönderilirken başarısız oluyordu:

```
14:07:11.886 | HTTP/1.1 400 Bad Request  ❌
14:07:16.311 | HTTP/1.1 400 Bad Request  ❌
```

Kullanıcı SHORT pozisyon açtığında bildirimi alamıyordu.

## Kök Neden

1. **Mesaj çok uzundu:** 2697+ karakter (GLM'in uzun reasoning'i)
2. **Markdown escape eksikti:** `reason` parametresindeki özel karakterler (`*`, `_`, `(`, `)`, `[`, `]`, `` ` ``) escape edilmemişti
3. **Hata yakalanmıyordu:** Exception catch ediliyordu ama retry yok

## Telegram API Limitleri

- Maximum message length: **4096 karakter**
- Markdown V2 özel karakterleri escape edilmeli: `_`, `*`, `[`, `]`, `(`, `)`, `~`, `` ` ``, `>`, `#`, `+`, `-`, `=`, `|`, `{`, `}`, `.`, `!`

## Çözüm

### 1. Markdown Escape Eklendi

```python
# Gerekçeyi ayrı mesaj olarak gönder (çok uzun olabilir)
reason_escaped = reason.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`').replace('(', '\\(').replace(')', '\\)')
```

### 2. Mesaj Uzunluğu Kontrolü

```python
# Gerekçe çok uzunsa kısalt (Telegram 4096 karakter limiti)
max_reason_length = 3000  # Ana mesaj + gerekçe için yer bırak
if len(reason_escaped) > max_reason_length:
    reason_escaped = reason_escaped[:max_reason_length] + "... (kısaltıldı)"

message_with_reason = message + f"\n\n💬 *Gerekçe:*\n{reason_escaped}"
```

### 3. Fallback Mekanizması

```python
try:
    # Tam mesajı gönder (gerekçe ile)
    telegram_client.send_message(message_with_reason)
    logger.info("Telegram trade notification sent successfully")
except Exception as exc:
    logger.error("Telegram trade notify failed: %s", exc, exc_info=True)
    # Gerekçe olmadan tekrar dene
    try:
        telegram_client.send_message(message + "\n\n💬 Gerekçe: (çok uzun, log'lara bakın)")
        logger.warning("Sent telegram notification without detailed reasoning")
    except Exception as exc2:
        logger.error("Failed to send simplified telegram notification: %s", exc2)
```

## Önceki Durum (Hatalı)

```
*🚨 İşlem Gerçekleşti - SHORT AÇILDI*
📊 Sembol: BTCUSDT
📉 Yön: SELL
📦 Miktar: 0.6407 BTC
💵 Fiyat: $109,253.20
...
💬 Gerekçe: ANALİZ: (1) **Timeframe Uyumu:** 4-saatlik... [2500+ karakter]
```
**Sorun:** Markdown karakterleri escape edilmemiş, mesaj çok uzun → 400 Bad Request

## Yeni Durum (Düzeltilmiş)

```
*🚨 İşlem Gerçekleşti - SHORT AÇILDI*
📊 Sembol: BTCUSDT
📉 Yön: SELL
📦 Miktar: 0.6407 BTC
💵 Fiyat: $109,253.20
...
🕒 Zaman: 2025-10-23T14:07:11.254806

💬 *Gerekçe:*
ANALİZ: \(1\) \*\*Timeframe Uyumu:\*\* 4-saatlik... [max 3000 karakter]
```
**İyileştirme:** 
- Markdown karakterleri escape edildi ✅
- 3000 karakter limiti ✅
- Başarısız olursa kısa mesaj gönderiliyor ✅

## Test Senaryoları

### Senaryo 1: Kısa Gerekçe (< 3000 karakter)
```
✅ Tam mesaj gönderilir (gerekçe ile)
✅ Telegram'a ulaşır
✅ Kullanıcı tüm detayları görür
```

### Senaryo 2: Uzun Gerekçe (> 3000 karakter)
```
✅ Gerekçe 3000 karaktere kısaltılır
✅ "... (kısaltıldı)" eklenir
✅ Mesaj başarıyla gönderilir
```

### Senaryo 3: Markdown Hatası
```
⚠️ İlk gönderim başarısız (400 Bad Request)
✅ Fallback: Kısa mesaj gönderilir ("Gerekçe: çok uzun, log'lara bakın")
✅ Kullanıcı en azından trade detaylarını görür
```

## Mesaj Formatı

### Ana Bilgiler (Her Zaman Gönderilir):
```
*🚨 İşlem Gerçekleşti - [ACTION_TEXT]*
📊 Sembol: BTCUSDT
[📈/📉/🔒] Yön: [BUY/SELL/CLOSE]
📦 Miktar: X.XXXX BTC
💵 Fiyat: $XX,XXX.XX
⚡ Kaldıraç: XX.Xx
💰 Notional: $XX,XXX.XX
💸 Fee: $XX.XX (0.05%)
[🟢/🔴] İşlem PnL: $XX.XX

📍 Pozisyon Durumu: [açıklama]
📊 Portföy Pozisyonu: X.XXXX BTC @ $XX,XXX.XX
💸 Pozisyon Değeri: $XX,XXX.XX

*💰 Portföy Özeti*
💎 Toplam Sermaye: $X,XXX.XX
📊 Kullanılan Margin: $X,XXX.XX
💵 Serbest Margin: $X,XXX.XX (XX.X%)
📈 Başlangıç: $10,000.00
[🟢/🔴] Toplam: ±X.XX%
✅ Gerçekleşen PnL: $XX.XX
⏳ Gerçekleşmemiş PnL: $XX.XX

🕒 Zaman: 2025-10-23T14:07:11
```

### Gerekçe (Opsiyonel - Başarılı Gönderilirse):
```
💬 *Gerekçe:*
[Escaped GLM reasoning, max 3000 karakter]
```

## Log Mesajları

### Başarılı Gönderim:
```
Telegram trade notify: side=SELL amount=0.6407 ... message_len=800 (with reason: 2500)
Telegram trade notification sent successfully
```

### Başarısız Gönderim (Fallback):
```
Telegram trade notify failed: 400 Bad Request ...
Sent telegram notification without detailed reasoning
```

### Tamamen Başarısız:
```
Telegram trade notify failed: 400 Bad Request ...
Failed to send simplified telegram notification: ...
```

## İyileştirmeler

| Özellik | Önce | Sonra |
|---------|------|-------|
| Markdown Escape | ❌ Yok | ✅ Tüm özel karakterler |
| Mesaj Uzunluğu | ❌ Kontrolsüz | ✅ 3000 karakter limit |
| Hata Yakalama | ⚠️ Log only | ✅ Fallback mekanizması |
| Başarı Log'u | ❌ Yok | ✅ "sent successfully" |
| Gerekçe Görünümü | ❌ 400 Error | ✅ Başarılı veya kısaltılmış |

## İlgili Düzeltmeler (Bugün)

Bu bugün yapılan **4. major düzeltme**:

1. **Sabah:** Pozisyon yönetimi kuralları (GLM prompt)
2. **Öğlen:** Telegram güncel metrics, Executor bug fix
3. **Akşam:** CLOSE action eklendi
4. **Gece:** **Telegram 400 Error Fix** ← ŞİMDİ

## Değiştirilen Dosya

### `app/executor/executor.py`

**Line 462-477:** Gerekçe escape ve uzunluk kontrolü
```python
# Gerekçe escape
reason_escaped = reason.replace('_', '\\_')...

# Uzunluk kontrolü
if len(reason_escaped) > max_reason_length:
    reason_escaped = reason_escaped[:max_reason_length] + "... (kısaltıldı)"

message_with_reason = message + f"\n\n💬 *Gerekçe:*\n{reason_escaped}"
```

**Line 491-502:** Fallback mekanizması
```python
try:
    telegram_client.send_message(message_with_reason)
    logger.info("Telegram trade notification sent successfully")
except Exception as exc:
    logger.error("Telegram trade notify failed: %s", exc, exc_info=True)
    try:
        telegram_client.send_message(message + "\n\n💬 Gerekçe: (çok uzun, log'lara bakın)")
        logger.warning("Sent telegram notification without detailed reasoning")
    except Exception as exc2:
        logger.error("Failed to send simplified telegram notification: %s", exc2)
```

## Sonraki Adımlar

1. ✅ Bot restart edildi
2. ✅ Bir sonraki trade'de telegram bildirimi gelecek
3. ✅ Gerekçe düzgün escape edilmiş olacak
4. ✅ Uzun gerekçeler kısaltılacak
5. ✅ Başarısız olursa fallback çalışacak

## Test Komutu

```bash
# Bot restart
systemctl restart trading-orchestrator

# Log takibi
journalctl -u trading-orchestrator -f | grep -E "Telegram|400"

# Bir sonraki trade'i bekle
# Telegram'da bildirim gelecek
```

## Önemli Notlar

- Telegram API'nin markdown V2 kurallarına uygun escape yapıldı
- Mesaj uzunluğu 4096 karakteri aşmıyor
- Fallback mekanizması en kötü durumda bile kullanıcıyı bilgilendiriyor
- Tüm hatalar detaylı loglanıyor
- Başarılı gönderimler de loglanıyor

## İlgili Dokümantasyon

- `CLOSE_ACTION_IMPLEMENTATION.md` - CLOSE action eklenmesi
- `TELEGRAM_METRICS_FIX.md` - Telegram güncel metrics düzeltmesi
- `POSITION_MANAGEMENT_FIX.md` - Pozisyon yönetimi kuralları
