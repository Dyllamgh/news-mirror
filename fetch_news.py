#!/usr/bin/env python3
"""Reuters & NYT news mirror — fetch RSS + full text, output JSON files.

Runs inside GitHub Actions (US servers), so reuters.com / nytimes.com are
directly reachable. Results are committed back to the repo; OpenClaw reads
them via raw.githubusercontent.com.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import httpx

OUT_DIR = Path("data")
OUT_DIR.mkdir(exist_ok=True)

# 路透源：旧 feeds.reuters.com 已停用，改用路透通讯社官网 + arc 端点
REUTERS_FEEDS = [
    ("reuters-world", "https://www.reutersagency.com/feed/?best-regions=world&post_type=best"),
    ("reuters-business", "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best"),
    ("reuters-markets", "https://www.reutersagency.com/feed/?best-topics=markets&post_type=best"),
    ("reuters-tech", "https://www.reutersagency.com/feed/?best-topics=technology&post_type=best"),
]

# 纽约时报 RSS
NYT_FEEDS = [
    ("nyt-world", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("nyt-business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("nyt-technology", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def clean_title(t: str) -> str:
    return re.sub(r"\s*-\s*(Reuters|The New York Times|NYT)\s*$", "", (t or "")).strip()


def fetch_full_text(client: httpx.Client, url: str, timeout: int = 20) -> str:
    """抓取文章页面提取正文：先 trafilatura，失败则 fallback 到 readability-lxml"""
    html = ""
    try:
        r = client.get(url, headers=HEADERS, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        html = r.text
    except Exception:
        return ""
    if not html:
        return ""
    # 引擎1: trafilatura
    try:
        from trafilatura import extract
        text = extract(html)
        if text and len(text.strip()) > 200:
            return text.strip()
    except Exception:
        pass
    # 引擎2: readability-lxml
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
    return ""


def process_feed(client: httpx.Client, name: str, feed_url: str) -> list:
    items = []
    try:
        r = client.get(feed_url, headers=HEADERS, timeout=25, follow_redirects=True)
        r.raise_for_status()
        feed = feedparser.parse(r.text)
        if not feed.entries:
            print(f"[warn] {name}: no entries from {feed_url}")
            return []
        for entry in feed.entries[:12]:
            url = entry.get("link", "")
            title = clean_title(entry.get("title", ""))
            if not url or not title:
                continue
            published = entry.get("published", "") or entry.get("updated", "")
            # RSS 摘要（NYT 通常带较完整描述）
            summary = re.sub(r"<[^>]+>", " ", entry.get("summary", "") or entry.get("description", "") or "")
            summary = re.sub(r"\s+", " ", summary).strip()
            # 尝试抓全文（控制单篇耗时，总量限制）
            full_text = fetch_full_text(client, url)
            items.append({
                "source": name,
                "title": title,
                "url": url,
                "published": published,
                "summary": summary[:800],
                "full_text": full_text[:10000],
            })
    except Exception as e:
        print(f"[err] {name} {feed_url}: {e}")
    return items


def main():
    with httpx.Client() as client:
        all_items = []
        for name, url in REUTERS_FEEDS:
            all_items.extend(process_feed(client, name, url))
        for name, url in NYT_FEEDS:
            all_items.extend(process_feed(client, name, url))

    # 去重（按 URL）
    seen, dedup = set(), []
    for it in all_items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        dedup.append(it)

    # 统计
    from collections import Counter
    src = Counter(i["source"].split("-")[0] for i in dedup)
    with_text = sum(1 for i in dedup if i["full_text"])
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
