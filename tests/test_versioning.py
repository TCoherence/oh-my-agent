from __future__ import annotations

import re
from pathlib import Path

import pytest

from oh_my_agent import __version__
from oh_my_agent.main import main


def _parse_version(version: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\.dev\d+)?", version)
    assert match, f"unexpected version format: {version}"
    return tuple(int(part) for part in match.groups())


def _changelog_text() -> str:
    return Path("CHANGELOG.md").read_text(encoding="utf-8")


def test_package_version_is_not_behind_latest_changelog_release():
    text = _changelog_text()
    match = re.search(r"^## v(\d+\.\d+\.\d+)\b", text, flags=re.MULTILINE)
    assert match, "CHANGELOG.md must contain at least one released version heading"
    latest_release = match.group(1)
    assert _parse_version(__version__) >= _parse_version(latest_release)


def test_cli_version_flag_prints_version(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["oh-my-agent", "--version"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out
