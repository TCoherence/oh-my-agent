from __future__ import annotations

from oh_my_agent.utils.usage import append_usage_audit, format_usage_audit


def test_format_usage_audit_renders_input_output_only():
    assert format_usage_audit({"input_tokens": 1234, "output_tokens": 567}) == "1,234 in / 567 out"


def test_format_usage_audit_renders_cache_and_cost():
    assert format_usage_audit(
        {
            "input_tokens": 4321,
            "output_tokens": 2109,
            "cache_read_input_tokens": 90000,
            "cache_creation_input_tokens": 12000,
            "cost_usd": 0.4821,
        }
    ) == "4,321 in / 2,109 out · cache 90,000r/12,000w · $0.4821"


def test_append_usage_audit_leaves_prefix_unchanged_when_usage_missing():
    assert append_usage_audit("-# via **codex**", None) == "-# via **codex**"
