from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def module(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


audit = module("portfolio_audit")
calendar = module("calendar_preview")


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "program.py").write_text("print('hello')\n")
    (root / "query.sql").write_text("SELECT 1;\n")
    (root / "result.html").write_text("<html>generated result</html>\n")
    (root / "index.html").write_text("<html>authored page</html>\n")
    (root / "vendor.py").write_text("# external dependency\n")
    (root / ".gitattributes").write_text(
        "result.html linguist-generated=true\nvendor.py linguist-vendored=true\n*.sql linguist-detectable=true\n"
    )
    return root


def test_source_audit_preserves_authored_html_and_excludes_generated_vendor(checkout):
    result = audit.scan_checkout(checkout)
    assert result["languages"]["Python"] == (checkout / "program.py").stat().st_size
    assert result["languages"]["HTML"] == (checkout / "index.html").stat().st_size
    assert result["languages"]["SQL"] == (checkout / "query.sql").stat().st_size
    assert set(result["excluded"]) == {"result.html", "vendor.py"}


def test_source_scan_respects_gitignore_and_avoids_symlinks(checkout, tmp_path):
    (checkout / ".gitignore").write_text("ignored.py\n")
    (checkout / "ignored.py").write_text("secret material")
    outside = tmp_path / "outside.py"
    outside.write_text("do not inspect")
    (checkout / "link.py").symlink_to(outside)
    inspected = audit.scan_checkout(checkout)
    assert "link.py" not in {source["path"] for source in inspected["sources"]}
    assert "ignored.py" not in {source["path"] for source in inspected["sources"]}


def test_summary_does_not_count_forks_and_does_not_mutate_input():
    rows = [
        {"name": "own", "fork": False, "languages": {"Python": 100, "Java": 50}},
        {"name": "fork", "fork": True, "languages": {"Java": 9999}},
    ]
    summary = audit.summarize(rows, "person")
    assert summary["largest_language"] == "Python"
    assert summary["total_bytes"] == 150
    assert summary["repository_count"] == 1
    assert sum(row["percent"] for row in summary["languages"]) == pytest.approx(100)
    assert rows[0]["languages"]["Python"] == 100


@pytest.mark.parametrize(
    "languages", [{"Python": -1}, {"Python": 1.1}, {"Python": True}, []]
)
def test_invalid_byte_counts_cannot_become_a_profile_graph(languages):
    with pytest.raises(audit.AuditError):
        audit.summarize([{"name": "repo", "languages": languages}], "person")


def test_local_overlay_is_labeled_and_unknown_repositories_are_rejected(checkout):
    rows = [{"name": "repo", "languages": {"Java": 100}}]
    summary = audit.summarize(rows, "person", {"repo": checkout})
    assert summary["local_estimate"] is True
    assert "Local source estimate" in summary["method"]
    with pytest.raises(audit.AuditError, match="Unknown checkout"):
        audit.summarize(rows, "person", {"other": checkout})


def test_svg_is_valid_xml_and_escapes_language_names():
    result = audit.summarize(
        [{"name": "repo", "languages": {"Python": 100, "<script>": 5}}], "person"
    )
    svg = audit.render_svg(result)
    ET.fromstring(svg)
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_cli_supports_both_input_snapshot_shapes(tmp_path):
    for key in ["repos", "repositories"]:
        snapshot = tmp_path / f"{key}.json"
        snapshot.write_text(
            json.dumps({key: [{"name": "repo", "languages": {"Python": 100}}]})
        )
        assert (
            audit.main(
                [
                    "--snapshot",
                    str(snapshot),
                    "--output",
                    str(tmp_path / "result.json"),
                    "--svg",
                    str(tmp_path / "result.svg"),
                ]
            )
            == 0
        )
        assert (
            json.loads((tmp_path / "result.json").read_text())["largest_language"]
            == "Python"
        )


def test_sample_calendar_is_reproducible_bounded_and_varied():
    start, end = dt.date(2025, 11, 3), dt.date(2026, 11, 2)
    first = calendar.sample_days(start, end, 42, ["Python project", "Web project"])
    second = calendar.sample_days(start, end, 42, ["Python project", "Web project"])
    assert first == second
    assert len(first) == 365
    assert len({day.date for day in first}) == 365
    assert all(1 <= day.count <= 7 for day in first)
    assert len({calendar.level(day.count) for day in first}) >= 3
    assert len({day.repository for day in first}) == 2


def test_sample_can_include_gaps_only_when_requested():
    days = calendar.sample_days(
        dt.date(2026, 1, 1), dt.date(2026, 12, 31), 42, ["repo"], gaps=True
    )
    assert any(day.count == 0 for day in days)
    assert any(day.count > 0 for day in days)


def test_calendar_export_discloses_illustration_and_round_trips_counts(tmp_path):
    start, end = dt.date(2026, 10, 1), dt.date(2026, 10, 31)
    days = calendar.sample_days(start, end, 3, ["<untrusted>"])
    result = calendar.export(days, tmp_path, True, 3)
    assert result["github_history_changed"] is False
    assert result["mode"] == "illustrative sample"
    assert calendar.read_counts(tmp_path / "calendar.csv", start, end) == days
    svg = (tmp_path / "calendar.svg").read_text()
    ET.fromstring(svg)
    assert "Illustrative activity preview" in svg
    assert "<untrusted>" not in svg
    assert (
        "does not represent completed historical work"
        in (tmp_path / "index.html").read_text()
    )


def test_imported_counts_fill_gaps_and_reject_duplicates(tmp_path):
    path = tmp_path / "counts.csv"
    path.write_text("date,count,repository\n2026-01-02,3,repo\n")
    result = calendar.read_counts(path, dt.date(2026, 1, 1), dt.date(2026, 1, 3))
    assert [day.count for day in result] == [0, 3, 0]
    with path.open("a") as stream:
        csv.writer(stream).writerow(["2026-01-02", 4, "repo"])
    with pytest.raises(ValueError, match="unique"):
        calendar.read_counts(path, dt.date(2026, 1, 1), dt.date(2026, 1, 3))


def test_ranges_and_negative_counts_are_rejected():
    with pytest.raises(ValueError, match="1 to 370"):
        calendar.dates(dt.date(2026, 1, 2), dt.date(2026, 1, 1))
    with pytest.raises(ValueError, match="negative"):
        calendar.level(-1)


def test_actual_local_history_uses_selected_author_and_deduplicates(tmp_path):
    root = tmp_path / "git"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / "file.py").write_text("print('working feature')\n")
    subprocess.run(["git", "-C", str(root), "add", "file.py"], check=True)
    # A fixed test fixture timestamp, isolated in a disposable temporary repository.
    import os

    env = dict(
        os.environ,
        GIT_AUTHOR_DATE="2026-01-02T12:00:00-05:00",
        GIT_COMMITTER_DATE="2026-01-02T12:00:00-05:00",
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test author",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "add working feature",
        ],
        check=True,
        env=env,
    )
    days = calendar.local_days(
        dt.date(2026, 1, 1), dt.date(2026, 1, 3), [root, root], "test@example.com"
    )
    assert [day.count for day in days] == [0, 1, 0]
    others = calendar.local_days(
        dt.date(2026, 1, 1), dt.date(2026, 1, 3), [root], "other@example.com"
    )
    assert sum(day.count for day in others) == 0
