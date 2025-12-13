from __future__ import annotations

from typing import Dict, List


def evaluate_hard_veto(action: str, current_snapshots: Dict[str, Dict]) -> List[str]:
    """
    Deterministic hard veto checks for BUY/SELL decisions.

    Returns a list of veto reasons. If non-empty, the caller should force HOLD.
    """
    action = (action or "").upper()
    if action not in {"BUY", "SELL"}:
        return []

    current_snapshots = current_snapshots or {}
    snapshot_1d = current_snapshots.get("1d") or {}
    snapshot_4h = current_snapshots.get("4h") or {}
    snapshot_15m = current_snapshots.get("15m") or {}

    d1_close = float(snapshot_1d.get("close") or 0)
    d1_ema50 = float(snapshot_1d.get("ema_50") or 0)
    d1_rsi = snapshot_1d.get("rsi_14")

    h4_close = float(snapshot_4h.get("close") or 0)
    h4_ema20 = float(snapshot_4h.get("ema_20") or 0)

    m15_rsi = snapshot_15m.get("rsi_14")

    reasons: List[str] = []

    if d1_close <= 0 or d1_ema50 <= 0 or d1_rsi is None:
        reasons.append("1D close/EMA50/RSI eksik (HTF veto kontrolü)")
    else:
        d1_rsi_val = float(d1_rsi)
        if action == "BUY" and d1_close < d1_ema50 and d1_rsi_val < 60:
            reasons.append("HTF veto: 1D close < EMA50 ve 1D RSI < 60 (LONG yasak)")
        if action == "SELL" and d1_close > d1_ema50 and d1_rsi_val > 40:
            reasons.append("HTF veto: 1D close > EMA50 ve 1D RSI > 40 (SHORT yasak)")

    if h4_close <= 0 or h4_ema20 <= 0:
        reasons.append("4H close/EMA20 eksik (teyit)")
    else:
        if action == "BUY" and h4_close <= h4_ema20:
            reasons.append("Teyit başarısız: 4H close EMA20 üstünde değil (LONG)")
        if action == "SELL" and h4_close >= h4_ema20:
            reasons.append("Teyit başarısız: 4H close EMA20 altında değil (SHORT)")

    if m15_rsi is None:
        reasons.append("15m RSI eksik (teyit)")
    else:
        m15_rsi_val = float(m15_rsi)
        if action == "BUY" and m15_rsi_val <= 50:
            reasons.append("Teyit başarısız: 15m RSI ≤ 50 (LONG)")
        if action == "SELL" and m15_rsi_val >= 50:
            reasons.append("Teyit başarısız: 15m RSI ≥ 50 (SHORT)")

    return reasons
