"""Журнал сделок и решений (CSV) — основа для разбора ошибок."""
import csv
from datetime import datetime, timezone

from .config import DATA_DIR

FIELDS = ["time", "mode", "event", "symbol", "price", "qty", "stop", "take", "pnl",
          "ai_decision", "ai_confidence", "reasoning"]


def log(event: str, **kw) -> None:
    path = DATA_DIR / "journal.csv"
    new = not path.exists()
    row = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **kw}
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)
