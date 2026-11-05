"""Бэктест правил стратегии на истории (без ИИ — его честно проверяет только paper-торговля).

Консервативные допущения: вход по открытию следующей свечи, если в одной свече
задеты и стоп, и тейк — считаем, что сработал стоп. Учитываются комиссии и проскальзывание.
"""
import pandas as pd

from .risk import position_size
from .strategy import check_entry, min_bars, prepare


def run(df: pd.DataFrame, cfg: dict, symbol: str = "X") -> dict:
    s, r, p = cfg["strategy"], cfg["risk"], cfg["paper"]
    fee, slip = p["fee"], p["slippage"]
    df = prepare(df, s)
    cash = float(p["starting_balance"])
    pos, pending, trades, curve = None, None, [], []

    for i in range(min_bars(s), len(df)):
        bar = df.iloc[i]
        if pending and pos is None:  # вход по открытию
            entry = bar.open * (1 + slip)
            if entry > pending.stop:
                qty = position_size(cash, cash, entry, pending.stop, r)
                if qty > 0:
                    cost = qty * entry * (1 + fee)
                    cash -= cost
                    pos = {"qty": qty, "entry": entry, "stop": pending.stop, "take": pending.take,
                           "r": entry - pending.stop, "cost": cost, "t": df.index[i]}
            pending = None

        if pos:
            exit_px, why = None, None
            if bar.low <= pos["stop"]:
                exit_px, why = min(pos["stop"], bar.open), "stop"
            elif bar.high >= pos["take"]:
                exit_px, why = max(pos["take"], bar.open), "take"
            elif bar.close < bar.ema_slow:
                exit_px, why = bar.close, "trend"
            if exit_px is not None:
                proceeds = pos["qty"] * exit_px * (1 - slip) * (1 - fee)
                cash += proceeds
                trades.append({"open": pos["t"], "close": df.index[i], "pnl": proceeds - pos["cost"],
                               "ret": proceeds / pos["cost"] - 1, "exit": why})
                pos = None
            elif bar.high >= pos["entry"] + pos["r"]:
                pos["stop"] = max(pos["stop"], pos["entry"])  # безубыток

        if pos is None and pending is None:
            pending = check_entry(symbol, df.iloc[: i + 1], s)
        curve.append(cash + (pos["qty"] * bar.close if pos else 0))

    return stats(trades, curve, float(p["starting_balance"]), df)


def stats(trades, curve, start, df) -> dict:
    eq = pd.Series(curve, dtype=float)
    wins = [t for t in trades if t["pnl"] > 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    bh = df["close"].iloc[-1] / df["close"].iloc[min(len(df) - 1, 0)] - 1
    return {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss else float("inf") if gross_win else 0.0,
        "total_return": (eq.iloc[-1] / start - 1) if len(eq) else 0.0,
        "max_drawdown": float((1 - eq / eq.cummax()).max()) if len(eq) else 0.0,
        "buy_and_hold": float(bh),
        "trade_list": trades,
    }


def format_report(symbol: str, st: dict) -> str:
    return (f"{symbol}: сделок {st['trades']}, винрейт {st['win_rate']:.0%}, "
            f"profit factor {st['profit_factor']:.2f}, доходность {st['total_return']:+.1%}, "
            f"макс. просадка {st['max_drawdown']:.1%} (просто держать: {st['buy_and_hold']:+.1%})")
