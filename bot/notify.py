import logging

import requests

log = logging.getLogger("bot")


def notify(cfg: dict, text: str) -> None:
    log.info(text)
    tok, chat = cfg["secrets"]["tg_token"], cfg["secrets"]["tg_chat"]
    if not (tok and chat):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text}, timeout=15)
    except Exception as e:
        log.warning("Telegram: %s", e)
