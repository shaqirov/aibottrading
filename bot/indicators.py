import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / down.replace(0, 1e-12)
    return 100 - 100 / (1 + rs)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def add_indicators(df: pd.DataFrame, s: dict) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = ema(df["close"], s["ema_fast"])
    df["ema_slow"] = ema(df["close"], s["ema_slow"])
    df["ema_trend"] = ema(df["close"], s["ema_trend"])
    df["rsi"] = rsi(df["close"], s["rsi_period"])
    df["atr"] = atr(df, s["atr_period"])
    df["vol_ma"] = df["volume"].rolling(20).mean()
    return df
