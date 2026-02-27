import os
import textwrap
from pathlib import Path

import pytest
import yaml
from oh_my_agent.config import load_config, _substitute


def test_substitute_replaces_env_var(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "abc123")
    assert _substitute("${MY_TOKEN}") == "abc123"


def test_substitute_leaves_unknown_var_unchanged():
    result = _substitute("${DEFINITELY_NOT_SET_XYZ}")
    assert result == "${DEFINITELY_NOT_SET_XYZ}"


def test_substitute_handles_nested_dict(monkeypatch):
    monkeypatch.setenv("TOKEN", "tok")
    data = {"key": "${TOKEN}", "nested": {"inner": "${TOKEN}"}}
    result = _substitute(data)
    assert result == {"key": "tok", "nested": {"inner": "tok"}}


def test_substitute_handles_list(monkeypatch):
    monkeypatch.setenv("VAL", "hello")
    result = _substitute(["${VAL}", "plain"])
    assert result == ["hello", "plain"]


def test_load_config_parses_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "tok-xyz")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(textwrap.dedent("""
        gateway:
          channels:
            - platform: discord
              token: ${BOT_TOKEN}
              channel_id: "999"
              agents: [claude]
        agents:
          claude:
            type: cli
            model: sonnet
    """))
    config = load_config(cfg_file)
    assert config["gateway"]["channels"][0]["token"] == "tok-xyz"
    assert config["gateway"]["channels"][0]["channel_id"] == "999"
    assert config["agents"]["claude"]["model"] == "sonnet"


def test_load_config_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nonexistent.yaml")


def _flatten_key_paths(value, prefix=""):
    paths = set()
    if isinstance(value, dict):
        for key, child in value.items():
            current = f"{prefix}.{key}" if prefix else str(key)
            paths.add(current)
            paths.update(_flatten_key_paths(child, current))
    elif isinstance(value, list) and value:
        current = f"{prefix}[]"
        paths.add(current)
        paths.update(_flatten_key_paths(value[0], current))
    return paths


def test_local_config_covers_example_keys():
    example_path = Path("config.yaml.example")
    local_path = Path("config.yaml")
    if not local_path.exists():
        pytest.skip("local config.yaml not present")

    example = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    local = yaml.safe_load(local_path.read_text(encoding="utf-8"))

    example_paths = _flatten_key_paths(example)
    local_paths = _flatten_key_paths(local)

    ignored_prefixes = {
        "gateway.channels[].token",
        "gateway.channels[].channel_id",
        "access.owner_user_ids",
        "router.api_key_env",
    }
    required_paths = {
        path
        for path in example_paths
        if path not in ignored_prefixes and not any(path.startswith(f"{prefix}.") for prefix in ignored_prefixes)
    }

    missing = sorted(path for path in required_paths if path not in local_paths)
    assert not missing, f"config.yaml is missing keys from config.yaml.example: {missing}"
