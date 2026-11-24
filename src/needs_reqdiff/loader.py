from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class NeedsReqdiffError(Exception):
    """Base package error."""


class InvalidNeedsJsonError(NeedsReqdiffError):
    """Raised when a JSON file is not a valid sphinx-needs export."""


class JsonParseError(NeedsReqdiffError):
    """Raised when a JSON file cannot be parsed."""


class FileScanner:
    def __init__(self, include: str = "**/*.json", exclude: tuple[str, ...] = ()) -> None:
        self.include = include
        self.exclude = exclude

    def scan(self, root: Path) -> dict[Path, Path]:
        files: dict[Path, Path] = {}
        for path in sorted(root.glob(self.include)):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if any(relative.match(pattern) for pattern in self.exclude):
                continue
            files[relative] = path
        return files


def load_sphinx_needs_json(path: Path) -> tuple[dict[str, Any], str, dict[str, dict[str, Any]]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise JsonParseError(
            f"{path}: invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}"
        ) from exc

    if not isinstance(data, dict):
        raise InvalidNeedsJsonError(f"{path}: root JSON element must be an object")

    current_version = data.get("current_version")
    if not isinstance(current_version, str) or not current_version:
        raise InvalidNeedsJsonError(f"{path}: missing or invalid 'current_version'")

    versions = data.get("versions")
    if not isinstance(versions, dict):
        raise InvalidNeedsJsonError(f"{path}: missing or invalid 'versions'")

    version_data = versions.get(current_version)
    if not isinstance(version_data, dict):
        raise InvalidNeedsJsonError(
            f"{path}: current version '{current_version}' not found in 'versions'"
        )

    needs = version_data.get("needs")
    if not isinstance(needs, dict):
        raise InvalidNeedsJsonError(
            f"{path}: missing or invalid 'versions[{current_version}].needs'"
        )

    normalized_needs: dict[str, dict[str, Any]] = {}
    for need_id, need in needs.items():
        if not isinstance(need_id, str) or not isinstance(need, dict):
            raise InvalidNeedsJsonError(f"{path}: each need entry must map string id to object")
        normalized_needs[need_id] = need

    return data, current_version, normalized_needs
