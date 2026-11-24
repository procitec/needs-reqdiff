from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest


@pytest.fixture()
def fixture_roots(tmp_path: Path) -> tuple[Path, Path]:
    project_root = Path(__file__).parent
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    shutil.copytree(project_root / "fixtures" / "base", baseline)
    shutil.copytree(project_root / "fixtures" / "candidate", candidate)
    return baseline, candidate
