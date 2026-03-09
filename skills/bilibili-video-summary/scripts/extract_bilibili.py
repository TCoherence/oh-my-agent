#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )


def _emit(payload: dict, *, exit_code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


def _emit_auth_required() -> None:
    _emit(
        {
            "status": "auth_required",
            "provider": "bilibili",
            "reason": "login_required",
        },
        exit_code=32,
    )


def _ensure_yt_dlp() -> tuple[list[str], bool, str]:
    binary = shutil.which("yt-dlp")
    if binary:
        return [binary], False, "system"
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"], False, "python_module"
    except ImportError:
        install = _run([sys.executable, "-m", "pip", "install", "yt-dlp"])
        if install.returncode != 0:
            _emit(
                {
                    "status": "error",
                    "reason": "yt_dlp_install_failed",
                    "details": (install.stderr or install.stdout).strip()[:2000],
                },
                exit_code=1,
            )
        return [sys.executable, "-m", "yt_dlp"], True, "python_module"


def _looks_like_auth_required(details: str) -> bool:
    normalized = details.lower()
    markers = (
        "login",
        "log in",
        "cookie",
        "cookies",
        "member",
        "vip",
        "permission",
        "forbidden",
        "precondition failed",
        "http error 412",
        "http error 403",
        "please login",
        "请先登录",
        "需要登录",
        "大会员",
        "会员",
        "风控",
    )
    return any(marker in normalized for marker in markers)


def _parse_lang_preferences(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _normalize_lang(lang: str) -> str:
    normalized = lang.strip().lower().replace("_", "-")
    if normalized.startswith("ai-"):
        normalized = normalized[3:]
    return normalized


def _lang_rank(lang: str, preferences: list[str]) -> int:
    normalized = _normalize_lang(lang)
    for idx, pref in enumerate(preferences):
        pref_norm = _normalize_lang(pref).rstrip("*")
        if normalized == pref_norm:
            return idx
        if pref.endswith("*") and normalized.startswith(pref_norm):
            return idx
    return len(preferences) + 100


def _find_info_json(root: Path) -> Path | None:
    matches = sorted(root.rglob("*.info.json"))
    return matches[0] if matches else None


def _extract_caption_lang(path: Path) -> str:
    parts = path.name.split(".")
    if len(parts) >= 3:
        return parts[-2]
    return "unknown"


def _clean_text(text: str) -> str:
    text = text.replace("\ufeff", "").replace("\u200b", "")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _seconds_to_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _parse_timestamp(raw: str) -> float:
    value = raw.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = "0"
        minutes, seconds = parts
    else:
        raise ValueError(f"Unsupported timestamp: {raw}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _parse_srt(path: Path) -> list[dict]:
    segments: list[dict] = []
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="replace").strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        if re.fullmatch(r"\d+", lines[0]):
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            continue
        start_raw, end_raw = [part.strip() for part in lines[0].split("-->", 1)]
        text = _clean_text(" ".join(lines[1:]))
        if not text:
            continue
        start_seconds = _parse_timestamp(start_raw)
        end_seconds = _parse_timestamp(end_raw)
        segments.append(
            {
                "start": _seconds_to_timestamp(start_seconds),
                "end": _seconds_to_timestamp(end_seconds),
                "text": text,
            }
        )
    return segments


def _parse_vtt(path: Path) -> list[dict]:
    segments: list[dict] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        if "-->" not in line:
            idx += 1
            continue
        start_raw, end_raw = [part.strip().split(" ", 1)[0] for part in line.split("-->", 1)]
        idx += 1
        text_lines: list[str] = []
        while idx < len(lines) and lines[idx].strip():
            text_lines.append(lines[idx].strip())
            idx += 1
        text = _clean_text(" ".join(text_lines))
        if text:
            start_seconds = _parse_timestamp(start_raw)
            end_seconds = _parse_timestamp(end_raw)
            segments.append(
                {
                    "start": _seconds_to_timestamp(start_seconds),
                    "end": _seconds_to_timestamp(end_seconds),
                    "text": text,
                }
            )
        idx += 1
    return segments


def _parse_bilibili_json(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    body = payload.get("body")
    if not isinstance(body, list):
        return []
    segments: list[dict] = []
    for item in body:
        if not isinstance(item, dict):
            continue
        text = _clean_text(str(item.get("content") or ""))
        if not text:
            continue
        start_seconds = float(item.get("from") or 0)
        end_seconds = float(item.get("to") or start_seconds)
        segments.append(
            {
                "start": _seconds_to_timestamp(start_seconds),
                "end": _seconds_to_timestamp(end_seconds),
                "text": text,
            }
        )
    return segments


def _parse_caption_file(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".srt":
        return _parse_srt(path)
    if suffix == ".vtt":
        return _parse_vtt(path)
    if suffix == ".json":
        return _parse_bilibili_json(path)
    return []


def _build_transcript(segments: list[dict], max_chars: int) -> tuple[str, list[dict]]:
    transcript_parts: list[str] = []
    kept_segments: list[dict] = []
    total = 0
    unlimited = max_chars <= 0
    for segment in segments:
        text = segment["text"]
        if not text:
            continue
        extra = len(text) + (1 if transcript_parts else 0)
        if not unlimited and transcript_parts and total + extra > max_chars:
            break
        transcript_parts.append(text)
        kept_segments.append(segment)
        total += extra
    return "\n".join(transcript_parts), kept_segments


def _collect_caption_candidates(video_dir: Path) -> list[Path]:
    candidates = [
        *video_dir.glob("*.srt"),
        *video_dir.glob("*.vtt"),
        *video_dir.glob("*.json"),
    ]
    filtered: list[Path] = []
    for path in candidates:
        name = path.name.lower()
        if not path.is_file():
            continue
        if name.endswith(".info.json"):
            continue
        if "danmaku" in name or "live_chat" in name:
            continue
        filtered.append(path)
    return sorted(set(filtered))


def _select_caption_file(candidates: list[Path], preferences: list[str]) -> Path | None:
    if not candidates:
        return None
    ranked = sorted(
        candidates,
        key=lambda path: (_lang_rank(_extract_caption_lang(path), preferences), path.name),
    )
    return ranked[0]


def _metadata_payload(info: dict, url: str, *, yt_dlp_was_installed: bool, yt_dlp_source: str) -> dict:
    return {
        "status": "metadata_only",
        "url": url,
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "channel": info.get("channel"),
        "duration_seconds": info.get("duration"),
        "description": info.get("description"),
        "evidence": "metadata_only",
        "yt_dlp_auto_installed": yt_dlp_was_installed,
        "yt_dlp_source": yt_dlp_source,
    }


def _emit_metadata_only(
    info: dict,
    url: str,
    *,
    yt_dlp_was_installed: bool,
    yt_dlp_source: str,
    reason: str,
) -> None:
    payload = _metadata_payload(
        info,
        url,
        yt_dlp_was_installed=yt_dlp_was_installed,
        yt_dlp_source=yt_dlp_source,
    )
    payload["metadata_reason"] = reason
    _emit(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Bilibili subtitles and metadata with yt-dlp.")
    parser.add_argument("--url", required=True, help="Bilibili or b23.tv URL")
    parser.add_argument(
        "--sub-langs",
        default="all,-danmaku",
        help="Comma-separated yt-dlp subtitle request list",
    )
    parser.add_argument(
        "--prefer-langs",
        default="zh,zh-hans,zh-cn,en,en-us",
        help="Comma-separated preferred subtitle languages for selection",
    )
    parser.add_argument(
        "--max-transcript-chars",
        type=int,
        default=200000,
        help="Maximum transcript characters to return; use 0 for no limit",
    )
    parser.add_argument(
        "--output-dir",
        help="Optional directory to keep yt-dlp output files instead of using a temp dir",
    )
    parser.add_argument(
        "--cookies-path",
        help="Optional cookies.txt path for restricted videos",
    )
    args = parser.parse_args()

    yt_dlp_cmd, yt_dlp_was_installed, yt_dlp_source = _ensure_yt_dlp()
    lang_preferences = _parse_lang_preferences(args.prefer_langs)

    if args.output_dir:
        output_root = Path(args.output_dir).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        temp_ctx = None
    else:
        temp_ctx = tempfile.TemporaryDirectory(prefix="oma-bilibili-subs-")
        output_root = Path(temp_ctx.name)

    try:
        output_template = output_root / "%(id)s.%(ext)s"
        cmd = [
            *yt_dlp_cmd,
            "--no-playlist",
            "--skip-download",
            "--ignore-errors",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            args.sub_langs,
            "--write-info-json",
            "-o",
            str(output_template),
            args.url,
        ]
        if args.cookies_path:
            cmd.extend(["--cookies", str(Path(args.cookies_path).expanduser().resolve())])

        result = _run(cmd)
        combined_output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        if result.returncode != 0:
            if _looks_like_auth_required(combined_output):
                _emit_auth_required()
            _emit(
                {
                    "status": "error",
                    "reason": "yt_dlp_failed",
                    "details": combined_output[:4000],
                    "yt_dlp_auto_installed": yt_dlp_was_installed,
                    "yt_dlp_source": yt_dlp_source,
                },
                exit_code=1,
            )

        info_json_path = _find_info_json(output_root)
        if info_json_path is None:
            if _looks_like_auth_required(combined_output):
                _emit_auth_required()
            _emit(
                {
                    "status": "error",
                    "reason": "info_json_missing",
                    "yt_dlp_auto_installed": yt_dlp_was_installed,
                    "yt_dlp_source": yt_dlp_source,
                },
                exit_code=1,
            )

        info = json.loads(info_json_path.read_text(encoding="utf-8"))
        video_dir = info_json_path.parent
        caption_candidates = _collect_caption_candidates(video_dir)
        chosen_caption = _select_caption_file(caption_candidates, lang_preferences)

        if chosen_caption is None:
            if not args.cookies_path:
                _emit_auth_required()
            _emit_metadata_only(
                info,
                args.url,
                yt_dlp_was_installed=yt_dlp_was_installed,
                yt_dlp_source=yt_dlp_source,
                reason="no_subtitles_after_authenticated_fetch",
            )

        segments = _parse_caption_file(chosen_caption)
        transcript, kept_segments = _build_transcript(segments, args.max_transcript_chars)

        if not transcript:
            if not args.cookies_path:
                _emit_auth_required()
            _emit_metadata_only(
                info,
                args.url,
                yt_dlp_was_installed=yt_dlp_was_installed,
                yt_dlp_source=yt_dlp_source,
                reason="empty_subtitles_after_authenticated_fetch",
            )

        _emit(
            {
                "status": "transcript_backed",
                "url": args.url,
                "title": info.get("title"),
                "uploader": info.get("uploader") or info.get("channel"),
                "channel": info.get("channel"),
                "duration_seconds": info.get("duration"),
                "language": _extract_caption_lang(chosen_caption),
                "subtitle_file": chosen_caption.name if args.output_dir else None,
                "evidence": "subtitles",
                "transcript": transcript,
                "segments": kept_segments,
                "description": info.get("description"),
                "yt_dlp_auto_installed": yt_dlp_was_installed,
                "yt_dlp_source": yt_dlp_source,
            }
        )
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()


if __name__ == "__main__":
    main()
