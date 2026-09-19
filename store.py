# store.py
# Persistence for live news (1 day), the hourly send queue, and the send log.

import hashlib
import json
import os
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_FILE = os.path.join(BASE_DIR, "live_news.json")
QUEUE_FILE = os.path.join(BASE_DIR, "send_queue.json")
LOG_FILE = os.path.join(BASE_DIR, "whatsapp_log.json")
SENT_FILE = os.path.join(BASE_DIR, "sent_whatsapp.json")

SEND_INTERVAL_SECONDS = 60 * 60  # one WhatsApp post per hour
LOG_LIMIT = 200

_lock = threading.Lock()


def article_id(title, link):
    return hashlib.md5(f"{title}{link}".encode()).hexdigest()[:16]


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_sent_ids():
    with _lock:
        return set(_read_json(SENT_FILE, []))


def _save_sent_ids_unlocked(sent_ids):
    _write_json(SENT_FILE, sorted(sent_ids))


def load_live():
    with _lock:
        data = _read_json(LIVE_FILE, {"day": "", "fetched_at": "", "articles": []})
    day = today_str()
    if data.get("day") != day:
        return {"day": day, "fetched_at": "", "articles": [], "stale": True}
    return {**data, "stale": False}


def save_live(articles):
    data = {
        "day": today_str(),
        "fetched_at": now_iso(),
        "articles": articles,
    }
    with _lock:
        _write_json(LIVE_FILE, data)
    return {**data, "stale": False}


def load_queue():
    with _lock:
        data = _read_json(QUEUE_FILE, {"last_sent_at": None, "items": []})
    if "items" not in data:
        data = {"last_sent_at": None, "items": []}
    return data


def save_queue(queue):
    with _lock:
        _write_json(QUEUE_FILE, queue)


def enqueue_articles(articles):
    """Add unseen articles to the hourly queue. Returns how many were added."""
    sent = load_sent_ids()
    with _lock:
        queue = _read_json(QUEUE_FILE, {"last_sent_at": None, "items": []})
        queued_ids = {item.get("id") for item in queue.get("items", [])}
        added = []
        for article in articles:
            aid = article.get("id") or article_id(article["title"], article["link"])
            if aid in sent or aid in queued_ids:
                continue
            item = {
                "id": aid,
                "title": article["title"],
                "link": article["link"],
                "source": article.get("source", ""),
                "country": article.get("country", ""),
                "date": article.get("date", ""),
                "queued_at": now_iso(),
            }
            queue.setdefault("items", []).append(item)
            queued_ids.add(aid)
            added.append(item)
        _write_json(QUEUE_FILE, queue)
    return added


def pop_next_queued():
    """Remove and return the next queued article, or None."""
    with _lock:
        queue = _read_json(QUEUE_FILE, {"last_sent_at": None, "items": []})
        items = queue.get("items", [])
        if not items:
            return None, queue
        item = items.pop(0)
        queue["items"] = items
        _write_json(QUEUE_FILE, queue)
        return item, queue


def _log_entry(item, status, group_id="", error=""):
    aid = item.get("id") or article_id(item.get("title", ""), item.get("link", ""))
    return {
        "id": aid,
        "title": item.get("title", ""),
        "link": item.get("link", ""),
        "source": item.get("source", ""),
        "country": item.get("country", ""),
        "date": item.get("date", ""),
        "sent_at": now_iso(),
        "status": status,
        "group_id": group_id or "",
        "error": error or "",
    }


def append_log(entry):
    with _lock:
        log = _read_json(LOG_FILE, [])
        log.insert(0, entry)
        _write_json(LOG_FILE, log[:LOG_LIMIT])
    return entry


def mark_sent(item, group_id=""):
    entry = _log_entry(item, "sent", group_id=group_id)
    with _lock:
        log = _read_json(LOG_FILE, [])
        log.insert(0, entry)
        _write_json(LOG_FILE, log[:LOG_LIMIT])
        sent = set(_read_json(SENT_FILE, []))
        sent.add(entry["id"])
        _save_sent_ids_unlocked(sent)
        queue = _read_json(QUEUE_FILE, {"last_sent_at": None, "items": []})
        queue["last_sent_at"] = entry["sent_at"]
        queue["items"] = [q for q in queue.get("items", []) if q.get("id") != entry["id"]]
        _write_json(QUEUE_FILE, queue)
    return entry


def mark_failed(item, group_id="", error=""):
    return append_log(_log_entry(item, "failed", group_id=group_id, error=error))


def load_log(limit=200):
    with _lock:
        log = _read_json(LOG_FILE, [])
    return log[:limit]


def seconds_until_next_send(queue=None):
    if queue is None:
        queue = load_queue()
    last = queue.get("last_sent_at")
    if not last:
        items = queue.get("items") or []
        last = items[0].get("queued_at") if items else None
    if not last:
        return 0
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return 0
    remaining = SEND_INTERVAL_SECONDS - (datetime.now() - last_dt).total_seconds()
    return max(0, int(remaining))


def format_whatsapp_message(article):
    """WhatsApp text without category labels."""
    country = (article.get("country") or "").strip()
    source = (article.get("source") or "").strip()
    meta = " · ".join(part for part in [country, source] if part)
    lines = [article.get("title", "").strip()]
    if meta:
        lines.append(meta)
    if article.get("link"):
        lines.append(article["link"])
    return "\n".join(lines)
