# llm_judge.py
#
# Final gate for "is this actually a construction contract/project award?"
# Keywords in pipeline.py are a cheap net. This module is the strict judge.
#
# Set GEMINI_API_KEY or OPENAI_API_KEY in the environment or a local .env file.
# If neither is set, pipeline.py falls back to keyword-only award matching.

import json
import os
import re
from datetime import datetime

import requests

AWARD_CATEGORIES = ("Contract Awarded", "Project Awarded")
BATCH_SIZE = 15

SYSTEM_PROMPT = """You judge Middle East construction news headlines.

KEEP a headline only if ALL of these are true:
1) It is about physical construction, infrastructure, EPC, buildings, housing, or real-estate development.
2) A contract/tender was awarded, signed, won, inked, or a contractor was appointed, or a JV was formed to actually develop/build a named project in the UAE or Saudi Arabia.
3) The parties are developers, contractors, consultants, or government bodies in that construction story — not AI, software, IT, or unrelated finance companies.

If the headline never mentions construction, building, infrastructure, EPC, contractor, housing, roads, metro, airport, or a development project, REJECT it.

REJECT everything else, including:
- building-permit statistics, market commentary, listings, sales ads
- traffic accidents, CSR/charity
- tech, AI, software, IT, telecom, or finance deals even if they say signed/inks/agreement
- generic "construction sector grows" stories with no award
- sports, defense, or oilfield service contracts that are not building/infrastructure construction
- "exploring" or "in talks" with no award, appointment, or signed construction/development deal

Return JSON only:
{"results":[{"i":0,"keep":true,"category":"Contract Awarded"|"Project Awarded"|null,"reason":"short reason"}]}
Use Contract Awarded for contracts/tenders/EPC deals.
Use Project Awarded for project awards, contractor appointments, development JVs.
If keep is false, category must be null. reason must be one short sentence.
"""


def _load_dotenv():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def llm_configured():
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY"))


def _parse_json_payload(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("results") or data.get("items") or []
    return []


def _call_gemini(prompt):
    key = os.environ["GEMINI_API_KEY"]
    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    response = requests.post(
        url,
        params={"key": key},
        json={
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        },
        timeout=40,
    )
    response.raise_for_status()
    text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_json_payload(text)


def _call_openai(prompt):
    key = os.environ["OPENAI_API_KEY"]
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=40,
    )
    response.raise_for_status()
    text = response.json()["choices"][0]["message"]["content"]
    return _parse_json_payload(text)


def _judge_batch(batch):
    lines = []
    for i, article in enumerate(batch):
        lines.append(f"{i}. [{article.get('source', '')}] {article.get('title', '')}")
    prompt = "Judge these headlines:\n" + "\n".join(lines)
    if os.environ.get("GEMINI_API_KEY"):
        return _call_gemini(prompt)
    return _call_openai(prompt)


def judge_award_articles(articles):
    """Judge headlines. Returns (kept_articles, evaluations).

    evaluations includes both kept and dropped rows so the UI can show
    how the LLM selected.
    """
    now = datetime.now().isoformat(timespec="seconds")

    if not articles:
        return [], []

    def row(article, keep, category, reason):
        title = article.get("title", "")
        link = article.get("link", "")
        aid = article.get("id")
        if not aid:
            import hashlib
            aid = hashlib.md5(f"{title}{link}".encode()).hexdigest()[:16]
        return {
            "id": aid,
            "title": title,
            "link": link,
            "source": article.get("source", ""),
            "country": article.get("country", ""),
            "date": article.get("date", ""),
            "keep": bool(keep),
            "category": category if keep else None,
            "reason": reason,
            "evaluated_at": now,
        }

    if not llm_configured():
        kept, evaluations = [], []
        for article in articles:
            hint = article.get("cat") or article.get("category")
            keep = hint in AWARD_CATEGORIES
            category = hint if keep else None
            reason = (
                "Keyword match: award/contract language in the headline."
                if keep else
                "Keyword filter: not a contract or project award."
            )
            evaluations.append(row(article, keep, category, reason))
            if keep:
                kept.append({**article, "cat": category, "llm_approved": True})
        print(f"LLM off — keyword award filter kept {len(kept)}/{len(articles)}", flush=True)
        return kept, evaluations

    kept, evaluations = [], []
    for start in range(0, len(articles), BATCH_SIZE):
        batch = articles[start:start + BATCH_SIZE]
        try:
            results = _judge_batch(batch)
        except Exception as exc:
            print(f"LLM judge failed, dropping this batch: {exc}", flush=True)
            for article in batch:
                evaluations.append(row(article, False, None, f"LLM error: {exc}"))
            continue

        by_index = {}
        for item in results:
            try:
                by_index[int(item.get("i"))] = item
            except (TypeError, ValueError):
                continue

        for i, article in enumerate(batch):
            item = by_index.get(i) or {}
            category = item.get("category")
            keep = bool(item.get("keep")) and category in AWARD_CATEGORIES
            reason = (item.get("reason") or "").strip()
            if not reason:
                reason = "Construction contract/project award." if keep else "Not a construction contract or project award."
            evaluations.append(row(article, keep, category if keep else None, reason))
            if keep:
                kept.append({**article, "cat": category, "llm_approved": True})

    print(f"LLM award filter kept {len(kept)}/{len(articles)}", flush=True)
    return kept, evaluations
