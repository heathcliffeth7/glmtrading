#!/usr/bin/env python3
"""
BTC/ETH/SOL portföy ve işlem geçmişini güvenli biçimde sıfırlar.

Çalışma şekli:
- TRADING_DATABASE_URL / DATABASE_URL / TRADING_DB_URL env değişkenleri üzerinden PostgreSQL bağlantısını alır.
  Varsayılan: postgresql://trading_user:trading_pass_2025@localhost/trading_db
- Şu tablolar temizlenir: trades, prediction_logs, stop_loss_orders, stop_loss_notifications, take_profit_orders (BTC/ETH/SOL).
- Portföy kayıtları sıfırlanır (pozisyon=0, sermaye=10k).
- daily_pnl tablolarındaki realized/unrealized/total_fees alanları 0lanır (tüm tarihler).

Kullanım:
    TRADING_DATABASE_URL=postgresql://... python scripts/reset_btc_eth_sol.py --force
    # veya varsayılan bağlantı için
    python scripts/reset_btc_eth_sol.py --force
"""

import os
import sys
from urllib.parse import urlparse

import subprocess
import psycopg2

TARGET_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
DEFAULT_DB_URL = "postgresql://trading_user:trading_pass_2025@localhost/trading_db"


def parse_db_url(url: str) -> dict:
    """Parse PostgreSQL URL into psycopg2 kwargs."""
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"Unsupported DB scheme: {parsed.scheme}")
    return {
        "dbname": parsed.path.lstrip("/") or "postgres",
        "user": parsed.username,
        "password": parsed.password,
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
    }


def confirm(force: bool) -> None:
    if force:
        return
    resp = input(
        "UYARI: BTC/ETH/SOL için TÜM trade geçmişi ve portföy sıfırlanacak. Devam? (y/n): "
    ).strip().lower()
    if resp != "y":
        print("İptal edildi.")
        sys.exit(1)


def main() -> None:
    db_url = (
        os.getenv("TRADING_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or os.getenv("TRADING_DB_URL")
        or DEFAULT_DB_URL
    )

    force = "--force" in sys.argv
    confirm(force)

    conn_kwargs = parse_db_url(db_url)
    print(f"Bağlanılıyor: {db_url}")

    # Try psycopg2 first; if it fails (e.g., libpq auth issues), fall back to psql
    symbols_sql = "(" + ",".join(f"'{s}'" for s in TARGET_SYMBOLS) + ")"
    sql_batch = f"""
BEGIN;
DELETE FROM stop_loss_notifications WHERE symbol IN {symbols_sql};
DELETE FROM take_profit_orders      WHERE symbol IN {symbols_sql};
DELETE FROM stop_loss_orders        WHERE symbol IN {symbols_sql};
DELETE FROM prediction_logs         WHERE symbol IN {symbols_sql};
DELETE FROM trades                  WHERE symbol IN {symbols_sql};
UPDATE portfolio
   SET position=0.0,
       long_position=0.0,
       short_position=0.0,
       net_position=0.0,
       average_price=0.0,
       long_avg_price=0.0,
       short_avg_price=0.0,
       initial_capital=10000.0,
       updated_at=NOW()
 WHERE symbol IN {symbols_sql};
UPDATE daily_pnl SET realized_pnl=0.0, unrealized_pnl=0.0, total_fees=0.0;
COMMIT;
""".strip()

    try:
        with psycopg2.connect(connect_timeout=5, **conn_kwargs) as conn:
            conn.autocommit = False
            cur = conn.cursor()
            cur.execute(sql_batch)
            conn.commit()
            print("🎉 Sıfırlama tamamlandı. (psycopg2)")
            return
    except Exception as exc:  # noqa: BLE001
        print(f"psycopg2 bağlantısı başarısız, psql ile deniyorum... ({exc})")

    env = os.environ.copy()
    env["PGPASSWORD"] = conn_kwargs.get("password", "") or ""
    host = conn_kwargs.get("host", "localhost")
    port = str(conn_kwargs.get("port", 5432))
    user = conn_kwargs.get("user", "")
    dbname = conn_kwargs.get("dbname", "")

    psql_bin = os.getenv("PSQL_BIN", "/usr/bin/psql")
    psql_cmd = [
        psql_bin,
        "-h",
        host,
        "-p",
        port,
        "-U",
        user,
        "-d",
        dbname,
        "-v",
        "ON_ERROR_STOP=1",
        "-f",
        "-",
    ]

    try:
        proc = subprocess.run(
            psql_cmd,
            input=sql_batch,
            text=True,
            env=env,
            check=True,
            capture_output=True,
        )
        if proc.stdout:
            print(proc.stdout.strip())
        print("🎉 Sıfırlama tamamlandı. (psql fallback)")
    except subprocess.CalledProcessError as sub_exc:
        if sub_exc.stderr:
            print(sub_exc.stderr.strip())
        print("❌ psql ile sıfırlama başarısız oldu.")
        raise sub_exc


if __name__ == "__main__":
    main()
