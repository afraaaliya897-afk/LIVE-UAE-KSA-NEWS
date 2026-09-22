# store.py
# Persistence for live news (1 day), the discovery log, the send queue, the
# send log, and the auto-send toggle.

import hashlib
import json
import os
import threading
from datetime import datetime, timedelta, timezone

from llm_judge import AWARD_CATEGORIES

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_FILE = os.path.join(BASE_DIR, "live_news.json")
QUEUE_FILE = os.path.join(BASE_DIR, "send_queue.json")
LOG_FILE = os.path.join(BASE_DIR, "whatsapp_log.json")
SENT_FILE = os.path.join(BASE_DIR, "sent_whatsapp.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
NEWS_LOG_FILE = os.path.join(BASE_DIR, "news_log.json")
EXTRACTED_FILE = os.path.join(BASE_DIR, "extracted_news.json")
LLM_PICKS_FILE = os.path.join(BASE_DIR, "llm_picks.json")

DEFAULT_SETTINGS = {"auto_send_enabled": False}

# UAE/KSA business-day cutoff (Asia/Dubai, UTC+4, no DST). Monitoring now
# runs continuously (no more fixed 8AM-5PM window), so there's no longer a
# scheduling gap for a "yesterday after 5PM" story to fall into - every
# story gets caught within one ~10-minute poll cycle of being discovered.
# "Today's news" is therefore just that, again: published today - but
# checked properly against the article's real timestamp converted to this
# timezone, instead of the old naive string-equality-on-date comparison.
BUSINESS_TZ = timezone(timedelta(hours=4))

_lock = threading.Lock()


def article_id(title, link):
    return hashlib.md5(f"{title}{link}".encode()).hexdigest()[:16]


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def today_str():
    """"Today" in the business timezone, not the host machine's local time -
    load-bearing once this runs somewhere other than a UAE-based machine
    (e.g. a UTC-default AWS instance), since live_news.json's day-bookkeeping
    needs to agree with is_fresh()'s notion of "today" or Live can spuriously
    reset around the host's own midnight instead of the business one."""
    return datetime.now(BUSINESS_TZ).strftime("%Y-%m-%d")


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
    """Set today's live feed to this LLM-approved list.

    A story kept earlier today is removed if this run no longer approves it.
    Same story from another publisher is not added again.
    Returns (live, newly_added).
    """
    day = today_str()
    with _lock:
        data = _read_json(LIVE_FILE, {"day": "", "articles": []})
        previous = data.get("articles", []) if data.get("day") == day else []
        previous_titles = [a.get("title", "") for a in previous]
        now = now_iso()
        accepted = []
        newly_added = []
        for article in new_articles:
            article = {**article, "llm_approved": True}
            title = article.get("title", "")
            if _is_duplicate_title(title, [a.get("title", "") for a in accepted]):
                continue
            already_known = _is_duplicate_title(title, previous_titles) or any(
                a.get("id") == article.get("id") for a in previous
            )
            if not already_known:
                article = {**article, "discovered_at": now}
                newly_added.append(article)
            else:
                prior = next(
                    (
                        a for a in previous
                        if a.get("id") == article.get("id")
                        or titles_are_same_story(title, a.get("title", ""))
                    ),
                    None,
                )
                if prior and prior.get("discovered_at"):
                    article = {**article, "discovered_at": prior["discovered_at"]}
            accepted.append(article)
        data = {"day": day, "fetched_at": now, "articles": accepted}
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


_TITLE_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "for", "on", "at", "by",
    "with", "from", "as", "is", "are", "was", "be", "its", "it", "after",
    "before", "new", "says", "said", "over", "into", "than", "that", "this",
    "all", "has", "have", "will", "set", "out", "up", "per",
}
_PUBLISHER_TOKENS = {
    "zawya", "meed", "gulf", "news", "khaleej", "times", "arabian", "business",
    "arab", "national", "reuters", "bloomberg", "constructionweek", "tradearabia",
    "argaam", "emirates", "okaz", "wam", "spa", "com",
}


def _story_tokens(title):
    """Content words used to tell whether two headlines are the same story."""
    clean = (title or "").lower()
    clean = clean.split(" - ")[0]
    clean = "".join(ch if ch.isalnum() else " " for ch in clean)
    tokens = []
    for word in clean.split():
        if len(word) <= 2 or word in _TITLE_STOPWORDS or word in _PUBLISHER_TOKENS:
            continue
        if word.isdigit():
            continue
        tokens.append(word)
    return set(tokens)


def titles_are_same_story(left, right):
    """True when two headlines are the same story, including different publishers.

    Uses both overall overlap and how much of the shorter headline is contained
    in the longer one, so a rewrite still matches.
    """
    left_tokens = _story_tokens(left)
    right_tokens = _story_tokens(right)
    if len(left_tokens) < 3 or len(right_tokens) < 3:
        return False
    shared = left_tokens & right_tokens
    if len(shared) < 3:
        return False
    jaccard = len(shared) / len(left_tokens | right_tokens)
    contained = len(shared) / min(len(left_tokens), len(right_tokens))
    return jaccard >= 0.5 or (len(shared) >= 4 and contained >= 0.55)


def _is_duplicate_title(new_title, existing_titles):
    return any(titles_are_same_story(new_title, existing) for existing in existing_titles)


def is_fresh(published_at):
    """True when an article was published on today's calendar date, in the
    business timezone (BUSINESS_TZ). A missing/unparseable timestamp is
    treated as not fresh - fail closed, same as the rest of the pipeline's
    safety checks."""
    if not published_at:
        return False
    try:
        published = datetime.fromisoformat(published_at)
    except (ValueError, TypeError):
        return False
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    published_date = published.astimezone(BUSINESS_TZ).date()
    today_date = datetime.now(BUSINESS_TZ).date()
    return published_date == today_date


def enqueue_articles(articles):
    """Add unseen articles to the send queue with improved duplicate detection.
    ONLY FRESH NEWS (see is_fresh) is queued. Returns how many were added."""
    sent = load_sent_ids()
    with _lock:
        queue = _read_json(QUEUE_FILE, {"last_sent_at": None, "items": []})
        queued_ids = {item.get("id") for item in queue.get("items", [])}
        queued_titles = [item.get("title", "") for item in queue.get("items", [])]
        
        # Also check against recent log entries to avoid re-queueing recently sent items
        log = _read_json(LOG_FILE, [])
        recent_titles = [entry.get("title", "") for entry in log]
        news_log = _read_json(NEWS_LOG_FILE, [])
        recent_titles.extend(entry.get("title", "") for entry in news_log)
        
        added = []
        for article in articles:
            # STRICT: Only queue news still inside the fresh window
            if not is_fresh(article.get("published_at", "")):
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
                "published_at": article.get("published_at", ""),
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
        _write_json(LOG_FILE, log)
    return entry


def mark_sent(item, group_id=""):
    entry = _log_entry(item, "sent", group_id=group_id)
    with _lock:
        log = _read_json(LOG_FILE, [])
        log.insert(0, entry)
        _write_json(LOG_FILE, log)
        sent = set(_read_json(SENT_FILE, []))
        sent.add(entry["id"])
        _save_sent_ids_unlocked(sent)
        queue = _read_json(QUEUE_FILE, {"last_sent_at": None, "items": []})
        queue["last_sent_at"] = entry["sent_at"]
        queue["items"] = [
            q for q in queue.get("items", [])
            if q.get("id") != entry["id"]
            and not titles_are_same_story(entry.get("title", ""), q.get("title", ""))
        ]
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
        known_titles = [entry.get("title", "") for entry in log]
        known_ids = {entry.get("id") for entry in log}
        now = now_iso()
        for article in articles:
            aid = article.get("id") or article_id(article.get("title", ""), article.get("link", ""))
            title = article.get("title", "")
            if aid in known_ids or _is_duplicate_title(title, known_titles):
                continue
            known_ids.add(aid)
            known_titles.append(title)
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
        _write_json(NEWS_LOG_FILE, log)


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
