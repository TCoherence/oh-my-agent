#!/usr/bin/env python3
"""Fetch top 5 daily news headlines from reliable RSS sources."""

import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

SOURCES = [
    ("BBC News", "https://feeds.bbci.co.uk/news/rss.xml"),
    ("Reuters", "https://feeds.reuters.com/reuters/topNews"),
    ("AP News", "https://rsshub.app/apnews/topics/apf-topnews"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; news-fetcher/1.0)"}


def fetch_headlines(url: str, count: int = 5) -> list[dict]:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = resp.read()
    root = ET.fromstring(data)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item")
    headlines = []
    for item in items[:count]:
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        pub_date = item.findtext("pubDate", "").strip()
        if title:
            headlines.append({"title": title, "link": link, "date": pub_date})
    return headlines


def main():
    source_name_arg = sys.argv[1] if len(sys.argv) > 1 else None

    sources_to_try = SOURCES
    if source_name_arg:
        sources_to_try = [(n, u) for n, u in SOURCES if source_name_arg.lower() in n.lower()]
        if not sources_to_try:
            sources_to_try = SOURCES

    for name, url in sources_to_try:
        try:
            headlines = fetch_headlines(url)
            if headlines:
                print(f"Top 5 Headlines — {name} ({datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')})\n")
                for i, h in enumerate(headlines, 1):
                    print(f"{i}. {h['title']}")
                    if h["link"]:
                        print(f"   {h['link']}")
                    if h["date"]:
                        print(f"   {h['date']}")
                    print()
                return
        except Exception as e:
            print(f"[{name}] failed: {e}", file=sys.stderr)
            continue

    print("Could not fetch news from any source. Check your internet connection.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
