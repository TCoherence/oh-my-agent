#!/usr/bin/env python3
"""Fetch paper candidates from arXiv + HuggingFace Daily + Semantic Scholar.

三源 curated + raw fresh 召回；去重（按 arxiv_id > doi > s2_paper_id > normalized title）
+ 排序（watchlist keyword / tracked author / tracked affiliation / venue / HF trending）。
任何单源失败都不会 abort 流水线，空数组 + stderr warning + exit 0。

Usage:
  ./.venv/bin/python skills/paper-digest/scripts/paper_fetch.py [options]

Env vars:
  SEMANTIC_SCHOLAR_API_KEY  可选，提高 S2 速率限制

See skills/paper-digest/references/paper_watchlist.yaml 配置数据源范围与排序权重。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


USER_AGENT = "oh-my-agent/paper-digest (+https://github.com/anthropics/oh-my-agent)"
ARXIV_API = "http://export.arxiv.org/api/query"
ARXIV_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
ARXIV_PACING_SECONDS = 0.35

HF_DAILY_JSON = "https://huggingface.co/api/daily_papers"
HF_DAILY_HTML = "https://huggingface.co/papers"

S2_GRAPH_BASE = "https://api.semanticscholar.org/graph/v1"
S2_PAPER_BATCH = f"{S2_GRAPH_BASE}/paper/batch"
S2_PAPER_SEARCH = f"{S2_GRAPH_BASE}/paper/search/bulk"
S2_FIELDS = "title,authors,venue,externalIds,citationCount,publicationDate,tldr,abstract,year"

DEFAULT_WATCHLIST = SCRIPT_DIR.parent / "references" / "paper_watchlist.yaml"


# ---------------------------------------------------------------------------
# watchlist + stdlib HTTP helpers
# ---------------------------------------------------------------------------


def load_watchlist(path: Path) -> dict[str, Any]:
    if yaml is None:
        _warn("PyYAML not available; using empty watchlist defaults.")
        return _empty_watchlist()
    if not path.exists():
        _warn(f"watchlist not found at {path}; using empty defaults.")
        return _empty_watchlist()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"watchlist must be a YAML object: {path}")
    return _hydrate_watchlist(data)


def _empty_watchlist() -> dict[str, Any]:
    return _hydrate_watchlist({})


def _hydrate_watchlist(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    out.setdefault("arxiv_categories", [])
    out.setdefault("keywords", {})
    kw = out["keywords"] or {}
    kw.setdefault("must_hit_any", [])
    kw.setdefault("nice_to_have", [])
    kw.setdefault("exclude_regex", [])
    out["keywords"] = kw
    out.setdefault("tracked_authors", [])
    out.setdefault("tracked_affiliations", [])
    out.setdefault("venues_must_read", [])
    out.setdefault(
        "ranking_weights",
        {
            "hf_trending": 3.0,
            "watchlist_keyword_hit": 2.0,
            "tracked_author_hit": 2.5,
            "tracked_affiliation_hit": 1.5,
            "venue_hit": 1.5,
            "citation_velocity": 1.0,
        },
    )
    out.setdefault("limits", {})
    limits = out["limits"] or {}
    limits.setdefault("top_picks_max", 8)
    limits.setdefault("per_category_max", 4)
    limits.setdefault("similar_papers_max", 5)
    limits.setdefault("freshness_window_hours", 48)
    limits.setdefault("seen_pool_days", 14)
    out["limits"] = limits
    return out


def _warn(msg: str) -> None:
    print(f"[paper_fetch] WARNING: {msg}", file=sys.stderr)


def _error(source: str, err: Exception) -> None:
    print(
        json.dumps({"source": source, "error": f"{type(err).__name__}: {err}"}, ensure_ascii=False),
        file=sys.stderr,
    )


def _http_get(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> bytes:
    req_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def _http_get_json(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> Any:
    payload = _http_get(url, timeout=timeout, headers={**(headers or {}), "Accept": "application/json"})
    return json.loads(payload.decode("utf-8"))


def _http_post_json(
    url: str,
    body: Any,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> Any:
    data = json.dumps(body).encode("utf-8")
    req_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------


def fetch_arxiv(
    *,
    categories: list[str],
    window_hours: int,
    max_per_source: int,
    timeout: float,
) -> list[dict[str, Any]]:
    if not categories:
        return []
    query = "+OR+".join(f"cat:{urllib.parse.quote(c)}" for c in categories)
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": "0",
        "max_results": str(max_per_source),
    }
    encoded = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{ARXIV_API}?{encoded}"

    for attempt in range(3):
        try:
            body = _http_get(url, timeout=timeout)
            break
        except (urllib.error.URLError, TimeoutError) as err:
            if attempt == 2:
                _error("arxiv", err)
                return []
            time.sleep(2 ** attempt)
    else:
        return []

    time.sleep(ARXIV_PACING_SECONDS)

    try:
        root = ET.fromstring(body)
    except ET.ParseError as err:
        _error("arxiv", err)
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    items: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", ARXIV_ATOM_NS):
        item = _arxiv_entry_to_candidate(entry)
        if item is None:
            continue
        published = item.get("published_at")
        if published:
            try:
                pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=UTC)
                if pub_dt < cutoff:
                    continue
            except ValueError:
                pass
        items.append(item)
    return items


def _arxiv_entry_to_candidate(entry: ET.Element) -> dict[str, Any] | None:
    id_el = entry.find("atom:id", ARXIV_ATOM_NS)
    title_el = entry.find("atom:title", ARXIV_ATOM_NS)
    summary_el = entry.find("atom:summary", ARXIV_ATOM_NS)
    published_el = entry.find("atom:published", ARXIV_ATOM_NS)
    updated_el = entry.find("atom:updated", ARXIV_ATOM_NS)
    if id_el is None or id_el.text is None:
        return None
    arxiv_url = id_el.text.strip()
    arxiv_id = _arxiv_id_from_url(arxiv_url)
    title = (title_el.text or "").strip() if title_el is not None else ""
    abstract = (summary_el.text or "").strip() if summary_el is not None else ""
    authors: list[str] = []
    for author in entry.findall("atom:author", ARXIV_ATOM_NS):
        name_el = author.find("atom:name", ARXIV_ATOM_NS)
        if name_el is not None and name_el.text:
            authors.append(name_el.text.strip())
    categories: list[str] = []
    for cat in entry.findall("atom:category", ARXIV_ATOM_NS):
        term = cat.attrib.get("term")
        if term:
            categories.append(term)
    pdf_url = ""
    for link in entry.findall("atom:link", ARXIV_ATOM_NS):
        if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
            pdf_url = link.attrib.get("href", "")
            break
    published = (published_el.text or "").strip() if published_el is not None else ""
    updated = (updated_el.text or "").strip() if updated_el is not None else ""
    hf_url = f"https://huggingface.co/papers/{arxiv_id}" if arxiv_id else ""
    return {
        "source": "arxiv",
        "arxiv_id": arxiv_id,
        "doi": "",
        "s2_paper_id": "",
        "title": _collapse_whitespace(title),
        "authors": authors,
        "affiliations": [],
        "abstract": _collapse_whitespace(abstract),
        "arxiv_url": arxiv_url,
        "pdf_url": pdf_url,
        "hf_url": hf_url,
        "s2_url": "",
        "published_at": published,
        "updated_at": updated,
        "categories": categories,
        "venue": "",
        "citation_count": None,
        "citation_velocity": None,
        "hf_upvotes": None,
        "hf_trending_rank": None,
        "ranking_score": 0.0,
        "ranking_reasons": [],
        "seen_before": False,
    }


def _arxiv_id_from_url(url: str) -> str:
    m = re.search(r"arxiv\.org/abs/([0-9a-zA-Z\./]+?)(?:v\d+)?$", url)
    if m:
        return m.group(1)
    m = re.search(r"arxiv\.org/abs/([^/?#]+)", url)
    if m:
        return re.sub(r"v\d+$", "", m.group(1))
    return ""


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# HuggingFace Daily Papers
# ---------------------------------------------------------------------------


def fetch_hf_daily(*, timeout: float, max_per_source: int) -> list[dict[str, Any]]:
    items = _fetch_hf_via_json(timeout=timeout, max_per_source=max_per_source)
    if items:
        return items
    return _fetch_hf_via_html(timeout=timeout, max_per_source=max_per_source)


def _fetch_hf_via_json(*, timeout: float, max_per_source: int) -> list[dict[str, Any]]:
    try:
        data = _http_get_json(HF_DAILY_JSON, timeout=timeout)
    except Exception as err:  # broad: any HTTP / JSON failure goes to HTML fallback
        _error("hf:json", err)
        return []
    if not isinstance(data, list):
        _warn(f"HF daily JSON returned non-list payload ({type(data).__name__}); falling back to HTML.")
        return []
    items: list[dict[str, Any]] = []
    for idx, raw in enumerate(data[:max_per_source]):
        if not isinstance(raw, dict):
            continue
        paper = raw.get("paper") if isinstance(raw.get("paper"), dict) else raw
        arxiv_id = str(paper.get("id") or paper.get("arxiv_id") or "").strip()
        if not arxiv_id:
            continue
        title = str(paper.get("title") or "").strip()
        summary = str(paper.get("summary") or paper.get("abstract") or "").strip()
        authors_raw = paper.get("authors") or []
        authors: list[str] = []
        for a in authors_raw:
            if isinstance(a, dict):
                name = a.get("name") or a.get("fullname")
                if name:
                    authors.append(str(name).strip())
            elif isinstance(a, str):
                authors.append(a.strip())
        upvotes = paper.get("upvotes") or raw.get("upvotes") or 0
        items.append(
            {
                "source": "hf",
                "arxiv_id": arxiv_id,
                "doi": "",
                "s2_paper_id": "",
                "title": _collapse_whitespace(title),
                "authors": authors,
                "affiliations": [],
                "abstract": _collapse_whitespace(summary),
                "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "",
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else "",
                "hf_url": f"https://huggingface.co/papers/{arxiv_id}" if arxiv_id else "",
                "s2_url": "",
                "published_at": str(paper.get("publishedAt") or paper.get("published_at") or "").strip(),
                "updated_at": "",
                "categories": [],
                "venue": "",
                "citation_count": None,
                "citation_velocity": None,
                "hf_upvotes": int(upvotes) if isinstance(upvotes, (int, float)) else None,
                "hf_trending_rank": idx + 1,
                "ranking_score": 0.0,
                "ranking_reasons": [],
                "seen_before": False,
            }
        )
    return items


class _HFHtmlParser(HTMLParser):
    """Minimal parser that harvests arXiv IDs from /papers/{id} links on HF Daily page."""

    def __init__(self) -> None:
        super().__init__()
        self.arxiv_ids: list[str] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        m = re.match(r"/papers/([0-9]{4}\.[0-9]{3,6})(?:\?|$|/|#)", href)
        if m:
            arxiv_id = m.group(1)
            if arxiv_id not in self._seen:
                self._seen.add(arxiv_id)
                self.arxiv_ids.append(arxiv_id)


def _fetch_hf_via_html(*, timeout: float, max_per_source: int) -> list[dict[str, Any]]:
    try:
        body = _http_get(HF_DAILY_HTML, timeout=timeout).decode("utf-8", errors="replace")
    except Exception as err:
        _error("hf:html", err)
        return []
    parser = _HFHtmlParser()
    try:
        parser.feed(body)
    except Exception as err:
        _error("hf:html-parse", err)
        return []
    ids = parser.arxiv_ids[:max_per_source]
    items: list[dict[str, Any]] = []
    for idx, arxiv_id in enumerate(ids):
        items.append(
            {
                "source": "hf",
                "arxiv_id": arxiv_id,
                "doi": "",
                "s2_paper_id": "",
                "title": "",
                "authors": [],
                "affiliations": [],
                "abstract": "",
                "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}",
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                "hf_url": f"https://huggingface.co/papers/{arxiv_id}",
                "s2_url": "",
                "published_at": "",
                "updated_at": "",
                "categories": [],
                "venue": "",
                "citation_count": None,
                "citation_velocity": None,
                "hf_upvotes": None,
                "hf_trending_rank": idx + 1,
                "ranking_score": 0.0,
                "ranking_reasons": [],
                "seen_before": False,
            }
        )
    return items


# ---------------------------------------------------------------------------
# Semantic Scholar
# ---------------------------------------------------------------------------


def enrich_with_s2(
    items: list[dict[str, Any]],
    *,
    timeout: float,
    api_key: str | None,
    total_budget_seconds: float = 90.0,
) -> dict[str, dict[str, Any]]:
    """Enrich existing arXiv/HF items with Semantic Scholar metadata (venue, citations, tldr).

    Returns a map keyed by arxiv_id to the enrichment payload; caller applies it in-place.
    """
    arxiv_ids = [it.get("arxiv_id") for it in items if it.get("arxiv_id")]
    arxiv_ids = sorted({aid for aid in arxiv_ids if aid})
    if not arxiv_ids:
        return {}

    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    start = time.monotonic()
    out: dict[str, dict[str, Any]] = {}
    # S2 paper/batch accepts up to 500 ids per request.
    batch_size = 250
    for i in range(0, len(arxiv_ids), batch_size):
        if time.monotonic() - start > total_budget_seconds:
            _warn(f"Semantic Scholar budget exhausted after {i} ids; returning partial enrichment.")
            break
        batch = arxiv_ids[i : i + batch_size]
        body = {"ids": [f"arXiv:{aid}" for aid in batch]}
        url = f"{S2_PAPER_BATCH}?fields={S2_FIELDS}"
        for attempt in range(3):
            try:
                resp = _http_post_json(url, body, timeout=timeout, headers=headers)
                break
            except urllib.error.HTTPError as err:
                if err.code == 429:
                    time.sleep(2 * (attempt + 1) * (attempt + 1))
                    continue
                _error("s2:batch", err)
                resp = None
                break
            except (urllib.error.URLError, TimeoutError) as err:
                if attempt == 2:
                    _error("s2:batch", err)
                    resp = None
                    break
                time.sleep(2 * (attempt + 1))
            except json.JSONDecodeError as err:
                _error("s2:batch-json", err)
                resp = None
                break
        else:
            resp = None
        if not isinstance(resp, list):
            continue
        for arxiv_id, payload in zip(batch, resp):
            if not isinstance(payload, dict):
                continue
            out[arxiv_id] = payload
    return out


def _apply_s2_enrichment(item: dict[str, Any], payload: dict[str, Any]) -> None:
    s2_id = str(payload.get("paperId") or "").strip()
    if s2_id:
        item["s2_paper_id"] = s2_id
        item["s2_url"] = f"https://www.semanticscholar.org/paper/{s2_id}"
    external = payload.get("externalIds") or {}
    doi = external.get("DOI") if isinstance(external, dict) else None
    if doi and not item.get("doi"):
        item["doi"] = str(doi)
    venue = payload.get("venue")
    if venue:
        item["venue"] = str(venue)
    citation_count = payload.get("citationCount")
    if isinstance(citation_count, (int, float)):
        item["citation_count"] = int(citation_count)
        pub_date = payload.get("publicationDate")
        if isinstance(pub_date, str) and pub_date:
            try:
                pub_dt = datetime.fromisoformat(pub_date)
                weeks = max(((datetime.now(timezone.utc).date() - pub_dt.date()).days) / 7.0, 1.0)
                item["citation_velocity"] = round(citation_count / weeks, 3)
            except ValueError:
                pass
    authors_raw = payload.get("authors") or []
    if authors_raw and not item.get("authors"):
        names = [str(a.get("name")).strip() for a in authors_raw if isinstance(a, dict) and a.get("name")]
        if names:
            item["authors"] = names
    tldr = payload.get("tldr")
    if isinstance(tldr, dict) and tldr.get("text"):
        item["s2_tldr"] = str(tldr["text"]).strip()
    if not item.get("abstract") and payload.get("abstract"):
        item["abstract"] = _collapse_whitespace(str(payload["abstract"]))


# ---------------------------------------------------------------------------
# merge + rank
# ---------------------------------------------------------------------------


def _normalize_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def _candidate_key(item: dict[str, Any]) -> str:
    arxiv_id = str(item.get("arxiv_id") or "").strip()
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    doi = str(item.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    s2 = str(item.get("s2_paper_id") or "").strip()
    if s2:
        return f"s2:{s2}"
    title = _normalize_title(str(item.get("title") or ""))
    return f"title:{title[:120]}" if title else f"anon:{id(item)}"


def merge_candidates(*bundles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for bundle in bundles:
        for item in bundle:
            key = _candidate_key(item)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = dict(item)
                continue
            # merge: keep non-empty fields, and remember multi-source origin
            for field, value in item.items():
                if value in (None, "", []) or field == "source":
                    continue
                if existing.get(field) in (None, "", []):
                    existing[field] = value
            sources = set()
            for src in (existing.get("source"), item.get("source")):
                if src:
                    sources.update(src.split("+") if isinstance(src, str) else [src])
            existing["source"] = "+".join(sorted(sources))
            if item.get("hf_trending_rank") is not None:
                existing["hf_trending_rank"] = item["hf_trending_rank"]
            if item.get("hf_upvotes") is not None:
                existing["hf_upvotes"] = item["hf_upvotes"]
    return list(by_key.values())


def apply_ranking(items: list[dict[str, Any]], watchlist: dict[str, Any]) -> list[dict[str, Any]]:
    weights = watchlist.get("ranking_weights") or {}
    kw = watchlist.get("keywords") or {}
    must_hit = [k.lower() for k in kw.get("must_hit_any", []) if k]
    nice = [k.lower() for k in kw.get("nice_to_have", []) if k]
    exclude = [re.compile(p, re.IGNORECASE) for p in kw.get("exclude_regex", []) if p]
    authors = [a.lower() for a in watchlist.get("tracked_authors", []) if a]
    affiliations = [a.lower() for a in watchlist.get("tracked_affiliations", []) if a]
    venues = [v.lower() for v in watchlist.get("venues_must_read", []) if v]

    for item in items:
        title = str(item.get("title") or "")
        abstract = str(item.get("abstract") or "")
        haystack = f"{title}\n{abstract}".lower()
        reasons: list[str] = []
        score = 0.0

        if exclude and any(p.search(title) for p in exclude):
            item["ranking_score"] = -9999.0
            item["ranking_reasons"] = ["excluded_by_regex"]
            continue

        if "hf" in (item.get("source") or ""):
            rank = item.get("hf_trending_rank") or 99
            bonus = max(0.0, float(weights.get("hf_trending", 3.0)) * (1.0 - min(rank, 30) / 30))
            if bonus > 0:
                score += bonus
                reasons.append(f"hf_trending_rank:{rank}")

        matched_keywords = [k for k in must_hit if k and k in haystack]
        if matched_keywords:
            score += float(weights.get("watchlist_keyword_hit", 2.0)) * min(len(matched_keywords), 3)
            reasons.append("watchlist_keyword:" + ",".join(matched_keywords[:3]))

        matched_nice = [k for k in nice if k and k in haystack]
        if matched_nice:
            score += 0.5 * min(len(matched_nice), 3)
            reasons.append("nice_to_have:" + ",".join(matched_nice[:3]))

        lower_authors = [str(a).lower() for a in item.get("authors") or []]
        matched_authors = [a for a in authors if any(a == la for la in lower_authors)]
        if matched_authors:
            score += float(weights.get("tracked_author_hit", 2.5)) * min(len(matched_authors), 2)
            reasons.append("tracked_author:" + ",".join(matched_authors[:2]))

        lower_affs = [str(a).lower() for a in item.get("affiliations") or []]
        matched_affs = [a for a in affiliations if any(a in la for la in lower_affs)]
        if matched_affs:
            score += float(weights.get("tracked_affiliation_hit", 1.5)) * min(len(matched_affs), 2)
            reasons.append("tracked_affiliation:" + ",".join(matched_affs[:2]))

        venue = str(item.get("venue") or "").lower()
        if venue and any(v in venue for v in venues):
            score += float(weights.get("venue_hit", 1.5))
            reasons.append(f"venue:{item.get('venue')}")

        velocity = item.get("citation_velocity")
        if isinstance(velocity, (int, float)) and velocity > 0:
            score += float(weights.get("citation_velocity", 1.0)) * min(float(velocity), 5.0)
            reasons.append(f"citation_velocity:{velocity}")

        item["ranking_score"] = round(score, 3)
        item["ranking_reasons"] = reasons

    items.sort(key=lambda it: float(it.get("ranking_score") or 0.0), reverse=True)
    return items


# ---------------------------------------------------------------------------
# seen-pool integration
# ---------------------------------------------------------------------------


def mark_seen_before(items: list[dict[str, Any]], state_path: Path, *, include_seen: bool) -> list[dict[str, Any]]:
    if not state_path.exists():
        return items
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as err:
        _error("seen-pool", err)
        return items
    seen = state.get("seen") or {}
    filtered: list[dict[str, Any]] = []
    for item in items:
        key = _candidate_key(item)
        if key in seen:
            item["seen_before"] = True
            if not include_seen:
                continue
        filtered.append(item)
    return filtered


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch paper candidates from arXiv + HF Daily + Semantic Scholar",
    )
    parser.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST))
    parser.add_argument(
        "--source",
        choices=("arxiv", "hf", "s2", "all"),
        default="all",
        help="restrict to a single source (s2 is enrichment-only; needs at least one other source to have arxiv ids)",
    )
    parser.add_argument("--window-hours", type=int, default=None, help="override watchlist.limits.freshness_window_hours")
    parser.add_argument("--max-per-source", type=int, default=100)
    parser.add_argument(
        "--seen-state",
        default=None,
        help="path to seen-pool JSON state; default ~/.oh-my-agent/reports/paper-digest/state/paper_seen_pool.json",
    )
    parser.add_argument("--include-seen", action="store_true", help="do not drop papers seen in the last 14 days")
    parser.add_argument("--output", choices=("json", "pretty"), default="json")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--s2-budget-seconds", type=float, default=90.0)
    return parser.parse_args()


def _default_seen_state() -> Path:
    return Path.home() / ".oh-my-agent" / "reports" / "paper-digest" / "state" / "paper_seen_pool.json"


def main() -> int:
    args = _parse_args()
    watchlist = load_watchlist(Path(args.watchlist))
    limits = watchlist.get("limits") or {}
    window_hours = args.window_hours or int(limits.get("freshness_window_hours", 48))

    sources = {args.source} if args.source != "all" else {"arxiv", "hf", "s2"}

    arxiv_items: list[dict[str, Any]] = []
    hf_items: list[dict[str, Any]] = []

    if "arxiv" in sources:
        arxiv_items = fetch_arxiv(
            categories=watchlist.get("arxiv_categories") or [],
            window_hours=window_hours,
            max_per_source=args.max_per_source,
            timeout=args.timeout_seconds,
        )
    if "hf" in sources:
        hf_items = fetch_hf_daily(timeout=args.timeout_seconds, max_per_source=args.max_per_source)

    merged = merge_candidates(arxiv_items, hf_items)

    if "s2" in sources and merged:
        s2_map = enrich_with_s2(
            merged,
            timeout=args.timeout_seconds,
            api_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or None,
            total_budget_seconds=args.s2_budget_seconds,
        )
        for item in merged:
            aid = item.get("arxiv_id")
            if aid and aid in s2_map:
                _apply_s2_enrichment(item, s2_map[aid])
                existing_src = item.get("source") or ""
                if "s2" not in existing_src:
                    item["source"] = "+".join(sorted(set((existing_src.split("+") if existing_src else []) + ["s2"])))

    ranked = apply_ranking(merged, watchlist)

    seen_path = Path(args.seen_state).expanduser() if args.seen_state else _default_seen_state()
    ranked = mark_seen_before(ranked, seen_path, include_seen=args.include_seen)

    ranked = [it for it in ranked if float(it.get("ranking_score") or 0.0) > -9000.0]

    if args.output == "pretty":
        print(json.dumps(ranked, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(ranked, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
