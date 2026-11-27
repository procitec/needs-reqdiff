from __future__ import annotations

import json
import re
from collections import ChainMap, Counter, deque
from collections.abc import Iterable, Mapping
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .differ import DEFAULT_COMPARE_FIELDS, RELATED_LINK_FIELDS, fieldwise_changes
from .models import ComparisonResult, FileComparison

NEEDTABLE_COLUMNS = "id;title;status;type;tags;links;docname"
ADDED_PREFIX = "ADD_"
REMOVED_PREFIX = "REM_"
CHANGED_OLD_PREFIX = "OLD_"
CHANGED_NEW_PREFIX = "NEW_"
CHANGED_OLD_FOCUS_TAG = "diff_changed_old_focus"
CHANGED_NEW_FOCUS_TAG = "diff_changed_new_focus"
RELATED_CONTENT_FIELDS = ("content", "description")
NEED_ROLE_PATTERN = re.compile(r":need:`([^`]+)`")
LINK_LIST_TOKEN_PATTERN = re.compile(r"<<.*?(?:>>|$)|<\{.*?(?:\}>|$)|[\[\],;|]", re.DOTALL)
LINE_BREAK_LABELS = {"\n": "LF", "\r\n": "CRLF", "\r": "CR"}
LINE_BREAK_PATTERN = r"\r\n|\n|\r"
DiffCell = list[tuple[str, str | None]]


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


def _split_link_string(value: str) -> list[str]:
    """Split a scalar link list, keeping separators between references."""
    parts = []
    start = depth = 0
    for match in LINK_LIST_TOKEN_PATTERN.finditer(value):
        token = match[0]
        if token == "[":
            depth += 1
        elif token == "]":
            depth = max(depth - 1, 0)
        elif depth == 0 and token in {",", ";", "|"}:
            parts.extend((value[start : match.start()], token))
            start = match.end()
    parts.append(value[start:])
    return parts


def _iter_related_need_ids(need: dict[str, Any]) -> list[str]:
    related_ids: list[str] = []
    for field in RELATED_LINK_FIELDS:
        raw_values = need.get(field, [])
        if isinstance(raw_values, str):
            raw_values = _split_link_string(raw_values)[::2]
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


def _include_link_context(
    source_needs: Mapping[str, dict[str, Any]],
    snapshot: dict[str, dict[str, Any]],
    exported_needs: Iterable[dict[str, Any]],
    report_prefixes: dict[str, str],
    prefix: str,
) -> None:
    """Include targets that would otherwise be missing from this side of the report."""
    pending = deque(exported_needs)
    while pending:
        for related_id in _iter_related_need_ids(pending.popleft()):
            need_id, _ = _split_part_reference(related_id)
            if need_id in report_prefixes or need_id not in source_needs:
                continue
            target = source_needs[need_id]
            snapshot[need_id] = target
            report_prefixes[need_id] = prefix
            pending.append(target)


def _replace_need_roles_in_text(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        return f":nrd_link:`{target}`"

    return NEED_ROLE_PATTERN.sub(replace, text)


def _rewrite_related_reference(raw_value: str, target_ids: Mapping[str, str]) -> str:
    parent_id, _ = _split_part_reference(raw_value)
    target_id = target_ids.get(parent_id)
    if target_id is None:
        return raw_value

    start = len(raw_value) - len(raw_value.lstrip())
    return f"{raw_value[:start]}{target_id}{raw_value[start + len(parent_id) :]}"


def _rewrite_need_for_export(
    need: dict[str, Any],
    export_id: str,
    target_ids: Mapping[str, str],
) -> dict[str, Any]:
    copied = json.loads(json.dumps(need, ensure_ascii=False))
    copied["id"] = export_id

    for field in RELATED_LINK_FIELDS:
        raw_values = copied.get(field)
        if isinstance(raw_values, str):
            parts = _split_link_string(raw_values)
            parts[::2] = [_rewrite_related_reference(part, target_ids) for part in parts[::2]]
            copied[field] = "".join(parts)
        elif isinstance(raw_values, list):
            copied[field] = [
                _rewrite_related_reference(value, target_ids) if isinstance(value, str) else value
                for value in raw_values
            ]
    for field in RELATED_CONTENT_FIELDS:
        text = copied.get(field)
        if isinstance(text, str):
            copied[field] = _replace_need_roles_in_text(text)

    return copied


def _prepare_export_needs(
    source_needs: dict[str, dict[str, Any]],
    export_ids: dict[str, str],
    other_local_ids: dict[str, str] | None = None,
    report_ids: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    target_ids = ChainMap(export_ids, other_local_ids or {}, report_ids or {})
    return {
        export_ids[need_id]: _rewrite_need_for_export(need, export_ids[need_id], target_ids)
        for need_id, need in source_needs.items()
    }


def _allocate_export_ids(
    exports: dict[tuple[Path, str], dict[str, dict[str, Any]]],
) -> dict[tuple[Path, str], dict[str, str]]:
    # Keep the original export ID for one occurrence, preferring a native need.
    owners: dict[str, tuple[Path, str]] = {}
    for key, needs in exports.items():
        _, prefix = key
        for need_id, need in needs.items():
            base_id = f"{prefix}{need_id}"
            owner = owners.get(base_id)
            if owner is None or (
                exports[owner][need_id].get("is_import") is True
                and need.get("is_import") is not True
            ):
                owners[base_id] = key

    used_ids = set(owners)
    allocated: dict[tuple[Path, str], dict[str, str]] = {}
    for key, needs in exports.items():
        path, prefix = key
        module = re.sub(r"[^A-Z0-9_]+", "_", _slugify_path(path).upper())
        ids: dict[str, str] = {}
        allocated[key] = ids
        for need_id in needs:
            base_id = f"{prefix}{need_id}"
            if owners[base_id] == key:
                ids[need_id] = base_id
                continue
            qualified_id = f"{base_id}__{module}"
            export_id = qualified_id
            number = 2
            while export_id in used_ids:
                export_id = f"{qualified_id}_{number}"
                number += 1
            ids[need_id] = export_id
            used_ids.add(export_id)
    return allocated


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
    return re.findall(rf"{LINE_BREAK_PATTERN}|[^\S\r\n]+|\w+|[^\w\s]", text)


def _display_labels(comparison: FileComparison) -> tuple[str, str]:
    old_version = comparison.old_version or "reference"
    new_version = comparison.new_version or "current"

    if old_version != new_version:
        return old_version, new_version

    return f"{old_version}-reference", f"{new_version}-current"


def _highlight_chunk(chunk: str, break_class: str, text_class: str) -> DiffCell:
    segments: DiffCell = []
    for index, part in enumerate(re.split(rf"({LINE_BREAK_PATTERN})", chunk)):
        if index % 2:
            segments.append((f"↵ {LINE_BREAK_LABELS[part]}", break_class))
            segments.append((part, None))
        elif part:
            leading = len(part) - len(part.lstrip())
            end = len(part.rstrip())
            segments.append((part[:leading], None))
            if end > leading:
                segments.extend(((part[leading:end], text_class), (part[end:], None)))
    return segments


def _highlight_text_diff(old_text: str, new_text: str) -> tuple[DiffCell, DiffCell]:
    old_tokens = _split_diff_tokens(old_text)
    new_tokens = _split_diff_tokens(new_text)
    matcher = SequenceMatcher(a=old_tokens, b=new_tokens)
    old_rendered: DiffCell = []
    new_rendered: DiffCell = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_chunk = "".join(old_tokens[i1:i2])
        new_chunk = "".join(new_tokens[j1:j2])
        if tag == "equal":
            old_rendered.append((old_chunk, None))
            new_rendered.append((new_chunk, None))
        else:
            old_rendered.extend(_highlight_chunk(old_chunk, "nrd_del", "nrd_del"))
            text_class = "nrd_ins" if tag == "insert" else "nrd_changed"
            new_rendered.extend(_highlight_chunk(new_chunk, "nrd_ins", text_class))

    return old_rendered, new_rendered


def _highlight_list_diff(old_values: list[Any], new_values: list[Any]) -> tuple[DiffCell, DiffCell]:
    old_items = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in old_values]
    new_items = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in new_values]

    def render(items: list[str], other_items: list[str], css_class: str) -> DiffCell:
        matches = Counter(other_items)
        segments: DiffCell = [("[", None)]
        for index, item in enumerate(items):
            if index:
                segments.append((", ", None))
            if matches[item] > 0:
                matches[item] -= 1
                segments.append((item, None))
            else:
                segments.append((item, css_class))
        segments.append(("]", None))
        return segments

    return render(old_items, new_items, "nrd_del"), render(new_items, old_items, "nrd_ins")


def _render_side_by_side_block(
    comparison: FileComparison, need_id: str, new_export_id: str, compare_fields: tuple[str, ...]
) -> str:
    assert comparison.diff is not None
    old_label, new_label = _display_labels(comparison)

    old_need = comparison.diff.changed_old[need_id]
    new_need = comparison.diff.changed_new[need_id]
    rows = []
    for field, old_value, new_value in fieldwise_changes(old_need, new_need, compare_fields):
        if isinstance(old_value, str) and isinstance(new_value, str):
            old_cell, new_cell = _highlight_text_diff(old_value, new_value)
        elif isinstance(old_value, list) and isinstance(new_value, list):
            old_cell, new_cell = _highlight_list_diff(old_value, new_value)
        else:
            old_cell = [(_format_table_value(old_value), None)]
            new_cell = [(_format_table_value(new_value), None)]
        rows.append((field, old_cell, new_cell))

    if not rows:
        rows = [("(keine relevanten Änderungen)", [], [])]

    title = need_id
    lines = [
        *_heading(title, "^"),
        "",
        f"Siehe Anhang: :need:`{new_export_id}`",
        "",
        ".. nrd-diff-table::",
        "",
        f'   {{"headers": {json.dumps(["Feld", old_label, new_label])}, "rows": [',
        ",\n".join(f"     {json.dumps(row)}" for row in rows),
        "   ]}",
    ]
    lines.append("")
    return "\n".join(lines)


def _render_report_rst(
    comparison: FileComparison, new_snapshot_ids: dict[str, str], compare_fields: tuple[str, ...]
) -> str:
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
        '   :filter: c.this_doc() and "diff_added" in tags',
        f"   :columns: {NEEDTABLE_COLUMNS}",
        "   :style: table",
        "   :class: need_rd",
        "   :filter_warning: Keine Anforderungen hinzugekommen",
        "",
        *_heading("Geänderte Anforderungen", "-"),
        "",
        ".. needtable::",
        f'   :filter: c.this_doc() and "{CHANGED_NEW_FOCUS_TAG}" in tags',
        f"   :columns: {NEEDTABLE_COLUMNS}",
        "   :style: datatable",
        "   :class: need_rd",
        "",
    ]

    if changed_ids:
        for need_id in changed_ids:
            lines.append(
                _render_side_by_side_block(
                    comparison, need_id, new_snapshot_ids[need_id], compare_fields
                )
            )
    else:
        lines.extend(["Keine geänderten Anforderungen.", ""])

    lines.extend(
        [
            *_heading("Entfernte Anforderungen", "-"),
            "",
            ".. needtable::",
            '   :filter: c.this_doc() and "diff_removed" in tags',
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
                f"* ``{error.relative_path.as_posix()}`` "
                f"({error.side}, {error.error_type}): {error.message}"
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

    changed_files = result.changed_files
    old_prefixes: dict[str, str] = {}
    new_prefixes: dict[str, str] = {}
    old_source: dict[str, dict[str, Any]] = {}
    new_source: dict[str, dict[str, Any]] = {}
    old_snapshots: dict[Path, dict[str, dict[str, Any]]] = {}
    new_snapshots: dict[Path, dict[str, dict[str, Any]]] = {}

    for comparison in result.comparable_files:
        assert comparison.old_data is not None
        assert comparison.new_data is not None
        assert comparison.old_version is not None
        assert comparison.new_version is not None
        old_source.update(comparison.old_data["versions"][comparison.old_version]["needs"])
        new_source.update(comparison.new_data["versions"][comparison.new_version]["needs"])

    for comparison in changed_files:
        assert comparison.diff is not None
        assert comparison.old_data is not None
        assert comparison.new_data is not None
        assert comparison.old_version is not None
        assert comparison.new_version is not None
        old_prefixes.update(dict.fromkeys(comparison.diff.removed, REMOVED_PREFIX))
        old_prefixes.update(dict.fromkeys(comparison.diff.changed_old, CHANGED_OLD_PREFIX))
        new_prefixes.update(dict.fromkeys(comparison.diff.added, ADDED_PREFIX))
        new_prefixes.update(dict.fromkeys(comparison.diff.changed_new, CHANGED_NEW_PREFIX))

        path = comparison.relative_path
        old_snapshots[path] = _collect_linked_needs(
            comparison.old_data["versions"][comparison.old_version]["needs"],
            set(comparison.diff.changed_old),
        )
        new_snapshots[path] = _collect_linked_needs(
            comparison.new_data["versions"][comparison.new_version]["needs"],
            set(comparison.diff.changed_new),
        )

    # Reuse context already present in any snapshot, keeping ADD_/REM_ as the
    # primary targets for added or removed needs referenced from other exports.
    for snapshot in old_snapshots.values():
        for need_id in snapshot:
            old_prefixes.setdefault(need_id, CHANGED_OLD_PREFIX)
    for snapshot in new_snapshots.values():
        for need_id in snapshot:
            new_prefixes.setdefault(need_id, CHANGED_NEW_PREFIX)

    exports: dict[tuple[Path, str], dict[str, dict[str, Any]]] = {}
    for comparison in changed_files:
        assert comparison.diff is not None
        path = comparison.relative_path
        old_snapshot = old_snapshots[path]
        new_snapshot = new_snapshots[path]
        _include_link_context(
            old_source,
            old_snapshot,
            [*comparison.diff.removed.values(), *old_snapshot.values()],
            old_prefixes,
            CHANGED_OLD_PREFIX,
        )
        _include_link_context(
            new_source,
            new_snapshot,
            [*comparison.diff.added.values(), *new_snapshot.values()],
            new_prefixes,
            CHANGED_NEW_PREFIX,
        )
        exports[path, ADDED_PREFIX] = comparison.diff.added
        exports[path, REMOVED_PREFIX] = comparison.diff.removed
        exports[path, CHANGED_OLD_PREFIX] = old_snapshot
        exports[path, CHANGED_NEW_PREFIX] = new_snapshot

    export_ids = _allocate_export_ids(exports)
    old_ids = {need_id: f"{prefix}{need_id}" for need_id, prefix in old_prefixes.items()}
    new_ids = {need_id: f"{prefix}{need_id}" for need_id, prefix in new_prefixes.items()}

    for comparison in changed_files:
        assert comparison.diff is not None
        assert comparison.old_data is not None
        assert comparison.new_data is not None
        assert comparison.old_version is not None
        assert comparison.new_version is not None

        path = comparison.relative_path
        target_dir = output_dir / _slugify_path(path)
        target_dir.mkdir(parents=True, exist_ok=True)
        added_ids = export_ids[path, ADDED_PREFIX]
        removed_ids = export_ids[path, REMOVED_PREFIX]
        old_snapshot_ids = export_ids[path, CHANGED_OLD_PREFIX]
        new_snapshot_ids = export_ids[path, CHANGED_NEW_PREFIX]

        changed_old_visible = _needs_with_focus_tags(
            old_snapshots[path],
            set(comparison.diff.changed_old),
            CHANGED_OLD_FOCUS_TAG,
        )
        changed_new_visible = _needs_with_focus_tags(
            new_snapshots[path],
            set(comparison.diff.changed_new),
            CHANGED_NEW_FOCUS_TAG,
        )

        added_prepared = _prepare_export_needs(
            comparison.diff.added, added_ids, new_snapshot_ids, new_ids
        )
        removed_prepared = _prepare_export_needs(
            comparison.diff.removed, removed_ids, old_snapshot_ids, old_ids
        )
        changed_old_prepared = _prepare_export_needs(
            changed_old_visible, old_snapshot_ids, removed_ids, old_ids
        )
        changed_new_prepared = _prepare_export_needs(
            changed_new_visible, new_snapshot_ids, added_ids, new_ids
        )

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
            _render_report_rst(comparison, new_snapshot_ids, compare_fields), encoding="utf-8"
        )

    index_path = output_dir / "index.rst"
    index_path.write_text(_render_index_rst(result), encoding="utf-8")
    return index_path
