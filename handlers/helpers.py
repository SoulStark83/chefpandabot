# -*- coding: utf-8 -*-
import re
from datetime import date
from typing import Any

from telegram import Update
from config.settings import ADMIN_CHAT


def is_admin(update: Update) -> bool:
    if not update.message:
        return False
    return str(update.effective_chat.id) == ADMIN_CHAT


def safe_text(s: Any) -> str:
    return "" if s is None else str(s).strip()


def is_valid_url(s: str) -> bool:
    return bool(re.match(r"^https?://", safe_text(s), re.IGNORECASE))


def truncate(s: str, n: int = 120) -> str:
    s = safe_text(s)
    return s if len(s) <= n else s[:n-3] + "..."


def default_modo_for_plataforma(plataforma: str) -> str:
    if plataforma == "google":
        return "scraping"
    return "manual"


def today_iso() -> str:
    return str(date.today())
