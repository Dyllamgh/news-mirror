#!/usr/bin/env python3
"""Reuters & NYT news mirror — fetch RSS + full text, output JSON files.

Runs inside GitHub Actions (US servers). Reuters has no public RSS anymore,
so we scrape the official site pages directly; NYT uses RSS + full-text fetch.
"""
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import httpx

OUT_DIR = Path("data")
OUT_DIR.mkdir(exist_ok=True)

# ---------- 请求头（模拟真实浏览器，降低反爬拦截） ----------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# ---------- 纽约时报 RSS（官方公开 RSS） ----------
NYT_FEEDS = [
    ("nyt-world", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("nyt-business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("nyt-technology", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
]

# ---------- 路透官网栏目页（无公开 RSS，直接抓页面提取链接） ----------
REUTERS_SECTIONS = [
    ("reuters-world", "https://www.reuters.com/world/"),
    ("reuters-business", "https://www.reuters.com/business/"),
    ("reuters-markets", "https://www.reuters.com/markets/"),
    ("reuters-tech", "https://www.reuters.com/technology/"),
]

REUTERS_ARTICLE_RE = re.compile(r"https://www\.reuters\.com/[a-z0-9-]+/[a-z0-9-]+/\d{4}/\d{2}/\d{2}/[a-z0-9-]+/")


def clean_title(t: str) -> str:
    return re.sub(r"\s*-\s*(Reuters|The New York Times|NYT)\s*$", "", (t or "")).strip()


def extract_text(html: str) -> str:
    """trafilatura 优先，readability-lxml 兜底"""
    try:
        from trafilatura import extract
        text = extract(html)
        if text and len(text.strip()) > 100:
            return text.strip()
    except Exception:
        pass
    try:
        from readability import Document
        doc = Document(html)
        content = doc.summary(html_partial=True)
        text = re.sub(r"<[^>]+>", " ", content)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 100:
            return text
    except Exception:
        pass
    return ""


def fetch_page(client: httpx.Client, url: str, timeout: int = 25) -> str:
    try:
        r = client.get(url, headers=HEADERS, timeout=timeout, follow_redirects=True)
        if r.status_code == 200:
            return r.text
        print(f"  [warn] {url} -> HTTP {r.status_code}")
    except Exception as e:
        print(f"  [err] {url} -> {e}")
    return ""


def fetch_nyt(client: httpx.Client, name: str, feed_url: str) -> list:
    items = []
    html = fetch_page(client, feed_url)
    if not html:
        return items
    feed = feedparser.parse(html)
    for entry in feed.entries[:10]:
        url = entry.get("link", "")
        title = clean_title(entry.get("title", ""))
        if not url or not title:
            continue
        published = entry.get("published", "") or entry.get("updated", "")
        summary = re.sub(r"<[^>]+>", " ", entry.get("summary", "") or entry.get("description", "") or "")
        summary = re.sub(r"\s+", " ", summary).strip()
        full_text = ""
        page = fetch_page(client, url, timeout=20)
        if page:
            full_text = extract_text(page)
        items.append({
            "source": name, "title": title, "url": url,
            "published": published, "summary": summary[:800],
            "full_text": full_text[:10000],
        })
    return items


def fetch_reuters(client: httpx.Client, name: str, section_url: str) -> list:
    """抓路透栏目页 → 提取文章链接 → 逐篇抓全文"""
    items = []
    html = fetch_page(client, section_url)
    if not html:
        return items
    # 提取文章链接
    urls = []
    for m in REUTERS_ARTICLE_RE.finditer(html):
        u = m.group(0).rstrip("/")
        if u not in urls:
            urls.append(u)
    print(f"  [info] {name}: found {len(urls)} article links")
    for url in urls[:8]:
        page = fetch_page(client, url, timeout=20)
        if not page:
            continue
        full_text = extract_text(page)
        # 标题：页面 <title>
        m = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
        title = clean_title(m.group(1).strip() if m else "")
        items.append({
            "source": name, "title": title or url.split("/")[-1].replace("-", " "),
            "url": url, "published": "", "summary": full_text[:800],
            "full_text": full_text[:10000],
        })
    return items


def main():
    with httpx.Client() as client:
        all_items = []
        print("[1/3] Fetching NYT RSS...")
        for name, url in NYT_FEEDS:
            all_items.extend(fetch_nyt(client, name, url))
        print("[2/3] Fetching Reuters sections...")
        for name, url in REUTERS_SECTIONS:
            all_items.extend(fetch_reuters(client, name, url))

    # 去重（按 URL）
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
