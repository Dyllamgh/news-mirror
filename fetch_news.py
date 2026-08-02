#!/usr/bin/env python3
"""Reuters & NYT news mirror v4 — RSS/links + r.jina.ai full-text extraction.

Runs inside GitHub Actions (US servers):
- NYT: official RSS for titles/links
- Reuters: Google News RSS (site:reuters.com) for links (Reuters has no public RSS)
- Full text: r.jina.ai reader (handles anti-bot; reachable from outside GFW)
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
}

# 纽约时报官方 RSS
NYT_FEEDS = [
    ("nyt-world", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("nyt-business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("nyt-technology", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
]

# 路透：Google News RSS 检索（路透已无公开 RSS）
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


def fetch_rss(client: httpx.Client, url: str) -> feedparser.FeedParserDict:
    r = client.get(url, headers=HEADERS, timeout=25, follow_redirects=True)
    r.raise_for_status()
    return feedparser.parse(r.text)


def fetch_fulltext_jina(client: httpx.Client, url: str) -> str:
    """用 r.jina.ai reader 提取正文（处理反爬）"""
    try:
        r = client.get(JINA + url, headers=HEADERS, timeout=40, follow_redirects=True)
        if r.status_code == 200:
            text = r.text.strip()
            # 去掉可能的导航噪音，保留正文
            if len(text) > 150:
                return text[:10000]
        else:
            print(f"  [jina] {url} -> HTTP {r.status_code}")
    except Exception as e:
        print(f"  [jina] {url} -> {e}")
    return ""


def fetch_nyt(client: httpx.Client, name: str, feed_url: str) -> list:
    items = []
    try:
        feed = fetch_rss(client, feed_url)
        for entry in feed.entries[:10]:
            url = entry.get("link", "")
            title = clean_title(entry.get("title", ""))
            if not url or not title:
                continue
            published = entry.get("published", "") or entry.get("updated", "")
            summary = re.sub(r"<[^>]+>", " ", entry.get("summary", "") or entry.get("description", "") or "")
            summary = re.sub(r"\s+", " ", summary).strip()
            full_text = fetch_fulltext_jina(client, url)
            time.sleep(1.5)  # jina 限流保护
            items.append({
                "source": name, "title": title, "url": url,
                "published": published, "summary": summary[:800],
                "full_text": full_text,
            })
    except Exception as e:
        print(f"[err] {name}: {e}")
    return items


def fetch_reuters(client: httpx.Client, name: str, query: str) -> list:
    items = []
    try:
        feed = fetch_rss(client, GN_URL.format(q=query.replace(" ", "+")))
        for entry in feed.entries[:10]:
            # Google News 链接是跳转链接，需要解析真实 URL
            url = entry.get("link", "")
            title = clean_title(entry.get("title", ""))
            if not url or not title:
                continue
            published = entry.get("published", "") or entry.get("updated", "")
            summary = re.sub(r"<[^>]+>", " ", entry.get("summary", "") or entry.get("description", "") or "")
            summary = re.sub(r"\s+", " ", summary).strip()
            full_text = fetch_fulltext_jina(client, url)
            time.sleep(1.5)
            items.append({
                "source": name, "title": title, "url": url,
                "published": published, "summary": summary[:800],
                "full_text": full_text,
            })
    except Exception as e:
        print(f"[err] {name}: {e}")
    return items


def main():
    with httpx.Client() as client:
        all_items = []
        print("[1/3] NYT RSS + jina fulltext ...")
        for name, url in NYT_FEEDS:
            all_items.extend(fetch_nyt(client, name, url))
        print("[2/3] Reuters via Google News RSS + jina fulltext ...")
        for name, q in REUTERS_GN_QUERIES:
            all_items.extend(fetch_reuters(client, name, q))

    # 去重
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
