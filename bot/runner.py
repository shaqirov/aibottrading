"""Основной торговый цикл."""
import logging
import time

from . import journal, risk
from .analyst import Analyst, apply_review
from .broker import make_broker
from .data import fetch_ohlcv, make_exchange
from .notify import notify
from .strategy import check_entry, min_bars, prepare, trend_broken

log = logging.getLogger("bot")


class Bot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.ex = make_exchange(cfg, authenticated=cfg["mode"] == "live")
        self.broker = make_broker(cfg, self.ex)
        self.analyst = Analyst(cfg)
        self.s, self.r = cfg["strategy"], cfg["risk"]

    def candles(self, symbol, tf=None, limit=None):
        limit = limit or max(self.cfg["history_bars"], min_bars(self.s) + 50)
        return prepare(fetch_ohlcv(self.ex, symbol, tf or self.cfg["timeframe"], limit), self.s)

    # --- выходы -------------------------------------------------------------
    def manage_positions(self) -> None:
        st = self.broker.state
        for sym, pos in list(st["positions"].items()):
            price = self.broker.price(sym)
            r_unit = pos.get("risk_per_unit", pos["entry"] - pos["stop"])
            # перевод в безубыток после движения на +1R
            if price >= pos["entry"] + r_unit and pos["stop"] < pos["entry"]:
                pos["stop"] = pos["entry"]
                self.broker.save()
                notify(self.cfg, f"{sym}: стоп перенесён в безубыток {pos['entry']:.6g}")
            reason = None
            if price <= pos["stop"]:
                reason = "стоп"
            elif price >= pos["take"]:
                reason = "тейк"
            elif trend_broken(self.candles(sym)):
                reason = "слом тренда (закрытие ниже EMA50)"
            if reason:
                pnl = self.broker.sell(sym, price)
                risk.register_result(st, pnl, self.r)
                self.broker.save()
                journal.log("close", mode=self.cfg["mode"], symbol=sym, price=price,
                            qty=pos["qty"], pnl=round(pnl, 4), reasoning=reason)
                notify(self.cfg, f"Закрыта {sym} по {price:.6g} ({reason}), PnL {pnl:+.2f} USDT")

    # --- входы --------------------------------------------------------------
    def look_for_entries(self) -> None:
        st = self.broker.state
        seen = st.setdefault("last_signal_bar", {})
        prices = {s: self.broker.price(s) for s in st["positions"]}
        equity = self.broker.equity(prices)
        risk.roll_day(st, equity)

        for sym in self.cfg["symbols"]:
            if sym in st["positions"]:
                continue
            df = self.candles(sym)
            bar = df.index[-1].isoformat()
            if seen.get(sym) == bar:   # эту свечу уже анализировали
                continue
            seen[sym] = bar
            self.broker.save()

            sig = check_entry(sym, df, self.s)
            if not sig:
                continue
            ok, why = risk.can_open(st, equity, self.r)
            if not ok:
                journal.log("skip", mode=self.cfg["mode"], symbol=sym, price=sig.entry, reasoning=why)
                continue

            daily = self.candles(sym, "1d", 120) if self.analyst.active else None
            review = self.analyst.review(sig, df, daily)
            journal.log("signal", mode=self.cfg["mode"], symbol=sym, price=sig.entry, stop=sig.stop,
                        take=sig.take, ai_decision=review["decision"],
                        ai_confidence=review["confidence"], reasoning=review["reasoning"])
            if review["decision"] != "approve":
                notify(self.cfg, f"{sym}: сигнал отклонён ИИ — {review['reasoning']}")
                continue
            sig = apply_review(sig, review)

            price = self.broker.price(sym)
            if price <= sig.stop:
                continue
            qty = risk.position_size(equity, self.broker.cash(), price, sig.stop, self.r)
            if qty * price < 10:   # минимальный ордер на большинстве бирж
                journal.log("skip", mode=self.cfg["mode"], symbol=sym, price=price, reasoning="слишком маленький объём")
                continue
            pos = self.broker.buy(sym, qty, price, sig.stop, sig.take)
            pos["risk_per_unit"] = pos["entry"] - sig.stop
            self.broker.save()
            journal.log("open", mode=self.cfg["mode"], symbol=sym, price=pos["entry"], qty=pos["qty"],
                        stop=sig.stop, take=sig.take, ai_decision=review["decision"],
                        ai_confidence=review["confidence"], reasoning=review["reasoning"])
            notify(self.cfg, f"Куплено {sym}: {pos['qty']:.6g} по {pos['entry']:.6g}, стоп {sig.stop:.6g}, "
                             f"тейк {sig.take:.6g}\n{review['reasoning']}")

    def cycle(self) -> None:
        self.manage_positions()
        self.look_for_entries()

    def run_forever(self) -> None:
        notify(self.cfg, f"Бот запущен: режим {self.cfg['mode']}, {', '.join(self.cfg['symbols'])}, "
                         f"ИИ {'включён' if self.analyst.active else 'выключен'}")
        while True:
            try:
                self.cycle()
            except Exception as e:
                log.exception("ошибка цикла")
                notify(self.cfg, f"Ошибка: {e}")
            time.sleep(self.cfg["loop_interval_minutes"] * 60)
