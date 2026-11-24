from __future__ import annotations

import json
from pathlib import Path

from sphinx.cmd.build import build_main

from needs_reqdiff.api import generate_reqdiff
from needs_reqdiff.differ import compare_directories, fieldwise_changes
from needs_reqdiff.loader import load_sphinx_needs_json
from needs_reqdiff.models import FileComparison
from needs_reqdiff.rst_writer import (
    CHANGED_NEW_FOCUS_TAG,
    CHANGED_NEW_PREFIX,
    CHANGED_OLD_FOCUS_TAG,
    CHANGED_OLD_PREFIX,
    _display_labels,
    write_reports,
)


def test_compare_directories_detects_all_categories(fixture_roots: tuple[Path, Path]) -> None:
    baseline, candidate = fixture_roots
    result = compare_directories(baseline, candidate)

    assert [path.as_posix() for path in result.left_only_files] == ["left_only/legacy.json"]
    assert [path.as_posix() for path in result.right_only_files] == ["right_only/future.json"]
    assert len(result.errors) == 1
    assert result.errors[0].relative_path.as_posix() == "invalid/broken.json"
    assert result.errors[0].side == "candidate"
    assert len(result.comparable_files) == 1
    assert result.comparable_files[0].relative_path.as_posix() == "valid/system.json"
    assert [comparison.relative_path.as_posix() for comparison in result.changed_files] == [
        "valid/system.json"
    ]


def test_diff_detects_added_removed_and_changed_needs(fixture_roots: tuple[Path, Path]) -> None:
    baseline, candidate = fixture_roots
    result = compare_directories(baseline, candidate)
    comparison = result.comparable_files[0]
    diff = comparison.diff
    assert diff is not None

    assert sorted(diff.added) == ["REQ_004"]
    assert sorted(diff.removed) == ["REQ_003"]
    assert sorted(diff.changed_new) == ["REQ_002"]


def test_fieldwise_changes_are_reported_for_changed_need(fixture_roots: tuple[Path, Path]) -> None:
    baseline, candidate = fixture_roots
    _, _, old_needs = load_sphinx_needs_json(baseline / "valid" / "system.json")
    _, _, new_needs = load_sphinx_needs_json(candidate / "valid" / "system.json")

    changes = fieldwise_changes(old_needs["REQ_002"], new_needs["REQ_002"])
    changed_fields = [field for field, _, _ in changes]
    assert changed_fields == ["description", "status", "tags", "links"]


def test_display_labels_use_versions_when_different(fixture_roots: tuple[Path, Path]) -> None:
    baseline, candidate = fixture_roots
    result = compare_directories(baseline, candidate)
    comparison = result.comparable_files[0]
    assert _display_labels(comparison) == ("1.0", "2.0")


def test_display_labels_add_suffix_when_versions_are_equal() -> None:
    comparison = FileComparison(
        relative_path=Path("same.json"), old_version="2.1", new_version="2.1"
    )
    assert _display_labels(comparison) == ("2.1-reference", "2.1-current")


def test_generate_reqdiff_writes_index_diff_and_prefixed_change_exports(
    fixture_roots: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    baseline, candidate = fixture_roots
    result, index_path = generate_reqdiff(baseline, candidate, tmp_path / "out")

    assert index_path.exists()
    assert len(result.comparable_files) == 1

    report_dir = tmp_path / "out" / "valid__system"
    diff_path = report_dir / "diff.rst"
    assert diff_path.exists()
    diff_text = diff_path.read_text(encoding="utf-8")
    assert "valid/system.json\n=================" in diff_text
    assert "Übersicht\n---------" in diff_text
    assert "Neu hinzugekommene Anforderungen\n-------------------------------" in diff_text
    assert ".. table::" in diff_text
    assert "   :class: need_rd_table" in diff_text
    # assert f":id_prefix: {CHANGED_OLD_PREFIX}" in diff_text
    # assert f":id_prefix: {CHANGED_NEW_PREFIX}" in diff_text
    assert "REQ_002\n^^^^^^^" in diff_text
    assert f"Siehe Anhang: :need:`{CHANGED_NEW_PREFIX}REQ_002`" in diff_text
    assert "Anhang Anforderungen" in diff_text
    assert ".. needimport:: added.json" in diff_text
    assert ".. needimport:: removed.json" in diff_text
    assert ".. needimport:: changed_new.json" in diff_text
    assert ".. needimport:: changed_old.json" in diff_text

    changed_old = json.loads((report_dir / "changed_old.json").read_text(encoding="utf-8"))
    changed_old_needs = changed_old["versions"]["1.0"]["needs"]
    assert set(changed_old_needs) == {"OLD_REQ_001", "OLD_REQ_002"}
    assert CHANGED_OLD_FOCUS_TAG in changed_old_needs["OLD_REQ_002"]["tags"]
    assert CHANGED_OLD_FOCUS_TAG not in changed_old_needs["OLD_REQ_001"]["tags"]

    changed_new = json.loads((report_dir / "changed_new.json").read_text(encoding="utf-8"))
    changed_new_needs = changed_new["versions"]["2.0"]["needs"]
    assert set(changed_new_needs) == {"NEW_REQ_001", "NEW_REQ_002", "NEW_REQ_004"}
    assert CHANGED_NEW_FOCUS_TAG in changed_new_needs["NEW_REQ_002"]["tags"]
    assert CHANGED_NEW_FOCUS_TAG not in changed_new_needs["NEW_REQ_001"]["tags"]
    assert "Need 1.0" not in diff_text
    assert "Need 2.0" not in diff_text
    assert "Feldweiser Vergleich" not in diff_text
    assert "   :class: need_rd" in diff_text
    assert "   :filter_warning: Keine Anforderungen hinzugekommen" in diff_text
    assert "   :filter_warning: Keine Anforderung entfernt" in diff_text

    index_text = index_path.read_text(encoding="utf-8")
    assert "Geänderte Dateien" in index_text
    assert "valid/system.json" in index_text
    assert "left_only/legacy.json" in index_text
    assert "right_only/future.json" in index_text
    assert "invalid/broken.json" in index_text


def test_generated_rst_builds_as_html_with_sphinx(
    fixture_roots: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    baseline, candidate = fixture_roots
    reference = "siehe :need:`REQ_NOT_EXPORTED`"
    for root, field in ((baseline, "description"), (candidate, "content")):
        export_path = root / "valid" / "system.json"
        export = json.loads(export_path.read_text(encoding="utf-8"))
        need = export["versions"][export["current_version"]]["needs"]["REQ_002"]
        need[field] = f"{reference}. {need.pop('description')}"
        export_path.write_text(json.dumps(export), encoding="utf-8")
    docs_dir = tmp_path / "docs"
    source_dir = docs_dir / "source"
    build_dir = docs_dir / "build"
    reqdiff_dir = source_dir / "reqdiff"
    source_dir.mkdir(parents=True)

    _, index_path = generate_reqdiff(baseline, candidate, reqdiff_dir)
    assert index_path.exists()

    (source_dir / "conf.py").write_text(
        "extensions = ['sphinx_needs','needs_reqdiff']\n"
        "master_doc = 'index'\n"
        "project = 'needs-reqdiff test'\n"
        "needs_id_required = False\n",
        encoding="utf-8",
    )
    (source_dir / "index.rst").write_text(
        "Needs Reqdiff Test\n"
        "==================\n\n"
        ".. toctree::\n"
        "   :maxdepth: 2\n\n"
        "   reqdiff/index\n",
        encoding="utf-8",
    )

    warning_path = tmp_path / "sphinx-warnings.txt"
    exit_code = build_main(
        [
            "-b",
            "html",
            "-W",
            "-n",
            "-d",
            str(build_dir / ".doctrees"),
            "-w",
            str(warning_path),
            "-c",
            str(source_dir),
            str(source_dir),
            str(build_dir / "html"),
        ]
    )

    assert exit_code == 0
    assert (build_dir / "html" / "index.html").exists()
    diff_html = (build_dir / "html" / "reqdiff" / "valid__system" / "diff.html").read_text(
        encoding="utf-8"
    )
    assert 'class="nrd_link">REQ_NOT_EXPORTED</span>' in diff_html
    combined = warning_path.read_text(encoding="utf-8") if warning_path.exists() else ""
    assert "could not be imported" not in combined
    assert "unknown outgoing link" not in combined


def test_index_lists_only_files_with_real_differences(
    tmp_path: Path, fixture_roots: tuple[Path, Path]
) -> None:
    baseline, candidate = fixture_roots
    output_dir = tmp_path / "reports"

    result = compare_directories(baseline, candidate)
    write_reports(result, output_dir)

    index_rst = (output_dir / "index.rst").read_text(encoding="utf-8")

    assert "* ``valid/system.json`` (hinzugekommen: 1, geändert: 1, entfernt: 1)" in index_rst
    assert "valid__system/diff" in index_rst
    assert "left_only__legacy/diff" not in index_rst
    assert "right_only__future/diff" not in index_rst
    assert "invalid__broken/diff" not in index_rst


def test_index_places_requirements_diff_section_at_end(
    tmp_path: Path, fixture_roots: tuple[Path, Path]
) -> None:
    baseline, candidate = fixture_roots
    output_dir = tmp_path / "reports"

    result = compare_directories(baseline, candidate)
    write_reports(result, output_dir)

    index_rst = (output_dir / "index.rst").read_text(encoding="utf-8")

    changed_pos = index_rst.find("Geänderte Dateien")
    diff_pos = index_rst.find("Anforderungsdifferenzen")

    assert changed_pos != -1
    assert diff_pos > changed_pos
