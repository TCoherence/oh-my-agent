#!/usr/bin/env python3
"""Normalize a Bilibili video URL or identifier into structured JSON."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse


VIDEO_SEGMENT_RE = re.compile(r"(?i)^(bv[0-9a-z]{10}|av\d+)$")
TIME_RE = re.compile(
    r"^(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)?$"
)


@dataclass
class NormalizedVideo:
    input: str
    kind: str
    video_id: Optional[str]
    video_id_type: Optional[str]
    page: Optional[int]
    timestamp_seconds: Optional[int]
    canonical_url: Optional[str]
    note_slug: Optional[str]
    needs_resolution: bool


def normalize_video_id(raw: str) -> tuple[Optional[str], Optional[str]]:
    match = VIDEO_SEGMENT_RE.match(raw.strip())
    if not match:
        return None, None
    value = match.group(1)
    if value[:2].lower() == "av":
        return f"av{value[2:]}", "av"
    return f"BV{value[2:]}", "bv"


def parse_positive_int(raw: Optional[str]) -> Optional[int]:
    if raw is None or raw == "":
        return None
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if value > 0 else None


def parse_timestamp(raw: Optional[str]) -> Optional[int]:
    if raw is None or raw == "":
        return None
    if raw.isdigit():
        return int(raw)

    match = TIME_RE.match(raw.strip().lower())
    if not match:
        return None

    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    total = hours * 3600 + minutes * 60 + seconds
    return total if total > 0 else None


def build_note_slug(video_id: str, page: Optional[int]) -> str:
    parts = [video_id]
    if page:
        parts.append(f"p{page}")
    return "-".join(parts)


def build_canonical_url(video_id: str, page: Optional[int], timestamp: Optional[int]) -> str:
    query_parts = []
    if page:
        query_parts.append(f"p={page}")
    if timestamp:
        query_parts.append(f"t={timestamp}")
    query = ""
    if query_parts:
        query = "?" + "&".join(query_parts)
    return f"https://www.bilibili.com/video/{video_id}/{query}"


def normalize_from_url(raw: str) -> NormalizedVideo:
    parsed = urlparse(raw)
    host = parsed.netloc.lower()

    if host in {"b23.tv", "www.b23.tv", "bili2233.cn", "www.bili2233.cn"}:
        return NormalizedVideo(
            input=raw,
            kind="short_url",
            video_id=None,
            video_id_type=None,
            page=None,
            timestamp_seconds=None,
            canonical_url=None,
            note_slug=None,
            needs_resolution=True,
        )

    segments = [segment for segment in parsed.path.split("/") if segment]
    video_id = None
    if "video" in segments:
        index = segments.index("video")
        if index + 1 < len(segments):
            video_id, video_id_type = normalize_video_id(segments[index + 1])
        else:
            video_id_type = None
    else:
        video_id_type = None

    if not video_id:
        raise ValueError("URL does not contain a recognizable Bilibili video ID.")

    query = parse_qs(parsed.query)
    page = parse_positive_int(query.get("p", [None])[0])
    timestamp = parse_timestamp(query.get("t", [None])[0])

    return NormalizedVideo(
        input=raw,
        kind="video_url",
        video_id=video_id,
        video_id_type=video_id_type,
        page=page,
        timestamp_seconds=timestamp,
        canonical_url=build_canonical_url(video_id, page, timestamp),
        note_slug=build_note_slug(video_id, page),
        needs_resolution=False,
    )


def normalize_target(raw: str) -> NormalizedVideo:
    candidate = raw.strip()
    if not candidate:
        raise ValueError("Input is empty.")

    video_id, video_id_type = normalize_video_id(candidate)
    if video_id:
        return NormalizedVideo(
            input=raw,
            kind="bare_id",
            video_id=video_id,
            video_id_type=video_id_type,
            page=None,
            timestamp_seconds=None,
            canonical_url=build_canonical_url(video_id, None, None),
            note_slug=build_note_slug(video_id, None),
            needs_resolution=False,
        )

    parsed = urlparse(candidate)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Input is neither a bare BV/av ID nor a valid URL.")

    return normalize_from_url(candidate)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize a Bilibili video URL or bare BV/av ID."
    )
    parser.add_argument("target", help="Bilibili URL, BV ID, or av ID")
    args = parser.parse_args()

    try:
        normalized = normalize_target(args.target)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(asdict(normalized), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
