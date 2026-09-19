# app.py
#
# Monitoring UI + Live tab. Manual search stays on Search.
# Live keeps today's news for one day and posts one WhatsApp message per hour.
#
# Run with:  python app.py
# Then open: http://127.0.0.1:5050

from flask import Flask, render_template, request, jsonify
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta
import asyncio
import json
import os
import threading
import time
import requests

from pipeline import (
    fetch_all_feeds_parallel,
    classify_category,
    classify_country,
    get_cache_key,
    load_from_cache,
    save_to_cache,
)
from sources import TRUSTED_PUBLISHERS
from store import (
    article_id,
    enqueue_articles,
    format_whatsapp_message,
    load_live,
    load_log,
    load_queue,
    load_sent_ids,
    mark_failed,
    mark_sent,
    save_live,
    seconds_until_next_send,
)

app = Flask(__name__)

WHATSAPP_CONFIG_FILE = "selfhosted_config.json"
SEND_INTERVAL_SECONDS = 60 * 60


def collect_articles(keyword, date_from, date_to, category_filter=None):
    cache_key = get_cache_key(keyword or "", date_from or "", date_to or "", category_filter or "both")
    cached_results = load_from_cache(cache_key)
    if cached_results is not None:
        for item in cached_results:
            if "id" not in item:
                item["id"] = article_id(item["title"], item["link"])
        return cached_results, True

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    feed_results = loop.run_until_complete(
        fetch_all_feeds_parallel(keyword or None, date_from, date_to)
    )
    loop.close()

    seen_articles = {}
    for publisher, _country, articles in feed_results:
        for a in articles:
            cat = classify_category(a.title)
            country_tag = classify_country(a.title)

            if cat is None or country_tag is None:
                continue
            if category_filter and cat != category_filter:
                continue

            title_clean = a.title.lower().strip()
            for prefix in ["saudi arabia:", "uae:", "dubai:", "abu dhabi:", "riyadh:"]:
                if title_clean.startswith(prefix):
                    title_clean = title_clean[len(prefix):].strip()
            for src in ["zawya", "meed", "construction week", "trade arabia", "arab news", "khaleej times"]:
                title_clean = title_clean.replace(f"| {src}", "").replace(f"- {src}", "")
            title_normalized = "".join(c for c in title_clean if c.isalnum() or c.isspace())[:60].strip()

            is_duplicate = False
            best_match_key = None
            for seen_key in list(seen_articles.keys()):
                seen_words = set(seen_key.split())
                new_words = set(title_normalized.split())
                if not seen_words or not new_words:
                    continue
                common_words = seen_words & new_words
                similarity = len(common_words) / max(len(seen_words), len(new_words))
                if similarity >= 0.85:
                    is_duplicate = True
                    best_match_key = seen_key
                    break

            if is_duplicate:
                existing = seen_articles[best_match_key]
                try:
                    if TRUSTED_PUBLISHERS.index(publisher) < TRUSTED_PUBLISHERS.index(existing["source"]):
                        del seen_articles[best_match_key]
                        seen_articles[title_normalized] = {
                            "title": a.title,
                            "link": a.link,
                            "source": publisher,
                            "cat": cat,
                            "country": country_tag,
                            "published": a.get("published", ""),
                        }
                except ValueError:
                    pass
                continue

            seen_articles[title_normalized] = {
                "title": a.title,
                "link": a.link,
                "source": publisher,
                "cat": cat,
                "country": country_tag,
                "published": a.get("published", ""),
            }

    results = []
    for article_data in seen_articles.values():
        published_raw = article_data["published"]
        try:
            article_date = parsedate_to_datetime(published_raw)
            date_str = article_date.strftime("%Y-%m-%d")
            if date_from and date_to:
                search_start = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=article_date.tzinfo)
                search_end = datetime.strptime(date_to, "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59, tzinfo=article_date.tzinfo
                )
                if article_date < search_start or article_date > search_end:
                    continue
        except Exception:
            date_str = ""
            if date_from and date_to:
                continue

        results.append({
            "id": article_id(article_data["title"], article_data["link"]),
            "title": article_data["title"],
            "link": article_data["link"],
            "category": article_data["cat"],
            "country": article_data["country"],
            "source": article_data["source"],
            "date": date_str,
        })

    save_to_cache(cache_key, results)
    return results, False


def fresh_date_range():
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def refresh_live_feed(force=False):
    live = load_live()
    if not force and not live.get("stale"):
        return live, 0
    date_from, date_to = fresh_date_range()
    articles, _from_cache = collect_articles(None, date_from, date_to)
    live = save_live(articles)
    added = enqueue_articles(articles)
    return live, len(added)


def load_whatsapp_config():
    with open(WHATSAPP_CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    return config["groupId"], config.get("botApiUrl", "http://localhost:3000")


def bot_ready(bot_api_url):
    response = requests.get(f"{bot_api_url}/status", timeout=5)
    return bool(response.json().get("ready"))


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


def try_auto_send():
    queue = load_queue()
    remaining = seconds_until_next_send(queue)
    if remaining > 0 or not queue.get("items"):
        return None
    try:
        _group_id, bot_api_url = load_whatsapp_config()
        if not bot_ready(bot_api_url):
            print("Hourly sender: WhatsApp bot is not ready")
            return None
    except Exception as exc:
        print(f"Hourly sender: {exc}")
        return None
    item = queue["items"][0]
    entry, status = send_article_to_group(item)
    return entry if status == "sent" else None


def hourly_sender_loop():
    time.sleep(8)
    while True:
        try:
            try_auto_send()
        except Exception as exc:
            print(f"Hourly sender: {exc}")
        time.sleep(30)


def start_hourly_sender():
    thread = threading.Thread(target=hourly_sender_loop, name="whatsapp-hourly", daemon=True)
    thread.start()


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
        "seconds_until_next": seconds_until_next_send(queue),
        "interval_minutes": SEND_INTERVAL_SECONDS // 60,
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
        "message": f"Queued {len(added)} article(s). One post goes out every hour.",
    })


@app.route("/api/send-next", methods=["POST"])
def api_send_next():
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force"))
    article_id_req = data.get("id")

    queue = load_queue()
    remaining = seconds_until_next_send(queue)
    if remaining > 0 and not force:
        return jsonify({
            "success": False,
            "error": f"Next post is in {remaining // 60}m {remaining % 60}s. Wait so the group is not flooded.",
            "queue": queue_payload(),
        }), 429

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
    """Queue selected search results. One item posts per hour."""
    try:
        data = request.get_json() or {}
        articles = data.get("articles", [])
        if not articles:
            return jsonify({"success": False, "error": "No articles provided"}), 400

        added = enqueue_articles(articles)
        sent_now = None
        try:
            sent_now = try_auto_send()
        except Exception:
            sent_now = None

        extra = " Posted the first one now." if sent_now else ""
        return jsonify({
            "success": True,
            "added": len(added),
            "sent": 1 if sent_now else 0,
            "queue": queue_payload(),
            "message": f"Queued {len(added)} article(s). One post per hour.{extra}",
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


if __name__ == "__main__":
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        start_hourly_sender()
    app.run(debug=True, port=5050)
