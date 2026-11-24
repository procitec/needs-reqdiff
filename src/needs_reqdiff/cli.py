from __future__ import annotations

import argparse
from pathlib import Path

from .api import generate_reqdiff


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare sphinx-needs JSON exports and generate RST reports."
    )
    parser.add_argument("baseline", type=Path, help="Directory with reference JSON files")
    parser.add_argument("candidate", type=Path, help="Directory with comparison JSON files")
    parser.add_argument("output", type=Path, help="Output directory for generated reports")
    parser.add_argument("--include", default="**/*.json", help="Glob pattern for included files")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Glob pattern to exclude; can be used multiple times",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    result, index_path = generate_reqdiff(
        baseline_dir=args.baseline,
        candidate_dir=args.candidate,
        output_dir=args.output,
        include=args.include,
        exclude=tuple(args.exclude),
    )

    print(f"Comparable files : {len(result.comparable_files)}")
    print(f"Changed files    : {len(result.changed_files)}")
    print(f"Left-only files  : {len(result.left_only_files)}")
    print(f"Right-only files : {len(result.right_only_files)}")
    print(f"Load errors      : {len(result.errors)}")
    print(f"Index written to : {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
