import html
import re

import requests

from .config import cfg

API = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


def _call(method, params=None, timeout=60):
    # "from None" keeps the bot token (inside the URL) out of tracebacks in public logs
    try:
        r = requests.post(f"{API}/bot{cfg.tg_token}/{method}", json=params or {}, timeout=timeout)
    except requests.RequestException:
        raise TelegramError(f"Telegram {method}: network error") from None
    try:
        data = r.json()
    except ValueError:
        raise TelegramError(f"Telegram {method}: bad response {r.status_code}") from None
    if not data.get("ok"):
        raise TelegramError(f"Telegram {method}: {data.get('error_code')} {data.get('description')}")
    return data["result"]


def send_message(text, chat=None):
    params = {"chat_id": chat or cfg.tg_chat, "text": text[:4000], "parse_mode": "HTML",
              "disable_web_page_preview": True}
    try:
        return _call("sendMessage", params)
    except TelegramError as e:
        if "parse entities" not in str(e):
            raise
        plain = html.unescape(re.sub(r"<[^>]+>", "", text))
        params.pop("parse_mode")
        params["text"] = plain[:4000]
        return _call("sendMessage", params)


def send_document(file_id, caption="", chat=None):
    return _call("sendDocument", {"chat_id": chat or cfg.tg_chat, "document": file_id, "caption": caption[:1000]})


def get_updates(offset=None):
    params = {"timeout": 0, "limit": 20, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    return _call("getUpdates", params)


def download_file(file_id, max_bytes=15_000_000):
    info = _call("getFile", {"file_id": file_id})
    if info.get("file_size", 0) > max_bytes:
        raise TelegramError("That file is too large (max 15 MB).")
    try:
        r = requests.get(f"{API}/file/bot{cfg.tg_token}/{info['file_path']}", timeout=60)
    except requests.RequestException:
        raise TelegramError("Telegram file download: network error") from None
    if r.status_code != 200 or len(r.content) > max_bytes:
        raise TelegramError("Telegram file download failed")
    return r.content


def delete_webhook():
    return _call("deleteWebhook", {"drop_pending_updates": False})
