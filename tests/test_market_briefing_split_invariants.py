"""Invariants for the 4-way market-briefing skill split.

After splitting the monolithic ``skills/market-briefing/`` into 4 atomic
skills (``-ai`` / ``-finance`` / ``-politics`` / ``-weekly``), these tests
guard against future drift:

- per-skill frontmatter integrity
- AI- and Finance-only references stay isolated to their owning skill
- ``REPORTS_ROOT_NAME`` stays ``"market-briefing"`` (output dir invariant)
- ``report_store.py`` stays byte-identical across all 4 skills
- no stray bare ``market-briefing`` references in the repo (everything
  must be ``market-briefing-{ai,finance,politics,weekly}`` or
  ``market-briefing-*`` / ``市列`` / ``family`` placeholders)
- ``automation_templates.md`` ``skill_name`` matches the owning skill

If a test in this file fails, do NOT silence it without re-reading the
Stage 3 plan in ``plans/market-briefing-daily-ai-0900-fail-patt-mutable-nest.md``
— each invariant exists because it tied directly to a regression risk
the plan called out.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = REPO_ROOT / "skills"
SPLIT_SKILLS = (
    "market-briefing-ai",
    "market-briefing-finance",
    "market-briefing-politics",
    "market-briefing-weekly",
)


def _parse_frontmatter(skill_md: Path) -> dict[str, object]:
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise AssertionError(f"{skill_md} missing leading frontmatter")
    _, fm, _ = text.split("---\n", 2)
    return yaml.safe_load(fm) or {}


def test_split_skills_directories_exist() -> None:
    """All 4 new skill directories exist and the old monolithic one is gone."""
    for skill in SPLIT_SKILLS:
        assert (SKILLS_ROOT / skill / "SKILL.md").is_file(), f"{skill}/SKILL.md missing"
    assert not (SKILLS_ROOT / "market-briefing").exists(), (
        "old skills/market-briefing/ should have been removed in the split"
    )


def test_frontmatter_valid_per_skill() -> None:
    """Each new SKILL.md has matching ``name`` + ``metadata.{timeout_seconds,max_turns}``."""
    for skill in SPLIT_SKILLS:
        fm = _parse_frontmatter(SKILLS_ROOT / skill / "SKILL.md")
        assert fm.get("name") == skill, f"{skill}/SKILL.md name mismatch: {fm.get('name')!r}"
        assert isinstance(fm.get("description"), str) and fm["description"], (
            f"{skill}/SKILL.md missing description"
        )
        meta = fm.get("metadata") or {}
        assert isinstance(meta.get("timeout_seconds"), int) and meta["timeout_seconds"] > 0, (
            f"{skill}/SKILL.md timeout_seconds invalid: {meta.get('timeout_seconds')!r}"
        )
        assert isinstance(meta.get("max_turns"), int) and meta["max_turns"] > 0, (
            f"{skill}/SKILL.md max_turns invalid: {meta.get('max_turns')!r}"
        )


def test_ai_only_files_isolated_to_ai_skill() -> None:
    """``section_schemas.md`` / ``ai_frontier_watchlist.md`` / ``ai_people_seed.yaml`` /
    ``ai_people_pool.py`` live only under ``market-briefing-ai/``."""
    ai_only_relpaths = (
        "references/section_schemas.md",
        "references/ai_frontier_watchlist.md",
        "references/ai_people_seed.yaml",
        "scripts/ai_people_pool.py",
    )
    for relpath in ai_only_relpaths:
        ai_path = SKILLS_ROOT / "market-briefing-ai" / relpath
        assert ai_path.is_file(), f"market-briefing-ai/{relpath} should exist"
        for other in SPLIT_SKILLS:
            if other == "market-briefing-ai":
                continue
            other_path = SKILLS_ROOT / other / relpath
            assert not other_path.exists(), (
                f"{relpath} leaked into {other}/ (must stay AI-only)"
            )


def test_finance_only_files_isolated_to_finance_skill() -> None:
    """``finance_watchlist.md`` lives only under ``market-briefing-finance/``."""
    finance_relpath = "references/finance_watchlist.md"
    assert (SKILLS_ROOT / "market-briefing-finance" / finance_relpath).is_file()
    for other in SPLIT_SKILLS:
        if other == "market-briefing-finance":
            continue
        assert not (SKILLS_ROOT / other / finance_relpath).exists(), (
            f"finance_watchlist.md leaked into {other}/"
        )


def test_reports_root_name_consistent_across_copies() -> None:
    """All 4 copies of ``report_store.py`` declare ``REPORTS_ROOT_NAME = "market-briefing"``
    so the canonical output tree at ``~/.oh-my-agent/reports/market-briefing/`` stays shared."""
    expected_literal = 'REPORTS_ROOT_NAME = "market-briefing"'
    for skill in SPLIT_SKILLS:
        path = SKILLS_ROOT / skill / "scripts" / "report_store.py"
        text = path.read_text(encoding="utf-8")
        assert expected_literal in text, (
            f"{skill}/scripts/report_store.py missing {expected_literal!r}"
        )


def test_report_store_copies_byte_identical() -> None:
    """All 4 ``report_store.py`` copies must be byte-identical (sha256).

    LOAD-BEARING: this is what forces future bugfixes to update all 4 copies
    in lockstep. Stripping AI-only logic from any one copy (and thus letting
    them diverge) defeats the safety net and will be caught here.
    """
    digests: dict[str, str] = {}
    for skill in SPLIT_SKILLS:
        path = SKILLS_ROOT / skill / "scripts" / "report_store.py"
        digests[skill] = hashlib.sha256(path.read_bytes()).hexdigest()
    distinct = set(digests.values())
    assert len(distinct) == 1, (
        f"report_store.py copies diverged: {digests!r}. "
        "All 4 must stay byte-identical — fix the outlier or apply the same patch to every copy."
    )


# Allow-list for legitimate bare ``market-briefing`` mentions: the output
# directory under ``reports/`` (kept stable on purpose) plus the family
# placeholders. Anything else must be one of the per-domain skill names.
_BARE_MENTION_RE = re.compile(
    r"market-briefing"
    r"(?!"
    r"-(ai|finance|politics|weekly|\*)"
    r"|\s*(系列|family|family\.)"
    r"|/\s*[a-z]"  # "reports/market-briefing/<sub>/" path (KEEP for output tree)
    r"|/<"  # "reports/market-briefing/<date>/" docstring snippets
    r")"
)


def test_no_stray_bare_market_briefing_refs() -> None:
    """Repo-wide grep: every ``market-briefing`` mention must either point at
    one of the 4 split skills, be a family-style placeholder (``market-briefing-*``
    / ``市列 系列`` / ``family``), or refer to the output report path
    ``reports/market-briefing/...`` (unchanged under the split).

    Excludes ``CHANGELOG.md`` (historical entries are ground truth) and
    ``plans/`` (which discusses the split plan itself).
    """
    # Allow-list tokens that legitimately appear regardless of path:
    # (a) output-path strings (the report tree under reports/market-briefing/
    #     stays shared across all 4 split skills),
    # (b) the plan slug `market-briefing-daily-ai-0900-fail-patt-mutable-nest`
    #     (a stable git artifact name, not a skill identifier).
    output_path_whitelist = (
        "reports/market-briefing/",
        "reports/market-briefing\"",
        '"market-briefing"',  # JSON / DB column literal (output-tree identifier)
        "/market-briefing/",
        ".oh-my-agent/reports/market-briefing",
        "/market-briefing-test",  # tests/test_market_briefing_ai_report_store.py temp dir
        # Plan slug (stable name; see plans/<slug>.md)
        "market-briefing-daily-ai-0900-fail-patt-mutable-nest",
        # Section_schemas reference text uses bare slug
        "market-briefing-daily-ai",
    )

    # Backtick-quoted ``market-briefing`` literals — markdown's standard way
    # of referring to the old monolithic skill by name in history / migration
    # prose. Permitted ONLY in docs / changelog / tests / CN+EN guides where
    # the term IS a historical citation. NOT permitted inside the new active
    # skill directories (`skills/market-briefing-*/`) — if a new SKILL.md or
    # prompt references the bare old name, that's almost certainly a missed
    # rename, not a deliberate history mention. (Codex review:
    # whitelist-too-wide hardening — `monolithic \`market-briefing\``-style
    # phrasing in active skills would otherwise route the agent to a skill
    # that no longer exists.)
    HISTORICAL_LITERAL = "`market-briefing`"
    HISTORICAL_OK_PREFIXES = (
        "docs/",
        "CHANGELOG.md:",
        "tests/",
        "README.md:",
        "AGENT.md:",
        "AGENTS.md:",
        "CLAUDE.md:",
        "GEMINI.md:",
        "TECH_OVERVIEW.md:",
        "CONTRIBUTING.md:",
    )

    proc = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "grep",
            "-nIE",
            "market-briefing",
            "--",
            ":!CHANGELOG.md",
            ":!plans/",
            ":!tests/test_market_briefing_split_invariants.py",  # self-reference
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 128:
        pytest.skip(f"git grep unavailable: {proc.stderr.strip()}")

    bad: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        # Strip filename + lineno prefix for matching.
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        path_part, _, body = parts
        # Quick allow: any line that is fully covered by the output-path
        # whitelist is fine. Remove every whitelisted token before checking.
        scrubbed = body
        for token in output_path_whitelist:
            scrubbed = scrubbed.replace(token, "")
        # Historical-literal allowance: ``market-briefing`` in backticks is
        # OK only in docs / changelog / tests / repo-root guides — NOT
        # inside the new active skill directories.
        if HISTORICAL_LITERAL in scrubbed:
            if path_part.startswith(HISTORICAL_OK_PREFIXES):
                scrubbed = scrubbed.replace(HISTORICAL_LITERAL, "")
            # else: leave the literal in `scrubbed` so the regex catches it.
        # After scrubbing, any remaining ``market-briefing`` substring must
        # be followed by an allowed suffix per ``_BARE_MENTION_RE``.
        if "market-briefing" not in scrubbed:
            continue
        if _BARE_MENTION_RE.search(scrubbed):
            bad.append(line)

    assert not bad, (
        "Found stray bare `market-briefing` references that should be a per-domain "
        "name (`-ai` / `-finance` / `-politics` / `-weekly`) or the `market-briefing-*` "
        "family placeholder:\n  " + "\n  ".join(bad[:40])
    )


def test_automation_templates_skill_name_matches_owner() -> None:
    """Each skill's ``references/automation_templates.md`` must declare
    ``skill_name: <owner>`` so a user copying the template into
    ``~/.oh-my-agent/automations/<name>.yaml`` gets the right binding."""
    for skill in SPLIT_SKILLS:
        templates = SKILLS_ROOT / skill / "references" / "automation_templates.md"
        text = templates.read_text(encoding="utf-8")
        expected = f"skill_name: {skill}"
        assert expected in text, (
            f"{skill}/references/automation_templates.md missing `{expected}` (templates would bind to the wrong skill)"
        )
        # Also make sure no template binds to a sibling skill name by mistake.
        for other in SPLIT_SKILLS:
            if other == skill:
                continue
            forbidden = f"skill_name: {other}"
            assert forbidden not in text, (
                f"{skill}/references/automation_templates.md contains `{forbidden}` "
                "— a sibling skill's template leaked in"
            )
