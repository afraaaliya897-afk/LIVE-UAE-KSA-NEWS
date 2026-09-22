# llm_judge.py
#
# Final gate for "is this actually a construction contract/project award?"
# Keywords in pipeline.py are a cheap net. This module is the strict judge.
#
# Set GEMINI_API_KEY or OPENAI_API_KEY in the environment or a local .env file.
# If neither is set, nothing is approved. Keywords never count as an LLM pass.

import json
import os
import re
from datetime import datetime

import requests

AWARD_CATEGORIES = ("Contract Awarded", "Project Awarded")
BATCH_SIZE = 15
_AWARD_CUE = re.compile(
    r"\b(awarded|award|awards|wins|won|secures|secured|signed|signs|inks|inked|"
    r"appointed|appoints|bags|bagged|clinches|clinched|contract|tender|epc|"
    r"contractor|selected|named)\b",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """You are the final editor for a UAE and Saudi Arabia construction desk.

USER INTENT
The reader wants only construction news from the UAE or Saudi Arabia where a project or a contract was actually awarded. Related means the same event told another way: tender won, EPC signed, contractor or consultant appointed, package awarded, joint venture formed to build a named project. It does not mean "anything about construction".

NEWS INTENT
Judge what the headline is reporting, not which keywords it contains.
Ask: did someone award, win, sign, or get appointed to deliver physical construction work?
If the headline is really about sales, prices, occupancy, the economy, oil, flights, policy, or commentary, the news intent is not an award. REJECT it.

KEEP only when ALL are true:
1) The work is physical construction, infrastructure, EPC, buildings, housing delivery, or real-estate development in the UAE or Saudi Arabia.
2) The headline reports a completed award: awarded, won, secured, signed, inked, or a contractor/consultant was appointed.
3) A named project, package, or scope is being given to a builder, developer, consultant, or government client.

REJECT, even if the headline mentions construction, projects, homes, or a large sum:
- homes sold, units sold out, sales, bookings, occupancy, hotel performance
- market growth, forecasts, statistics, permit counts, "sector grows"
- "plans", "eyes", "exploring", "in talks", "mulls", "proposed" with no award
- oil, energy trading, flights, telecom, AI, software, IT, finance, defense, sports
- CSR, accidents, appointments of CEOs that are not a construction contract

Examples:
KEEP "Besix awarded AED 500m contract to build Dubai metro station" -> Contract Awarded
KEEP "NEOM appoints contractor for staff housing project" -> Project Awarded
REJECT "Sharjah waterfront sells all homes before construction begins" -> sales, not an award
REJECT "Saudi economy projected to grow" -> not construction
REJECT "Developer exploring Riyadh tower" -> no award yet

Return JSON only:
{"results":[{"i":0,"keep":true,"category":"Contract Awarded"|"Project Awarded"|null,"reason":"short reason"}]}
Contract Awarded = a contract, tender, or EPC deal was awarded or signed.
Project Awarded = a project was awarded or a contractor was appointed to build it.
If keep is false, category must be null. If you are unsure, keep must be false.
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
        evaluations = [
            row(article, False, None, "LLM is not configured, so nothing is approved.")
            for article in articles
        ]
        print("LLM off — nothing approved", flush=True)
        return [], evaluations

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
            reason = (item.get("reason") or "").strip()
            keep = bool(item.get("keep")) and category in AWARD_CATEGORIES
            title = article.get("title") or ""
            if keep and not _AWARD_CUE.search(title):
                keep = False
                reason = "Headline does not report an award, signed contract, or contractor appointment."
            elif not reason:
                reason = (
                    "UAE/KSA construction contract or project award."
                    if keep else
                    "Not a UAE/KSA construction contract or project award."
                )
            evaluations.append(row(article, keep, category if keep else None, reason))
            if keep:
                kept.append({**article, "cat": category, "llm_approved": True})

    print(f"LLM award filter kept {len(kept)}/{len(articles)}", flush=True)
    return kept, evaluations
