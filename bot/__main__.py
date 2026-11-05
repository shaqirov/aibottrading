"""Запуск:
  python -m bot run                 — торговать (paper или live — см. config.yaml)
  python -m bot once                — один цикл и выход
  python -m bot backtest --days 730 — проверка стратегии на истории
"""
import argparse
import logging

from .config import load_config


def main() -> None:
    ap = argparse.ArgumentParser(prog="bot")
    ap.add_argument("command", choices=["run", "once", "backtest"])
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--config", default=None)
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(a.config) if a.config else load_config()

    if a.command == "backtest":
        from .backtest import format_report, run
        from .data import fetch_history, make_exchange
        ex = make_exchange(cfg)
        for sym in cfg["symbols"]:
            df = fetch_history(ex, sym, cfg["timeframe"], a.days)
            print(format_report(sym, run(df, cfg, sym)))
        return

    from .runner import Bot
    bot = Bot(cfg)
    if a.command == "once":
        bot.cycle()
    else:
        bot.run_forever()


if __name__ == "__main__":
    main()
