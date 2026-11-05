"""Основной торговый цикл."""
import logging
import time
from datetime import datetime, timezone

from . import journal, risk
from .analyst import Analyst, apply_review, market_block, safe_new_stop, validate_setup
from .broker import make_broker
from .data import fetch_ohlcv, make_exchange
from .news import headlines
from .notify import notify
from .strategy import check_entry, min_bars, prepare, trend_broken

log = logging.getLogger("bot")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hours_since(iso: str | None) -> float:
    if not iso:
        return float("inf")
    return (_now() - datetime.fromisoformat(iso)).total_seconds() / 3600


class Bot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.mode = cfg["mode"]
        self.ex = make_exchange(cfg, authenticated=self.mode == "live")
        self.broker = make_broker(cfg, self.ex)
        self.analyst = Analyst(cfg)
        self.s, self.r, self.ai = cfg["strategy"], cfg["risk"], cfg["llm"]

    @property
    def st(self) -> dict:
        return self.broker.state

    def candles(self, symbol, tf=None, limit=None):
        limit = limit or max(self.cfg["history_bars"], min_bars(self.s) + 50)
        return prepare(fetch_ohlcv(self.ex, symbol, tf or self.cfg["timeframe"], limit), self.s)

    def market(self, symbol, df):
        daily = self.candles(symbol, "1d", 250)
        return market_block(symbol, df, daily, headlines(self.cfg, symbol), self.st.get("regime"))

    # --- 3. режим рынка (раз в N часов) --------------------------------------
    def update_regime(self) -> dict:
        reg = self.st.get("regime")
        if reg and _hours_since(reg.get("at")) < self.ai.get("regime_every_hours", 24):
            return reg
        sym = self.ai.get("regime_symbol", "BTC/USDT")
        btc_4h, btc_d = self.candles(sym), self.candles(sym, "1d", 250)
        new = None
        if self.analyst.active and self.ai.get("regime"):
            try:
                new = self.analyst.market_regime(btc_4h, btc_d, headlines(self.cfg))
            except Exception as e:
                log.warning("режим рынка от ИИ: %s", e)
        if new is None:  # запасной вариант по правилам: BTC выше/ниже дневной EMA200
            up = btc_d["close"].iloc[-1] > btc_d["ema_trend"].iloc[-1]
            new = {"regime": "trend_up" if up else "trend_down", "risk_multiplier": 1.0 if up else 0.5,
                   "reasoning": f"по правилам: {sym} {'выше' if up else 'ниже'} дневной EMA{self.s['ema_trend']}"}
        new["at"] = _now().isoformat(timespec="seconds")
        if not reg or reg.get("regime") != new["regime"] or reg.get("risk_multiplier") != new["risk_multiplier"]:
            notify(self.cfg, f"Режим рынка: {new['regime']}, риск ×{new['risk_multiplier']:.2f}\n{new['reasoning']}")
        journal.log("regime", mode=self.mode, symbol=sym, ai_decision=new["regime"],
                    ai_confidence=new["risk_multiplier"], reasoning=new["reasoning"])
        self.st["regime"] = new
        self.broker.save()
        return new

    # --- 2. сопровождение позиций ----------------------------------------------
    def close(self, sym: str, price: float, reason: str) -> None:
        pos = self.st["positions"][sym]
        pnl = self.broker.sell(sym, price)
        risk.register_result(self.st, pnl, self.r)
        self.broker.save()
        journal.log("close", mode=self.mode, symbol=sym, price=price, qty=pos["qty"],
                    pnl=round(pnl, 4), reasoning=reason)
        notify(self.cfg, f"Закрыта {sym} по {price:.6g} ({reason}), PnL {pnl:+.2f} USDT")

    def manage_positions(self) -> None:
        for sym, pos in list(self.st["positions"].items()):
            price = self.broker.price(sym)
            r_unit = pos.get("risk_per_unit", pos["entry"] - pos["stop"])
            if price >= pos["entry"] + r_unit and pos["stop"] < pos["entry"]:
                pos["stop"] = pos["entry"]
                self.broker.save()
                notify(self.cfg, f"{sym}: стоп перенесён в безубыток {pos['entry']:.6g}")

            if price <= pos["stop"]:
                self.close(sym, price, "стоп")
                continue
            if price >= pos["take"]:
                self.close(sym, price, "тейк")
                continue

            df = self.candles(sym)
            if trend_broken(df):
                self.close(sym, price, "слом тренда (закрытие ниже EMA50)")
                continue

            bar = df.index[-1].isoformat()
            if self.ai.get("manage_positions") and self.analyst.active and pos.get("last_ai_bar") != bar:
                pos["last_ai_bar"] = bar
                self.broker.save()
                d = self.analyst.manage_position(sym, pos, price, self.market(sym, df))
                journal.log("manage", mode=self.mode, symbol=sym, price=price, stop=pos["stop"],
                            ai_decision=d["action"], ai_confidence=d["confidence"], reasoning=d["reasoning"])
                if d["action"] == "close":
                    self.close(sym, price, f"решение ИИ: {d['reasoning']}")
                elif d["action"] == "tighten":
                    new_stop = safe_new_stop(pos["stop"], price, d.get("stop"))
                    if new_stop:
                        pos["stop"] = new_stop
                        self.broker.save()
                        notify(self.cfg, f"{sym}: ИИ подтянул стоп до {new_stop:.6g}\n{d['reasoning']}")

    # --- входы: 1. сигналы правил + 4. идеи ИИ ----------------------------------
    def open(self, sig, equity: float, multiplier: float, source: str, review: dict) -> None:
        sym = sig.symbol
        price = self.broker.price(sym)
        if price <= sig.stop:
            return
        qty = risk.position_size(equity, self.broker.cash(), price, sig.stop, self.r, multiplier)
        if qty * price < 10:  # минимальный ордер на большинстве бирж
            journal.log("skip", mode=self.mode, symbol=sym, price=price, reasoning="слишком маленький объём")
            return
        pos = self.broker.buy(sym, qty, price, sig.stop, sig.take)
        pos.update(risk_per_unit=pos["entry"] - sig.stop, source=source,
                   last_ai_bar=self.st.get("last_signal_bar", {}).get(sym))  # на свече входа не пересматриваем
        self.broker.save()
        journal.log("open", mode=self.mode, symbol=sym, price=pos["entry"], qty=pos["qty"], stop=sig.stop,
                    take=sig.take, ai_decision=f"{source}:{review.get('decision', review.get('action'))}",
                    ai_confidence=review["confidence"], reasoning=review["reasoning"])
        label = "по правилам" if source == "rules" else "по идее ИИ"
        notify(self.cfg, f"Куплено {sym} ({label}, риск ×{multiplier:.2f}): {pos['qty']:.6g} по "
                         f"{pos['entry']:.6g}, стоп {sig.stop:.6g}, тейк {sig.take:.6g}\n{review['reasoning']}")

    def look_for_entries(self, regime: dict) -> None:
        seen = self.st.setdefault("last_signal_bar", {})
        scans = self.st.setdefault("last_scan", {})
        prices = {s: self.broker.price(s) for s in self.st["positions"]}
        equity = self.broker.equity(prices)
        risk.roll_day(self.st, equity)
        reg_mult = regime.get("risk_multiplier", 1.0)

        for sym in self.cfg["symbols"]:
            if sym in self.st["positions"]:
                continue
            df = self.candles(sym)
            bar = df.index[-1].isoformat()
            if seen.get(sym) == bar:  # эту свечу уже анализировали
                continue
            seen[sym] = bar
            self.broker.save()

            if reg_mult <= 0:
                journal.log("skip", mode=self.mode, symbol=sym, reasoning=f"режим рынка {regime['regime']}: входы запрещены")
                continue
            ok, why = risk.can_open(self.st, equity, self.r)
            if not ok:
                journal.log("skip", mode=self.mode, symbol=sym, reasoning=why)
                continue

            sig = check_entry(sym, df, self.s)
            if sig:  # 1. сигнал правил → проверка ИИ
                review = self.analyst.review_signal(sig, self.market(sym, df) if self.analyst.active else "")
                journal.log("signal", mode=self.mode, symbol=sym, price=sig.entry, stop=sig.stop, take=sig.take,
                            ai_decision=review["decision"], ai_confidence=review["confidence"],
                            reasoning=review["reasoning"])
                if review["decision"] != "approve":
                    notify(self.cfg, f"{sym}: сигнал отклонён ИИ — {review['reasoning']}")
                    continue
                self.open(apply_review(sig, review), equity, reg_mult, "rules", review)

            elif (self.ai.get("scanner") and self.analyst.active
                  and _hours_since(scans.get(sym)) >= self.ai.get("scan_every_hours", 12)):
                # 4. правила молчат → ИИ сам ищет сделку
                scans[sym] = _now().isoformat(timespec="seconds")
                self.broker.save()
                idea = self.analyst.find_setup(self.market(sym, df), self.ai.get("scanner_min_rr", 2.0))
                price = float(df["close"].iloc[-1])
                sig, why = validate_setup(sym, idea, price, float(df["atr"].iloc[-1]),
                                          self.ai.get("scanner_min_rr", 2.0))
                journal.log("ai_idea", mode=self.mode, symbol=sym, price=price, stop=idea.get("stop"),
                            take=idea.get("take"), ai_decision=idea.get("action"),
                            ai_confidence=idea["confidence"], reasoning=f"{idea['reasoning']} | проверка: {why}")
                if sig:
                    self.open(sig, equity, reg_mult * self.ai.get("scanner_risk_multiplier", 0.5), "ai", idea)

    def cycle(self) -> None:
        regime = self.update_regime()
        self.manage_positions()
        self.look_for_entries(regime)

    def run_forever(self) -> None:
        roles = [n for k, n in [("manage_positions", "сопровождение"), ("regime", "режим рынка"),
                                ("scanner", "поиск сделок")] if self.ai.get(k)]
        ai = f"включён ({', '.join(['проверка сигналов'] + roles)})" if self.analyst.active else "выключен"
        notify(self.cfg, f"Бот запущен: режим {self.mode}, {', '.join(self.cfg['symbols'])}, ИИ {ai}")
        while True:
            try:
                self.cycle()
            except Exception as e:
                log.exception("ошибка цикла")
                notify(self.cfg, f"Ошибка: {e}")
            time.sleep(self.cfg["loop_interval_minutes"] * 60)
