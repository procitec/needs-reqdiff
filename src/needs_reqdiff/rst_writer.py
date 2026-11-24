from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .differ import DEFAULT_COMPARE_FIELDS, fieldwise_changes
from .models import ComparisonResult, FileComparison

NEEDTABLE_COLUMNS = "id;title;status;type;tags;links;docname"
ADDED_PREFIX = "ADD_"
REMOVED_PREFIX = "REM_"
CHANGED_OLD_PREFIX = "OLD_"
CHANGED_NEW_PREFIX = "NEW_"
CHANGED_OLD_FOCUS_TAG = "diff_changed_old_focus"
CHANGED_NEW_FOCUS_TAG = "diff_changed_new_focus"
RELATED_LINK_FIELDS = ("links", "implements", "testet", "parent_needs")
RELATED_CONTENT_FIELDS = ("content", "description")
NEED_ROLE_PATTERN = re.compile(r":need:`([^`]+)`")


def _slugify_path(relative_path: Path) -> str:
    return "__".join(relative_path.with_suffix("").parts)


def _heading(title: str, adornment: str, offset: int = 0, guarded: bool = False) -> list[str]:
    if guarded:
        return [adornment * (len(title) + offset), title, adornment * (len(title) + offset)]
    else:
        return [title, adornment * (len(title) + offset)]


def _build_export_template(
    source_data: dict[str, Any],
    version: str,
    needs_subset: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "created": source_data.get("created", ""),
        "current_version": version,
        "project": source_data.get("project", "needs-reqdiff"),
        "versions": {
            version: {
                "created": source_data.get("versions", {}).get(version, {}).get("created", ""),
                "filters": {},
                "filters_amount": 0,
                "needs": needs_subset,
                "needs_amount": len(needs_subset),
            }
        },
    }


def _needs_with_focus_tags(
    source_needs: dict[str, dict[str, Any]],
    focus_ids: set[str],
    focus_tag: str,
) -> dict[str, dict[str, Any]]:
    tagged: dict[str, dict[str, Any]] = {}
    for need_id, need in source_needs.items():
        copied = json.loads(json.dumps(need, ensure_ascii=False))
        tags = copied.get("tags")
        if not isinstance(tags, list):
            tags = []
        else:
            tags = list(tags)
        if need_id in focus_ids and focus_tag not in tags:
            tags.append(focus_tag)
        copied["tags"] = tags
        tagged[need_id] = copied
    return tagged


def _normalize_related_need_id(value: str) -> str:
    return value.split("[", 1)[0].strip()


def _split_part_reference(value: str) -> tuple[str, str | None]:
    normalized = _normalize_related_need_id(value)
    if "." not in normalized:
        return normalized, None

    parent_id, part_id = normalized.rsplit(".", 1)
    if not parent_id or not part_id:
        return normalized, None

    return parent_id, part_id


def _iter_related_need_ids(need: dict[str, Any]) -> list[str]:
    related_ids: list[str] = []
    for field in RELATED_LINK_FIELDS:
        raw_values = need.get(field, [])
        if isinstance(raw_values, str):
            raw_values = [raw_values]
        if not isinstance(raw_values, list):
            continue
        for raw_value in raw_values:
            if not isinstance(raw_value, str):
                continue
            related_id = _normalize_related_need_id(raw_value)
            if related_id:
                related_ids.append(related_id)
    return related_ids


def _collect_linked_needs(
    source_needs: dict[str, dict[str, Any]], seed_ids: set[str]
) -> dict[str, dict[str, Any]]:
    seen: set[str] = set()
    queue = list(seed_ids)

    while queue:
        raw_need_id = queue.pop(0)
        parent_id, _part_id = _split_part_reference(raw_need_id)
        need_id = parent_id if parent_id in source_needs else raw_need_id

        if need_id in seen or need_id not in source_needs:
            continue

        need = source_needs[need_id]
        seen.add(need_id)

        for related_id in _iter_related_need_ids(need):
            related_parent_id, _related_part_id = _split_part_reference(related_id)
            candidate_id = related_parent_id if related_parent_id in source_needs else related_id
            if candidate_id not in seen and candidate_id in source_needs:
                queue.append(candidate_id)

    return {need_id: source_needs[need_id] for need_id in source_needs if need_id in seen}


def _replace_need_roles_in_text(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        return f":nrd_link:`{target}`"

    return NEED_ROLE_PATTERN.sub(replace, text)


def _prefix_related_reference(raw_value: str, prefix: str, available_need_ids: set[str]) -> str:
    if not isinstance(raw_value, str):
        return raw_value

    core, sep, suffix = raw_value.partition("[")
    normalized = core.strip()
    if not normalized:
        return raw_value

    parent_id, part_id = _split_part_reference(normalized)
    target_need_id = parent_id if part_id is not None else normalized

    if target_need_id not in available_need_ids:
        return raw_value

    prefixed = f"{prefix}{parent_id}" if part_id is not None else f"{prefix}{normalized}"
    if part_id is not None:
        prefixed = f"{prefixed}.{part_id}"

    if sep:
        return f"{prefixed}[{suffix}"
    return prefixed


def _prefix_need_for_export(
    need: dict[str, Any],
    prefix: str,
    available_need_ids: set[str],
) -> dict[str, Any]:
    copied = json.loads(json.dumps(need, ensure_ascii=False))
    need_id = copied.get("id")
    if isinstance(need_id, str):
        copied["id"] = f"{prefix}{need_id}"

    for field in RELATED_LINK_FIELDS:
        raw_values = copied.get(field)
        if isinstance(raw_values, str):
            copied[field] = _prefix_related_reference(raw_values, prefix, available_need_ids)
        elif isinstance(raw_values, list):
            copied[field] = [
                _prefix_related_reference(value, prefix, available_need_ids)
                if isinstance(value, str)
                else value
                for value in raw_values
            ]
    for field in RELATED_CONTENT_FIELDS:
        text = copied.get(field)
        if isinstance(text, str):
            copied[field] = _replace_need_roles_in_text(text)

    return copied


def _prepare_export_needs(
    source_needs: dict[str, dict[str, Any]],
    prefix: str,
) -> dict[str, dict[str, Any]]:
    available_need_ids = set(source_needs.keys())
    prepared: dict[str, dict[str, Any]] = {}
    for need_id, need in source_needs.items():
        prepared[f"{prefix}{need_id}"] = _prefix_need_for_export(need, prefix, available_need_ids)
    return prepared


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


def _format_table_value(value: object) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(item, ensure_ascii=False) for item in value) + "]"

    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    return str(value)


def _split_diff_tokens(text: str) -> list[str]:
    return re.findall(r"\s+|\S+", text)


def _display_labels(comparison: FileComparison) -> tuple[str, str]:
    old_version = comparison.old_version or "reference"
    new_version = comparison.new_version or "current"

    if old_version != new_version:
        return old_version, new_version

    return f"{old_version}-reference", f"{new_version}-current"


def _cell_lines(value: object) -> list[str]:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if not lines:
        return [""]
    return lines


def _grid_table(headers: list[str], rows: list[list[str]]) -> str:
    all_rows = [headers] + rows

    widths: list[int] = []
    for col_idx in range(len(headers)):
        max_width = 0
        for row in all_rows:
            for line in _cell_lines(row[col_idx]):
                max_width = max(max_width, len(line))
        widths.append(max_width)

    def sep(char: str = "-") -> str:
        return "+" + "+".join(char * (w + 2) for w in widths) + "+"

    def render_row(row: list[str]) -> list[str]:
        split_cells = [_cell_lines(cell) for cell in row]
        row_height = max(len(cell_lines) for cell_lines in split_cells)

        rendered_lines: list[str] = []
        for line_idx in range(row_height):
            parts = []
            for col_idx, cell_lines in enumerate(split_cells):
                cell_line = cell_lines[line_idx] if line_idx < len(cell_lines) else ""
                parts.append(f" {cell_line.ljust(widths[col_idx])} ")
            rendered_lines.append("|" + "|".join(parts) + "|")
        return rendered_lines

    lines: list[str] = []
    lines.append(sep("-"))
    lines.extend(render_row(headers))
    lines.append(sep("="))

    for row in rows:
        lines.extend(render_row(row))
        lines.append(sep("-"))

    return "\n".join(lines)


def _wrap_role(text: str, role: str) -> str:
    if not text:
        return ""
    if text.isspace():
        return text
    leading = text[: len(text) - len(text.lstrip())]
    trailing = text[len(text.rstrip()) :]
    core = text.strip()
    if not core:
        return text
    core = core.replace("`", r"\`")
    return f"{leading}:{role}:`{core}`{trailing}"


def _highlight_new_text(old_text: str, new_text: str) -> str:
    old_tokens = _split_diff_tokens(old_text)
    new_tokens = _split_diff_tokens(new_text)
    matcher = SequenceMatcher(a=old_tokens, b=new_tokens)
    rendered: list[str] = []

    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        chunk = "".join(new_tokens[j1:j2])
        if tag == "equal":
            rendered.append(chunk)
        elif tag == "insert":
            rendered.append(_wrap_role(chunk, "nrd_ins"))
        elif tag == "replace":
            rendered.append(_wrap_role(chunk, "nrd_changed"))
        elif tag == "delete":
            continue

    return "".join(rendered)


def _render_diff_value(old_value: Any, new_value: Any) -> str:
    if isinstance(old_value, str) and isinstance(new_value, str):
        return _format_table_value(_highlight_new_text(old_value, new_value))
    # return _format_value(new_value)
    return _format_table_value(new_value)


def _render_side_by_side_block(
    comparison: FileComparison, need_id: str, compare_fields: tuple[str, ...]
) -> str:
    assert comparison.diff is not None
    old_label, new_label = _display_labels(comparison)
    new_prefixed_id = f"{CHANGED_NEW_PREFIX}{need_id}"

    old_need = comparison.diff.changed_old[need_id]
    new_need = comparison.diff.changed_new[need_id]
    rows = []
    for field, old_value, new_value in fieldwise_changes(old_need, new_need, compare_fields):
        if field in RELATED_CONTENT_FIELDS:
            if isinstance(old_value, str):
                old_value = _replace_need_roles_in_text(old_value)
            if isinstance(new_value, str):
                new_value = _replace_need_roles_in_text(new_value)
        rows.append(
            [field, _format_table_value(old_value), _render_diff_value(old_value, new_value)]
        )

    if not rows:
        rows = [["(keine relevanten Änderungen)", "", ""]]

    title = need_id
    lines = [
        *_heading(title, "^"),
        "",
        f"Siehe Anhang: :need:`{new_prefixed_id}`",
        "",
        ".. table::",
        "   :class: need_rd_table",
        "",
    ]

    table_lines = _grid_table(["Feld", old_label, new_label], rows).splitlines()
    lines.extend([f"   {line}" if line else "" for line in table_lines])
    lines.append("")
    return "\n".join(lines)


def _render_report_rst(comparison: FileComparison, compare_fields: tuple[str, ...]) -> str:
    assert comparison.diff is not None
    title = comparison.relative_path.as_posix()
    changed_ids = sorted(comparison.diff.changed_new)
    old_label, new_label = _display_labels(comparison)
    lines = [
        *_heading(title, "=", offset=1),
        "",
        *_heading("Übersicht", "-"),
        "",
        f"* Referenzstand: ``{old_label}``",
        f"* Vergleichsstand: ``{new_label}``",
        f"* Neu hinzugekommen: {len(comparison.diff.added)}",
        f"* Geändert: {len(changed_ids)}",
        f"* Entfernt: {len(comparison.diff.removed)}",
        "",
        *_heading("Neu hinzugekommene Anforderungen", "-"),
        "",
        ".. needtable::",
        '   :filter: "diff_added" in tags',
        f"   :columns: {NEEDTABLE_COLUMNS}",
        "   :style: table",
        "   :class: need_rd",
        "   :filter_warning: Keine Anforderungen hinzugekommen",
        "",
        *_heading("Geänderte Anforderungen", "-"),
        "",
        ".. needtable::",
        f'   :filter: "{CHANGED_NEW_FOCUS_TAG}" in tags',
        f"   :columns: {NEEDTABLE_COLUMNS}",
        "   :style: datatable",
        "   :class: need_rd",
        "",
    ]

    if changed_ids:
        for need_id in changed_ids:
            lines.append(_render_side_by_side_block(comparison, need_id, compare_fields))
    else:
        lines.extend(["Keine geänderten Anforderungen.", ""])

    lines.extend(
        [
            *_heading("Entfernte Anforderungen", "-"),
            "",
            ".. needtable::",
            '   :filter: "diff_removed" in tags',
            f"   :columns: {NEEDTABLE_COLUMNS}",
            "   :style: table",
            "   :class: need_rd",
            "   :filter_warning: Keine Anforderung entfernt",
            "",
            *_heading("Anhang Anforderungen", "-"),
            "",
            ".. needimport:: added.json",
            # f"   :id_prefix: {ADDED_PREFIX}",
            "   :tags: diff_added",
            "",
            ".. needimport:: removed.json",
            # f"   :id_prefix: {REMOVED_PREFIX}",
            "   :tags: diff_removed",
            "",
            ".. needimport:: changed_old.json",
            # f"   :id_prefix: {CHANGED_OLD_PREFIX}",
            "   :hide:",
            "",
            ".. needimport:: changed_new.json",
            # f"   :id_prefix: {CHANGED_NEW_PREFIX}",
            "",
        ]
    )
    return "\n".join(lines)


def _render_index_rst(result: ComparisonResult) -> str:
    lines: list[str] = []

    lines.extend(
        [
            *_heading("Vergleich von Anforderungen", "=", guarded=True),
            "",
        ]
    )

    lines.extend(
        [
            *_heading("Dateien nur in Referenzstand", "="),
            "",
        ]
    )
    if result.left_only_files:
        lines.extend([f"* ``{path.as_posix()}``" for path in result.left_only_files])
    else:
        lines.append("Keine Dateien nur im Referenzstand gefunden.")

    lines.extend(
        [
            "",
            *_heading("Dateien nur im Vergleichsstand", "="),
            "",
        ]
    )
    if result.right_only_files:
        lines.extend([f"* ``{path.as_posix()}``" for path in result.right_only_files])
    else:
        lines.append("Keine Dateien nur im Vergleichsstand gefunden.")

    lines.extend(
        [
            "",
            *_heading("Dateien mit Fehlern", "="),
            "",
        ]
    )
    if result.errors:
        for error in result.errors:
            lines.append(
                f"* ``{error.relative_path.as_posix()}`` ({error.side}, {error.error_type}): {error.message}"
            )
    else:
        lines.append("Keine Dateien mit Fehlern gefunden.")

    lines.extend(
        [
            "",
            *_heading("Geänderte Dateien", "="),
            "",
        ]
    )
    if result.changed_files:
        for comparison in result.changed_files:
            assert comparison.diff is not None
            lines.append(
                f"* ``{comparison.relative_path.as_posix()}`` "
                f"(hinzugekommen: {len(comparison.diff.added)}, "
                f"geändert: {len(comparison.diff.changed_new)}, "
                f"entfernt: {len(comparison.diff.removed)})"
            )
    else:
        lines.append("Keine Dateien mit geänderten Anforderungen gefunden.")

    lines.extend(
        [
            "",
            *_heading("Anforderungsdifferenzen", "="),
            "",
        ]
    )
    if result.changed_files:
        lines.extend(
            [
                ".. toctree::",
                "   :maxdepth: 2",
                "",
            ]
        )
        for comparison in result.changed_files:
            lines.append(f"   {_slugify_path(comparison.relative_path)}/diff")
    else:
        lines.append("Keine Anforderungsdifferenzen gefunden.")

    lines.append("")
    return "\n".join(lines)


def write_reports(
    result: ComparisonResult,
    output_dir: Path,
    compare_fields: tuple[str, ...] = DEFAULT_COMPARE_FIELDS,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    for comparison in result.changed_files:
        assert comparison.diff is not None
        assert comparison.old_data is not None
        assert comparison.new_data is not None
        assert comparison.old_version is not None
        assert comparison.new_version is not None

        target_dir = output_dir / _slugify_path(comparison.relative_path)
        target_dir.mkdir(parents=True, exist_ok=True)

        old_source_needs = comparison.old_data["versions"][comparison.old_version]["needs"]
        new_source_needs = comparison.new_data["versions"][comparison.new_version]["needs"]

        changed_old_visible = _needs_with_focus_tags(
            _collect_linked_needs(old_source_needs, set(comparison.diff.changed_old)),
            set(comparison.diff.changed_old),
            CHANGED_OLD_FOCUS_TAG,
        )
        changed_new_visible = _needs_with_focus_tags(
            _collect_linked_needs(new_source_needs, set(comparison.diff.changed_new)),
            set(comparison.diff.changed_new),
            CHANGED_NEW_FOCUS_TAG,
        )

        added_prepared = _prepare_export_needs(comparison.diff.added, ADDED_PREFIX)
        removed_prepared = _prepare_export_needs(comparison.diff.removed, REMOVED_PREFIX)
        changed_old_prepared = _prepare_export_needs(changed_old_visible, CHANGED_OLD_PREFIX)
        changed_new_prepared = _prepare_export_needs(changed_new_visible, CHANGED_NEW_PREFIX)

        _write_json(
            target_dir / "added.json",
            _build_export_template(comparison.new_data, comparison.new_version, added_prepared),
        )
        _write_json(
            target_dir / "removed.json",
            _build_export_template(comparison.old_data, comparison.old_version, removed_prepared),
        )
        _write_json(
            target_dir / "changed_old.json",
            _build_export_template(
                comparison.old_data, comparison.old_version, changed_old_prepared
            ),
        )
        _write_json(
            target_dir / "changed_new.json",
            _build_export_template(
                comparison.new_data, comparison.new_version, changed_new_prepared
            ),
        )
        (target_dir / "diff.rst").write_text(
            _render_report_rst(comparison, compare_fields), encoding="utf-8"
        )

    index_path = output_dir / "index.rst"
    index_path.write_text(_render_index_rst(result), encoding="utf-8")
    return index_path
