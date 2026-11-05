"""Свежие новости для ИИ: CryptoPanic (если есть токен) + бесплатные RSS-ленты.

Заголовки новостей — это внешние данные. ИИ получает их как контекст,
а все его решения всё равно проходят через жёсткий риск-менеджер.
"""
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

log = logging.getLogger("bot")

NAMES = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "ether", "eth"],
    "SOL": ["solana", "sol"],
    "XRP": ["xrp", "ripple"],
    "BNB": ["bnb", "binance coin"],
    "DOGE": ["dogecoin", "doge"],
    "ADA": ["cardano", "ada"],
    "TON": ["toncoin", "ton"],
}

_cache: dict[str, tuple[float, list[dict]]] = {}
CACHE_SECONDS = 1800


def _parse_time(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return None


def _cryptopanic(token: str, url: str) -> list[dict]:
    r = requests.get(url, params={"auth_token": token, "public": "true", "kind": "news"}, timeout=20)
    r.raise_for_status()
    out = []
    for it in r.json().get("results", []):
        out.append({"title": it.get("title", ""), "time": _parse_time(it.get("published_at", "")),
                    "source": "cryptopanic"})
    return out


def _rss(url: str) -> list[dict]:
    r = requests.get(url, timeout=20, headers={"User-Agent": "aibottrading/1.0"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for item in root.iter("item"):
        out.append({"title": (item.findtext("title") or "").strip(),
                    "time": _parse_time(item.findtext("pubDate") or ""),
                    "source": url.split("/")[2]})
    return out


def _all_news(cfg: dict) -> list[dict]:
    if "all" in _cache and time.time() - _cache["all"][0] < CACHE_SECONDS:
        return _cache["all"][1]
    n = cfg["news"]
    items: list[dict] = []
    token = cfg["secrets"].get("cryptopanic_token")
    if token:
        try:
            items += _cryptopanic(token, n["cryptopanic_url"])
        except Exception as e:
            log.warning("CryptoPanic: %s", e)
    for url in n.get("rss_feeds", []):
        try:
            items += _rss(url)
        except Exception as e:
            log.warning("RSS %s: %s", url, e)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=n["max_age_hours"])
    items = [i for i in items if i["title"] and (i["time"] is None or i["time"] >= cutoff)]
    items.sort(key=lambda i: i["time"] or cutoff, reverse=True)
    _cache["all"] = (time.time(), items)
    return items


def _fmt(items: list[dict]) -> str:
    lines = []
    for i in items:
        t = i["time"].strftime("%m-%d %H:%M") if i["time"] else "?"
        lines.append(f"- [{t}] {i['title'][:200]}")
    return "\n".join(lines) or "нет свежих новостей"


def headlines(cfg: dict, symbol: str | None = None) -> str:
    """Заголовки по монете (symbol='ETH/USDT') или общие новости рынка (symbol=None)."""
    if not cfg.get("news", {}).get("enabled"):
        return "новости отключены"
    items = _all_news(cfg)
    if symbol:
        base = symbol.split("/")[0].upper()
        keys = NAMES.get(base, [base.lower()])
        pat = re.compile(r"\b(" + "|".join(map(re.escape, keys)) + r")\b", re.I)
        items = [i for i in items if pat.search(i["title"])]
    return _fmt(items[: cfg["news"]["max_items"]])
