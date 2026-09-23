# app.py
#
# Monitoring UI + Live tab. Manual search stays on Search.
# A background poller watches for fresh news continuously; a background
# sender posts anything new to WhatsApp the moment it's confirmed unique,
# as long as the auto-send toggle is on. Manual "Post this" always works
# as an override, toggle or no toggle.
#
# Run with:  python app.py
# Then open: http://127.0.0.1:5050

from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta
import json
import os
import random
import threading
import time
import requests

from pipeline import (
    fetch_candidates_sync,
    finalize_articles,
    get_cache_key,
    load_from_cache,
    save_to_cache,
)
from llm_judge import AWARD_CATEGORIES, judge_award_articles
from store import (
    BUSINESS_TZ,
    article_id,
    enqueue_articles,
    format_whatsapp_message,
    is_fresh,
    load_extracted,
    load_live,
    load_llm_picks,
    load_log,
    load_news_log,
    load_queue,
    load_sent_ids,
    load_settings,
    log_discovered,
    mark_failed,
    mark_sent,
    merge_into_live,
    save_extracted,
    save_llm_picks,
    save_settings,
    titles_are_same_story,
)

app = Flask(__name__)

WHATSAPP_CONFIG_FILE = "selfhosted_config.json"
POLL_INTERVAL_SECONDS = 5 * 60  # how often the poller checks for fresh news
SEND_PACE_SECONDS = (15, 20)  # gap between consecutive auto-sends when several land at once

_poll_lock = threading.Lock()
_poll_running = False


def collect_articles(keyword, date_from, date_to, category_filter=None):
    """Tab 1 (Search): the raw pool. Date + keyword + source matching only -
    no LLM relevance filtering. That gate lives in run_poll_cycle() instead,
    downstream of Extracted/LLM Picked."""
    cache_key = get_cache_key(keyword or "", date_from or "", date_to or "", category_filter or "both")
    cached_results = load_from_cache(cache_key)
    if cached_results is not None:
        for item in cached_results:
            if "id" not in item:
                item["id"] = article_id(item["title"], item["link"])
        return cached_results, True

    raw = fetch_candidates_sync(keyword or None, date_from, date_to)
    results = finalize_articles(raw, date_from, date_to)
    if category_filter:
        results = [item for item in results if item.get("category") == category_filter]

    save_to_cache(cache_key, results)
    return results, False


def fresh_date_range():
    """Search window for the continuous poller. Wider than 'today' so the
    Google News query still surfaces a story even with the RSS feed's own
    6-24h indexing lag; is_fresh() (calendar-day, business-timezone-based)
    is what actually decides whether a matched article is new enough to
    act on."""
    today = datetime.now(BUSINESS_TZ)
    yesterday = today - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def run_poll_cycle():
    """Fetch candidates, let the LLM pick awards, then merge into Live.

    The search itself looks back further than today (fresh_date_range) to
    absorb Google News RSS's own indexing lag, but Extracted/LLM Picked/Live
    only ever show TODAY's news - yesterday's matches aren't lost, they're
    already in News Log from whichever earlier cycle first caught them as
    "today"."""
    date_from, date_to = fresh_date_range()
    raw = fetch_candidates_sync(None, date_from, date_to)
    finalized = finalize_articles(raw, date_from, date_to)
    extracted = [item for item in finalized if is_fresh(item.get("published_at", ""))]
    save_extracted(extracted)

    to_judge = [{**item, "cat": item.get("category")} for item in extracted]
    kept, evaluations = judge_award_articles(to_judge)
    save_llm_picks(evaluations)
    judge_failed = bool(evaluations) and all(
        str(item.get("reason", "")).startswith("LLM error") for item in evaluations
    )
    if judge_failed:
        return load_live(), []

    kept_by_id = {item.get("id"): item for item in kept}
    articles = []
    for item in extracted:
        chosen = kept_by_id.get(item["id"])
        if not chosen:
            continue
        category = chosen.get("cat") or chosen.get("category")
        if category not in AWARD_CATEGORIES:
            continue
        articles.append({**item, "category": category, "llm_approved": True})

    live, newly_added = merge_into_live(articles)
    if newly_added:
        log_discovered(newly_added)
        enqueue_articles(newly_added)
    return live, newly_added


def kick_poll():
    """Start an RSS + LLM cycle in the background. Never blocks the UI."""
    global _poll_running
    with _poll_lock:
        if _poll_running:
            return False
        _poll_running = True

    def _run():
        global _poll_running
        try:
            run_poll_cycle()
        except Exception as exc:
            print(f"Poller: {exc}", flush=True)
        finally:
            with _poll_lock:
                _poll_running = False

    threading.Thread(target=_run, name="news-poll-once", daemon=True).start()
    return True


def poll_is_running():
    with _poll_lock:
        return _poll_running


def refresh_live_feed(force=False):
    if force:
        kick_poll()
    return load_live(), 0


def load_whatsapp_config():
    with open(WHATSAPP_CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    return config["groupId"], config.get("botApiUrl", "http://localhost:3000")


def get_bot_api_url():
    """Like load_whatsapp_config but doesn't require groupId - used for the
    connect/QR flow, which runs before a group has been chosen yet."""
    try:
        with open(WHATSAPP_CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("botApiUrl", "http://localhost:3000")
    except Exception:
        return "http://localhost:3000"


def bot_ready(bot_api_url):
    response = requests.get(f"{bot_api_url}/status", timeout=5)
    return bool(response.json().get("ready"))


def bot_is_ready():
    try:
        _group_id, bot_api_url = load_whatsapp_config()
        return bot_ready(bot_api_url)
    except Exception:
        return False


def send_article_to_group(article):
    group_id = ""
    aid = article.get("id") or article_id(article.get("title", ""), article.get("link", ""))
    article = {**article, "id": aid}
    try:
        group_id, bot_api_url = load_whatsapp_config()
        if not bot_ready(bot_api_url):
            raise RuntimeError("WhatsApp bot is not ready. Start node whatsapp_selfhosted.js")

        # STRICT: Only send news still inside the fresh window
        if not is_fresh(article.get("published_at", "")):
            print(f"Skipping stale news (published_at: {article.get('published_at', '')}): {article.get('title', '')[:50]}")
            return None, "not_fresh"

        if not article.get("llm_approved"):
            judged, _evals = judge_award_articles([{
                "title": article.get("title", ""),
                "source": article.get("source", ""),
                "link": article.get("link", ""),
                "cat": article.get("category") or article.get("cat"),
            }])
            if not judged:
                print(f"LLM rejected before send: {article.get('title', '')[:50]}", flush=True)
                return None, "not_award"
            article = {**article, "category": judged[0]["cat"], "llm_approved": True}

        if article.get("category") not in AWARD_CATEGORIES:
            print(f"Skipping non-award news: {article.get('title', '')[:50]}")
            return None, "not_award"

        sent_ids = load_sent_ids()
        if aid in sent_ids:
            return None, "already_sent"
        sent_titles = [
            row.get("title", "")
            for row in load_log(limit=None)
            if row.get("status") == "sent"
        ]
        if any(titles_are_same_story(article.get("title", ""), sent_title) for sent_title in sent_titles):
            return None, "already_sent"

        message = format_whatsapp_message(article)
        response = requests.post(
            f"{bot_api_url}/send",
            json={"groupId": group_id, "message": message},
            timeout=20,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Failed to send message: {response.text}")

        entry = mark_sent(article, group_id=group_id)
        return entry, "sent"
    except Exception as exc:
        mark_failed(article, group_id=group_id, error=str(exc))
        raise


def sender_tick():
    """Attempt one send from the queue if auto-send is on and possible.
    Returns the resulting status ('sent', 'already_sent', 'failed'), or
    None if nothing was attempted this tick."""
    if not load_settings().get("auto_send_enabled"):
        return None
    queue = load_queue()
    items = queue.get("items", [])
    if not items:
        return None
    if not bot_is_ready():
        return None
    try:
        _entry, status = send_article_to_group(items[0])
        return status
    except Exception as exc:
        print(f"Sender: {exc}")
        return "failed"


def sender_loop():
    time.sleep(8)
    while True:
        status = None
        try:
            status = sender_tick()
        except Exception as exc:
            print(f"Sender: {exc}")
        time.sleep(random.uniform(*SEND_PACE_SECONDS) if status else 5)


def poller_loop():
    time.sleep(8)
    while True:
        try:
            kick_poll()
        except Exception as exc:
            print(f"Poller: {exc}", flush=True)
        time.sleep(POLL_INTERVAL_SECONDS)


def start_background_loops():
    threading.Thread(target=poller_loop, name="news-poller", daemon=True).start()
    threading.Thread(target=sender_loop, name="whatsapp-sender", daemon=True).start()


@app.route("/", methods=["GET", "POST"])
def index():
    results = None
    form_values = {"from_date": "", "to_date": "", "keyword": "", "category": "both"}
    from_cache = False

    if request.method == "POST":
        from_date = request.form.get("from_date", "").strip()
        to_date = request.form.get("to_date", "").strip()
        keyword = request.form.get("keyword", "").strip()
        category = request.form.get("category", "both")

        form_values = {
            "from_date": from_date,
            "to_date": to_date,
            "keyword": keyword,
            "category": category,
        }

        if not from_date or not to_date:
            date_from, date_to = fresh_date_range()
        else:
            date_from, date_to = from_date, to_date

        category_filter = None
        if category == "contract":
            category_filter = "Contract Awarded"
        elif category == "project":
            category_filter = "Project Awarded"
        elif category == "general":
            category_filter = "General Construction"

        results, from_cache = collect_articles(keyword, date_from, date_to, category_filter)

    return render_template(
        "index.html",
        results=results,
        form_values=form_values,
        from_cache=from_cache,
    )


def queue_payload():
    queue = load_queue()
    return {
        "items": queue.get("items", []),
        "count": len(queue.get("items", [])),
        "last_sent_at": queue.get("last_sent_at"),
        "auto_send_enabled": load_settings().get("auto_send_enabled", False),
    }


@app.route("/api/live")
def api_live():
    force = request.args.get("refresh") == "1"
    try:
        live, queued_new = refresh_live_feed(force=force)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

    sent_ids = load_sent_ids()
    articles = []
    for article in live.get("articles", []):
        aid = article.get("id") or article_id(article["title"], article["link"])
        if not article.get("llm_approved"):
            continue
        if article.get("category") not in AWARD_CATEGORIES:
            continue
        if not is_fresh(article.get("published_at", "")):
            continue
        status = "sent" if aid in sent_ids else "waiting"
        articles.append({**article, "id": aid, "status": status})

    return jsonify({
        "success": True,
        "day": live.get("day"),
        "fetched_at": live.get("fetched_at"),
        "articles": articles,
        "queued_new": queued_new,
        "polling": poll_is_running(),
        "queue": queue_payload(),
        "log": load_log(200),
    })


@app.route("/api/extracted")
def api_extracted():
    data = load_extracted()
    return jsonify({"success": True, **data})


@app.route("/api/llm-picks")
def api_llm_picks():
    data = load_llm_picks()
    return jsonify({"success": True, **data})


@app.route("/api/log")
def api_log():
    try:
        group_id, _ = load_whatsapp_config()
    except Exception:
        group_id = ""
    log = load_log(200)
    for row in log:
        if not row.get("group_id"):
            row["group_id"] = group_id
        if not row.get("status"):
            row["status"] = "sent"
    return jsonify({"success": True, "log": log, "queue": queue_payload()})


@app.route("/api/news-log")
def api_news_log():
    try:
        return jsonify({"success": True, "log": load_news_log(200)})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc), "log": []}), 500


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify({"success": True, "settings": load_settings()})


@app.route("/api/settings", methods=["POST"])
def api_set_settings():
    data = request.get_json(silent=True) or {}
    if "auto_send_enabled" not in data:
        return jsonify({"success": False, "error": "auto_send_enabled is required"}), 400
    settings = save_settings({"auto_send_enabled": bool(data["auto_send_enabled"])})
    return jsonify({"success": True, "settings": settings})


@app.route("/api/whatsapp/status")
def api_whatsapp_status():
    bot_api_url = get_bot_api_url()
    try:
        response = requests.get(f"{bot_api_url}/status", timeout=5)
        return jsonify({"success": True, "reachable": True, "ready": bool(response.json().get("ready"))})
    except Exception:
        return jsonify({"success": True, "reachable": False, "ready": False})


@app.route("/api/whatsapp/qr")
def api_whatsapp_qr():
    bot_api_url = get_bot_api_url()
    try:
        response = requests.get(f"{bot_api_url}/qr", timeout=5)
        data = response.json()
        return jsonify({"success": True, "ready": bool(data.get("ready")), "qr": data.get("qr")})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@app.route("/api/whatsapp/disconnect", methods=["POST"])
def api_whatsapp_disconnect():
    bot_api_url = get_bot_api_url()
    try:
        response = requests.post(f"{bot_api_url}/disconnect", timeout=20)
        response.raise_for_status()
        return jsonify({"success": True, **response.json()})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@app.route("/api/whatsapp/groups")
def api_whatsapp_groups():
    bot_api_url = get_bot_api_url()
    params = {"refresh": "1"} if request.args.get("refresh") == "1" else {}
    try:
        current_group_id, _ = load_whatsapp_config()
    except Exception:
        current_group_id = ""
    try:
        response = requests.get(f"{bot_api_url}/groups", params=params, timeout=15)
        response.raise_for_status()
        return jsonify({"success": True, "groups": response.json(), "current_group_id": current_group_id})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@app.route("/api/whatsapp/config", methods=["POST"])
def api_whatsapp_config():
    data = request.get_json(silent=True) or {}
    group_id = (data.get("groupId") or "").strip()
    if not group_id:
        return jsonify({"success": False, "error": "groupId is required"}), 400
    config = {"groupId": group_id, "botApiUrl": get_bot_api_url()}
    with open(WHATSAPP_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    return jsonify({"success": True, "config": config})


@app.route("/api/queue-articles", methods=["POST"])
def api_queue_articles():
    data = request.get_json(silent=True) or {}
    articles = data.get("articles", [])
    if not articles:
        return jsonify({"success": False, "error": "No articles provided"}), 400
    added = enqueue_articles(articles)
    return jsonify({
        "success": True,
        "added": len(added),
        "queue": queue_payload(),
        "message": f"Queued {len(added)} article(s). They post automatically when auto-send is on.",
    })


@app.route("/api/send-next", methods=["POST"])
def api_send_next():
    data = request.get_json(silent=True) or {}
    article_id_req = data.get("id")

    queue = load_queue()
    item = None
    if article_id_req:
        for queued in queue.get("items", []):
            if queued.get("id") == article_id_req:
                item = queued
                break
        if item is None:
            live = load_live()
            for article in live.get("articles", []):
                aid = article.get("id") or article_id(article["title"], article["link"])
                if aid == article_id_req:
                    item = {**article, "id": aid}
                    break
    elif queue.get("items"):
        item = queue["items"][0]

    if item is None:
        return jsonify({"success": False, "error": "Nothing waiting to post.", "queue": queue_payload()}), 400

    try:
        entry, status = send_article_to_group(item)
    except FileNotFoundError:
        return jsonify({"success": False, "error": "WhatsApp config is missing."}), 500
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc), "queue": queue_payload()}), 500

    if status == "already_sent":
        return jsonify({"success": True, "skipped": True, "message": "Already sent.", "queue": queue_payload(), "log": load_log(200)})

    return jsonify({
        "success": True,
        "sent": entry,
        "message": "Posted 1 article to the group.",
        "queue": queue_payload(),
        "log": load_log(200),
    })


@app.route("/send-to-whatsapp", methods=["POST"])
def send_to_whatsapp():
    """Queue selected search results. They post automatically if auto-send is on."""
    try:
        data = request.get_json() or {}
        articles = data.get("articles", [])
        if not articles:
            return jsonify({"success": False, "error": "No articles provided"}), 400

        added = enqueue_articles(articles)
        sent_now = False
        try:
            sent_now = sender_tick() == "sent"
        except Exception:
            sent_now = False

        extra = " Posted the first one now." if sent_now else ""
        return jsonify({
            "success": True,
            "added": len(added),
            "sent": 1 if sent_now else 0,
            "queue": queue_payload(),
            "message": f"Queued {len(added)} article(s).{extra}",
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


if __name__ == "__main__":
    # Start background loops once
    start_background_loops()
    # Run with debug=False to prevent auto-reload creating multiple processes
    # Use use_reloader=False to ensure only one process
    app.run(debug=False, port=5050, threaded=True, use_reloader=False)
