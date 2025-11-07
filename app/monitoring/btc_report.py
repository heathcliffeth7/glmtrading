#!/usr/bin/env python3
"""
BTC single-asset report generator

Produces a JSON and/or text report aligned with the external format:
- ALL BTC DATA: current values + intraday arrays (oldest→newest) and 4h context
- Futures metrics (OI, Funding)
- Account & Performance (Available Cash, Account Value, Total Return %, Position)

Usage:
  python -m app.monitoring.btc_report --text
  python -m app.monitoring.btc_report --json
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from sqlalchemy.orm import Session

from app.executor.executor import Executor
from app.executor.ledger import Portfolio, engine
from app.utils.influx import query_historical_snapshots, query_latest_snapshot

SYMBOL = "BTCUSDT"


def _get_arrays(
    measurement: str, interval: str, fields: List[str], limit: int = 10
) -> Dict[str, List[float]]:
    snaps = query_historical_snapshots(measurement, SYMBOL, interval, limit=limit)
    arrays: Dict[str, List[float]] = {k: [] for k in fields}
    for s in snaps:
        for f in fields:
            v = s.get(f)
            if v is not None:
                try:
                    arrays[f].append(float(v))
                except (TypeError, ValueError):
                    arrays[f].append(0.0)
            else:
                arrays[f].append(0.0)
    return arrays


def _get_latest(measurement: str, interval: str) -> Dict[str, Any]:
    snap = query_latest_snapshot(measurement, SYMBOL, interval)
    return snap or {}


def _effective_leverage(exposure: float, margin_used: float) -> float:
    eps = 1e-9
    if margin_used <= eps:
        return 0.0
    return abs(exposure) / max(margin_used, eps)


def _estimate_liquidation_price(
    entry: float, qty: float, eff_lev: float, mmr: float = 0.004
) -> float | None:
    """
    Approximate liquidation price for linear USDT-M futures ignoring fees/funding.
    Long:  Liq ≈ entry * (1 - 1/lev + mmr)
    Short: Liq ≈ entry * (1 + 1/lev - mmr)
    Returns None when no position or insufficient params.
    """
    if entry <= 0 or abs(qty) < 1e-9 or eff_lev <= 0:
        return None
    if qty > 0:  # LONG
        return entry * max(0.0, (1.0 - (1.0 / eff_lev) + mmr))
    else:  # SHORT
        return entry * (1.0 + (1.0 / eff_lev) - mmr)


def _suggest_tp_sl(
    entry: float, qty: float, atr: float | None, ema50: float | None
) -> Dict[str, Any]:
    """
    Suggest TP/SL based on ATR14 (30m). Defaults: TP = entry ± 1.5*ATR, SL = entry ∓ 1.0*ATR.
    Also include an invalidation condition using EMA50 (30m).
    """
    if entry <= 0 or abs(qty) < 1e-9:
        return {}
    atr = float(atr or 0.0)
    k_tp, k_sl = 1.5, 1.0
    if atr <= 0:
        # Fallback to 1% bands if ATR missing
        atr = entry * 0.01
    if qty > 0:  # LONG
        tp = entry + k_tp * atr
        sl = max(0.0, entry - k_sl * atr)
        inval = (
            "If the price closes below EMA50 (30m)"
            if ema50 and ema50 > 0
            else "If 30m closes below prior swing low"
        )
    else:  # SHORT
        tp = max(0.0, entry - k_tp * atr)
        sl = entry + k_sl * atr
        inval = (
            "If the price closes above EMA50 (30m)"
            if ema50 and ema50 > 0
            else "If 30m closes above prior swing high"
        )
    return {"profit_target": tp, "stop_loss": sl, "invalidation_condition": inval}


def _compute_sharpe_from_prices(prices: List[float]) -> float | None:
    """Compute annualized Sharpe from 30m close prices (log returns)."""
    import math

    if not prices or len(prices) < 5:
        return None
    rets: List[float] = []
    for i in range(1, len(prices)):
        p0, p1 = prices[i - 1], prices[i]
        if p0 and p1 and p0 > 0 and p1 > 0:
            rets.append(math.log(p1 / p0))
    if len(rets) < 3:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(max(var, 1e-18))
    if std == 0:
        return 0.0
    # Annualize: 48 bars/day * 252 days ≈ 12096 bars/year
    ann_factor = math.sqrt(48 * 252)
    return (mean / std) * ann_factor


def generate_btc_report() -> Tuple[Dict[str, Any], str]:
    # Portfolio/account metrics
    exe = Executor(symbol=SYMBOL)
    pm = exe.portfolio_metrics()
    price = pm.get("price", 0.0)
    equity = pm.get("equity", 10000.0)
    free_cash = pm.get("free_cash", equity)
    realized = pm.get("realized_pnl", 0.0)
    unrealized = pm.get("unrealized_pnl", 0.0)
    exposure = pm.get("exposure", 0.0)
    margin_used = pm.get("margin_used", 0.0)
    eff_lev = _effective_leverage(exposure, margin_used)
    total_return_pct = ((equity - 10000.0) / 10000.0) * 100.0
    pos_qty = pm.get("position", 0.0)
    avg_price = pm.get("average_price", 0.0)

    # Real PnL hesaplama - son işlemlerden
    recent_trades = exe.get_recent_trades_with_pnl(limit=5)
    total_fees = 0.0
    total_gross_pnl = 0.0
    total_net_pnl = 0.0

    for trade in recent_trades:
        if trade.get("is_closed", False):
            # Kapanmış işlemler için Real PnL bilgilerini topla
            real_pnl = trade.get("real_pnl", {})
            if real_pnl:
                total_fees += real_pnl.get("fees", 0.0)
                total_gross_pnl += real_pnl.get("pnl_gross", 0.0)
                total_net_pnl += real_pnl.get("pnl_net", 0.0)

    # Real PnL toplamı (brüt PnL - toplam fees)
    real_total_pnl = total_gross_pnl - total_fees if total_gross_pnl > 0 else realized

    # Intraday (1m) arrays → downsample to 3m (10 points)
    arr_1m_full = _get_arrays(
        measurement="enriched_1m",
        interval="1m",
        fields=["close", "ema_20", "macd", "rsi_7", "rsi_14"],
        limit=30,
    )

    def _downsample_3m(d: Dict[str, List[float]]) -> Dict[str, List[float]]:
        out: Dict[str, List[float]] = {}
        for k, v in d.items():
            out[k] = v[::3] if v else []
        return out

    arr_1m = _downsample_3m(arr_1m_full)

    # Main 30m snapshot
    snap_30m = _get_latest("enriched_30min", "30min")

    # 4h context
    snap_4h = _get_latest("enriched_4h", "4h")
    arr_4h_macd = _get_arrays("enriched_4h", "4h", ["macd"], limit=10).get("macd", [])
    arr_4h_rsi = _get_arrays("enriched_4h", "4h", ["rsi_14"], limit=10).get("rsi_14", [])
    # For Sharpe (price-based): last 30m closes
    arr_30m_close = _get_arrays("enriched_30min", "30min", ["close"], limit=50).get("close", [])

    # Futures metrics: latest + OI average via REST
    fut = {}
    try:
        import httpx

        base = "https://fapi.binance.com"
        with httpx.Client(base_url=base, timeout=5.0) as client:
            oi = client.get(
                "/futures/data/openInterestHist",
                params={"symbol": SYMBOL, "period": "5m", "limit": 20},
            ).json()
            fr = client.get("/fapi/v1/fundingRate", params={"symbol": SYMBOL, "limit": 1}).json()
            lsr = client.get(
                "/futures/data/globalLongShortAccountRatio",
                params={"symbol": SYMBOL, "period": "5m", "limit": 1},
            ).json()
        oi_vals = (
            [float(x.get("sumOpenInterestValue", 0) or 0) for x in oi]
            if isinstance(oi, list)
            else []
        )
        oi_latest = oi_vals[-1] if oi_vals else 0.0
        oi_avg = sum(oi_vals) / len(oi_vals) if oi_vals else 0.0
        funding = float(fr[0].get("fundingRate", 0.0)) if isinstance(fr, list) and fr else 0.0
        lsr_v = float(lsr[0].get("longShortRatio", 0.0)) if isinstance(lsr, list) and lsr else 0.0
        fut = {
            "open_interest": oi_latest,
            "open_interest_avg": oi_avg,
            "funding_rate": funding,
            "long_short_ratio": lsr_v,
        }
    except Exception:
        fut = {
            "open_interest": 0.0,
            "open_interest_avg": 0.0,
            "funding_rate": 0.0,
            "long_short_ratio": 0.0,
        }

    report: Dict[str, Any] = {
        "current_price": float(price),
        "current_ema20": float(snap_30m.get("ema_20", 0.0) or 0.0),
        "current_macd": float(snap_30m.get("macd", 0.0) or 0.0),
        "current_rsi_7": float(arr_1m.get("rsi_7", [0])[-1] if arr_1m.get("rsi_7") else 0.0),
        "futures": {
            "open_interest": float(fut.get("open_interest", 0.0) or 0.0),
            "open_interest_avg": float(fut.get("open_interest_avg", 0.0) or 0.0),
            "funding_rate": float(fut.get("funding_rate", 0.0) or 0.0),
            "long_short_ratio": float(fut.get("long_short_ratio", 0.0) or 0.0),
        },
        "intraday": {
            "mid": arr_1m.get("close", []),
            "ema20": arr_1m.get("ema_20", []),
            "macd": arr_1m.get("macd", []),
            "rsi7": arr_1m.get("rsi_7", []),
            "rsi14": arr_1m.get("rsi_14", []),
        },
        "main_30m": {
            "close": float(snap_30m.get("close", 0.0) or 0.0),
            "ema20": float(snap_30m.get("ema_20", 0.0) or 0.0),
            "ema50": float(snap_30m.get("ema_50", 0.0) or 0.0),
            "macd": float(snap_30m.get("macd", 0.0) or 0.0),
            "rsi14": float(snap_30m.get("rsi_14", 50.0) or 50.0),
            "stoch_k": float(snap_30m.get("stoch_k", 50.0) or 50.0),
            "stoch_d": float(snap_30m.get("stoch_d", 50.0) or 50.0),
            "atr14": float(snap_30m.get("atr_14", 0.0) or 0.0),
            "mfi": float(snap_30m.get("mfi", 50.0) or 50.0),
            "bb_upper": float(snap_30m.get("bb_upper", 0.0) or 0.0),
            "bb_middle": float(snap_30m.get("bb_middle", 0.0) or 0.0),
            "bb_lower": float(snap_30m.get("bb_lower", 0.0) or 0.0),
            "willr": float(snap_30m.get("willr", -50.0) or -50.0),
            "cci": float(snap_30m.get("cci", 0.0) or 0.0),
            "obv": float(snap_30m.get("obv", 0.0) or 0.0),
            "vwap_20": float(snap_30m.get("vwap_20", 0.0) or 0.0),
            "sar": float(snap_30m.get("sar", 0.0) or 0.0),
        },
        "ctx_4h": {
            "ema20": float(snap_4h.get("ema_20", 0.0) or 0.0),
            "ema50": float(snap_4h.get("ema_50", 0.0) or 0.0),
            "atr3": float(snap_4h.get("atr_3", 0.0) or 0.0),
            "atr14": float(snap_4h.get("atr_14", 0.0) or 0.0),
            "macd": arr_4h_macd,
            "rsi14": arr_4h_rsi,
        },
        "account": {
            "available_cash": float(free_cash),
            "account_value": float(equity),
            "total_return_pct": float(total_return_pct),
            "realized_pnl": float(realized),
            "unrealized_pnl": float(unrealized),
            "real_pnl": {  # Real PnL bilgileri
                "total_fees": float(total_fees),
                "total_gross_pnl": float(total_gross_pnl),
                "total_net_pnl": float(total_net_pnl),
                "real_total_pnl": float(real_total_pnl),
            },
        },
        "position": {
            "symbol": SYMBOL,
            "quantity": float(pos_qty),
            "entry_price": float(avg_price),
            "current_price": float(price),
            "effective_leverage": float(eff_lev),
        },
    }

    # Enrich with liquidation price and TP/SL if position exists
    liq = _estimate_liquidation_price(avg_price, pos_qty, eff_lev)
    if liq is not None:
        report["position"]["liquidation_price"] = float(liq)
    tp_sl = _suggest_tp_sl(avg_price, pos_qty, snap_30m.get("atr_14"), snap_30m.get("ema_50"))
    if tp_sl:
        report["position"]["exit_plan"] = tp_sl

    # Sharpe (price-based on 30m closes)
    sharpe = _compute_sharpe_from_prices(arr_30m_close)
    if sharpe is not None:
        report["sharpe_ratio"] = float(sharpe)

    # Text rendering
    lines: List[str] = []
    lines.append("ALL BTC DATA")
    lines.append(
        f"current_price = {report['current_price']:.2f}, current_ema20 = {report['current_ema20']:.3f}, "
        f"current_macd = {report['current_macd']:.3f}, current_rsi (7 period) = {report['current_rsi_7']:.3f}"
    )
    lines.append("")
    fr = report["futures"]
    lines.append("In addition, here is the latest BTC open interest and funding rate for perps:")
    lines.append("")
    lines.append(
        f"Open Interest: Latest: {fr['open_interest']:.2f} Average: {fr['open_interest_avg']:.2f}"
    )
    lines.append("")
    lines.append(f"Funding Rate: {fr['funding_rate']}")
    lines.append("")
    intr = report["intraday"]
    lines.append("Intraday series (by minute, oldest → latest):")
    lines.append("")
    lines.append(f"Mid prices: {intr['mid']}")
    lines.append("")
    lines.append(f"EMA indicators (20‑period): {intr['ema20']}")
    lines.append("")
    lines.append(f"MACD indicators: {intr['macd']}")
    lines.append("")
    lines.append(f"RSI indicators (7‑Period): {intr['rsi7']}")
    lines.append("")
    lines.append(f"RSI indicators (14‑Period): {intr['rsi14']}")
    lines.append("")
    ctx = report["ctx_4h"]
    lines.append("Longer‑term context (4‑hour timeframe):")
    lines.append("")
    lines.append(f"20‑Period EMA: {ctx['ema20']:.3f} vs. 50‑Period EMA: {ctx['ema50']:.3f}")
    lines.append("")
    lines.append(f"3‑Period ATR: {ctx['atr3']:.3f} vs. 14‑Period ATR: {ctx['atr14']:.3f}")
    lines.append("")
    lines.append(f"MACD indicators: {ctx['macd']}")
    lines.append("")
    lines.append(f"RSI indicators (14‑Period): {ctx['rsi14']}")
    lines.append("")
    acc = report["account"]
    lines.append("HERE IS YOUR ACCOUNT INFORMATION & PERFORMANCE")
    lines.append(f"Current Total Return (percent): {acc['total_return_pct']:.2f}%")
    lines.append("")
    lines.append(f"Available Cash: {acc['available_cash']:.2f}")
    lines.append("")
    lines.append(f"Current Account Value: {acc['account_value']:.2f}")
    lines.append("")

    # Real PnL bilgilerini göster
    real_pnl = acc.get("real_pnl", {})
    if real_pnl and real_pnl.get("total_gross_pnl", 0) != 0:
        lines.append("💰 Real PnL (Net Kar - Komisyon Sonrası)")
        lines.append(f"  Brüt PnL: ${real_pnl.get('total_gross_pnl', 0):.2f}")
        lines.append(f"  Komisyon: -${real_pnl.get('total_fees', 0):.2f}")
        lines.append(f"  Net PnL: ${real_pnl.get('total_net_pnl', 0):.2f} ✅")
        lines.append("")
    pos = report["position"]
    pos_block = {
        "symbol": pos["symbol"],
        "quantity": pos["quantity"],
        "entry_price": pos["entry_price"],
        "current_price": pos["current_price"],
        "unrealized_pnl": acc["unrealized_pnl"],
        "leverage": pos["effective_leverage"],
    }
    if "liquidation_price" in pos:
        pos_block["liquidation_price"] = pos["liquidation_price"]
    if "exit_plan" in pos:
        pos_block["exit_plan"] = pos["exit_plan"]
    lines.append("Current live position (BTC): " + str(pos_block))

    if "sharpe_ratio" in report:
        lines.append("")
        lines.append(f"Sharpe Ratio: {report['sharpe_ratio']:.3f}")

    # Multi-coin positions block (ETH, SOL, XRP, BTC, DOGE, BNB)
    symbols = ["ETHUSDT", "SOLUSDT", "XRPUSDT", "BTCUSDT", "DOGEUSDT", "BNBUSDT"]
    positions_txt = []
    positions_json: List[Dict[str, Any]] = []

    def _resolve_price_simple(sym: str) -> float:
        try:
            import httpx

            r = httpx.get(
                "https://api.binance.com/api/v3/ticker/price", params={"symbol": sym}, timeout=5.0
            )
            r.raise_for_status()
            return float(r.json()["price"])
        except Exception:
            return 0.0

    with Session(engine) as session:
        for sym in symbols:
            # Portfolio row (may be absent)
            pf = session.query(Portfolio).filter(Portfolio.symbol == sym).first()
            qty = float(pf.position) if pf else 0.0
            entry = float(pf.average_price) if pf else 0.0
            cur = _resolve_price_simple(sym)
            notional = abs(qty) * cur if cur > 0 else 0.0
            # leverage approx via exposure/margin for that symbol
            exe_sym = Executor(symbol=sym)
            pm_sym = exe_sym.portfolio_metrics()
            eff_lev_sym = _effective_leverage(
                pm_sym.get("exposure", 0.0), pm_sym.get("margin_used", 0.0)
            )
            liq_sym = _estimate_liquidation_price(entry, qty, eff_lev_sym)
            # ATR(30m) for TP/SL
            snap30 = query_latest_snapshot("enriched_30min", sym, "30min") or {}
            tpsl = _suggest_tp_sl(entry, qty, snap30.get("atr_14"), snap30.get("ema_50"))
            unreal = (cur - entry) * qty if qty else 0.0
            # risk_usd from SL if present
            risk_usd = abs(entry - tpsl.get("stop_loss", entry)) * abs(qty) if tpsl else 0.0
            pos_d = {
                "symbol": sym.replace("USDT", ""),
                "quantity": qty,
                "entry_price": entry,
                "current_price": cur,
                "liquidation_price": liq_sym if liq_sym is not None else 0.0,
                "unrealized_pnl": unreal,
                "leverage": eff_lev_sym,
                "exit_plan": tpsl or {},
                "confidence": None,
                "risk_usd": risk_usd,
                "sl_oid": -1,
                "tp_oid": -1,
                "wait_for_fill": False,
                "entry_oid": -1,
                "notional_usd": notional,
            }
            positions_json.append(pos_d)
            positions_txt.append(str(pos_d))
    if positions_json:
        report["positions"] = positions_json
        lines.append("")
        lines.append("Current live positions & performance:")
        for p in positions_txt:
            lines.append(p)

    text = "\n".join(lines)
    return report, text


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="BTC single-asset report")
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    parser.add_argument("--text", action="store_true", help="Print text report")
    args = parser.parse_args()

    report, text = generate_btc_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.text or not (args.text or args.json):
        print(text)


if __name__ == "__main__":
    main()
