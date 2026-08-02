#!/usr/bin/env python3
"""Reuters & NYT news mirror — fetch RSS + full text, output JSON files.

Runs inside GitHub Actions (US servers), so reuters.com / nytimes.com are
directly reachable. Results are committed back to the repo; OpenClaw reads
them via raw.githubusercontent.com.
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import httpx
from trafilatura import extract

OUT_DIR = Path("data")
OUT_DIR.mkdir(exist_ok=True)

# 路透各栏目 RSS（官网官方源，GitHub Actions 服务器可达）
REUTERS_FEEDS = {
    "world": "https://feeds.reuters.com/reuters/worldNews",
    "business": "https://feeds.reuters.com/reuters/businessNews",
    "markets": "https://feeds.reuters.com/reuters/marketsNews",
    "technology": "https://feeds.reuters.com/reuters/technologyNews",
}

# 纽约时报 RSS（免费额度内的公开 RSS）
NYT_FEEDS = {
    "world": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "business": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "technology": "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0"
}


def clean_title(t: str) -> str:
    return re.sub(r"\s*-\s*(Reuters|The New York Times|NYT)\s*$", "", (t or "")).strip()


def fetch_full_text(client: httpx.Client, url: str) -> str:
    """抓取文章页面并提取正文（trafilatura）"""
    try:
        r = client.get(url, headers=HEADERS, timeout=25, follow_redirects=True)
        r.raise_for_status()
        text = extract(r.text)
        return (text or "").strip()
    except Exception:
        return ""


def process_feed(client: httpx.Client, name: str, feed_url: str) -> list:
    items = []
    try:
        r = client.get(feed_url, headers=HEADERS, timeout=25, follow_redirects=True)
        r.raise_for_status()
        feed = feedparser.parse(r.text)
        for entry in feed.entries[:12]:
            url = entry.get("link", "")
            title = clean_title(entry.get("title", ""))
            if not url or not title:
                continue
            published = entry.get("published", "") or entry.get("updated", "")
            summary = re.sub(r"<[^>]+>", "", entry.get("summary", "") or "")
            # 抓全文（每篇限时，控制总时长）
            full_text = fetch_full_text(client, url)
            items.append({
                "source": name,
                "title": title,
                "url": url,
                "published": published,
                "summary": summary[:500],
                "full_text": full_text[:8000],
            })
    except Exception as e:
        print(f"[err] {name} {feed_url}: {e}")
    return items


def main():
    with httpx.Client() as client:
        all_items = []
        for name, url in REUTERS_FEEDS.items():
            all_items.extend(process_feed(client, f"reuters-{name}", url))
        for name, url in NYT_FEEDS.items():
            all_items.extend(process_feed(client, f"nyt-{name}", url))

    # 去重（按 URL）
    seen, dedup = set(), []
    for it in all_items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        dedup.append(it)

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(dedup),
        "items": dedup,
    }
    (OUT_DIR / "news.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"[OK] {len(dedup)} items -> data/news.json")


if __name__ == "__main__":
    main()
