# store.py
# Persistence for live news (1 day), the discovery log, the send queue, the
# send log, and the auto-send toggle.

import hashlib
import json
import os
import threading
from datetime import datetime

from llm_judge import AWARD_CATEGORIES

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_FILE = os.path.join(BASE_DIR, "live_news.json")
QUEUE_FILE = os.path.join(BASE_DIR, "send_queue.json")
LOG_FILE = os.path.join(BASE_DIR, "whatsapp_log.json")
SENT_FILE = os.path.join(BASE_DIR, "sent_whatsapp.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
EXTRACTED_FILE = os.path.join(BASE_DIR, "extracted_news.json")
LLM_PICKS_FILE = os.path.join(BASE_DIR, "llm_picks.json")

LOG_LIMIT = 200
NEWS_LOG_LIMIT = 500
DEFAULT_SETTINGS = {"auto_send_enabled": False}

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


def merge_into_live(new_articles):
    """Replace today's live feed with LLM-approved award articles.

    Old unjudged items are dropped so Live never shows keyword leftovers.
    Returns (live, newly_added).
    """
    day = today_str()
    with _lock:
        data = _read_json(LIVE_FILE, {"day": "", "articles": []})
        if data.get("day") != day:
            data = {"day": day, "articles": []}
        existing = {
            a["id"]: a
            for a in data.get("articles", [])
            if a.get("llm_approved") and a.get("category") in AWARD_CATEGORIES
        }
        now = now_iso()
        newly_added = []
        for article in new_articles:
            article = {**article, "llm_approved": True}
            if article["id"] not in existing:
                article = {**article, "discovered_at": now}
                newly_added.append(article)
            else:
                article = {**existing[article["id"]], **article}
            existing[article["id"]] = article
        data = {"day": day, "fetched_at": now, "articles": list(existing.values())}
        _write_json(LIVE_FILE, data)
    return {**data, "stale": False}, newly_added


def load_queue():
    with _lock:
        data = _read_json(QUEUE_FILE, {"last_sent_at": None, "items": []})
    if "items" not in data:
        data = {"last_sent_at": None, "items": []}
    return data


def save_queue(queue):
    with _lock:
        _write_json(QUEUE_FILE, queue)


def _normalize_title_for_dedup(title):
    """Normalize title for duplicate detection."""
    clean = title.lower().strip()
    # Remove common prefixes
    for prefix in ["saudi arabia:", "uae:", "dubai:", "abu dhabi:", "riyadh:"]:
        if clean.startswith(prefix):
            clean = clean[len(prefix):].strip()
    # Remove source suffixes
    for src in ["zawya", "meed", "construction week", "trade arabia", "arab news", 
                "khaleej times", "gulf news", "arabianbusiness", "argaam", "emirates 24|7"]:
        clean = clean.replace(f"| {src}", "").replace(f"- {src}", "")
        clean = clean.replace(f" - {src}.com", "").replace(f" | {src}.com", "")
    # Keep only alphanumeric and spaces
    return "".join(c for c in clean if c.isalnum() or c.isspace()).strip()


def _is_duplicate_title(new_title, existing_titles, threshold=0.80):
    """Check if new_title is similar to any existing titles using word overlap."""
    new_norm = _normalize_title_for_dedup(new_title)
    new_words = set(new_norm.split())
    if not new_words:
        return False
    
    for existing_title in existing_titles:
        existing_norm = _normalize_title_for_dedup(existing_title)
        existing_words = set(existing_norm.split())
        if not existing_words:
            continue
        
        common_words = new_words & existing_words
        similarity = len(common_words) / max(len(new_words), len(existing_words))
        if similarity >= threshold:
            return True
    return False


def _is_today(date_str):
    """Check if date_str is today's date."""
    if not date_str:
        return False
    try:
        article_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.now().date()
        return article_date == today
    except (ValueError, AttributeError):
        return False


def enqueue_articles(articles):
    """Add unseen articles to the send queue with improved duplicate detection. 
    ONLY TODAY'S NEWS is queued. Returns how many were added."""
    sent = load_sent_ids()
    with _lock:
        queue = _read_json(QUEUE_FILE, {"last_sent_at": None, "items": []})
        queued_ids = {item.get("id") for item in queue.get("items", [])}
        queued_titles = [item.get("title", "") for item in queue.get("items", [])]
        
        # Also check against recent log entries to avoid re-queueing recently sent items
        log = _read_json(LOG_FILE, [])
        recent_titles = [entry.get("title", "") for entry in log[:50]]  # Check last 50 sent
        
        added = []
        for article in articles:
            # STRICT: Only queue TODAY's news
            article_date = article.get("date", "")
            if not _is_today(article_date):
                continue
            
            cat = article.get("category") or article.get("cat")
            if cat and cat not in AWARD_CATEGORIES:
                continue

            aid = article.get("id") or article_id(article["title"], article["link"])
            
            # Skip if already sent or queued by ID
            if aid in sent or aid in queued_ids:
                continue
            
            # Skip if title is too similar to something already queued or recently sent
            if _is_duplicate_title(article["title"], queued_titles + recent_titles):
                continue
            
            item = {
                "id": aid,
                "title": article["title"],
                "link": article["link"],
                "source": article.get("source", ""),
                "country": article.get("country", ""),
                "category": cat or "",
                "date": article.get("date", ""),
                "queued_at": now_iso(),
            }
            queue.setdefault("items", []).append(item)
            queued_ids.add(aid)
            queued_titles.append(article["title"])
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


def load_settings():
    with _lock:
        data = _read_json(SETTINGS_FILE, DEFAULT_SETTINGS.copy())
    return {**DEFAULT_SETTINGS, **data}


def save_settings(patch):
    with _lock:
        data = {**DEFAULT_SETTINGS, **_read_json(SETTINGS_FILE, {}), **patch}
        _write_json(SETTINGS_FILE, data)
    return data


def log_discovered(articles):
    """Append newly-discovered articles to the persistent discovery history,
    independent of whether they ever get sent to WhatsApp."""
    with _lock:
        log = _read_json(NEWS_LOG_FILE, [])
        now = now_iso()
        for article in articles:
            log.insert(0, {
                "id": article.get("id") or article_id(article.get("title", ""), article.get("link", "")),
                "title": article.get("title", ""),
                "link": article.get("link", ""),
                "source": article.get("source", ""),
                "country": article.get("country", ""),
                "category": article.get("category", ""),
                "date": article.get("date", ""),
                "discovered_at": article.get("discovered_at") or now,
            })
        _write_json(NEWS_LOG_FILE, log[:NEWS_LOG_LIMIT])


def load_news_log(limit=200):
    with _lock:
        log = _read_json(NEWS_LOG_FILE, [])
    return log[:limit]


def save_extracted(articles):
    with _lock:
        _write_json(EXTRACTED_FILE, {
            "day": today_str(),
            "fetched_at": now_iso(),
            "articles": articles,
        })


def load_extracted():
    with _lock:
        return _read_json(EXTRACTED_FILE, {"day": "", "fetched_at": "", "articles": []})


def save_llm_picks(evaluations):
    kept = sum(1 for item in evaluations if item.get("keep"))
    with _lock:
        _write_json(LLM_PICKS_FILE, {
            "day": today_str(),
            "evaluated_at": now_iso(),
            "kept": kept,
            "dropped": len(evaluations) - kept,
            "items": evaluations,
        })


def load_llm_picks():
    with _lock:
        return _read_json(LLM_PICKS_FILE, {
            "day": "",
            "evaluated_at": "",
            "kept": 0,
            "dropped": 0,
            "items": [],
        })


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
