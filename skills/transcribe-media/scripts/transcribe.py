#!/usr/bin/env python3
"""Wrapper around the stt project's cli.py for the oh-my-agent skill ecosystem.

Always emits a JSON envelope on stdout (oh-my-agent convention). Internally
calls stt's CLI with `--format json`, then re-formats locally if the caller
asked for srt/text — the wrapper never lets stt's raw non-JSON stdout reach
the caller's stdout.

Locates stt via $STT_HOME (default ~/repos/stt). Hard-caps the subprocess
runtime under SKILL.md's `timeout_seconds: 1800` budget; on timeout, kills
the entire process group so ffmpeg children don't outlive the parent.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any


SUBPROCESS_TIMEOUT_SECONDS = 1700  # SKILL.md frontmatter cap is 1800; leave ~100s headroom
GRACEFUL_TERM_GRACE_SECONDS = 5


def _emit(payload: dict, *, exit_code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


def _resolve_stt_home() -> Path:
    raw = os.environ.get("STT_HOME") or "~/repos/stt"
    home = Path(raw).expanduser().resolve()
    cli_py = home / "cli.py"
    venv_python = home / "venv" / "bin" / "python"
    if not cli_py.is_file() or not venv_python.is_file():
        _emit(
            {
                "status": "error",
                "kind": "stt_not_found",
                "message": (
                    f"stt project not found at {home} (looked for cli.py and venv/bin/python). "
                    "Set $STT_HOME or clone https://github.com/TCoherence/stt.git into ~/repos/stt "
                    "and create its venv per the project README."
                ),
            },
            exit_code=2,
        )
    return home


# stt CLI emits its error JSON as a single line on stderr (with --verbose, progress
# lines may precede it; the wrapper does not pass --verbose so this is a defensive
# scan). Walk lines from the end and take the last parseable JSON object.
def _extract_error_envelope(stderr_text: str, returncode: int) -> dict:
    text = stderr_text.strip()
    if text:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {
        "status": "error",
        "kind": "internal",
        "message": f"stt CLI exited {returncode}",
        "details": text[:1500] if text else "no stderr output",
    }


def _ts_srt(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_srt(segments: list[dict]) -> str:
    parts = [
        f"{i}\n{_ts_srt(seg['start'])} --> {_ts_srt(seg['end'])}\n{seg['text']}\n"
        for i, seg in enumerate(segments, 1)
    ]
    return "\n".join(parts)


def _format_text(segments: list[dict]) -> str:
    return "\n".join(seg["text"] for seg in segments)


def _kill_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, OSError):
        pass


def _run_stt(cmd: list[str]) -> tuple[int, str, str]:
    """Run stt CLI in its own process group with a timeout; kill the whole group on timeout."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=SUBPROCESS_TIMEOUT_SECONDS)
        return proc.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid, signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=GRACEFUL_TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _kill_group(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
        _emit(
            {
                "status": "error",
                "kind": "timeout",
                "message": (
                    f"stt CLI exceeded {SUBPROCESS_TIMEOUT_SECONDS}s budget; "
                    "process group terminated"
                ),
                "details": (stderr or "")[:1500],
            },
            exit_code=3,
        )


class _JsonErrorParser(argparse.ArgumentParser):
    """Argparse that emits the oh-my-agent JSON envelope on parse failures."""

    def error(self, message: str) -> None:  # type: ignore[override]
        _emit(
            {
                "status": "error",
                "kind": "invalid_input",
                "message": f"argument error: {message}",
            },
            exit_code=2,
        )


def main() -> None:
    parser = _JsonErrorParser(
        prog="transcribe-media",
        description=(
            "Transcribe audio/video via the stt CLI. "
            "Always emits a JSON envelope on stdout regardless of --format. "
            "(--help is the only exception; argparse handles it directly.)"
        ),
    )
    parser.add_argument("--input", required=True, help="Path to media file (~ expanded).")
    parser.add_argument(
        "--format", default="json", choices=["json", "srt", "text"],
        help="Body shape inside the JSON envelope (default: json with structured segments).",
    )
    parser.add_argument("--language", default="auto", help="ISO 639-1 code or 'auto' (default).")
    parser.add_argument(
        "--engine", choices=["faster-whisper", "mlx"], default=None,
        help="Backend override; default reads stt's set.ini.",
    )
    parser.add_argument("--model", default="large-v3-turbo", help="Whisper model name.")
    args = parser.parse_args()

    home = _resolve_stt_home()

    # Always invoke stt with --format json so we can re-shape the body locally
    # without ever letting raw SRT/text reach the caller's stdout.
    cmd = [
        str(home / "venv" / "bin" / "python"),
        str(home / "cli.py"),
        os.path.expanduser(args.input),
        "--format", "json",
        "--language", args.language,
        "--model", args.model,
    ]
    if args.engine:
        cmd.extend(["--engine", args.engine])

    try:
        rc, stdout, stderr = _run_stt(cmd)
    except FileNotFoundError as e:
        _emit(
            {
                "status": "error",
                "kind": "stt_not_found",
                "message": f"failed to invoke stt CLI: {e}",
            },
            exit_code=2,
        )

    if rc != 0:
        _emit(_extract_error_envelope(stderr, rc), exit_code=rc)

    try:
        envelope: dict[str, Any] = json.loads(stdout)
    except json.JSONDecodeError as e:
        _emit(
            {
                "status": "error",
                "kind": "internal",
                "message": f"stt CLI emitted unparseable stdout: {e}",
                "details": stdout[:1500] if stdout else "empty stdout",
            },
            exit_code=4,
        )

    segments = envelope.pop("segments", [])
    if args.format == "json":
        envelope["segments"] = segments
    elif args.format == "srt":
        envelope["srt"] = _format_srt(segments)
    else:  # "text"
        envelope["text"] = _format_text(segments)

    print(json.dumps(envelope, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
