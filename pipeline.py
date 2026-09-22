# pipeline.py

import requests
import feedparser
import json
import os
import hashlib
from urllib.parse import quote
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta
import asyncio
import aiohttp
from concurrent.futures import ThreadPoolExecutor
from sources import COUNTRY_KEYWORDS, TRUSTED_PUBLISHERS, CONSTRUCTION_KEYWORDS
from store import BUSINESS_TZ, article_id, titles_are_same_story

# Cache settings
CACHE_DIR = "cache"
CACHE_DURATION_MINUTES = 30  # Cache results for 30 minutes (extended from 15)


def fetch_feed(url):
    """Download a feed URL and return its list of articles."""
    try:
        response = requests.get(url, timeout=12)  # Reduced from 20s to 12s
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        return parsed.entries
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return []


async def fetch_feed_async(session, url):
    """Async version of fetch_feed for parallel requests."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as response:  # Reduced from 20s to 12s
            content = await response.read()
            parsed = feedparser.parse(content)
            return parsed.entries
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return []


def build_search_url(publisher_domain, country, keyword=None, date_from=None, date_to=None):
    """Build a Google News RSS search URL scoped to one publisher and one
    country, topically anchored to construction news.

    Deliberately does NOT put the award-keyword OR-list (see
    classify_category) into this query: Google silently stops honoring
    `when:` once the query gets that long/complex, so a giant OR-list here
    was making the date filter a no-op - it returned "best textual match"
    articles from any year, and the app's own date-range filter then threw
    almost all of them away. classify_category() already re-checks for
    award language client-side with more precision than a keyword-soup
    Google query could anyway, so that's the only place it needs to happen.

    Args:
        publisher_domain: Domain to search (e.g., "zawya.com")
        country: "UAE" or "Saudi Arabia"
        keyword: Optional additional keyword to filter
        date_from: Optional start date (YYYY-MM-DD format)
        date_to: Optional end date (YYYY-MM-DD format)
    """
    query_parts = [f"site:{publisher_domain}", "construction"]

    # Add optional keyword filter
    if keyword:
        query_parts.append(f'"{keyword}"')
    
    # Add date filtering
    if date_from and date_to:
        try:
            start = datetime.strptime(date_from, "%Y-%m-%d")
            end = datetime.strptime(date_to, "%Y-%m-%d")
            days_diff = (end - start).days + 1
            
            # Google News supports: when:1d, when:7d, when:1m, when:1y
            if days_diff <= 1:
                query_parts.append("when:1d")
            elif days_diff <= 7:
                query_parts.append("when:7d")
            elif days_diff <= 30:
                query_parts.append("when:1m")
            else:
                query_parts.append("when:1y")
        except ValueError:
            query_parts.append("when:1d")  # Default fallback
    else:
        query_parts.append("when:1d")  # Default to last day
    
    query = " ".join(query_parts)
    gl = "AE" if country == "UAE" else "SA"
    ceid = f"{gl}:en"

    return f"https://news.google.com/rss/search?q={quote(query)}&hl=en&gl={gl}&ceid={ceid}"


def matches_construction(title):
    """
    Check if the title matches construction keywords.
    Returns True if any construction keyword is found.
    """
    title_lower = title.lower()
    for kw in CONSTRUCTION_KEYWORDS:
        if kw.lower() in title_lower:
            return True
    return False


def classify_category(title):
    """Cheap first-pass tag. Returns an award category or None.

    This is not the final gate - it's a keyword hint shown in Search/Extracted.
    llm_judge.judge_award_articles() is what decides Contract vs Project vs drop
    before anything reaches Live or gets sent.
    """
    title_lower = title.lower()
    
    # Award action words (expanded for better coverage)
    award_verbs = [
        "awarded", "awards", "wins", "won", "secures", "secured",
        "signs", "signed", "bags", "bagged", "lands", "landed",
        "inks", "inked", "clinches", "clinched", "appoints", "appointed",
        "selects", "selected", "names", "named", "picks", "picked",
        "chooses", "chosen", "grants", "granted"
    ]
    
    has_award_verb = any(verb in title_lower for verb in award_verbs)
    
    # CONTRACT AWARDED - More lenient matching
    contract_keywords = ["contract", "deal", "tender", "agreement", "procurement"]
    has_contract = any(word in title_lower for word in contract_keywords)
    
    if has_contract:
        # High confidence: contract + award verb
        if has_award_verb:
            return "Contract Awarded"
        
        # Medium confidence: contract + value (even without construction keyword)
        has_value = any(val in title_lower for val in ["$", "aed", "sar", "dh", "million", "billion", "bn", "m", "worth"])
        if has_value:
            return "Contract Awarded"
        
        # Lower confidence: contract + construction terms
        if any(term in title_lower for term in ["construction", "building", "project", "development", "epc"]):
            return "Contract Awarded"
    
    # PROJECT AWARDED - More lenient matching
    project_keywords = ["project", "development", "scheme", "initiative", "facility"]
    has_project = any(word in title_lower for word in project_keywords)
    
    # High confidence: contractor appointment
    if "contractor" in title_lower and has_award_verb:
        return "Project Awarded"
    
    # High confidence: project + award verb
    if has_project and has_award_verb:
        return "Project Awarded"
    
    # Medium confidence: project + value
    if has_project:
        has_value = any(val in title_lower for val in ["$", "aed", "sar", "dh", "million", "billion"])
        if has_value:
            return "Project Awarded"
    
    # Catch edge cases: "for" + construction term (common pattern)
    if " for " in title_lower and has_award_verb:
        construction_terms = ["tower", "building", "mall", "hospital", "road", "bridge", "airport", "metro", "railway"]
        if any(term in title_lower for term in construction_terms):
            return "Contract Awarded"
    
    return None


def classify_country(title):
    """Return 'UAE', 'Saudi Arabia', or None based on keywords in the headline."""
    title_lower = title.lower()
    for country, keywords in COUNTRY_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in title_lower:
                return country
    return None


def load_seen_ids(path="seen.json"):
    if os.path.exists(path):
        with open(path) as f:
            return set(json.load(f))
    return set()


def save_seen_ids(seen_ids, path="seen.json"):
    with open(path, "w") as f:
        json.dump(list(seen_ids), f)


def get_cache_key(keyword, date_from, date_to, category):
    """Generate a cache key based on search parameters."""
    params = f"{keyword}|{date_from}|{date_to}|{category}"
    return hashlib.md5(params.encode()).hexdigest()


def load_from_cache(cache_key):
    """Load cached results if they exist and are fresh."""
    if not os.path.exists(CACHE_DIR):
        return None
    
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.json")
    if not os.path.exists(cache_file):
        return None
    
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)
        
        # Check if cache is still fresh
        cached_time = datetime.fromisoformat(cache_data['timestamp'])
        age_minutes = (datetime.now() - cached_time).total_seconds() / 60
        
        if age_minutes < CACHE_DURATION_MINUTES:
            print(f"Using cached results (age: {age_minutes:.1f} minutes)")
            return cache_data['results']
        else:
            print(f"Cache expired (age: {age_minutes:.1f} minutes)")
            return None
    except Exception as e:
        print(f"Error loading cache: {e}")
        return None


def save_to_cache(cache_key, results):
    """Save results to cache."""
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.json")
    cache_data = {
        'timestamp': datetime.now().isoformat(),
        'results': results
    }
    
    try:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=2)
        print(f"Results cached successfully")
    except Exception as e:
        print(f"Error saving cache: {e}")


def write_html_report(items, path="report.html"):
    """Write a simple, clean HTML page listing all items, grouped by category."""
    rows_html = ""
    for category in ["Contract Awarded", "Project Awarded"]:
        cat_items = [i for i in items if i["category"] == category]
        rows_html += f'<h2>{category} ({len(cat_items)})</h2>'
        if not cat_items:
            rows_html += "<p class='empty'>No new items this run.</p>"
        for i in cat_items:
            rows_html += f"""
            <div class="item">
                <a href="{i['link']}" target="_blank">{i['title']}</a>
                <div class="meta">{i['country']} &middot; {i['source']}</div>
            </div>
            """

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>UAE & Saudi Construction News</title>
<style>
  body {{ font-family: Georgia, serif; max-width: 720px; margin: 40px auto;
          color: #222; padding: 0 16px; }}
  h1 {{ font-size: 22px; border-bottom: 2px solid #b5551e; padding-bottom: 8px; }}
  h2 {{ font-size: 16px; color: #b5551e; margin-top: 28px; }}
  .item {{ padding: 10px 0; border-bottom: 1px solid #ddd; }}
  .item a {{ color: #222; text-decoration: none; font-size: 15px; }}
  .item a:hover {{ text-decoration: underline; }}
  .meta {{ color: #777; font-size: 12px; margin-top: 3px; }}
  .empty {{ color: #999; font-style: italic; }}
</style>
</head>
<body>
<h1>UAE & Saudi Construction News — {len(items)} new item(s)</h1>
{rows_html}
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report written to {path}")


async def fetch_all_feeds_parallel(keyword=None, date_from=None, date_to=None):
    """Fetch all feeds in parallel using async requests."""
    tasks = []
    
    async with aiohttp.ClientSession() as session:
        for publisher in TRUSTED_PUBLISHERS:
            for country in ["UAE", "Saudi Arabia"]:
                url = build_search_url(publisher, country, keyword, date_from, date_to)
                tasks.append((publisher, country, fetch_feed_async(session, url)))
        
        results = []
        for publisher, country, task in tasks:
            articles = await task
            results.append((publisher, country, articles))

        return results


def dedup_articles(feed_results):
    """Keep construction + UAE/Saudi headlines, then cross-source-dedupe.

    This is the raw pool: date + keyword (construction) + source (country)
    matching only. Category here is a keyword hint only, never a relevance
    filter - llm_judge.judge_award_articles() is what decides Contract vs
    Project vs drop, further downstream in the poll cycle.
    """
    seen_articles = {}
    for publisher, _country, articles in feed_results:
        for a in articles:
            if not matches_construction(a.title):
                continue

            cat = classify_category(a.title) or "General Construction"
            country_tag = classify_country(a.title)
            if country_tag is None:
                continue

            incoming = {
                "title": a.title,
                "link": a.link,
                "source": publisher,
                "cat": cat,
                "country": country_tag,
                "published": a.get("published", ""),
            }
            match_key = next(
                (key for key, seen in seen_articles.items() if titles_are_same_story(a.title, seen["title"])),
                None,
            )
            if match_key:
                existing = seen_articles[match_key]
                try:
                    incoming_rank = TRUSTED_PUBLISHERS.index(publisher)
                    existing_rank = TRUSTED_PUBLISHERS.index(existing["source"])
                except ValueError:
                    incoming_rank, existing_rank = 10_000, 0
                if incoming_rank < existing_rank:
                    seen_articles[match_key] = incoming
                continue

            seen_articles[a.link or a.title] = incoming

    return list(seen_articles.values())


def finalize_articles(deduped, date_from=None, date_to=None):
    """Turn deduped article dicts into the app's result shape: adds an id,
    a display date string, and a full published_at timestamp (used for
    is_fresh()'s calendar-day check), and applies date-range filtering when
    both bounds are given.

    The display date is shown in BUSINESS_TZ (UAE/KSA, UTC+4), not the RSS
    feed's own timezone (usually UTC) - otherwise something published at
    9pm UTC (1am UAE time, i.e. already "today" locally) would display as
    yesterday's date while is_fresh() correctly treats it as today, which
    reads as a bug even though it isn't one."""
    results = []
    for article_data in deduped:
        published_raw = article_data["published"]
        published_at = ""
        try:
            article_date = parsedate_to_datetime(published_raw)
            date_str = article_date.astimezone(BUSINESS_TZ).strftime("%Y-%m-%d")
            published_at = article_date.isoformat()
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
            "published_at": published_at,
        })
    return results


def fetch_candidates_sync(keyword=None, date_from=None, date_to=None):
    """Fetch and keyword-filter only. Does not run the LLM."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        feed_results = loop.run_until_complete(fetch_all_feeds_parallel(keyword, date_from, date_to))
        return dedup_articles(feed_results)
    finally:
        loop.close()


def main():
    seen_ids = load_seen_ids()
    all_items = []

    # Use asyncio for parallel fetching
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    results = loop.run_until_complete(fetch_all_feeds_parallel())
    loop.close()

    for publisher, country, articles in results:
        print(f"Processing {publisher} ({country}): {len(articles)} articles")
        
        for a in articles:
            category = classify_category(a.title)
            article_country = classify_country(a.title)

            if category is None or article_country is None:
                continue

            item_id = article_id(a.title, a.link)
            if item_id in seen_ids:
                continue

            seen_ids.add(item_id)
            all_items.append({
                "title": a.title,
                "link": a.link,
                "category": category,
                "country": article_country,
                "source": publisher,
            })

    write_html_report(all_items)
    save_seen_ids(seen_ids)


if __name__ == "__main__":
    main()