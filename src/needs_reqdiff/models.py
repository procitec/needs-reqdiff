from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class FileLoadError:
    relative_path: Path
    side: str
    error_type: str
    message: str


@dataclass(slots=True)
class NeedDiff:
    added: dict[str, dict[str, Any]] = field(default_factory=dict)
    removed: dict[str, dict[str, Any]] = field(default_factory=dict)
    changed_old: dict[str, dict[str, Any]] = field(default_factory=dict)
    changed_new: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class FileComparison:
    relative_path: Path
    old_source_path: Path | None = None
    new_source_path: Path | None = None
    diff: NeedDiff | None = None
    old_version: str | None = None
    new_version: str | None = None
    old_data: dict[str, Any] | None = None
    new_data: dict[str, Any] | None = None


@dataclass(slots=True)
class ComparisonResult:
    comparable_files: list[FileComparison] = field(default_factory=list)
    left_only_files: list[Path] = field(default_factory=list)
    right_only_files: list[Path] = field(default_factory=list)
    errors: list[FileLoadError] = field(default_factory=list)

    @property
    def changed_files(self) -> list[FileComparison]:
        return [
            comparison
            for comparison in self.comparable_files
            if comparison.diff
            and (comparison.diff.added or comparison.diff.removed or comparison.diff.changed_new)
        ]
