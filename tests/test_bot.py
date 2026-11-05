import numpy as np
import pandas as pd
import yaml

from bot import backtest, risk
from bot.analyst import apply_review, parse_reply
from bot.strategy import Signal, check_entry, prepare

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))


def synthetic(n=1500, seed=1, drift=0.0006):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n))
    idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.uniform(100, 200, n)}, index=idx)


def test_signal_has_valid_levels():
    df = prepare(synthetic(), CFG["strategy"])
    sigs = [check_entry("X", df.iloc[: i + 1], CFG["strategy"]) for i in range(300, len(df))]
    sigs = [s for s in sigs if s]
    assert sigs, "на трендовых данных должны быть сигналы"
    for s in sigs:
        assert s.stop < s.entry < s.take


def test_no_signal_in_downtrend():
    df = prepare(synthetic(drift=-0.002), CFG["strategy"])
    assert all(check_entry("X", df.iloc[: i + 1], CFG["strategy"]) is None for i in range(400, len(df)))


def test_position_size_respects_risk():
    r = CFG["risk"]
    qty = risk.position_size(1000, 1000, entry=100, stop=95, r=r)
    assert qty * 5 <= 1000 * r["risk_per_trade"] + 1e-9
    assert qty * 100 <= 1000 * r["max_position_pct"] + 1e-9


def test_pause_after_losses():
    st = {"positions": {}}
    for _ in range(CFG["risk"]["max_consecutive_losses"]):
        risk.register_result(st, -1, CFG["risk"])
    ok, _ = risk.can_open(st, 1000, CFG["risk"])
    assert not ok


def test_daily_loss_limit():
    st = {"positions": {}}
    risk.roll_day(st, 1000)
    assert risk.can_open(st, 990, CFG["risk"])[0]
    assert not risk.can_open(st, 960, CFG["risk"])[0]


def test_ai_cannot_widen_stop():
    sig = Signal("X", "buy", 100, 95, 112)
    apply_review(sig, {"stop": 90, "take": 120})
    assert sig.stop == 95 and sig.take == 120
    apply_review(sig, {"stop": 97})
    assert sig.stop == 97


def test_parse_reply():
    d = parse_reply('Вот ответ: {"decision": "Approve", "confidence": 0.7, "reasoning": "ок"}')
    assert d["decision"] == "approve" and d["confidence"] == 0.7


def test_backtest_runs():
    st = backtest.run(synthetic(), CFG)
    assert st["trades"] > 0
    assert 0 <= st["max_drawdown"] < 1
