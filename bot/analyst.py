"""ИИ-аналитик: второе мнение «опытного трейдера» по сигналу правил.

ИИ НЕ может увеличить риск или сдвинуть стоп дальше — он может только
одобрить сделку, отклонить её или подтянуть стоп/тейк ближе к цене.
Работает с любым OpenAI-совместимым API (настройки в .env).
"""
import json
import re

import pandas as pd
import requests

from .strategy import Signal

SYSTEM_PROMPT = """Ты — опытный свинг-трейдер криптовалют с 15-летним стажем.
Твои принципы:
- Капитал важнее прибыли. Лучше пропустить сделку, чем войти в сомнительную.
- Торгуешь только по тренду и только с понятной точкой отмены идеи (стоп).
- Не входишь, если цена прямо под сильным сопротивлением, после вертикального роста
  или при аномальном объёме против позиции.
- Оцениваешь контекст: старший таймфрейм, структуру максимумов/минимумов, волатильность.
- Честно говоришь «не знаю» — тогда confidence низкий.

Тебе дают сигнал от механической стратегии и рыночные данные. Реши, брать ли сделку.
Ответь ТОЛЬКО JSON без пояснений вокруг:
{"decision": "approve" | "reject",
 "confidence": число 0..1,
 "stop": число или null (можно только ПОДНЯТЬ стоп ближе к цене),
 "take": число или null,
 "reasoning": "кратко, 2-4 предложения по-русски"}"""


def _table(df: pd.DataFrame, n: int) -> str:
    cols = ["open", "high", "low", "close", "volume", "ema_fast", "ema_slow", "ema_trend", "rsi", "atr"]
    t = df[cols].tail(n).copy()
    t.index = t.index.strftime("%m-%d %H:%M")
    return t.to_csv(float_format="%.6g")


def build_prompt(sig: Signal, df: pd.DataFrame, df_daily: pd.DataFrame | None) -> str:
    parts = [
        f"Инструмент: {sig.symbol}",
        f"Сигнал: {sig.side} по {sig.entry:.6g}, стоп {sig.stop:.6g}, тейк {sig.take:.6g}",
        "Причины сигнала: " + "; ".join(sig.reasons),
        "",
        "Последние 60 свечей 4h (с индикаторами):",
        _table(df, 60),
    ]
    if df_daily is not None and len(df_daily):
        parts += ["Последние 30 дневных свечей:", _table(df_daily, 30)]
    return "\n".join(parts)


def parse_reply(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("в ответе нет JSON")
    d = json.loads(m.group(0))
    d["decision"] = str(d.get("decision", "reject")).lower()
    d["confidence"] = float(d.get("confidence", 0) or 0)
    d["reasoning"] = str(d.get("reasoning", ""))
    return d


def apply_review(sig: Signal, review: dict) -> Signal:
    """Применяем поправки ИИ только в безопасную сторону."""
    stop = review.get("stop")
    if isinstance(stop, (int, float)) and sig.stop < stop < sig.entry:
        sig.stop = float(stop)
    take = review.get("take")
    if isinstance(take, (int, float)) and take > sig.entry:
        sig.take = float(take)
    return sig


class Analyst:
    def __init__(self, cfg: dict):
        self.cfg = cfg["llm"]
        sec = cfg["secrets"]
        self.key, self.url, self.model = sec["llm_key"], sec["llm_base_url"], sec["llm_model"]

    @property
    def active(self) -> bool:
        return bool(self.cfg.get("enabled") and self.key and self.url and self.model)

    def review(self, sig: Signal, df: pd.DataFrame, df_daily=None) -> dict:
        if not self.active:
            return {"decision": "approve", "confidence": 1.0, "reasoning": "ИИ отключён — только правила"}
        try:
            r = requests.post(
                f"{self.url}/chat/completions",
                headers={"Authorization": f"Bearer {self.key}"},
                json={
                    "model": self.model,
                    "temperature": 0.2,
                    "max_tokens": 600,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": build_prompt(sig, df, df_daily)},
                    ],
                },
                timeout=self.cfg.get("timeout_seconds", 90),
            )
            r.raise_for_status()
            d = parse_reply(r.json()["choices"][0]["message"]["content"])
        except Exception as e:  # при сбое ИИ сделку НЕ берём
            return {"decision": "reject", "confidence": 0.0, "reasoning": f"ошибка ИИ: {e}"}
        if d["confidence"] < self.cfg.get("min_confidence", 0.6):
            d["decision"] = "reject"
        return d
