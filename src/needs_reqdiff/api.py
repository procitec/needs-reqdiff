from __future__ import annotations

from pathlib import Path

from .differ import DEFAULT_COMPARE_FIELDS, compare_directories
from .rst_writer import write_reports


def generate_reqdiff(
    baseline_dir: str | Path,
    candidate_dir: str | Path,
    output_dir: str | Path,
    include: str = "**/*.json",
    exclude: tuple[str, ...] = (),
    compare_fields: tuple[str, ...] = DEFAULT_COMPARE_FIELDS,
):
    baseline_path = Path(baseline_dir)
    candidate_path = Path(candidate_dir)
    output_path = Path(output_dir)

    result = compare_directories(
        old_root=baseline_path,
        new_root=candidate_path,
        include=include,
        exclude=exclude,
        compare_fields=compare_fields,
    )
    index_path = write_reports(result, output_path, compare_fields=compare_fields)
    return result, index_path
