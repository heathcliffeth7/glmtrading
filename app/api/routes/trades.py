"""Trades API Routes"""
import csv
import io
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

import numpy as np
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.api.dependencies import get_db
from app.api.schemas.trade import (
    TradeResponse,
    TradeListResponse,
    PnLHistogramResponse,
    HistogramBin,
    PnLStats,
)
from app.executor.ledger import Trade
from app.utils.influx import query_historical_snapshots
from app.utils.logging import get_logger
from app.utils.exit_plan_history import normalize_exit_plan_history
from app.utils.prompt_restore import find_best_entry_in_log_file

router = APIRouter()
logger = get_logger(__name__)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
LOG_FILE_PATH = "/root/trading/logs/glm_prompts.log"


def _fetch_signals_for_reasoning(symbols: List[str], hours: int = 30 * 24) -> List[Dict[str, Any]]:
    """InfluxDB'den signals çek (reasoning için)"""
    signals = []
    try:
        for sym in symbols:
            result = query_historical_snapshots(
                measurement="trading_signals",
                symbol=sym,
                interval="30min",
                limit=500,
                hours=hours
            )
            if result:
                # Symbol'ü her record'a ekle (query_historical_snapshots exclude ediyor)
                for record in result:
                    record["symbol"] = sym
                signals.extend(result)
    except Exception as e:
        logger.warning("Failed to fetch signals for reasoning: %s", e)
    return signals


def _find_matching_signal(
    signals: List[Dict[str, Any]],
    trade_timestamp: datetime,
    symbol: str,
    action: str  # Artık kullanılmıyor ama API uyumluluğu için tutuldu
) -> Optional[Dict[str, Any]]:
    """Trade ile eşleşen signal'i bul (±5 dakika tolerance, sadece symbol+timestamp)"""
    from datetime import timezone

    # Trade timestamp'ı timezone-aware yap
    if trade_timestamp.tzinfo is None:
        trade_time = trade_timestamp.replace(tzinfo=timezone.utc)
    else:
        trade_time = trade_timestamp

    best_match = None
    best_diff = float('inf')

    for signal in signals:
        # Symbol eşleşmesi
        if signal.get('symbol') != symbol:
            continue

        # Timestamp eşleşmesi (±5 dakika) - EN YAKIN olanı bul
        signal_time_str = signal.get('timestamp', '')
        try:
            if isinstance(signal_time_str, str):
                signal_time_str = signal_time_str.replace('Z', '+00:00')
                if '+' not in signal_time_str and signal_time_str.count(':') >= 2:
                    signal_time_str += '+00:00'
                signal_time = datetime.fromisoformat(signal_time_str)
            else:
                continue

            if signal_time.tzinfo is None:
                signal_time = signal_time.replace(tzinfo=timezone.utc)

            time_diff = abs((trade_time - signal_time).total_seconds())
            if time_diff < 300 and time_diff < best_diff:  # 5 dakika içinde ve en yakın
                best_diff = time_diff
                best_match = signal
        except Exception:
            continue

    return best_match


@router.get("", response_model=TradeListResponse)
def get_trades(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    status: str = Query("all", regex="^(open|closed|all)$", description="Trade status filter"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_db)
):
    """Get paginated trade list"""
    query = db.query(Trade)

    # Açık pozisyonları (close_price IS NULL) map'leyelim: aynı position_id açık kaldıysa kısmi kapanış demektir
    open_trades = (
        db.query(Trade)
        .filter(Trade.close_price.is_(None))
        .filter(Trade.position_id.isnot(None))
        .all()
    )
    # Sembol + position_id kombinasyonu ile map oluştur (farklı semboller aynı position_id paylaşabilir)
    open_trades_by_position: Dict[str, Trade] = {}
    for ot in open_trades:
        if not ot.position_id:
            continue
        # Sembol bazlı key: "BTCUSDT:POS-20251202-010"
        key = f"{ot.symbol}:{ot.position_id}"
        # Aynı key için en güncel kaydı tut
        prev = open_trades_by_position.get(key)
        if not prev or (ot.timestamp and prev.timestamp and ot.timestamp > prev.timestamp):
            open_trades_by_position[key] = ot
    open_position_keys = set(open_trades_by_position.keys())

    # Filter by symbol
    if symbol:
        query = query.filter(Trade.symbol == symbol.upper())

    # Filter by status
    if status == "open":
        query = query.filter(Trade.close_price.is_(None))
    elif status == "closed":
        query = query.filter(Trade.close_price.isnot(None))

    # Get total count
    total = query.count()

    # Paginate
    offset = (page - 1) * per_page
    trades = query.order_by(Trade.timestamp.desc()).offset(offset).limit(per_page).all()

    # Fetch signals for reasoning enrichment (eski trade'ler için)
    symbols_to_fetch = [symbol.upper()] if symbol else SYMBOLS
    signals = _fetch_signals_for_reasoning(symbols_to_fetch, hours=30 * 24)

    # Convert to response
    trade_responses = []
    for t in trades:
        is_closed = t.close_price is not None
        # Sembol bazlı partial close kontrolü
        position_key = f"{t.symbol}:{t.position_id}" if t.position_id else None
        is_partial_close = bool(is_closed and position_key and position_key in open_position_keys)
        action_label = "PARTIAL CLOSE" if is_partial_close else ("CLOSE" if is_closed else t.side)
        remaining_amount = None
        if is_partial_close and position_key:
            open_trade = open_trades_by_position.get(position_key)
            remaining_amount = open_trade.amount if open_trade else None

        pnl_pct = None
        if t.pnl and t.price and t.amount:
            position_value = t.price * t.amount
            if position_value > 0:
                pnl_pct = (t.pnl / position_value) * 100

        # Exit plan enrichment - reasoning yoksa signals'dan bul
        exit_plan = dict(t.exit_plan) if t.exit_plan else {}
        if not exit_plan.get('reasoning') and t.timestamp and t.side in ('BUY', 'SELL'):
            matching_signal = _find_matching_signal(signals, t.timestamp, t.symbol, t.side)
            if matching_signal and matching_signal.get('reasoning'):
                exit_plan['reasoning'] = matching_signal['reasoning']

        # Exit prompt/reasoning fallback - log'dan ara
        resolved_exit_prompt = t.exit_prompt
        resolved_exit_reasoning = t.exit_reasoning

        if t.close_time and (t.exit_prompt is None or t.exit_reasoning is None):
            match, _, _ = find_best_entry_in_log_file(
                Path(LOG_FILE_PATH),
                max_bytes=50 * 1024 * 1024,  # 50MB
                symbol=t.symbol,
                target_timestamp=t.close_time,
                preferred_signals=["CLOSE"],
                tolerance_seconds=3600,
            )
            if match:
                if t.exit_prompt is None and match.prompt:
                    resolved_exit_prompt = match.prompt
                if t.exit_reasoning is None and match.response:
                    resolved_exit_reasoning = match.response

        trade_responses.append(TradeResponse(
            id=t.id,
            position_id=t.position_id,
            symbol=t.symbol,
            side=t.side,
            position_side=t.position_side,
            amount=t.amount,
            entry_price=t.price,
            close_price=t.close_price,
            pnl=t.pnl or 0.0,
            pnl_pct=round(pnl_pct, 2) if pnl_pct else None,
            leverage=t.leverage or 1.0,
            fees=t.fees or 0.0,
            timestamp=t.timestamp.isoformat() if t.timestamp else "",
            close_time=t.close_time.isoformat() if t.close_time else None,
            exit_plan=exit_plan,
            exit_reasoning=resolved_exit_reasoning,
            entry_reasoning=t.entry_reasoning,
            entry_prompt=t.entry_prompt,
            exit_prompt=resolved_exit_prompt,
            is_partial_close=is_partial_close,
            action_label=action_label,
            remaining_amount=remaining_amount,
        ))

    return TradeListResponse(
        trades=trade_responses,
        total=total,
        page=page,
        per_page=per_page,
        has_more=(offset + per_page) < total,
    )


@router.get("/pnl-histogram", response_model=PnLHistogramResponse)
def get_pnl_histogram(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    days: int = Query(30, ge=1, le=365, description="Days of history"),
    bins: int = Query(10, ge=5, le=50, description="Number of histogram bins"),
    db: Session = Depends(get_db)
):
    """Get PnL distribution histogram"""
    cutoff = datetime.utcnow() - timedelta(days=days)

    query = db.query(Trade).filter(
        Trade.close_price.isnot(None),
        Trade.close_time >= cutoff
    )

    if symbol:
        query = query.filter(Trade.symbol == symbol.upper())

    trades = query.all()
    pnls = [t.pnl for t in trades if t.pnl is not None]

    if not pnls:
        return PnLHistogramResponse(
            symbol=symbol.upper() if symbol else None,
            period_days=days,
            histogram=[],
            stats=PnLStats(
                total_trades=0,
                win_count=0,
                loss_count=0,
                win_rate=0.0,
                avg_pnl=0.0,
                avg_winner=0.0,
                avg_loser=0.0,
                max_profit=0.0,
                max_loss=0.0,
                profit_factor=0.0,
            ),
        )

    # Calculate histogram
    counts, bin_edges = np.histogram(pnls, bins=bins)
    histogram = [
        HistogramBin(
            range_min=round(bin_edges[i], 2),
            range_max=round(bin_edges[i + 1], 2),
            count=int(counts[i]),
        )
        for i in range(len(counts))
    ]

    # Calculate statistics
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p < 0]

    win_rate = len(winners) / len(pnls) if pnls else 0.0
    avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0
    avg_winner = sum(winners) / len(winners) if winners else 0.0
    avg_loser = sum(losers) / len(losers) if losers else 0.0
    profit_factor = abs(sum(winners) / sum(losers)) if losers and sum(losers) != 0 else 0.0

    return PnLHistogramResponse(
        symbol=symbol.upper() if symbol else None,
        period_days=days,
        histogram=histogram,
        stats=PnLStats(
            total_trades=len(pnls),
            win_count=len(winners),
            loss_count=len(losers),
            win_rate=round(win_rate, 4),
            avg_pnl=round(avg_pnl, 2),
            avg_winner=round(avg_winner, 2),
            avg_loser=round(avg_loser, 2),
            max_profit=round(max(pnls), 2),
            max_loss=round(min(pnls), 2),
            profit_factor=round(profit_factor, 2),
        ),
    )


@router.get("/export/csv")
def export_trades_csv(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    status: str = Query("all", regex="^(open|closed|all)$", description="Trade status filter"),
    days: int = Query(30, ge=1, le=365, description="Days of history"),
    db: Session = Depends(get_db)
):
    """Export trades to CSV file"""
    cutoff = datetime.utcnow() - timedelta(days=days)
    query = db.query(Trade).filter(Trade.timestamp >= cutoff)

    if symbol:
        query = query.filter(Trade.symbol == symbol.upper())

    if status == "open":
        query = query.filter(Trade.close_price.is_(None))
    elif status == "closed":
        query = query.filter(Trade.close_price.isnot(None))

    trades = query.order_by(Trade.timestamp.desc()).all()

    # CSV oluştur
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "ID", "Position ID", "Symbol", "Side", "Position Side",
        "Amount", "Entry Price", "Exit Price", "PnL", "PnL %",
        "Leverage", "Fees", "Entry Time", "Exit Time",
        "Entry Reasoning", "Entry Prompt", "Exit Reasoning", "Exit Prompt"
    ])

    # Data rows
    for t in trades:
        pnl_pct = None
        if t.pnl and t.price and t.amount:
            position_value = t.price * t.amount
            if position_value > 0:
                pnl_pct = round((t.pnl / position_value) * 100, 2)

        # Entry reasoning: exit_plan içindeki reasoning veya entry_reasoning alanı
        entry_reasoning = ""
        if t.exit_plan and isinstance(t.exit_plan, dict):
            entry_reasoning = t.exit_plan.get("reasoning", "")
        if not entry_reasoning and t.entry_reasoning:
            entry_reasoning = t.entry_reasoning

        # Entry prompt
        entry_prompt = t.entry_prompt or ""

        # Exit reasoning: exit_reasoning alanı
        exit_reasoning = t.exit_reasoning or ""

        # Exit prompt
        exit_prompt = t.exit_prompt or ""

        writer.writerow([
            t.id,
            t.position_id or "",
            t.symbol,
            t.side,
            t.position_side or "",
            t.amount,
            t.price,
            t.close_price or "",
            t.pnl or 0,
            pnl_pct or "",
            t.leverage or 1,
            t.fees or 0,
            t.timestamp.isoformat() if t.timestamp else "",
            t.close_time.isoformat() if t.close_time else "",
            entry_reasoning,
            entry_prompt,
            exit_reasoning,
            exit_prompt
        ])

    output.seek(0)
    filename = f"trades_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/{trade_id}")
def get_trade_detail(
    trade_id: int,
    db: Session = Depends(get_db)
):
    """Get single trade details"""
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    return {
        "id": trade.id,
        "position_id": trade.position_id,
        "symbol": trade.symbol,
        "side": trade.side,
        "position_side": trade.position_side,
        "amount": trade.amount,
        "entry_price": trade.price,
        "close_price": trade.close_price,
        "pnl": trade.pnl,
        "leverage": trade.leverage,
        "fees": trade.fees,
        "notional_value": trade.notional_value,
        "exit_plan": trade.exit_plan,
        "exit_plan_history": normalize_exit_plan_history(trade.exit_plan_history),
        "entry_reasoning": trade.entry_reasoning,
        "exit_reasoning": trade.exit_reasoning,
        "entry_prompt": trade.entry_prompt,
        "exit_prompt": trade.exit_prompt,
        "timestamp": trade.timestamp.isoformat() if trade.timestamp else None,
        "close_time": trade.close_time.isoformat() if trade.close_time else None,
    }


@router.get("/{trade_id}/resolved-prompts")
def resolve_trade_prompts(
    trade_id: int,
    max_scan_kb: int = Query(131072, ge=512, le=524288, description="Max log tail to scan (KB)"),
    tolerance_seconds: int = Query(3600, ge=60, le=86400, description="Timestamp matching tolerance (seconds)"),
    db: Session = Depends(get_db),
):
    """
    Resolve missing prompts for historical trades by matching symbol + timestamp against glm_prompts.log.

    This endpoint does NOT persist anything (no DB writes). It is intended purely for copy UX.
    """
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    if not trade.timestamp:
        raise HTTPException(status_code=400, detail="Trade has no timestamp")

    entry_prompt = trade.entry_prompt
    exit_prompt = trade.exit_prompt
    debug: dict[str, Any] = {}

    log_path = Path(LOG_FILE_PATH)
    try:
        file_size = log_path.stat().st_size
    except FileNotFoundError:
        return {
            "id": trade.id,
            "entry_prompt": entry_prompt,
            "exit_prompt": exit_prompt,
            "debug": {"reason": "Log file not found"},
        }

    max_bytes = min(max_scan_kb * 1024, file_size)
    initial_bytes = min(20 * 1024 * 1024, max_bytes)  # 20MB fast path
    debug["log_file_bytes"] = file_size

    if not entry_prompt:
        preferred_signals = [trade.side] if trade.side in {"BUY", "SELL"} else []
        match, _candidate, stats = find_best_entry_in_log_file(
            log_path,
            max_bytes=initial_bytes,
            symbol=trade.symbol,
            target_timestamp=trade.timestamp,
            preferred_signals=preferred_signals,
            tolerance_seconds=tolerance_seconds,
        )
        entry_debug: dict[str, Any] = {"scanned_bytes": initial_bytes, **stats}
        if not match and initial_bytes < max_bytes:
            match, _candidate, stats = find_best_entry_in_log_file(
                log_path,
                max_bytes=max_bytes,
                symbol=trade.symbol,
                target_timestamp=trade.timestamp,
                preferred_signals=preferred_signals,
                tolerance_seconds=tolerance_seconds,
            )
            entry_debug = {"scanned_bytes": max_bytes, **stats}
        debug["entry"] = entry_debug
        if match and match.prompt:
            entry_prompt = match.prompt[:50000]

    if trade.close_time and not exit_prompt:
        match, _candidate, stats = find_best_entry_in_log_file(
            log_path,
            max_bytes=initial_bytes,
            symbol=trade.symbol,
            target_timestamp=trade.close_time,
            preferred_signals=["CLOSE"],
            tolerance_seconds=tolerance_seconds,
        )
        exit_debug: dict[str, Any] = {"scanned_bytes": initial_bytes, **stats}
        if not match and initial_bytes < max_bytes:
            match, _candidate, stats = find_best_entry_in_log_file(
                log_path,
                max_bytes=max_bytes,
                symbol=trade.symbol,
                target_timestamp=trade.close_time,
                preferred_signals=["CLOSE"],
                tolerance_seconds=tolerance_seconds,
            )
            exit_debug = {"scanned_bytes": max_bytes, **stats}
        debug["exit"] = exit_debug
        if match and match.prompt:
            exit_prompt = match.prompt[:50000]

    return {
        "id": trade.id,
        "entry_prompt": entry_prompt,
        "exit_prompt": exit_prompt,
        "debug": debug,
    }
