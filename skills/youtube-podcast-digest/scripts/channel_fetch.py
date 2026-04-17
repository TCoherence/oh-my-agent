#!/usr/bin/env python3
"""
Fetch recent YouTube episodes for subscribed podcast channels.

Reads channels.yaml (sibling of this script's parent), resolves @handle →
channelId on first run, then pulls each channel's public RSS feed and filters
entries published within the freshness window.

Output: JSON array to stdout, one entry per fresh episode with fields
  name, group, channel_url, channel_id, handle,
  video_title, video_url, video_id, published_at, description_snippet

Usage:
  ./.venv/bin/python skills/youtube-podcast-digest/scripts/channel_fetch.py \
      [--since-days 7] [--group ai|vc|public_markets|china_tech|deep_dive|all]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import xml.etree.ElementTree as ET
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
    yaml = None  # type: ignore[assignment]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = aiohttp.ClientTimeout(total=30)
DEFAULT_SINCE_DAYS = 7
DESCRIPTION_SNIPPET_CHARS = 500
MAX_CONCURRENCY = 3
RETRY_BACKOFF_SECONDS = (2.0, 6.0)
MAX_FETCH_ATTEMPTS = 3

ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

CHANNELS_YAML = Path(__file__).resolve().parent.parent / "references" / "channels.yaml"


def _load_channels(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.exists() or yaml is None:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


async def _resolve_channel_id(
    session: aiohttp.ClientSession,
    handle: str,
) -> str | None:
    handle_clean = handle.lstrip("@")
    url = f"https://www.youtube.com/@{handle_clean}"
    try:
        async with session.get(url, headers={"User-Agent": UA}) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
    except Exception:
        return None
    # Prefer canonical URL (authoritative) → externalId → first channelId as last resort.
    # The handle page often contains `channelId` fields for unrelated recommended
    # channels in rails/widgets, so matching that first yields wrong IDs.
    canonical = re.search(
        r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{20,})"',
        html,
    )
    if canonical:
        return canonical.group(1)
    match = re.search(r'"externalId":"(UC[\w-]{20,})"', html)
    if match:
        return match.group(1)
    match = re.search(r'"channelId":"(UC[\w-]{20,})"', html)
    if match:
        return match.group(1)
    return None


def _write_back_channel_ids(yaml_path: Path, resolved: dict[str, str]) -> None:
    """Update `channel_id: null` lines to resolved IDs while preserving comments."""
    if not resolved:
        return
    original = yaml_path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    out: list[str] = []
    last_handle: str | None = None
    for line in lines:
        handle_match = re.match(r'\s*-?\s*handle:\s*"?([^"#\s]+)"?\s*(?:#.*)?$', line)
        if handle_match:
            last_handle = handle_match.group(1)
            out.append(line)
            continue
        cid_match = re.match(r'(\s*)channel_id:\s*null\s*$', line)
        if cid_match and last_handle and last_handle in resolved:
            indent = cid_match.group(1)
            out.append(f'{indent}channel_id: "{resolved[last_handle]}"\n')
            last_handle = None
            continue
        out.append(line)
    new_text = "".join(out)
    if new_text == original:
        return
    tmp = yaml_path.with_suffix(".yaml.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(yaml_path)


async def _fetch_feed(
    session: aiohttp.ClientSession,
    channel_id: str,
) -> list[dict[str, Any]]:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    body: str | None = None
    for attempt in range(MAX_FETCH_ATTEMPTS):
        try:
            async with session.get(url, headers={"User-Agent": UA}) as resp:
                if resp.status == 200:
                    body = await resp.text()
                    break
                if resp.status in (429, 500, 502, 503) and attempt < MAX_FETCH_ATTEMPTS - 1:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS[attempt])
                    continue
                return []
        except Exception:
            if attempt < MAX_FETCH_ATTEMPTS - 1:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS[attempt])
                continue
            return []
    if body is None:
        return []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    entries: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        link_el = entry.find("atom:link", ATOM_NS)
        href = link_el.get("href") if link_el is not None else ""
        if "/shorts/" in href:
            continue
        video_id_el = entry.find("yt:videoId", ATOM_NS)
        title_el = entry.find("atom:title", ATOM_NS)
        published_el = entry.find("atom:published", ATOM_NS)
        media_group = entry.find("media:group", ATOM_NS)
        description = ""
        if media_group is not None:
            desc_el = media_group.find("media:description", ATOM_NS)
            if desc_el is not None and desc_el.text:
                description = desc_el.text.strip()
        entries.append(
            {
                "video_id": video_id_el.text if video_id_el is not None else "",
                "video_title": (title_el.text or "").strip() if title_el is not None else "",
                "video_url": link_el.get("href") if link_el is not None else "",
                "published_at": (published_el.text or "").strip() if published_el is not None else "",
                "description_snippet": description[:DESCRIPTION_SNIPPET_CHARS],
            }
        )
    return entries


def _filter_fresh(entries: list[dict[str, Any]], since_days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    kept: list[dict[str, Any]] = []
    for entry in entries:
        raw = entry.get("published_at", "")
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt >= cutoff:
            kept.append(entry)
    return kept


async def _process_channel(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    group: str,
    channel: dict[str, Any],
    since_days: int,
    resolved: dict[str, str],
) -> list[dict[str, Any]]:
    name = channel.get("name", "")
    handle = channel.get("handle", "")
    channel_id = channel.get("channel_id")
    async with sem:
        if not channel_id:
            channel_id = await _resolve_channel_id(session, handle)
            if not channel_id:
                print(
                    f"[channel_fetch] handle resolution failed: {name} ({handle})",
                    file=sys.stderr,
                )
                return []
            resolved[handle] = channel_id
        entries = await _fetch_feed(session, channel_id)
    if not entries:
        print(
            f"[channel_fetch] rss empty or failed: {name} ({channel_id})",
            file=sys.stderr,
        )
        return []
    fresh = _filter_fresh(entries, since_days)
    channel_url = f"https://www.youtube.com/{handle}" if handle else ""
    for entry in fresh:
        entry["name"] = name
        entry["group"] = group
        entry["handle"] = handle
        entry["channel_id"] = channel_id
        entry["channel_url"] = channel_url
    return fresh


async def _main_async(since_days: int, group_filter: str) -> None:
    feeds = _load_channels(CHANNELS_YAML)
    if not feeds:
        print(
            f"[channel_fetch] channels.yaml empty or unreadable: {CHANNELS_YAML}",
            file=sys.stderr,
        )
        print("[]")
        return

    if group_filter != "all" and group_filter not in feeds:
        print(
            f"[channel_fetch] unknown group: {group_filter} (available: {', '.join(feeds)})",
            file=sys.stderr,
        )
        print("[]")
        return

    resolved: dict[str, str] = {}
    results: list[dict[str, Any]] = []

    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        tasks = []
        for grp, channels in feeds.items():
            if group_filter != "all" and grp != group_filter:
                continue
            for channel in channels:
                tasks.append(_process_channel(session, sem, grp, channel, since_days, resolved))
        chunks = await asyncio.gather(*tasks, return_exceptions=True)
        for chunk in chunks:
            if isinstance(chunk, Exception):
                print(f"[channel_fetch] unexpected error: {chunk}", file=sys.stderr)
                continue
            results.extend(chunk)

    if resolved:
        _write_back_channel_ids(CHANNELS_YAML, resolved)

    results.sort(key=lambda e: e.get("published_at", ""), reverse=True)
    json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch fresh YouTube podcast episodes")
    parser.add_argument(
        "--since-days",
        type=int,
        default=DEFAULT_SINCE_DAYS,
        help=f"Freshness window in days (default: {DEFAULT_SINCE_DAYS})",
    )
    parser.add_argument(
        "--group",
        default="all",
        help="Channel group filter: ai | vc | public_markets | china_tech | deep_dive | all",
    )
    args = parser.parse_args()
    asyncio.run(_main_async(args.since_days, args.group))


if __name__ == "__main__":
    main()
