"""Загрузка свечей с биржи через ccxt."""
import time

import ccxt
import pandas as pd


def make_exchange(cfg: dict, authenticated: bool = False):
    params = {"enableRateLimit": True}
    if authenticated:
        params["apiKey"] = cfg["secrets"]["exchange_key"]
        params["secret"] = cfg["secrets"]["exchange_secret"]
    return getattr(ccxt, cfg["exchange"])(params)


def _to_df(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.drop_duplicates("ts").set_index("ts").sort_index()


def fetch_ohlcv(exchange, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """Последние `limit` ЗАКРЫТЫХ свечей (незакрытая текущая свеча отбрасывается)."""
    rows = exchange.fetch_ohlcv(symbol, timeframe, limit=limit + 1)
    return _to_df(rows).iloc[:-1]


def fetch_history(exchange, symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Длинная история для бэктеста (постранично)."""
    tf_ms = exchange.parse_timeframe(timeframe) * 1000
    since = exchange.milliseconds() - days * 86_400_000
    rows = []
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + tf_ms
        if since >= exchange.milliseconds() - tf_ms:
            break
        time.sleep(exchange.rateLimit / 1000)
    return _to_df(rows).iloc[:-1]
