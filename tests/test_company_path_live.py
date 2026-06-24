import os
from pathlib import Path

import pytest

from warmpath.cli import build_api, find_company_path_candidates

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
    api = build_api(COOKIE_FILE)
    return find_company_path_candidates(
        api=api,
        company_input=company,
        max_degree=2,
        limit=25,
        max_targets=50,
        cache_dir=tmp_path / "cache",
        refresh_cache=True,
    )


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
