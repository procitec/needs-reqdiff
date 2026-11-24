from __future__ import annotations

from needs_reqdiff.rst_writer import (
    _collect_linked_needs,
    _prepare_export_needs,
    _replace_need_roles_in_text,
)


def test_collect_linked_needs_resolves_need_part_parent() -> None:
    source_needs = {
        "REQ_ABC_1000": {
            "id": "REQ_ABC_1000",
            "implements": ["REQ_MODULE_3210.1"],
            "links": [],
            "testet": [],
            "parent_needs": [],
        },
        "REQ_MODULE_3210": {
            "id": "REQ_MODULE_3210",
            "links": [],
            "implements": [],
            "testet": [],
            "parent_needs": [],
            "parts": {
                "1": {
                    "id": "1",
                    "content": "Teil 1",
                }
            },
        },
    }

    collected = _collect_linked_needs(source_needs, {"REQ_ABC_1000"})

    assert set(collected.keys()) == {"REQ_ABC_1000", "REQ_MODULE_3210"}


def test_prepare_export_needs_prefixes_part_and_regular_links() -> None:
    source_needs = {
        "REQ_ABC_1000": {
            "id": "REQ_ABC_1000",
            "links": ["REQ_OTHER_1"],
            "implements": ["REQ_MODULE_3210.1"],
            "testet": ["TEST_7[status=='open']"],
            "parent_needs": [],
            "tags": [],
        },
        "REQ_OTHER_1": {
            "id": "REQ_OTHER_1",
            "links": [],
            "implements": [],
            "testet": [],
            "parent_needs": [],
            "tags": [],
        },
        "REQ_MODULE_3210": {
            "id": "REQ_MODULE_3210",
            "links": [],
            "implements": [],
            "testet": [],
            "parent_needs": [],
            "tags": [],
        },
        "TEST_7": {
            "id": "TEST_7",
            "links": [],
            "implements": [],
            "testet": [],
            "parent_needs": [],
            "tags": [],
        },
    }

    prepared = _prepare_export_needs(source_needs, "OLD_")
    need = prepared["OLD_REQ_ABC_1000"]

    assert need["id"] == "OLD_REQ_ABC_1000"
    assert need["links"] == ["OLD_REQ_OTHER_1"]
    assert need["implements"] == ["OLD_REQ_MODULE_3210.1"]
    assert need["testet"] == ["OLD_TEST_7[status=='open']"]


def test_replace_need_roles_in_text() -> None:
    text = "wie unter :need:`REQ_APP_1010` und :need:`REQ_MODULE_3210.1`"
    result = _replace_need_roles_in_text(text)
    assert result == "wie unter :nrd_link:`REQ_APP_1010` und :nrd_link:`REQ_MODULE_3210.1`"
