from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path("skills/scheduler/scripts/validate_automations.py")


def _run_validator(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_validator_accepts_file_driven_directory_with_cron_and_interval(tmp_path):
    (tmp_path / "daily-politics.yaml").write_text(
        "\n".join(
            [
                "name: daily-politics",
                "platform: discord",
                'channel_id: "${DISCORD_CHANNEL_ID}"',
                'prompt: "Run the politics daily digest."',
                'cron: "0 8 * * *"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "hello.yaml").write_text(
        "\n".join(
            [
                "name: hello-smoke",
                "platform: discord",
                'channel_id: "${DISCORD_CHANNEL_ID}"',
                "delivery: dm",
                'prompt: "Say hello."',
                "interval_seconds: 20",
                "initial_delay_seconds: 5",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = _run_validator(tmp_path)
    assert result.returncode == 0
    assert "[OK] validated 2 automation file(s)" in result.stdout
    assert "owner fallback" in result.stdout


def test_validator_rejects_duplicate_names(tmp_path):
    for index in (1, 2):
        (tmp_path / f"dup-{index}.yaml").write_text(
            "\n".join(
                [
                    "name: duplicate-name",
                    "platform: discord",
                    'channel_id: "${DISCORD_CHANNEL_ID}"',
                    'prompt: "Run it."',
                    'cron: "0 9 * * *"',
                    "",
                ]
            ),
            encoding="utf-8",
        )

    result = _run_validator(tmp_path)
    assert result.returncode == 1
    assert "duplicate automation name" in result.stdout


def test_validator_rejects_cron_with_initial_delay(tmp_path):
    target = tmp_path / "bad.yaml"
    target.write_text(
        "\n".join(
            [
                "name: bad-cron",
                "platform: discord",
                'channel_id: "${DISCORD_CHANNEL_ID}"',
                'prompt: "Run it."',
                'cron: "0 9 * * *"',
                "initial_delay_seconds: 10",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = _run_validator(target)
    assert result.returncode == 1
    assert "initial_delay_seconds is not supported with cron" in result.stdout
