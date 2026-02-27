from oh_my_agent.agents.cli.base import _extract_cli_error


def test_extract_cli_error_prefers_stderr():
    err = _extract_cli_error(b"fatal on stderr", b'{"result":"from stdout"}')
    assert err == "fatal on stderr"


def test_extract_cli_error_uses_plain_stdout_when_no_stderr():
    err = _extract_cli_error(b"", b"plain stdout error")
    assert err == "plain stdout error"


def test_extract_cli_error_reads_json_result():
    err = _extract_cli_error(b"", b'{"is_error":true,"result":"Not logged in"}')
    assert err == "Not logged in"


def test_extract_cli_error_reads_nested_json_message():
    err = _extract_cli_error(b"", b'{"error":{"message":"bad auth"}}')
    assert err == "bad auth"


def test_extract_cli_error_handles_empty_streams():
    err = _extract_cli_error(b"", b"")
    assert err == "(no stdout/stderr)"
