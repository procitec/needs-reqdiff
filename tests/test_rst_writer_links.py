from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from sphinx.cmd.build import build_main

from needs_reqdiff.api import generate_reqdiff
from needs_reqdiff.loader import load_sphinx_needs_json


@pytest.mark.parametrize(
    ("testet", "existing_target"),
    [
        (["REQ_INMODULE_2005", "REQ_INMODULE_2010"], False),
        ("REQ_INMODULE_2005, REQ_INMODULE_2010", False),
        ("REQ_INMODULE_2005; REQ_INMODULE_2010", False),
        ("REQ_INMODULE_2005 | REQ_INMODULE_2010", False),
        (["REQ_INMODULE_2005"], True),
    ],
)
def test_added_testet_links_resolve_in_sphinx(
    tmp_path: Path, testet: str | list[str], existing_target: bool
) -> None:
    baseline, candidate, source = (tmp_path / name for name in ("base", "candidate", "source"))
    _write_needs(baseline, "a_tests.json", "1.0", {})
    requirements = {"REQ_INMODULE_2005": {"type": "req", "title": "First requirement"}}
    if existing_target:
        _write_needs(baseline, "b_requirements.json", "1.0", requirements)
    else:
        _write_needs(baseline, "b_requirements.json", "1.0", {})
        requirements["REQ_INMODULE_2010"] = {"type": "req", "title": "Second requirement"}
    _write_needs(
        candidate,
        "a_tests.json",
        "2.0",
        {
            "TEST_MYMODULE_1065": {
                "type": "test",
                "title": "New test",
                "testet": testet,
            }
        },
    )
    _write_needs(candidate, "b_requirements.json", "2.0", requirements)

    generate_reqdiff(baseline, candidate, source)
    (source / "conf.py").write_text(
        "extensions = ['sphinx_needs', 'needs_reqdiff']\n"
        "needs_types = ["
        "{'directive': 'req', 'title': 'Requirement', 'prefix': 'REQ_'},"
        "{'directive': 'test', 'title': 'Test', 'prefix': 'TEST_'}]\n"
        "needs_extra_links = [{'option': 'testet', 'incoming': 'tested by', "
        "'outgoing': 'tests'}]\n"
        "suppress_warnings = ['needs.deprecated']\n",
        encoding="utf-8",
    )
    build_dir = tmp_path / "html"
    assert build_main(["-b", "html", "-W", "-n", str(source), str(build_dir)]) == 0
    html = (build_dir / "a_tests" / "diff.html").read_text(encoding="utf-8")
    if existing_target:
        assert 'href="#NEW_REQ_INMODULE_2005"' in html
    else:
        assert "../b_requirements/diff.html#ADD_REQ_INMODULE_2005" in html
        assert "../b_requirements/diff.html#ADD_REQ_INMODULE_2010" in html


def _write_needs(
    root: Path, filename: str, version: str, needs: dict[str, dict[str, object]]
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    export = {
        "current_version": version,
        "versions": {
            version: {
                "needs": {need_id: {"id": need_id, **need} for need_id, need in needs.items()}
            }
        },
    }
    (root / filename).write_text(json.dumps(export), encoding="utf-8")


@pytest.mark.parametrize("builder", ["html", "singlehtml"])
def test_diff_tables_only_list_needs_from_their_json_file(tmp_path: Path, builder: str) -> None:
    baseline, candidate, source = (tmp_path / name for name in ("base", "candidate", "source"))
    for module in ("mymodule", "othermodule"):
        req = f"REQ_{module.upper()}"
        _write_needs(
            baseline,
            f"{module}.json",
            "1.0",
            {
                f"{req}_1000": {"type": "req", "title": "Old title"},
                f"{req}_3000": {"type": "req", "title": "Removed requirement"},
            },
        )
        _write_needs(
            candidate,
            f"{module}.json",
            "2.0",
            {
                f"{req}_1000": {"type": "req", "title": "New title"},
                f"{req}_2000": {"type": "req", "title": "Added requirement"},
            },
        )

    generate_reqdiff(baseline, candidate, source)
    (source / "conf.py").write_text(
        "extensions = ['sphinx_needs', 'needs_reqdiff']\n", encoding="utf-8"
    )
    build_dir = tmp_path / "build"
    assert build_main(["-b", builder, "-W", "-n", str(source), str(build_dir)]) == 0

    pages = (
        ["index.html"]
        if builder == "singlehtml"
        else [
            "mymodule/diff.html",
            "othermodule/diff.html",
        ]
    )
    listed_ids = []
    for page in pages:
        html = (build_dir / page).read_text(encoding="utf-8")
        tables = re.findall(
            r'<table\b[^>]*class="[^"]*\bneed_rd\b[^"]*"[^>]*>.*?</table>', html, re.S
        )
        listed_ids.extend(
            set(re.findall(r"(?:ADD|NEW|REM)_REQ_[A-Z]+_\d+", table)) for table in tables
        )

    assert listed_ids == [
        {"ADD_REQ_MYMODULE_2000"},
        {"NEW_REQ_MYMODULE_1000"},
        {"REM_REQ_MYMODULE_3000"},
        {"ADD_REQ_OTHERMODULE_2000"},
        {"NEW_REQ_OTHERMODULE_1000"},
        {"REM_REQ_OTHERMODULE_3000"},
    ]


def test_changed_import_appears_in_both_module_reports(tmp_path: Path) -> None:
    baseline, candidate, source = (tmp_path / name for name in ("base", "candidate", "source"))
    need_id = "REQ_SHARED_1000"
    for root, version, title in (
        (baseline, "1.0", "Reference title"),
        (candidate, "2.0", "Comparison title"),
    ):
        for module, is_import in (("a_consumer", True), ("z_origin", False)):
            needs: dict[str, dict[str, object]] = {
                need_id: {"type": "req", "title": title, "is_import": is_import}
            }
            if is_import and root == candidate:
                needs["REQ_LOCAL_2000"] = {"type": "req", "title": "Local", "links": [need_id]}
            _write_needs(root, f"{module}.json", version, needs)

    generate_reqdiff(baseline, candidate, source)
    (source / "conf.py").write_text(
        "extensions = ['sphinx_needs', 'needs_reqdiff']\n", encoding="utf-8"
    )
    output = tmp_path / "html"
    assert build_main(["-b", "singlehtml", "-W", "-n", str(source), str(output)]) == 0

    _, _, origin = load_sphinx_needs_json(source / "z_origin" / "changed_new.json")
    assert set(origin) == {f"NEW_{need_id}"}
    _, _, consumer = load_sphinx_needs_json(source / "a_consumer" / "changed_new.json")
    (imported_id,) = consumer
    assert imported_id != f"NEW_{need_id}"
    assert consumer[imported_id]["is_import"] is True
    _, _, added = load_sphinx_needs_json(source / "a_consumer" / "added.json")
    assert added["ADD_REQ_LOCAL_2000"]["links"] == [imported_id]

    html = (output / "index.html").read_text(encoding="utf-8")
    tables = re.findall(r'<table\b[^>]*class="[^"]*\bneed_rd\b[^"]*"[^>]*>.*?</table>', html, re.S)
    listed_ids = [
        need
        for table in tables
        for need in re.findall(r'<td class="needs_id"><p><a [^>]+>([^<]+)</a>', table)
    ]
    assert listed_ids == ["ADD_REQ_LOCAL_2000", imported_id, f"NEW_{need_id}"]
    assert f":need:`{imported_id}`" in (source / "a_consumer" / "diff.rst").read_text(
        encoding="utf-8"
    )


def test_added_tests_link_to_added_requirements_in_both_exports(tmp_path: Path) -> None:
    baseline, candidate, output = (tmp_path / name for name in ("base", "candidate", "report"))
    # The test report is written before its cross-file target report.
    _write_needs(baseline, "a_tests.json", "1.0", {})
    _write_needs(baseline, "b_requirements.json", "1.0", {"REQ_EXISTING": {}})
    _write_needs(
        candidate,
        "a_tests.json",
        "2.0",
        {
            "TEST_NEW": {
                "implements": ["REQ_FEATURE", "REQ_LOCAL", "REQ_EXISTING"],
                "required": ["REQ_BASE.part", "REQ_LOCAL"],
            },
            "REQ_LOCAL": {},
        },
    )
    _write_needs(
        candidate,
        "b_requirements.json",
        "2.0",
        {
            "REQ_FEATURE": {"implements": "REQ_BASE"},
            "REQ_BASE": {"parts": {"part": {"id": "part", "content": "Basisanforderung"}}},
            "REQ_EXISTING": {},
        },
    )

    generate_reqdiff(baseline, candidate, output)

    _, _, tests = load_sphinx_needs_json(output / "a_tests" / "added.json")
    _, _, requirements = load_sphinx_needs_json(output / "b_requirements" / "added.json")
    assert tests["ADD_TEST_NEW"]["id"] == "ADD_TEST_NEW"
    assert tests["ADD_TEST_NEW"]["implements"] == [
        "ADD_REQ_FEATURE",
        "ADD_REQ_LOCAL",
        "NEW_REQ_EXISTING",
    ]
    assert tests["ADD_TEST_NEW"]["required"] == ["ADD_REQ_BASE.part", "ADD_REQ_LOCAL"]
    assert tests["ADD_REQ_LOCAL"]["id"] == "ADD_REQ_LOCAL"
    assert requirements["ADD_REQ_FEATURE"]["implements"] == "ADD_REQ_BASE"
    assert requirements["ADD_REQ_BASE"]["parts"]["part"]["id"] == "part"


def test_link_context_reuses_changed_snapshots_and_follows_missing_targets(tmp_path: Path) -> None:
    baseline, candidate, output = (tmp_path / name for name in ("base", "candidate", "report"))
    for root, version, test_id, title in (
        (baseline, "1.0", "TEST_REMOVED", "Old requirement title"),
        (candidate, "2.0", "TEST_ADDED", "New requirement title"),
    ):
        _write_needs(
            root, "tests.json", version, {test_id: {"testet": ["REQ_CHANGED", "REQ_BASE"]}}
        )
        _write_needs(
            root,
            "changed.json",
            version,
            {"REQ_CHANGED": {"title": title, "required": ["REQ_BASE"]}},
        )
        _write_needs(
            root, "context.json", version, {"REQ_BASE": {"required": ["REQ_LEAF"]}, "REQ_LEAF": {}}
        )

    generate_reqdiff(baseline, candidate, output)

    for snapshot_name, snapshot_prefix, delta_name, delta_id in (
        ("changed_old.json", "OLD_", "removed.json", "REM_TEST_REMOVED"),
        ("changed_new.json", "NEW_", "added.json", "ADD_TEST_ADDED"),
    ):
        _, _, tests = load_sphinx_needs_json(output / "tests" / delta_name)
        _, _, snapshot = load_sphinx_needs_json(output / "changed" / snapshot_name)
        _, _, duplicate_snapshot = load_sphinx_needs_json(output / "tests" / snapshot_name)
        assert tests[delta_id]["testet"] == [
            f"{snapshot_prefix}REQ_CHANGED",
            f"{snapshot_prefix}REQ_BASE",
        ]
        assert snapshot[f"{snapshot_prefix}REQ_CHANGED"]["required"] == [
            f"{snapshot_prefix}REQ_BASE"
        ]
        assert snapshot[f"{snapshot_prefix}REQ_BASE"]["required"] == [f"{snapshot_prefix}REQ_LEAF"]
        assert snapshot[f"{snapshot_prefix}REQ_BASE"]["tags"] == []
        assert duplicate_snapshot == {}


def test_required_only_change_appears_in_both_snapshots(tmp_path: Path) -> None:
    baseline, candidate, output = (tmp_path / name for name in ("base", "candidate", "report"))
    requirements = {"REQ_BASE_A": {}, "REQ_BASE_B": {}}
    _write_needs(
        baseline, "system.json", "1.0", {**requirements, "TEST_1": {"required": ["REQ_BASE_A"]}}
    )
    _write_needs(
        candidate, "system.json", "2.0", {**requirements, "TEST_1": {"required": ["REQ_BASE_B"]}}
    )

    generate_reqdiff(baseline, candidate, output)

    _, _, old = load_sphinx_needs_json(output / "system" / "changed_old.json")
    _, _, new = load_sphinx_needs_json(output / "system" / "changed_new.json")
    assert set(old) == {"OLD_TEST_1", "OLD_REQ_BASE_A"}
    assert set(new) == {"NEW_TEST_1", "NEW_REQ_BASE_B"}
    assert old["OLD_TEST_1"]["required"] == ["OLD_REQ_BASE_A"]
    assert new["NEW_TEST_1"]["required"] == ["NEW_REQ_BASE_B"]
