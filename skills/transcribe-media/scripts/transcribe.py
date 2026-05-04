#!/usr/bin/env python3
"""Wrapper around the stt project's cli.py for the oh-my-agent skill ecosystem.

Locates stt via $STT_HOME (default ~/repos/stt), shells out to its CLI,
and translates the result to the oh-my-agent convention: JSON envelope on
stdout (always), exit code reflects success/failure.

stt's CLI emits success to stdout and errors to stderr; this wrapper
captures both and re-emits errors on stdout so callers only need to read
one stream.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


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


def _extract_error_envelope(stderr_text: str, returncode: int) -> dict:
    """Parse stt CLI's JSON error from stderr; fall back to a generic envelope."""
    text = stderr_text.strip()
    if text:
        # stt CLI emits its JSON error on a single line; with --verbose, it may
        # be preceded by progress lines. Walk lines from the end to find JSON.
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
        description="Transcribe audio/video to JSON / SRT / text via the stt CLI.",
    )
    parser.add_argument("--input", required=True, help="Path to media file (~ expanded).")
    parser.add_argument(
        "--format", default="json", choices=["json", "srt", "text"],
        help="Output format (default: json).",
    )
    parser.add_argument("--language", default="auto", help="ISO 639-1 code or 'auto' (default).")
    parser.add_argument(
        "--engine", choices=["faster-whisper", "mlx"], default=None,
        help="Backend override; default reads stt's set.ini.",
    )
    parser.add_argument("--model", default="large-v3-turbo", help="Whisper model name.")
    parser.add_argument(
        "--output", default=None,
        help="Write body to file instead of stdout. `-` means stdout (default).",
    )
    args = parser.parse_args()

    home = _resolve_stt_home()

    cmd = [
        str(home / "venv" / "bin" / "python"),
        str(home / "cli.py"),
        os.path.expanduser(args.input),
        "--format", args.format,
        "--language", args.language,
        "--model", args.model,
    ]
    if args.engine:
        cmd.extend(["--engine", args.engine])
    if args.output:
        cmd.extend(["--output", args.output])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as e:
        _emit(
            {
                "status": "error",
                "kind": "stt_not_found",
                "message": f"failed to invoke stt CLI: {e}",
            },
            exit_code=2,
        )

    if result.returncode == 0:
        sys.stdout.write(result.stdout)
        if result.stdout and not result.stdout.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()
        sys.exit(0)

    envelope = _extract_error_envelope(result.stderr, result.returncode)
    _emit(envelope, exit_code=result.returncode)


if __name__ == "__main__":
    main()
