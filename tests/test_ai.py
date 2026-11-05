import copy

import numpy as np
import pandas as pd
import pytest
import yaml

from bot import analyst, journal, news, risk, runner
from bot.broker import PaperBroker
from bot.strategy import Signal

BASE = yaml.safe_load(open("config.yaml", encoding="utf-8"))


def cfg_with_ai():
    c = copy.deepcopy(BASE)
    c["news"]["enabled"] = False
    c["secrets"] = {"llm_key": "k", "llm_base_url": "http://x", "llm_model": "m",
                    "exchange_key": "", "exchange_secret": "", "tg_token": "", "tg_chat": ""}
    return c


# --- ограничения решений ИИ ---------------------------------------------------
def test_safe_new_stop_only_tightens():
    assert analyst.safe_new_stop(95, 110, 100) == 100
    assert analyst.safe_new_stop(95, 110, 90) is None      # дальше от цены
    assert analyst.safe_new_stop(95, 110, 111) is None     # выше цены
    assert analyst.safe_new_stop(95, 110, "100") is None   # мусор


def test_regime_multiplier_clamped():
    assert analyst.clamp_multiplier(3) == 1.0
    assert analyst.clamp_multiplier(-1) == 0.0
    assert analyst.clamp_multiplier(None) == 1.0
    assert analyst.clamp_multiplier(0.5) == 0.5


def test_position_size_multiplier_cannot_increase():
    r = BASE["risk"]
    full = risk.position_size(1000, 1000, 100, 95, r, 1.0)
    assert risk.position_size(1000, 1000, 100, 95, r, 5.0) == full
    assert risk.position_size(1000, 1000, 100, 95, r, 0.5) == pytest.approx(full / 2)
    assert risk.position_size(1000, 1000, 100, 95, r, 0.0) == 0


@pytest.mark.parametrize("idea,ok", [
    ({"action": "buy", "stop": 96, "take": 110}, True),
    ({"action": "buy", "stop": 96, "take": 104}, False),   # R:R < 2
    ({"action": "buy", "stop": 99.5, "take": 110}, False), # стоп слишком близко
    ({"action": "buy", "stop": 80, "take": 200}, False),   # стоп слишком далеко
    ({"action": "buy", "stop": 101, "take": 110}, False),  # стоп выше цены
    ({"action": "none"}, False),
])
def test_validate_setup(idea, ok):
    sig, _ = analyst.validate_setup("X", idea, price=100, atr=2, min_rr=2.0)
    assert (sig is not None) == ok


def test_parse_reply_variants():
    d = analyst.parse_reply('```json\n{"action": "TIGHTEN", "confidence": "0.8", "stop": 5}\n```')
    assert d["action"] == "tighten" and d["confidence"] == 0.8


# --- новости ------------------------------------------------------------------
RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Bitcoin hits new high</title><pubDate>Thu, 05 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Solana network upgrade</title><pubDate>Thu, 05 Nov 2026 09:00:00 GMT</pubDate></item>
<item><title>Something unrelated</title><pubDate>Thu, 05 Nov 2026 08:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_news_filter_by_coin(monkeypatch):
    class R:
        content = RSS
        def raise_for_status(self): pass
    monkeypatch.setattr(news.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(news, "_cache", {})
    c = {"news": {"enabled": True, "max_items": 10, "max_age_hours": 10**6,
                  "rss_feeds": ["https://feed.example/rss"]}, "secrets": {}}
    assert "Bitcoin hits" in news.headlines(c, "BTC/USDT")
    assert "Solana" not in news.headlines(c, "BTC/USDT")
    assert "Solana" in news.headlines(c, "SOL/USDT")
    assert "unrelated" in news.headlines(c)


# --- полный цикл бота с фейковой биржей и фейковым ИИ -----------------------
def uptrend_rows(n=400):
    rng = np.random.default_rng(3)
    close = 100 * np.exp(np.cumsum(rng.normal(0.002, 0.01, n)))
    t0 = 1_700_000_000_000
    return [[t0 + i * 14_400_000, c, c * 1.01, c * 0.99, c, 100.0] for i, c in enumerate(close)]


class FakeExchange:
    def __init__(self):
        self.rows = uptrend_rows()
    def fetch_ohlcv(self, symbol, tf, limit=100, since=None):
        return self.rows[-limit:]
    def fetch_ticker(self, symbol):
        return {"last": self.rows[-1][4]}


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "make_exchange", lambda *a, **k: FakeExchange())
    monkeypatch.setattr(PaperBroker, "state_file", tmp_path / "state.json")
    monkeypatch.setattr(journal, "DATA_DIR", tmp_path)
    c = cfg_with_ai()
    c["symbols"] = ["ETH/USDT"]
    c["strategy"]["rsi_max"] = 100  # чтобы правила молчали не из-за RSI — проверяем сканер
    return runner.Bot(c)


def test_cycle_regime_scanner_and_management(bot, monkeypatch):
    calls = []

    def fake_ask(task, data):
        calls.append(task)
        if task is analyst.TASK_REGIME:
            return {"regime": "range", "risk_multiplier": 0.5, "confidence": 1, "reasoning": "боковик"}
        if task.startswith("Задача: механические правила"):
            price = bot.broker.price("ETH/USDT")
            return {"action": "buy", "confidence": 0.8, "stop": price * 0.96, "take": price * 1.12,
                    "setup": "отскок", "reasoning": "сильная поддержка"}
        if task is analyst.TASK_MANAGE:
            return {"action": "tighten", "confidence": 0.9, "stop": bot.broker.price("ETH/USDT") * 0.98,
                    "reasoning": "подтягиваю"}
        return {"decision": "approve", "confidence": 0.9, "reasoning": "ок"}

    monkeypatch.setattr(bot.analyst, "ask", fake_ask)
    monkeypatch.setattr(runner, "check_entry", lambda *a, **k: None)  # правила молчат
    bot.cycle()

    assert bot.st["regime"]["regime"] == "range"
    pos = bot.st["positions"]["ETH/USDT"]
    assert pos["source"] == "ai"
    # риск = 1% * 0.5 (режим) * 0.5 (идея ИИ) = 0.25% капитала
    assert pos["qty"] * (pos["entry"] - pos["stop"]) <= 1000 * 0.0025 * 1.02  # +проскальзывание

    old_stop = pos["stop"]
    bot.cycle()  # та же свеча: ИИ не вызывается повторно
    assert calls.count(analyst.TASK_MANAGE) == 0
    pos["last_ai_bar"] = None
    bot.manage_positions()
    assert bot.st["positions"]["ETH/USDT"]["stop"] > old_stop
    assert calls.count(analyst.TASK_REGIME) == 1  # режим кэшируется на сутки


def test_ai_close_and_errors(bot, monkeypatch):
    monkeypatch.setattr(runner, "check_entry", lambda *a, **k: None)
    monkeypatch.setattr(bot.analyst, "ask", lambda *a: (_ for _ in ()).throw(RuntimeError("сеть")))
    bot.cycle()  # ИИ недоступен: режим по правилам, сделок нет
    assert bot.st["regime"]["reasoning"].startswith("по правилам")
    assert not bot.st["positions"]

    price = bot.broker.price("ETH/USDT")
    bot.broker.buy("ETH/USDT", 1, price, price * 0.9, price * 1.3)
    monkeypatch.setattr(bot.analyst, "ask", lambda *a: {"action": "close", "confidence": 0.9, "reasoning": "разворот"})
    bot.manage_positions()
    assert not bot.st["positions"]


def test_panic_regime_blocks_entries(bot, monkeypatch):
    monkeypatch.setattr(bot.analyst, "ask", lambda task, data: (
        {"regime": "panic", "risk_multiplier": 0, "confidence": 1, "reasoning": "обвал"}
        if task is analyst.TASK_REGIME else
        {"action": "buy", "decision": "approve", "confidence": 1, "stop": 1, "take": 10**9, "reasoning": ""}))
    bot.cycle()
    assert not bot.st["positions"]
