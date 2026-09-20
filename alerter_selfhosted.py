# alerter_selfhosted.py
# -*- coding: utf-8 -*-
#
# Self-hosted WhatsApp alerter - works with whatsapp_selfhosted.js
# No third-party service, everything runs on your computer.
#
# `python app.py` is now the continuous auto-poster (poll + auto-send with
# a toggle) - see app.py. This script is a manual one-shot fallback only;
# --watch is deprecated because running it alongside app.py would double-post.
#
# Commands:
#   python alerter_selfhosted.py                 one check, send new items
#   python alerter_selfhosted.py --dry-run       print messages, do not send
#   python alerter_selfhosted.py --list-groups   show WhatsApp group ids

import argparse
import asyncio
import json
import os
import sys
import io
import time
import requests
from datetime import datetime, timedelta

# Fix Windows console encoding for emoji/unicode
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pipeline import (
    classify_category,
    classify_country,
    fetch_all_feeds_parallel,
)
from store import article_id, format_whatsapp_message, mark_failed, mark_sent

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "selfhosted_config.json")
SENT_PATH = os.path.join(os.path.dirname(__file__), "sent_whatsapp.json")
MAX_PER_RUN = 1


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError(
            f"{CONFIG_PATH} not found. "
            "Make sure selfhosted_config.json exists with your group ID."
        )
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    
    if "YOUR_GROUP_ID" in cfg.get("groupId", ""):
        raise RuntimeError(
            "Please update selfhosted_config.json with your actual group ID.\n"
            "Run: python alerter_selfhosted.py --list-groups"
        )
    
    return cfg


def check_bot_ready(api_url):
    try:
        response = requests.get(f"{api_url}/status", timeout=5)
        return response.json().get("ready", False)
    except Exception as e:
        return False


def list_groups(api_url):
    try:
        response = requests.get(f"{api_url}/groups", timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise RuntimeError(
            f"Failed to get groups: {e}\n"
            "Make sure: node whatsapp_selfhosted.js is running"
        )


def send_message(api_url, group_id, message):
    response = requests.post(
        f"{api_url}/send",
        json={"groupId": group_id, "message": message},
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def load_sent():
    if os.path.exists(SENT_PATH):
        with open(SENT_PATH, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_sent(sent_ids):
    with open(SENT_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(sent_ids), f, indent=2)


def fetch_latest():
    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(fetch_all_feeds_parallel(None, week_ago, today))
    finally:
        loop.close()


def collect_new_items(sent_ids):
    seen_titles = set()
    new_items = []
    for publisher, _country, articles in fetch_latest():
        for article in articles:
            category = classify_category(article.title)
            country = classify_country(article.title)
            if category is None or country is None:
                continue

            title_key = article.title.lower().strip()[:50]
            if any(title_key in seen or seen in title_key for seen in seen_titles):
                continue
            seen_titles.add(title_key)

            item_id = article_id(article.title, article.link)
            if item_id in sent_ids:
                continue

            new_items.append({
                "id": item_id,
                "title": article.title,
                "link": article.link,
                "category": category,
                "country": country,
                "source": publisher,
            })
    return new_items


def format_message(item):
    return format_whatsapp_message(item)


def run_once(dry_run=False):
    cfg = load_config()
    api_url = cfg.get("botApiUrl", "http://localhost:3000")
    group_id = cfg["groupId"]
    
    # Check if bot is ready
    if not dry_run:
        if not check_bot_ready(api_url):
            print("ERROR: WhatsApp bot is not ready.")
            print("Make sure to run: node whatsapp_selfhosted.js")
            print("And scan the QR code with your spare WhatsApp.")
            return 0
    
    sent_ids = load_sent()
    new_items = collect_new_items(sent_ids)
    
    if not new_items:
        print("No new articles to send.")
        return 0

    to_send = new_items[:MAX_PER_RUN]
    print(f"Found {len(new_items)} new article(s). Sending {len(to_send)}.")

    for item in to_send:
        message = format_message(item)
        print("-" * 40)
        print(message)
        
        if dry_run:
            continue
        
        try:
            send_message(api_url, group_id, message)
            sent_ids.add(item["id"])
            save_sent(sent_ids)
            mark_sent(item, group_id=group_id)
        except Exception as exc:
            mark_failed(item, group_id=group_id, error=str(exc))
            print(f"Failed: {exc}")
            continue
        time.sleep(2)

    if dry_run:
        print("Dry run only. Nothing was posted.")
    else:
        print(f"\n✓ Sent {len(to_send)} message(s) to WhatsApp group")
    
    return len(to_send)


def main():
    parser = argparse.ArgumentParser(
        description="Post new construction news to WhatsApp group (self-hosted)."
    )
    parser.add_argument("--dry-run", action="store_true", help="Print messages without sending")
    parser.add_argument("--watch", action="store_true", help="Deprecated - use `python app.py` instead")
    parser.add_argument("--list-groups", action="store_true", help="List WhatsApp group ids")
    args = parser.parse_args()

    if args.list_groups:
        # For list-groups, we don't need a valid group ID yet
        api_url = "http://localhost:3000"
        
        print("\nFetching groups from local WhatsApp bot...")
        groups = list_groups(api_url)
        
        if not groups:
            print("No groups found. Make sure:")
            print("1. node whatsapp_selfhosted.js is running")
            print("2. You've scanned the QR code")
            print("3. The spare number is in at least one group")
            return
        
        print("\nYour WhatsApp groups:")
        print("=" * 60)
        for group in groups:
            print(f"{group['id']}")
            print(f"  Name: {group['name']}")
            print()
        
        print("Copy one of the group IDs above into selfhosted_config.json")
        return

    try:
        if args.watch:
            print("--watch is deprecated: `python app.py` now polls continuously and")
            print("auto-sends on its own (toggle it on in the Live tab). Running this")
            print("script's --watch at the same time would double-fetch and race with")
            print("app.py on the same queue/log files. Run `python app.py` instead.")
            print("This script still works for a manual one-shot check:")
            print("  python alerter_selfhosted.py [--dry-run]")
        else:
            run_once(dry_run=args.dry_run)
    except KeyboardInterrupt:
        print("\n\nStopped by user.")
    except Exception as e:
        print(f"\nError: {e}")


if __name__ == "__main__":
    main()
