"""Исполнение ордеров: бумажный и реальный брокер.

Стоп и тейк отслеживает сам бот (проверка каждый цикл) и закрывает позицию
рыночным ордером. Поэтому бот должен работать постоянно (VPS).
"""
import json
from datetime import datetime, timezone

from .config import DATA_DIR

QUOTE = "USDT"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BaseBroker:
    state_file = DATA_DIR / "state.json"

    def __init__(self, cfg: dict, exchange):
        self.cfg, self.ex = cfg, exchange
        self.state = self._load()

    def _default_state(self) -> dict:
        return {"positions": {}, "consecutive_losses": 0, "paused_until": None, "day": {}}

    def _load(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return self._default_state()

    def save(self) -> None:
        self.state_file.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")

    def price(self, symbol: str) -> float:
        return float(self.ex.fetch_ticker(symbol)["last"])

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash() + sum(p["qty"] * prices.get(s, p["entry"]) for s, p in self.state["positions"].items())


class PaperBroker(BaseBroker):
    state_file = DATA_DIR / "paper_state.json"

    def _default_state(self) -> dict:
        st = super()._default_state()
        st["cash"] = float(self.cfg["paper"]["starting_balance"])
        return st

    def cash(self) -> float:
        return self.state["cash"]

    def buy(self, symbol, qty, price, stop, take) -> dict:
        p = self.cfg["paper"]
        fill = price * (1 + p["slippage"])
        cost = qty * fill * (1 + p["fee"])
        self.state["cash"] -= cost
        pos = {"qty": qty, "entry": fill, "stop": stop, "take": take, "opened_at": _now(), "cost": cost}
        self.state["positions"][symbol] = pos
        self.save()
        return pos

    def sell(self, symbol, price) -> float:
        p = self.cfg["paper"]
        pos = self.state["positions"].pop(symbol)
        proceeds = pos["qty"] * price * (1 - p["slippage"]) * (1 - p["fee"])
        self.state["cash"] += proceeds
        self.save()
        return proceeds - pos["cost"]


class LiveBroker(BaseBroker):
    def cash(self) -> float:
        return float(self.ex.fetch_balance()["free"].get(QUOTE, 0.0))

    def buy(self, symbol, qty, price, stop, take) -> dict:
        qty = float(self.ex.amount_to_precision(symbol, qty))
        o = self.ex.create_market_buy_order(symbol, qty)
        fill = float(o.get("average") or price)
        filled = float(o.get("filled") or qty)
        pos = {"qty": filled, "entry": fill, "stop": stop, "take": take, "opened_at": _now(),
               "cost": float(o.get("cost") or filled * fill), "order_id": o.get("id")}
        self.state["positions"][symbol] = pos
        self.save()
        return pos

    def sell(self, symbol, price) -> float:
        pos = self.state["positions"][symbol]
        base = symbol.split("/")[0]
        free = float(self.ex.fetch_balance()["free"].get(base, 0.0))
        qty = float(self.ex.amount_to_precision(symbol, min(pos["qty"], free)))
        o = self.ex.create_market_sell_order(symbol, qty)
        proceeds = float(o.get("cost") or qty * float(o.get("average") or price))
        self.state["positions"].pop(symbol)
        self.save()
        return proceeds - pos["cost"]


def make_broker(cfg: dict, exchange):
    if cfg["mode"] == "live":
        if not (cfg["secrets"]["exchange_key"] and cfg["secrets"]["exchange_secret"]):
            raise SystemExit("mode: live требует EXCHANGE_API_KEY и EXCHANGE_API_SECRET в .env")
        return LiveBroker(cfg, exchange)
    return PaperBroker(cfg, exchange)
