"""Tests for the read-intended ``reports_archive/`` workspace symlink helper."""

from oh_my_agent.utils.reports_link import ensure_reports_archive_link


def test_creates_link_and_precreates_reports_dir(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    reports = tmp_path / "reports"  # does not exist yet
    ensure_reports_archive_link(ws, reports)
    link = ws / "reports_archive"
    assert link.is_symlink()
    assert link.resolve() == reports.resolve()
    assert reports.is_dir(), "reports_dir must be pre-created so the link never dangles"


def test_idempotent_keeps_correct_symlink(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    reports = tmp_path / "reports"
    ensure_reports_archive_link(ws, reports)
    before = (ws / "reports_archive").resolve()
    ensure_reports_archive_link(ws, reports)  # second call
    assert (ws / "reports_archive").resolve() == before


def test_repoints_wrong_symlink(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    reports = tmp_path / "reports"
    reports.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (ws / "reports_archive").symlink_to(other)
    ensure_reports_archive_link(ws, reports)
    assert (ws / "reports_archive").resolve() == reports.resolve()


def test_preserves_real_directory(tmp_path):
    """A real dir at reports_archive (e.g. in the durable base workspace) must
    never be clobbered — refuse and keep its contents."""
    ws = tmp_path / "ws"
    ws.mkdir()
    real = ws / "reports_archive"
    real.mkdir()
    (real / "keep.txt").write_text("important", encoding="utf-8")
    reports = tmp_path / "reports"
    ensure_reports_archive_link(ws, reports)
    assert real.is_dir() and not real.is_symlink()
    assert (real / "keep.txt").read_text(encoding="utf-8") == "important"


def test_preserves_real_file(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    real = ws / "reports_archive"
    real.write_text("not a link", encoding="utf-8")
    ensure_reports_archive_link(ws, tmp_path / "reports")
    assert real.is_file() and not real.is_symlink()
    assert real.read_text(encoding="utf-8") == "not a link"


def test_noop_when_disabled(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    ensure_reports_archive_link(ws, None)
    assert not (ws / "reports_archive").exists()
    assert not (ws / "reports_archive").is_symlink()


def test_repoints_broken_symlink(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "reports_archive").symlink_to(tmp_path / "gone")  # dangling
    reports = tmp_path / "reports"
    ensure_reports_archive_link(ws, reports)
    assert (ws / "reports_archive").resolve() == reports.resolve()
