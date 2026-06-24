import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COOKIE_FILE = ROOT / "cookies" / "linkedin.cookies"

pytestmark = pytest.mark.skipif(
    os.environ.get("WARMPATH_LIVE_TESTS") != "1"
    or not (COOKIE_FILE.exists() and COOKIE_FILE.stat().st_size > 0),
    reason=(
        "WARMPATH_LIVE_TESTS=1 and cookies/linkedin.cookies are required "
        "for live LinkedIn tests"
    ),
)


def run_company(company: str, tmp_path: Path) -> dict:
    json_out = tmp_path / "company.json"
    cache_dir = tmp_path / "cache"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "warmpath.cli",
            "company",
            company,
            "--max-degree",
            "2",
            "--limit",
            "25",
            "--max-targets",
            "50",
            "--cache-dir",
            str(cache_dir),
            "--refresh-cache",
            "--cookie-file",
            str(COOKIE_FILE),
            "--json-out",
            str(json_out),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )

    assert result.returncode == 0, (
        f"company failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert json_out.exists(), "company did not write JSON output"
    return json.loads(json_out.read_text(encoding="utf-8"))


def test_procreate_art_finds_direct_connection(tmp_path: Path) -> None:
    data = run_company(
        "https://www.linkedin.com/company/procreate-art/",
        tmp_path,
    )

    direct_candidates = [
        candidate
        for candidate in data.get("candidates", [])
        if candidate.get("degree") == 1
    ]

    assert len(direct_candidates) >= 1, (
        "expected at least one direct connection at procreate-art"
    )


def test_contentsquare_finds_second_degree_connection(tmp_path: Path) -> None:
    data = run_company(
        "https://www.linkedin.com/company/contentsquare/",
        tmp_path,
    )

    second_degree_candidates = [
        candidate
        for candidate in data.get("candidates", [])
        if candidate.get("degree") == 2
    ]

    assert len(second_degree_candidates) >= 1, (
        "expected at least one second-degree connection at contentsquare"
    )
