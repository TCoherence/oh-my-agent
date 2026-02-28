#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".py",
    ".sh",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".ini",
    ".cfg",
}

PATTERNS = (
    ("destructive git command", re.compile(r"\bgit\s+reset\s+--hard\b")),
    ("destructive git command", re.compile(r"\bgit\s+checkout\s+--\b")),
    ("privileged command", re.compile(r"\bsudo\b")),
    ("curl pipe shell", re.compile(r"curl\b[^\n|]*\|\s*(sh|bash|zsh)\b")),
    ("wget pipe shell", re.compile(r"wget\b[^\n|]*\|\s*(sh|bash|zsh)\b")),
    ("system package manager", re.compile(r"\b(apt|apt-get|yum|dnf|brew|pacman|apk)\b")),
    ("global npm install", re.compile(r"\bnpm\s+install\s+-g\b")),
    ("global pip install", re.compile(r"\bpip(?:3)?\s+install\b")),
    ("home-directory path", re.compile(r"(^|[\s\"'])~\/")),
    ("hardcoded dotfiles path", re.compile(r"/Users/[^/\s]+/|\b/home/[^/\s]+/")),
)


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def iter_text_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != "SKILL.md":
            continue
        files.append(path)
    return files


def scan_frontmatter(skill_md: Path, rel: str, findings: list[str]) -> None:
    text = read_text(skill_md)
    if text is None:
        findings.append(f"WARN {rel}: SKILL.md is not valid UTF-8 text")
        return
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        findings.append(f"WARN {rel}: missing or malformed YAML frontmatter")
        return
    frontmatter = match.group(1)
    if "name:" not in frontmatter:
        findings.append(f"WARN {rel}: frontmatter missing name")
    if "description:" not in frontmatter:
        findings.append(f"WARN {rel}: frontmatter missing description")


def scan_file(path: Path, root: Path, findings: list[str]) -> None:
    text = read_text(path)
    if text is None:
        return
    rel = str(path.relative_to(root))
    if path.name == "SKILL.md":
        scan_frontmatter(path, rel, findings)
    for label, pattern in PATTERNS:
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                findings.append(f"WARN {rel}:{lineno}: {label}: {line.strip()}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan a community skill for common portability problems."
    )
    parser.add_argument("skill_path", help="Path to the source skill directory")
    args = parser.parse_args()

    root = Path(args.skill_path).resolve()
    if not root.exists():
        print(f"[ERROR] skill path not found: {root}")
        return 1
    if not root.is_dir():
        print(f"[ERROR] not a directory: {root}")
        return 1

    skill_md = root / "SKILL.md"
    findings: list[str] = []

    if not skill_md.exists():
        findings.append("WARN SKILL.md: missing required file")

    files = iter_text_files(root)
    for path in files:
        scan_file(path, root, findings)

    print(f"[INFO] scanned {len(files)} text file(s) under {root}")
    if not findings:
        print("[OK] no common portability issues found")
        return 0

    for finding in findings:
        print(finding)
    print(f"[INFO] total findings: {len(findings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
