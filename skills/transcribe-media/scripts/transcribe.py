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
import platform
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any


SUBPROCESS_TIMEOUT_SECONDS = 1700  # SKILL.md frontmatter cap is 1800; leave ~100s headroom
GRACEFUL_TERM_GRACE_SECONDS = 5

# TODO: switch to upstream jianchang512/stt once the CLI lands there.
STT_REPO_URL = "https://github.com/TCoherence/stt.git"
INSTALL_TIMEOUT_CLONE = 300       # 5 min — git clone of the stt repo
INSTALL_TIMEOUT_VENV = 120        # 2 min — python -m venv
INSTALL_TIMEOUT_PIP_BASE = 600    # 10 min — torch + faster-whisper, slow on cold caches
INSTALL_TIMEOUT_PIP_MLX = 300     # 5 min — mlx-whisper
INSTALL_STDERR_TAIL_BYTES = 4000  # last N bytes of failing tool's stderr in error envelope


def _emit(payload: dict, *, exit_code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


def _log(msg: str) -> None:
    """Progress messages → stderr so stdout stays clean for the JSON envelope."""
    print(f"[transcribe-media] {msg}", file=sys.stderr, flush=True)


def _install_failed(message: str, exit_code: int = 1) -> None:
    _emit(
        {"status": "error", "kind": "stt_install_failed", "message": message},
        exit_code=exit_code,
    )


def _run_install_step(cmd: list[str], *, timeout: int, label: str) -> None:
    """Run an install subprocess in its own process group; kill the group on timeout.

    Mirrors the transcribe-step pattern in `_run_stt` so a SIGTERM hitting the
    wrapper mid-install doesn't leak pip / git children.
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError as e:
        _install_failed(f"{label} failed: {e}")
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid, signal.SIGTERM)
        try:
            proc.communicate(timeout=GRACEFUL_TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _kill_group(proc.pid, signal.SIGKILL)
            proc.communicate()
        _install_failed(f"{label} timed out after {timeout}s; process group terminated")
    if proc.returncode != 0:
        # pip can spew megabytes; keep the tail which usually has the real reason.
        tail = (stderr or stdout or "").strip()[-INSTALL_STDERR_TAIL_BYTES:]
        cmd_summary = " ".join(cmd[:3]) + (" ..." if len(cmd) > 3 else "")
        _install_failed(
            f"{label} exited {proc.returncode} (cmd: {cmd_summary}): {tail}",
        )


def _toggle_engine_to_mlx(ini_path: Path) -> None:
    """Flip the active `engine=` line in set.ini from faster-whisper → mlx.

    Line-aware so a commented `; engine=faster-whisper` is left alone; only an
    UNcommented active line is changed. Logs whether or not a substitution was made.
    """
    try:
        content = ini_path.read_text(encoding="utf-8")
    except OSError as e:
        _log(f"could not read {ini_path}: {e}; staying on faster-whisper")
        return

    out_lines: list[str] = []
    modified = False
    for line in content.splitlines(keepends=True):
        stripped = line.lstrip()
        if (
            not modified
            and not stripped.startswith((";", "#"))
            and stripped.startswith("engine")
            and "=" in stripped
        ):
            key, _, value = stripped.partition("=")
            if key.strip() == "engine" and value.strip().lower() == "faster-whisper":
                indent = line[: len(line) - len(stripped)]
                trailing_nl = "\n" if line.endswith("\n") else ""
                out_lines.append(f"{indent}engine=mlx{trailing_nl}")
                modified = True
                continue
        out_lines.append(line)

    if modified:
        try:
            ini_path.write_text("".join(out_lines), encoding="utf-8")
            _log("flipped engine=faster-whisper → engine=mlx in set.ini")
        except OSError as e:
            _log(f"could not write {ini_path}: {e}; staying on faster-whisper")
    else:
        _log("no active `engine=faster-whisper` line in set.ini; engine setting left as-is")


def _install_stt(home: Path) -> None:
    """Clone stt into `home`, create its venv, install requirements.

    Apple Silicon also gets mlx-whisper + engine=mlx flipped on for the ~30x
    realtime payoff. mlx install failure falls back to faster-whisper CPU
    instead of erroring out.
    """
    git_bin = shutil.which("git")
    py3_bin = shutil.which("python3") or sys.executable
    if not git_bin:
        _install_failed(
            "git not on PATH; install git or pre-install stt manually at $STT_HOME",
            exit_code=2,
        )

    if home.exists():
        # Require BOTH a .git/ and a set.ini — the latter is stt-specific so we
        # don't accidentally run pip inside an unrelated git checkout sitting at
        # $STT_HOME.
        if not (home / ".git").is_dir() or not (home / "set.ini").is_file():
            _install_failed(
                f"{home} exists but is not a stt checkout (missing .git/ or set.ini); "
                "remove it or set $STT_HOME elsewhere",
                exit_code=2,
            )

    if not home.exists():
        home.parent.mkdir(parents=True, exist_ok=True)
        _log(f"cloning {STT_REPO_URL} → {home}")
        _run_install_step(
            [git_bin, "clone", "--depth", "1", STT_REPO_URL, str(home)],
            timeout=INSTALL_TIMEOUT_CLONE,
            label="git clone",
        )

    venv_dir = home / "venv"
    if not (venv_dir / "bin" / "python").is_file():
        _log(f"creating venv at {venv_dir}")
        _run_install_step(
            [py3_bin, "-m", "venv", str(venv_dir)],
            timeout=INSTALL_TIMEOUT_VENV,
            label="venv create",
        )

    pip_bin = venv_dir / "bin" / "pip"
    _log("installing stt requirements (torch + faster-whisper + ...; first run takes 1–3 min)")
    _run_install_step(
        [str(pip_bin), "install", "--upgrade", "pip"],
        timeout=INSTALL_TIMEOUT_VENV,
        label="pip self-upgrade",
    )
    _run_install_step(
        [str(pip_bin), "install", "-r", str(home / "requirements.txt")],
        timeout=INSTALL_TIMEOUT_PIP_BASE,
        label="pip install -r requirements.txt",
    )

    if platform.system() == "Darwin" and platform.machine() == "arm64":
        _log("Apple Silicon detected; installing mlx-whisper for GPU acceleration")
        try:
            rs = subprocess.run(
                [str(pip_bin), "install", "mlx-whisper"],
                capture_output=True, text=True, timeout=INSTALL_TIMEOUT_PIP_MLX,
            )
        except subprocess.TimeoutExpired:
            rs = None
        if rs is not None and rs.returncode == 0:
            _toggle_engine_to_mlx(home / "set.ini")
        else:
            _log("mlx-whisper install failed/timed out; falling back to faster-whisper CPU")

    _log("stt setup complete")


def _ensure_stt() -> Path:
    """Return $STT_HOME, auto-installing stt on first call if missing.

    Mirrors oh-my-agent's `_ensure_yt_dlp` pattern (cf. youtube-video-summary).
    Set STT_AUTO_INSTALL=0 to opt out and require a pre-existing install.
    """
    raw = os.environ.get("STT_HOME") or "~/repos/stt"
    home = Path(raw).expanduser().resolve()
    cli_py = home / "cli.py"
    venv_python = home / "venv" / "bin" / "python"
    if cli_py.is_file() and venv_python.is_file():
        return home

    if os.environ.get("STT_AUTO_INSTALL") == "0":
        _emit(
            {
                "status": "error",
                "kind": "stt_not_found",
                "message": (
                    f"stt project not found at {home} (auto-install disabled via "
                    f"STT_AUTO_INSTALL=0). Clone {STT_REPO_URL} into $STT_HOME and "
                    "create its venv."
                ),
            },
            exit_code=2,
        )

    _log(f"stt not found at {home}; auto-installing (slow on first call, fast thereafter)")
    _install_stt(home)

    if not (cli_py.is_file() and venv_python.is_file()):
        _install_failed(
            f"setup completed but expected files still missing at {home}",
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

    home = _ensure_stt()

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
