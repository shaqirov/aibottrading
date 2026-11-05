"""Правила стратегии: покупка отката в восходящем тренде (long-only, спот).

Логика опытного свинг-трейдера, записанная как код:
  1. Торгуем только по тренду: цена выше EMA200, EMA20 выше EMA50.
  2. Входим не на пике, а на откате: свеча коснулась EMA20 и закрылась выше неё.
  3. RSI в «здоровой» зоне — нет перекупленности.
  4. Стоп ставится заранее по волатильности (ATR), тейк — с соотношением риск/прибыль >= 2.
  5. Выход досрочно, если тренд сломан (закрытие ниже EMA50).
"""
from dataclasses import dataclass, field

import pandas as pd

from .indicators import add_indicators


@dataclass
class Signal:
    symbol: str
    side: str            # "buy"
    entry: float
    stop: float
    take: float
    reasons: list[str] = field(default_factory=list)


def min_bars(s: dict) -> int:
    return s["ema_trend"] + 5


def check_entry(symbol: str, df: pd.DataFrame, s: dict) -> Signal | None:
    """df — закрытые свечи С индикаторами. Смотрим на последнюю свечу."""
    if len(df) < min_bars(s):
        return None
    c = df.iloc[-1]
    if c[["ema_fast", "ema_slow", "ema_trend", "rsi", "atr"]].isna().any():
        return None

    uptrend = c.close > c.ema_trend and c.ema_fast > c.ema_slow
    pullback = c.low <= c.ema_fast * 1.002 and c.close > c.ema_fast
    rsi_ok = s["rsi_min"] <= c.rsi <= s["rsi_max"]
    if not (uptrend and pullback and rsi_ok):
        return None

    entry = float(c.close)
    stop = entry - s["stop_atr_mult"] * float(c.atr)
    if stop <= 0:
        return None
    take = entry + s["reward_risk"] * (entry - stop)
    return Signal(
        symbol=symbol,
        side="buy",
        entry=entry,
        stop=stop,
        take=take,
        reasons=[
            f"тренд вверх: close {c.close:.4g} > EMA{s['ema_trend']} {c.ema_trend:.4g}, EMA{s['ema_fast']} > EMA{s['ema_slow']}",
            f"откат к EMA{s['ema_fast']} ({c.ema_fast:.4g}) с закрытием выше",
            f"RSI {c.rsi:.1f}",
            f"ATR {c.atr:.4g}, стоп {stop:.4g}, тейк {take:.4g}",
        ],
    )


def trend_broken(df: pd.DataFrame) -> bool:
    c = df.iloc[-1]
    return bool(c.close < c.ema_slow)


def prepare(df: pd.DataFrame, s: dict) -> pd.DataFrame:
    return add_indicators(df, s)
