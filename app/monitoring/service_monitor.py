"""
Service Monitoring Module
Monitors enriched feed services and data freshness, sends Telegram notifications
"""

import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from app.config.settings import get_settings
from app.utils.influx import _ensure_client, query_latest_snapshot
from app.utils.logging import get_logger
from app.utils.telegram import format_markdown, telegram_client

logger = get_logger(__name__)
settings = get_settings()

# Servis eşleştirmeleri: (servis_adı, measurement, interval, threshold_seconds)
ENRICHED_FEED_SERVICES = {
    "1m": {
        "service_name": "trading-enriched-feed-1m",
        "measurement": "enriched_1m",
        "interval": "1m",
        "threshold_seconds": 300,  # 5 dakika
    },
    "15min": {
        "service_name": "trading-enriched-feed-15min",
        "measurement": "enriched_15min",
        "interval": "15min",
        "threshold_seconds": 1200,  # 20 dakika
    },
    "30min": {
        "service_name": "trading-enriched-feed",
        "measurement": "enriched_30min",
        "interval": "30min",
        "threshold_seconds": 2400,  # 40 dakika
    },
    "4h": {
        "service_name": "trading-enriched-feed-4h",
        "measurement": "enriched_4h",
        "interval": "4h",
        "threshold_seconds": 18000,  # 5 saat
    },
}

# Bildirim durumu takibi (spam önlemek için)
_notification_state: Dict[str, Dict[str, Any]] = {}


def check_service_status(service_name: str) -> bool:
    """
    Systemd servisinin aktif olup olmadığını kontrol et

    Args:
        service_name: Systemd servis adı

    Returns:
        True eğer servis aktifse, False değilse
    """
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() == "active"
    except Exception as exc:
        logger.error("Failed to check service status for %s: %s", service_name, exc)
        return False


def check_data_freshness(
    measurement: str,
    interval: str,
    threshold_seconds: int,
    symbol: str = "BTCUSDT",
) -> Tuple[bool, Optional[datetime]]:
    """
    Verinin taze olup olmadığını kontrol et

    Args:
        measurement: InfluxDB measurement adı
        interval: Zaman aralığı (1m, 30min, 4h)
        threshold_seconds: Tazelik eşiği (saniye)
        symbol: Trading sembolü

    Returns:
        (is_fresh, last_timestamp) tuple
    """
    try:
        snapshot = query_latest_snapshot(measurement, symbol, interval)
        if not snapshot:
            logger.warning("No data found for %s/%s/%s", measurement, symbol, interval)
            return False, None

        timestamp_str = snapshot.get("timestamp")
        if not timestamp_str:
            return False, None

        # Parse timestamp
        try:
            if isinstance(timestamp_str, str):
                last_timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            else:
                last_timestamp = timestamp_str
        except Exception:
            logger.warning("Failed to parse timestamp: %s", timestamp_str)
            return False, None

        # UTC'ye normalize et
        if last_timestamp.tzinfo is None:
            last_timestamp = last_timestamp.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        age_seconds = (now - last_timestamp).total_seconds()

        is_fresh = age_seconds <= threshold_seconds

        logger.debug(
            "Data freshness check: %s/%s - age=%ds threshold=%ds fresh=%s",
            measurement,
            interval,
            int(age_seconds),
            threshold_seconds,
            is_fresh,
        )

        return is_fresh, last_timestamp

    except Exception as exc:
        logger.error("Failed to check data freshness for %s/%s: %s", measurement, interval, exc)
        return False, None


def verify_htf_data(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """
    HTF veri yapısını ve tazeliğini doğrula

    Üç kontrol yapar:
    1. HTF filtresi çalışıyor mu (detect_htf_support_resistance test)
    2. enriched_15min verisi var mı ve taze mi (HTF için)
    3. enriched_4h verisi var mı ve taze mi

    Args:
        symbol: Trading sembolü

    Returns:
        Doğrulama sonuçları dict'i
    """
    results = {
        "htf_filter_working": False,
        "htf_filter_error": None,
        "enriched_15min_exists": False,
        "enriched_15min_fresh": False,
        "enriched_15min_timestamp": None,
        "enriched_15min_age_minutes": None,
        "enriched_15min_structure_valid": False,
        "enriched_15min_missing_fields": [],
        "enriched_15min_error": None,
        "enriched_4h_exists": False,
        "enriched_4h_fresh": False,
        "enriched_4h_timestamp": None,
        "enriched_4h_age_hours": None,
        "enriched_4h_structure_valid": False,
        "enriched_4h_missing_fields": [],
        "enriched_4h_error": None,
        "longterm_4h_usable": False,
    }

    # 1. HTF Filtresi Kontrolü
    try:
        from app.utils.influx import detect_htf_support_resistance

        htf_result = detect_htf_support_resistance(symbol)
        if htf_result.get("status") == "success":
            results["htf_filter_working"] = True
            logger.debug("HTF filter test passed: %s", htf_result.get("htf_interval", "unknown"))
        else:
            results["htf_filter_error"] = htf_result.get("error", "Unknown error")
            logger.warning("HTF filter test failed: %s", results["htf_filter_error"])
    except Exception as exc:
        results["htf_filter_error"] = str(exc)
        logger.error("HTF filter test exception: %s", exc)

    # 2. enriched_15min Verisi Kontrolü (HTF için)
    try:
        snapshot_15min = query_latest_snapshot("enriched_15min", symbol, "15min")

        if snapshot_15min:
            results["enriched_15min_exists"] = True

            # Timestamp kontrolü
            timestamp_str = snapshot_15min.get("timestamp")
            if timestamp_str:
                try:
                    if isinstance(timestamp_str, str):
                        last_timestamp = datetime.fromisoformat(
                            timestamp_str.replace("Z", "+00:00")
                        )
                    else:
                        last_timestamp = timestamp_str

                    if last_timestamp.tzinfo is None:
                        last_timestamp = last_timestamp.replace(tzinfo=timezone.utc)

                    now = datetime.now(timezone.utc)
                    age_minutes = (now - last_timestamp).total_seconds() / 60

                    # 15min için 20 dakika threshold
                    results["enriched_15min_fresh"] = age_minutes <= 20
                    results["enriched_15min_timestamp"] = last_timestamp.isoformat()
                    results["enriched_15min_age_minutes"] = round(age_minutes, 2)

                except Exception as exc:
                    logger.warning("Failed to parse enriched_15min timestamp: %s", exc)
                    results["enriched_15min_error"] = f"Timestamp parse error: {str(exc)}"

            # Veri yapısı kontrolü - temel alanlar
            required_fields = ["close", "high", "low", "volume"]
            missing_fields = []

            for field in required_fields:
                if field not in snapshot_15min:
                    missing_fields.append(f"{field} (missing)")
                    logger.debug(
                        "enriched_15min field '%s' not found in snapshot keys: %s",
                        field,
                        list(snapshot_15min.keys()),
                    )
                elif snapshot_15min.get(field) is None:
                    missing_fields.append(f"{field} (None)")
                    logger.debug("enriched_15min field '%s' is None", field)
                else:
                    logger.debug(
                        "enriched_15min field '%s': %s (type: %s)",
                        field,
                        snapshot_15min[field],
                        type(snapshot_15min[field]).__name__,
                    )

            results["enriched_15min_missing_fields"] = missing_fields

            if not missing_fields:
                results["enriched_15min_structure_valid"] = True
                logger.debug("enriched_15min structure valid: all required fields present")
            else:
                logger.warning("enriched_15min missing fields: %s", missing_fields)
        else:
            logger.warning("enriched_15min measurement not found or empty")

    except Exception as exc:
        logger.error("Failed to verify enriched_15min data: %s", exc)
        results["enriched_15min_error"] = str(exc)

    # 3. Longterm 4h Verisi Kontrolü
    try:
        snapshot = query_latest_snapshot("enriched_4h", symbol, "4h")

        if snapshot:
            results["enriched_4h_exists"] = True

            # Timestamp kontrolü
            timestamp_str = snapshot.get("timestamp")
            if timestamp_str:
                try:
                    if isinstance(timestamp_str, str):
                        last_timestamp = datetime.fromisoformat(
                            timestamp_str.replace("Z", "+00:00")
                        )
                    else:
                        last_timestamp = timestamp_str

                    if last_timestamp.tzinfo is None:
                        last_timestamp = last_timestamp.replace(tzinfo=timezone.utc)

                    now = datetime.now(timezone.utc)
                    age_hours = (now - last_timestamp).total_seconds() / 3600

                    # 4h için 5 saat threshold
                    results["enriched_4h_fresh"] = age_hours <= 5
                    results["enriched_4h_timestamp"] = last_timestamp.isoformat()
                    results["enriched_4h_age_hours"] = round(age_hours, 2)

                except Exception as exc:
                    logger.warning("Failed to parse enriched_4h timestamp: %s", exc)
                    results["enriched_4h_error"] = f"Timestamp parse error: {str(exc)}"

            # Veri yapısı kontrolü - beklenen alanlar
            required_fields = ["ema_20", "ema_50", "rsi_14", "macd", "atr_14", "volume"]
            missing_fields = []

            for field in required_fields:
                if field not in snapshot:
                    missing_fields.append(f"{field} (missing)")
                    logger.debug(
                        "enriched_4h field '%s' not found in snapshot keys: %s",
                        field,
                        list(snapshot.keys()),
                    )
                elif snapshot.get(field) is None:
                    missing_fields.append(f"{field} (None)")
                    logger.debug("enriched_4h field '%s' is None", field)

            results["enriched_4h_missing_fields"] = missing_fields

            if not missing_fields:
                results["enriched_4h_structure_valid"] = True
                results["longterm_4h_usable"] = True
                logger.debug("enriched_4h structure valid: all required fields present")
            else:
                logger.warning("enriched_4h missing fields: %s", missing_fields)
        else:
            logger.warning("enriched_4h measurement not found or empty")

    except Exception as exc:
        logger.error("Failed to verify enriched_4h data: %s", exc)
        results["enriched_4h_error"] = str(exc)

    return results


def monitor_enriched_feeds(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """
    Tüm enriched feed servislerini izle

    Args:
        symbol: Trading sembolü

    Returns:
        İzleme sonuçları dict'i
    """
    results = {}

    for key, config in ENRICHED_FEED_SERVICES.items():
        service_name = config["service_name"]
        measurement = config["measurement"]
        interval = config["interval"]
        threshold = config["threshold_seconds"]

        # Servis durumu kontrolü
        service_active = check_service_status(service_name)

        # Veri tazeliği kontrolü
        data_fresh, last_timestamp = check_data_freshness(measurement, interval, threshold, symbol)

        results[key] = {
            "service_name": service_name,
            "measurement": measurement,
            "interval": interval,
            "service_active": service_active,
            "data_fresh": data_fresh,
            "last_timestamp": last_timestamp.isoformat() if last_timestamp else None,
            "threshold_seconds": threshold,
        }

        # Sorun tespiti
        issue_detected = not service_active or not data_fresh

        if issue_detected:
            logger.warning(
                "⚠️ Issue detected for %s: service_active=%s data_fresh=%s",
                service_name,
                service_active,
                data_fresh,
            )

            # Servis inactive ise otomatik yeniden başlatmayı dene
            if not service_active:
                logger.info(
                    "🔄 Service %s is inactive, attempting automatic restart...", service_name
                )
                restart_success = restart_service(service_name)
                if restart_success:
                    # Yeniden başlatıldıktan sonra durumu tekrar kontrol et
                    time.sleep(5)  # Servisin başlaması için kısa bekleme
                    service_active = check_service_status(service_name)
                    if service_active:
                        logger.info("✅ Service %s restarted successfully", service_name)
                        # Servis başarıyla başlatıldıysa durumu güncelle ve bildirim gönderme
                        results[key]["service_active"] = True
                        issue_detected = not data_fresh  # Sadece veri kontrolü kaldı
                        if not issue_detected:
                            # Sorun tamamen çözüldüyse bildirim durumunu temizle
                            clear_notification_state(key)
                            continue
                    else:
                        logger.warning("⚠️ Service %s restart failed - still inactive", service_name)
                else:
                    logger.error("❌ Failed to restart service %s", service_name)

        # Bildirim gönder (deduplication ile)
        if issue_detected:
            should_notify = should_send_notification(
                key, service_active, data_fresh, last_timestamp
            )
            if should_notify:
                send_notification(key, config, service_active, data_fresh, last_timestamp)
        else:
            # Sorun çözüldüyse bildirim durumunu temizle
            clear_notification_state(key)

    return results


def restart_service(service_name: str) -> bool:
    """
    Systemd servisini yeniden başlat

    Args:
        service_name: Systemd servis adı

    Returns:
        True eğer başarılıysa, False değilse
    """
    try:
        logger.info("Attempting to restart service: %s", service_name)
        result = subprocess.run(
            ["systemctl", "restart", service_name],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            logger.info("✅ Successfully restarted service: %s", service_name)
            return True
        else:
            logger.error("Failed to restart service %s: %s", service_name, result.stderr)
            return False
    except Exception as exc:
        logger.error("Exception while restarting service %s: %s", service_name, exc)
        return False


def should_send_notification(
    service_key: str,
    service_active: bool,
    data_fresh: bool,
    last_timestamp: Optional[datetime],
) -> bool:
    """
    Bildirim gönderilmeli mi kontrol et (deduplication)

    Args:
        service_key: Servis anahtarı (1m, 30min, 4h)
        service_active: Servis aktif mi
        data_fresh: Veri taze mi
        last_timestamp: Son veri zamanı

    Returns:
        True eğer bildirim gönderilmeli
    """
    global _notification_state

    state_key = service_key
    current_state = {
        "service_active": service_active,
        "data_fresh": data_fresh,
        "last_timestamp": last_timestamp.isoformat() if last_timestamp else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Önceki durum varsa karşılaştır
    if state_key in _notification_state:
        prev_state = _notification_state[state_key]

        # Eğer durum değişmemişse ve son bildirimden 30 dakikadan az geçtiyse bildirim gönderme
        if (
            prev_state.get("service_active") == service_active
            and prev_state.get("data_fresh") == data_fresh
        ):
            prev_time = datetime.fromisoformat(prev_state.get("timestamp", ""))
            if prev_time.tzinfo is None:
                prev_time = prev_time.replace(tzinfo=timezone.utc)
            time_diff = (datetime.now(timezone.utc) - prev_time).total_seconds()

            if time_diff < 1800:  # 30 dakika
                logger.debug(
                    "Skipping notification for %s (duplicate, sent %ds ago)",
                    service_key,
                    int(time_diff),
                )
                return False

    # Durumu güncelle
    _notification_state[state_key] = current_state
    return True


def clear_notification_state(service_key: str) -> None:
    """Bildirim durumunu temizle (sorun çözüldüyse)"""
    global _notification_state
    if service_key in _notification_state:
        del _notification_state[service_key]
        logger.debug("Cleared notification state for %s", service_key)


def send_notification(
    service_key: str,
    config: Dict[str, Any],
    service_active: bool,
    data_fresh: bool,
    last_timestamp: Optional[datetime],
) -> None:
    """
    Telegram bildirimi gönder

    Args:
        service_key: Servis anahtarı
        config: Servis yapılandırması
        service_active: Servis aktif mi
        data_fresh: Veri taze mi
        last_timestamp: Son veri zamanı
    """
    if not telegram_client.enabled():
        logger.debug("Telegram disabled, skipping notification")
        return

    service_name = config["service_name"]
    interval = config["interval"]

    # Sorun açıklaması
    issues = []
    if not service_active:
        issues.append("❌ Servis inactive")
    if not data_fresh:
        if last_timestamp:
            if isinstance(last_timestamp, str):
                last_timestamp_dt = datetime.fromisoformat(last_timestamp.replace("Z", "+00:00"))
            else:
                last_timestamp_dt = last_timestamp
            if last_timestamp_dt.tzinfo is None:
                last_timestamp_dt = last_timestamp_dt.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - last_timestamp_dt).total_seconds()
            age_minutes = int(age_seconds / 60)
            issues.append(f"⏰ Veri eski ({age_minutes} dakika önce)")
        else:
            issues.append("⏰ Veri bulunamadı")

    # Mesaj oluştur
    lines = [
        f"🚨 *Enriched Feed Servis Uyarısı*",
        "",
        f"*Servis:* `{service_name}`",
        f"*Interval:* {interval}",
        "",
        "*Durum:*",
        f"  • Servis: {'✅ Active' if service_active else '❌ Inactive'}",
        f"  • Veri: {'✅ Taze' if data_fresh else '❌ Eski/Eksik'}",
        "",
    ]

    if last_timestamp:
        lines.append(f"*Son Veri:* {last_timestamp.isoformat()}")
    else:
        lines.append("*Son Veri:* Bulunamadı")

    lines.extend(
        [
            "",
            "*Sorunlar:*",
        ]
    )

    for issue in issues:
        lines.append(f"  • {issue}")

    lines.extend(
        [
            "",
            "*Aksiyon:*",
            f"  • Durum kontrol: `systemctl status {service_name}`",
            f"  • Yeniden başlat: `systemctl restart {service_name}`",
            f"  • Log kontrol: `journalctl -u {service_name} -n 50`",
            "",
            f"🕒 {datetime.now(timezone.utc).isoformat()}",
        ]
    )

    message = "\n".join(lines)

    try:
        telegram_client.send_message(message)
        logger.info("Service notification sent for %s", service_name)
    except Exception as exc:
        logger.error("Failed to send Telegram notification: %s", exc)


def send_htf_verification_notification(
    htf_results: Dict[str, Any], symbol: str = "BTCUSDT"
) -> None:
    """
    HTF doğrulama sonuçlarını Telegram'a gönder

    Args:
        htf_results: verify_htf_data() sonuçları
        symbol: Trading sembolü
    """
    if not telegram_client.enabled():
        return

    lines = [
        "📊 *HTF Veri Doğrulama Raporu*",
        "",
        f"*Sembol:* {symbol}",
        "",
    ]

    # HTF Filtresi Durumu
    lines.append("*HTF Filtresi:*")
    if htf_results.get("htf_filter_working"):
        lines.append("  ✅ Çalışıyor")
    else:
        error = htf_results.get("htf_filter_error", "Bilinmeyen hata")
        lines.append(f"  ❌ Çalışmıyor: {error}")

    lines.append("")

    # enriched_15min Durumu (HTF için)
    lines.append("*enriched_15min Verisi (HTF):*")
    if htf_results.get("enriched_15min_exists"):
        lines.append("  ✅ Measurement mevcut")

        if htf_results.get("enriched_15min_fresh"):
            lines.append("  ✅ Veri taze")
        else:
            lines.append("  ⚠️ Veri eski")

        if htf_results.get("enriched_15min_structure_valid"):
            lines.append("  ✅ Veri yapısı geçerli")
        else:
            lines.append("  ❌ Veri yapısı eksik/hatalı")

        timestamp = htf_results.get("enriched_15min_timestamp")
        if timestamp:
            lines.append(f"  📅 Son güncelleme: {timestamp}")
    else:
        lines.append("  ❌ Measurement bulunamadı")

    lines.append("")

    # enriched_4h Durumu
    lines.append("*enriched_4h Verisi:*")
    if htf_results.get("enriched_4h_exists"):
        lines.append("  ✅ Measurement mevcut")

        if htf_results.get("enriched_4h_fresh"):
            lines.append("  ✅ Veri taze")
        else:
            lines.append("  ⚠️ Veri eski")

        if htf_results.get("enriched_4h_structure_valid"):
            lines.append("  ✅ Veri yapısı geçerli")
        else:
            lines.append("  ❌ Veri yapısı eksik/hatalı")

        if htf_results.get("longterm_4h_usable"):
            lines.append("  ✅ Longterm 4h kullanılabilir")
        else:
            lines.append("  ❌ Longterm 4h kullanılamaz")

        timestamp = htf_results.get("enriched_4h_timestamp")
        if timestamp:
            lines.append(f"  📅 Son güncelleme: {timestamp}")
    else:
        lines.append("  ❌ Measurement bulunamadı")

    lines.extend(
        [
            "",
            f"🕒 {datetime.now(timezone.utc).isoformat()}",
        ]
    )

    message = "\n".join(lines)

    try:
        telegram_client.send_message(message)
        logger.info("HTF verification notification sent")
    except Exception as exc:
        logger.error("Failed to send HTF verification notification: %s", exc)


def monitor_all(symbol: str = "BTCUSDT") -> Dict[str, Any]:
    """
    Tüm servisleri ve HTF verilerini izle

    Args:
        symbol: Trading sembolü

    Returns:
        Kapsamlı izleme sonuçları
    """
    logger.debug("Starting comprehensive service monitoring...")

    # Enriched feed servislerini izle
    feed_results = monitor_enriched_feeds(symbol)

    # HTF verilerini doğrula
    htf_results = verify_htf_data(symbol)

    # Özet bilgi logla
    active_count = sum(1 for r in feed_results.values() if r.get("service_active"))
    fresh_count = sum(1 for r in feed_results.values() if r.get("data_fresh"))

    logger.debug(
        "Monitoring complete: %d/%d services active, %d/%d fresh data",
        active_count,
        len(feed_results),
        fresh_count,
        len(feed_results),
    )

    # HTF durumunu logla
    if (
        htf_results.get("htf_filter_working")
        and htf_results.get("enriched_15min_structure_valid")
        and htf_results.get("longterm_4h_usable")
    ):
        logger.debug("✅ HTF data: filter working, 15min data usable, 4h data usable")
    else:
        logger.warning("⚠️ HTF data issues detected")

        # Detaylı sorun loglaması
        issues = []

        # HTF Filtresi Sorunları
        if not htf_results.get("htf_filter_working"):
            error_msg = htf_results.get("htf_filter_error", "Unknown error")
            logger.warning("  ❌ HTF Filter: Çalışmıyor - %s", error_msg)
            logger.warning(
                "     💡 Çözüm: enriched_15min servisini kontrol edin: systemctl status trading-enriched-feed-15min"
            )
            issues.append(f"HTF filter çalışmıyor: {error_msg}")

        # enriched_15min Sorunları
        if not htf_results.get("enriched_15min_exists"):
            logger.warning("  ❌ enriched_15min: Measurement bulunamadı")
            logger.warning(
                "     💡 Çözüm: enriched_15min servisini başlatın: systemctl restart trading-enriched-feed-15min"
            )
            issues.append("enriched_15min measurement bulunamadı")
        else:
            if not htf_results.get("enriched_15min_fresh"):
                age = htf_results.get("enriched_15min_age_minutes")
                if age is not None:
                    logger.warning("  ⚠️ enriched_15min: Veri eski (%.1f dakika önce)", age)
                    logger.warning(
                        "     💡 Çözüm: enriched_15min servisini kontrol edin: systemctl status trading-enriched-feed-15min"
                    )
                    issues.append(f"enriched_15min verisi eski ({age:.1f} dakika önce)")
                else:
                    logger.warning("  ⚠️ enriched_15min: Timestamp bilgisi eksik")
                    issues.append("enriched_15min timestamp bilgisi eksik")

            if not htf_results.get("enriched_15min_structure_valid"):
                missing_fields = htf_results.get("enriched_15min_missing_fields", [])
                if missing_fields:
                    logger.warning(
                        "  ❌ enriched_15min: Eksik alanlar - %s", ", ".join(missing_fields)
                    )
                    logger.warning(
                        "     💡 Çözüm: enriched_15min servisi veriyi düzgün işlemiyor. Logları kontrol edin: journalctl -u trading-enriched-feed-15min -n 50"
                    )
                    issues.append(f"enriched_15min eksik alanlar: {', '.join(missing_fields)}")
                else:
                    logger.warning("  ❌ enriched_15min: Veri yapısı geçersiz")
                    issues.append("enriched_15min veri yapısı geçersiz")

            error = htf_results.get("enriched_15min_error")
            if error:
                logger.warning("  ❌ enriched_15min: Hata - %s", error)
                logger.warning(
                    "     💡 Çözüm: enriched_15min servisi hatası. Logları kontrol edin: journalctl -u trading-enriched-feed-15min -n 50"
                )
                issues.append(f"enriched_15min hatası: {error}")

        # enriched_4h Sorunları
        if not htf_results.get("enriched_4h_exists"):
            logger.warning("  ❌ enriched_4h: Measurement bulunamadı")
            logger.warning(
                "     💡 Çözüm: enriched_4h servisini başlatın: systemctl restart trading-enriched-feed-4h"
            )
            issues.append("enriched_4h measurement bulunamadı")
        else:
            if not htf_results.get("enriched_4h_fresh"):
                age = htf_results.get("enriched_4h_age_hours")
                if age is not None:
                    logger.warning("  ⚠️ enriched_4h: Veri eski (%.1f saat önce)", age)
                    logger.warning(
                        "     💡 Çözüm: enriched_4h servisini kontrol edin: systemctl status trading-enriched-feed-4h"
                    )
                    issues.append(f"enriched_4h verisi eski ({age:.1f} saat önce)")
                else:
                    logger.warning("  ⚠️ enriched_4h: Timestamp bilgisi eksik")
                    issues.append("enriched_4h timestamp bilgisi eksik")

            if not htf_results.get("enriched_4h_structure_valid"):
                missing_fields = htf_results.get("enriched_4h_missing_fields", [])
                if missing_fields:
                    logger.warning(
                        "  ❌ enriched_4h: Eksik alanlar - %s", ", ".join(missing_fields)
                    )
                    logger.warning(
                        "     💡 Çözüm: enriched_4h servisi veriyi düzgün işlemiyor. Logları kontrol edin: journalctl -u trading-enriched-feed-4h -n 50"
                    )
                    issues.append(f"enriched_4h eksik alanlar: {', '.join(missing_fields)}")
                else:
                    logger.warning("  ❌ enriched_4h: Veri yapısı geçersiz")
                    issues.append("enriched_4h veri yapısı geçersiz")

            if not htf_results.get("longterm_4h_usable"):
                logger.warning("  ❌ enriched_4h: Longterm 4h kullanılamaz")
                logger.warning(
                    "     💡 Çözüm: enriched_4h veri yapısı eksik veya geçersiz. Servisi yeniden başlatın: systemctl restart trading-enriched-feed-4h"
                )
                issues.append("enriched_4h longterm kullanılamaz")

            error = htf_results.get("enriched_4h_error")
            if error:
                logger.warning("  ❌ enriched_4h: Hata - %s", error)
                logger.warning(
                    "     💡 Çözüm: enriched_4h servisi hatası. Logları kontrol edin: journalctl -u trading-enriched-feed-4h -n 50"
                )
                issues.append(f"enriched_4h hatası: {error}")

        # Özet sorun mesajı
        if issues:
            logger.warning("⚠️ HTF Sorun Özeti: %s", " | ".join(issues))

    return {
        "feed_monitoring": feed_results,
        "htf_verification": htf_results,
        "summary": {
            "total_services": len(feed_results),
            "active_services": active_count,
            "fresh_data_count": fresh_count,
            "htf_filter_working": htf_results.get("htf_filter_working", False),
            "htf_15min_usable": htf_results.get("enriched_15min_structure_valid", False),
            "htf_4h_usable": htf_results.get("longterm_4h_usable", False),
        },
    }
