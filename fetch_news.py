#!/usr/bin/env python3
"""Reuters & NYT news mirror v5 — resolve real URLs, then full-text extraction.

Runs inside GitHub Actions (US servers):
- NYT: official RSS for titles/links; full text via direct fetch (cookie jar) + readability
- Reuters: Google News RSS -> resolve redirect to real reuters.com URL -> jina/readability
"""
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import httpx

OUT_DIR = Path("data")
OUT_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

NYT_FEEDS = [
    ("nyt-world", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("nyt-business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("nyt-technology", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
]

REUTERS_GN_QUERIES = [
    ("reuters-world", "site:reuters.com world"),
    ("reuters-business", "site:reuters.com business"),
    ("reuters-markets", "site:reuters.com markets"),
    ("reuters-tech", "site:reuters.com technology"),
]
GN_URL = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

JINA = "https://r.jina.ai/"


def clean_title(t: str) -> str:
    return re.sub(r"\s*-\s*(Reuters|The New York Times|NYT)\s*$", "", (t or "")).strip()


def extract_with_readability(html: str) -> str:
    try:
        from readability import Document
        doc = Document(html)
        content = doc.summary(html_partial=True)
        text = re.sub(r"<[^>]+>", " ", content)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 200:
            return text
    except Exception:
        pass
    try:
        from trafilatura import extract
        text = extract(html)
        if text and len(text.strip()) > 200:
            return text.strip()
    except Exception:
        pass
    return ""


def resolve_gn_url(client: httpx.Client, gn_url: str) -> str:
    """把 Google News 跳转链接解析成真实 URL"""
    try:
        r = client.get(gn_url, headers=HEADERS, timeout=20, follow_redirects=True)
        return str(r.url)
    except Exception:
        return gn_url


def fetch_fulltext(client: httpx.Client, url: str) -> str:
    """尝试多种方式抓正文"""
    # 方式1: 直接抓 + readability
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True) as c2:
            r = c2.get(url, timeout=25)
            if r.status_code == 200:
                text = extract_with_readability(r.text)
                if text:
                    return text[:10000]
            else:
                print(f"  [direct] {url[:70]} -> HTTP {r.status_code}")
    except Exception as e:
        print(f"  [direct] {url[:70]} -> {e}")
    # 方式2: r.jina.ai
    try:
        r = client.get(JINA + url, headers=HEADERS, timeout=40, follow_redirects=True)
        if r.status_code == 200 and len(r.text.strip()) > 200:
            return r.text.strip()[:10000]
        else:
            print(f"  [jina] {url[:70]} -> HTTP {r.status_code}")
    except Exception as e:
        print(f"  [jina] {url[:70]} -> {e}")
    return ""


def fetch_nyt(client: httpx.Client, name: str, feed_url: str) -> list:
    items = []
    try:
        r = client.get(feed_url, headers=HEADERS, timeout=25, follow_redirects=True)
        feed = feedparser.parse(r.text)
        for entry in feed.entries[:10]:
            url = entry.get("link", "")
            title = clean_title(entry.get("title", ""))
            if not url or not title:
                continue
            published = entry.get("published", "") or entry.get("updated", "")
            summary = re.sub(r"<[^>]+>", " ", entry.get("summary", "") or entry.get("description", "") or "")
            summary = re.sub(r"\s+", " ", summary).strip()
            full_text = fetch_fulltext(client, url)
            time.sleep(1)
            items.append({
                "source": name, "title": title, "url": url,
                "published": published, "summary": summary[:1000],
                "full_text": full_text,
            })
    except Exception as e:
        print(f"[err] {name}: {e}")
    return items


def fetch_reuters(client: httpx.Client, name: str, query: str) -> list:
    items = []
    try:
        r = client.get(GN_URL.format(q=query.replace(" ", "+")), headers=HEADERS, timeout=25, follow_redirects=True)
        feed = feedparser.parse(r.text)
        for entry in feed.entries[:10]:
            gn_link = entry.get("link", "")
            title = clean_title(entry.get("title", ""))
            if not gn_link or not title:
                continue
            published = entry.get("published", "") or entry.get("updated", "")
            summary = re.sub(r"<[^>]+>", " ", entry.get("summary", "") or entry.get("description", "") or "")
            summary = re.sub(r"\s+", " ", summary).strip()
            # 解析真实 URL
            real_url = resolve_gn_url(client, gn_link)
            full_text = fetch_fulltext(client, real_url)
            time.sleep(1)
            items.append({
                "source": name, "title": title, "url": real_url,
                "published": published, "summary": summary[:1000],
                "full_text": full_text,
            })
    except Exception as e:
        print(f"[err] {name}: {e}")
    return items


def main():
    with httpx.Client() as client:
        all_items = []
        print("[1/3] NYT ...")
        for name, url in NYT_FEEDS:
            all_items.extend(fetch_nyt(client, name, url))
        print("[2/3] Reuters ...")
        for name, q in REUTERS_GN_QUERIES:
            all_items.extend(fetch_reuters(client, name, q))

    seen, dedup = set(), []
    for it in all_items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        dedup.append(it)

    src = Counter(i["source"].split("-")[0] for i in dedup)
    with_text = sum(1 for i in dedup if len(i["full_text"]) > 300)
    print(f"[OK] {len(dedup)} items | sources: {dict(src)} | with_full_text: {with_text}")

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(dedup),
        "items": dedup,
    }
    (OUT_DIR / "news.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
