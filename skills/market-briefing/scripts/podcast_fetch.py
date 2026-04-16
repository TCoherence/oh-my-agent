#!/usr/bin/env python3
"""
Fetch latest episodes from subscribed podcasts on xiaoyuzhoufm.com.

Reads the subscription list from references/podcast_feeds.yaml.
Outputs a JSON array to stdout. Each entry contains:
  name, url, episode_title, episode_url, episode_date, shownotes

Only episodes published within the last 48 hours are included.

Usage:
  ./.venv/bin/python skills/market-briefing/scripts/podcast_fetch.py [--domain ai|finance|all]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import aiohttp
except ImportError:
    print(
        json.dumps({"error": "aiohttp is required: pip install aiohttp"}),
        file=sys.stderr,
    )
    sys.exit(1)

try:
    import yaml
except ImportError:
    # Fallback: minimal YAML-like parser not needed if PyYAML is installed.
    yaml = None  # type: ignore[assignment]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = aiohttp.ClientTimeout(total=30)
FRESHNESS_HOURS = 48

FEEDS_YAML = Path(__file__).resolve().parent.parent / "references" / "podcast_feeds.yaml"


def _load_feeds(path: Path | None = None) -> dict[str, list[dict[str, str]]]:
    """Load podcast_feeds.yaml → {group: [{name, url}, ...]}."""
    p = path or FEEDS_YAML
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    # Minimal fallback parser when PyYAML is not available.
    # Handles the simple structure: top-level keys → list of {name, url} dicts.
    result: dict[str, list[dict[str, str]]] = {}
    current_group: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" ") and stripped.endswith(":"):
            current_group = stripped[:-1]
            result[current_group] = []
        elif current_group is not None and stripped.startswith("- name:"):
            result[current_group].append({"name": stripped.split(":", 1)[1].strip()})
        elif current_group is not None and stripped.startswith("url:") and result[current_group]:
            result[current_group][-1]["url"] = stripped.split(" ", 1)[1].strip()
    return result


def load_subscriptions(
    domain: str = "ai",
    feeds_path: Path | None = None,
) -> list[tuple[str, str]]:
    """Return (name, url) tuples for the given domain + general group."""
    feeds = _load_feeds(feeds_path)
    entries: list[dict[str, str]] = []
    if domain == "all":
        for group in feeds.values():
            entries.extend(group)
    else:
        entries.extend(feeds.get(domain, []))
        entries.extend(feeds.get("general", []))
    # Deduplicate by URL, preserving order.
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for entry in entries:
        url = entry.get("url", "")
        name = entry.get("name", "")
        if url and url not in seen:
            seen.add(url)
            result.append((name, url))
    return result


async def _fetch_one(
    session: aiohttp.ClientSession,
    name: str,
    url: str,
) -> dict[str, Any] | None:
    """Fetch a single podcast page -> extract latest episode from __NEXT_DATA__."""
    try:
        async with session.get(url, headers={"User-Agent": UA}) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()

        match = re.search(r"__NEXT_DATA__[^>]*>(.*?)</script>", html)
        if not match:
            return None

        next_data = json.loads(match.group(1))
        episodes = (
            next_data.get("props", {})
            .get("pageProps", {})
            .get("podcast", {})
            .get("episodes", [])
        )
        if not episodes:
            return None

        ep = episodes[0]
        title = (ep.get("title") or "").strip()
        eid = ep.get("eid") or ""
        pub_date_str = ep.get("pubDate") or ""
        shownotes = ep.get("shownotes") or ep.get("description") or ""
        # Strip HTML tags from shownotes
        shownotes = re.sub(r"<[^>]+>", "", shownotes).strip()

        if not title or not eid:
            return None

        # Freshness filter
        if pub_date_str:
            try:
                pub_dt = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
                if pub_dt < datetime.now(timezone.utc) - timedelta(hours=FRESHNESS_HOURS):
                    return None
            except (ValueError, TypeError):
                pass

        return {
            "name": name,
            "url": url,
            "episode_title": title,
            "episode_url": f"https://www.xiaoyuzhoufm.com/episode/{eid}",
            "episode_date": pub_date_str,
            "shownotes": shownotes[:500],
        }
    except Exception:
        return None


async def fetch_podcasts(
    session: aiohttp.ClientSession,
    subscriptions: list[tuple[str, str]] | None = None,
    domain: str = "ai",
    feeds_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Fetch all subscribed podcasts in parallel, return fresh episodes."""
    subs = subscriptions or load_subscriptions(domain=domain, feeds_path=feeds_path)
    tasks = [_fetch_one(session, name, url) for name, url in subs]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if r and not isinstance(r, Exception)]


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Fetch latest podcast episodes")
    parser.add_argument(
        "--domain",
        default="ai",
        choices=["ai", "finance", "all"],
        help="Which domain group to fetch (default: ai = ai + general feeds)",
    )
    parser.add_argument("--feeds", type=Path, default=None, help="Path to podcast_feeds.yaml")
    args = parser.parse_args()

    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        items = await fetch_podcasts(session, domain=args.domain, feeds_path=args.feeds)
    json.dump(items, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    asyncio.run(_main())
