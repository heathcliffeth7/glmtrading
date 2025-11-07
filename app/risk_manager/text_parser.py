import re
from typing import Any, Dict, List


def _parse_float(s: str, default: float = 0.0) -> float:
    try:
        return float(s)
    except Exception:
        return default


def _parse_array(line: str) -> List[float]:
    # Extract numbers inside [ ... ]
    m = re.search(r"\[([^\]]*)\]", line)
    if not m:
        return []
    body = m.group(1)
    parts = [p.strip() for p in body.split(",") if p.strip()]
    result: List[float] = []
    for p in parts:
        # Remove any non-number trailing chars
        p = re.sub(r"[^0-9eE+\-\.]+", "", p)
        if p:
            try:
                result.append(float(p))
            except Exception:
                continue
    return result


def parse_state_payload(payload: str) -> Dict[str, Any]:
    """
    Parse an external state payload text and convert to AgentSignal metadata structure
    expected by RiskManager._build_prompt.

    Returns a dict with keys: historical_data, feature_snapshot.
    """
    text = payload or ""
    lines = text.splitlines()

    # Defaults
    current_price = 0.0
    current_ema20 = 0.0
    current_macd = 0.0
    current_rsi14 = 50.0

    intraday_close: List[float] = []
    intraday_ema20: List[float] = []
    intraday_macd: List[float] = []
    intraday_rsi7: List[float] = []
    intraday_rsi14: List[float] = []

    fourh_ema20 = 0.0
    fourh_ema50 = 0.0
    fourh_atr14 = 0.0
    fourh_volume = 0.0
    fourh_macd: List[float] = []
    fourh_rsi14: List[float] = []

    feature_snapshot: Dict[str, Any] = {}

    # Regex helpers
    re_kv = lambda key: re.compile(rf"{key}\s*=\s*([\-0-9.eE]+)")
    re_line_contains = lambda pat: re.compile(pat, re.IGNORECASE)

    # First pass: simple key=value
    for ln in lines:
        if "current_price" in ln:
            m = re_kv("current_price").search(ln)
            if m:
                current_price = _parse_float(m.group(1), current_price)
        if "current_ema20" in ln:
            m = re_kv("current_ema20").search(ln)
            if m:
                current_ema20 = _parse_float(m.group(1), current_ema20)
        if "current_macd" in ln:
            m = re_kv("current_macd").search(ln)
            if m:
                current_macd = _parse_float(m.group(1), current_macd)
        if "current_rsi" in ln and "14" in ln:
            # e.g., current_rsi (14 period) = 55.296
            m = re.search(r"current_rsi\s*\([^)]*14[^)]*\)\s*=\s*([\-0-9.eE]+)", ln)
            if m:
                current_rsi14 = _parse_float(m.group(1), current_rsi14)

        # Open Interest / Funding
        if "Open Interest:" in ln:
            # Open Interest: Latest: 28623.66 Average: 28626.97
            m = re.search(r"Latest:\s*([\-0-9.eE]+)", ln)
            if m:
                feature_snapshot["open_interest"] = _parse_float(m.group(1))
        if "Funding Rate:" in ln:
            m = re.search(r"Funding Rate:\s*([\-0-9.eE]+)", ln)
            if m:
                feature_snapshot["funding_rate"] = _parse_float(m.group(1))

        # 4h context line values
        if "20" in ln and "EMA" in ln and "50" in ln and "vs" in ln:
            # 20‑Period EMA: 111513.71 vs. 50‑Period EMA: 110753.453
            m = re.search(r"20[^:]*EMA:\s*([\-0-9.eE]+).*?50[^:]*EMA:\s*([\-0-9.eE]+)", ln)
            if m:
                fourh_ema20 = _parse_float(m.group(1), fourh_ema20)
                fourh_ema50 = _parse_float(m.group(2), fourh_ema50)
        if "14-Period ATR" in ln or "14‑Period ATR" in ln:
            m = re.search(r"14[^A]*ATR:\s*([\-0-9.eE]+)", ln)
            if m:
                fourh_atr14 = _parse_float(m.group(1), fourh_atr14)
        if "Current Volume:" in ln:
            m = re.search(r"Current Volume:\s*([\-0-9.eE]+)", ln)
            if m:
                fourh_volume = _parse_float(m.group(1), fourh_volume)

    # Second pass: arrays
    for ln in lines:
        l = ln.strip()
        if l.lower().startswith("mid prices:"):
            intraday_close = _parse_array(l)
        elif l.lower().startswith("ema indicators"):
            intraday_ema20 = _parse_array(l)
        elif l.lower().startswith("macd indicators"):
            intraday_macd = _parse_array(l)
        elif l.lower().startswith("rsi indicators (7"):
            intraday_rsi7 = _parse_array(l)
        elif l.lower().startswith("rsi indicators (14"):
            intraday_rsi14 = _parse_array(l)
        elif l.lower().startswith("macd indicators:") and "4-hour" in text:
            # This condition is too broad, 1m also uses MACD indicators label. Handled above.
            pass
        # 4h arrays
        elif l.lower().startswith("macd indicators:"):
            # Might be 4h context block; disambiguate by checking if we've already set intraday_macd
            # We'll assign to fourh_macd only if this line appears after a 'Longer-term context' header
            pass

    # More robust 4h arrays via section search
    fourh_section = re.search(
        r"Longer[^\n]*4\-hour[^\n]*:\s*(.*?)(?:(?:\n\n)|\Z)", text, re.IGNORECASE | re.DOTALL
    )
    if fourh_section:
        sec = fourh_section.group(1)
        # MACD indicators: [ ... ]
        m = re.search(r"MACD indicators:\s*\[([^\]]*)\]", sec, re.IGNORECASE)
        if m:
            fourh_macd = _parse_array("[" + m.group(1) + "]")
        m = re.search(r"RSI indicators\s*\(14[^)]*\):\s*\[([^\]]*)\]", sec, re.IGNORECASE)
        if m:
            fourh_rsi14 = _parse_array("[" + m.group(1) + "]")

    # If 14-period intraday RSI not found, try to use current_rsi14
    if not intraday_rsi14 and current_rsi14:
        intraday_rsi14 = [current_rsi14]

    # Build historical data structure
    historical_data: Dict[str, Any] = {
        "intraday_1m": {
            "close": intraday_close,
            "ema_20": intraday_ema20,
            "macd": intraday_macd,
            "rsi_14": intraday_rsi14,
        },
        # Provide minimal main_30min using current values so GLM has non-zero context
        "main_30min": {
            "close": [current_price] if current_price else [],
            "ema_20": [current_ema20] if current_ema20 else [],
            "ema_50": [],
            "macd": [current_macd] if current_macd else [],
            "rsi_14": [intraday_rsi14[-1]] if intraday_rsi14 else [],
        },
        "longterm_4h": {
            "ema_20": fourh_ema20,
            "ema_50": fourh_ema50,
            "volume": fourh_volume,
            "atr_14": fourh_atr14,
            "macd": fourh_macd,
            "rsi_14": fourh_rsi14,
        },
    }

    return {
        "historical_data": historical_data,
        "feature_snapshot": feature_snapshot,
    }
