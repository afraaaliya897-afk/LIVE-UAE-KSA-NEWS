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
    fetch_and_dedup_sync,
    finalize_articles,
    get_cache_key,
    load_from_cache,
    save_to_cache,
)
from store import (
    article_id,
    enqueue_articles,
    format_whatsapp_message,
    load_live,
    load_log,
    load_news_log,
    load_queue,
    load_sent_ids,
    load_settings,
    log_discovered,
    mark_failed,
    mark_sent,
    merge_into_live,
    save_settings,
)

app = Flask(__name__)

WHATSAPP_CONFIG_FILE = "selfhosted_config.json"
POLL_INTERVAL_SECONDS = 10 * 60  # how often the poller checks for fresh news
SEND_PACE_SECONDS = (15, 20)  # gap between consecutive auto-sends when several land at once


def collect_articles(keyword, date_from, date_to, category_filter=None):
    cache_key = get_cache_key(keyword or "", date_from or "", date_to or "", category_filter or "both")
    cached_results = load_from_cache(cache_key)
    if cached_results is not None:
        for item in cached_results:
            if "id" not in item:
                item["id"] = article_id(item["title"], item["link"])
        return cached_results, True

    deduped = fetch_and_dedup_sync(keyword or None, date_from, date_to, category_filter)
    results = finalize_articles(deduped, date_from, date_to)

    save_to_cache(cache_key, results)
    return results, False


def fresh_date_range():
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def run_poll_cycle():
    """Fetch, dedupe, and merge today's news; log and queue anything new."""
    date_from, date_to = fresh_date_range()
    deduped = fetch_and_dedup_sync(None, date_from, date_to)
    articles = finalize_articles(deduped, date_from, date_to)
    live, newly_added = merge_into_live(articles)
    if newly_added:
        log_discovered(newly_added)
        enqueue_articles(newly_added)
    return live, newly_added


def refresh_live_feed(force=False):
    if not force:
        return load_live(), 0
    live, newly_added = run_poll_cycle()
    return live, len(newly_added)


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

        sent_ids = load_sent_ids()
        if aid in sent_ids:
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
            run_poll_cycle()
        except Exception as exc:
            print(f"Poller: {exc}")
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
        status = "sent" if aid in sent_ids else "waiting"
        articles.append({**article, "id": aid, "status": status})

    return jsonify({
        "success": True,
        "day": live.get("day"),
        "fetched_at": live.get("fetched_at"),
        "articles": articles,
        "queued_new": queued_new,
        "queue": queue_payload(),
        "log": load_log(200),
    })


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
    return jsonify({"success": True, "log": load_news_log(200)})


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


@app.route("/api/whatsapp/groups")
def api_whatsapp_groups():
    bot_api_url = get_bot_api_url()
    try:
        response = requests.get(f"{bot_api_url}/groups", timeout=15)
        response.raise_for_status()
        return jsonify({"success": True, "groups": response.json()})
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
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        start_background_loops()
    app.run(debug=True, port=5050, threaded=True)
