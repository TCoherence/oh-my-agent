import os
import textwrap
import pytest
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
