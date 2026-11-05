"""Риск-менеджер. Жёсткие правила, которые не может обойти ни стратегия, ни ИИ."""
from datetime import datetime, timedelta, timezone


def position_size(equity: float, cash: float, entry: float, stop: float, r: dict,
                  multiplier: float = 1.0) -> float:
    """Количество монет так, чтобы при срабатывании стопа потерять risk_per_trade капитала.

    multiplier (0..1) уменьшает риск: режим рынка, сделки по идее ИИ и т.п. Увеличить нельзя.
    """
    per_unit = entry - stop
    multiplier = max(0.0, min(1.0, multiplier))
    if per_unit <= 0 or entry <= 0 or multiplier == 0:
        return 0.0
    qty = equity * r["risk_per_trade"] * multiplier / per_unit
    qty = min(qty, equity * r["max_position_pct"] / entry, cash * 0.98 / entry)
    return max(qty, 0.0)


def can_open(state: dict, equity: float, r: dict, now: datetime | None = None) -> tuple[bool, str]:
    now = now or datetime.now(timezone.utc)
    paused = state.get("paused_until")
    if paused and now < datetime.fromisoformat(paused):
        return False, f"пауза до {paused}"
    if len(state["positions"]) >= r["max_open_positions"]:
        return False, "достигнут лимит открытых позиций"
    day = state.get("day", {})
    if day.get("date") == now.date().isoformat() and day.get("start_equity"):
        dd = 1 - equity / day["start_equity"]
        if dd >= r["max_daily_loss"]:
            return False, f"дневной убыток {dd:.1%} — стоп на сегодня"
    return True, "ok"


def roll_day(state: dict, equity: float, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    if state.get("day", {}).get("date") != today:
        state["day"] = {"date": today, "start_equity": equity}


def register_result(state: dict, pnl: float, r: dict, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    if pnl < 0:
        state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
        if state["consecutive_losses"] >= r["max_consecutive_losses"]:
            state["paused_until"] = (now + timedelta(hours=r["pause_hours_after_losses"])).isoformat()
            state["consecutive_losses"] = 0
    else:
        state["consecutive_losses"] = 0
