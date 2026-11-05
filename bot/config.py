import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def load_config(path: str | Path = ROOT / "config.yaml") -> dict:
    load_dotenv(ROOT / ".env")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["secrets"] = {
        "exchange_key": os.getenv("EXCHANGE_API_KEY", ""),
        "exchange_secret": os.getenv("EXCHANGE_API_SECRET", ""),
        "llm_key": os.getenv("LLM_API_KEY", ""),
        "llm_base_url": os.getenv("LLM_BASE_URL", "").rstrip("/"),
        "llm_model": os.getenv("LLM_MODEL", ""),
        "tg_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "tg_chat": os.getenv("TELEGRAM_CHAT_ID", ""),
        "cryptopanic_token": os.getenv("CRYPTOPANIC_TOKEN", ""),
    }
    DATA_DIR.mkdir(exist_ok=True)
    return cfg
