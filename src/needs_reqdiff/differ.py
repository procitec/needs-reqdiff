from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .loader import InvalidNeedsJsonError, JsonParseError, FileScanner, load_sphinx_needs_json
from .models import ComparisonResult, FileComparison, FileLoadError, NeedDiff

DEFAULT_COMPARE_FIELDS = (
    "id",
    "title",
    "description",
    "content",
    "status",
    "type",
    "type_name",
    "tags",
    "links",
    "implements",
    "testet",
    "parent_needs",
    "docname",
    "sections",
)


def normalize_need(
    need: dict[str, Any], compare_fields: tuple[str, ...] = DEFAULT_COMPARE_FIELDS
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for field in compare_fields:
        value = need.get(field)
        if isinstance(value, list):
            normalized[field] = sorted(
                value, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False)
            )
        elif isinstance(value, dict):
            normalized[field] = {key: value[key] for key in sorted(value)}
        else:
            normalized[field] = value
    return normalized


def need_fingerprint(
    need: dict[str, Any], compare_fields: tuple[str, ...] = DEFAULT_COMPARE_FIELDS
) -> str:
    payload = json.dumps(
        normalize_need(need, compare_fields=compare_fields), sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fieldwise_changes(
    old_need: dict[str, Any],
    new_need: dict[str, Any],
    compare_fields: tuple[str, ...] = DEFAULT_COMPARE_FIELDS,
) -> list[tuple[str, Any, Any]]:
    changes: list[tuple[str, Any, Any]] = []
    old_norm = normalize_need(old_need, compare_fields=compare_fields)
    new_norm = normalize_need(new_need, compare_fields=compare_fields)
    for field in compare_fields:
        if old_norm.get(field) != new_norm.get(field):
            changes.append((field, old_norm.get(field), new_norm.get(field)))
    return changes


def diff_needs(
    old_needs: dict[str, dict[str, Any]],
    new_needs: dict[str, dict[str, Any]],
    compare_fields: tuple[str, ...] = DEFAULT_COMPARE_FIELDS,
) -> NeedDiff:
    old_ids = set(old_needs)
    new_ids = set(new_needs)

    added = {need_id: new_needs[need_id] for need_id in sorted(new_ids - old_ids)}
    removed = {need_id: old_needs[need_id] for need_id in sorted(old_ids - new_ids)}
    changed_old: dict[str, dict[str, Any]] = {}
    changed_new: dict[str, dict[str, Any]] = {}

    for need_id in sorted(old_ids & new_ids):
        if need_fingerprint(old_needs[need_id], compare_fields) != need_fingerprint(
            new_needs[need_id], compare_fields
        ):
            changed_old[need_id] = old_needs[need_id]
            changed_new[need_id] = new_needs[need_id]

    return NeedDiff(added=added, removed=removed, changed_old=changed_old, changed_new=changed_new)


def compare_directories(
    old_root: Path,
    new_root: Path,
    include: str = "**/*.json",
    exclude: tuple[str, ...] = (),
    compare_fields: tuple[str, ...] = DEFAULT_COMPARE_FIELDS,
) -> ComparisonResult:
    scanner = FileScanner(include=include, exclude=exclude)
    old_files = scanner.scan(old_root)
    new_files = scanner.scan(new_root)

    result = ComparisonResult()
    common_paths = sorted(set(old_files) & set(new_files))
    result.left_only_files = sorted(set(old_files) - set(new_files))
    result.right_only_files = sorted(set(new_files) - set(old_files))

    for relative_path in common_paths:
        old_path = old_files[relative_path]
        new_path = new_files[relative_path]

        try:
            old_data, old_version, old_needs = load_sphinx_needs_json(old_path)
        except (JsonParseError, InvalidNeedsJsonError) as exc:
            result.errors.append(
                FileLoadError(
                    relative_path=relative_path,
                    side="baseline",
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            )
            continue

        try:
            new_data, new_version, new_needs = load_sphinx_needs_json(new_path)
        except (JsonParseError, InvalidNeedsJsonError) as exc:
            result.errors.append(
                FileLoadError(
                    relative_path=relative_path,
                    side="candidate",
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            )
            continue

        result.comparable_files.append(
            FileComparison(
                relative_path=relative_path,
                old_source_path=old_path,
                new_source_path=new_path,
                old_version=old_version,
                new_version=new_version,
                old_data=old_data,
                new_data=new_data,
                diff=diff_needs(old_needs, new_needs, compare_fields=compare_fields),
            )
        )

    return result
