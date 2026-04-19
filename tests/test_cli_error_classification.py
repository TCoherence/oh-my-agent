from __future__ import annotations

import pytest

from oh_my_agent.agents.cli.base import classify_cli_error_kind


@pytest.mark.parametrize(
    "err_msg, expected",
    [
        # rate limit
        ("Rate limit exceeded", "rate_limit"),
        ("rate_limit hit for model", "rate_limit"),
        ("HTTP 429 Too Many Requests", "rate_limit"),
        ("You have exceeded your quota exceeded for today", "rate_limit"),
        ("Usage limit reached", "rate_limit"),
        # api 5xx
        ("500 Internal Server Error", "api_5xx"),
        ("Bad Gateway", "api_5xx"),
        ("Service Unavailable — retry later", "api_5xx"),
        ("Upstream connection timed out", "api_5xx"),
        ("API returned 503", "api_5xx"),
        ("Model is currently overloaded, please try again", "api_5xx"),
        # auth
        ("Invalid API key provided", "auth"),
        ("Authentication failed: token expired", "auth"),
        ("Please log in to continue", "auth"),
        ("401 Unauthorized", "auth"),
        ("not authenticated", "auth"),
        # fallback
        ("Unknown error parsing JSON", "cli_error"),
        ("(no stdout/stderr)", "cli_error"),
        ("", "cli_error"),
        # priority check: rate limit wins over 429 alone, and over 5xx
        ("429 rate limit hit, upstream also 503", "rate_limit"),
        # priority check: 5xx wins over auth if both appear
        ("503 service unavailable, also unauthorized", "api_5xx"),
    ],
)
def test_classify_cli_error_kind(err_msg: str, expected: str) -> None:
    assert classify_cli_error_kind(err_msg) == expected


def test_classifier_is_case_insensitive() -> None:
    assert classify_cli_error_kind("RATE LIMIT EXCEEDED") == "rate_limit"
    assert classify_cli_error_kind("Invalid API Key") == "auth"
    assert classify_cli_error_kind("OVERLOADED") == "api_5xx"
