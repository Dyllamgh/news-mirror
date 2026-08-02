#!/usr/bin/env python3
"""Diagnostic: test which channels can fetch Reuters/NYT full text from GitHub Actions."""
import json
import re
import base64
from pathlib import Path

import httpx

OUT = Path("data")
OUT.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

results = {}

def test(name, fn):
    try:
        results[name] = fn()
    except Exception as e:
        results[name] = {"error": str(e)[:120]}

# 1. Reuters sitemap
def t_sitemap():
    r = httpx.get("https://www.reuters.com/sitemap.xml", headers=HEADERS, timeout=30, follow_redirects=True)
    return {"status": r.status_code, "size": len(r.text), "sample": r.text[:200]}

# 2. Reuters article page direct
def t_rt_article():
    r = httpx.get("https://www.reuters.com/world/", headers=HEADERS, timeout=30, follow_redirects=True)
    return {"status": r.status_code, "size": len(r.text)}

# 3. Google News link decode (base64url)
def t_gn_decode():
    gn = "https://news.google.com/rss/articles/CBMiowFBVV95cUxOYjBLM01uX0dLclpkY041M29fZHdIbm52RmhrcUxBdEhxZ1dzeVlHY25tRVJYcGRIejJ5ZjZZOU9RYnhsaFdGX0dWdG9aRGxpRDE5QzItNFNqWm9RVDRheEYxaFVuRm54aXh3cmRNLTlLSEV1ZVZ5azgzMDNtUGR4dVNjQUp2b0R1eldwSTVqeFJXMmJZQWl2MkxMNzFGYkxKM3ZR?oc=5"
    m = re.search(r"articles/(CBMi[^?]+)", gn)
    if not m:
        return {"error": "no CBMi token"}
    token = m.group(1)
    try:
        # base64url -> bytes, then extract readable URL
        raw = base64.urlsafe_b64decode(token + "==")
        urls = re.findall(rb"https?://[^\x00-\x20]+", raw)
        decoded = [u.decode("utf-8", "ignore") for u in urls[:3]]
        return {"decoded_urls": decoded, "token_len": len(token)}
    except Exception as e:
        return {"error": str(e)[:120]}

# 4. NYT article via jina with different format
def t_nyt_jina():
    r = httpx.get("https://r.jina.ai/https://www.nytimes.com/2026/08/02/world/middleeast/trump-iran-cancels-attack-deal.html",
                  headers={"User-Agent": "Mozilla/5.0"}, timeout=40, follow_redirects=True)
    return {"status": r.status_code, "size": len(r.text), "sample": r.text[:150]}

# 5. Reuters via jina
def t_rt_jina():
    r = httpx.get("https://r.jina.ai/https://www.reuters.com/world/",
                  headers={"User-Agent": "Mozilla/5.0"}, timeout=40, follow_redirects=True)
    return {"status": r.status_code, "size": len(r.text), "sample": r.text[:150]}

# 6. NYT API (public svc endpoint)
def t_nyt_api():
    r = httpx.get("https://www.nytimes.com/svc/news-v3/1.0/world.json?api-key=0",
                  headers=HEADERS, timeout=30, follow_redirects=True)
    return {"status": r.status_code, "size": len(r.text), "sample": r.text[:150]}

# 7. Reuters pf API (frontend JSON API)
def t_rt_api():
    import urllib.parse
    q = urllib.parse.quote(json.dumps({"section_path": "/world/", "limit": 5, "offset": 0, "ordered_by": "display_date:desc"}))
    url = f"https://www.reuters.com/pf/api/v3/content/fetch/articles-by-section-v1?query={q}&d=1&t=1"
    r = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
    return {"status": r.status_code, "size": len(r.text), "sample": r.text[:200]}

test("reuters_sitemap", t_sitemap)
test("reuters_world_page", t_rt_article)
test("gn_link_decode", t_gn_decode)
test("nyt_via_jina", t_nyt_jina)
test("reuters_via_jina", t_rt_jina)
test("nyt_svc_api", t_nyt_api)
test("reuters_pf_api", t_rt_api)

(OUT / "diagnostic.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(results, ensure_ascii=False, indent=1))
