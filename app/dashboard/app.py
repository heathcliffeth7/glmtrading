from datetime import datetime

import pandas as pd
import streamlit as st

from app.executor.ledger import DailyPnL, Trade, engine
from app.utils.influx import query_latest, query_range


def load_price_series(symbol: str, interval: str) -> pd.DataFrame:
    records = query_range(f"features_{interval}", symbol, interval)
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    pivot = df.pivot(index="timestamp", columns="field", values="value").reset_index()
    pivot["timestamp"] = pd.to_datetime(pivot["timestamp"])
    return pivot


def load_ledger() -> pd.DataFrame:
    with engine.connect() as conn:
        trades = pd.read_sql(Trade.__table__.select(), conn)
        pnl = pd.read_sql(DailyPnL.__table__.select(), conn)
    trades["executed_at"] = pd.to_datetime(trades["executed_at"])
    pnl["date"] = pd.to_datetime(pnl["date"])
    return trades, pnl


st.set_page_config(page_title="AI Trading Dashboard", layout="wide")
st.title("AI Trading Platformu")

symbol = st.sidebar.selectbox("Sembol", ["BTCUSDT"])
interval = st.sidebar.selectbox("Interval", ["1m", "4h"])

price_df = load_price_series(symbol, interval)
if not price_df.empty:
    latest_close = price_df.iloc[-1].get("close")
    st.metric(
        label=f"Son Fiyat ({interval})",
        value=round(latest_close, 2) if latest_close else "-",
        delta=price_df.iloc[-1].get("ema_20", 0),
    )
    st.line_chart(price_df.set_index("timestamp")["close"])

st.header("Ajan Sinyalleri")
st.caption("Gerçek zamanlı sinyal izlemesi için Redis/Influx entegrasyonu gereklidir.")
st.table([
    {"Ajan": "Kısa Vadeli", "Sinyal": "HOLD", "Güven": 0.0},
    {"Ajan": "Uzun Vadeli", "Sinyal": "HOLD", "Güven": 0.0},
    {"Ajan": "Türev", "Sinyal": "HOLD", "Güven": 0.0},
])

st.header("PnL ve İşlem Geçmişi")
trades_df, pnl_df = load_ledger()
if not pnl_df.empty:
    pnl_df = pnl_df.sort_values("date")
    st.line_chart(pnl_df.set_index("date")[["realized_pnl", "unrealized_pnl"]])
else:
    st.info("PnL verisi bulunamadı")

if not trades_df.empty:
    st.dataframe(trades_df.sort_values("executed_at", ascending=False))
else:
    st.info("Trade kaydı bulunamadı")
